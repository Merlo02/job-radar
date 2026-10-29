# Steps of the two claude.ai routines

The routines' prompts hold the candidate's profile, the page links and the collections. The steps
are here, so that they can change with a push. Each routine copies `tools/radar.py` from its
checkout of this repo into its working directory before starting (`radar.py` below), and never
downloads code from the internet. Shell commands run in the working directory. Everything
user-facing is in Italian.

## Radar

1. `python3 radar.py fetch` downloads the scanner's data. If it prints STOP, end the run with the
   one line it gives you.
2. ArtifactData on the tracker: query "jobs" with where [["closed","==",false]], limit 1000,
   out_dir "db"; list "radarRuns" with limit 1000, out_dir "db"; list "tracker" with limit 1000,
   out_dir "db" (read only: never write to it). Keep the versions shown for "jobs": closures and
   verdicts need them.
3. `python3 radar.py cards --db db` prints numbered cards (company · role · place · source · date |
   years, level, keywords, signals | ~400 characters on the job), as many as one morning can read,
   in page files (cards-1.txt, cards-2.txt…) that it names: read every page with the Read tool, in
   order, and append the lines for that page to giudizi.txt before reading the next one. ML titles
   come first with the full text; then shorter cards (ML words only in the text, or only negative
   keywords); last, cards on one line (no ML, data or AI word in the text: judge from the title).
   Judge EVERY card, one line per card: `n|tipo|fit|prestigio|anni|motivo`
   - tipo: ricerca, training, edge, data science, AI-SWE, altro.
   - fit 1–5 with his CV. 5: research, training or edge role close to his work (foundation models,
     self-supervised learning, signals and sensors, on-device ML), open to a junior. 4: good ML or
     research role open to a junior, other domain. 3: applied ML or data science with real
     modelling open to a new graduate, or a strong role with one obstacle (PhD preferred, 2 years
     asked). 2: adjacent technical role with a real ML part, or a good role with serious obstacles (a
     language he does not speak, UK without visa sponsorship: 2, not 1, when the role itself fits).
     1: out of profile.
   - fit 2 at most (he has had only quick rejections from these, 23/10): a PhD among the minimum
     qualifications, even with "or equivalent practical experience"; Research Scientist roles at big
     tech and frontier labs that do not say they take a master's degree or new graduates; roles whose
     core is hands-on experience he does not have (training LLMs at scale, RL or post-training of
     LLMs, VLA and robot learning, audio DSP) or that ask for an expert or a track record; very
     selective frontier-lab roles with no sign that they hire new graduates.
   - fit 1 always for: AI-SWE roles (integrating ready-made models: LLM APIs, RAG, LangChain,
     agents, copilots, prompt engineering); roles without ML; 3 or more years of experience asked (he is a new graduate: write the number in anni,
     the script keeps them out); senior roles, by the
     title (senior, staff, lead, principal, manager, director) or by the text ("a senior role",
     "we are looking for a Senior…", Level 6 or above, leads delivery and mentors other engineers):
     he is a new graduate and never applies to them; internships for enrolled students only (curricular,
     end-of-studies, thesis, "currently enrolled", "returning to university", French stage with a convention);
     strict PhD requirement; data-labelling and AI-tutor gigs (Alignerr, Outlier, Mercor and
     similar); pure data engineering, BI or reporting (MLOps only as below); staffing agencies and consulting
     body-shops (the AI units of top consulting firms such as QuantumBlack, BCG X and Bain are
     fine); sales, pre-sales or forward-deployed roles for experienced hires; academic PhD
     positions (they go to the PhD map).
   - Data science and consulting AI units: fit 3 at most.
   - Software engineering on ML teams (28/10, Gabriele: a SWE role at a top company is a way in, then move
     to a research team): "Software Engineer, Machine Learning", ML infrastructure or platform, inference,
     MLOps, deep learning libraries, open to new graduates: fit 3 at big tech, frontier labs and strong AI
     companies (4 if the team trains or serves its own models), fit 2 elsewhere. AI-SWE stays fit 1.
   - Internships, graduate programmes and residencies: he graduates in December 2026 and can still do an
     internship afterwards (through the Politecnico, up to 6 months within 12 months of the degree; in Italy a
     tirocinio extracurriculare). An ML internship that does not ask for enrolment is judged like a junior role
     in the same field; with fit 3 or more write kind "internship" and the flag "Stage per neolaureati" when
     the text welcomes graduates, otherwise "Stage: verifica se accetta laureati". The script already gives
     fit 1 to the internships whose text asks for enrolment. Graduate trainee programmes (ESA, Prosus AI Talent
     Lab…) and AI residencies are junior roles: flag "Programma graduate 2027".
   - prestigio 1–5, how good and well known the company is (a name that makes a CV stand out):
     5 big tech (Google, Apple, Meta, Amazon, Microsoft, NVIDIA…, any team), frontier AI labs
     (DeepMind, OpenAI, Anthropic, Mistral, Cohere…), deep-tech scale-ups and startups everyone in AI
     knows (Hugging Face, ElevenLabs, Helsing, Isomorphic, Wayve…) and top institutes (ETH, EPFL, Max
     Planck, CERN); 4 large well-known tech or industrial companies and strong AI startups that build
     their own models (e.g. Domyn); 3 solid companies; 2 small or little-known firms; 1 agencies and
     unknown employers. The script turns prestigio, tipo and place into a "rilevanza" (company 45%,
     closeness to frontier AI research 35%, pay 20%) and pushes junior roles and graduate programmes
     up the page: judge prestigio and tipo with care.
   - anni: the years of experience asked (a number, or -). motivo: at most 12 Italian words.

   Do not skip cards: a card without a line comes back tomorrow. Judge from the card; most cards
   are clearly out of profile and need only a few words. Signals added by the script: "agenzia" is
   a staffing agency posting for an unnamed client (fit 1 unless the role itself is strong: only fit
   4–5 agency postings go into the Radar); "testo non in inglese né in italiano" means the posting
   is written in a language he does not read, so the job almost surely needs it (the card shows only
   its start). A card listing several places is the same role posted more than once: judge it once.
