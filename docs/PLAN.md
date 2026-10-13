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
1. **EURAXESS.** Il sito ignora i filtri: si leggono gli annunci pagina per pagina (in ordine di
   data; al primo giro tutti, poi solo le pagine nuove) e si filtra nello script: restano quelli che
   citano Marie Skłodowska-Curie, MSCA, Doctoral Network o «doctoral candidate» e passano
   `PHD_TOPIC`.
2. **Network MSCA da CORDIS.** Dal dataset aperto dei progetti Horizon Europe si estraggono i
   Doctoral Networks dei bandi 2024 e 2025 su ML, AI, segnali, sensori, visione, robotica e salute
   digitale, con sito e coordinatore; la pagina delle posizioni aperte di ciascuno diventa una fonte
   `watch` in `phd_sources.json`.
3. **CIFRE e dottorati in Francia.** Il portale ABG (abg.asso.fr) per le offerte di tesi, CIFRE
   comprese, filtrate su ML e AI; più le pagine dei laboratori industriali che assumono dottorandi
   CIFRE: Meta FAIR Parigi, valeo.ai, Naver Labs Europe, Criteo, Sony CSL Parigi, Huawei Parigi.
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
