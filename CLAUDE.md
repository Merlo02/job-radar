# job-radar — note per Claude

Motore della ricerca di lavoro e PhD di Gabriele. Parla con lui in italiano.
Il piano di lavoro in corso è in `docs/PLAN.md`: leggilo prima di toccare scanner o fonti.

## Com'è fatto
- `scan.py` legge i portali carriera (adapter per Workday, Greenhouse, Ashby, Lever, SuccessFactors,
  Eightfold, Oracle…) e scrive in `out/`. Le fonti sono in `sources.json` (aziende) e
  `phd_sources.json` (dottorati, `SCAN_PROFILE=phd`, file `out/phd_*`).
- `.github/workflows/scan.yml` lo esegue ogni notte su GitHub Actions (gli orari cron partono
  con ~7 ore di ritardo) e committa `out/` su `main`. Prima di lavorare in locale: `git pull --rebase`.
- A valle, due routine su claude.ai leggono `out/*.json` e aggiornano due pagine (Radar ruoli di
  ricerca, Mappa PhD). Non rinominare né togliere campi da `out/*.json` senza aggiornarle:
  aggiungere campi va bene.
- Test rapido di un adapter: `python scan.py` con una `sources.json` ridotta, oppure
  "Run workflow" dalla tab Actions.

## Regole
- La repo è **pubblica**: niente CV, lettere, email, dati personali. Tutto ciò che è personale sta in
  `private/` e `career-ops/`, ignorati da git. Note personali e contesto: `private/NOTES.md`
  (leggilo quando scrivi lettere o valuti il fit di un annuncio).
- Nelle pagine claude.ai le collezioni `tracker` e `targetChecks` sono dati di Gabriele: mai scriverci.
- Routine: esistono già, si aggiornano, non se ne creano di nuove. Ogni giro consuma i limiti di
  Gabriele: prompt brevi, lavoro meccanico negli script (zero token), Claude solo per giudicare.
- Se trovi un problema nello scanner, sistemalo e pusha.
- Testi per Gabriele (lettere, risposte ai form): prosa naturale, niente tono da AI, niente elenchi
  su elenchi, nessun fatto inventato, nessuna frase attribuita ai suoi supervisor.