4. `python3 radar.py more` prints the requirements of the cards with fit 3 or more (in pages
   more-1.txt… when they are many: read them all). For each of them write one line in
   schede.jsonl: `{"n": 12, "why": "1–2 Italian sentences tying the role to his experience,
   addressing him as tu, with the honest caveat", "req": "short Italian summary of the
   requirements"}` and, only when needed, "flag" (short label such as "Difesa", "In tedesco",
   "PhD preferito", "Data science", "Programma graduate 2027"; "PhD richiesto", "Esperienza richiesta",
   "Cercano esperto", "Molto selettivo" and "UK senza visto" keep a role out of Apply today), "salary", "kind" ("internship",
   "academic" or "industrial-phd"), "deadline" (YYYY-MM-DD), or "loc", "area", "remote" when the
   script got them wrong. A card titled "Pagina aggiornata: X" is a watched careers page with new
   lines: when it holds a role he fits, also write "title" (the role's real title, as on the page,
   so he can find it in the Radar) and "url" when the new lines give the role's own page. Run
   `python3 radar.py desc <n> …` only for a promising card that is really unclear.
5. `python3 radar.py docs --db db` writes the documents and prints the batches, the closures and a
   draft of the final message. For each write/batch-N.json call ArtifactData batch with writes =
   the JSON array in that file (new documents: no if_version). Then close every job listed under
   CHIUSURE with one batch of "update" entries on "jobs": if_version from step 2, data
   `{"closed": true, "closedNote": the reason printed, "checked": today}`. The closures are postings
   gone from the company sites, deadlines passed, 3 or more years asked, and Indeed or other board
   postings older than 30 days.
6. `python3 radar.py check --db db` asks whether a third of the open jobs (a different third each day)
   are still online: LinkedIn links through LinkedIn, the links the scanner does not follow through
   the job-board APIs or the page. It takes a few minutes. Close the jobs listed under DA CHIUDERE the
   same way (no WebFetch for the ones without an answer: they are asked again in 3 days).
