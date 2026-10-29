#!/usr/bin/env python3
"""
The mechanical part of the claude.ai routines (Radar and Mappa PhD), so that Claude only judges.
Standard library only. The routines copy this file from their checkout of the repo (attached to the
routine: their permission check blocks code downloaded at run time) and follow tools/routines.md.

Radar, every morning:
    python3 radar.py fetch                 # status.json and new_cards.json from the repo
    python3 radar.py cards --db db         # numbered cards to judge: not judged by an earlier run, not in the Radar,
                                           # ML titles first, as many as one morning reads (CARD_PAGES)
    (Claude writes giudizi.txt: one line per card, n|tipo|fit|prestigio|anni|motivo)
    python3 radar.py more                  # requirements of the cards with fit >= 3, to write why and req
    python3 radar.py desc 12 40            # full description of some cards, only when the card is unclear
    (Claude writes schede.jsonl for the cards with fit >= 3: {"n", "why", "req", optional overrides})
    python3 radar.py docs --db db          # documents to write, batch entries, closures, final message
    python3 radar.py check --db db         # every day, a third of the open postings in turn: LinkedIn links and
                                           # links the scanner does not follow, checked for being still online
    python3 radar.py verify --db db        # full text of the jobs that could go into Apply today, not verified yet
    (Claude writes verifiche.jsonl: {"id", "verdict": ok|no|dubbio, "checks"})
    python3 radar.py verify --db db --write  # the batch that stores the verdicts (Apply today shows only "ok")

Mappa PhD, Monday and Thursday:
    python3 radar.py phd --db db           # PhD positions not judged yet and not on the map, positions to close
    python3 radar.py phd-done              # the document that records them as judged (collection phdRuns)

Long lists (cards, more, phd) go to page files of about 24,000 characters (cards-1.txt, ...),
which fit in one tool output: the script names them and the routine reads them all.

--db is the folder where the routine saved the page's collections with ArtifactData (out_dir):
db/jobs/*.json and db/radarRuns/*.json for the Radar, db/positions/*.json and db/phdRuns/*.json
for the PhD map.
"""
import datetime as dt
import glob
import hashlib
import json
import os
import re
import sys
import unicodedata
import urllib.request

RAW = "https://raw.githubusercontent.com/Merlo02/job-radar/main/out/"
MSG_LINES = 30       # postings listed one by one in the final message (the rest are counted)
PAGE_CHARS = 24000   # longer lists go to page files: one tool output shows about 30,000 characters
CARD_PAGES = 20      # pages of cards in one morning (~450,000 characters, what one run can read): the rest waits
MAX_YEARS = 3        # a new graduate: postings asking this many years of experience or more stay out (Gabriele, 18/10)
BOARD_DAYS = 30      # Indeed and the other boards (not LinkedIn, which `check` asks): older postings count as expired
CHECK_TURNS = 3      # `check` asks a third of the open postings each day, so each one is checked every 3 days
NOW = dt.datetime.now(dt.timezone.utc)
TODAY = dt.datetime.now(dt.timezone(dt.timedelta(hours=2))).date().isoformat()   # Rome (summer time is close enough)
BOARD_NAME = {"linkedin": "LinkedIn", "indeed": "Indeed", "wttj": "Welcome to the Jungle", "thehub": "The Hub",
              "hn": "Hacker News", "landingjobs": "Landing.jobs", "joinup": "joinup.ch"}
TIPI = ("ricerca", "training", "edge", "data science", "ai-swe", "altro")

ZURICH = re.compile(r"z[uü]e?rich|\bzug\b|\bbaar\b|\bcham\b|rotkreuz|\bbaden\b|r[uü]schlikon|winterthur|d[uü]bendorf|"
                    r"schlieren|wallisellen|kloten|opfikon|glattbrugg|thalwil|horgen|\buster\b|dietikon|regensdorf|"
                    r"adliswil|kilchberg|k[uü]snacht|zollikon|w[aä]denswil|\bZH\b", re.I)
SWISS = re.compile(r"switzerland|schweiz|suisse|svizzera|gen[eè]v|lausanne|basel|\bbern\b|lugano|luzern|lucerne|"
                   r"neuch[aâ]tel|fribourg|st\.? ?gallen|\bsion\b|villigen|\bchur\b|\bthun\b|manno|"
                   r"(?-i:(?<![A-Za-z])CH(?![A-Za-z]))", re.I)
ITALY = re.compile(r"\bital(?:y|ia)\b|milan|turin|torino|\brom[ae]\b|bologna|\bpisa\b|genoa|genova|padova|padua|"
                   r"naples|napoli|trento|trieste|florence|firenze|\bbari\b|catania|agrate|ispra|pozzuoli|modena|"
                   r"(?-i:,\s*IT\b)", re.I)
# German places whose names also exist around Zurich (Baden, Cham, Baar…): never the Zurich area
NOT_SWISS = re.compile(r"germany|deutschland|w[uü]e?rttemberg|bavaria|bayern|baden-baden|(?-i:\bDE\b)", re.I)
FAR = re.compile(r"(?-i:\bUSA?\b)|united states|\(US\)|canada|singapore|india|china|japan|korea|taiwan|israel|"
                 r"australia|brazil|mexico|toronto|montr[eé]al|san francisco|new york|seattle|mountain view|"
                 r"palo alto|sunnyvale|boston|austin|los angeles|san diego|tokyo", re.I)
# staffing agencies and recruiters posting for unnamed clients (the rubric gives them fit 1)
AGENCY = re.compile(r"recruit|staffing|headhunt|personnel|personalvermittlung|manpower|randstad|adecco|\bhays\b|"
                    r"michael page|robert half|robert walters|harnham|\bgi group|jefferson frank|xcede|oliver bernard|"
                    r"talent (?:partners|solutions|acquisition)|\bpeople$|jobgether|deeprec|executive search|"
                    r"search partners|\bresourcing\b|hunters?$", re.I)
# any word of machine learning, data or AI, in the languages of the postings: a card without one is
# shown on one line (Claude judges it from the title, as it did anyway)
ML_TEXT = re.compile(r"machine.?learning|deep.?learning|\bml\b|\bai\b|\bk\.?i\.?\b|\bia\b|artificial|artificiel|"
                     r"künstlich|kunstig|intelligenza|inteligencia|neural|neuronal|data.?scien|data.?analy|analytics|"
                     r"datenanaly|computer.?vision|machine.?vision|bildverarbeitung|\bllms?\b|\bnlp\b|natural.?language|"
                     r"reinforcement|pytorch|tensorflow|\bjax\b|transformer|foundation.?model|genai|generative|"
                     r"statisti|predictive|forecast|signal|perception|algorithm|apprentissage|aprendizaje|"
                     r"apprendimento|modell|model|robot|autonom|sensor|optimi[sz]|research|recherche|forschung|"
                     r"ricerca|investigaci|scientist|wissenschaft|quantitative|\bquant\b|embedded|\bedge\b|"
                     r"\bgpu|\bcuda\b|inference|training data|annotation", re.I)
# data-labelling and AI-tutor gigs: fit 1 by the rubric, so the script judges them
GIG = re.compile(r"alignerr|\boutlier\b|mercor|dataannotation|remotasks|toloka|telus digital|\bappen\b|micro1|"
                 r"invisible technologies|human frontier collective", re.I)
# same as scan.py: a role whose text says it is senior, though the title does not ("This is a senior hands-on role",
# "we are looking for a Senior ML Engineer", "The AI Engineer Level 6", "mentor junior engineers")
SENIOR_TEXT = re.compile(
    r"\b(?:is|as|for|seeking|hiring|be|looking for)\s+(?:a|an)\s+(?:highly\s+|very\s+)?(?:experienced\s+|seasoned\s+)?"
    r"senior\b(?![- ](?:manager|management|leader|leadership|stakeholder|executive|colleague|team member|member of|"
    r"engineers? (?:who|to)|scientists? (?:who|to)|partner))"
    r"|\bthis senior (?:role|position)\b|\bsenior[- ]level (?:role|position)\b"
    r"|(?<!eqf )(?<!nqf )(?:engineer|scientist|developer|analyst|specialist|role|position|grade|band|job level)\s+level\s+[6-9]\b"
    r"|\b(?:mentor|coach)(?:ing|s|es)?\s+(?:other|junior|less[- ]experienced|more junior)\s+"
    r"(?:engineers|scientists|developers|team members|colleagues|data scientists|researchers)", re.I)
