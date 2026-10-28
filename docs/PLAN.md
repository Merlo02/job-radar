# Piano: job-radar 2.0

Obiettivo: vedere tutto ciò che Gabriele troverebbe cercando a mano su LinkedIn e sui siti delle
aziende, e lasciare a Claude (non alle parole chiave) il compito di scartare.

Decisioni già prese (ottobre 2026):
- **Paesi:** tutta Europa, Regno Unito, Svizzera e Norvegia comprese, più i ruoli remote-Europa.
- **Ruoli senza gerarchia.** Contano due cose: il prestigio della posizione e il fit col CV. Edge AI,
  pretraining, post-training, data science con modelli o ricerca vanno tutti bene.
- **Da scartare:** solo i ruoli da "AI-SWE", cioè integrazione di modelli già fatti (API LLM,
  RAG, LangChain, agenti, prompt engineering), più quelli chiaramente troppo senior. Lo decide
  Claude leggendo l'annuncio, non un filtro sul titolo.
- **Filtro largo:** lo script toglie solo l'impossibile, Claude giudica tutto il resto.
- **Aziende:** più ne abbiamo, meglio è.

## Come è organizzato il lavoro
- **Repo `job-radar`** (pubblica), clonata in locale e collegata a GitHub. Codice e lista delle
  aziende si cambiano qui e si pushano. GitHub Actions fa girare tutto ogni notte.
- **Cartella `career-ops/`** dentro la repo locale, ignorata da git: clone di
  https://github.com/santifer/career-ops con CV, profilo e candidature. Non va mai pushata.
- **Cartella `private/`** (ignorata): `NOTES.md` con il contesto personale, CV in PDF, lettere.
- **Routine claude.ai e pagine Radar e Mappa PhD:** restano. Le aggiorna una sessione claude.ai
  alla fine della fase 3 (il terminale potrebbe non avere gli strumenti per farlo).

## Stato (13 ottobre 2026)

**Fase 1 fatta**, con queste differenze dal piano:
- LinkedIn: le 17 famiglie sono raggruppate con OR in 8 ricerche su `location=Europe`, perché
  ogni pagina costa 5-20 secondi (429 frequenti) e una sola famiglia arriva a centinaia di risultati
  al giorno. Ogni ricerca copre il tempo dall'ultima volta che è finita (24-96 ore) e il budget di
  tempo (`search_min`, `budget_min` in `sources.json`) è controllato a ogni richiesta: quello che
  resta fuori parte per primo al giro dopo. Le descrizioni si scaricano solo per gli annunci nuovi
  che non ripetono quello del sito dell'azienda.
  Primo giro su Actions (13/10): LinkedIn risponde ma rifiuta spesso (69 risposte 429): in 38 minuti
  ha finito 2 ricerche su 8 (1055 risultati, 411 utili). Ora ha fino a 180 minuti di ricerche e 240
  in tutto (`search_min`, `budget_min`). Se non bastano: far girare LinkedIn da una macchina di
  Gabriele.
