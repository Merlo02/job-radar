# job-radar

Scanner notturno degli annunci di lavoro ML/ricerca in Europa, letto direttamente dai portali
carriera delle aziende (Workday, Greenhouse, Ashby, Lever, SuccessFactors, Eightfold, Oracle…) e
dalle API di ricerca delle big tech (Amazon, Apple, Meta, IBM, Google, Microsoft). LinkedIn (ricerca
pubblica) e Indeed, letti con [JobSpy](https://github.com/speedyapply/JobSpy), servono a trovare le
aziende che mancano e a controllare che gli adapter diretti non perdano annunci.

Gira ogni notte su GitHub Actions e salva i risultati in `out/`:

- `out/recent.json` – annunci visti per la prima volta negli ultimi 7 giorni, con estratto della descrizione
- `out/open.json` – tutti gli annunci aperti che passano i filtri
- `out/closed.json` – annunci spariti negli ultimi 14 giorni
- `out/status.json` – esito di ogni fonte nell'ultimo giro, avvisi (`warnings`: fonti a zero, fonti
  dimezzate, annunci visti su LinkedIn/Indeed ma assenti dall'adapter dell'azienda) e il controllo di
  copertura (`coverage`)

- `out/candidates.json` – tutti gli annunci aperti che passano il filtro largo (esclude solo funzioni
  non tecniche, seniority impossibili, stage/tesi/postdoc e luoghi fuori Europa), ciascuno con una
  scheda (~400 caratteri sul lavoro), anni di esperienza chiesti, parole chiave e un punteggio
  indicativo che serve solo a ordinarli; la descrizione (fino a 5.000 caratteri) c'è per gli annunci
  visti negli ultimi 3 giorni. In `closed` ci sono tutte le chiusure degli ultimi 14 giorni.

`recent.json`, `open.json` e `closed.json` restano come prima: solo titoli ML (`title_ok` in
`scan.py`) e niente LinkedIn/Indeed (`BOARDS_IN_RECENT`), perché la routine che li legge giudica
ogni annuncio nuovo. Gli annunci che ripetono quello del sito dell'azienda contano una volta sola,
con il link diretto.

Con lo stesso giro lo scanner legge anche i dottorati (`phd_sources.json`: ETH, UZH, EPFL,
AcademicTransfer per tutte le università olandesi, KU Leuven, Ghent) e scrive `out/phd_recent.json`,
`out/phd_open.json`, `out/phd_closed.json` e `out/phd_status.json`. Si lancia a mano con
`SCAN_PROFILE=phd python scan.py`.

Le fonti sono in `sources.json`, i filtri (titolo e località) in cima a `scan.py`.
Per lanciarlo a mano: tab **Actions** → **Job scanner** → **Run workflow**. In locale serve
Python ≥ 3.10 con `pip install requests python-jobspy`; `python scan.py id1 id2` gira solo quelle fonti.

Per chi ci lavora con Claude Code: regole in `CLAUDE.md`, piano in corso in `docs/PLAN.md`.