# same as scan.py: an internship whose text asks for current enrolment, unless it also welcomes graduates
INTERN = re.compile(r"\bintern\b|\binterns\b|internship|praktik\w*|\bstage\b|stagiaire|tirocin\w*|stagista|"
                    r"summer student", re.I)
ENROLLED_TEXT = re.compile(
    r"(?:currently|must be|be|are|still|actively)\s+(?:enrolled|registered|matriculated)\b"
    r"|enrolled (?:full[- ]time )?(?:in|at) (?:a|an|your)\b|(?:currently|actively) (?:pursuing|studying)\b"
    r"|working towards? (?:a|an|your) (?:\w+ ){0,2}(?:degree|bachelor|master|ph\.?d)"
    r"|returning to (?:school|university|your (?:studies|degree|university|program))|return to (?:school|university|your studies)"
    r"|(?:remaining|left) (?:in|of) your (?:studies|degree|program)|student status|proof of enrou?l?ment|enrolment certificate"
    r"|immatrikuliert|eingeschrieben|immatrikulation|pflichtpraktikum|convention de stage|stage de fin d'[ée]tudes"
    r"|[ée]tudiant(?:e)?s? en (?:derni[èe]re ann[ée]e|master|[ée]cole)|iscritt[oaie] (?:a|al|ad|presso)\b|studente universitario",
    re.I)
GRADS_TEXT = re.compile(
    r"recent(?:ly)? graduat|graduated (?:with)?in the (?:last|past)|within (?:\w+ )?(?:\d+ )?(?:months?|years?) of graduat"
    r"|(?:students|student) or (?:recent )?graduates|graduates are (?:also )?(?:welcome|eligible)|after (?:your )?graduation"
    r"|neolaureat|post[- ]?lauream|tirocinio extracurricular|jeunes? dipl[ôo]m|absolvent|hochschulabsolvent", re.I)
EN_WORDS = set("the and to of with you we our in for is are will on as be your this an at or by from have team".split())
IT_WORDS = set("il di che la per con una del nel della delle sono lavoro esperienza azienda".split())
CO_GENERIC = re.compile(r"\b(?:gmbh|ag|se|sa|sas|spa|srl|bv|nv|inc|llc|ltd|limited|plc|corp|corporation|co|kg|oy|ab|"
                        r"as|group|holding|holdings|company|technologies|international|the|ai|labs?|io)\b")
CITY_IT = {"zurich": "Zurigo", "zürich": "Zurigo", "geneva": "Ginevra", "genève": "Ginevra", "lausanne": "Losanna",
           "basel": "Basilea", "bern": "Berna", "lucerne": "Lucerna", "luzern": "Lucerna", "munich": "Monaco di Baviera",
           "münchen": "Monaco di Baviera", "paris": "Parigi", "london": "Londra", "cologne": "Colonia", "köln": "Colonia",
           "vienna": "Vienna", "wien": "Vienna", "brussels": "Bruxelles", "bruxelles": "Bruxelles", "brussel": "Bruxelles",
           "copenhagen": "Copenaghen", "københavn": "Copenaghen", "stockholm": "Stoccolma", "barcelona": "Barcellona",
           "lisbon": "Lisbona", "lisboa": "Lisbona", "prague": "Praga", "praha": "Praga", "warsaw": "Varsavia",
           "warszawa": "Varsavia", "berlin": "Berlino", "hamburg": "Amburgo", "frankfurt": "Francoforte",
           "frankfurt am main": "Francoforte", "dublin": "Dublino", "edinburgh": "Edimburgo", "athens": "Atene",
           "milan": "Milano", "turin": "Torino", "rome": "Roma", "naples": "Napoli", "florence": "Firenze",
           "genoa": "Genova", "venice": "Venezia", "padua": "Padova", "leuven": "Lovanio", "the hague": "L'Aia",
           "den haag": "L'Aia", "nuremberg": "Norimberga", "nürnberg": "Norimberga", "dresden": "Dresda",
           "aachen": "Aquisgrana", "freiburg": "Friburgo", "gothenburg": "Göteborg", "seville": "Siviglia",
           "bucharest": "Bucarest", "helsinki": "Helsinki", "oslo": "Oslo", "madrid": "Madrid", "amsterdam": "Amsterdam",
           "antwerp": "Anversa", "ghent": "Gand", "marseille": "Marsiglia", "lyon": "Lione", "toulouse": "Tolosa",
           "greater paris metropolitan region": "Parigi", "greater london": "Londra", "greater munich metropolitan area":
           "Monaco di Baviera", "greater zurich area": "Zurigo", "greater milan metropolitan area": "Milano"}
ACADEMIC = re.compile(r"universit|hochschule|institut|\beth\b|epfl|cnrs|inria|max planck|fraunhofer|helmholtz|"
                      r"\bcern\b|\bembl\b|\bucl\b|college|school of", re.I)


