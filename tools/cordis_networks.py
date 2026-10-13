#!/usr/bin/env python3
"""
MSCA Doctoral Networks to watch, from the open CORDIS dataset of Horizon Europe projects.

Picks the networks of the 2024 and 2025 calls on ML, AI, signals, sensors, vision, robotics and
digital health, finds their website (CORDIS rarely has it: common domain patterns, verified by the
grant number or the project title on the page) and the page listing open positions, and writes
them into phd_sources.json as "watch" sources with kind msca-dn (replacing the previous ones).

    python tools/cordis_networks.py            # about 10 minutes, ~10k HTTP probes
"""
import concurrent.futures as cf
import csv
import html as htmlmod
import io
import json
import os
import re
import zipfile
from collections import defaultdict
from urllib.parse import urljoin, urlsplit

import requests
import urllib3

urllib3.disable_warnings()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = "https://cordis.europa.eu/data/cordis-HORIZONprojects-csv.zip"
CALLS = ("HORIZON-MSCA-2024-DN-01", "HORIZON-MSCA-2025-DN-01")
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"}
CORE = re.compile(r"machine learning|deep learning|artificial intelligence|\bAI\b|neural networks?|computer vision|robot\w*|"
                  r"signal processing|digital health|e-?health|wearables?|foundation models?|language models?|"
                  r"reinforcement learning|sensors?|sensing|data science|computer science|intelligent systems", re.I)
STOP = set("the and for of in on to with a an from through european doctoral network training networks next "
           "generation research".split())
POSITIONS = re.compile(r"open (?:positions?|calls?)|vacanc|recruit|positions?|jobs?\b|careers?|apply|join|"
                       r"doctoral candidates|\bDCs?\b|phd positions?|opportunit", re.I)
COUNTRY = {"AT": "at", "BE": "be", "CH": "ch", "CY": "cy", "CZ": "cz", "DE": "de", "DK": "dk", "EE": "ee", "EL": "gr",
           "ES": "es", "FI": "fi", "FR": "fr", "HR": "hr", "HU": "hu", "IE": "ie", "IT": "it", "LT": "lt", "LU": "lu",
           "LV": "lv", "NL": "nl", "NO": "no", "PL": "pl", "PT": "pt", "RO": "ro", "RS": "rs", "SE": "se", "SI": "si",
           "SK": "sk", "TR": "tr", "UK": "gb"}


def rows(z, name):
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"), delimiter=";")


def networks():
    csv.field_size_limit(10 ** 9)
    z = zipfile.ZipFile(io.BytesIO(requests.get(DATA, headers=H, timeout=300).content))
    proj = {r["id"]: r for r in rows(z, "project.csv") if r["masterCall"] in CALLS}
    voc, web, coord = defaultdict(list), defaultdict(list), {}
    for r in rows(z, "euroSciVoc.csv"):
        if r["projectID"] in proj:
            voc[r["projectID"]].append(r["euroSciVocPath"])
    for r in rows(z, "webLink.csv"):
        if r["projectID"] in proj and r["type"] == "relatedWebsite":
            web[r["projectID"]].append(r["physUrl"])
    for r in rows(z, "organization.csv"):
        if r["projectID"] in proj and r["role"] == "coordinator":
            coord[r["projectID"]] = r
    out = []
    for i, r in proj.items():
        head = " ".join([r["title"], r["keywords"], " ".join(voc[i])])
        if CORE.search(head) or len(CORE.findall(r["objective"])) >= 3:
            c = coord.get(i, {})
            out.append({"id": i, "acronym": r["acronym"], "title": r["title"], "call": r["masterCall"][13:17],
                        "start": r["startDate"], "web": web[i], "coordinator": c.get("name", ""),
                        "country": COUNTRY.get(c.get("country", ""), c.get("country", "").lower()),
                        "coord_url": c.get("organizationURL", "")})
    return out


