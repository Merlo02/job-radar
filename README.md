# job-radar

Scanner notturno degli annunci di lavoro ML/ricerca in Europa, letto direttamente dai portali
carriera delle aziende (Workday, Greenhouse, Ashby, Lever, SuccessFactors, Eightfold, Oracle…).

Gira ogni notte su GitHub Actions e salva i risultati in `out/`:

- `out/recent.json` – annunci visti per la prima volta negli ultimi 7 giorni, con estratto della descrizione
- `out/open.json` – tutti gli annunci aperti che passano i filtri
- `out/closed.json` – annunci spariti negli ultimi 14 giorni
- `out/status.json` – esito di ogni fonte nell'ultimo giro

Le fonti sono in `sources.json`, i filtri (titolo e località) in cima a `scan.py`.
Per lanciarlo a mano: tab **Actions** → **Job scanner** → **Run workflow**.