7. `python3 radar.py verify --db db` prints the full text of up to 6 jobs that could go into Apply
   today and were not verified in the last 30 days (Apply today shows only verdict "ok"). Read each
   one in full and write one line per job in verifiche.jsonl, following the rules the script prints:
   no when it asks 2+ years, a PhD (even preferred, except graduate programmes), a language he does
   not speak, a mandatory requirement he lacks entirely, or when the job is not ML/AI. When the text
   could not be read write "dubbio" with "testo non letto": Claude Code verifies those with his
   browser. Then `python3 radar.py verify --db db --write` and one ArtifactData batch with the writes
   of write/verify.json, adding if_version from step 2.

On Mondays only:
- One WebSearch for AI and data roles at Bain, Reply, Generali, Comau and Stellantis in Italy or
  Zurich (no readable job system): add fitting ones by hand with all the fields of the documents
  the script writes (source "sito aziendale").
- Update "targets": for each target with scanner postings set checked = today and found = one
  short Italian sentence like "Scanner: 3 annunci aperti in linea con il profilo" (count its items
  in https://raw.githubusercontent.com/Merlo02/job-radar/main/out/open.json by the "target"
  field); one or two batches, if_version from your read.

Final message (Italian, short: he reads it at breakfast). Start from the draft printed by
`radar.py docs` (add the closures from `check` to the count): "Nuovi da ieri: N", one line per new posting with fit alto or medio (azienda ·
ruolo · città · fit), Zurich ones first, how many with fit basso, closures, deadlines in the next
7 days, scanner problems (stale run or failed sources), and how many cards are left for the next
mornings if the script says so. If nothing is new, say so in one line.

## Mappa PhD

1. ArtifactData on the map: list "positions" (limit 1000, out_dir "db") and "phdRuns" (limit 1000,
   out_dir "db"); list "events" and "discarded" to know the next free ids. Keep the versions shown
   for "positions": closures need them.
2. `python3 radar.py phd --db db` prints the numbered positions not judged yet and not on the map
   (entity · title · place · kind and job board · deadline · salary · MSCA note | start of the
   description, url, key) and the positions to close; when they are many it writes them to page
   files (phd-1.txt…) and names them: read every page with the Read tool. If it prints STOP, end
   the run with the one line it gives you. Judge each one: add the fitting ones to "positions"
   with all the fields (added = today, checked = today, closed = false, phdKind, scanKey, country
   and city always set) and an "events" entry for each deadline (ev = false). Open the url with
   WebFetch only for a promising position whose description is unclear or missing.
3. `python3 radar.py phd-done` prints one batch entry: add it to your last batch, so these
   positions are not shown again.
4. Close the positions listed under DA CHIUDERE (closed = true, closedNote = the reason printed,
   checked = today; if_version from step 1).
5. On Mondays only: download
   https://raw.githubusercontent.com/Merlo02/job-radar/main/out/phd_fellowships.json (weekly check
   of the fellowship pages, with the dates found) and update the "programma" positions and events
   whose deadlines changed or are new; re-check the url of open "annuncio" positions that the
   scanner does not cover (for example lab pages) and close the ones that are gone; run 2
   WebSearch queries such as "funded PhD position machine learning wearable sensing 2027" and
   "PhD self-supervised learning time series 2027 Europe". The "contatto" lab pages only on the
   first Monday of the month.

Writes: use the "batch" action (≤50 writes per call, file_path entries pointing to JSON files you
write in the working directory) and always pass if_version from your read when updating an
existing document. Never delete documents; close them instead.

Final message (Italian, short): "PhD · nuove dall'ultimo giro: N" then one line per new position
(laboratorio o azienda · tema · città e paese · fit · scadenza), alto first; then closures (count
and names), deadlines in the next 14 days, and any scanner problem (stale run or failed sources).
If nothing is new, say so in one line.

## Verifica col browser (Claude Code, quando Gabriele apre una sessione)

The jobs with verdict "dubbio" and checks starting "testo non letto" (LinkedIn refused, Indeed, pages
built in JavaScript) wait for Claude Code: read the posting with Claude in Chrome (Gabriele is logged
in to LinkedIn; never solve a CAPTCHA or a security check: use the company's own careers page or ATS
instead), apply the same rules and write verified, verdict and checks with ArtifactData. Before
proposing any posting to Gabriele outside the Radar, read its full text the same way.