- Indeed risponde solo in 18 paesi europei (gli altri danno 400 o niente).
- Big tech: Amazon, IBM, Apple, Meta con adapter propri; Netflix (Eightfold v2) e Stripe
  (Greenhouse) con quelli esistenti. Google ora legge `location=Europe` in un colpo solo (le ricerche
  per città erano anche troncate: Londra 80 su 135), Microsoft legge ogni paese europeo (la ricerca
  globale non arrivava mai all'Europa: 0 annunci).
- Punto 4: aggiunte NXP, Arm, Continental, Adyen, DeepL, Owkin, Cradle, Aignostics. Restano fuori
  (nessun ATS leggibile o API bloccata): Infineon (403), VW/CARIAD, Porsche, Audi, Stellantis,
  Toyota Motor Europe, ZF, Pruna AI, Bioptimus. Le copre LinkedIn.
- Aggregatori (punto 5): The Hub, Landing.jobs, joinup.ch, Welcome to the Jungle, HN «Who is
  hiring», trattati come LinkedIn/Indeed (stessa deduplica e audit, solo in `candidates.json`).
  `coverage.new_companies` in `status.json` elenca le aziende con annunci ML sui board che non sono
  ancora in `sources.json`: è la lista da cui pescare quelle nuove.
- Audit: `status.json` ha `warnings` (fonti a 0, dimezzate, lacune) e `coverage.gaps` (annunci di
  aziende in `sources.json` visti su LinkedIn/Indeed ma assenti dal loro adapter).
- Deduplica: azienda + titolo normalizzato + città; vince il sito dell'azienda (`dup_of`).

**Fase 2 fatta.** Il filtro largo (`wide_ok`) decide cosa entra nello stato e in
`out/candidates.json`; il vecchio filtro sui titoli ML (`title_ok`) resta come campo `ml` e decide
ancora cosa va in `recent.json`, `open.json` e `closed.json`, così la routine Radar di oggi non si
trova centinaia di annunci in più. Anche gli annunci di LinkedIn e Indeed stanno solo in
`candidates.json` (`BOARDS_IN_RECENT = False`).

Verifica sui dati veri del 14/10 (7806 annunci aperti, 604 nuovi in una notte):
- Esclusioni sicure aggiunte, controllate titolo per titolo (271 annunci in meno):
  - sanità e cura (infermieri, psicologi, terapisti);
  - finanza non tecnica (attuari, underwriting, credit risk, wealth);
  - contenuti e SEO;
  - logistica e facility;
  - insegnanti;
  - postdoc scritti in altri modi («Post Doctoral», «Postdoktorand»);
  - «Principle Engineer».

  Escono anche, pur con un titolo ML, i lavori di annotazione dati e di «AI training» (`GIG`).
- Un titolo ML con un ruolo tecnico resta anche se nomina una funzione esclusa («Applied
  Scientist, Procurement», «Auditor Data Scientist»): prima si perdeva.
- Le posizioni PhD trovate su LinkedIn, Indeed e gli altri portali (~80 aperte) non stanno più tra
  i lavori: vanno alla Mappa PhD (vedi fase 1-bis, `boards-phd`).
- `candidates.json` pesa ~12 MB: per la fase 3 la routine non può leggerlo intero, serve un file
  compatto con le sole schede nuove (da decidere con la routine).

`out/candidates.json`: `items` (annunci aperti, ordinati per `score`) con key, src, co, title, loc,
posted, first_seen, url, apply_url, ats, target, big, baseline, ml, score, kw (parole chiave che
hanno dato o tolto punti), years (anni di esperienza chiesti, il minimo più alto trovato), card
(~400 caratteri sul lavoro da fare), level, dept, e `desc` (fino a 5.000 caratteri) solo per gli
annunci visti negli ultimi 3 giorni; `closed` (tutte le chiusure degli ultimi 14 giorni). Al primo
giro col filtro largo gli annunci non-ML già aperti sono marcati `baseline`.

**Fase 3 fatta (14/10), routine aggiornate da Gabriele il 14/10: primo giro giovedì 15/10 (Radar 8:00, Mappa PhD 8:30).**

15/10, primo giro: il controllo dei permessi delle routine (modalità auto) blocca con
[Code from External] l'esecuzione di `radar.py` scaricato con curl, anche in un comando separato.
Il Radar si è fermato al passo 1 (due volte); la Mappa PhD ha letto lo script e se ne è scritta
una copia ridotta da sola (15 posizioni aggiunte, `phdRuns` scritto). Risolto il 15/10:
Gabriele ha creato due routine Claude Code con il repo collegato («Radar ruoli ML», ogni giorno alle
8:00, modello predefinito Sonnet; «Radar phd», lunedì e giovedì alle 8:30, Opus) e spento le due
routine Cowork. Copiano `tools/radar.py` dalla checkout e seguono `tools/routines.md`: i prompt
tengono solo profilo, pagine e collezioni, i passi si cambiano con un push. Giro di prova del Radar:
900 schede giudicate in 6 minuti, 104 annunci scritti.

Correzioni dopo il giro di prova (15/10): `norm_url` toglieva `?jk=` agli URL di Indeed, quindi tutti
gli annunci Indeed di un paese sembravano «già nel Radar» (~800 schede saltate); Baden-Württemberg
finiva nella zona Zurigo; lo stesso ruolo in più città diventava più annunci (ora una scheda con tutte
le sedi, le altre chiavi in `keys` del documento `radarRuns`); le agenzie di selezione entrano solo con
fit 4–5 e il bollino «Agenzia»; le schede in lingue che Gabriele non legge mostrano solo l'inizio
del testo. Corretti a mano nel Radar 3 zone, 6 agenzie e 11 doppioni (chiusi con una nota).
`NEW_DAYS` passa a 21: con ~3300 schede in arretrato e 900 al giorno, a 10 giorni le più vecchie
sarebbero uscite dal file prima di essere giudicate.

16/10, primo giro del mattino: 900 schede in 5 minuti, 55 annunci nel Radar (8 fit medio), 6 chiusure.
Ma lo scanner porta ~2000 schede a notte (LinkedIn ~1400, Indeed ~700), più delle 900 lette: le 900
erano tutte della notte, quindi l'arretrato del 13/10 (~1000 titoli ML) non sarebbe mai arrivato. Resa
dei giudizi già dati, per tipo di scheda: titolo ML 10-17% con fit ≥ 2, parole ML solo nel testo 1-4%,
nessuna parola ML 0-1% (e quelle poche giudicate dal titolo). Quindi `cards` ora mette prima i titoli
ML (prima i nuovi, poi l'arretrato), poi le schede corte (parole ML solo nel testo, o solo parole chiave
negative: testo a 150 caratteri), poi quelle su una riga (nessuna parola ML, dati o AI: solo il titolo);
il limite è in pagine (`CARD_PAGES` = 20, ~450.000 caratteri, prima 16) e non in schede: ~2000 schede a
mattina. Lo script giudica da solo, con una riga nel documento del giro, le ripubblicazioni di ruoli
già scartati (stessa azienda e titolo) e i gig di annotazione dati (Alignerr, Outlier, TELUS…, fit 1
per la rubrica). Il Radar mette nel luogo la sede migliore per lui ("Mountain View; Zürich" → Zurigo).
Lo scanner non scambia più Florence SC o Rome GA per Firenze e Roma. `.gitignore` copre i file di
lavoro delle routine (che lavorano nella radice della checkout: un hook chiedeva di committarli).
IIT: lo scanner leggeva solo la prima pagina delle offerte (10 su 43) e prendeva come titolo la
categoria del link ("Postdoc", "Fellow", "Technician"); ora legge `?cur=1…10` e prende il titolo
dall'URL (`title_slug`). joinup.ch e Landing.jobs rispondono 403 solo da GitHub Actions dal 13/10
(`ci_blocked`: riprovati ogni notte, in `status.json` sotto `blocked` e non più tra le fonti fallite);
CENTAI dà 522 anche da fuori (sito giù).
Rilevanza e ruoli junior (17/10, chiesto da Gabriele dopo i primi rifiuti): accanto al fit ogni annuncio ha una
«rilevanza» da 1 a 5 calcolata da `radar.py` (`rilevanza()`), senza token in più per la routine: azienda 45% (il
prestigio della routine, con la rubrica riscritta: big tech, laboratori di frontiera e scale-up deep tech famose a 5;
`PREST5` lo forza per i nomi noti), ricerca 35% (dal tipo: ricerca e training 5, edge 4, data science e AI-SWE 2,
altro 1), stipendio 20% (dal paese, +1 per le aziende a 5). `is_junior()` riconosce ruoli junior, graduate, entry
level e new grad da titolo, requisiti e motivo (anche in italiano: «aperta a neolaureati»). Il `prio` ora somma fit,
rilevanza e una spinta forte ai junior. Nella pagina: scheda «Junior e graduate» (la prima), etichette rilevanza e
junior, ordine «consigliato» (prio) e «per rilevanza». Ricalcolati i 613 annunci aperti del 17/10 (211 senza
prestigio e tipo, giudicati a mano).
Ruoli senior dal testo (16/10): il filtro sui titoli lasciava passare annunci come «AI Engineer» di Roche,
che nel testo dice «The AI Engineer Level 6 is a senior hands-on technical contributor», e la routine lo
aveva messo nel Radar con fit medio. Ora lo scanner cerca nella descrizione le frasi che dicono che il
ruolo è senior (`SENIOR_TEXT`: «is a senior…», «we are looking for a Senior…», «Engineer Level 6»,
«mentor junior engineers») e le salva nel campo `senior` di `candidates.json` e `new_cards.json`, con la
parola chiave `-senior`. `radar.py cards` le giudica da solo con fit 1, anche dal testo della scheda, e la
rubrica dice di dare fit 1 ai ruoli senior anche quando lo dice solo il testo. Prova su candidates.json:
107 annunci su 11.563, quasi tutti senior davvero. Nel Radar chiusi con una nota 4 annunci già
dentro (Roche AI Engineer, LiveEO, BAE Systems, Samba).
Pulizia del Radar (18/10, chiesto da Gabriele: molti annunci vecchi non accettavano più candidature, e alcuni,
come Snap «Software Engineer, CV» a Vienna, chiedevano requisiti fuori portata). Prima le chiusure arrivavano solo dai
siti delle aziende (`new_cards.json` → `closed`) e, il lunedì, dai link LinkedIn: gli annunci di Indeed e degli altri
portali e i 210 aggiunti a mano dalle vecchie routine (senza `scanKey`) non si chiudevano mai. Ora:
- `radar.py docs` chiude anche gli annunci che chiedono 3 o più anni (`MAX_YEARS`, dal campo `anni` del giudizio) e
  quelli di Indeed e degli altri portali più vecchi di 30 giorni (`BOARD_DAYS`): quei portali non si rileggono e i loro
  annunci scadono in poche settimane. `kept()` non fa più entrare i ruoli con 3 o più anni.
- `radar.py check` gira ogni giorno su un terzo degli annunci aperti, a turno (`CHECK_TURNS`): LinkedIn dalla pagina
  pubblica dell'annuncio, gli altri link che lo scanner non segue dalle API di Greenhouse, Lever, Ashby e
  SmartRecruiters o dalla pagina (404, 410, frasi di chiusura). Quelli dei siti aziendali si chiudono già in `docs`.
- `prio` mette il fit prima di tutto: ogni fit è una fascia (10 punti), dentro la fascia contano rilevanza, junior
  (2 punti) e luogo. Prima un fit basso di una big tech (rilevanza 4,8) finiva alla pari di un fit medio.
- Rubrica: 3 anni chiesti → fit 1 (prima era fit 3 «con un ostacolo»), 2 anni restano un ostacolo.
- Pulizia una tantum (18/10, dal terminale): 651 annunci aperti. Chiusi 33 che chiedevano 3 anni e 7 non più online
  (Ericsson ×2, GE Healthcare, Helsing, DNV, Microsoft, EPFL Apertus: tutti verificati sul sito); `prio` ricalcolato su
  tutti; 107 dei 153 aggiunti a mano dalle vecchie routine hanno ora la `scanKey` dello scanner (agganciati per
  identificativo e titolo: si chiuderanno da soli), i 42 restanti li segue `check`. I 338 link LinkedIn controllati uno a uno
  dal terminale (LinkedIn rifiuta spesso: ~2 ore).
«Apply today» (18/10, chiesto da Gabriele: «ogni mattina mi candiderò di sicuro a tutte e 5»): in cima alla pagina del
Radar, fino a 5 annunci da candidarsi oggi. Li sceglie la pagina stessa alla prima apertura della giornata (zero token,
nessun passo della routine) e li salva nella collezione `picks` (documento = data, campo `ids`), così restano fissi fino
a domani anche se Gabriele li segna come inviati. Punteggio: rilevanza 40%, fit 35%, recenza 25% (lineare su 30 giorni
dalla data di pubblicazione), +0,05 ai junior, +0,10 se scade entro 7 giorni. Filtri: aperti, ancora da gestire nel
tracker, fit ≥ 3, rilevanza ≥ 3,5, niente stage, agenzie o annunci che richiedono un visto (flag o motivo del giudizio:
«senza visto», «serve un visto»), una posizione per azienda. Il pulsante «Inviata» scrive lo stato come la tendina.
Apply today solo entry level (23/10, chiesto da Gabriele: «ci stiamo candidando alle posizioni sbagliate… spingere su ruoli
entry level», dopo un Research Scientist di DeepMind con PhD tra i requisiti minimi in cima alla lista e i rifiuti rapidi di
H Company e Medal). Dal 18/10 al 23/10 quasi tutti gli Apply today erano fit medio con rilevanza ≥ 4,3 e bollini come
«Molto selettivo», «PhD richiesto», «Cercano esperto»: la rilevanza (40%) batteva il fit. Ora `pickToday()` nella pagina
prende solo ruoli junior o graduate con fit ≥ 3 e gli altri con fit ≥ 4, rilevanza ≥ 2,5, e scarta: 2 o più anni, bollini
da esperto o PhD richiesto (o agenzia e visto), requisiti che iniziano con il PhD senza alternativa alla magistrale,
«esperto di…», «track record», i Research Scientist che non accettano la magistrale, il Regno Unito senza sponsor del visto.
Ordine: fit 40%, +0,25 ai junior, rilevanza 20%, recenza 15%, scadenze. Se ne trova meno di 5 ne mostra meno. Sui dati
del 23/10: 25 annunci candidabili, 16 junior. Rubrica della routine: fit 2 al massimo per PhD tra i requisiti minimi (anche
con «or equivalent practical experience»), Research Scientist di big tech e laboratori che non dicono di accettare la
magistrale, ruoli che chiedono esperienza pratica che non ha (training di LLM su larga scala, RL o post-training di LLM,
VLA, DSP audio) o un esperto, ruoli molto selettivi senza segni di assunzioni di neolaureati. `is_junior()` riconosce anche
«livello entry», «giovane diplomato», «jeune diplômé», «débutant». Una tantum dal terminale: 19 annunci aperti abbassati a
fit basso con la rubrica nuova (campi `fitPrima` e `fitNota`), 3 segnati junior, Apply today del 23/10 rifatti.
24/10 (Gabriele: AI4I con fit alto non era negli Apply today e non lo trovava nel Radar). Tre cause: la pagina era stata
aperta poco dopo mezzanotte e aveva fissato la lista del giorno prima che la routine aggiungesse gli annunci nuovi; il
bonus junior (+0,25) faceva passare un fit medio junior davanti a un fit 5; la regola sugli anni scattava su «1-3 anni
oppure neolaureati». La scheda si chiamava «Pagina aggiornata: AI4I (Torino)» (le pagine `watch` dello scanner) e stava
nella scheda Recentissime. Ora: la lista si salva solo dopo il giro del mattino (un annuncio con `added` o `checked` di
oggi) o dopo mezzogiorno, prima è provvisoria; bonus junior +0,15 (vale circa un punto di fit); gli anni dai requisiti
contano solo se non sono un intervallo che parte da 0 o 1 e il ruolo non è junior; la routine può scrivere `title` e
`url` in schede.jsonl per le schede «Pagina aggiornata» e `radar.py docs` li usa. Snap Machine Learning Engineer (Parigi,
team BCI) chiuso: 3+ anni nei requisiti minimi.
28/10 (Gabriele, dopo la chiusura del ruolo Pinterest: «dobbiamo anche puntare ai tirocini», e i ruoli da ML Engineer per
neolaureati in Europa sono rarissimi, quindi anche SWE nei team ML delle aziende top). Stage: lo scanner scartava tutti gli
stage (`NOT_A_JOB`); ora passano quelli con un titolo ML (`ml_internship`), perché Gabriele si laurea a dicembre 2026 e può
farne uno dopo la laurea (post-lauream del Politecnico: entro 12 mesi dal titolo, al massimo 6 mesi, anche all'estero).
Restano fuori tesi, working student, apprendistati, stage curricolari, di fine studi e per dottorandi (`STUDENT_ONLY`).
Se il testo chiede l'iscrizione (`ENROLLED_TEXT`: «currently enrolled», «working towards a Master's», «returning to
university», «immatrikuliert», «convention de stage») e non accoglie i laureati (`GRADS_TEXT`), lo scanner scrive la frase
nel campo `students_only` di `candidates.json` e `new_cards.json` e `radar.py cards` lo giudica da solo con fit 1. Rubrica:
stage aperti ai laureati come un ruolo junior (bollino «Stage per neolaureati» o «Stage: verifica se accetta laureati»);
SWE nei team ML (Software Engineer ML, infrastruttura e piattaforma ML, inference, MLOps) fit 3 nelle aziende top, 4 se il
team allena o serve modelli propri, 2 altrove; programmi graduate e residency sono ruoli junior. Fonti nuove: Analog Devices,
Silicon Labs, Cadence (Workday), Synopsys (Radancy), Prosus (AI Talent Lab), Plumerai, Innatera (Ashby), Prophesee
(Teamtailor), e `apple-residency`: la ricerca Apple negli Stati Uniti tenuta solo per i titoli «Resident» (opzione
`title_only`, con `ignore_location`), per vedere quando apre la AIML Residency (la tornata 2026 chiudeva il 15/12/2025).
LinkedIn: la ricerca graduate prende anche «new college grad», traineeship e residency; due ricerche nuove, SWE nei team
ML e stage ML. Indeed: «software engineer machine learning» e «machine learning internship». L'ESA Graduate Trainee
(circa 100 posti ogni febbraio, per chi si è laureato da meno di un anno) arriva già dalla fonte `esa`. Nella pagina, Apply
today scarta ancora gli stage: si cambia da una sessione claude.ai se Gabriele li vuole lì.
- `out/new_cards.json` (lo scrive lo scanner lavori): le schede di `candidates.json` viste per la
  prima volta negli ultimi `NEW_DAYS` (21) giorni, senza doppioni e senza `baseline`, senza
  descrizione ma con `req` (700 caratteri dalla parte dei requisiti), `sig` (phd, german, french,
  visa) e `via` (la fonte); più `closed`, le chiusure degli stessi giorni. ~3,5 MB.
- `tools/radar.py` (solo libreria standard: la routine lo scarica e lo esegue) fa tutto il lavoro
  meccanico. Claude scrive solo `giudizi.txt` (una riga per scheda:
  `n|tipo|fit|prestigio|anni|motivo`) e, per le schede con fit ≥ 3, `schede.jsonl` (why, req e
  correzioni).
  - `cards` stampa le schede non ancora giudicate e non già nel Radar: prima tutte quelle delle
    ultime 36 ore, poi le più vecchie (ML e punteggio alto prima), fino a `--max 900`. Le schede
    già giudicate le ricava dai documenti `radarRuns` del Radar: ogni giro ne scrive uno con tutti
    i giudizi (`key|azienda|ruolo|tipo|fit|prestigio|anni|motivo`). Così un giro saltato o
    l'arretrato si recuperano nei giorni dopo, e i giudizi restano per la fase 5.
  - `more` stampa i requisiti delle schede con fit ≥ 3; `desc` stampa le descrizioni da
    `candidates.json`.
  - `docs` costruisce i documenti di `jobs` (fit 5–4 alto, 3 medio, 2 basso, 1 e AI-SWE
    scartati). Calcola `prio` da fit, prestigio e luogo (Zurigo, Svizzera, Italia, remoto,
    Europa) dentro le fasce di sempre. Ricava area, remote, kind, pub e pubNote. Aggiunge i campi
    `tipo`, `fitScore`, `prestigio`, `anni`, `scanKey` e `applyUrl`. Prepara i batch da 50, le
    chiusure (annunci spariti, scadenze passate) e la bozza del messaggio finale.
  - `check` (il lunedì): annunci LinkedIn aperti che non accettano più candidature.
  - `phd` e `phd-done` fanno lo stesso per la Mappa PhD, con i documenti `phdRuns`.
- Arretrato: al 16/10 ~4200 schede mai giudicate (i primi giri di LinkedIn e Indeed del 13/10 non
  andavano in `recent.json`). Le nuove sono ~2000 a notte, non ~550: vedi sopra (16/10).
- Prompt nuovi delle routine: `private/routines/radar.md` e `mappa-phd.md` (contengono il
  profilo: non vanno nella repo). `private/routines/AGGIORNA-ROUTINE.md` è il messaggio da
  incollare in una sessione claude.ai che sostituisce i prompt e sposta gli orari: Radar ogni
  giorno alle 8:00, Mappa PhD lunedì e giovedì alle 8:30 (prima erano alle 13:10 e alle 5:46).
  Dal terminale `RemoteTrigger update` esiste, ma il prompt sta dentro `job_config` (~85 KB con
  il prompt di sistema) e non è documentato se l'aggiornamento sostituisce o unisce. Il 14/10 la
  prova da Claude Code è stata bloccata: 403 sulla routine LinkedIn, legata al PC di Gabriele, e il
  classificatore di sicurezza sulla Mappa PhD. Le routine si cambiano da una chat claude.ai.
- Liste lunghe: `cards`, `more` e `phd` scrivono le voci in pagine da ~24.000 caratteri
  (`cards-1.txt`…, quanto entra in un output di uno strumento) e dicono quali leggere. Se un file di
  `out/` manca, lo script si ferma con una riga `STOP` da riportare nel messaggio finale.
- La routine Radar non legge più `recent.json` (resta per compatibilità). I controlli del lunedì su
  Amazon, Apple e Meta sono tolti; restano una ricerca per Bain, Reply, Generali, Comau e Stellantis
  e il controllo dei link LinkedIn.
- Mappa PhD: la pagina mostra ora ogni paese europeo («Altri paesi»), il tipo di dottorato
  (`phdKind`: Marie Curie, CIFRE, PhD industriale, fellowship) e «MSCA: non idoneo», con i filtri
  «Industriali e CIFRE» e «Marie Curie». La routine copre tutta l'Europa, con Svizzera, Paesi Bassi
  e Belgio davanti: prima scartava tutto il resto, cioè quasi tutti i dottorati dai portali.
- Prima settimana: misurare quanto consuma la routine Radar (~100 token per scheda in lettura).
  Se pesa troppo, abbassare `--max` o accorciare le schede.
- Decisioni di Gabriele (14/10): si resta larghi, anche i fit basso (2) entrano nel Radar, ma si
  tiene d'occhio quanto consuma. La routine "Radar LinkedIn" (browser sul suo PC, lun/mer/ven)
  è spenta: LinkedIn lo legge lo scanner ogni notte.

**Fase 1-bis fatta.**
- EURAXESS (`euraxess`) **è spenta dal 14/10** (`disabled` in `phd_sources.json`): su GitHub
  Actions ha letto 250 pagine senza rifiuti ma sempre le stesse 6 posizioni, quindi la paginazione
  va rivista prima di riaccenderla. Quando torna, il job PhD (che ora parte dopo quello lavori)
  non può più avere 240 minuti: va riletta in più notti o in un job a parte.
  Com'era fatta: il filtro «PhD positions» funziona se messo nell'URL
  (`f[0]=positions:php_positions`, ~100 pagine da 10). Il sito risponde 429 per minuti a chi va
  veloce, quindi si legge una pagina ogni 20-30 secondi (il passo rallenta da solo a ogni rifiuto),
  tutte le pagine ogni notte, con un tetto di 240 minuti. Filtro largo: `PHD_TOPIC` su titolo,
  riassunto e campo di ricerca, oppure campo Computer science, Engineering, Mathematics o Technology.
  MSCA è un'etichetta (`kind`), non un filtro. Le descrizioni complete arrivano 150 a notte; le
  posizioni si chiudono alla scadenza.
- Doctoral Networks: `tools/cordis_networks.py` estrae dal dataset CORDIS i network dei bandi 2024 e
  2025 sul tema (107) e trova sito e pagina delle posizioni per 43; più EXPLORA a mano. Sono fonti
  `watch` (`kind: msca-dn`) in `phd_sources.json`.
- Francia: ABG (`abg`, tutte le offerte, tenute le tesi su temi vicini al profilo, `cifre` se
  citato), Naver Labs Europe, valeo.ai, Criteo AI Lab, Sony CSL Parigi (pagine sorvegliate); Criteo
  anche come fonte lavori (Workday). Meta FAIR Parigi arriva dai dottorati industriali («AI Research
  Assistant (PhD)»). Huawei Parigi: nessuna pagina carriere leggibile trovata.
- Dottorati industriali: lo scanner lavori passa a `out/phd_industrial.json` gli annunci delle fonti
  aziendali con PhD, doctoral, Doktorand, Promotion, thèse o CIFRE nel titolo (fonti accademiche
  escluse: `academic: true`), e il profilo PhD li legge (`industrial`; `cifre` se in Francia o se
  citato). Escono dai file lavori senza risultare chiusi.
- Dottorati dai portali (`boards-phd`, dal 14/10): lo stesso passaggio vale per LinkedIn, Indeed e
  gli altri portali, con in più Promovendus, doktorant, PhD scholarship/studentship. Il `kind` dipende
  dal datore di lavoro: `academic` per università e istituti (`ACADEMIC_CO`), altrimenti
  `industrial`, `cifre` se citato o in Francia fuori dall'università; `via` dice il portale.
  I portali mostrano un annuncio una volta sola, quindi lo scanner lavori lo ripassa per 30 giorni
  (`BOARD_PHD_DAYS`, in `meta.board_phd` dello stato). Nessuna chiusura: dopo 30 giorni sparisce
  dalla mappa. Doppioni tra portali e con le fonti delle università tolti: stesso titolo e nome del
  datore che inizia allo stesso modo, o stessa città e una parola distintiva in comune nel nome
  ("Friedrich-Schiller-Universität Jena" e "Friedrich Schiller University Jena").
  Primo giro vero (14/10, run partito da push di giorno): 76 dottorati dai portali, 56 nuovi sulla
  mappa dopo il filtro di tema; LinkedIn di giorno ha rifiutato quasi tutto (429) dopo il giro
  della notte, la ricerca interrotta riparte per prima la notte dopo.
- Fellowship: `phd_fellowships.json` (FWO, Swiss Government Excellence Scholarships, ELLIS, COFUND
  GROW, trainLSE e Gen-Q, più ETH AI Center, CLS e RobotX); controllo settimanale in
  `out/phd_fellowships.json`. Nessun COFUND di dottorato attivo coordinato in Svizzera o in Belgio.
- Mobilità MSCA: `eligible: false` per l'Italia, nota per la Svizzera; sulle pagine dei network, che
  hanno sedi in più paesi, solo la nota.
- I Doctoral Networks senza pagina trovata (64 su 107) non si perdono: le loro posizioni devono
  essere pubblicate su EURAXESS.

**Filtri larghi anche qui (13/10).** Una località che lo script non riconosce (Gramat, Ilmenau,
Twente…) ora è `unknown` e resta, invece di contare come fuori Europa; i nomi dei paesi ci sono
anche in francese, tedesco, italiano, olandese e spagnolo, e la lista delle città extraeuropee è più
lunga. `PHD_TOPIC` copre più temi tecnici e cinque lingue, e i titoli senza tema («PhD Position»)
restano. I dottorati industriali non hanno filtro di tema. Il 14/10 `PHD_TOPIC` ha preso anche
«ML» (es. «focus on ML-methods»), modeling e i temi cardiaci; quelli dai portali passano dallo
stesso filtro di tema delle università.

**Orari.** GitHub fa partire i giri programmati con ~7 ore di ritardo (il cron delle 03:17 UTC
partiva alle 10:00-11:30 UTC, cioè dopo le 12 in Italia). Il cron ora è alle 15:00 UTC (più un
secondo tentativo alle 17:00, saltato se il giro c'è già stato): si parte verso mezzanotte e si
finisce entro la mattina. Il job PhD parte dopo quello lavori (fino a 340 minuti) e legge i
dottorati che questo ha trovato la notte stessa. Notte del 13/10: partenza alle 23:45, lavori finiti alle
3:50 (LinkedIn usa tutto il budget), PhD in pochi minuti.

**Per la sessione claude.ai della Mappa PhD:** al primo giro le posizioni già aperte di ogni fonte
nuova (EURAXESS, ABG, dottorati industriali) sono marcate `baseline`, così `phd_recent.json` non si
riempie di centinaia di voci; sono comunque in `phd_open.json` e vanno guardate una volta.

## Fase 1 — raccolta larga (zero token, GitHub Actions)
1. **JobSpy** (`pip install python-jobspy`) come nuova fonte in `scan.py`: LinkedIn (ricerca
   pubblica, senza login) e Indeed, per tutti i paesi europei che Indeed supporta.
   - Ricerche per famiglia di ruolo: research engineer, research scientist, machine learning
     engineer, deep learning, ML scientist, applied scientist, data scientist, computer vision,
     foundation model, pretraining, LLM training, edge AI, embedded machine learning, perception,
     signal processing ML, AI researcher, graduate ML/AI.
   - Annunci delle ultime 48-72 ore, con `fetch_description` sui risultati nuovi.
2. **Big tech lette direttamente dal loro sito**: sono le più importanti e vanno coperte alla fonte.
   - Netflix gira su Eightfold e Stripe su Greenhouse: bastano gli adapter esistenti in `scan.py`,
     si aggiunge solo la voce in `sources.json`.
   - Amazon (amazon.jobs) e IBM hanno API di ricerca pubbliche: career-ops ha già i provider
     (`amazon`, `ibm`), da portare in `scan.py`.
   - Apple (jobs.apple.com) e Meta (metacareers.com) usano endpoint interni con token: servono
     adapter nuovi, da verificare dal vivo. Per Meta, se l'endpoint resiste, si ripiega su LinkedIn
     filtrato per azienda (`linkedin_company_ids`), ma solo dopo aver provato.
   - Google e Microsoft sono già nella lista: controllare che coprano tutta Europa.
3. **Audit di copertura (zero token).** LinkedIn fa da revisore degli adapter diretti: se un annuncio
   rilevante di un'azienda già in `sources.json` compare su LinkedIn ma non nel suo adapter,
   l'adapter è rotto o troppo stretto e va segnalato in `out/status.json`. Lo stesso vale se una
   fonte restituisce 0 annunci o perde più di metà degli annunci rispetto al giro prima.
4. **Più aziende in `sources.json`**, con adapter diretto dove l'ATS è già supportato:
   - automotive: VW/CARIAD, Porsche, Audi, Stellantis, Toyota Motor Europe, Continental, ZF;
   - laboratori e AI europea: DeepL, Pruna AI, Aignostics, Bioptimus, Owkin, Cradle, Adyen;
   - chip edge: NXP, Infineon, Arm;
   - poi ogni azienda nuova che compare dai risultati di LinkedIn con almeno un annuncio rilevante.
5. **Aggregatori zero-token:** Hacker News "Who is hiring" (mensile), joinup.ch, The Hub,
   Landing.jobs, Welcome to the Jungle. Si possono prendere dai provider di career-ops.
6. **Deduplica:** lo stesso annuncio da LinkedIn e dal sito dell'azienda conta una volta sola
   (azienda + titolo normalizzato + città); vince il link diretto dell'azienda.

**Rischio da verificare subito:** LinkedIn può bloccare gli IP di GitHub Actions (429). Se succede:
meno ricerche e pause più lunghe, poi Indeed e Google Jobs come ripiego. In ultima istanza JobSpy
gira da una macchina di Gabriele e pusha l'output.

## Fase 1-bis — Mappa PhD: Marie Curie, CIFRE, dottorati industriali (zero token)
Obiettivo: il profilo PhD (`SCAN_PROFILE=phd`, `phd_sources.json`, `out/phd_*`) copre anche i
dottorati finanziati fuori dai portali delle università. Ogni posizione riceve un campo `kind`:
`academic`, `msca-dn`, `msca-cofund`, `cifre`, `industrial` o `fellowship`. Nessun campo esistente
viene tolto o rinominato.
1. **EURAXESS.** Si leggono tutte le posizioni PhD (filtro «PhD positions» nell'URL), tutte le
   pagine ogni notte, a passo lento perché il sito blocca chi va veloce. Restano quelle con un tema di
   `PHD_TOPIC` nel titolo, nel riassunto o nel campo di ricerca, o con campo Computer science,
   Engineering, Mathematics o Technology. MSCA non è un filtro ma un'etichetta: chi cita Marie
   Skłodowska-Curie, MSCA o COFUND (anche solo nel programma di finanziamento) prende `kind:
   msca-dn` o `msca-cofund`, le altre `academic`.
2. **Network MSCA da CORDIS.** Dal dataset aperto dei progetti Horizon Europe si estraggono i
   Doctoral Networks dei bandi 2024 e 2025 su ML, AI, segnali, sensori, visione, robotica e salute
   digitale, con sito e coordinatore; la pagina delle posizioni aperte di ciascuno diventa una fonte
   `watch` in `phd_sources.json`.
3. **CIFRE e dottorati in Francia.** Il portale ABG (abg.asso.fr) per le offerte di tesi, CIFRE
   comprese, su tutti i temi vicini al profilo (non solo ML e AI); più le pagine dei laboratori
   industriali che assumono dottorandi CIFRE: Meta FAIR Parigi, valeo.ai, Naver Labs Europe, Criteo,
   Sony CSL Parigi, Huawei Parigi.
4. **Dottorati industriali delle aziende.** Gli annunci delle fonti aziendali con PhD, doctoral,
   Doktorand, Promotion, thèse o CIFRE nel titolo non si scartano più: lo scanner lavori li passa al
   profilo PhD con `kind: industrial`.
5. **Fellowship con scadenza annuale** in `phd_fellowships.json` (nome, link, scadenza tipica,
   requisiti): FWO PhD fellowship, Swiss Government Excellence Scholarships, programmi MSCA COFUND
   di dottorato in Svizzera, Paesi Bassi, Belgio e Germania, ELLIS PhD. Una volta a settimana lo
   scanner controlla che le pagine rispondano e riporta le date trovate in
   `out/phd_fellowships.json`.
6. **Regola di mobilità MSCA.** Per le posizioni `msca-*`: `eligible: false` se il paese è l'Italia;
   per la Svizzera vale solo se l'assunzione avviene entro 12 mesi dall'arrivo (marzo 2027).

La pagina Mappa PhD e la sua routine le aggiorna dopo una sessione claude.ai, per mostrare i nuovi
`kind` e le fellowship.

## Fase 2 — solo esclusioni sicure (zero token)
In `scan.py` si sostituisce la lista obbligatoria `INCLUDE` sui titoli con sole esclusioni:
- funzioni non tecniche: vendite, marketing, HR, legale, finanza, supporto clienti;
- seniority impossibili: senior, staff, principal, lead, manager, director, head;
- stage, tesi, working student, apprendistato, postdoc e professore;
- fuori Europa.

Tutto il resto passa. Per ogni annuncio si salvano titolo, azienda, luogo, data, link, fonte e
descrizione (troncata a ~5.000 caratteri). Si aggiunge anche un punteggio indicativo, che serve
solo per ordinare e non taglia nulla:
- **più punti:** training, pretraining, self-supervised, PyTorch, training distribuito,
  pubblicazioni, biosegnali, wearable, ultrasuoni, edge, quantizzazione;
- **meno punti:** LangChain, RAG, prompt, «5+ anni».

Output nuovo: `out/candidates.json`, cioè gli annunci aperti con descrizione e punteggio. I file
esistenti restano compatibili.

## Fase 3 — Claude giudica tutto quello che passa
**Primo passaggio, su ogni annuncio nuovo.** Claude legge l'annuncio con la descrizione e
restituisce in JSON:
- `tipo`: ricerca / training / edge / data science / AI-SWE / altro;
- `fit` da 1 a 5 col CV;
- `prestigio` da 1 a 5;
- `anni_richiesti`;
- un motivo di una riga.

Scarta solo AI-SWE, ruoli fuori profilo e ruoli troppo senior; tutto il resto va nel Radar,
ordinato per prestigio × fit.

**Deciso: si fa nella routine Radar** (abbonamento, nessun costo extra). Per stare nei limiti:
- lo script prepara per ogni annuncio una scheda compatta in `out/candidates.json`: titolo,
  azienda, luogo, anni richiesti estratti, ~400 caratteri sul lavoro da fare, parole chiave
  trovate;
- la routine legge tutte le schede, senza tagli, e dà il giudizio;
- la descrizione completa la apre solo per gli annunci con fit ≥ 3.

Prima settimana: misurare quanto consuma la routine. Se pesa troppo, si accorciano le schede.
Il ripiego è GitHub Actions con una chiave API (Haiku 5.5, circa 2 $ al mese).

## Fase 4 — career-ops sul terminale
**Avviata il 16/10.** Node installato con Homebrew, `npm install` (con Chromium per i PDF) fatto. File
utente compilati (tutti ignorati da git): `cv.md` dal .tex ufficiale, `config/profile.yml`,
`modes/_profile.md` (archetipi ML, framing, regole delle lettere ricavate dalle lettere già scritte e
dai feedback, che prevalgono sul formato a elenchi di `modes/cover.md`), `modes/_custom.md` (il
tracker resta il Radar: niente `data/applications.md`; mai inviare nulla), `modes/_brief.md`,
`article-digest.md` (fatti oltre il CV, ognuno con la fonte). `portals.yml` è il modello: la ricerca
la fa lo scanner di job-radar. Primo uso: valutazione, lettera e risposte del modulo per una
candidatura. Manca un compilatore LaTeX locale per i CV su misura (`tectonic` da Homebrew, se serve).
- Clonare in `career-ops/`, `npm install`, poi `cv.md` dal CV vero e profilo da `private/NOTES.md`.
- Archetipi riscritti per i ruoli sopra al posto di LLMOps, PM e FDE.
- `portals.yml` con le aziende di `sources.json` e i provider europei.
- Modello di CV: quello di Gabriele, non il loro.
- Uso: valutazione A–F, CV e lettera su misura, ricerca contatti, preparazione colloqui, solo
  sugli annunci scelti.
- Il tracker ufficiale resta il Radar, per non avere due stati diversi della stessa candidatura.

## Fase 5 — miglioramento continuo
- Ogni annuncio trovato a mano e assente dal radar si annota in `private/missed.md`, e si capisce
  il perché: fonte mancante, ricerca o esclusione sbagliata.
- Una volta a settimana si confrontano gli scartati e gli inviati del Radar con i giudizi di Claude
  e si corregge il prompt di valutazione.
- Le aziende nuove che compaiono spesso nei risultati LinkedIn entrano in `sources.json`.
