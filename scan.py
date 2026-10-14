#!/usr/bin/env python3
"""
Nightly job scanner.

Reads sources.json, asks each company's public careers system for its open jobs,
keeps machine-learning / research roles located in Europe (or with unknown location),
and writes into out/:

  recent.json   postings first seen in the last RECENT_DAYS days, with a description excerpt
  open.json     every matching posting that is currently open (no descriptions)
  closed.json   matching postings that disappeared in the last CLOSED_DAYS days
  status.json   health of every source in the last run, warnings, coverage check
  state.json    internal memory between runs

With SCAN_PROFILE=phd it reads phd_sources.json instead, keeps funded PhD positions
(title filter below) and writes the same files with a phd_ prefix (phd_recent.json, ...).

The job boards (LinkedIn, Indeed) also need python-jobspy; their postings are matched against the
company sources (duplicates dropped, postings a company source missed listed in status.json).

Needs `requests`, plus `python-jobspy` for the job boards.
"""
import concurrent.futures as cf
import datetime as dt
import email.utils
import hashlib
import html as htmlmod
import json
import os
import random
import re
import sys
import threading
import time
import traceback
import unicodedata
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "out")
NOW = dt.datetime.now(dt.timezone.utc)
NOW_ISO = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
TODAY = NOW.date()
PROFILE = os.environ.get("SCAN_PROFILE", "jobs")   # "jobs" or "phd"
PREFIX = "phd_" if PROFILE == "phd" else ""

RECENT_DAYS = 7      # how long a posting stays in recent.json
CLOSED_DAYS = 14     # how long a closure stays in closed.json
STALE_DAYS = 10      # search-based sources: a posting unseen this long counts as closed
MAX_DETAIL = 150     # detail requests per source per run
DESC_HEAD = 600
DESC_REQ = 1400
DESC_MAX = 5000      # description in candidates.json, only for postings first seen in DESC_DAYS
DESC_DAYS = 3
NEW_DAYS = 10        # new_cards.json: postings first seen in the last days (the Radar routine reads it;
                     # it keeps track of what it judged, so a backlog or a missed morning is caught up)
NEW_REQ = 700        # characters of the requirements part in new_cards.json
WORKERS = 8

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# Searches used by sources that need a keyword (Workday, Eightfold, SuccessFactors, ...).
QUERIES = [
    "machine learning", "deep learning", "artificial intelligence", "AI engineer",
    "research scientist", "research engineer", "data scientist", "computer vision",
    "applied scientist", "signal processing", "algorithm", "LLM", "graduate",
]

# ---------------------------------------------------------------- title filter
# Acronyms are matched in capitals only: "ai" is an Italian word ("cameriera ai piani").
INCLUDE = re.compile(
    r"machine[- ]?learning|(?-i:\bML\b|\bMLE\b|\bAI\b|\bKI\b|\bIA\b)|deep[- ]?learning|\bA\.I\.|\bgen ?ai\b|"
    r"artificial intelligence|intelligenza artificiale|intelligence artificielle|"
    r"k(?:ü|ue)nstliche intelligenz|"
    r"research (?:engineer|scientist|fellow|software engineer|associate)|"
    r"applied (?:scientist|research|ml)|data scien|computer vision|\bvision\b|perception|"
    r"algorithm|signal processing|(?-i:\bDSP\b|\bLLMs?\b|\bNLP\b)|language model|natural language|"
    r"generative|foundation model|reinforcement learning|neural|robot learning|"
    r"technical staff|machine vision|image processing|computational imaging|"
    r"autonomous|autonomy|speech|multimodal|multi-modal|agentic",
    re.I)
GRAD = re.compile(r"graduate|trainee|young (?:talent|professional)|early career|"
                  r"new grad|entry[- ]level|rotational", re.I)
GRAD_TOPIC = re.compile(r"\bAI\b|data|analytic|machine|digital|technolog|tech\b|engineer|"
                        r"software|quant|research|science", re.I)
EXCLUDE = re.compile(
    r"\bsenior\b|\bsr\b\.?|\bstaff\b|principal|\blead\b|\bleader|manager|management|"
    r"director|\bhead\b|\bvp\b|vice president|chief|\bexecutive\b|architect|consultant|"
    r"consulting|\bsales\b|account (?:executive|manager)|marketing|recruit|\blegal\b|counsel|"
    r"working student|werkstudent|student assistant|student job|thesis|abschlussarbeit|"
    r"masterarbeit|bachelorarbeit|apprentice|ausbildung|lehrling|duales? stud|praktikum|"
    r"stagiaire|\bstage\b|tirocinio|alternance|alternant|postdoc|post-doc|postdoctoral|"
    r"professor|lecturer|faculty position|technician|nurse|physician|"
    r"\bphd student\b.*(?:chemistry|biology)|(?<!phd )(?<!doctoral )\bstudent\b|undergraduate|placement|"
    r"\btesi\b|curricular|mandatory internship|pflicht|product owner|customer|strategist|producer|"
    r"go-to-market|business development|solutions? engineer|pre-?sales|platform engineer|reliability|\bSRE\b|"
    r"\bQA\b|quality assurance|test automation|product analytics|devops|infrastructure engineer|"
    r"\bL[6-9]\b|\bIC[5-9]\b|\bP[5-9]\b|\bE[6-9]\b",
    re.I)


PHD_RE = re.compile(r"\bph\.?\s?d\b|doctoral|doctorate|doctoraat|promovend|doktorand|doctorant|dottorat|\bcifre\b|"
                    r"th[èe]se\b", re.I)
PHD_EXCLUDE = re.compile(r"post[- ]?do[ck]|professor|lecturer|master'?s? (?:thesis|project)|"
                         r"student assistant|internship|technician|\bmanager\b|pre-?doctoral|praktik|"
                         r"werkstudent|stagiaire|tirocin", re.I)
# Subjects near the profile, wide on purpose (Claude judges the fit), in EN, FR, DE, IT, NL.
PHD_TOPIC = re.compile(
    r"machine learning|deep learning|(?-i:\bAI\b|\bKI\b|\bIA\b|\bML\b)|artificial intelligence|neural|learning\b|\bdata\b|"
    r"model(?:l)?ing|cardi|heart|"
    r"signal|sensor|sensing|wearable|robot|vision|imaging|image|ultrasound|acoustic|biomedical|health|medical|"
    r"computational|algorithm|autonom|embedded|edge|hardware|\bLLMs?\b|\bVLMs?\b|language model|foundation model|"
    r"generative|time series|control|digital twin|statistic|computer|information technology|"
    r"electrical|electronic|informatics|intelligen|neuro|physiolog|biosignal|\bECG\b|\bEEG\b|\bEMG\b|"
    r"gait|motion|movement|perception|radar|wireless|speech|audio|"
    r"optimi[sz]|simulat|numerical|mathemat|software|security|cyber|network|communicat|(?-i:\b[56]G\b)|"
    r"\bMEMS\b|semiconductor|\bchips?\b|\bASIC|\bFPGA|circuit|optic|photonic|quantum|mechatronic|automation|"
    r"anomal|detection|recognition|agent|brain|cognit|biomechan|human performance|physical activity|"
    r"rehabilit|prosthe|exoskelet|bioengineer|"
    r"apprentissage|donn[ée]es|capteur|imagerie|informatique|[ée]lectroni|automatique|sant[ée]|m[ée]dic|"
    r"g[ée]n[ée]rati|traitement|num[ée]rique|calcul|parole|r[ée]seau|"
    r"maschinelles|lernen|daten|\bbild|informatik|elektr|regelung|steuerung|messtechnik|medizin|gesundheit|"
    r"wahrnehmung|sprach|akusti|optimierung|"
    r"apprendimento|\bdati\b|segnal|immagin|informatic|elettronic|controllo|salute|"
    r"\bleren\b|gegevens|\bbeeld|elektro|gezondheid", re.I)
# Titles that name no subject at all ("PhD Position", "R&D Engineer (PhD Student)") are kept.
PHD_GENERIC = re.compile(
    r"\b(?:ph\.?\s?d|doctoral|doctorate|doktorand\w*|doctorant\w*|dottorand\w*|dottorato|promovend\w*|"
    r"positions?|students?|candidates?|researchers?|research|programm?e?|fellow(?:ship)?s?|openings?|"
    r"vacanc(?:y|ies)|open|call|in|of|the|for|and|at|an?|[mfwdx]|div|all|genders?)\b", re.I)


def generic_phd_title(t):
    return len(re.findall(r"[A-Za-zÀ-ÿ]{3,}", PHD_GENERIC.sub(" ", t or ""))) <= 1


# Doctoral positions advertised by companies and on the job boards: the job scan hands them to the
# PhD profile (out/phd_industrial.json).
INDUSTRIAL_PHD = re.compile(
    r"\bph\.?\s?d\.?\s*(?:student\w*|candidate|position|researcher|thesis|project|programme|program|fellow\w*|"
    r"scholarship|"
    r"opportunit\w*|in\b|on\b|at\b|:|[–-])|^\s*ph\.?\s?d\b(?!\.?\s*(?:graduate|holder|level))|doctoral (?:student|candidate|researcher|position|thesis)|"
    r"industrial (?:ph\.?\s?d|doctora\w*)|doktorand|promotionsstelle|(?:^\s*|\b(?:für|zur|der) )promotion\b(?! (?:manager|specialist|coordinator|finan|des\b|de\b|du\b))|"
    r"th[èe]se|\bcifre\b|dottorat|doctorat|promovend|\bdoktorant|\bdoctorand|"
    r"research assistant \(ph\.?d\)", re.I)   # the last one: Meta FAIR's PhD
# Employers on the job boards that make a doctoral position academic rather than industrial.
ACADEMIC_CO = re.compile(
    r"universit|hochschule|\bTU\b|\bETH\b|EPFL|[ée]cole|\bcollege\b|school|institut|\bDTU\b|\bKTH\b|\bUMC\b|"
    r"\bMC\b|hospital|klinik|ziekenhuis|fraunhofer|max planck|helmholtz|leibniz|\bCNRS\b|\bINRIA\b|\bCEA\b|"
    r"\bINSERM\b|\bINRAE\b|\bIIT\b|\bEMBL\b|\bCERN\b|\bTNO\b|\bimec\b|academ|polytechn|politecnic|\bUCL\b|"
    r"leuven|research cent|centre de recherche|forschungszentrum|\bDLR\b|\bSISSA\b|\bISTA\b|\bIST\b|centrum|"
    r"wageningen|\bWUR\b|laborator|forschung|recherche|ricerca|onderzoek|\bXFEL\b|electron laser", re.I)
FRANCE_RE = re.compile(r"france|paris|grenoble|lyon|toulouse|saclay|sophia|rennes|nice|lille", re.I)
BOARD_PHD_DAYS = 30   # a doctoral position seen on a job board stays on the PhD map this long
# words of an employer name that tell nothing apart (matched at the start of each word)
PHD_CO_GENERIC = re.compile(r"univ|tech|inst|hochsch|college|school|research|cent|medi|klinik|hospital|faculty|depart|labor")
IT_RE = re.compile(r"\bital(?:y|ia|ie|ien)\b|(?-i:(?<![A-Za-z])IT(?![A-Za-z]))|\b(?:milan|milano|turin|torino|rome|roma|"
                   r"bologna|pisa|genoa|genova|padova|padua|naples|napoli|trento|trieste|florence|firenze|bari|catania)\b", re.I)
CH_RE = re.compile(r"switzerland|schweiz|suisse|svizzera|(?-i:(?<![A-Za-z])CH(?![A-Za-z]))|\b(?:zurich|zürich|geneva|genève|"
                   r"lausanne|basel|bern|lugano|zug|winterthur|st\. gallen)\b", re.I)
# MSCA mobility rule (no more than 12 months in the host country in the 3 years before recruitment)
MSCA_NOT_ELIGIBLE = "it"
MSCA_CH_NOTE = "MSCA: in Svizzera solo con assunzione entro marzo 2027 (regola di mobilità)"


def country_of(e):
    c = (e.get("country") or "").lower()
    if c in ("it", "ch"):
        return c
    loc = e.get("loc") or ""
    return "it" if IT_RE.search(loc) else "ch" if CH_RE.search(loc) else c


def msca_eligibility(e):
    """eligible / eligibility_note for MSCA positions (kind msca-*)."""
    if not str(e.get("kind", "")).startswith("msca"):
        return
    if e.get("ats") == "watch":   # a network page: its positions sit in several countries
        e["eligibility_note"] = ("MSCA: vale la sede di ciascuna posizione (non in Italia; in Svizzera solo con "
                                 "assunzione entro marzo 2027)")
        return
    c = country_of(e)
    e["eligible"] = c != MSCA_NOT_ELIGIBLE
    if c == "ch":
        e["eligibility_note"] = MSCA_CH_NOTE
    else:
        e.pop("eligibility_note", None)


def keep_title(src, r):
    if PROFILE != "phd":
        return wide_ok(r["title"], r.get("dept"))
    t = r.get("title") or ""
    if PHD_EXCLUDE.search(t):
        return False
    if not (r.get("_is_phd") or src.get("all_phd") or PHD_RE.search(t)):
        return False
    return src.get("topic") is False or bool(PHD_TOPIC.search(t + " " + (r.get("dept") or ""))) or \
        generic_phd_title(t)


def title_ok(title):
    t = title or ""
    t_ex = re.sub(r"member of (?:the )?technical staff", "MTS", t, flags=re.I)
    if EXCLUDE.search(t_ex):
        return False
    if INCLUDE.search(t):
        return True
    return bool(GRAD.search(t) and GRAD_TOPIC.search(t))


# ------------------------------------------------- wide filter (plan, phase 2)
# The script only drops what can never fit; everything else goes to candidates.json for Claude to
# judge. The old INCLUDE/EXCLUDE pair (title_ok) still decides what reaches recent/open.json.
NOT_TECH = re.compile(   # always out, whatever else the title says
    r"\bsales\b|account (?:executive|manager|director)|business development|\bBDR\b|\bSDR\b|pre-?sales|"
    r"recruit|talent acquisition|(?-i:\bHR\b)|human resources|people (?:partner|operations|business)|payroll|"
    r"\blegal\b|counsel\b|paralegal|lawyer|attorney|solicitor|accountant|accounting|bookkeep|\btax\b|treasury|"
    r"internal audit|\bauditor\b|financial controller|controlling\b|procurement|purchasing|\bbuyer\b|"
    r"customer (?:support|success|service|care)|support (?:agent|representative|specialist|engineer|technician)|"
    r"help ?desk|call cent|receptionist|office (?:manager|assistant|coordinator)|"
    r"(?:administrative|executive|personal) assistant|\bretail\b|\bstore (?:manager|associate|assistant|staff)|"
    r"cashier|warehouse|\bdriver\b|housekeep|cleaner|vertrieb|verkauf|buchhalt|personalreferent|"
    r"rechtsanwalt|steuerberater|comptab|juridique|ressources humaines|vendite|contabil|risorse umane|"
    r"addetto vendite|cameriere|camariera|cameriera|facchino|data entry", re.I)
GIG = re.compile(   # data labelling and AI-training gigs, out even with an ML title
    r"data annotat|annotator|annotation (?:specialist|quality|analyst|expert)|\bAI (?:trainer|tutor)s?\b|"
    r"\(AI training\)|[-–] AI training|AI training (?:contributor|specialist)|transcriptionist|\brater\b|"
    r"content moderat", re.I)
SOFT_NOT_TECH = re.compile(   # out unless the title also has a technical word ("Marketing Data Scientist")
    r"marketing|\bbrand\b|public relations|communications?\b|\bfinance\b|financial (?:analyst|planning)|"
    r"commercial(?:e)?\b|\bevent|"
    r"\bnurse|nursing|psycholog|therapist|physiotherap|midwi|pharmacist|care (?:worker|assistant|coordinator|home)|"
    r"support worker|healthcare assistant|\bcarer\b|dental|dentist|veterinar|radiographer|phlebotom|paramedic|"
    r"psychiatr|surgeon|physician|sonographer|counsell?or\b|social worker|pflege|infirmi|infermier|"
    r"actuar|underwrit|market risk|credit risk|deal advisory|transaction services|investment bank|equity research|"
    r"\bwealth\b|claims (?:handler|adjuster)|\bloans?\b|mortgage|\bbanker\b|private equity|\bM&A\b|\bAML\b|\bKYC\b|"
    r"demand generation|growth hack|\bSEO\b|copywrit|content (?:writer|creator|manager|strateg|editor|specialist)|"
    r"social media|community manager|journalist|\beditor\b|translator|interpreter|"
    r"facilit(?:y|ies)|electrician|plumber|\bmechanic\b|welder|\bcook\b|waiter|barista|security guard|janitor|"
    r"logistics|supply chain|teacher|\btutor\b|lehrer|enseignant|insegnant|secretary|\bclerk\b", re.I)
# A technical word in the title keeps the posting even with a function word ("Marketing Data Scientist").
TECH_ANCHOR = re.compile(
    r"engineer|ingenieur|ingénieur|ingegner|scientist|wissenschaftl|chercheur|ricercat|research|"
    r"\bdata\b|machine learning|deep learning|(?-i:\bML\b|\bAI\b|\bKI\b|\bIA\b|\bNLP\b|\bLLMs?\b)|"
    r"developer|entwickler|développeur|sviluppat|software|algorithm|analytics|statistic|quant\b|"
    r"computer vision|robotic|modell|modeling|\bmodels?\b|simulation|signal|perception|technical staff|"
    r"architect|\beng\b|informati", re.I)