# ------------------------------------------------------------------ helpers
def get(name, dest=None):
    dest = dest or name
    req = urllib.request.Request(RAW + name, headers={"User-Agent": "job-radar-routine"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
    except OSError as e:
        sys.exit(f"STOP: non riesco a scaricare out/{name} dal repo ({e}). Chiudi il giro con una riga "
                 f"nel messaggio finale: \"Scanner: out/{name} non disponibile, giro saltato.\"")
    with open(dest, "wb") as f:
        f.write(data)
    return dest


def emit(blocks, name):
    """Print the numbered blocks, or write them to name-1.txt, name-2.txt… when they would not fit in
    one tool output, and say which pages to read."""
    for old in glob.glob(f"{name}-*.txt"):
        os.remove(old)
    if sum(len(b) + 1 for b in blocks) <= PAGE_CHARS:
        print("\n".join(blocks))
        return
    pages, cur, size = [], [], 0
    for b in blocks:
        if cur and size + len(b) + 1 > PAGE_CHARS:
            pages.append(cur)
            cur, size = [], 0
        cur.append(b)
        size += len(b) + 1
    pages.append(cur)
    for i, page in enumerate(pages, 1):
        with open(f"{name}-{i}.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(page) + "\n")
    print(f"Sono {len(blocks)} voci, troppe per un solo output: le ho scritte in {len(pages)} pagine. "
          f"Leggile tutte, una alla volta, nell'ordine: " + ", ".join(f"{name}-{i}.txt" for i in range(1, len(pages) + 1)))


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def ascii_low(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def norm(s):
    return " ".join(re.findall(r"[a-z0-9]+", ascii_low(s)))


def co_key(co):
    """Company name without legal suffixes: "Mistral AI" and "Mistral", "CuspAI Ltd" and "CuspAI"."""
    return " ".join(CO_GENERIC.sub(" ", norm(co)).split())


def title_key(t):
    t = re.sub(r"\((?:[^)]{0,12}[/|][^)]{0,12}|all genders?|alle geschlechter)\)|\b[mwfdh]\s*/\s*[mwfdhx](?:\s*/\s*[dxi])?\b",
               " ", ascii_low(t))
    return norm(t)


def foreign(text):
    """True when a description is neither English nor Italian (the candidate reads only those)."""
    w = re.findall(r"[a-zà-ÿ']+", (text or "").lower())
    if len(w) < 15:
        return False
    return sum(x in EN_WORDS for x in w) / len(w) < 0.06 and sum(x in IT_WORDS for x in w) / len(w) < 0.04


ID_PARAM = re.compile(r"[?&]((?:jk|id|jobid|job_id|gh_jid|currentjobid|vjk|pid|reqid|requisitionid)=[^&#]+)", re.I)


def norm_url(u):
    """URL without tracking parameters, keeping the one that names the posting (Indeed's viewjob?jk=…)."""
    u = (u or "").strip().lower()
    m = ID_PARAM.search(u)
    return re.sub(r"[?#].*$", "", u).rstrip("/") + (f"?{m.group(1)}" if m else "") if u else ""


def parse_ts(s):
    try:
        return dt.datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def docs(folder):
    out = {}
    for f in glob.glob(os.path.join(folder, "*.json")):
        d = load(f, {})
        if isinstance(d, dict):
            out[os.path.splitext(os.path.basename(f))[0]] = d
    return out


def slug(*parts):
    s = "-".join(re.findall(r"[a-z0-9]+", ascii_low(" ".join(p for p in parts if p))))
    return s[:56].rstrip("-")


def short(s, n):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def where(loc):
    """area, remote and an Italian place name from a location string."""
    loc = loc or ""
    low = loc.lower()
    remote = "unknown"
    if "remote" in low or "remoto" in low:
        remote = "remote-EU" if re.search(r"europe|emea|\beu\b", low) else \
            "remote-worldwide" if re.search(r"worldwide|anywhere|global", low) else "remote-country"
    elif "hybrid" in low or "ibrido" in low:
        remote = "hybrid"
    # several offices ("Mountain View, CA, USA; Zürich, Switzerland"): the best one for him names the place
    parts = [p for p in loc.split(";") if p.strip()] or [loc]
    found = []
    for p in parts:
        swiss = not NOT_SWISS.search(p)
        a = "zurigo" if swiss and ZURICH.search(p) else "svizzera" if swiss and SWISS.search(p) else \
            "italia" if ITALY.search(p) else None
        if a:
            found.append(({"zurigo": 0, "svizzera": 1, "italia": 2}[a], a, p))
    if found:
        _, area, seg = min(found)
    else:
        area = "remoto" if remote.startswith("remote") else "europa"
        seg = next((p for p in parts if not FAR.search(p)), parts[0])
    city = re.split(r"[,(·|]", seg)[0].strip()
    city = CITY_IT.get(city.lower(), city) or loc
    if remote == "hybrid":
        city += " (ibrido)"
    elif remote.startswith("remote") and "remot" not in city.lower():
        city += " (remoto)"
    return area, remote, city


# ------------------------------------------------------------------ radar
def judged_keys(db, coll="radarRuns"):
    """Scanner keys already judged by an earlier run (each run stores its judgements in one document)."""
    keys = set()
    for r in docs(os.path.join(db, coll)).values():
        for line in (r.get("lines") or "").splitlines():
            keys.add(line.split("|", 1)[0])
        keys.update(r.get("keys") or [])
    return keys


def judged_out(db):
    """Roles (company, title) that an earlier run judged out of the Radar: when they are posted again
    under a new key (LinkedIn reposts, another board) the script gives the same judgement."""
    out = {}
    for _, r in sorted(docs(os.path.join(db, "radarRuns")).items()):
        for line in (r.get("lines") or "").splitlines():
            p = line.split("|")
            if len(p) < 8 or not p[4].isdigit():
                continue
            j = {"tipo": p[3], "fit": int(p[4]), "prest": int(p[5]) if p[5].isdigit() else 1,
                 "anni": int(p[6]) if p[6].isdigit() else None, "motivo": "|".join(p[7:])}
            role = (co_key(p[1]), title_key(p[2]))
            if kept(j, {"agency": bool(AGENCY.search(p[1]))}):
                out.pop(role, None)
            else:
                out[role] = j
    return out


def radar_known(db):
    jobs = docs(os.path.join(db, "jobs"))
    urls, names, keys = set(), set(), set()
    for d in jobs.values():
        for u in (d.get("url"), d.get("applyUrl")):
            if u:
                urls.add(norm_url(u))
        names.add((co_key(d.get("co")), title_key(d.get("title"))))
        if d.get("scanKey"):
            keys.add(d["scanKey"])
    return jobs, urls, names, keys


def cmd_fetch():
    for name in ("status.json", "new_cards.json"):
        get(name)
    st = load("status.json", {})
    nc = load("new_cards.json", {})
    print(f"scanner: run_at {st.get('run_at')} · schede degli ultimi {nc.get('days')} giorni: {nc.get('count')}")


def cmd_cards():
    """Cards not judged yet, most promising first: ML titles (the newest first, then the older ones),
    then the cards with ML words only in the text, then those without any, shown on one line. One
    morning reads up to CARD_PAGES pages; what is left goes first in the next runs."""
    db = arg("--db", "db")
    cap = int(arg("--max", "100000"))
    st, nc = load("status.json", {}), load("new_cards.json", {})
    judged = judged_keys(db)
    out_before = judged_out(db)
    _, urls, names, keys = radar_known(db)
    todo, auto, skipped = [], [], 0
    for c in nc.get("items", []):
        if c["key"] in judged:
            continue
        role = (co_key(c.get("co")), title_key(c.get("title")))
        if c["key"] in keys or norm_url(c.get("url")) in urls or norm_url(c.get("apply_url")) in urls or role in names:
            skipped += 1
            continue
        # judged by the script: reposts of roles already judged out, data-labelling gigs (fit 1 by the rubric)
        if role in out_before:
            j = out_before[role]
            auto.append(dict(key=c["key"], co=c.get("co"), title=c.get("title"), **j))
        elif GIG.search(f"{c.get('co')} {c.get('title')}"):
            auto.append(dict(key=c["key"], co=c.get("co"), title=c.get("title"), tipo="altro", fit=1, prest=1,
                             anni=None, motivo="Gig di annotazione o valutazione dati"))
        elif c.get("senior") or SENIOR_TEXT.search(f"{c.get('card') or ''} {c.get('req') or ''}"):
            # senior by the text, not by the title (the scanner drops senior titles): fit 1 by the rubric
            said = c.get("senior") or SENIOR_TEXT.search(f"{c.get('card') or ''} {c.get('req') or ''}").group(0)
            auto.append(dict(key=c["key"], co=c.get("co"), title=c.get("title"), tipo="altro", fit=1, prest=1,
                             anni=c.get("years"), motivo=f"Ruolo senior: «{short(' '.join(said.split()), 50)}»"))
        elif INTERN.search(c.get("title") or "") and (c.get("students_only") or (
                ENROLLED_TEXT.search(f"{c.get('card') or ''} {c.get('req') or ''}") and
                not GRADS_TEXT.search(f"{c.get('card') or ''} {c.get('req') or ''}"))):
            # an internship for enrolled students: Gabriele graduates in December 2026 (fit 1 by the rubric)
            said = c.get("students_only") or ENROLLED_TEXT.search(f"{c.get('card') or ''} {c.get('req') or ''}").group(0)
            auto.append(dict(key=c["key"], co=c.get("co"), title=c.get("title"), tipo="altro", fit=1, prest=1,
                             anni=c.get("years"), motivo=f"Stage solo per iscritti: «{short(' '.join(said.split()), 50)}»"))
        else:
            todo.append(c)
    # the same role posted several times (other cities, LinkedIn and Indeed): one card, all places, all keys
    rank = lambda c: (c.get("ml") is not True, -(c.get("score") or 0))
    groups = {}
    for c in sorted(todo, key=rank):
        groups.setdefault((co_key(c.get("co")), title_key(c.get("title"))), []).append(c)
    todo = []
    for same in groups.values():
        c = dict(same[0])
        if len(same) > 1:
            locs = list(dict.fromkeys(x.get("loc") for x in same if x.get("loc")))
            c["loc"] = " / ".join(locs[:4]) + (f" e altre {len(locs) - 4}" if len(locs) > 4 else "")
            c["dups"] = [x["key"] for x in same[1:]]
            c["first_seen"] = max(x.get("first_seen") or "" for x in same)
        c["agency"] = bool(AGENCY.search(c.get("co") or ""))
        words = " ".join([c.get("title") or "", c.get("card") or "", c.get("req") or ""] +
                         [k for k in c.get("kw") or [] if not k.startswith("-")])
        ai_swe = c.get("kw") and all(k.startswith("-") for k in c["kw"])   # only LangChain, RAG, agents, 5+ years…
        c["form"] = "full" if c.get("ml") is True and not ai_swe else "short" if ML_TEXT.search(words) else "line"
        todo.append(c)
    fresh_cut = (NOW - dt.timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%SZ")
    order = {"full": 0, "short": 1, "line": 2}
    todo.sort(key=lambda c: (order[c["form"]], (c.get("first_seen") or "") < fresh_cut) + rank(c))
    budget, used, cards = CARD_PAGES * PAGE_CHARS * 0.95, 0, []   # pages are never filled to the last character
    for c in todo:
        size = len(card_block(0, c)) + 1
        if len(cards) >= cap or used + size > budget:
            break
        cards.append(c)
        used += size
    left = len(todo) - len(cards)
    for i, c in enumerate(cards, 1):
        c["n"] = i
    save("cards.json", {"scan": st.get("run_at"), "cards": cards, "auto": auto, "left": left})
    run = parse_ts(st.get("run_at"))
    age = (NOW - run).total_seconds() / 3600 if run else 999
    print(f"Scanner: giro del {st.get('run_at')} ({age:.0f} ore fa){' · VECCHIO: lo scanner non ha girato stanotte' if age > 30 else ''}"
          f"{' · fonti fallite: ' + ', '.join(st.get('failed', [])) if st.get('failed') else ''}")
    n = {f: sum(1 for c in cards if c["form"] == f) for f in order}
    print(f"Da giudicare: {len(cards)} schede ({n['full']} intere, {n['short']} corte, {n['line']} su una riga)"
          f"{f'; ne restano {left} per i prossimi giri' if left else ''}; {skipped} già nel Radar"
          f"{f'; {len(auto)} giudicate dallo script (ripubblicazioni di ruoli già scartati, gig di annotazione dati, ruoli senior dal testo, stage solo per iscritti)' if auto else ''}.")
    print("Formato: n. azienda · ruolo · luogo · fonte · data | anni, livello, parole chiave, segnali | scheda. "
          "Schede intere: titolo da ML. Corte (testo accorciato): parole di ML o dati solo nel testo, o solo "
          "parole chiave negative (LangChain, RAG, agenti, anni). "
          "Su una riga: nessuna parola di ML, dati o AI nel testo, giudica dal titolo.\n")
    emit([card_block(c["n"], c) for c in cards], "cards")


def card_block(n, c):
    sig = list(c.get("sig") or []) + (["agenzia"] if c.get("agency") else [])
    text = c.get("card") or ""
    if foreign(text):
        sig.append("testo non in inglese né in italiano")
        text = text[:100]
    bits = [f"anni {c['years']}" if c.get("years") else "",
            short(c.get("level"), 50) if c.get("level") else "",
            "kw " + ",".join(c["kw"]) if c.get("kw") else "",
            "segnali " + ",".join(sig) if sig else "",
            "scade " + c["deadline"] if c.get("deadline") else "", c.get("salary") or ""]
    line = c.get("form") == "line"
    head = " · ".join(x for x in [c.get("co"), c.get("title"), c.get("loc")] +
                      ([] if line else [BOARD_NAME.get(c.get("via"), ""), c.get("posted") or ""]) if x)
    meta = " | ".join(b for b in bits if b)
    if line or not text.strip():
        return f"{n}. {head}{' | ' + meta if meta else ''}{'' if line else ' | senza descrizione'}"
    text = short(text, 420 if c.get("form") == "full" else 150)
    return f"{n}. {head}\n   {meta + ' | ' if meta else ''}{text}"


def read_judgments():
    out = {}
    try:
        lines = open("giudizi.txt", encoding="utf-8").read().splitlines()
    except OSError:
        sys.exit("manca giudizi.txt")
    for line in lines:
        p = [x.strip() for x in line.split("|")]
        if len(p) < 4 or not p[0].rstrip(".").isdigit():
            continue
        try:
            fit, prest = int(p[2]), int(p[3])
        except ValueError:
            continue
        anni = p[4] if len(p) > 4 else ""
        out[int(p[0].rstrip("."))] = {"tipo": p[1].lower(), "fit": max(1, min(5, fit)), "prest": max(1, min(5, prest)),
                                      "anni": int(anni) if anni.isdigit() else None,
                                      "motivo": "|".join(p[5:]).strip() if len(p) > 5 else ""}
    return out


def kept(j, c=None):
    """Judgements that go into the Radar: not AI-SWE, not MAX_YEARS or more years of experience asked,
    agencies only for a strong role (fit 4-5)."""
    return (j["fit"] >= 2 and j["tipo"] != "ai-swe" and (j.get("anni") or 0) < MAX_YEARS
            and not ((c or {}).get("agency") and j["fit"] < 4))


def cmd_more():
    cards = {c["n"]: c for c in load("cards.json", {}).get("cards", [])}
    js = read_judgments()
    missing = [n for n in cards if n not in js]
    if missing:
        print(f"ATTENZIONE: {len(missing)} schede senza giudizio: {missing[:40]}")
    sel = sorted((n for n, j in js.items() if n in cards and kept(j, cards[n]) and j["fit"] >= 3),
                 key=lambda n: (-js[n]["fit"], -js[n]["prest"]))
    print(f"{len(sel)} schede con fit >= 3: requisiti per scrivere why e req\n")
    emit([f"{n}. {cards[n].get('co')} · {cards[n].get('title')} · {cards[n].get('loc')} "
          f"(fit {js[n]['fit']}, prestigio {js[n]['prest']})\n"
          f"   {short(cards[n].get('req'), 700) or '(requisiti non trovati: guarda la scheda o usa desc)'}"
          for n in sel], "more")


def cmd_desc():
    cards = {c["n"]: c for c in load("cards.json", {}).get("cards", [])}
    if not os.path.exists("candidates.json"):
        get("candidates.json")
    descs = {c["key"]: c.get("desc") for c in load("candidates.json", {}).get("items", [])}
    for a in sys.argv[2:]:
        if a.isdigit() and int(a) in cards:
            c = cards[int(a)]
            print(f"{a}. {c.get('co')} · {c.get('title')} · {c.get('url')}\n{descs.get(c['key']) or '(nessuna descrizione: apri il link)'}\n")


# "Rilevanza" (Gabriele, 17/10), next to the fit with his CV, each part 1-5: how good and well known the company is
# (prestigio, 45%), how close the role is to the frontier AI research he wants (35%), the pay (20%). Very junior roles
# and graduate programmes get a strong push in the order. All from the routine's judgement: no extra tokens.
PREST5 = re.compile(r"\b(?:google|alphabet|deepmind|apple|meta|facebook|amazon|aws|microsoft|nvidia|tesla|netflix|"
                    r"spotify|pinterest|uber|airbnb|linkedin|snap|adobe|bloomberg|jane street|citadel|hudson river|"
                    r"bytedance|tiktok|openai|anthropic|mistral(?: ai)?|cohere|hugging ?face|elevenlabs|"
                    r"perplexity|black forest labs|stability ai|isomorphic|wayve|helsing|synthesia|deepl|"
                    r"aleph alpha|poolside|xai)\b", re.I)
JUNIOR_TITLE = re.compile(r"\bjunior\b|\bjr\b\.?|\bgraduate|\buniversity grad|new[- ]?grad|entry[- ]level|"
                          r"early[- ]career|\btrainee\b|neo-?laureat|"
                          r"\b(?:engineer|scientist|developer|analyst)\s+(?:i|1)\b|"
                          r"\bassociate (?:ml|machine learning|ai|data|research|software|applied)", re.I)
JUNIOR_TEXT = re.compile(r"graduate (?:programme|program|scheme|talent|track)|new[- ]grads?\b|recent(?:ly)? graduat|"
                         r"entry[- ]level|early[- ]career|no (?:prior |professional )?(?:work )?experience (?:is )?"
                         r"(?:required|needed)|\b0\s*[-–]\s*[12]\s*years|junior (?:role|position|level|profile|"
                         r"candidates?)|starting (?:your|their) career|first job|(?:ruolo|profilo|posizione|livello|figura) junior|"
                         r"(?:apert[oaie]|adatt[oa]|pensat[oa]|rivolt[oa]|cerca(?:no)?) (?:anche )?(?:a |ai |agli |a un |ad un |un )?"
                         r"neo-?laureat|appena laureat|programma (?:graduate|per (?:neo-?)?laureati)|nessuna esperienza "
                         r"(?:richiesta|necessaria)|livello (?:entry|d'ingresso|iniziale)|giovan[ei] (?:diplomat|laureat)|"
                         r"jeunes? dipl[oô]m|d[ée]butant", re.I)
RICERCA = {"ricerca": 5, "training": 5, "edge": 4, "data science": 2, "ai-swe": 2, "altro": 1}
FRONTIER = re.compile(r"pre-?train|post-?train|foundation model|self-supervised|\bllms?\b|large language|multimodal|"
                      r"reinforcement learning|research (?:engineer|scientist)", re.I)
PAY = [(4, re.compile(r"london|cambridge|oxford|united kingdom|\buk\b|england|scotland|munich|münchen|berlin|"
                      r"hamburg|frankfurt|stuttgart|germany|deutschland|amsterdam|eindhoven|netherlands|copenhagen|"
                      r"denmark|stockholm|sweden|oslo|norway|dublin|ireland|luxembourg", re.I)),
       (3, re.compile(r"paris|france|vienna|wien|austria|brussels|belgium|leuven|helsinki|finland", re.I)),
       (2, re.compile(r"spain|madrid|barcelona|portugal|lisbon|braga|porto|poland|warsaw|krak|gda|czech|prague|"
                      r"romania|bucharest|cluj|greece|athens|hungary|budapest|bulgaria|croatia|serbia", re.I))]


def is_junior(kind, title, *texts):
    """An explicitly junior role or a graduate programme (internships are a kind of their own)."""
    return kind == "job" and bool(JUNIOR_TITLE.search(title or "") or JUNIOR_TEXT.search(" ".join(t or "" for t in texts)))


def rilevanza(co, prest, tipo, title, area, loc):
    """(rilevanza 1-5 with one decimal, its three parts)."""
    az = 5 if PREST5.search(co or "") else max(1, min(5, int(prest or 1)))
    ri = RICERCA.get((tipo or "").lower(), 2)
    if (tipo or "").lower() != "ai-swe" and FRONTIER.search(title or ""):
        ri = max(ri, 4)
    if area in ("zurigo", "svizzera"):
        st = 5
    elif area == "italia":
        st = 2
    else:
        st = next((v for v, rx in PAY if rx.search(loc or "")), 3)
    if az >= 5:
        st = min(5, st + 1)   # big tech and frontier labs pay above the local market
    return round(0.45 * az + 0.35 * ri + 0.20 * st, 1), {"azienda": az, "ricerca": ri, "stipendio": st}


def prio(fit, rel, junior, area):
    """Order of the page (lower first): the fit first, then, within the same fit, the rilevanza, a strong
    push to junior roles (2 points of rilevanza) and the place. Each fit is a band of its own, so a fit
    basso at a famous company never comes before a fit medio (Gabriele, 18/10)."""
    geo = {"zurigo": 0, "svizzera": 0.5, "italia": 1, "remoto": 1.5}.get(area, 2)
    return round((5 - fit) * 10 + (5 - rel) - (2 if junior else 0) + geo * 0.3, 2)


def run_line(c, j):
    """key|co|title|tipo|fit|prestigio|anni|motivo, the format judged_keys and judged_out read back."""
    clean = lambda s: re.sub(r"[|\n]+", " / ", str(s or ""))
    return "|".join([c["key"], clean(c.get("co")), clean(c.get("title")), j["tipo"], str(j["fit"]), str(j["prest"]),
                     str(j["anni"]) if j.get("anni") is not None else "-", clean(j["motivo"])])


def cmd_docs():
    db = arg("--db", "db")
    state = load("cards.json", {})
    cards = {c["n"]: c for c in state.get("cards", [])}
    js = read_judgments()
    extra = {}
    if os.path.exists("schede.jsonl"):
        for line in open("schede.jsonl", encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    x = json.loads(line)
                    extra[int(x["n"])] = x
                except (ValueError, KeyError, TypeError):
                    print("riga di schede.jsonl non valida:", line[:80])
    jobs, *_ = radar_known(db)
    taken = set(jobs)
    os.makedirs("write", exist_ok=True)
    for f in glob.glob("write/*.json"):
        os.remove(f)
    writes, added, lines, no_why, dup_keys = [], [], [], [], []
    for n, c in sorted(cards.items()):
        j = js.get(n)
        if not j:
            continue
        lines.append(run_line(c, j))
        dup_keys += c.get("dups") or []
        if not kept(j, c):
            continue
        x = extra.get(n, {})
        places = [where(p) for p in (c.get("loc") or "").split(" / ")]   # several places when the role was merged
        rank = {"zurigo": 0, "svizzera": 1, "italia": 2, "remoto": 3}
        area, remote, _ = min(places, key=lambda w: rank.get(w[0], 4))
        city = " / ".join(dict.fromkeys(w[2] for w in places if len(w[2]) > 3 or w[2].istitle()))
        area, remote = x.get("area") or area, x.get("remote") or remote
        pub = c.get("posted") or (c.get("first_seen") or "")[:10]
        via = c.get("via")
        title = x.get("title") or c.get("title") or ""   # the routine names "Pagina aggiornata: X" cards after the real role
        kind = x.get("kind") or ("internship" if re.search(r"intern|stage|stagiai|praktik|tirocin|werkstudent|working student",
                                                          title, re.I) else
                                 "academic" if ACADEMIC.search(c.get("co") or "") else "job")
        rel, parts = rilevanza(c.get("co"), j["prest"], j["tipo"], title, area,
                               " ".join(filter(None, [x.get("loc"), city, c.get("loc")])))
        junior = is_junior(kind, title, c.get("level"), c.get("card"), c.get("req"), x.get("req"), x.get("why"))
        doc = {
            "prio": prio(j["fit"], rel, junior, area), "rel": rel, "relParts": parts, "junior": junior,
            "co": c.get("co"), "title": title, "loc": x.get("loc") or city,
            "remote": remote, "kind": kind, "fit": "alto" if j["fit"] >= 4 else "medio" if j["fit"] == 3 else "basso",
            "area": area, "url": x.get("url") or c.get("url"), "pub": pub, "posted": pub,
            "pubNote": ("Data in cui lo scanner l'ha visto per la prima volta" if not c.get("posted") else
                        "Data di LinkedIn (può essere una ripubblicazione)" if via == "linkedin" else
                        f"Data di {BOARD_NAME[via]}" if via in BOARD_NAME else "Data dal sito dell'azienda"),
            "recent": pub >= "2026-01-01", "deadline": x.get("deadline") or c.get("deadline"),
            "req": x.get("req") or None, "salary": x.get("salary") or c.get("salary"), "flag": x.get("flag") or ("Agenzia" if c.get("agency") else None),
            "why": x.get("why") or j["motivo"], "source": BOARD_NAME.get(via, "sito aziendale"),
            "big": bool(c.get("big")) or parts["azienda"] >= 4, "added": TODAY, "checked": TODAY, "closed": False,
            # phase 3 fields (the page ignores them): Claude's judgement and the scanner key
            "tipo": j["tipo"], "fitScore": j["fit"], "prestigio": j["prest"], "anni": j["anni"], "scanKey": c["key"]}
        if c.get("apply_url") and norm_url(c["apply_url"]) != norm_url(c.get("url")):
            doc["applyUrl"] = c["apply_url"]
        if j["fit"] >= 3 and not x.get("why"):
            no_why.append(n)
        did = base = slug(c.get("co"), title) or "annuncio"
        if did in taken:
            did = f"{base[:50]}-{hashlib.md5(c['key'].encode()).hexdigest()[:5]}"
        taken.add(did)
        save(f"write/{did}.json", doc)
        writes.append({"op": "set", "collection": "jobs", "doc_id": did, "file_path": os.path.abspath(f"write/{did}.json")})
        added.append((doc, j))
    lines += [run_line(a, a) for a in state.get("auto", [])]
    # closures: postings gone from the company sites, deadlines passed, too many years asked, old board postings
    nc = load("new_cards.json", {})
    gone = {c["key"] for c in nc.get("closed", [])} | {norm_url(c.get("url")) for c in nc.get("closed", [])}
    close = [(did, why) for did, d in sorted(jobs.items()) if not d.get("closed")
             for why in [closing_reason(d, gone)] if why]
    run = {"scan": state.get("scan"), "date": TODAY, "judged": len(lines), "added": len(added),
           "lines": "\n".join(lines),   # key|co|title|tipo|fit|prestigio|anni|motivo, read back by judged_keys
           "keys": dup_keys}             # the same postings seen again under other keys (other cities, other boards)
    save("write/radar-run.json", run)
    writes.append({"op": "set", "collection": "radarRuns", "doc_id": NOW.strftime("%Y-%m-%dT%H%M%SZ"),
                   "file_path": os.path.abspath("write/radar-run.json")})
    chunks = [writes[i:i + 50] for i in range(0, len(writes), 50)]
    for i, ch in enumerate(chunks, 1):
        save(f"write/batch-{i}.json", ch)
    if no_why:
        print(f"ATTENZIONE: {len(no_why)} schede con fit >= 3 senza why in schede.jsonl (uso il motivo): {no_why[:40]}")
    unjudged = [n for n in cards if n not in js]
    n_auto = len(state.get("auto", []))
    print(f"Giudicate {len(lines) - n_auto} schede su {len(cards)}{' · SENZA GIUDIZIO: ' + str(unjudged[:40]) if unjudged else ''}"
          f"{f' (più {n_auto} giudicate dallo script)' if n_auto else ''}; da aggiungere al Radar: {len(added)}.")
    print(f"\nSCRITTURE: {len(chunks)} batch. Per ciascuno chiama ArtifactData batch con writes = il contenuto di "
          f"write/batch-N.json (nuovi documenti, nessun if_version):")
    for i in range(1, len(chunks) + 1):
        print(f"  write/batch-{i}.json ({len(chunks[i - 1])} scritture)")
    if close:
        print(f"\nCHIUSURE ({len(close)}): update con if_version dalla lista di 'jobs', "
              f"data {{\"closed\": true, \"closedNote\": ..., \"checked\": \"{TODAY}\"}}:")
        for did, why in close:
            print(f"  {did} · {why}")
    # final message
    st = load("status.json", {})
    good = sorted((x for x in added if x[0]["fit"] != "basso"), key=lambda x: x[0]["prio"])
    low = sum(1 for x in added if x[0]["fit"] == "basso")
    soon = sorted((d["deadline"], d.get("co"), d.get("title")) for d in jobs.values()
                  if not d.get("closed") and d.get("deadline") and TODAY <= d["deadline"] <=
                  (dt.date.fromisoformat(TODAY) + dt.timedelta(days=7)).isoformat())
    print("\nMESSAGGIO FINALE (bozza):")
    print(f"Nuovi da ieri: {len(added)} (giudicate {len(lines)} schede dello scanner)")
    for d, j in good[:MSG_LINES]:
        print(f"- {d['co']} · {d['title']} · {d['loc']} · fit {d['fit']} · rilevanza {d['rel']}"
              f"{' · junior' if d['junior'] else ''}")
    if len(good) > MSG_LINES:
        print(f"- e altri {len(good) - MSG_LINES} con fit alto o medio: sono nel Radar")
    if low:
        print(f"- più {low} con fit basso")
    print(f"Chiusi: {len(close)}")
    for dl, co, t in soon:
        print(f"Scade il {dl}: {co} · {t}")
    run_at = parse_ts(st.get("run_at"))
    if not run_at or (NOW - run_at).total_seconds() > 30 * 3600:
        print("Lo scanner non ha girato stanotte.")
    if st.get("failed"):
        print("Fonti con errori: " + ", ".join(st["failed"]))
    if state.get("left"):
        print(f"Restano {state['left']} schede da giudicare nei prossimi giri.")


def closing_reason(d, gone):
    """Why an open job should close now, or None. The scanner's closures (company sites), deadlines,
    MAX_YEARS or more years asked (by the judgement stored in the document), and postings from Indeed
    and the other boards older than BOARD_DAYS: those boards are never re-read, and their postings
    expire within weeks. LinkedIn postings are asked one by one by `check`."""
    if d.get("scanKey") in gone or norm_url(d.get("url")) in gone:
        return "L'annuncio non è più online."
    if d.get("deadline") and d["deadline"] < TODAY:
        return "La scadenza è passata."
    if isinstance(d.get("anni"), int) and d["anni"] >= MAX_YEARS:
        return f"Chiede {d['anni']} anni di esperienza: troppi per un neolaureato."
    src = d.get("source") or ""
    pub = d.get("pub") or d.get("added") or ""
    if src in BOARD_NAME.values() and src != "LinkedIn" and pub and \
            pub < (dt.date.fromisoformat(TODAY) - dt.timedelta(days=BOARD_DAYS)).isoformat():
        return f"Annuncio di {src} di oltre {BOARD_DAYS} giorni: quasi certamente scaduto."
    return None


UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/128.0 Safari/537.36"}
CLOSED_TEXT = re.compile(r"no longer accepting applications|no longer (?:available|open|active)|position has been filled|"
                         r"job (?:posting )?(?:has )?(?:expired|closed)|this job is closed|"
                         r"(?:posting|vacancy|position|job) (?:is|has been) closed|nicht mehr verf[uü]gbar|"
                         r"n'est plus disponible|non (?:è|e') più disponibile", re.I)


def fetch_status(url, timeout=30):
    """(HTTP status, text) of a page; status 0 when the site does not answer."""
    import urllib.error
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.status, r.read(400_000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (OSError, ValueError):
        return 0, ""


def still_online(url):
    """True, False, or None when it cannot tell. Public job-board APIs where there is one (they answer
    404 for a closed posting), otherwise the page itself: 404 or 410, or a sentence saying it closed."""
    u = url or ""
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", u)
    if m:
        code, _ = fetch_status(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}")
        return True if code == 200 else False if code == 404 else None
    m = re.search(r"jobs\.lever\.co/([\w.-]+)/([0-9a-f-]{36})", u)
    if m:
        code, _ = fetch_status(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
        return True if code == 200 else False if code == 404 else None
    m = re.search(r"jobs\.smartrecruiters\.com/([\w-]+)/(\d+)", u)
    if m:
        code, _ = fetch_status(f"https://api.smartrecruiters.com/v1/companies/{m.group(1)}/postings/{m.group(2)}")
        return True if code == 200 else False if code == 404 else None
    m = re.search(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})", u)
    if m:
        code, text = fetch_status(f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}")
        if code == 200 and '"jobs"' in text:
            return m.group(2) in text
        return None
    code, text = fetch_status(u)
    if code in (404, 410):
        return False
    if code == 200:
        return False if CLOSED_TEXT.search(text) else True
    return None


def cmd_check():
    """Every day a third of the open jobs, in turn (CHECK_TURNS): the LinkedIn ones through LinkedIn's
    public job page, which says when a posting stopped taking applications, and the ones whose link the
    scanner does not follow (added by hand, or from boards) through the job-board APIs or the page.
    Postings the scanner reads from company sites close by themselves in `docs`."""
    import time
    jobs = docs(os.path.join(arg("--db", "db"), "jobs"))
    turn = dt.date.fromisoformat(TODAY).toordinal() % CHECK_TURNS
    mine = lambda did: int(hashlib.md5(did.encode()).hexdigest(), 16) % CHECK_TURNS == turn
    company = lambda d: d.get("scanKey") and d["scanKey"].split(":")[0] not in BOARD_NAME
    todo = [(did, d) for did, d in sorted(jobs.items()) if not d.get("closed") and mine(did) and not company(d)]
    gone, unknown, refused, n_li = [], [], 0, 0
    for did, d in todo:
        m = re.search(r"linkedin\.com/jobs/view/(?:[^/?]*-)?(\d{6,})", d.get("url") or "")
        if m:
            n_li += 1
            if refused >= 3:
                unknown.append(did)
                continue
            code, html = fetch_status(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}")
            if code in (404, 410) or (code == 200 and re.search(r"no longer accepting applications", html, re.I)):
                gone.append((did, "Su LinkedIn non accetta più candidature."))
            elif code != 200:
                unknown.append(did)
                if code == 429:
                    refused += 1
                    time.sleep(30)   # LinkedIn slows down whoever asks too fast
            time.sleep(3)
            continue
        if d.get("source") in BOARD_NAME.values():
            continue   # Indeed and the other boards: closed by age in `docs`
        ok = still_online(d.get("url"))
        if ok is False:
            gone.append((did, "L'annuncio non è più online."))
        elif ok is None:
            unknown.append(did)
        time.sleep(1)
    print(f"Controllati oggi {len(todo)} annunci aperti (un terzo, a turno; {n_li} su LinkedIn): "
          f"{len(gone)} da chiudere, {len(unknown)} senza risposta.")
    if gone:
        print(f"DA CHIUDERE ({len(gone)}): update con if_version dalla lista di 'jobs', "
              f"data {{\"closed\": true, \"closedNote\": ..., \"checked\": \"{TODAY}\"}}:")
        for did, why in gone:
            print(f"  {did} · {why}")
    if unknown:
        print(f"Senza risposta (si riprovano fra {CHECK_TURNS} giorni): " + ", ".join(unknown[:40]))


# ------------------------------------------------------------------ verification
# 29/10 (Gabriele: «ho paura di candidarmi a posizioni che richiedono più esperienza… perché a volte non hai guardato
# il testo»): a job goes into Apply today only after someone read its full posting. `verify` prints the full text of
# the jobs that could go there and were not verified yet; Claude writes verifiche.jsonl; `verify --write` stores it.
VERIFY_MAX = 6       # postings read in full each morning (about 1,500 tokens each)
VERIFY_DAYS = 30     # a verdict older than this is asked again
HANDLED = {"inviata", "colloquio", "offerta", "no", "scartata"}
REQ_START = re.compile(r"(?:requirements|qualifications|what you(?:'ll)? bring|who you are|you have|your profile|"
                       r"we(?:'re| are) looking for|about you|profil|anforderungen|requisiti|must have)", re.I)


def plain(html_text):
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", html_text or "", flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|</(?:p|li|div|h\d)>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    import html as htmlmod
    t = htmlmod.unescape(t)
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", t)).strip()


def full_text(url):
    """(text, where) of a posting: the job-board APIs where there is one, LinkedIn's public job page,
    the JobPosting data of the page, or the page text. ("", why) when it cannot be read."""
    u = url or ""
    m = re.search(r"linkedin\.com/jobs/view/(?:[^/?]*-)?(\d{6,})", u)
    if m:
        code, h = fetch_status(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}")
        if code != 200:
            return "", f"LinkedIn ha risposto {code}"
        if re.search(r"no longer accepting applications", h, re.I):
            return "", "chiuso: su LinkedIn non accetta più candidature"
        d = re.search(r"show-more-less-html__markup[^>]*>(.*?)</div>", h, re.S)
        return (plain(d.group(1)), "LinkedIn") if d else ("", "LinkedIn senza descrizione")
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", u)
    if m:
        code, t = fetch_status(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}")
        return (plain(json.loads(t).get("content", "")), "Greenhouse") if code == 200 else ("", f"Greenhouse {code}")
    m = re.search(r"jobs\.lever\.co/([\w.-]+)/([0-9a-f-]{36})", u)
    if m:
        code, t = fetch_status(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
        if code != 200:
            return "", f"Lever {code}"
        p = json.loads(t)
        parts = [p.get("descriptionPlain") or ""] + [f"{l.get('text')}:\n{plain(l.get('content'))}" for l in p.get("lists") or []]
        return "\n".join(parts), "Lever"
    m = re.search(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})", u)
    if m:
        code, t = fetch_status(f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}")
        if code == 200:
            for j in json.loads(t).get("jobs") or []:
                if j.get("id") == m.group(2):
                    return plain(j.get("descriptionHtml")), "Ashby"
            return "", "chiuso: non più nella bacheca Ashby"
        return "", f"Ashby {code}"
    if re.search(r"indeed\.", u):
        return "", "Indeed non si legge da qui"
    code, h = fetch_status(u)
    if code in (404, 410):
        return "", "chiuso: la pagina non c'è più"
    if code != 200:
        return "", f"il sito ha risposto {code}"
    for blob in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', h, re.S):
        try:
            data = json.loads(blob.strip())
        except ValueError:
            continue
        for x in data if isinstance(data, list) else [data]:
            if isinstance(x, dict) and x.get("@type") == "JobPosting" and x.get("description"):
                return plain(x["description"]), "pagina (JobPosting)"
    if CLOSED_TEXT.search(h):
        return "", "chiuso: la pagina dice che non è più aperto"
    return plain(h)[:12000], "pagina"


def apply_candidates(jobs, status):
    """The jobs that could go into Apply today, in the page's order (pickToday in the Radar page: same rules,
    without the verification itself)."""
    phd_pref = re.compile(r"phd[^.;,]{0,25}(?:preferit|un plus|è un plus|gradit|is a plus|preferred|a plus|desirable|"
                          r"ideal)|(?:preferibilmente|preferably|ideally|idealmente)[^.;]{0,15}(?:phd|dottorato)|"
                          r"phd\s*/\s*(?:msc|master)|phd (?:richiesto|obbligatorio|required)", re.I)
    hard = re.compile(r"phd (?:richiesto|obbligatorio|required|necessario|preferit)|molto selettiv|cercano (?:un )?espert|"
                      r"esperienza richiesta|\bsenior\b|\b[2-9]\+? anni|agenzia|visto|visa\b|descrizione mancante", re.I)
    today = dt.date.fromisoformat(TODAY)
    out = []
    for did, d in jobs.items():
        if d.get("closed") or status.get(did) in HANDLED or d.get("kind") in ("internship", "industrial-phd"):
            continue
        fit = d.get("fitScore") if isinstance(d.get("fitScore"), (int, float)) else {"alto": 4, "medio": 3}.get(d.get("fit"), 2)
        rel = d.get("rel") if isinstance(d.get("rel"), (int, float)) else 0
        junior = d.get("junior") is True
        dead = d.get("deadline")
        open_dead = bool(dead) and dead >= TODAY
        if dead and not open_dead:
            continue
        if fit < (3 if junior else 4) or rel < 3.5 or (isinstance(d.get("anni"), (int, float)) and d["anni"] >= 2):
            continue
        if hard.search(d.get("flag") or "") or (phd_pref.search(d.get("req") or "") and
                                                 not re.search(r"programma graduate", d.get("flag") or "", re.I)):
            continue
        day = (d.get("pub") or d.get("added") or "")[:10]
        age = (today - dt.date.fromisoformat(day)).days if re.match(r"\d{4}-\d\d-\d\d$", day) else 60
        if age > 14 and not open_dead:
            continue
        score = 0.40 * (fit - 1) / 4 + 0.20 * (rel - 1) / 4 + 0.15 * max(0, 1 - age / 30) + (0.15 if junior else 0)
        out.append((score, did, d))
    return [(did, d) for _, did, d in sorted(out, key=lambda x: -x[0])]


def cmd_verify():
    """Full text of up to VERIFY_MAX jobs that could go into Apply today and have no recent verdict; with
    --write, the batch entries that store the verdicts of verifiche.jsonl."""
    import time
    db = arg("--db", "db")
    jobs = docs(os.path.join(db, "jobs"))
    status = {did: (t.get("status") or "") for did, t in docs(os.path.join(db, "tracker")).items()}
    if "--write" in sys.argv:
        rows = [json.loads(l) for l in open("verifiche.jsonl", encoding="utf-8") if l.strip()]
        os.makedirs("write", exist_ok=True)
        writes = []
        for r in rows:
            d = jobs.get(r.get("id"))
            if not d or r.get("verdict") not in ("ok", "no", "dubbio"):
                print(f"Saltata: {r}")
                continue
            data = {"verified": TODAY, "verdict": r["verdict"], "checks": short(r.get("checks"), 400)}
            if r.get("closed"):
                data.update(closed=True, closedNote=short(r.get("checks"), 200), checked=TODAY)
            writes.append({"op": "update", "collection": "jobs", "doc_id": r["id"], "data": data})
        save("write/verify.json", writes)
        print(f"{len(writes)} verdetti in write/verify.json: un solo ArtifactData batch con queste writes, aggiungendo a "
              f"ognuna if_version dalla lista di 'jobs' del passo 2: " + ", ".join(w["doc_id"] for w in writes))
        return
    todo = []
    for did, d in apply_candidates(jobs, status):
        v = d.get("verified") or ""
        if v and (dt.date.fromisoformat(TODAY) - dt.date.fromisoformat(v[:10])).days <= VERIFY_DAYS:
            continue
        todo.append((did, d))
        if len(todo) >= VERIFY_MAX:
            break
    if not todo:
        print("Nessun annuncio da verificare oggi.")
        return
    print(f"{len(todo)} annunci da verificare sul testo completo (candidati agli Apply today). Per ognuno scrivi una "
          f"riga in verifiche.jsonl: {{\"id\": ..., \"verdict\": \"ok\"|\"no\"|\"dubbio\", \"checks\": \"...\"}}, più "
          f"\"closed\": true se il testo dice che è chiuso.\nchecks: italiano, al massimo 30 parole: anni chiesti, PhD, "
          f"lingue, cosa gli manca tra i requisiti obbligatori, che lavoro è davvero. verdict no se chiede 2 o più anni "
          f"(anche «or equivalent»), il PhD (anche solo preferito, salvo i programmi graduate), una lingua che non parla "
          f"(tedesco, francese…), un requisito obbligatorio che non ha per niente, o se il lavoro non è ML/AI (vendite, "
          f"business development, hardware puro, integrazione di API). dubbio se il testo non basta per decidere.\n")
    blocks = []
    for did, d in todo:
        text, where_ = full_text(d.get("url"))
        if re.search(r"linkedin\.com", d.get("url") or ""):
            time.sleep(8)   # LinkedIn answers 429 to whoever asks fast
        i = REQ_START.search(text)
        body = text[max(0, i.start() - 1500):] if i else text
        blocks.append(f"[{did}] {d.get('co')} · {d.get('title')} · {d.get('loc')} · fonte: {where_}\n"
                      f"   {short(body, 4500) or '(testo non letto: verdict dubbio, lo verifica Claude Code col browser)'}")
    emit(blocks, "verify")


# ------------------------------------------------------------------ PhD map
def cmd_phd():
    db = arg("--db", "db")
    for name in ("phd_status.json", "phd_recent.json", "phd_closed.json"):
        get(name)
    st = load("phd_status.json", {})
    pos = docs(os.path.join(db, "positions"))
    judged = judged_keys(db, "phdRuns")
    urls = {norm_url(p.get("url")) for p in pos.values()} | {norm_url(p.get("alt")) for p in pos.values() if p.get("alt")}
    keys = {p.get("scanKey") for p in pos.values() if p.get("scanKey")}
    items = [it for it in load("phd_recent.json", {}).get("items", []) if it["key"] not in judged
             and not it.get("baseline") and it["key"] not in keys and norm_url(it.get("url")) not in urls]
    items.sort(key=lambda it: (it.get("kind") == "academic", it.get("co") or "", it.get("title") or ""))
    run = parse_ts(st.get("run_at"))
    age = (NOW - run).total_seconds() / 3600 if run else 999
    print(f"Scanner PhD: giro del {st.get('run_at')} ({age:.0f} ore fa){' · VECCHIO' if age > 30 else ''}"
          f"{' · fonti fallite: ' + ', '.join(st.get('failed', [])) if st.get('failed') else ''}")
    print(f"Posizioni da giudicare (non ancora giudicate né sulla mappa): {len(items)}")
    print("Formato: n. ente · titolo · luogo · tipo (via portale) · scadenza · stipendio · MSCA | descrizione\n")
    blocks = []
    for i, it in enumerate(items, 1):
        bits = [it.get("co"), it.get("title"), it.get("loc"),
                it.get("kind", "academic") + (f" via {BOARD_NAME.get(it['via'], it['via'])}" if it.get("via") else ""),
                "scade " + it["deadline"] if it.get("deadline") else "", it.get("salary") or "",
                "MSCA non idoneo (Italia)" if it.get("eligible") is False else it.get("eligible_note") or ""]
        sig = it.get("signals") or {}
        flags = [k for k in ("german", "french") if sig.get(k)]
        blocks.append(f"{i}. " + " · ".join(b for b in bits if b) +
                      f"\n   {'lingua: ' + ','.join(flags) + ' | ' if flags else ''}{short(it.get('desc'), 450) or '(nessuna descrizione)'}"
                      f"\n   {it.get('url')} · key {it['key']}")
    emit(blocks, "phd")
    save("phd_cards.json", items)
    gone = {norm_url(c.get("url")) for c in load("phd_closed.json", {}).get("items", [])} | \
           {c.get("key") for c in load("phd_closed.json", {}).get("items", [])}
    close = [(pid, "L'annuncio non è più online.") for pid, p in sorted(pos.items()) if not p.get("closed")
             and p.get("kind") == "annuncio" and (norm_url(p.get("url")) in gone or p.get("scanKey") in gone)]
    close += [(pid, "La scadenza è passata.") for pid, p in sorted(pos.items()) if not p.get("closed")
              and p.get("deadline") and p["deadline"] < TODAY and pid not in dict(close)]
    if close:
        print(f"\nDA CHIUDERE ({len(close)}): update con if_version dalla lista di 'positions':")
        for pid, why in close:
            print(f"  {pid} · {why}")


def cmd_phd_done():
    items = load("phd_cards.json", [])
    save("write/phd-run.json", {"date": TODAY, "count": len(items), "keys": [it["key"] for it in items]})
    did = NOW.strftime("%Y-%m-%dT%H%M%SZ")
    print(json.dumps({"op": "set", "collection": "phdRuns", "doc_id": did, "file_path": os.path.abspath("write/phd-run.json")}))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"fetch": cmd_fetch, "cards": cmd_cards, "more": cmd_more, "desc": cmd_desc, "docs": cmd_docs, "check": cmd_check,
     "verify": cmd_verify, "phd": cmd_phd, "phd-done": cmd_phd_done}.get(cmd, lambda: sys.exit(__doc__))()
