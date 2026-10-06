#!/usr/bin/env python3
"""
One-off checker run on GitHub Actions (the sites below are not reachable from the
assistant's sandbox). Reads tools/probe_in.json and writes tools/probe_out.json.

Each input item is {"id", "url", "mode"}:
  mode "check"  is the posting still online? Uses the ATS API when there is one,
                otherwise loads the page and looks for "no longer available" wording.
  mode "raw"    save the first MAX_RAW bytes of the response to tools/raw/<id>.txt
                (used to look at career portals and their JSON APIs).
"""
import json
import os
import re
import sys
import time
import html as htmlmod
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
MAX_RAW = 400_000

CLOSED_WORDS = re.compile(
    r"no longer (?:available|accepting|open|active|exists)|not accepting applications|"
    r"position (?:has been|is) (?:filled|closed)|this (?:job|position|vacancy|posting|role) "
    r"(?:is|has been) (?:closed|expired|removed|filled)|job (?:not found|has expired|is closed)|"
    r"vacancy (?:has )?(?:closed|expired)|page (?:not found|could not be found)|"
    r"application (?:period|deadline) has (?:ended|passed|expired)|applications? (?:are |is )?closed|"
    r"stelle ist (?:bereits )?besetzt|nicht mehr verfügbar|non (?:è|e') più disponibile|"
    r"n'est plus disponible|niet meer beschikbaar|sorry, we couldn't find|"
    r"the job you are looking for|we can't find|couldn't find that page|404", re.I)


def session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
                      "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"})
    return s


S = session()


def get(url, **kw):
    last = None
    for i in range(2):
        try:
            r = S.get(url, timeout=40, allow_redirects=True, **kw)
            if r.status_code == 429 and i == 0:
                time.sleep(10)
                continue
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(str(last))


def text_of(h):
    h = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", h or "")
    h = re.sub(r"<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", htmlmod.unescape(h)).strip()


def title_of(h):
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", h or "")
    return re.sub(r"\s+", " ", htmlmod.unescape(m.group(1))).strip()[:200] if m else ""


def api_check(url):
    """Return (state, detail) using a public ATS API, or None when there is none."""
    p = urlsplit(url)
    host, path = p.netloc.lower(), p.path.strip("/").split("/")
    if "greenhouse.io" in host and "jobs" in path:
        board, jid = path[0], path[path.index("jobs") + 1]
        r = get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{jid}")
        return ("open" if r.status_code == 200 else "gone" if r.status_code == 404 else "unknown"), f"gh {r.status_code}"
    if host == "jobs.ashbyhq.com" and len(path) >= 2:
        org, jid = path[0], path[1]
        r = get(f"https://api.ashbyhq.com/posting-api/job-board/{org}")
        if r.status_code != 200:
            return "unknown", f"ashby {r.status_code}"
        ids = {j.get("id") for j in r.json().get("jobs", [])}
        return ("open" if jid in ids else "gone"), f"ashby board {len(ids)} jobs"
    if host == "jobs.lever.co" and len(path) >= 2:
        r = get(f"https://api.lever.co/v0/postings/{path[0]}/{path[1]}")
        return ("open" if r.status_code == 200 else "gone" if r.status_code == 404 else "unknown"), f"lever {r.status_code}"
    if host == "apply.workable.com" and len(path) >= 3 and path[1] == "j":
        r = get(f"https://apply.workable.com/api/v2/accounts/{path[0]}/jobs/{path[2]}")
        return ("open" if r.status_code == 200 else "gone" if r.status_code == 404 else "unknown"), f"workable {r.status_code}"
    if host.endswith("linkedin.com") and "view" in path:
        jid = re.sub(r"\D", "", path[path.index("view") + 1])
        r = get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}")
        if r.status_code != 200:
            return ("gone" if r.status_code == 404 else "unknown"), f"linkedin {r.status_code}"
        h = r.text
        closed = bool(re.search(r"No longer accepting applications|closed-job", h))
        t = re.search(r'(?is)<h2[^>]*top-card-layout__title[^>]*>(.*?)</h2>', h)
        posted = re.search(r'(?is)posted-time-ago__text[^>]*>(.*?)<', h)
        return ("gone" if closed else "open"), "linkedin " + (text_of(t.group(1)) if t else "") + " | " + (text_of(posted.group(1)) if posted else "")
    if host.endswith("personio.com") and "job" in path:
        jid = path[path.index("job") + 1]
        r = get(f"https://{host}/xml")
        return ("open" if f"<id>{jid}</id>" in r.text else "gone"), f"personio xml {r.status_code}"
    return None