ML_ROLE = re.compile(r"scientist|engineer|ingenieur|ingénieur|developer|entwickler|researcher|\bdata\b|analytics|"
                     r"machine learning", re.I)
TOO_SENIOR = re.compile(
    r"(?<![(/])\bsenior\b(?!\s*[/)])|\bsr\b\.?|\bstaff\b|principal|\blead\b|\bleader|manager|director|\bhead\b|"
    r"principle (?:engineer|scientist|researcher|software|data|ml|ai)|"   # a frequent misspelling of principal
    r"\bVP\b|vice president|\bchief\b|\bexecutive\b|\bleiter|\bleitung\b|directeur|direttore|responsabile|"
    r"(?-i:\bL[6-9]\b|\bIC[5-9]\b|\bE[6-9]\b|\bP[5-9]\b)", re.I)
NOT_A_JOB = re.compile(
    r"\bintern\b|\binterns\b|internship|praktik|\bstage\b|stagiaire|tirocin|stagista|werkstudent|"
    r"working student|student assistant|student job|studentische|summer student|thesis|abschlussarbeit|"
    r"masterarbeit|bachelorarbeit|\btesi\b|m[ée]moire|apprentice|ausbildung|azubi|lehrling|duales? stud|"
    r"alternance|alternant|apprendist|\bpost[- ]?do[ck]|professor|lecturer|faculty position|"
    r"\bdozent", re.I)


def wide_ok(title, dept=""):
    """Phase 2 filter: drop only non-technical functions, impossible seniority and non-jobs."""
    t = re.sub(r"member of (?:the )?technical staff", "MTS", title or "", flags=re.I)
    if TOO_SENIOR.search(t) or NOT_A_JOB.search(t) or GIG.search(t):
        return False
    if title_ok(t) and ML_ROLE.search(t):   # an ML role stays whatever function it names ("Applied Scientist, Procurement")
        return True
    if NOT_TECH.search(t) or (NOT_TECH.search(dept or "") and not TECH_ANCHOR.search(t)):
        return False
    return not (SOFT_NOT_TECH.search(f"{t} | {dept or ''}") and not TECH_ANCHOR.search(t))


# Indicative score, only to sort candidates.json (it never drops anything).
SCORE = [
    ("pretraining", 2, r"pre-?train"), ("self-supervised", 2, r"self-supervised|masked auto-?encoder|contrastive learning"),
    ("distributed training", 2, r"distributed training|multi-node|multi-gpu|\bDDP\b|\bFSDP\b|deepspeed|megatron|\bSLURM\b"),
    ("training", 1, r"\btrain(?:ing)? (?:large |foundation |deep |neural |ml )?(?:models?|networks?)|model training"),
    ("pytorch", 1, r"pytorch"), ("publications", 1, r"publications?|\bpublish|neurips|\bicml\b|\biclr\b|\bcvpr\b|top-tier (?:venues|conferences)"),
    ("foundation models", 1, r"foundation models?"), ("biosignals", 2, r"bio-?signals?|\bECG\b|\bEEG\b|\bEMG\b|physiological"),
    ("wearable", 2, r"wearables?"), ("ultrasound", 2, r"ultrasound|ultrasonic"),
    ("edge", 2, r"edge (?:ai|ml|devices?|inference|computing)|on-device|embedded (?:ml|ai|machine learning)|tinyml|microcontroller"),
    ("quantization", 1, r"quantiz|model compression|pruning|distillation"), ("time series", 1, r"time[- ]series"),
    ("research", 1, r"research (?:engineer|scientist)|applied research"),
    ("langchain", -2, r"langchain|llamaindex|langgraph"), ("RAG", -2, r"\bRAG\b|retrieval[- ]augmented"),
    ("prompt", -1, r"prompt engineering|prompting\b"), ("agents", -1, r"agentic|ai agents?\b|multi-agent workflows"),
]
SCORE = [(k, w, re.compile(p, re.I)) for k, w, p in SCORE]
ROLE_HINT = re.compile(
    r"what you(?:'ll| will) (?:do|be doing|work on)|your (?:role|mission|tasks|responsibilities|impact)|"
    r"the role|about the role|about the job|role overview|responsibilities|in this role|you will\b|"
    r"job description|ihre aufgaben|deine aufgaben|aufgaben|vos missions|votre mission|missions? :|"
    r"cosa farai|mansioni|responsabilità", re.I)


def years_needed(text):
    """Largest minimum of years of experience asked for (e.g. "3+ years ... 5+ years" -> 5)."""
    best = None
    for m in re.finditer(r"\b(\d{1,2})\s*(?:\+|-\s*\d{1,2}|to \d{1,2}|or more)?\s*\+?\s*(?:years?|yrs|anni|jahre?n?|ans)\b",
                         text or "", re.I):
        n = int(m.group(1))
        if 1 <= n <= 15:
            best = max(best or 0, n)
    return best


def assess(e, text):
    """Score, keywords, years and a ~400-character card for one posting."""
    full = f"{e.get('title', '')}\n{text or ''}"
    kw, score = [], 0
    for k, w, p in SCORE:
        if p.search(full):
            kw.append(k if w > 0 else f"-{k}")
            score += w
    years = years_needed(text)
    if years and years >= 5:
        kw.append(f"-{years}+ years")
        score -= 2
    if title_ok(e.get("title")):
        score += 2   # ML / research title
    flat = re.sub(r"\s+", " ", text or "").strip()
    m = ROLE_HINT.search(flat, 0)
    start = m.start() if m and m.start() < len(flat) - 150 else 0
    e["score"], e["kw"], e["years"] = score, kw, years
    e["card"] = flat[start:start + 420].rsplit(" ", 1)[0] if len(flat) > start + 420 else flat[start:]


# ------------------------------------------------------------- location filter
EU_WORDS = [
    "switzerland", "schweiz", "suisse", "svizzera", "italy", "italia", "germany", "deutschland",
    "france", "netherlands", "nederland", "belgium", "belgique", "belgië", "luxembourg",
    "austria", "österreich", "spain", "españa", "portugal", "ireland", "denmark", "danmark",
    "sweden", "sverige", "norway", "norge", "finland", "suomi", "poland", "polska", "czech",
    "czechia", "slovakia", "hungary", "romania", "bulgaria", "greece", "croatia", "slovenia",
    "estonia", "latvia", "lithuania", "united kingdom", "england", "scotland", "wales",
    "great britain", "iceland", "malta", "cyprus", "liechtenstein", "monaco", "europe", "emea",
    "european union", "serbia", "bosnia", "montenegro", "albania", "north macedonia", "moldova",
    "ukraine", "kosovo",
    # country names in other languages (ABG, EURAXESS and national job sites write them so)
    "italie", "allemagne", "pays-bas", "espagne", "autriche", "royaume-uni", "irlande", "danemark",
    "suède", "norvège", "finlande", "pologne", "tchéquie", "république tchèque", "slovaquie",
    "hongrie", "roumanie", "bulgarie", "grèce", "croatie", "slovénie", "estonie", "lettonie",
    "lituanie", "islande", "chypre", "italien", "frankreich", "niederlande", "belgien", "spanien",
    "schweden", "dänemark", "norwegen", "finnland", "polen", "tschechien", "slowakei", "ungarn",
    "rumänien", "griechenland", "kroatien", "slowenien", "irland", "vereinigtes königreich",
    "großbritannien", "grossbritannien", "luxemburg", "europa", "germania", "francia",
    "paesi bassi", "belgio", "spagna", "svezia", "danimarca", "norvegia", "finlandia", "polonia",
    "grecia", "irlanda", "regno unito", "lussemburgo", "alemania", "países bajos", "bélgica",
    "suiza", "reino unido", "suecia", "dinamarca", "noruega", "duitsland", "frankrijk", "italië",
    "spanje", "zwitserland", "oostenrijk", "zweden", "denemarken", "noorwegen", "verenigd koninkrijk",
    "ierland", "griekenland", "alemanha", "frança", "espanha", "itália", "suíça", "países baixos",
    "česko", "česká republika", "magyarország", "românia", "hrvatska", "slovenija", "eesti",
    "latvija", "lietuva", "éire",
    # cities
    "zurich", "zürich", "zuerich", "basel", "geneva", "genève", "geneve", "lausanne", "bern",
    "zug", "baden", "dättwil", "rüschlikon", "ruschlikon", "kaiseraugst", "rotkreuz", "schlieren",
    "lugano", "winterthur", "st. gallen", "neuchâtel", "neuchatel", "dübendorf", "villigen",
    "stäfa", "staefa", "murten", "milan", "milano", "turin", "torino", "rome", "roma", "pisa",
    "genoa", "genova", "bologna", "naples", "napoli", "padova", "padua", "modena", "maranello",
    "florence", "firenze", "trento", "trieste", "bari", "catania", "pomigliano", "rivalta",
    "brindisi", "ivrea", "agrate", "catania", "munich", "münchen", "muenchen", "berlin",
    "hamburg", "frankfurt", "stuttgart", "heidelberg", "freiburg", "darmstadt", "erlangen",
    "tübingen", "tuebingen", "karlsruhe", "cologne", "köln", "düsseldorf", "dresden",
    "aachen", "nuremberg", "nürnberg", "bonn", "hannover", "leipzig", "ulm", "augsburg",
    "renningen", "paris", "lyon", "toulouse", "grenoble", "sophia antipolis", "nice",
    "marseille", "bordeaux", "nantes", "lille", "rennes", "saclay", "palaiseau", "amsterdam",
    "eindhoven", "delft", "veldhoven", "utrecht", "rotterdam", "the hague", "leiden",
    "groningen", "leuven", "brussels", "bruxelles", "ghent", "gent", "antwerp", "vienna",
    "wien", "graz", "linz", "madrid", "barcelona", "valencia", "seville", "lisbon", "lisboa",
    "porto", "dublin", "cork", "copenhagen", "aarhus", "stockholm", "gothenburg", "göteborg",
    "lund", "malmö", "oslo", "trondheim", "helsinki", "espoo", "tampere", "oulu", "warsaw",
    "kraków", "krakow", "wroclaw", "wrocław", "gdansk", "prague", "brno", "bratislava",
    "budapest", "bucharest", "sofia", "athens", "tallinn", "riga", "vilnius", "london",
    "cambridge, uk", "cambridge, united kingdom", "oxford", "edinburgh", "bristol",
    "manchester", "reading", "belfast", "glasgow", "heidelberg", "maastricht", "hoorn",
]
EU_RE = re.compile(r"(?<![a-zà-ÿ])(?:" + "|".join(re.escape(w) for w in EU_WORDS) + r")(?![a-zà-ÿ])", re.I)
EU_CODES = re.compile(
    r"(?<![A-Za-z])(?:CH|IT|DE|FR|NL|BE|LU|AT|ES|PT|IE|DK|SE|NO|FI|PL|CZ|SK|HU|RO|BG|GR|HR|SI|"
    r"EE|LV|LT|GB|UK|IS|MT|CY|LI|CHE|ITA|DEU|FRA|NLD|BEL|LUX|AUT|ESP|PRT|IRL|DNK|SWE|NOR|FIN|"
    r"POL|CZE|GBR|RS|BA|MK|UA|XK|SRB|ROU|BGR|GRC|HRV|SVN|SVK|HUN|EST|LVA|LTU|ISL|MLT|CYP|UKR)(?![A-Za-z])")
VAGUE = re.compile(r"^\s*$|remote|anywhere|worldwide|global|multiple|\d+\s+locations|various|"
                   r"flexible|hybrid|home ?office|to be defined|tbd|several", re.I)
NON_EU_WORDS = re.compile(
    r"(?<![A-Za-z])(?:United States|America|Canada|India|China|Japan|Korea|Singapore|"
    r"Australia|Brazil|Mexico|Israel|Taiwan|Hong Kong|Malaysia|Philippines|Vietnam|Thailand|"
    r"Indonesia|United Arab Emirates|Dubai|Saudi|Qatar|Egypt|South Africa|Argentina|Chile|"
    r"Colombia|Turkey|Türkiye|New Zealand|Kenya|Nigeria|Pakistan|Morocco|Tunisia|Costa Rica|"
    r"San Francisco|New York|Seattle|Boston|Toronto|Bangalore|Bengaluru|Hyderabad|Tokyo|Seoul|"
    r"Shanghai|Beijing|Shenzhen|Sydney|Tel Aviv|"
    r"Santa Clara|San Jose|Sunnyvale|Mountain View|Palo Alto|Menlo Park|Redmond|Bellevue|Kirkland|"
    r"Cupertino|Austin|Los Angeles|San Diego|Chicago|Atlanta|Pittsburgh|Denver|Boulder|Raleigh|"
    r"Portland|Irvine|Houston|Dallas|Philadelphia|Ann Arbor|Washington|Miami|Phoenix|Salt Lake|"
    r"Minneapolis|Detroit|Hillsboro|Folsom|Beaverton|Montr[ée]al|Vancouver|Ottawa|Qu[ée]bec|"
    r"Calgary|Edmonton|Sherbrooke|Pune|Chennai|Gurgaon|Gurugram|Noida|Mumbai|Delhi|Kolkata|"
    r"Ahmedabad|Hsinchu|Taipei|Osaka|Kyoto|Hangzhou|Guangzhou|Wuhan|Nanjing|Suzhou|Chengdu|"
    r"Kuala Lumpur|Penang|Manila|Bangkok|Jakarta|Ho Chi Minh|Hanoi|Melbourne|Brisbane|Perth|"
    r"Auckland|S[ãa]o Paulo|Buenos Aires|Bogot[áa]|Haifa|Herzliya|Jerusalem|Abu Dhabi|Riyadh|Doha|"
    r"Istanbul|Ankara|Cairo|Lagos|Nairobi|Cape Town|Johannesburg|Casablanca|Russia|Moscow|Belarus|"
    r"[ÉE]tats-Unis|Chine|Japon|Inde|Cor[ée]e|Br[ée]sil|Mexique|Australie|Isra[ëe]l|Maroc|Tunisie|"
    r"Alg[ée]rie|Algeria|S[ée]n[ée]gal|Senegal|Liban|Lebanon|Russie|Russland|Vereinigte Staaten|"
    r"Indien|Kanada|Brasilien|Australien|T[üu]rkei|Puerto Rico|Dominican Republic|Sri Lanka|"
    r"Kazakhstan|Uruguay|Peru|Ecuador|Bangladesh|Nepal|Ghana|Ethiopia|Uganda|Rwanda|Bahrain|Kuwait|"
    r"Oman|Mississauga|Ontario|British Columbia|Alberta|Nova Scotia|Manitoba|Alabama|Alaska|Arizona|"
    r"Arkansas|California|Colorado|Connecticut|Delaware|Florida|Hawaii|Idaho|Illinois|Indiana|Iowa|"
    r"Kansas|Kentucky|Louisiana|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|"
    r"Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|North Carolina|North Dakota|Ohio|Oklahoma|"
    r"Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|"
    r"Virginia|Wisconsin|Wyoming|Bay Area|Redwood City|Bethesda|Canberra|Benin|Cotonou|Aguascalientes|"
    r"S[ãa]o Jos[ée])(?![A-Za-z])", re.I)
NON_EU_CODES = re.compile(
    r"(?<![A-Za-z])(?:US|USA|U\.S\.|UAE|IND|CHN|JPN|KOR|SGP|CAN|ISR|BRA|MEX|SF)(?![A-Za-z])|"
    r",\s*(?:AL|AK|AZ|AR|CA|CO|CT|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|"
    r"NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)(?=,|\s|\)|$)|"
    # ISO codes of other countries, as the last part of a place ("Wuxi, Jiangsu, CN", "Bursa, TR")
    r"(?:^|,\s*|\(\s*)(?:CN|MY|TH|VN|TR|MX|JP|BR|PH|KR|TW|ID|AU|NZ|ZA|AR|CL|CO|PE|EG|MA|TN|SA|AE|QA|IL|"
    r"HK|SG|IN|KZ|LK|DO|PR|CR|PK|BD|NG|KE|UY|EC)(?=\s*(?:,|;|\)|$))|"
    r"(?<![A-Za-z])(?:TUR|THA|VNM|MYS|PHL|IDN|AUS|NZL|ZAF|ARG|CHL|COL|EGY|MAR|TUN|SAU|ARE|QAT|HKG|TWN|"
    r"KAZ|LKA|PAK|BGD|NGA|KEN|URY|PER|CRI|DOM|PRI)(?![A-Za-z])")


def loc_status(loc, default="unknown"):
    """eu / no / unknown. A place it does not recognise is "unknown" and kept: a town missing from
    the lists must not drop a posting (Claude judges it). Pass default="no" where only clear places
    should count."""
    loc = loc or ""
    if EU_RE.search(loc) or EU_CODES.search(loc):
        return "eu"
    if NON_EU_WORDS.search(loc) or NON_EU_CODES.search(loc):
        return "no"
    if VAGUE.search(loc):
        return "unknown"
    return default


# ------------------------------------------------------------------- helpers
class Gone(Exception):
    pass


_tls = threading.local()


def session():
    s = getattr(_tls, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
                          "Accept": "application/json, text/html;q=0.9, */*;q=0.8"})
        _tls.s = s
    return s


_CHAINS = {}


