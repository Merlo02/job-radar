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

**Fase 3 fatta (14/10), manca solo l'aggiornamento delle due routine.**
- `out/new_cards.json` (lo scrive lo scanner lavori): le schede di `candidates.json` viste per la
  prima volta negli ultimi `NEW_DAYS` (10) giorni, senza doppioni e senza `baseline`, senza
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
- Arretrato: al 14/10 ~3300 schede mai giudicate (i primi giri di LinkedIn e Indeed del 13/10 non
  andavano in `recent.json`). Con 900 schede al giorno e ~550 nuove a notte si smaltisce in circa
  una settimana, dentro i 10 giorni del file.
- Prompt nuovi delle routine: `private/routines/radar.md` e `mappa-phd.md` (contengono il
  profilo: non vanno nella repo). `private/routines/AGGIORNA-ROUTINE.md` è il messaggio da
  incollare in una sessione claude.ai che sostituisce i prompt e sposta gli orari: Radar ogni
  giorno alle 8:00, Mappa PhD lunedì e giovedì alle 8:30 (prima erano alle 13:10 e alle 5:46).
  Dal terminale `RemoteTrigger update` esiste, ma il prompt sta dentro `job_config` (~85 KB con
  il prompt di sistema) e non è documentato se l'aggiornamento sostituisce o unisce: meglio non
  rischiare di rompere le routine.
- La routine Radar non legge più `recent.json` (resta per compatibilità). I controlli del lunedì su
  Amazon, Apple e Meta sono tolti; restano una ricerca per Bain, Reply, Generali, Comau e Stellantis
  e il controllo dei link LinkedIn.
- Mappa PhD: la pagina mostra ora ogni paese europeo («Altri paesi»), il tipo di dottorato
  (`phdKind`: Marie Curie, CIFRE, PhD industriale, fellowship) e «MSCA: non idoneo», con i filtri
  «Industriali e CIFRE» e «Marie Curie». La routine copre tutta l'Europa, con Svizzera, Paesi Bassi
  e Belgio davanti: prima scartava tutto il resto, cioè quasi tutti i dottorati dai portali.
- Prima settimana: misurare quanto consuma la routine Radar (~100 token per scheda in lettura).
  Se pesa troppo, abbassare `--max` o accorciare le schede.

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