def check(item):
    url = item["url"]
    out = {"id": item["id"], "url": url}
    try:
        a = api_check(url)
        if a:
            out["state"], out["detail"] = a
            if out["state"] != "unknown":
                return out
        r = get(url)
        h = r.text if "html" in r.headers.get("content-type", "") or r.text[:200].lstrip().startswith("<") else r.text
        t = text_of(h)
        m = CLOSED_WORDS.search(t[:20000])
        out.update({
            "status": r.status_code, "final": r.url, "title": title_of(h), "len": len(t),
            "marker": t[max(0, m.start() - 80): m.end() + 80] if m else None,
            "snippet": t[:500],
        })
        if r.status_code in (404, 410):
            out["state"] = "gone"
        elif r.status_code >= 400:
            out["state"] = "unknown"
        else:
            out["state"] = "check" if m else "open?"
    except Exception as e:  # noqa: BLE001
        out.update({"state": "error", "detail": f"{type(e).__name__}: {e}"[:300]})
    return out


def pdf_text(content):
    try:
        import io
        from pypdf import PdfReader
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(content)).pages[:20])
    except Exception as e:  # noqa: BLE001
        return f"[pdf error {e}]"


def raw(item):
    out = {"id": item["id"], "url": item["url"]}
    try:
        hdr = item.get("headers") or {}
        if item.get("post") is not None:
            r = S.post(item["url"], json=item["post"], headers=hdr, timeout=40)
        else:
            r = S.get(item["url"], headers=hdr, timeout=40)
        body = pdf_text(r.content) if "pdf" in r.headers.get("content-type", "") else r.text
        if item.get("text") and "pdf" not in r.headers.get("content-type", ""):
            body = text_of(body)
        os.makedirs(os.path.join(ROOT, "raw"), exist_ok=True)
        with open(os.path.join(ROOT, "raw", item["id"] + ".txt"), "w", encoding="utf-8") as f:
            f.write(body[:MAX_RAW])
        out.update({"status": r.status_code, "final": r.url, "ctype": r.headers.get("content-type"),
                    "bytes": len(r.content), "title": title_of(r.text)})
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"[:300]
    return out


def run(item):
    return raw(item) if item.get("mode") == "raw" else check(item)


def main():
    items = json.load(open(os.path.join(ROOT, "probe_in.json"), encoding="utf-8"))
    linkedin = [i for i in items if "linkedin.com" in i["url"] and i.get("mode") != "raw"]
    rest = [i for i in items if i not in linkedin]
    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(run, rest))
    blocked = 0
    for i in linkedin:  # LinkedIn rate-limits: one at a time, give up after 3 refusals
        if blocked >= 3:
            res.append({"id": i["id"], "url": i["url"], "state": "unknown", "detail": "linkedin skipped (rate limit)"})
            continue
        r = run(i)
        blocked = blocked + 1 if r.get("state") in ("unknown", "error") else 0
        res.append(r)
        time.sleep(2.5)
    json.dump(res, open(os.path.join(ROOT, "probe_out.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps({s: sum(1 for r in res if r.get("state") == s) for s in
                      {r.get("state") for r in res}}, indent=1))


if __name__ == "__main__":
    sys.exit(main())