def extra_chain(host):
    """Some servers forget to send their intermediate certificate. Fetch it from the
    certificate's AIA field and verify against certifi + that intermediate (still verified)."""
    if host in _CHAINS:
        return _CHAINS[host]
    import ssl
    try:
        import certifi
        import cryptography  # noqa: F401
    except ImportError:  # installed on demand, only needed for a few hosts
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "cryptography", "certifi"], check=False)
        import certifi
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding
    from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID
    leaf = x509.load_pem_x509_certificate(ssl.get_server_certificate((host, 443)).encode())
    aia = leaf.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
    url = next(d.access_location.value for d in aia if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS)
    raw = requests.get(url, timeout=30).content
    try:
        inter = x509.load_der_x509_certificate(raw)
    except Exception:
        inter = x509.load_pem_x509_certificate(raw)
    path = os.path.join(ROOT, f".ca-{host}.pem")
    with open(certifi.where(), encoding="utf-8") as f, open(path, "w", encoding="utf-8") as g:
        g.write(f.read() + "\n" + inter.public_bytes(Encoding.PEM).decode())
    _CHAINS[host] = path
    return path


def http(method, url, *, json_body=None, data=None, headers=None, expect="json", tries=3, delay=0.35):
    last = None
    host = urlsplit(url).hostname
    verify = _CHAINS.get(host, True)
    for i in range(tries):
        try:
            time.sleep(delay)
            try:
                r = session().request(method, url, json=json_body, data=data, headers=headers, timeout=40, verify=verify)
            except requests.exceptions.SSLError:
                if verify is not True:
                    raise
                verify = extra_chain(host)
                r = session().request(method, url, json=json_body, data=data, headers=headers, timeout=40,
                                      verify=verify)
            if r.status_code in (404, 410):
                raise Gone(f"{r.status_code} {url}")
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                time.sleep(4 * (i + 1) ** 2)
                continue
            if 400 <= r.status_code < 500:
                raise RuntimeError(f"HTTP {r.status_code} for {url}")
            if expect == "json":
                return r.json()
            r.encoding = r.encoding or "utf-8"
            return r.text
        except (Gone, RuntimeError):
            raise
        except Exception as e:  # network errors, bad JSON
            last = f"{type(e).__name__}: {e}"
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"{method} {url} failed: {last}")


