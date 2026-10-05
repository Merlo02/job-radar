#!/usr/bin/env python3
"""
Nightly job scanner.

Reads sources.json, asks each company's public careers system for its open jobs,
keeps machine-learning / research roles located in Europe (or with unknown location),
and writes into out/:

  recent.json   postings first seen in the last RECENT_DAYS days, with a description excerpt
  open.json     every matching posting that is currently open (no descriptions)
  closed.json   matching postings that disappeared in the last CLOSED_DAYS days
  status.json   health of every source in the last run
  state.json    internal memory between runs

Only the standard library and `requests` are needed.
"""
import concurrent.futures as cf
import datetime as dt
import email.utils
import hashlib
import html as htmlmod
import json
import os
import re
import sys
import threading
import time
import traceback
import xml.etree.ElementTree as ET
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "out")
NOW = dt.datetime.now(dt.timezone.utc)
NOW_ISO = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
TODAY = NOW.date()

RECENT_DAYS = 7      # how long a posting stays in recent.json
CLOSED_DAYS = 14     # how long a closure stays in closed.json
STALE_DAYS = 10      # search-based sources: a posting unseen this long counts as closed
MAX_DETAIL = 150     # detail requests per source per run
DESC_HEAD = 600
DESC_REQ = 1400
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
INCLUDE = re.compile(
    r"machine[- ]?learning|\bML\b|\bMLE\b|deep[- ]?learning|\bAI\b|\bA\.I\.|\bGenAI\b|\bgen ?ai\b|"
    r"artificial intelligence|intelligenza artificiale|intelligence artificielle|"
    r"k(?:ü|ue)nstliche intelligenz|\bKI\b|\bIA\b|"
    r"research (?:engineer|scientist|fellow|software engineer|associate)|"
    r"applied (?:scientist|research|ml)|data scien|computer vision|\bvision\b|perception|"
    r"algorithm|signal processing|\bDSP\b|\bLLMs?\b|language model|\bNLP\b|natural language|"
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


def title_ok(title):
    t = title or ""
    t_ex = re.sub(r"member of (?:the )?technical staff", "MTS", t, flags=re.I)
    if EXCLUDE.search(t_ex):
        return False
    if INCLUDE.search(t):
        return True
    return bool(GRAD.search(t) and GRAD_TOPIC.search(t))


# ------------------------------------------------------------- location filter
EU_WORDS = [
    "switzerland", "schweiz", "suisse", "svizzera", "italy", "italia", "germany", "deutschland",
    "france", "netherlands", "nederland", "belgium", "belgique", "belgië", "luxembourg",
    "austria", "österreich", "spain", "españa", "portugal", "ireland", "denmark", "danmark",
    "sweden", "sverige", "norway", "norge", "finland", "suomi", "poland", "polska", "czech",
    "czechia", "slovakia", "hungary", "romania", "bulgaria", "greece", "croatia", "slovenia",
    "estonia", "latvia", "lithuania", "united kingdom", "england", "scotland", "wales",
    "great britain", "iceland", "malta", "cyprus", "liechtenstein", "monaco", "europe", "emea",
    "european union",
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
    r"POL|CZE|GBR)(?![A-Za-z])")
VAGUE = re.compile(r"^\s*$|remote|anywhere|worldwide|global|multiple|\d+\s+locations|various|"
                   r"flexible|hybrid|home ?office|to be defined|tbd|several", re.I)
NON_EU_WORDS = re.compile(
    r"(?<![A-Za-z])(?:United States|America|Canada|India|China|Japan|Korea|Singapore|"
    r"Australia|Brazil|Mexico|Israel|Taiwan|Hong Kong|Malaysia|Philippines|Vietnam|Thailand|"
    r"Indonesia|United Arab Emirates|Dubai|Saudi|Qatar|Egypt|South Africa|Argentina|Chile|"
    r"Colombia|Turkey|Türkiye|New Zealand|Kenya|Nigeria|Pakistan|Morocco|Tunisia|Costa Rica|"
    r"San Francisco|New York|Seattle|Boston|Toronto|Bangalore|Bengaluru|Hyderabad|Tokyo|Seoul|"
    r"Shanghai|Beijing|Shenzhen|Sydney|Tel Aviv)(?![A-Za-z])", re.I)
NON_EU_CODES = re.compile(
    r"(?<![A-Za-z])(?:US|USA|U\.S\.|UAE|IND|CHN|JPN|KOR|SGP|CAN|ISR|BRA|MEX)(?![A-Za-z])|"
    r",\s*(?:AL|AK|AZ|AR|CA|CO|CT|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|"
    r"NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)(?=,|\s|\)|$)")


def loc_status(loc):
    loc = loc or ""
    if EU_RE.search(loc) or EU_CODES.search(loc):
        return "eu"
    if NON_EU_WORDS.search(loc) or NON_EU_CODES.search(loc):
        return "no"
    if VAGUE.search(loc):
        return "unknown"
    return "no"


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


def http(method, url, *, json_body=None, headers=None, expect="json", tries=3, delay=0.35):
    last = None
    host = urlsplit(url).hostname
    verify = _CHAINS.get(host, True)
    for i in range(tries):
        try:
            time.sleep(delay)
            try:
                r = session().request(method, url, json=json_body, headers=headers, timeout=40, verify=verify)
            except requests.exceptions.SSLError:
                if verify is not True:
                    raise
                verify = extra_chain(host)
                r = session().request(method, url, json=json_body, headers=headers, timeout=40, verify=verify)
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
    host, domain = src["host"], src["domain"]
    out = {}
    mode = src.get("api", "pcsx")
    for q in queries_for(src):
        for page in range(src.get("pages", 5)):
            start = page * 10
            positions = None
            if mode == "pcsx":
                try:
                    data = http("GET", f"https://{host}/api/pcsx/search?domain={domain}&query={quote(q)}"
                                       f"&location=&start={start}&sort_by=timestamp")
                    positions = (data.get("data") or {}).get("positions") or []
                except (RuntimeError, Gone):
                    mode = "v2"
            if mode == "v2":
                data = http("GET", f"https://{host}/api/apply/v2/jobs?domain={domain}&start={start}"
                                   f"&num=10&query={quote(q)}&sort_by=timestamp")
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
            if len(positions) < 10 or (page >= 1 and hits == 0):
                break
    return list(out.values()), False


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
            l = re.search(r'class="[^"]*location[^"]*"[^>]*>(.*?)</span>', inner, re.S)
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
                r = rec(src, j[0], j[1], url_j, loc, posted, desc=desc, company=j[7] if len(j) > 7 else None)
                out[r["key"]] = r
            except Exception:
                continue
        if not jobs or (total and page * 20 >= total):
            break
    complete = bool(total) and len(out) >= total
    return list(out.values()), complete


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
    "links": (links_list, generic_detail),
    "sitemap": (sitemap_list, sitemap_detail),
    "watch": (watch_list, None),
}


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
        matched, seen = [], set()
        for r in recs:
            if r["key"] in seen:
                continue
            seen.add(r["key"])
            if not title_ok(r["title"]):
                continue
            if src.get("exclude") and re.search(src["exclude"], r["title"], re.I):
                continue
            ls = loc_status(r.get("loc"))
            if ls == "no" and not src.get("ignore_location"):
                continue
            r["loc_status"] = "eu" if src.get("ignore_location") else ls
            matched.append(r)
        n_detail, kept = 0, []
        for r in matched:
            need = detailer and (r["key"] not in known_keys or r["key"] not in has_detail) and \
                (not r.get("desc_full") or r["loc_status"] == "unknown" or r.get("_slug_title"))
            if need and n_detail < src.get("max_detail", MAX_DETAIL):
                n_detail += 1
                try:
                    detailer(src, r)
                except Gone:
                    continue
                except Exception as e:
                    r["detail_error"] = str(e)[:160]
                if r.get("_slug_title") and not title_ok(r["title"]):
                    continue
                ls = loc_status(r.get("loc"))
                if ls == "no" and not src.get("ignore_location"):
                    continue
                r["loc_status"] = "eu" if src.get("ignore_location") else ls
            kept.append(r)
        st.update(ok=True, matched=len(kept), complete=complete, details=n_detail,
                  secs=round(time.time() - t0, 1))
        return src, kept, complete, st
    except Exception as e:
        st["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        st["trace"] = traceback.format_exc(limit=3)[-600:]
        st["secs"] = round(time.time() - t0, 1)
        return src, None, False, st


def public(e, with_desc):
    keys = ["key", "co", "target", "big", "title", "url", "loc", "loc_status", "posted",
            "first_seen", "last_seen", "baseline", "ats"]
    if with_desc:
        keys += ["desc", "signals"]
    return {k: e.get(k) for k in keys if e.get(k) not in (None, "")}


def main():
    os.makedirs(OUT, exist_ok=True)
    sources = load(os.path.join(ROOT, "sources.json"), [])
    only = set(sys.argv[1:])
    if only:
        sources = [s for s in sources if s["id"] in only]
    state = load(os.path.join(OUT, "state.json"), {"jobs": {}, "meta": {}})
    jobs, meta = state["jobs"], state["meta"]
    meta.setdefault("raw_counts", {})
    meta.setdefault("sources_seen", [])
    meta.setdefault("watch", {})
    closed = [c for c in load(os.path.join(OUT, "closed.json"), {"items": []}).get("items", [])
              if days_since(c.get("closed_at", "")) <= CLOSED_DAYS]

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
                                   "big": bool(src.get("big")), "ats": "watch",
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
            if complete and prev_raw and st.get("raw", 0) < 0.5 * prev_raw:
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
                    e["first_seen"] = NOW_ISO
                    if baseline:
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
                e["last_seen"] = NOW_ISO
                if desc_full:
                    e["det"] = True
                    if days_since(e["first_seen"]) <= RECENT_DAYS and not e.get("desc"):
                        e["desc"] = summarize(desc_full)
                        e["signals"] = signals(desc_full)
            st["new"] = n_new
            new_total += n_new
            n_closed = 0
            for k in [k for k, v in jobs.items() if v.get("src") == sid and k not in now_keys]:
                v = jobs[k]
                if v.get("ats") == "watch":
                    if days_since(v.get("first_seen", "")) > RECENT_DAYS:
                        del jobs[k]
                    continue
                if complete or days_since(v.get("last_seen", "")) >= STALE_DAYS:
                    c = public(v, False)
                    c["closed_at"] = NOW_ISO
                    c["reason"] = "not listed anymore" if complete else f"not seen for {STALE_DAYS}+ days"
                    closed.append(c)
                    del jobs[k]
                    n_closed += 1
            st["closed"] = n_closed
            if baseline:
                meta["sources_seen"].append(sid)

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

    recent = [public(v, True) for v in jobs.values() if days_since(v.get("first_seen", "")) <= RECENT_DAYS]
    recent.sort(key=lambda v: (v.get("first_seen", ""), v.get("posted") or ""), reverse=True)
    recent = dedupe(recent)
    open_ = dedupe(sorted((public(v, False) for v in jobs.values() if v.get("ats") != "watch"),
                          key=lambda v: (v.get("co", ""), v.get("title", ""))))
    statuses.sort(key=lambda s: s["id"])
    failed = [s["id"] for s in statuses if not s.get("ok")]

    save(os.path.join(OUT, "state.json"), {"jobs": jobs, "meta": meta})
    save(os.path.join(OUT, "recent.json"), {"generated": NOW_ISO, "days": RECENT_DAYS,
                                           "count": len(recent), "items": recent})
    save(os.path.join(OUT, "open.json"), {"generated": NOW_ISO, "count": len(open_), "items": open_})
    save(os.path.join(OUT, "closed.json"), {"generated": NOW_ISO, "days": CLOSED_DAYS,
                                           "count": len(closed), "items": closed})
    save(os.path.join(OUT, "status.json"), {
        "run_at": NOW_ISO, "duration_s": round(time.time() - t0),
        "sources": len(statuses), "failed": failed, "new": new_total,
        "open": len(open_), "recent": len(recent), "details": statuses})
    print(f"done in {round(time.time() - t0)}s · sources {len(statuses)} · failed {len(failed)} "
          f"· new {new_total} · open {len(open_)}")
    for s in statuses:
        flag = "OK " if s.get("ok") else "ERR"
        print(f"{flag} {s['id']:<24} raw={s.get('raw', '-'):<5} matched={s.get('matched', '-'):<4} "
              f"new={s.get('new', '-'):<4} closed={s.get('closed', '-'):<3} {s.get('error', s.get('note', ''))}")


if __name__ == "__main__":
    main()
