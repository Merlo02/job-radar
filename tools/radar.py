#!/usr/bin/env python3
"""
The mechanical part of the claude.ai routines (Radar and Mappa PhD), so that Claude only judges.
Standard library only. The routines copy this file from their checkout of the repo (attached to the
routine: their permission check blocks code downloaded at run time) and follow tools/routines.md.

Radar, every morning:
    python3 radar.py fetch                 # status.json and new_cards.json from the repo
    python3 radar.py cards --db db         # numbered cards to judge: not judged by an earlier run, not in the Radar
    (Claude writes giudizi.txt: one line per card, n|tipo|fit|prestigio|anni|motivo)
    python3 radar.py more                  # requirements of the cards with fit >= 3, to write why and req
    python3 radar.py desc 12 40            # full description of some cards, only when the card is unclear
    (Claude writes schede.jsonl for the cards with fit >= 3: {"n", "why", "req", optional overrides})
    python3 radar.py docs --db db          # documents to write, batch entries, closures, final message
    python3 radar.py check --db db         # Mondays: open LinkedIn postings that are no longer online

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
# staffing agencies and recruiters posting for unnamed clients (the rubric gives them fit 1)
AGENCY = re.compile(r"recruit|staffing|headhunt|personnel|personalvermittlung|manpower|randstad|adecco|\bhays\b|"
                    r"michael page|robert half|robert walters|harnham|\bgi group|jefferson frank|xcede|oliver bernard|"
                    r"talent (?:partners|solutions|acquisition)|\bpeople$|jobgether|deeprec|executive search|"
                    r"search partners|\bresourcing\b|hunters?$", re.I)
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
    swiss = not NOT_SWISS.search(loc)
    area = "zurigo" if swiss and ZURICH.search(loc) else "svizzera" if swiss and SWISS.search(loc) else \
        "italia" if ITALY.search(loc) else "remoto" if remote.startswith("remote") else "europa"
    city = re.split(r"[,;(·|]", loc)[0].strip()
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
    """Cards not judged yet: everything first seen in the last 36 hours, then the older ones (most
    promising first) up to --max cards in all; what is left goes first in the next runs."""
    db = arg("--db", "db")
    cap = int(arg("--max", "900"))
    st, nc = load("status.json", {}), load("new_cards.json", {})
    judged = judged_keys(db)
    _, urls, names, keys = radar_known(db)
    todo, skipped = [], 0
    for c in nc.get("items", []):
        if c["key"] in judged:
            continue
        if c["key"] in keys or norm_url(c.get("url")) in urls or norm_url(c.get("apply_url")) in urls or \
                (co_key(c.get("co")), title_key(c.get("title"))) in names:
            skipped += 1
            continue
        todo.append(c)
    # the same role posted several times (other cities, LinkedIn and Indeed): one card, all places, all keys
    groups = {}
    for c in sorted(todo, key=lambda c: (c.get("ml") is not True, -(c.get("score") or 0))):
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
        todo.append(c)
    fresh_cut = (NOW - dt.timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rank = lambda c: (c.get("ml") is not True, -(c.get("score") or 0))
    fresh = sorted((c for c in todo if (c.get("first_seen") or "") >= fresh_cut), key=rank)
    older = sorted((c for c in todo if (c.get("first_seen") or "") < fresh_cut), key=rank)
    cards = (fresh + older)[:cap]   # the most recent first; a long backlog is spread over several mornings
    left = len(todo) - len(cards)
    for i, c in enumerate(cards, 1):
        c["n"] = i
    save("cards.json", {"scan": st.get("run_at"), "cards": cards})
    run = parse_ts(st.get("run_at"))
    age = (NOW - run).total_seconds() / 3600 if run else 999
    print(f"Scanner: giro del {st.get('run_at')} ({age:.0f} ore fa){' · VECCHIO: lo scanner non ha girato stanotte' if age > 30 else ''}"
          f"{' · fonti fallite: ' + ', '.join(st.get('failed', [])) if st.get('failed') else ''}")
    n_fresh = min(len(fresh), len(cards))
    print(f"Da giudicare: {len(cards)} schede ({n_fresh} delle ultime 36 ore, {len(cards) - n_fresh} più vecchie)"
          f"{f'; ne restano {left} per i prossimi giri' if left else ''}; {skipped} già nel Radar.")
    print("Formato: n. azienda · ruolo · luogo · fonte · data | anni, livello, parole chiave, segnali | scheda\n")
    blocks = []
    for c in cards:
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
        head = " · ".join(x for x in [c.get("co"), c.get("title"), c.get("loc"), BOARD_NAME.get(c.get("via"), ""),
                                      c.get("posted") or ""] if x)
        meta = " | ".join(b for b in bits if b)
        blocks.append(f"{c['n']}. {head}\n   {meta + ' | ' if meta else ''}"
                      f"{short(text, 420) or '(nessuna descrizione: giudica dal titolo)'}")
    emit(blocks, "cards")


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
    """Judgements that go into the Radar: agencies only for a strong role (fit 4-5)."""
    return j["fit"] >= 2 and j["tipo"] != "ai-swe" and not ((c or {}).get("agency") and j["fit"] < 4)


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


def prio(fit, prest, area):
    geo = {"zurigo": 0, "svizzera": 1, "italia": 2, "remoto": 2.5}.get(area, 3)
    if fit >= 4:
        return round(1 + (5 - fit) * 2.5 + (5 - prest) * 1.2 + geo * 0.4, 2)
    if fit == 3:
        return round(11 + (5 - prest) * 8 + geo * 2, 2)
    return round(52 + (5 - prest) * 7 + geo * 2, 2)


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
        lines.append("|".join(str(x) for x in (c["key"], c.get("co"), c.get("title"), j["tipo"], j["fit"], j["prest"],
                                                j["anni"] if j["anni"] is not None else "-", j["motivo"])))
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
        title = c.get("title") or ""
        kind = x.get("kind") or ("internship" if re.search(r"intern|stage|stagiai|praktik|tirocin|werkstudent|working student",
                                                          title, re.I) else
                                 "academic" if ACADEMIC.search(c.get("co") or "") else "job")
        doc = {
            "prio": prio(j["fit"], j["prest"], area), "co": c.get("co"), "title": title, "loc": x.get("loc") or city,
            "remote": remote, "kind": kind, "fit": "alto" if j["fit"] >= 4 else "medio" if j["fit"] == 3 else "basso",
            "area": area, "url": c.get("url"), "pub": pub, "posted": pub,
            "pubNote": ("Data in cui lo scanner l'ha visto per la prima volta" if not c.get("posted") else
                        "Data di LinkedIn (può essere una ripubblicazione)" if via == "linkedin" else
                        f"Data di {BOARD_NAME[via]}" if via in BOARD_NAME else "Data dal sito dell'azienda"),
            "recent": pub >= "2026-01-01", "deadline": x.get("deadline") or c.get("deadline"),
            "req": x.get("req") or None, "salary": x.get("salary") or c.get("salary"), "flag": x.get("flag") or ("Agenzia" if c.get("agency") else None),
            "why": x.get("why") or j["motivo"], "source": BOARD_NAME.get(via, "sito aziendale"),
            "big": bool(c.get("big")) or j["prest"] >= 4, "added": TODAY, "checked": TODAY, "closed": False,
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
    # closures: postings gone from the company sites, deadlines passed
    nc = load("new_cards.json", {})
    gone = {c["key"] for c in nc.get("closed", [])} | {norm_url(c.get("url")) for c in nc.get("closed", [])}
    close = []
    for did, d in sorted(jobs.items()):
        if d.get("closed"):
            continue
        if d.get("scanKey") in gone or norm_url(d.get("url")) in gone:
            close.append((did, "L'annuncio non è più online."))
        elif d.get("deadline") and d["deadline"] < TODAY:
            close.append((did, "La scadenza è passata."))
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
    print(f"Giudicate {len(lines)} schede su {len(cards)}{' · SENZA GIUDIZIO: ' + str(unjudged[:40]) if unjudged else ''}; "
          f"da aggiungere al Radar: {len(added)}.")
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
    good = sorted((x for x in added if x[0]["fit"] != "basso"),
                  key=lambda x: (x[0]["fit"] != "alto", {"zurigo": 0, "svizzera": 1, "italia": 2}.get(x[0]["area"], 3),
                                 x[0]["prio"]))
    low = sum(1 for x in added if x[0]["fit"] == "basso")
    soon = sorted((d["deadline"], d.get("co"), d.get("title")) for d in jobs.values()
                  if not d.get("closed") and d.get("deadline") and TODAY <= d["deadline"] <=
                  (dt.date.fromisoformat(TODAY) + dt.timedelta(days=7)).isoformat())
    print("\nMESSAGGIO FINALE (bozza):")
    print(f"Nuovi da ieri: {len(added)} (giudicate {len(lines)} schede dello scanner)")
    for d, j in good[:MSG_LINES]:
        print(f"- {d['co']} · {d['title']} · {d['loc']} · fit {d['fit']}")
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

def cmd_check():
    """Open jobs with a LinkedIn link: the public job page says when a posting stopped taking
    applications (job-board postings never reach the scanner's closures)."""
    import time
    import urllib.error
    jobs = docs(os.path.join(arg("--db", "db"), "jobs"))
    todo = [(did, m.group(1)) for did, d in sorted(jobs.items()) if not d.get("closed")
            for m in [re.search(r"linkedin\.com/jobs/view/(?:[^/?]*-)?(\d{6,})", d.get("url") or "")] if m]
    gone, unknown, refused = [], [], 0
    for did, jid in todo:
        if refused >= 3:
            unknown.append(did)
            continue
        req = urllib.request.Request(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}",
                                     headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                html = r.read().decode("utf-8", "ignore")
            if re.search(r"no longer accepting applications", html, re.I):
                gone.append(did)
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                gone.append(did)
            else:
                if e.code == 429:
                    refused += 1
                    time.sleep(30)   # LinkedIn slows down whoever asks too fast
                unknown.append(did)
        except OSError:
            unknown.append(did)
        time.sleep(4)
    print(f"Annunci LinkedIn aperti controllati: {len(todo) - len(unknown)} su {len(todo)}")
    if gone:
        print(f"DA CHIUDERE ({len(gone)}), closedNote \"Su LinkedIn non accetta più candidature.\":")
        for did in gone:
            print(f"  {did}")
    if unknown:
        print(f"Non controllati (LinkedIn ha rifiutato o non risponde): {len(unknown)}; con WebFetch solo quelli con fit alto o medio:")
        print("  " + ", ".join(unknown[:60]))


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
     "phd": cmd_phd, "phd-done": cmd_phd_done}.get(cmd, lambda: sys.exit(__doc__))()