def is_site(d, url):
    try:
        r = requests.get(url, headers=H, timeout=12, verify=False)
        if r.status_code != 200 or len(r.text) < 500:
            return None
        t = r.text.lower()
        words = {w for w in re.findall(r"[a-z]{5,}", d["title"].lower()) if w not in STOP}
        hit = sum(1 for w in words if w in t)
        acr = re.sub(r"[^a-z0-9]", "", d["acronym"].lower())
        if d["id"] in t or (re.search(r"doctoral|msca|marie", t) and acr in re.sub(r"[^a-z0-9]", "", t)
                            and hit >= max(2, len(words) // 2)):
            return r.url
    except Exception:
        return None


def candidates(d):
    a = re.sub(r"[^a-z0-9]+", "", d["acronym"].lower())
    b = re.sub(r"[^a-z0-9-]+", "-", d["acronym"].lower()).strip("-")
    dom = urlsplit(d["coord_url"] if "://" in d["coord_url"] else "https://" + d["coord_url"]).netloc.replace("www.", "")
    out = list(d["web"])
    for n in dict.fromkeys([a, b, a + "-dn", b + "-dn", a + "dn", a + "-project", a + "project", a + "-msca",
                            a + "-network", a + "network", a + "-eu"]):
        for tld in ("eu", "org", "com", "net"):
            out += [f"https://{n}.{tld}/", f"https://www.{n}.{tld}/"]
        out += [f"https://{n}.github.io/", f"https://{n}-network.github.io/"]
        if dom:
            out.append(f"https://{n}.{dom}/")
    return list(dict.fromkeys(out))


def find_site(d):
    for u in candidates(d):
        found = is_site(d, u)
        if found:
            return found
    return None


def positions_page(site):
    try:
        r = requests.get(site, headers=H, timeout=15, verify=False)
    except Exception:
        return site
    host, best = urlsplit(r.url).netloc, None
    for m in re.finditer(r'<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', r.text, re.S):
        href, text = m.group(1), re.sub(r"<[^>]+>|\s+", " ", htmlmod.unescape(m.group(2))).strip()
        u = urljoin(r.url, href)
        if urlsplit(u).netloc != host:
            continue
        score = 2 * bool(POSITIONS.search(text)) + bool(POSITIONS.search(href)) + \
            bool(re.search(r"open|vacanc|recruit|positions", text + href, re.I))
        if score >= 2 and (not best or score > best[0]):
            best = (score, u)
    return best[1] if best else r.url


def main():
    nets = networks()
    print(len(nets), "networks on topic")
    with cf.ThreadPoolExecutor(24) as ex:
        sites = list(ex.map(find_site, nets))
    found = [(d, s) for d, s in zip(nets, sites) if s]
    print(len(found), "with a website")
    with cf.ThreadPoolExecutor(16) as ex:
        pages = list(ex.map(lambda x: positions_page(x[1]), found))
    srcs = [{"id": "dn-" + re.sub(r"[^a-z0-9]+", "-", d["acronym"].lower()).strip("-"),
             "co": f"{d['acronym']} (MSCA Doctoral Network)", "type": "watch", "url": page, "kind": "msca-dn",
             "country": d["country"], "loc_default": f"{d['coordinator']} ({d['country'].upper()})", "site": site, "call": d["call"],
             "cordis": f"https://cordis.europa.eu/project/id/{d['id']}", "title": d["title"], "origin": "cordis"}
            for (d, site), page in zip(found, pages)]
    path = os.path.join(ROOT, "phd_sources.json")
    current = json.load(open(path, encoding="utf-8"))
    ids = {s["id"] for s in srcs}
    keep = [s for s in current if s.get("origin") != "cordis" and s["id"] not in ids]
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(keep + sorted(srcs, key=lambda s: s["id"]), ensure_ascii=False, indent=1) + "\n")
    print(f"phd_sources.json: {len(srcs)} networks to watch")


if __name__ == "__main__":
    main()