def strip_html(s):
    if not s:
        return ""
    s = htmlmod.unescape(s) if "&lt;" in s[:2000] else s
    s = re.sub(r"(?is)<(script|style|noscript|svg|header|footer|nav)\b.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</div>|</tr>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmlmod.unescape(s)
    s = re.sub(r"[ \t\r\f\v\xa0]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


REQ_HINT = re.compile(
    r"requirements|qualifications|what you(?:'ll)? bring|what we(?:'re| are) looking for|"
    r"your profile|who you are|about you|you have|you bring|must have|minimum qualifications|"
    r"preferred qualifications|skills|profilo|requisiti|ihr profil|dein profil|votre profil|"
    r"we expect|you should have|ideal candidate", re.I)


def summarize(text):
    """Start of the description plus the requirements part, so the reader can judge fit."""
    text = (text or "").strip()
    if len(text) <= DESC_HEAD + DESC_REQ:
        return text
    head = text[:DESC_HEAD]
    m = REQ_HINT.search(text, DESC_HEAD // 2)
    if m:
        part = text[m.start():m.start() + DESC_REQ]
    else:
        part = text[DESC_HEAD:DESC_HEAD + DESC_REQ]
    return head.rsplit(" ", 1)[0] + " […] " + part.rsplit(" ", 1)[0] + " …"


def signals(text):
    t = text or ""
    years = sorted(set(m.group(0).strip() for m in re.finditer(
        r"\b\d{1,2}\s*(?:\+|-\s*\d{1,2})?\s*(?:\+\s*)?(?:years?|yrs|anni|jahre|ans)\b", t, re.I)))[:6]
    return {
        "years": years,
        "phd": bool(re.search(r"\bph\.?\s?d\b|doctorate|doctoral degree", t, re.I)),
        "german": bool(re.search(r"(fluent|fluency|excellent|very good|proficien\w*|native|business)"
                                 r"[^.\n]{0,40}\bgerman\b|\bdeutsch(kenntnisse)?\b|german (?:is )?(?:a must|required|mandatory)",
                                 t, re.I)),
        "french": bool(re.search(r"(fluent|fluency|excellent|proficien\w*|native)[^.\n]{0,40}\bfrench\b|"
                                 r"french (?:is )?(?:a must|required|mandatory)", t, re.I)),
        "visa": bool(re.search(r"visa sponsorship|sponsor (?:a |your )?visa|relocation support", t, re.I)),
    }


def to_date(x):
    """Return YYYY-MM-DD from epoch (s or ms), ISO strings or RFC-822 dates."""
    if x in (None, "", 0):
        return None
    try:
        if isinstance(x, (int, float)) or (isinstance(x, str) and x.isdigit()):
            v = float(x)
            if v > 1e12:
                v /= 1000.0
            return dt.datetime.fromtimestamp(v, dt.timezone.utc).date().isoformat()
        s = str(x).strip()
        m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
        if m:
            return m.group(1)
        s = re.sub(r"\s+", " ", s)
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %b %Y", "%d %B %Y"):
            try:
                return dt.datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                pass
        d = email.utils.parsedate_to_datetime(s)
        return d.date().isoformat()
    except Exception:
        return None


def wd_posted(text):
    t = (text or "").lower()
    if "today" in t:
        return TODAY.isoformat()
    if "yesterday" in t:
        return (TODAY - dt.timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*days? ago", t)
    if m:
        n = int(m.group(1))
        return None if "+" in t else (TODAY - dt.timedelta(days=n)).isoformat()
    return None


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def clean_url(u):
    try:
        p = urlsplit(u)
        q = "&".join(x for x in p.query.split("&")
                     if x and not x.startswith(("utm_", "feedId", "source=", "src=")))
        return urlunsplit((p.scheme, p.netloc, p.path, q, ""))
    except Exception:
        return u


def rec(src, jid, title, url, loc="", posted=None, desc=None, **extra):
    r = {
        "key": f"{src['id']}:{jid}",
        "src": src["id"],
        "co": src["co"],
        "target": src.get("target"),
        "big": bool(src.get("big")),
        "ats": src["type"],
        "id": str(jid),
        "title": re.sub(r"\s+", " ", htmlmod.unescape(title or "")).strip(),
        "url": clean_url(url),
        "loc": re.sub(r"\s+", " ", loc or src.get("loc_default", "")).strip(),
        "posted": to_date(posted) if posted else None,
    }
    if desc:
        r["desc_full"] = desc
    if src.get("country"):
        r["country"] = src["country"]
    r.update(extra)
    return r


def page_text(url):
    return strip_html(http("GET", url, expect="text"))


def generic_detail(src, r):
    text = page_text(r["url"])
    i = text.find(r["title"][:40]) if r["title"] else -1
    if i > 0:
        text = text[i:]
    r["desc_full"] = text[:12000]
    if not r.get("loc") or loc_status(r["loc"]) == "unknown":
        m = re.search(r"(?:location(?:\(s\)|s)?|standort|sede|lieu|arbeitsort)\s*[:\n]\s*([^\n]{2,120})", text, re.I)
        if m:
            r["loc"] = m.group(1).strip()


def queries_for(src):
    return src.get("queries") or QUERIES


# ------------------------------------------------------------------ adapters
def workday_list(src):
    host, tenant, site = src["host"], src["tenant"], src["site"]
    base = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    hdr = {"Content-Type": "application/json", "Accept": "application/json"}
    out = {}
    for q in queries_for(src):
        for page in range(src.get("pages", 5)):
            body = {"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": q}
            data = http("POST", base, json_body=body, headers=hdr)
            posts = data.get("jobPostings") or []
            hits = 0
            for p in posts:
                path = p.get("externalPath")
                if not path:
                    continue
                jid = path.rstrip("/").rsplit("/", 1)[-1]
                r = rec(src, jid, p.get("title"), f"https://{host}/{site}{path}",
                        p.get("locationsText", ""), wd_posted(p.get("postedOn")), _path=path)
                out[r["key"]] = r
                hits += title_ok(r["title"])
            if len(posts) < 20 or (page >= 1 and hits == 0):
                break
    return list(out.values()), False


def workday_detail(src, r):
    d = http("GET", f"https://{src['host']}/wday/cxs/{src['tenant']}/{src['site']}{r['_path']}",
             headers={"Accept": "application/json"})
    info = d.get("jobPostingInfo") or {}
    if not info.get("title"):
        raise Gone(r["url"])
    locs = [info.get("location")] + list(info.get("additionalLocations") or [])
    country = (info.get("country") or {}).get("descriptor")
    loc = "; ".join(x for x in locs if x)
    if country and country not in loc:
        loc += f" ({country})"
    if loc:
        r["loc"] = loc
    r["desc_full"] = strip_html(info.get("jobDescription"))
    if info.get("startDate"):
        r["posted"] = to_date(info["startDate"])
    if info.get("externalUrl"):
        r["url"] = info["externalUrl"]


def greenhouse_host(src):
    return "boards-api.eu.greenhouse.io" if src.get("eu") else "boards-api.greenhouse.io"


def greenhouse_list(src):
    data = http("GET", f"https://{greenhouse_host(src)}/v1/boards/{src['board']}/jobs")
    out = []
    for j in data.get("jobs", []):
        out.append(rec(src, j["id"], j.get("title"), j.get("absolute_url"),
                       (j.get("location") or {}).get("name", ""),
                       j.get("first_published") or j.get("updated_at")))
    return out, True


def greenhouse_detail(src, r):
    j = http("GET", f"https://{greenhouse_host(src)}/v1/boards/{src['board']}/jobs/{r['id']}")
    r["desc_full"] = strip_html(htmlmod.unescape(j.get("content") or ""))
    if j.get("first_published"):
        r["posted"] = to_date(j["first_published"])
    offices = ", ".join(o.get("name", "") for o in j.get("offices") or [])
    if offices and loc_status(r.get("loc")) != "eu":
        r["loc"] = (r.get("loc", "") + "; " + offices).strip("; ")


def ashby_list(src):
    data = http("GET", f"https://api.ashbyhq.com/posting-api/job-board/{src['board']}")
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")) or ""
        loc = "; ".join(x for x in locs if x) + (f" ({country})" if country else "")
        if j.get("isRemote") or j.get("workplaceType") == "Remote":
            loc += " · remote"
        out.append(rec(src, j["id"], j.get("title"), j.get("jobUrl"), loc, j.get("publishedAt"),
                       desc=j.get("descriptionPlain") or strip_html(j.get("descriptionHtml"))))
    return out, True


def lever_list(src):
    base = "https://api.eu.lever.co" if src.get("eu") else "https://api.lever.co"
    data = http("GET", f"{base}/v0/postings/{src['slug']}?mode=json")
    out = []
    for j in data:
        cat = j.get("categories") or {}
        locs = cat.get("allLocations") or [cat.get("location", "")]
        loc = "; ".join(x for x in locs if x) + (f" ({j['country']})" if j.get("country") else "")
        parts = [j.get("descriptionPlain") or ""]
        for l in j.get("lists") or []:
            parts.append(f"{l.get('text', '')}:\n{strip_html(l.get('content', ''))}")
        parts.append(j.get("additionalPlain") or "")
        out.append(rec(src, j["id"], j.get("text"), j.get("hostedUrl"), loc, j.get("createdAt"),
                       desc="\n".join(p for p in parts if p)))
    return out, True


def smartrecruiters_list(src):
    c = src["company"]
    out = {}
    qs = src.get("queries") or [None]
    for q in qs:
        offset = 0
        while True:
            url = f"https://api.smartrecruiters.com/v1/companies/{c}/postings?limit=100&offset={offset}"
            if q:
                url += "&q=" + quote(q)
            data = http("GET", url)
            content = data.get("content") or []
            for j in content:
                l = j.get("location") or {}
                loc = ", ".join(x for x in [l.get("city"), l.get("region"), (l.get("country") or "").upper()] if x)
                if l.get("remote"):
                    loc += " · remote"
                r = rec(src, j["id"], j.get("name"), f"https://jobs.smartrecruiters.com/{c}/{j['id']}",
                        loc, j.get("releasedDate"))
                out[r["key"]] = r
            offset += 100
            if not content or offset >= min(data.get("totalFound", 0), src.get("max", 1000)):
                break
    return list(out.values()), not src.get("queries")


def smartrecruiters_detail(src, r):
    j = http("GET", f"https://api.smartrecruiters.com/v1/companies/{src['company']}/postings/{r['id']}")
    sec = ((j.get("jobAd") or {}).get("sections")) or {}
    r["desc_full"] = "\n".join(strip_html((sec.get(k) or {}).get("text", ""))
                               for k in ("jobDescription", "qualifications", "additionalInformation"))


def workable_list(src):
    data = http("GET", f"https://apply.workable.com/api/v1/widget/accounts/{src['slug']}?details=true")
    out = []
    for j in data.get("jobs", []):
        loc = ", ".join(x for x in [j.get("city"), j.get("state"), j.get("country")] if x)
        if j.get("telecommuting"):
            loc += " · remote"
        out.append(rec(src, j.get("shortcode") or j.get("id"), j.get("title"),
                       j.get("url") or j.get("shortlink") or j.get("application_url"), loc,
                       j.get("published_on") or j.get("created_at"),
                       desc=strip_html(j.get("description"))))
    return out, True


def recruitee_list(src):
    data = http("GET", f"https://{src['slug']}.recruitee.com/api/offers/")
    out = []
    for j in data.get("offers", []):
        loc = ", ".join(x for x in [j.get("location") or j.get("city"), j.get("country")] if x)
        if j.get("remote"):
            loc += " · remote"
        out.append(rec(src, j["id"], j.get("title"), j.get("careers_url"), loc, j.get("published_at"),
                       desc=strip_html((j.get("description") or "") + "\n" + (j.get("requirements") or ""))))
    return out, True


def parse_rss(text):
    text = re.sub(r"^[^<]*", "", text)
    root = ET.fromstring(text.encode("utf-8"))
    items = []
    for it in root.iter("item"):
        d = {"title": "", "link": "", "pub": "", "desc": "", "locs": []}
        for c in it:
            tag = c.tag.split("}")[-1].lower()
            if tag == "title":
                d["title"] = (c.text or "").strip()
            elif tag == "link":
                d["link"] = (c.text or "").strip()
            elif tag == "pubdate":
                d["pub"] = (c.text or "").strip()
            elif tag == "description":
                d["desc"] = c.text or ""
            elif tag in ("locations", "location"):
                for x in c.iter():
                    t = x.tag.split("}")[-1].lower()
                    if t in ("city", "country", "name") and (x.text or "").strip():
                        d["locs"].append(x.text.strip())
        items.append(d)
    return items


def teamtailor_list(src):
    items = parse_rss(http("GET", f"https://{src['host']}/jobs.rss", expect="text"))
    out = []
    for d in items:
        jid = re.search(r"/jobs/(\d+)", d["link"])
        jid = jid.group(1) if jid else hashlib.md5(d["link"].encode()).hexdigest()[:10]
        loc = ", ".join(dict.fromkeys(d["locs"])) or src.get("loc_default", "")
        out.append(rec(src, jid, d["title"], d["link"], loc, d["pub"], desc=strip_html(d["desc"])))
    return out, True


def sf_rss_list(src):
    out = {}
    host, locale = src["host"], src.get("locale", "en_US")
    qs = [""] + ['"%s"' % q for q in queries_for(src)]
    for q in qs:
        url = f"https://{host}/services/rss/job/?locale={locale}&keywords={quote(q)}"
        try:
            items = parse_rss(http("GET", url, expect="text"))
        except Gone:
            continue
        for d in items:
            m = re.search(r"/job/[^/]+/(\d+)/?", d["link"])
            jid = m.group(1) if m else hashlib.md5(d["link"].encode()).hexdigest()[:10]
            title, loc = d["title"], ""
            m2 = re.match(r"^(.*)\(([^()]*)\)\s*$", title)
            if m2:
                title, loc = m2.group(1).strip(), m2.group(2).strip()
            r = rec(src, jid, title, d["link"], loc, d["pub"], desc=strip_html(d["desc"]))
            out[r["key"]] = r
    return list(out.values()), False


def eightfold_list(src):
    """Keyword searches, or with "locations" and an empty query every posting in those places
    (then the listing is complete)."""
    host, domain = src["host"], src["domain"]
    out = {}
    mode = src.get("api", "pcsx")
    queries, locations = queries_for(src), src.get("locations") or [""]
    if locations == "europe":
        locations = EUROPE_COUNTRIES
    capped = False
    for q in queries:
        for where in locations:
            for page in range(src.get("pages", 5)):
                start = page * 10
                positions = None
                if mode == "pcsx":
                    try:
                        data = http("GET", f"https://{host}/api/pcsx/search?domain={domain}&query={quote(q)}"
                                           f"&location={quote(where)}&start={start}&sort_by=timestamp")
                        positions = (data.get("data") or {}).get("positions") or []
                    except (RuntimeError, Gone):
                        mode = "v2"
                if mode == "v2":
                    data = http("GET", f"https://{host}/api/apply/v2/jobs?domain={domain}&start={start}"
                                       f"&num=10&query={quote(q)}&location={quote(where)}&sort_by=timestamp")
                    positions = data.get("positions") or []
                hits = 0
                for p in positions:
                    locs = p.get("standardizedLocations") or p.get("locations") or [p.get("location", "")]
                    loc = "; ".join(dict.fromkeys(list(p.get("locations") or []) + list(locs)))
                    purl = p.get("positionUrl") or p.get("canonicalPositionUrl") or f"/careers/job/{p['id']}"
                    r = rec(src, p["id"], p.get("name"), urljoin(f"https://{host}", purl), loc,
                            p.get("postedTs") or p.get("t_create") or p.get("creationTs"), _mode=mode)
                    out[r["key"]] = r
                    hits += title_ok(r["title"])
                if len(positions) < 10 or (q and page >= 1 and hits == 0):
                    break
            else:
                capped = True
    return list(out.values()), queries == [""] and src.get("locations") and not capped


def eightfold_detail(src, r):
    host, domain = src["host"], src["domain"]
    if r.get("_mode") == "v2":
        d = http("GET", f"https://{host}/api/apply/v2/jobs/{r['id']}?domain={domain}")
        r["desc_full"] = strip_html(d.get("job_description"))
    else:
        d = http("GET", f"https://{host}/api/pcsx/position_details?position_id={r['id']}&domain={domain}&hl=en")
        d = d.get("data") or {}
        r["desc_full"] = strip_html(d.get("jobDescription"))
        if d.get("locations"):
            r["loc"] = "; ".join(d["locations"])


def avature_list(src):
    host = src["host"]
    out = {}
    for q in src.get("queries") or [""]:
        offset, empty = 0, 0
        for _ in range(src.get("max_pages", 40)):
            path = src["path"].replace("{q}", quote(q))
            sep = "&" if "?" in path else "?"
            url = f"https://{host}{path}{sep}{src.get('offset_param', 'jobOffset')}={offset}"
            html = http("GET", url, expect="text")
            found = 0
            blocks = re.split(r"<article\b", html)
            per_article = len(blocks) > 2
            for b in (blocks[1:] if per_article else [html]):
                for m in re.finditer(r'<a\b[^>]*href="(https?://[^"]*?/JobDetail/[^"]+?)"[^>]*>(.*?)</a>', b, re.S):
                    href, text = htmlmod.unescape(m.group(1)), strip_html(m.group(2))
                    low = text.lower()
                    if (not text or low in ("learn more", "apply", "apply now", "more", "details", "view job")
                            or "share" in href.lower() or len(text) < 4):
                        continue
                    jid = re.search(r"/JobDetail/(?:[^?#]*/)?(\d+)", href)
                    jid = jid.group(1) if jid else hashlib.md5(href.encode()).hexdigest()[:10]
                    seg = re.sub(r"\s+", " ", b[m.end():m.end() + 8000])
                    lm = re.search(r'class="[^"]*location[^"]*"[^>]*>(.*?)</(?:span|div|li)>', seg, re.S)
                    loc = strip_html(lm.group(1)).replace("\n", " ").strip() if lm else ""
                    if not loc:
                        for line in strip_html(seg[:3000]).split("\n")[:6]:
                            if "Job ID" in line or "•" in line or loc_status(line) == "eu":
                                loc = line.split("•")[0].strip()
                                break
                    r = rec(src, jid, text, href, loc)
                    if r["key"] not in out:
                        found += 1
                        out[r["key"]] = r
                    if per_article:
                        break
            if found == 0:
                break
            offset += src.get("step", 6)
    return list(out.values()), not src.get("queries")


def oracle_list(src):
    host, site = src["host"], src["site"]
    out = {}
    for q in src.get("queries") or queries_for(src):
        for page in range(src.get("pages", 3)):
            finder = (f'findReqs;siteNumber={site},limit=25,offset={page * 25},'
                      f'keyword="{q}",sortBy=POSTING_DATES_DESC')
            url = (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
                   f"&expand=requisitionList.secondaryLocations&finder={quote(finder, safe='')}")
            data = http("GET", url)
            items = (data.get("items") or [{}])[0]
            reqs = items.get("requisitionList") or []
            for j in reqs:
                locs = [j.get("PrimaryLocation") or ""] + [s.get("Name", "") for s in j.get("secondaryLocations") or []]
                loc = "; ".join(x for x in locs if x) + (f" ({j['PrimaryLocationCountry']})" if j.get("PrimaryLocationCountry") else "")
                url_j = src.get("job_url", f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{{id}}").format(id=j["Id"])
                r = rec(src, j["Id"], j.get("Title"), url_j, loc, j.get("PostedDate"))
                out[r["key"]] = r
            if len(reqs) < 25:
                break
    return list(out.values()), False


def oracle_detail(src, r):
    finder = f'ById;Id="{r["id"]}",siteNumber={src["site"]}'
    d = http("GET", f"https://{src['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
                    f"?expand=all&onlyData=true&finder={quote(finder, safe='')}")
    it = (d.get("items") or [{}])[0]
    r["desc_full"] = "\n".join(strip_html(it.get(k) or "") for k in
                               ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr"))


def jibe_list(src):
    host = src["host"]
    out = {}
    for q in src.get("queries") or queries_for(src):
        for page in range(1, src.get("pages", 3) + 1):
            data = http("GET", f"https://{host}/api/jobs?page={page}&limit=100&keywords={quote(q)}"
                               f"&sortBy=posted_date&descending=true")
            jobs = data.get("jobs") or []
            for j in jobs:
                d = j.get("data") or {}
                loc = d.get("full_location") or ", ".join(x for x in [d.get("city"), d.get("country")] if x)
                url = src["job_url"].format(id=d.get("req_id"))
                r = rec(src, d.get("req_id") or d.get("slug"), d.get("title"), url, loc,
                        d.get("posted_date") or d.get("create_date"), desc=strip_html(d.get("description")))
                out[r["key"]] = r
            if len(jobs) < 100:
                break
    return list(out.values()), False


def radancy_list(src):
    host = src["host"]
    out = {}
    for q in src.get("queries") or queries_for(src):
        url = (f"https://{host}/en/search-jobs/results?ActiveFacetID=0&CurrentPage=1&RecordsPerPage=100"
               f"&Keywords={quote(q)}&SearchResultsModuleName=Search%20Results"
               f"&SearchFiltersModuleName=Search%20Filters&SortCriteria=0&SortDirection=0&SearchType=5")
        data = http("GET", url, headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"})
        html = data.get("results") or ""
        for m in re.finditer(r'<a\b[^>]*href="(/[^"]*/job/[^"]+)"[^>]*>(.*?)</a>', html, re.S):
            href, inner = m.group(1), m.group(2)
            t = re.search(r"<h\d[^>]*>(.*?)</h\d>", inner, re.S)
            title = strip_html(t.group(1) if t else inner).split("\n")[0]
            after = html[m.end():m.end() + 2000].split("<li", 1)[0]   # some sites put it next to the link
            l = (re.search(r'class="[^"]*location[^"]*"[^>]*>(.*?)</span>', inner, re.S) or
                 re.search(r'class="[^"]*location[^"]*"[^>]*>(.*?)</span>', after, re.S))
            loc = re.sub(r"^\s*location\s*:\s*", "", strip_html(l.group(1)), flags=re.I) if l else ""
            jid = href.rstrip("/").rsplit("/", 1)[-1]
            r = rec(src, jid, title, urljoin(f"https://{host}", href), loc)
            out[r["key"]] = r
    return list(out.values()), False


def google_list(src):
    out = {}
    params = "&".join(f"{k}={quote(v)}" for k, v in src.get("params", []))
    total = None
    for page in range(1, src.get("pages", 10) + 1):
        url = f"https://www.google.com/about/careers/applications/jobs/results?{params}&page={page}"
        html = http("GET", url, expect="text")
        s = html.find("AF_initDataCallback({key: 'ds:1'")
        if s < 0:
            raise RuntimeError("Google: data block not found")
        a = html.find("data:", s) + 5
        data, _ = json.JSONDecoder().raw_decode(html, a)
        jobs = data[0] or []
        total = data[2] if len(data) > 2 else total
        for j in jobs:
            try:
                locs = j[9] or []
                loc = "; ".join(f"{l[0]} ({l[5]})" if len(l) > 5 and l[5] else str(l[0]) for l in locs)
                desc = "\n".join(strip_html(x[1]) for x in (j[3], j[4], j[19] if len(j) > 19 else None)
                                 if isinstance(x, list) and len(x) > 1 and x[1])
                posted = j[12][0] if len(j) > 12 and j[12] else None
                url_j = f"https://www.google.com/about/careers/applications/jobs/results/{j[0]}-{slugify(j[1])}"
                company = j[7] if len(j) > 7 else None
                r = rec(src, j[0], j[1], url_j, loc, posted, desc=desc, company=company)
                if company == "DeepMind":
                    r["co"] = "Google DeepMind"
                out[r["key"]] = r
            except Exception:
                continue
        if not jobs or (total and page * 20 >= total):
            break
    complete = bool(total) and len(out) >= total
    return list(out.values()), complete


def mckinsey_list(src):
    """McKinsey / QuantumBlack careers search API (public, used by mckinsey.com/careers)."""
    base = "https://gateway.mckinsey.com/apigw-x0cceuow60/v1/api/jobs/search"
    hdr = {"Accept": "application/json", "Origin": "https://www.mckinsey.com",
           "Referer": "https://www.mckinsey.com/careers/search-jobs"}
    out = {}
    for q in src.get("queries") or ["quantumblack"]:
        start = 1
        for _ in range(src.get("pages", 6)):
            data = http("GET", f"{base}?pageSize=50&start={start}&lang=en&q={quote(q)}", headers=hdr)
            docs = data.get("docs") or []
            for d in docs:
                loc = ", ".join(d.get("cities") or []) + (" (" + ", ".join(d.get("countries") or []) + ")" if d.get("countries") else "")
                desc = strip_html((d.get("whatYouWillDo") or "") + "\n" + (d.get("yourBackground") or ""))
                url = f"https://www.mckinsey.com/careers/search-jobs/jobs/{d.get('friendlyURL')}"
                r = rec(src, d.get("jobID"), d.get("title"), url, loc, d.get("postedToLinkedInDate"), desc=desc)
                out[r["key"]] = r
            start += 50
            if len(docs) < 50 or start > (data.get("numFound") or 0):
                break
    return list(out.values()), False


def gem_list(src):
    data = http("GET", f"https://api.gem.com/job_board/v0/{src['board']}/job_posts/")
    jobs = data if isinstance(data, list) else (data.get("job_posts") or data.get("jobs") or [])
    out = []
    for j in jobs:
        loc = j.get("location")
        loc = loc.get("name", "") if isinstance(loc, dict) else (loc or "")
        out.append(rec(src, j.get("id"), j.get("title"), j.get("absolute_url") or j.get("url"), loc,
                       j.get("first_published_at") or j.get("created_at") or j.get("updated_at"),
                       desc=strip_html(j.get("content") or j.get("content_plain") or "")))
    return out, True


AMAZON_EU = ("DEU GBR FRA ESP ITA NLD POL IRL LUX CHE SWE AUT BEL CZE ROU DNK FIN NOR PRT GRC HUN BGR SVK "
             "EST LTU LVA HRV SVN ISL MLT CYP").split()


def amazon_list(src):
    """amazon.jobs: the public JSON search behind its search page, limited to European countries."""
    cc = [("normalized_country_code[]", c) for c in src.get("countries") or AMAZON_EU]
    out = {}
    for q in queries_for(src):
        for page in range(src.get("pages", 5)):
            params = [("base_query", q), ("loc_query", ""), ("sort", "recent"), ("result_limit", "100"),
                      ("offset", str(page * 100))] + cc
            data = http("GET", "https://www.amazon.jobs/en/search.json?" + urlencode(params))
            jobs = data.get("jobs") or []
            for j in jobs:
                locs = [j.get("normalized_location") or j.get("location") or ""]
                for x in j.get("locations") or []:
                    try:
                        x = json.loads(x) if isinstance(x, str) else x
                    except ValueError:
                        continue
                    name = x.get("normalizedLocation") or x.get("location")
                    if name and name not in locs:
                        locs.append(name)
                desc = "\n".join(strip_html(j.get(k) or "") for k in
                                 ("description", "basic_qualifications", "preferred_qualifications"))
                r = rec(src, j.get("id_icims") or j.get("id"), j.get("title"),
                        urljoin("https://www.amazon.jobs", j.get("job_path") or ""), "; ".join(x for x in locs if x),
                        j.get("posted_date"), desc=desc, dept=j.get("job_category") or "")
                out[r["key"]] = r
            if len(jobs) < 100:
                break
    return list(out.values()), False


EUROPE_COUNTRIES = ["Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czech Republic", "Czechia", "Denmark", "Estonia",
          "Finland", "France", "Germany", "Greece", "Hungary", "Iceland", "Ireland", "Italy", "Latvia", "Lithuania",
          "Luxembourg", "Malta", "Netherlands", "Norway", "Poland", "Portugal", "Romania", "Serbia", "Slovakia",
          "Slovenia", "Spain", "Sweden", "Switzerland", "United Kingdom"]


def ibm_list(src):
    """IBM: the search API behind ibm.com/careers/search, every posting in a European country. Its job
    pages sit behind a bot check, so the description is the short summary the API returns."""
    countries = {"bool": {"should": [{"term": {"field_keyword_05": c}} for c in src.get("countries") or EUROPE_COUNTRIES]}}
    out, total = {}, None
    for start in range(0, 3000, 100):
        body = {"appId": "careers", "scopes": ["careers2"], "query": {"bool": {"must": []}},
                "post_filter": {"bool": {"must": [countries]}}, "size": 100, "from": start,
                "sort": [{"dcdate": "desc"}, {"_score": "desc"}], "lang": "zz", "localeSelector": {},
                "sm": {"query": "", "lang": "zz"},
                "_source": ["title", "url", "description", "dcdate", "field_keyword_05", "field_keyword_08",
                            "field_keyword_17", "field_keyword_19"]}
        data = http("POST", "https://www-api.ibm.com/search/api/v2", json_body=body,
                    headers={"Content-Type": "application/json", "Accept": "application/json"})
        hits = (data.get("hits") or {}).get("hits")
        if hits is None:
            raise RuntimeError(f"IBM: unexpected answer {str(data)[:200]}")
        total = ((data.get("hits") or {}).get("total") or {}).get("value")
        for h in hits:
            s = h.get("_source") or {}
            m = re.search(r"jobId=(\d+)", s.get("url") or "")
            loc = ", ".join(x for x in [s.get("field_keyword_19"), s.get("field_keyword_05")] if x)
            if s.get("field_keyword_17"):
                loc += f" · {s['field_keyword_17']}"
            r = rec(src, m.group(1) if m else h.get("_id"), s.get("title"), s.get("url"), loc, s.get("dcdate"),
                    desc=strip_html(s.get("description")), dept=s.get("field_keyword_08") or "")
            out[r["key"]] = r
        if len(hits) < 100:
            break
    return list(out.values()), bool(total) and len(out) >= total


APPLE_EU = ["CHEC", "GBR", "DEU", "FRAC", "ITAC", "IRL", "ESPC", "NLD", "SWEC", "DNK", "FINC", "NORC", "POL",
            "AUT", "BELC", "CZE", "PRTC", "HUNC", "ROMC", "GRCC", "LUXC"]


def router_data(html):
    """loaderData of a React Router page (window.__staticRouterHydrationData)."""
    m = re.search(r'__staticRouterHydrationData\s*=\s*JSON\.parse\(("(?:[^"\\]|\\.)*")\)', html)
    if not m:
        raise RuntimeError("hydration data not found")
    return json.loads(json.loads(m.group(1))).get("loaderData") or {}


def apple_list(src):
    """jobs.apple.com: the JSON search its search page calls, with the CSRF token the site hands out.
    A search stops after 15 pages (300 roles), so there is one search per country."""
    s = session()
    s.get("https://jobs.apple.com/en-gb/search", timeout=40)
    tok = s.get("https://jobs.apple.com/api/v1/CSRFToken", timeout=40).headers.get("x-apple-csrf-token")
    if not tok:
        raise RuntimeError("Apple: no CSRF token")
    hdr = {"x-apple-csrf-token": tok, "Content-Type": "application/json", "Accept": "application/json"}
    out, complete = {}, True
    for code in src.get("countries") or APPLE_EU:
        total = 0
        for page in range(1, 16):
            body = {"query": "", "filters": {"locations": [f"postLocation-{code}"]}, "page": page,
                    "locale": "en-gb", "sort": "newest",
                    "format": {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"}}
            for attempt in range(4):   # now and then a search comes back empty for no reason: ask again
                res = (http("POST", "https://jobs.apple.com/api/v1/search", json_body=body, headers=hdr)
                       .get("res") or {})
                jobs = res.get("searchResults") or []
                if jobs or (page > 1 and (page - 1) * 20 >= total) or (page == 1 and attempt == 2):
                    break
                time.sleep(3 * (attempt + 1))
            else:
                complete = False
            total = max(total, res.get("totalRecords") or 0)
            for j in jobs:
                locs = j.get("locations") or []
                loc = "; ".join(dict.fromkeys(l.get("name") or "" for l in locs))
                country = next((l.get("countryName") for l in locs if l.get("countryName")), "")
                if country and country not in loc:
                    loc += f" ({country})"
                jid = j.get("positionId") or j.get("id")
                url = f"https://jobs.apple.com/en-gb/details/{j.get('jobPositionId', jid).replace('PIPE-', '')}"
                if j.get("transformedPostingTitle"):
                    url += "/" + j["transformedPostingTitle"]
                r = rec(src, jid, j.get("postingTitle"), url, loc, j.get("postDateInGMT"),
                        dept=(j.get("team") or {}).get("teamName") or "")
                out[r["key"]] = r
            if len(jobs) < 20:
                break
            if page == 15:
                complete = False
    return list(out.values()), complete


def apple_detail(src, r):
    d = (router_data(http("GET", r["url"], expect="text")).get("jobDetails") or {}).get("jobsData") or {}
    if not d.get("postingTitle"):
        raise Gone(r["url"])
    r["desc_full"] = "\n".join(strip_html(d.get(k) or "") for k in
                               ("jobSummary", "description", "minimumQualifications", "preferredQualifications"))
    if d.get("lowJobTitle") or d.get("highJobTitle"):
        r["level"] = " – ".join(dict.fromkeys(x for x in (d.get("lowJobTitle"), d.get("highJobTitle")) if x))
    if d.get("jobNumber"):
        r["url"] = re.sub(r"/details/[^/]+", f"/details/{d['jobNumber']}", r["url"])


BROWSER = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none",
           "Sec-Fetch-User": "?1", "Upgrade-Insecure-Requests": "1"}
META_OP = "CareersJobSearchResultsDataQuery"


def meta_find_doc(html):
    """The id of META_OP, read from the page's scripts (Meta changes it when it redeploys)."""
    pat = re.compile(META_OP + r'_candidate_portalRelayOperation",\[\],\(function\([^)]*\)\{a\.exports="(\d+)"')
    urls = dict.fromkeys(u.replace("\\/", "/") for u in
                         re.findall(r'https:\\?/\\?/static\.xx\.fbcdn\.net\\?/rsrc\.php\\?/[^"]+?\.js', html))
    for u in urls:
        m = pat.search(http("GET", u, expect="text", delay=0))
        if m:
            return m.group(1)
    raise RuntimeError(f"Meta: {META_OP} not found in {len(urls)} scripts")


def meta_list(src):
    """metacareers.com: the GraphQL query its search page runs, which returns every open role at
    once. It needs the page's LSD token and the query id (doc_id in sources.json)."""
    html = http("GET", "https://www.metacareers.com/jobsearch/", headers=BROWSER, expect="text")
    m = re.search(r'"LSD",\[\],\{"token":"([^"]+)"', html)
    if not m:
        raise RuntimeError("Meta: LSD token not found")
    lsd = m.group(1)
    search = {"q": "", "divisions": [], "offices": [], "roles": [], "leadership_levels": [], "saved_jobs": [],
              "saved_searches": [], "sub_teams": [], "teams": [], "is_leadership": False,
              "is_remote_only": False, "sort_by_new": False, "results_per_page": None}

    def ask(doc):
        form = {"lsd": lsd, "fb_api_caller_class": "RelayModern", "fb_api_req_friendly_name": META_OP,
                "variables": json.dumps({"isLoggedIn": False, "search_input": search, "viewasUserID": None}),
                "doc_id": doc, "server_timestamps": "true"}
        text = http("POST", "https://www.metacareers.com/graphql", data=form, expect="text",
                    headers={"x-fb-lsd": lsd, "Origin": "https://www.metacareers.com", "Accept": "*/*",
                             "Referer": "https://www.metacareers.com/jobsearch/", "Sec-Fetch-Site": "same-origin",
                             "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"})
        try:
            d = json.loads(text.split("\n", 1)[0])
            return d["data"]["job_search_with_featured_jobs"]["all_jobs"]
        except (ValueError, KeyError, TypeError):
            return None

    jobs = ask(src["doc_id"]) if src.get("doc_id") else None
    if jobs is None:
        doc = meta_find_doc(html)
        jobs = ask(doc)
        NOTES[src["id"]] = {"note": f"doc_id is now {doc}: update sources.json"}
        if jobs is None:
            raise RuntimeError("Meta: the job search query gave no data")
    out = []
    for j in jobs:
        out.append(rec(src, j["id"], j.get("title"), f"https://www.metacareers.com/jobs/{j['id']}/",
                       "; ".join(j.get("locations") or []), dept=", ".join(j.get("teams") or [])))
    return out, True


def ld_detail(src, r):
    """Description from the page's schema.org JobPosting (JSON-LD)."""
    html = http("GET", r["url"], headers=BROWSER, expect="text")
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            d = json.loads(block)
        except ValueError:
            continue
        d = next((x for x in (d if isinstance(d, list) else [d]) if x.get("@type") == "JobPosting"), None)
        if not d:
            continue
        r["desc_full"] = "\n".join(strip_html(htmlmod.unescape(str(d.get(k) or "")))
                                   for k in ("description", "responsibilities", "qualifications"))
        if d.get("datePosted"):
            r["posted"] = to_date(d["datePosted"])
        return
    r["desc_full"] = strip_html(html)[:12000]


# ----------------------------------------------------- aggregators (no token)
# Feeds of many companies at once (from career-ops providers). Like the job boards, their postings
# are matched to the company sources and stay out of recent/open.json until phase 3.
def thehub_list(src):
    """The Hub (Nordic and European startups): its JSON feed, every page for the given country code."""
    out, page, pages = [], 1, 1
    while page <= min(pages, src.get("pages", 40)):
        data = http("GET", f"https://thehub.io/api/v2/jobsandfeatured?page={page}"
                           f"&countryCode={src.get('country_code', 'EU')}")
        jobs = (data.get("jobs") or {})
        pages = jobs.get("pages") or 1
        for j in jobs.get("docs") or []:
            l = j.get("location") or {}
            loc = l.get("address") or ", ".join(x for x in [l.get("locality"), l.get("country")] if x)
            if j.get("isRemote"):
                loc += " · remote"
            out.append(rec(src, j["id"], j.get("title"), f"https://thehub.io/jobs/{j['id']}", loc,
                           co=(j.get("company") or {}).get("name") or "?", ats="thehub"))
        page += 1
    return out, page > pages


def landingjobs_list(src):
    """Landing.jobs (Portugal and remote-Europe tech jobs): the public jobs API."""
    out, offset = [], 0
    while True:
        data = http("GET", f"https://landing.jobs/api/v1/jobs?limit=50&offset={offset}")
        for j in data:
            locs = j.get("locations") or []
            loc = "; ".join(", ".join(x for x in [l.get("city"), l.get("country_code")] if x) for l in locs)
            if j.get("remote"):
                loc += " · remote"
            slug = re.search(r"/at/([^/]+)/", j.get("url") or "")
            co = slug.group(1).replace("-", " ").title() if slug else "?"
            desc = "\n".join(strip_html(j.get(k) or "") for k in ("role_description", "main_requirements", "nice_to_have"))
            out.append(rec(src, j["id"], j.get("title"), j.get("url"), loc, j.get("published_at"), desc=desc,
                           co=co, ats="landingjobs"))
        offset += 50
        if len(data) < 50 or offset > 2000:
            break
    return out, True


def joinup_list(src):
    """joinup.ch (Swiss startups): the job list embedded in its browse page."""
    html = http("GET", "https://joinup.ch/browse/jobs", expect="text")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("joinup: __NEXT_DATA__ not found")
    res = ((((json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}).get("serverState") or {})
           .get("initialResults") or {}).get("jobs", {}).get("results") or [{}]
    out = []
    for h in res[0].get("hits") or []:
        if not h.get("slug"):
            continue
        loc = h.get("location")
        loc = loc if isinstance(loc, str) else ((loc or {}).get("name") or (loc or {}).get("city") or "")
        out.append(rec(src, h["slug"], h.get("title") or h.get("headline"), f"https://joinup.ch/job/{h['slug']}",
                       loc or "Switzerland", h.get("created"), co=h.get("startup") or "?", ats="joinup"))
    return out, False


def wttj_list(src):
    """Welcome to the Jungle: the Algolia index behind its search, with the public key from /api/env."""
    env = http("GET", "https://www.welcometothejungle.com/api/env", expect="text")
    env = json.loads(env[env.index("{"):env.rindex("}") + 1])
    app, key = env["PUBLIC_ALGOLIA_APPLICATION_ID"], env["PUBLIC_ALGOLIA_API_KEY_CLIENT"]
    hdr = {"x-algolia-application-id": app, "x-algolia-api-key": key, "Content-Type": "application/json",
           "Referer": "https://www.welcometothejungle.com/", "Origin": "https://www.welcometothejungle.com"}
    out = {}
    for q in queries_for(src):
        for page in range(src.get("pages", 3)):
            params = urlencode({"query": q, "hitsPerPage": 100, "page": page, "filters": src.get("filters", ""),
                                "attributesToRetrieve": "name,slug,organization,offices,remote,published_at_timestamp,"
                                                        "contract_type,experience_level_minimum"})
            data = http("POST", f"https://{app}-dsn.algolia.net/1/indexes/wttj_jobs_production_en/query",
                        json_body={"params": params}, headers=hdr)
            hits = data.get("hits") or []
            for h in hits:
                org = h.get("organization") or {}
                if not h.get("slug") or not org.get("slug"):
                    continue
                loc = "; ".join(", ".join(x for x in [o.get("city"), o.get("country")] if x)
                                for o in h.get("offices") or [])
                if h.get("remote") in ("full", "fulltime"):
                    loc += " · remote"
                r = rec(src, h.get("objectID") or h["slug"], h.get("name"),
                        f"https://www.welcometothejungle.com/en/companies/{org['slug']}/jobs/{h['slug']}", loc,
                        h.get("published_at_timestamp"), co=org.get("name") or "?", ats="wttj")
                out[r["key"]] = r
            if len(hits) < 100:
                break
    return list(out.values()), False


def hn_list(src):
    """Hacker News "Ask HN: Who is hiring?": the comments of the latest monthly thread."""
    hits = http("GET", "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=6")["hits"]
    thread = next(h for h in hits if "who is hiring" in (h.get("title") or "").lower())
    item = http("GET", f"https://hn.algolia.com/api/v1/items/{thread['objectID']}")
    out = []
    for c in item.get("children") or []:
        text = strip_html((c.get("text") or "").replace("<p>", "\n"))
        if not text:
            continue
        first = text.split("\n", 1)[0]
        parts = [p.strip() for p in first.split("|") if p.strip()]
        if len(parts) < 2:
            continue
        loc = "; ".join(p for p in parts[1:] if loc_status(p, default="no") != "no" or
                        re.search(r"remote|onsite|hybrid", p, re.I))
        title = next((p for p in parts[1:] if re.search(r"engineer|scientist|research|developer|machine|learning|"
                                                         r"\bAI\b|\bML\b|data|founding", p, re.I)), first[:120])
        out.append(rec(src, c["id"], title, f"https://news.ycombinator.com/item?id={c['id']}", loc or first[:150],
                       c.get("created_at"), desc=text[:8000], co=parts[0][:80], ats="hn"))
    return out, True


# ------------------------------------------------------- job boards (JobSpy)
# LinkedIn (public guest search, no login) and Indeed, through python-jobspy. It is only
# imported by these two sources, so the rest of the scanner still runs without it.
_JS, _JS_LOCK = {}, threading.Lock()
NOTES = {}   # extra per-source facts for status.json, filled by the listers ("_" keys stay internal)
INDUSTRIAL, MOVED, PHD_DONE = [], set(), set()   # doctoral positions the job scan hands to the PhD profile
BOARDS = ("linkedin", "indeed", "thehub", "landingjobs", "joinup", "wttj", "hn")   # postings of many companies
# Postings from the job boards stay out of recent.json / open.json until the Radar routine reads
# candidates.json (plan, phase 3): the routine reads every new item and they are hundreds a day.
BOARDS_IN_RECENT = False


def jobspy_scraper(site):
    """One scraper per site, shared by search and detail calls. Its session retries the 429s
    that LinkedIn hands out every few pages: JobSpy alone stops at the first one."""
    with _JS_LOCK:
        if site in _JS:
            return _JS[site]
        from jobspy.indeed import Indeed
        from jobspy.linkedin import LinkedIn
        from jobspy.util import set_logger_level
        set_logger_level(1)   # warnings and errors only: no line per result page

        class LinkedInByDate(LinkedIn):
            delay, band_delay = 4, 4   # pause between result pages: 4-8 s

            def _search_params(self):
                return {**super()._search_params(), "sortBy": "DD"}

        sc = {"linkedin": LinkedInByDate, "indeed": Indeed}[site]()
        raw = sc.session.request
        stats = {"retries": 0, "failed": 0, "cut": 0}

        def request(method, url, **kw):
            if sc.deadline and time.time() > sc.deadline:
                stats["cut"] += 1   # JobSpy ends the search (or skips the detail) on this error
                raise requests.ConnectionError("time budget used up")
            kw.setdefault("timeout", 30)
            r = None
            for i in range(5):
                try:
                    r = raw(method, url, **kw)
                except requests.RequestException:
                    if i == 4:
                        raise
                    time.sleep(5 * (i + 1))
                    continue
                if r.status_code != 429 and r.status_code < 500:
                    return r
                stats["retries"] += 1
                time.sleep(12 * (i + 1) + random.uniform(0, 6))
            stats["failed"] += 1
            return r

        sc.session.request = request
        sc.stats, sc.deadline = stats, None
        _JS[site] = sc
        return sc


def jobspy_list(src):
    """LinkedIn or Indeed searches. Each search looks back to when it last finished (between
    hours_min and hours_max; hours_first the first time), so a search left out because the time
    budget ran out, or a night without a run, is caught up later. Oldest searches go first."""
    from jobspy.model import Country, DescriptionFormat, ScraperInput
    site, sid = src["site"], src["id"]
    sc = jobspy_scraper(site)
    last = src.get("_last") or {}
    if site == "linkedin":
        plan = [(q, loc, None) for q in src["queries"] for loc in src.get("locations") or ["Europe"]]
    else:
        plan = [(q, None, c) for c in src["countries"] for q in src["queries"]]
    names = [f"{q} @ {c or loc}" for q, loc, c in plan]
    order = sorted(range(len(plan)), key=lambda i: (last.get(names[i], ""), i))
    t0 = time.time()
    src["_stop_at"] = t0 + 60 * src.get("budget_min", 60)       # details stop here
    search_until = t0 + 60 * src.get("search_min", 40)
    out, done, capped, blocked = {}, {}, [], False
    sc.deadline = search_until
    for i in order:
        if time.time() > search_until:
            break
        q, loc, country = plan[i]
        hours = src.get("hours_first", 24)
        if names[i] in last:
            hours = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(
                last[names[i]].replace("Z", "+00:00"))).total_seconds() / 3600 + 3
        hours = int(min(max(hours, src.get("hours_min", 24)), src.get("hours_max", 120)))
        started = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        failed, cut = sc.stats["failed"], sc.stats["cut"]
        jobs = sc.scrape(ScraperInput(
            search_term=q, location=loc, hours_old=hours, results_wanted=1000,
            country=Country.from_string(country) if country else Country.USA,
            description_format=DescriptionFormat.PLAIN)).jobs
        if sc.stats["failed"] > failed:
            blocked = True   # still refused after the retries: the search is partial, stop here
        elif sc.stats["cut"] == cut:
            done[names[i]] = started   # a search cut by the time budget is redone next time
        if len(jobs) >= 990:
            capped.append(names[i])
        for j in jobs:
            jid = (j.id or "").split("-", 1)[-1] or hashlib.md5(j.job_url.encode()).hexdigest()[:12]
            loc_s = j.location.display_location() if j.location else ""
            if j.is_remote and "remote" not in loc_s.lower():
                loc_s += " · remote"
            r = rec(src, jid, j.title, j.job_url, loc_s, j.date_posted.isoformat() if j.date_posted else None,
                    desc=j.description, co=(j.company_name or "").strip() or "?", ats=site,
                    apply_url=j.job_url_direct)
            out[r["key"]] = r
        if blocked:
            break
    sc.deadline = src["_stop_at"]
    note = {"searches": f"{len(done)}/{len(plan)}", "retries_429": sc.stats["retries"],
            "search_min": round((time.time() - t0) / 60, 1), "_done": done}
    if blocked:
        note["note"] = f"{site} kept refusing (429): stopped after {len(done)} of {len(plan)} searches"
    elif len(done) < len(plan):
        note["note"] = f"time budget used up after {len(done)} of {len(plan)} searches (the rest go first next time)"
    if capped:
        note["capped"] = capped   # 1000 results: LinkedIn shows no more, split the search
    NOTES[sid] = note
    if not out and not done:
        raise RuntimeError(f"{site}: no search finished ({sc.stats['retries']} refusals)")
    return list(out.values()), False


def jobspy_detail(src, r):
    if src["site"] != "linkedin":
        return
    dup = COMPANIES.direct_match(r)
    if dup:
        r["dup_of"] = dup   # already listed by the company's own adapter: no need for the description
        return
    from jobspy.model import DescriptionFormat, ScraperInput
    sc = jobspy_scraper("linkedin")
    sc.scraper_input = ScraperInput(description_format=DescriptionFormat.PLAIN)
    time.sleep(random.uniform(1.5, 3))
    d = sc._fetch_details(r["id"])
    if d.get("description"):
        r["desc_full"] = d["description"]
    if d.get("job_level"):
        r["level"] = d["job_level"]


# ------------------------------------------------- companies across sources
CO_DROP = re.compile(r"\b(?:gmbh|ag|se|sa|sas|spa|srl|bv|nv|inc|llc|ltd|limited|plc|corp|corporation|co|kg|oy|ab|"
                     r"as|group|holding|holdings|company|technologies|international|the)\b")
GENDER = re.compile(r"\(\s*(?:[a-z]{1,3}\.?\s*[/|,*]\s*){1,3}[a-z]{1,4}\.?\s*\)|\b[mwfdh]\s*/\s*[mwfdhx](?:\s*/\s*[dxi])?\b|"
                    r"\(?\ball genders?\)?|\(?alle geschlechter\)?|\(?\bany gender\)?")
PLACE_SYN = {"munchen": "munich", "muenchen": "munich", "zuerich": "zurich", "koln": "cologne", "wien": "vienna",
             "milano": "milan", "torino": "turin", "roma": "rome", "lisboa": "lisbon", "praha": "prague",
             "warszawa": "warsaw", "kobenhavn": "copenhagen", "goteborg": "gothenburg", "bruxelles": "brussels",
             "brussel": "brussels", "geneve": "geneva", "den haag": "the hague", "firenze": "florence",
             "napoli": "naples", "nurnberg": "nuremberg", "krakow": "krakow", "athina": "athens"}


COUNTRY_NAMES = {unicodedata.normalize("NFKD", c).encode("ascii", "ignore").decode().lower() for c in
                 EUROPE_COUNTRIES + ["uk", "england", "scotland", "wales", "deutschland", "schweiz", "suisse",
                                     "svizzera", "italia", "espana", "nederland", "the netherlands", "belgique",
                                     "osterreich", "polska", "sverige", "norge", "danmark", "suomi", "europe",
                                     "european union", "emea", "european economic area"]}


def ascii_low(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def co_key(name):
    return " ".join(CO_DROP.sub(" ", re.sub(r"[^a-z0-9]+", " ", ascii_low(name))).split())


def title_key(t):
    return " ".join(re.findall(r"[a-z0-9+#]+", GENDER.sub(" ", ascii_low(t))))


def same_title(a, b, loose=False):
    """Same normalised title; loose also accepts one title inside the other ("AI Filmmaker" and
    "AI Filmmaker (100% Remote Worldwide)"), which is fine to decide that a company lists a role,
    not to merge two postings."""
    if a == b:
        return True
    a, b = set(a.split()), set(b.split())
    if loose and min(len(a), len(b)) >= 2 and (a <= b or b <= a):
        return True
    return len(a & b) / max(1, len(a | b)) >= 0.75


def city(loc):
    """First place named in a location string, normalised ("Greater London Area" -> "london")."""
    c = ascii_low(re.split(r"[,;(·|]", loc or "")[0])
    c = re.sub(r"\b(?:greater|metropolitan|area|region|city of|remote)\b", " ", c)
    c = " ".join(c.split())
    return PLACE_SYN.get(c, c)


def same_place(board_loc, direct_loc):
    c = city(board_loc)
    if not c or loc_status(c, default="no") == "unknown" or c in COUNTRY_NAMES:
        return True
    d = ascii_low(direct_loc)
    for k, v in PLACE_SYN.items():
        d = d.replace(k, v)
    return c in d or not city(direct_loc)


class Companies:
    """Maps company names seen on the job boards to the sources in sources.json."""

    def __init__(self, sources=(), jobs=None):
        self.keys = {}
        for s in sources:
            if s["type"] in BOARD_TYPES:
                continue
            parts = re.split(r"\s+/\s+|\s+[–-]\s+|[()]", s["co"]) + list(s.get("aliases") or [])
            for k in {co_key(p) for p in parts}:
                if len(k) >= 2:
                    self.keys.setdefault(k, []).append(s)
        self.listed = {}   # source id -> its postings, from the state
        for v in (jobs or {}).values():
            if v.get("ats") not in BOARDS and v.get("ats") != "watch":
                self.listed.setdefault(v.get("src"), []).append(v)

    def find(self, name):
        n, best = co_key(name), ""
        for k in self.keys:
            if (n == k or n.startswith(k + " ")) and len(k) > len(best):
                best = k
        return self.keys.get(best, [])

    def direct_match(self, r, any_place=False):
        """Key of the company-site posting this board posting duplicates, if any."""
        t = title_key(r["title"])
        for s in self.find(r["co"]):
            for v in self.listed.get(s["id"], []):
                if same_title(t, title_key(v.get("title")), loose=any_place) and \
                        (any_place or same_place(r.get("loc"), v.get("loc"))):
                    return v["key"]
        return None


COMPANIES = Companies()
BOARD_TYPES = ("jobspy", "thehub", "landingjobs", "joinup", "wttj", "hn")


def links_list(src):
    out = {}
    urls = src.get("urls") or [src["url"]]
    pat = re.compile(src["pattern"])
    for u in urls:
        try:
            html = http("GET", u, expect="text")
        except Gone:
            continue
        for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.S):
            href = htmlmod.unescape(m.group(1))
            pm = pat.search(href)
            if not pm:
                continue
            text = strip_html(m.group(2)).split("\n")[0].strip()
            full = urljoin(u, href)
            jid = pm.group(1) if pm.groups() else hashlib.md5(full.encode()).hexdigest()[:10]
            generic = (len(text) < 4 or text.lower() in
                       ("apply", "more", "details", "learn more", "read more", "view", "apply now", "scopri di più"))
            if generic:
                text = slugify(urlsplit(full).path.rstrip("/").rsplit("/", 1)[-1]).replace("-", " ")
            r = rec(src, jid, text, full)
            old = out.get(r["key"])
            if old and (generic or len(old["title"]) >= len(r["title"])):
                continue
            out[r["key"]] = r
    return list(out.values()), True


def sitemap_list(src):
    xml = http("GET", src["url"], expect="text")
    pat = re.compile(src["pattern"])
    out = []
    for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml):
        m = pat.search(loc)
        if not m:
            continue
        jid = m.group(1) if m.groups() else hashlib.md5(loc.encode()).hexdigest()[:10]
        seg = urlsplit(loc).path.rstrip("/").rsplit("/", 1)[-1]
        if src.get("slug_strip"):
            seg = re.sub(src["slug_strip"], "", seg)
        title = seg.replace("-", " ").strip()
        out.append(rec(src, jid, title, loc, _slug_title=True))
    return out, True


def sitemap_detail(src, r):
    html = http("GET", r["url"], expect="text")
    m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html) or \
        re.search(r"<title>(.*?)</title>", html, re.S)
    if m:
        t = htmlmod.unescape(m.group(1)).strip()
        t = re.split(r"\s+[|@–-]\s+", t)[0].strip()
        if t:
            r["title"] = t
    text = strip_html(html)
    r["desc_full"] = text[:12000]
    if not r.get("loc"):
        mm = re.search(r"(?:location|standort|sede|lieu)\s*[:\n]\s*([^\n]{2,80})", text, re.I)
        if mm:
            r["loc"] = mm.group(1).strip()


def ethz_list(src):
    """ETH Zurich job portal: one server-rendered page with every open position."""
    html = http("GET", src.get("url", "https://jobs.ethz.ch/?lang=en"), expect="text")
    out = []
    for m in re.finditer(r'<a href="(/job/view/[^"]+)" class="job-ad__item__link".*?job-ad__item__title">(.*?)</h3>'
                         r'.*?job-ad__item__details">(.*?)</div>.*?job-ad__item__company">(.*?)</div>', html, re.S):
        path, title, details, company = m.groups()
        date, _, dept = strip_html(company).partition("|")
        d = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", date.strip())
        posted = f"{d.group(3)}-{d.group(2)}-{d.group(1)}" if d else None
        out.append(rec(src, path.rsplit("/", 1)[-1], strip_html(title), urljoin("https://jobs.ethz.ch", path),
                       strip_html(details), posted, dept=re.sub(r"\s+", " ", dept).strip(" |")))
    return out, True


def prospective_list(src):
    """prospective.ch job feeds (University of Zurich and others)."""
    out, off = [], 0
    while True:
        d = http("GET", f"https://ohws.prospective.ch/public/v1/medium/{src['medium']}/jobs?lang=en&limit=100&offset={off}")
        jobs = d.get("jobs") or []
        for j in jobs:
            sz = j.get("szas") or {}
            desc = strip_html(" ".join(str(sz.get(k) or "") for k in
                                       ("sza_tasks", "sza_profile", "sza_requirements", "sza_offer", "sza_starting_date")))
            out.append(rec(src, j["id"], j.get("title"), (j.get("links") or {}).get("directlink"),
                           sz.get("sza_location.city") or src.get("loc_default", ""), j.get("start_date"),
                           desc=desc, dept=strip_html(sz.get("sza_department") or "")))
        off += 100
        if not jobs or off >= (d.get("total") or 0):
            break
    return out, True


def nuxt_values(html):
    """Decode the __NUXT_DATA__ payload of a Nuxt page and return every object in it."""
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return []
    arr = json.loads(m.group(1))

    def res(v, depth=0):
        if depth > 10 or isinstance(v, bool) or not isinstance(v, int) or not 0 <= v < len(arr):
            return v
        x = arr[v]
        if isinstance(x, dict):
            return {k: res(y, depth + 1) for k, y in x.items()}
        if isinstance(x, list):
            if x and x[0] in ("ShallowReactive", "Reactive", "Ref", "ShallowRef"):
                return res(x[1], depth + 1)
            return [res(y, depth + 1) for y in x]
        return x
    return [res(i) for i, x in enumerate(arr) if isinstance(x, dict)]


def academictransfer_list(src):
    """AcademicTransfer (all Dutch universities): keyword searches, 10 results per page."""
    out = {}
    for q in queries_for(src):
        for page in range(src.get("pages", 10)):
            url = f"https://www.academictransfer.com/en/jobs/?q={quote(q)}" + (f"&offset={page * 10}" if page else "")
            vals = [v for v in nuxt_values(http("GET", url, expect="text"))
                    if "absolute_url" in v and "function_types" in v]
            for v in vals:
                if 9 not in (v.get("function_types") or []):   # 9 = PhD position
                    continue
                sal = None
                if v.get("min_salary"):
                    sal = f"€{v['min_salary']}–{v.get('max_salary') or v['min_salary']} lordi/mese"
                r = rec(src, v.get("external_id") or v.get("id"), v.get("title"), v.get("absolute_url"),
                        v.get("city") or "", v.get("created_datetime"),
                        desc=strip_html((v.get("description") or "") + "\n" + (v.get("requirements") or "")),
                        dept=v.get("department_name") or "", deadline=to_date(v.get("end_date")), salary=sal,
                        _is_phd=True)
                r["co"] = v.get("organisation_name") or r["co"]
                out[r["key"]] = r
            if len(vals) < 10:
                break
    return list(out.values()), False


def ugent_list(src):
    """Ghent University: table of open doctoral fellowships."""
    html = http("GET", src["url"], expect="text")
    out = []
    for row in re.findall(r"<tr.*?</tr>", html, re.S):
        m = re.search(r'href="([^"]*/doctoral-fellow-(\d+))"', row)
        if not m:
            continue
        cells = [strip_html(c) for c in re.findall(r"<td.*?</td>", row, re.S)]
        dl = re.search(r"(\d{4}-\d{2}-\d{2})", row)
        out.append(rec(src, m.group(2), cells[0] if cells else "Doctoral fellow", urljoin(src["url"], m.group(1)),
                       src.get("loc_default", ""), None, dept=cells[1] if len(cells) > 1 else "",
                       deadline=dl.group(1) if dl else None))
    return out, True


def kuleuven_list(src):
    """KU Leuven jobsite: the search API behind the PhD vacancy page (15 results per page)."""
    base = ("https://icts-p-fii-toep-component-filter2.cloud.icts.kuleuven.be/api/projects/"
            + src.get("project", "Jobsite_phd") + "/search")
    hdr = {"Origin": "https://www.kuleuven.be", "Referer": "https://www.kuleuven.be/personeel/jobsite/jobs/phd?lang=en"}
    out = {}
    for page in range(src.get("pages", 10)):
        d = http("POST", base + (f"?page={page}" if page else ""),
                 json_body={"_locale": "en", "environment": "production"}, headers=hdr)
        hits = d.get("hits") or []
        for h in hits:
            s = h.get("_source") or {}
            p = s.get("posting") or {}
            jid = s.get("id") or h.get("_id")
            ab = str(s.get("applyBefore") or "")
            r = rec(src, jid, p.get("title"), f"https://www.kuleuven.be/personeel/jobsite/jobs/{jid}?lang=en",
                    s.get("city") or "", None, desc=p.get("teaser"), dept=s.get("orgUnitDescription") or "",
                    deadline=f"{ab[:4]}-{ab[4:6]}-{ab[6:8]}" if re.fullmatch(r"\d{8}", ab) else None)
            out[r["key"]] = r
        if not hits or len(out) >= (d.get("total_nb_hits") or 0):
            break
    return list(out.values()), True


MSCA_RE = re.compile(r"marie[\s-]+(?:sk[lł]odowska|curie)|sk[lł]odowska|\bMSCA\b|doctoral networks?|\bCOFUND\b", re.I)
# EURAXESS research fields kept whatever the title says (wide on purpose: Claude judges the fit).
EURAXESS_FIELDS = re.compile(r"computer science|engineering|mathematics|technology", re.I)


def msca_kind(text):
    if re.search(r"\bcofund\b", text or "", re.I):
        return "msca-cofund"
    if MSCA_RE.search(text or ""):
        return "msca-dn"
    return "academic"


def euraxess_get(url):
    """EURAXESS answers 429 after a burst of requests and keeps refusing for a few minutes: read at
    a slow pace that slows down further on each refusal, and stop once the time budget is spent."""
    for attempt in range(5):
        if time.time() > EURAXESS["stop"]:
            raise RuntimeError("EURAXESS time budget spent")
        try:
            html = http("GET", url, expect="text", delay=EURAXESS["pace"], tries=1)
            EURAXESS["ok"] += 1
            return html
        except RuntimeError:
            EURAXESS["refused"] += 1
            EURAXESS["pace"] = min(EURAXESS["pace"] + 10, 120)
            if attempt == 4:
                raise
            time.sleep(120)


EURAXESS = {"stop": 1e12, "pace": 20, "ok": 0, "refused": 0}


def euraxess_list(src):
    """EURAXESS: every PhD position (the positions filter in the URL, f[0]=positions:php_positions),
    all pages every night, newest first. Kept: subjects matching PHD_TOPIC in title, summary or
    research field, or a research field in EURAXESS_FIELDS. kind: msca-dn / msca-cofund when the
    funding programme or the text cites MSCA, otherwise academic (refined on the full offer)."""
    EURAXESS.update(stop=time.time() + 60 * src.get("budget_min", 240), pace=src.get("pace_s", 20))
    q = "&".join(f"f%5B{i}%5D={quote(f)}" for i, f in enumerate(src.get("filters", ["positions:php_positions"])))
    out, complete, page = {}, False, 0
    while page < src.get("pages", 250):
        try:
            html = euraxess_get(f"https://euraxess.ec.europa.eu/jobs/search?{q}&page={page}")
        except RuntimeError as e:   # refused or out of time: keep what was read (closures go by deadline)
            NOTES.setdefault(src["id"], {})["note"] = f"stopped at page {page}: {str(e)[-40:]}"
            break
        blocks = html.split('<div id="job-teaser-content">')[1:]
        if not blocks:
            complete = True
            break
        for b in blocks:
            m = re.search(r'href="/jobs/(\d+)"', b)
            if not m:
                continue
            jid = int(m.group(1))
            f = {k: strip_html(v) for k, v in
                 re.findall(r'class="id-([A-Za-z-]+)[^"]*".*?</span></div><div[^>]*>(.*?)</div>', b, re.S)}
            t = re.search(r'<h3[^>]*>.*?<span>(.*?)</span>', b, re.S)
            title = strip_html(t.group(1)) if t else ""
            org = re.search(r'primary-meta-item"><a[^>]*>(.*?)</a>', b, re.S)
            posted = re.search(r"Posted on:\s*([^<]+)<", b)
            snippet = re.search(r'ecl-content-block__description">(.*?)</div>', b, re.S)
            snippet = strip_html(snippet.group(1)) if snippet else ""
            field, funding = f.get("Research-Field", ""), f.get("Funding-Programme", "")
            if jid in out or PHD_EXCLUDE.search(title) or not (
                    PHD_TOPIC.search(" ".join([title, snippet, field])) or EURAXESS_FIELDS.search(field)):
                continue
            loc = re.sub(r"^Number of offers:\s*\d+,\s*", "", f.get("Work-Locations", ""))
            dl = re.match(r"\s*(\d{1,2} \w{3} \d{4})", f.get("Application-Deadline", ""))
            out[jid] = rec(src, jid, title, f"https://euraxess.ec.europa.eu/jobs/{jid}", loc,
                           posted.group(1).strip() if posted else None,
                           co=strip_html(org.group(1)) if org else "?", dept=field,
                           deadline=to_date(dl.group(1)) if dl else None, funding=funding,
                           kind=msca_kind(" ".join([title, snippet, funding])), _is_phd=True)
        page += 1
    NOTES.setdefault(src["id"], {}).update(pages=page, requests_ok=EURAXESS["ok"], refused=EURAXESS["refused"],
                                           pace_s=EURAXESS["pace"])
    return list(out.values()), False   # never "complete": closures go by deadline (close_by_deadline)


def euraxess_detail(src, r):
    html = euraxess_get(r["url"]).split('<div id="job-teaser-content">')[0]   # drop the related offers below
    html = re.sub(r"(?is)<(select|form)\b.*?</\1>", " ", html)   # the search form lists "MSCA", "COFUND"
    text = strip_html(html)
    i = text.find(r["title"][:40]) if r["title"] else -1
    text = text[i:] if i > 0 else text
    if r.get("kind") == "academic":
        r["kind"] = msca_kind(text + " " + (r.get("funding") or ""))
    r["desc_full"] = text[:12000]


ML_AI = re.compile(r"machine learning|apprentissage|deep learning|intelligence artificielle|artificial intelligence|"
                   r"(?-i:\bIA\b|\bAI\b|\bLLMs?\b)|neural|neurone|vision par ordinateur|computer vision|"
                   r"traitement (?:du signal|d'images)|signal processing|data science|science des données", re.I)
# ABG theses are kept on any subject near the profile, in French or English (wide on purpose).
ABG_TOPIC = re.compile(r"donn[ée]es|capteur|imagerie|sant[ée]|m[ée]dic|informatique|[ée]lectroni|automatique|"
                       r"commande|contr[ôo]le|intelligen|neuro|calcul|num[ée]rique|algorithm|statisti|optimi[sz]ation|"
                       r"mod[ée]lisation|ing[ée]ni|math[ée]mati|physiolog|t[ée]l[ée]com|r[ée]seaux|embarqu|syst[èe]mes",
                       re.I)


def abg_list(src):
    """ABG (abg.asso.fr): French thesis offers, CIFRE included. Its search ignores the criteria, so
    every page is read and theses near the profile are kept here (ML_AI, PHD_TOPIC, ABG_TOPIC)."""
    session().get("https://www.abg.asso.fr/fr/candidatOffres", timeout=40)
    hdr = {"X-Requested-With": "XMLHttpRequest", "Accept": "text/html, */*"}
    out, last, page = [], 1, 1
    while page <= min(last, src.get("pages", 80)):
        html = http("POST", "https://www.abg.asso.fr/fr/candidatOffres/recherche", expect="text", headers=hdr,
                    data={"page": str(page), "orderBy": "updated_at_desc", "autoload": "1"})
        last = max([last] + [int(x) for x in re.findall(r"/recherche/page/(\d+)", html)])
        for b in html.split('class="item itLien it_offre')[1:]:
            m = re.search(r'<h2><a href="([^"]*id_offre/(\d+)[^"]*)">(.*?)</a>', b, re.S)
            if not m:
                continue
            g = {k: strip_html(v) for k, v in re.findall(r'<div class="(societe|adresse|text|expertises|mots|l_date|type)">'
                                                           r'(.*?)</div>', b, re.S)}
            if not re.search(r"th[èe]se", g.get("type", ""), re.I):   # "Sujet de Thèse"
                continue
            title = strip_html(m.group(3))
            text = " ".join([title, g.get("text", ""), g.get("expertises", ""), g.get("mots", "")])
            if not (ML_AI.search(text) or PHD_TOPIC.search(text) or ABG_TOPIC.search(text)):
                continue
            d = re.match(r"(\d{2})/(\d{2})/(\d{4})", g.get("l_date", ""))
            org = re.sub(r"\s*Th[èe]se\s*$", "", g.get("societe", "")).strip()
            out.append(rec(src, m.group(2), title, urljoin("https://www.abg.asso.fr", m.group(1)),
                           g.get("adresse", "").replace(" ,", ","), f"{d.group(3)}-{d.group(2)}-{d.group(1)}" if d else None,
                           co=org or "?", dept=g.get("expertises", ""), _is_phd=True,
                           kind="cifre" if re.search(r"\bcifre\b", text, re.I) else "academic"))
        page += 1
    return out, page > last


def abg_detail(src, r):
    generic_detail(src, r)
    if re.search(r"\bcifre\b", r.get("desc_full") or "", re.I):
        r["kind"] = "cifre"


def handoff():
    data = load(os.path.join(OUT, "phd_industrial.json"), None)
    if not data or days_since(data.get("generated", "")) > 2:
        raise RuntimeError("out/phd_industrial.json missing or older than 2 days: run the job scan first")
    return data.get("items", [])


def industrial_list(src):
    """Doctoral positions found by the job scan in company sources (out/phd_industrial.json)."""
    out = []
    for it in handoff():
        if it.get("board"):
            continue
        out.append(rec(src, it["key"], it["title"], it["url"], it.get("loc", ""), it.get("posted"),
                       desc=it.get("desc"), co=it["co"], kind=it.get("kind", "industrial"), dept=it.get("dept", ""),
                       _is_phd=True))
    return out, True


def board_phd_list(src):
    """Doctoral positions found on LinkedIn, Indeed and the other job boards (out/phd_industrial.json,
    "board": true). The same position on two boards, or already listed by its university's own
    source, appears once."""
    def names(co):   # distinctive words of an employer: "Friedrich-Schiller-Universität Jena" -> friedrich schiller jena
        return {PLACE_SYN.get(w, w) for w in co.split() if len(w) > 3 and not PHD_CO_GENERIC.match(w)}

    out, seen = [], {}   # title -> employers already listed ("Erasmus MC" and "Erasmus MC-Klinische Chemie")
    for it in sorted(handoff(), key=lambda it: it.get("via") != "linkedin"):
        co, t, c = co_key(it["co"]), title_key(it["title"]), city(it.get("loc"))
        if not it.get("board") or COMPANIES.direct_match(it, any_place=True) or \
                any(co.startswith(o) or o.startswith(co) or (c == oc and names(co) & names(o)) for o, oc in seen.get(t, ())):
            continue
        seen.setdefault(t, []).append((co, c))
        out.append(rec(src, it["key"], it["title"], it["url"], it.get("loc", ""), it.get("posted"),
                       desc=it.get("desc"), co=it["co"], kind=it.get("kind", "academic"), dept=it.get("dept", ""),
                       via=it.get("via"), _is_phd=True))
    return out, True


def watch_list(src):
    """Pages without a job list: report when their text changes."""
    text = page_text(src["url"])
    return [{"_watch_text": text}], True


ADAPTERS = {
    "workday": (workday_list, workday_detail),
    "greenhouse": (greenhouse_list, greenhouse_detail),
    "ashby": (ashby_list, None),
    "lever": (lever_list, None),
    "smartrecruiters": (smartrecruiters_list, smartrecruiters_detail),
    "workable": (workable_list, None),
    "recruitee": (recruitee_list, None),
    "teamtailor": (teamtailor_list, None),
    "sf_rss": (sf_rss_list, None),
    "eightfold": (eightfold_list, eightfold_detail),
    "avature": (avature_list, generic_detail),
    "oracle": (oracle_list, oracle_detail),
    "jibe": (jibe_list, None),
    "radancy": (radancy_list, generic_detail),
    "google": (google_list, None),
    "gem": (gem_list, None),
    "mckinsey": (mckinsey_list, None),
    "links": (links_list, generic_detail),
    "sitemap": (sitemap_list, sitemap_detail),
    "watch": (watch_list, None),
    "board_phd": (board_phd_list, None),
    "ethz": (ethz_list, generic_detail),
    "prospective": (prospective_list, None),
    "academictransfer": (academictransfer_list, None),
    "ugent": (ugent_list, generic_detail),
    "kuleuven": (kuleuven_list, generic_detail),
    "euraxess": (euraxess_list, euraxess_detail),
    "abg": (abg_list, abg_detail),
    "industrial": (industrial_list, None),
    "amazon": (amazon_list, None),
    "ibm": (ibm_list, None),
    "apple": (apple_list, apple_detail),
    "meta": (meta_list, ld_detail),
    "jobspy": (jobspy_list, jobspy_detail),
    "thehub": (thehub_list, ld_detail),
    "landingjobs": (landingjobs_list, None),
    "joinup": (joinup_list, ld_detail),
    "wttj": (wttj_list, ld_detail),
    "hn": (hn_list, None),
}


def phd_item(r, src_id, board=False):
    """A doctoral position found by the job scan, as handed to the PhD profile."""
    desc = r.get("desc_full") or r.get("desc") or ""
    co = r.get("co") or "?"
    academic = board and ACADEMIC_CO.search(co)
    cifre = re.search(r"\bcifre\b", r["title"] + " " + desc, re.I)
    kind = "cifre" if cifre or (FRANCE_RE.search(r.get("loc") or "") and not academic) else \
        "academic" if academic else "industrial"
    it = {"key": r["key"], "src": src_id, "co": co, "title": r["title"], "url": r["url"], "loc": r.get("loc", ""),
          "posted": r.get("posted"), "dept": r.get("dept", ""), "kind": kind, "desc": desc[:12000] or None}
    if board:   # seen once by a search over the last days: the job scan keeps handing it over for a while
        it.update(board=True, via=r.get("ats"), seen=(r.get("last_seen") or NOW_ISO)[:10])
    return it


# ----------------------------------------------------------------- run logic
def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def days_since(iso):
    try:
        d = dt.datetime.strptime(iso[:10], "%Y-%m-%d").date()
        return (TODAY - d).days
    except Exception:
        return 999


def run_source(src, known_keys, has_detail):
    t0 = time.time()
    st = {"id": src["id"], "co": src["co"], "type": src["type"], "ok": False}
    try:
        lister, detailer = ADAPTERS[src["type"]]
        recs, complete = lister(src)
        if src["type"] == "watch":
            st.update(ok=True, raw=1, matched=0, complete=True, secs=round(time.time() - t0, 1))
            return src, recs, True, st
        st["raw"] = len(recs)
        matched, seen, phd = [], set(), []
        for r in recs:
            if r["key"] in seen:
                continue
            seen.add(r["key"])
            if src.get("exclude") and re.search(src["exclude"], r["title"] or "", re.I):
                continue
            if PROFILE != "phd" and not src.get("academic") and \
                    INDUSTRIAL_PHD.search(r["title"] or "") and not PHD_EXCLUDE.search(r["title"]):
                if loc_status(r.get("loc")) != "no":
                    phd.append(r)   # a doctoral position: it goes to the PhD profile (out/phd_industrial.json)
                continue
            if not keep_title(src, r):
                continue
            ls = loc_status(r.get("loc"))
            if ls == "no" and not src.get("ignore_location"):
                continue
            r["loc_status"] = "eu" if src.get("ignore_location") else ls
            r["ml"] = PROFILE == "phd" or title_ok(r["title"])
            matched.append(r)
        matched.sort(key=lambda r: not r["ml"])   # ML titles get the detail requests first
        n_detail, kept = 0, []
        for r in matched:
            need = detailer and (r["key"] not in known_keys or r["key"] not in has_detail) and \
                (not r.get("desc_full") or r["loc_status"] == "unknown" or r.get("_slug_title"))
            if need and n_detail < src.get("max_detail", MAX_DETAIL) and time.time() < src.get("_stop_at", 1e12):
                n_detail += 1
                try:
                    detailer(src, r)
                except Gone:
                    continue
                except Exception as e:
                    r["detail_error"] = str(e)[:160]
                if r.get("_slug_title"):
                    if not keep_title(src, r):
                        continue
                    r["ml"] = PROFILE == "phd" or title_ok(r["title"])
                ls = loc_status(r.get("loc"))
                if ls == "no" and not src.get("ignore_location"):
                    continue
                r["loc_status"] = "eu" if src.get("ignore_location") else ls
            kept.append(r)
        for i, r in enumerate(phd):
            if i < src.get("max_phd", 25) and detailer and not r.get("desc_full") and r["key"] not in PHD_DONE \
                    and time.time() < src.get("_stop_at", 1e12):
                try:
                    detailer(src, r)
                except Gone:
                    continue
                except Exception:
                    pass
            if loc_status(r.get("loc")) == "no":
                continue
            INDUSTRIAL.append(phd_item(r, src["id"], board=src["type"] in BOARD_TYPES))
            MOVED.add(r["key"])
        st.update(ok=True, matched=len(kept), complete=complete, details=n_detail,
                  secs=round(time.time() - t0, 1))
        if phd:
            st["phd"] = len(phd)
        st.update({k: v for k, v in NOTES.get(src["id"], {}).items() if not k.startswith("_")})
        return src, kept, complete, st
    except Exception as e:
        st.update({k: v for k, v in NOTES.get(src["id"], {}).items() if not k.startswith("_")})
        st["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        st["trace"] = traceback.format_exc(limit=3)[-600:]
        st["secs"] = round(time.time() - t0, 1)
        return src, None, False, st


def candidate(e, descs):
    """One card of candidates.json: everything open past the wide filter, with an indicative score
    (sorting only), the keywords behind it, the years of experience asked and ~400 characters on the
    job; the description (up to DESC_MAX characters) only for postings first seen in DESC_DAYS."""
    keys = ["key", "src", "co", "title", "loc", "loc_status", "posted", "first_seen", "url", "apply_url", "ats",
            "target", "big", "baseline", "score", "years", "kw", "card", "level", "dept", "deadline", "salary",
            "country"]
    c = {k: e.get(k) for k in keys if e.get(k) not in (None, "", [])}
    c["ml"] = e.get("ml", True)
    if e.get("ats") == "watch":
        desc = e.get("desc")
    elif not e.get("baseline") and days_since(e.get("first_seen", "")) <= DESC_DAYS:
        desc = descs.get(e["key"])
    else:
        desc = None
    if desc:
        c["desc"] = desc
    return c


def new_card(c, desc):
    """A card of new_cards.json, the file the Radar routine judges every morning: a card of
    candidates.json without the description, plus the requirements part of it and its signals."""
    n = {k: v for k, v in c.items() if k not in ("desc", "src", "ats")}
    n["via"] = c.get("ats")
    if n.get("level"):
        n["level"] = n["level"][:60]
    if desc and c.get("ats") != "watch":
        m = REQ_HINT.search(desc, min(len(desc) // 3, 300))
        if m:
            n["req"] = desc[m.start():m.start() + NEW_REQ].rsplit(" ", 1)[0]
        sig = signals(desc)
        n["sig"] = [k for k in ("phd", "german", "french", "visa") if sig[k]]
    elif desc:
        n["card"] = desc[:NEW_REQ]   # a watched page: what changed on it
    return n


def public(e, with_desc):
    keys = ["key", "co", "target", "big", "title", "url", "loc", "loc_status", "posted",
            "first_seen", "last_seen", "baseline", "ats", "country", "dept", "deadline", "salary",
            "level", "apply_url", "kind", "eligible", "eligibility_note", "funding", "via"]
    if with_desc:
        keys += ["desc", "signals"]
    return {k: e.get(k) for k in keys if e.get(k) not in (None, "")}


MONTHS = ("january february march april may june july august september october november december "
          "jan feb mar apr may jun jul aug sep oct nov dec").split()
DATE_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\.? (" + "|".join(MONTHS) + r")\.?,? (\d{4})\b|\b(" + "|".join(MONTHS) +
                     r")\.? (\d{1,2})(?:st|nd|rd|th)?,? (\d{4})\b|\b(\d{4})-(\d{2})-(\d{2})\b|\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b",
                     re.I)
DEADLINE_WORDS = re.compile(r"deadline|apply|application|closing|submission|call|scadenza|date limite|bewerbung|frist", re.I)


def dates_near_deadlines(text):
    """Dates (ISO) written near deadline words, with a bit of context; past ones older than a month dropped."""
    out = {}
    for m in DATE_RE.finditer(text):
        g = m.groups()
        try:
            if g[0]:
                d = dt.date(int(g[2]), MONTHS.index(g[1].lower()) % 12 + 1, int(g[0]))
            elif g[3]:
                d = dt.date(int(g[5]), MONTHS.index(g[3].lower()) % 12 + 1, int(g[4]))
            elif g[6]:
                d = dt.date(int(g[6]), int(g[7]), int(g[8]))
            else:
                d = dt.date(int(g[11]), int(g[10]), int(g[9]))
        except ValueError:
            continue
        ctx = re.sub(r"\s+", " ", text[max(0, m.start() - 120):m.end() + 40]).strip()
        if (d - TODAY).days >= -30 and DEADLINE_WORDS.search(ctx) and d.isoformat() not in out:
            out[d.isoformat()] = ctx[-200:]
    return [{"date": k, "context": v} for k, v in sorted(out.items())][:8]


def check_fellowships(meta):
    """Once a week: is every page in phd_fellowships.json reachable, and which dates does it show?"""
    path = os.path.join(OUT, "phd_fellowships.json")
    if days_since(meta.get("fellowships_checked", "")) < 7 and os.path.exists(path):
        return
    items = []
    for f in load(os.path.join(ROOT, "phd_fellowships.json"), {}).get("items", []):
        it = dict(f, kind="fellowship")
        if f.get("msca"):
            it["kind"] = "msca-cofund"
            msca_eligibility(it)
            it["kind"] = "fellowship"
        try:
            text = page_text(f["url"])
            it.update(reachable=True, dates_found=dates_near_deadlines(text))
        except Exception as e:
            it.update(reachable=False, error=str(e)[:160])
        items.append(it)
    meta["fellowships_checked"] = NOW_ISO
    save(path, {"generated": NOW_ISO, "count": len(items), "items": items})


def migrate(sources, jobs, meta, prune):
    """Postings of sources merged into another ("absorbs") move to it and keep their first_seen;
    postings of sources no longer in the file are dropped."""
    for s in sources:
        for old in s.get("absorbs") or []:
            for k in [k for k, v in jobs.items() if v.get("src") == old]:
                v = jobs.pop(k)
                nk = f"{s['id']}:{v['id']}"
                if nk in jobs and jobs[nk].get("first_seen", "") <= v.get("first_seen", ""):
                    continue
                v.update(src=s["id"], key=nk)
                jobs[nk] = v
            if old in meta["sources_seen"]:
                meta["sources_seen"].remove(old)
                if s["id"] not in meta["sources_seen"]:
                    meta["sources_seen"].append(s["id"])
            meta["raw_counts"].pop(old, None)
    if prune:
        ids = {s["id"] for s in sources}
        for k in [k for k, v in jobs.items() if v.get("src") not in ids]:
            del jobs[k]


def check_boards(jobs, status):
    """Job-board postings against the company sources: give them the company's target, mark the
    ones that repeat a company-site posting (or another board posting) as duplicates, and list
    the ones a company source should have found but did not (coverage gaps)."""
    comp = Companies(load(os.path.join(ROOT, PREFIX + "sources.json"), []), jobs)
    boards = sorted((v for v in jobs.values() if v.get("ats") in BOARDS),
                    key=lambda v: (v["ats"] != "linkedin", not v.get("det"), v.get("first_seen", "")))
    seen, gaps, matched = {}, {}, 0
    for v in boards:
        v.pop("dup_of", None)
        srcs = comp.find(v["co"])
        if srcs:
            matched += 1
            v["target"] = next((s["target"] for s in srcs if s.get("target")), None)
            v["big"] = any(s.get("big") for s in srcs)
            dup = comp.direct_match(v)
            if dup:
                v["dup_of"] = dup
                continue
        k = (co_key(v["co"]), title_key(v["title"]), city(v.get("loc")))
        if k in seen:
            v["dup_of"] = seen[k]
            continue
        seen[k] = v["key"]
        listing = [s for s in srcs if s["type"] != "watch" and status.get(s["id"], {}).get("ok")]
        # only ML titles count: the company sources search with ML keywords on purpose
        if listing and v.get("ml", True) and v.get("last_seen") == NOW_ISO and \
                not comp.direct_match(v, any_place=True):
            gaps.setdefault((listing[0]["id"], title_key(v["title"])), {
                "src": listing[0]["id"], "co": v["co"], "title": v["title"], "loc": v.get("loc"),
                "url": v["url"], "via": v["ats"]})
    gaps = list(gaps.values())
    others = {}
    for v in boards:
        if v.get("ml", True) and not v.get("dup_of") and not comp.find(v["co"]):
            others.setdefault(v["co"], []).append(v["title"])
    new_cos = sorted(others.items(), key=lambda x: -len(x[1]))[:40]
    by_src = {}
    for g in gaps:
        by_src[g["src"]] = by_src.get(g["src"], 0) + 1
    for sid, n in by_src.items():
        if sid in status:
            status[sid]["coverage_gaps"] = n
    return {"board_postings": len(boards), "known_companies": matched,
            "duplicates": sum(1 for v in boards if v.get("dup_of")), "gaps": gaps[:150],
            "new_companies": [{"co": co, "postings": len(t), "titles": t[:3]} for co, t in new_cos]}


def main():
    os.makedirs(OUT, exist_ok=True)
    global COMPANIES
    all_sources = load(os.path.join(ROOT, PREFIX + "sources.json"), [])
    only = set(sys.argv[1:])
    sources = [s for s in all_sources if s["id"] in only] if only else all_sources
    state = load(os.path.join(OUT, PREFIX + "state.json"), {"jobs": {}, "meta": {}})
    jobs, meta = state["jobs"], state["meta"]
    meta.setdefault("raw_counts", {})
    meta.setdefault("sources_seen", [])
    meta.setdefault("watch", {})
    meta.setdefault("searches", {})
    migrate(all_sources, jobs, meta, prune=not only)
    COMPANIES = Companies(all_sources, jobs)
    meta.setdefault("hwm", {})
    for s in sources:
        if s["type"] == "jobspy":
            s["_last"] = meta["searches"].get(s["id"], {})
        if s["type"] == "euraxess":
            s["_hwm"] = meta["hwm"].get(s["id"])
    if PROFILE != "phd":   # industrial PhDs already described in the PhD state need no new detail request
        PHD_DONE.update(k.split(":", 1)[1] for k, v in load(os.path.join(OUT, "phd_state.json"), {"jobs": {}})["jobs"].items()
                        if k.startswith(("industrial:", "boards-phd:")) and v.get("det"))
    closed = [c for c in load(os.path.join(OUT, PREFIX + "closed.json"), {"items": []}).get("items", [])
              if days_since(c.get("closed_at", "")) <= CLOSED_DAYS]
    # every closure, also of postings outside recent/open.json, for the readers of candidates.json;
    # the long descriptions live only there (not in the state) and are carried over between runs
    prev = load(os.path.join(OUT, "candidates.json"), {}) if PROFILE != "phd" else {}
    closed_all = [c for c in prev.get("closed", []) if days_since(c.get("closed_at", "")) <= CLOSED_DAYS]
    descs = {c["key"]: c["desc"] for c in prev.get("items", []) if c.get("desc") and c.get("ats") != "watch"}
    # the run that switched to the wide filter: what it reveals is backlog, not news
    widen = PROFILE != "phd" and "wide_since" not in meta
    if widen:
        meta["wide_since"] = NOW_ISO

    known = set(jobs)
    has_detail = {k for k, v in jobs.items() if v.get("det")}
    t0 = time.time()
    statuses, new_total = [], 0
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        futs = [ex.submit(run_source, s, known, has_detail) for s in sources if not s.get("disabled")]
        for fut in cf.as_completed(futs):
            src, recs, complete, st = fut.result()
            sid = src["id"]
            statuses.append(st)
            if recs is None:
                continue
            baseline = sid not in meta["sources_seen"]
            board = src["type"] in ("jobspy", "board_phd")
            if NOTES.get(sid, {}).get("_hwm") is not None:
                meta["hwm"][sid] = NOTES[sid]["_hwm"]
            if board:   # the boards only return recent postings: nothing to hold back as "baseline"
                if src["type"] == "jobspy":
                    meta["searches"].setdefault(sid, {}).update(NOTES.get(sid, {}).get("_done", {}))
                baseline = False
            if src["type"] == "watch":
                text = recs[0]["_watch_text"]
                lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 3]
                h = hashlib.sha1("\n".join(lines).encode()).hexdigest()
                prev = meta["watch"].get(sid)
                if prev and prev.get("hash") != h:
                    old = set(prev.get("lines", []))
                    added = [l for l in lines if l not in old and not l.lstrip().startswith(("{", "[", "<"))]
                    jobby = re.compile(r"position|job|engineer|research|scientist|developer|call for|bando|posizion|"
                                       r"borsa|ph\.?d|doctoral|intern|stage|apply|candidat|hiring|vacanc|open role|"
                                       r"assegno|ricercat|fellow", re.I)
                    added = [l for l in added if jobby.search(l)][:40]
                    if added:
                        k = f"{sid}:{TODAY.isoformat()}"
                        jobs[k] = {"key": k, "src": sid, "co": src["co"], "target": src.get("target"),
                                   "big": bool(src.get("big")), "ats": "watch", "kind": src.get("kind"),
                                   "country": src.get("country"),
                                   "title": f"Pagina aggiornata: {src['co']}", "url": src["url"],
                                   "loc": src.get("loc_default", ""), "loc_status": "eu",
                                   "first_seen": NOW_ISO, "last_seen": NOW_ISO, "det": True,
                                   "desc": "Righe nuove sulla pagina:\n" + "\n".join(added)[:2500]}
                        st["new"] = 1
                        new_total += 1
                meta["watch"][sid] = {"hash": h, "lines": lines[:400]}
                if baseline:
                    meta["sources_seen"].append(sid)
                continue

            prev_raw = meta["raw_counts"].get(sid)
            if prev_raw and not board and st.get("raw", 0) < 0.5 * prev_raw:
                st["shrank_from"] = prev_raw
                if complete:
                    complete = False
                    st["note"] = f"listing shrank from {prev_raw} to {st.get('raw')}: closures skipped"
            meta["raw_counts"][sid] = st.get("raw", 0)

            now_keys, n_new = set(), 0
            for r in recs:
                k = r["key"]
                now_keys.add(k)
                desc_full = r.pop("desc_full", None)
                e = jobs.get(k)
                if e is None:
                    e = {x: r.get(x) for x in ("key", "src", "co", "target", "big", "ats", "id", "title",
                                               "url", "loc", "loc_status", "posted")}
                    e.update({x: r[x] for x in ("country", "dept", "deadline", "salary", "level", "apply_url",
                                                "dup_of", "kind", "funding", "via") if r.get(x)})
                    e["first_seen"] = NOW_ISO
                    if baseline or (widen and not board and not r.get("ml", True)):
                        e["baseline"] = True
                    n_new += 1
                    jobs[k] = e
                else:
                    for x in ("url", "co", "target", "big"):
                        if r.get(x):
                            e[x] = r[x]
                    if r.get("title") and not r.get("_slug_title"):
                        e["title"] = r["title"]
                    # keep a location learned from a detail page unless the list gives a clear one
                    if r.get("loc") and (r.get("loc_status") == "eu" or e.get("loc_status") != "eu"):
                        e["loc"], e["loc_status"] = r["loc"], r.get("loc_status")
                    if r.get("posted") and not e.get("posted"):
                        e["posted"] = r["posted"]
                    e.update({x: r[x] for x in ("deadline", "salary", "kind", "funding") if r.get(x)})
                e["last_seen"] = NOW_ISO
                e["ml"] = r.get("ml", True)
                if desc_full:
                    e["det"] = True
                    if days_since(e["first_seen"]) <= RECENT_DAYS and not e.get("desc") and e["ml"]:
                        e["desc"] = summarize(desc_full)
                        e["signals"] = signals(desc_full)
                    if not e.get("baseline") and days_since(e["first_seen"]) <= DESC_DAYS:
                        descs[k] = (desc_full[:DESC_MAX].rsplit(" ", 1)[0] + " …"
                                    if len(desc_full) > DESC_MAX else desc_full)
                    if not e.get("card"):
                        assess(e, desc_full)
                elif "score" not in e:
                    assess(e, e.get("desc") or "")
            st["new"] = n_new
            new_total += n_new
            n_closed = 0
            for k in [k for k, v in jobs.items() if v.get("src") == sid and k not in now_keys]:
                v = jobs[k]
                if k in MOVED:   # handed to the PhD profile: not a closure
                    del jobs[k]
                    continue
                if PROFILE != "phd" and not wide_ok(v.get("title"), v.get("dept")):   # out of a narrower filter
                    del jobs[k]
                    continue
                if board:   # out of the search window, not necessarily closed: just forget it
                    if days_since(v.get("last_seen", "")) > src.get("keep_days", 30):
                        del jobs[k]
                    continue
                if v.get("ats") == "watch":
                    if days_since(v.get("first_seen", "")) > RECENT_DAYS:
                        del jobs[k]
                    continue
                if src.get("close_by_deadline"):   # read only in part each run: open until the deadline
                    dl = v.get("deadline")
                    if not ((dl and dl < TODAY.isoformat()) or
                            (not dl and days_since(v.get("first_seen", "")) > src.get("keep_days", 60))):
                        continue
                if complete or days_since(v.get("last_seen", "")) >= STALE_DAYS:
                    c = public(v, False)
                    c["closed_at"] = NOW_ISO
                    c["reason"] = "not listed anymore" if complete else f"not seen for {STALE_DAYS}+ days"
                    closed_all.append(c)
                    if v.get("ml", True) and (BOARDS_IN_RECENT or v.get("ats") not in BOARDS):
                        closed.append(c)
                    del jobs[k]
                    n_closed += 1
            st["closed"] = n_closed
            if baseline:
                meta["sources_seen"].append(sid)

    if PROFILE != "phd" and not only:
        # doctoral positions recorded from the job boards before they went to the PhD profile
        for k in [k for k, v in jobs.items() if v.get("ats") in BOARDS and INDUSTRIAL_PHD.search(v.get("title") or "")
                  and not PHD_EXCLUDE.search(v["title"]) and loc_status(v.get("loc")) != "no"]:
            INDUSTRIAL.append(phd_item(jobs.pop(k), k.split(":")[0], board=True))
    coverage = check_boards(jobs, {s["id"]: s for s in statuses})
    if PROFILE == "phd" and not only:
        check_fellowships(meta)
    if PROFILE == "phd":   # every position gets a kind; MSCA ones the mobility check
        kinds = {s["id"]: s.get("kind") for s in all_sources}
        for v in jobs.values():
            v["kind"] = v.get("kind") or kinds.get(v.get("src")) or "academic"
            msca_eligibility(v)
    elif not only:
        held = meta.setdefault("board_phd", {})   # job-board doctoral positions, handed over for BOARD_PHD_DAYS
        for it in INDUSTRIAL:
            if it.get("board"):
                held[it["key"]] = {**held.get(it["key"], {}), **{x: y for x, y in it.items() if y}}
        for k in [k for k, v in held.items() if days_since(v.get("seen", "")) > BOARD_PHD_DAYS]:
            del held[k]
        items = [it for it in INDUSTRIAL if not it.get("board")] + list(held.values())
        save(os.path.join(OUT, "phd_industrial.json"), {"generated": NOW_ISO, "count": len(items), "items": items})
    # a posting dropped by one source but still listed by another (Google and DeepMind) is not closed
    open_urls = {v.get("url") for v in jobs.values()}
    closed = [c for c in closed if c.get("closed_at") != NOW_ISO or c.get("url") not in open_urls]
    closed_all = [c for c in closed_all if c.get("closed_at") != NOW_ISO or c.get("url") not in open_urls]

    # forget descriptions of older postings to keep the state small
    for v in jobs.values():
        if days_since(v.get("first_seen", "")) > RECENT_DAYS:
            v.pop("desc", None)
            v.pop("signals", None)
            v.pop("baseline", None)

    def dedupe(items):
        seen, out = set(), []
        for it in items:
            u = it.get("url") or it.get("key")
            if u in seen:
                continue
            seen.add(u)
            out.append(it)
        return out

    def listed(v):   # what the current Radar routine reads: ML titles, no job boards yet
        return not v.get("dup_of") and v.get("ml", True) and (BOARDS_IN_RECENT or v.get("ats") not in BOARDS)

    recent = [public(v, True) for v in jobs.values()
              if days_since(v.get("first_seen", "")) <= RECENT_DAYS and listed(v)]
    recent.sort(key=lambda v: (v.get("first_seen", ""), v.get("posted") or ""), reverse=True)
    recent = dedupe(recent)
    open_ = dedupe(sorted((public(v, False) for v in jobs.values() if v.get("ats") != "watch" and listed(v)),
                          key=lambda v: (v.get("co", ""), v.get("title", ""))))
    statuses.sort(key=lambda s: s["id"])
    failed = [s["id"] for s in statuses if not s.get("ok")]
    warnings = []
    for s in statuses:
        if not s.get("ok") or s["type"] == "watch":
            continue
        if s.get("raw") == 0:
            warnings.append(f"{s['id']}: 0 postings")
        if s.get("shrank_from"):
            warnings.append(f"{s['id']}: {s['shrank_from']} -> {s['raw']} postings since the last run")
        if s.get("coverage_gaps"):
            warnings.append(f"{s['id']}: {s['coverage_gaps']} postings on LinkedIn/Indeed missing from its listing")
        if s["type"] == "jobspy" and s.get("note"):
            warnings.append(f"{s['id']}: {s['note']}")

    save(os.path.join(OUT, PREFIX + "state.json"), {"jobs": jobs, "meta": meta})
    save(os.path.join(OUT, PREFIX + "recent.json"), {"generated": NOW_ISO, "days": RECENT_DAYS,
                                           "count": len(recent), "items": recent})
    save(os.path.join(OUT, PREFIX + "open.json"), {"generated": NOW_ISO, "count": len(open_), "items": open_})
    save(os.path.join(OUT, PREFIX + "closed.json"), {"generated": NOW_ISO, "days": CLOSED_DAYS,
                                           "count": len(closed), "items": closed})
    n_cand = None
    if PROFILE != "phd":
        cands = dedupe([candidate(v, descs) for v in jobs.values() if not v.get("dup_of")])
        cands.sort(key=lambda c: c.get("first_seen") or "", reverse=True)
        cands.sort(key=lambda c: c.get("score") or 0, reverse=True)
        n_cand = len(cands)
        save(os.path.join(OUT, "candidates.json"), {
            "generated": NOW_ISO, "count": n_cand, "desc_days": DESC_DAYS, "closed_days": CLOSED_DAYS,
            "items": cands, "closed": closed_all})
        # descriptions last DESC_DAYS: older cards keep the requirements they had in the previous file
        kept = {c["key"]: c for c in load(os.path.join(OUT, "new_cards.json"), {}).get("items", [])}
        new = [{**{k: kept.get(c["key"], {}).get(k) for k in ("req", "sig") if kept.get(c["key"], {}).get(k)},
                **new_card(c, descs.get(c["key"]) or c.get("desc"))} for c in cands
               if not c.get("baseline") and days_since(c.get("first_seen", "")) <= NEW_DAYS]
        new.sort(key=lambda c: (c["first_seen"], c.get("score") or 0), reverse=True)
        save(os.path.join(OUT, "new_cards.json"), {
            "generated": NOW_ISO, "days": NEW_DAYS, "count": len(new), "items": new,
            "closed": [{k: c.get(k) for k in ("key", "co", "title", "url", "closed_at")} for c in closed_all
                       if days_since(c.get("closed_at", "")) <= NEW_DAYS]})
    save(os.path.join(OUT, PREFIX + "status.json"), {
        "run_at": NOW_ISO, "duration_s": round(time.time() - t0),
        "sources": len(statuses), "failed": failed, "new": new_total,
        "open": len(open_), "recent": len(recent), "candidates": n_cand, "warnings": warnings,
        "coverage": coverage, "details": statuses})
    print(f"done in {round(time.time() - t0)}s · sources {len(statuses)} · failed {len(failed)} "
          f"· new {new_total} · open {len(open_)}")
    for w in warnings:
        print("WARN", w)
    for s in statuses:
        flag = "OK " if s.get("ok") else "ERR"
        print(f"{flag} {s['id']:<24} raw={s.get('raw', '-'):<5} matched={s.get('matched', '-'):<4} "
              f"new={s.get('new', '-'):<4} closed={s.get('closed', '-'):<3} {s.get('error', s.get('note', ''))}")


if __name__ == "__main__":
    main()
