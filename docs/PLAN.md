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
2. **Big tech con portali difficili** (Apple, Meta, Amazon, Netflix, IBM, Microsoft, Stripe…):
   ricerca LinkedIn per azienda (`linkedin_company_ids` di JobSpy), invece di un adapter ciascuna.
3. **Più aziende in `sources.json`**, con adapter diretto dove l'ATS è già supportato:
   - automotive: VW/CARIAD, Porsche, Audi, Stellantis, Toyota Motor Europe, Continental, ZF;
   - laboratori e AI europea: DeepL, Pruna AI, Aignostics, Bioptimus, Owkin, Cradle, Adyen;
   - chip edge: NXP, Infineon, Arm;
   - poi ogni azienda nuova che compare dai risultati di LinkedIn con almeno un annuncio rilevante.
4. **Aggregatori zero-token:** Hacker News "Who is hiring" (mensile), joinup.ch, The Hub,
   Landing.jobs, Welcome to the Jungle. Si possono prendere dai provider di career-ops.
5. **Deduplica:** lo stesso annuncio da LinkedIn e dal sito dell'azienda conta una volta sola
   (azienda + titolo normalizzato + città); vince il link diretto dell'azienda.

**Rischio da verificare subito:** LinkedIn può bloccare gli IP di GitHub Actions (429). Se succede:
meno ricerche e pause più lunghe, poi Indeed e Google Jobs come ripiego. In ultima istanza JobSpy
gira da una macchina di Gabriele e pusha l'output.

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

Due modi per farlo (**da decidere**):
- **(A) GitHub Actions + API Anthropic.** Serve una chiave API con credito prepagato, salvata come
  secret della repo. Non tocca i limiti dell'abbonamento.
  - Stima per ~300 annunci nuovi al giorno a ~1.500 token l'uno: con Claude Haiku 5.5 circa
    2 $/mese, con Sonnet 5.5 circa 36 $/mese. La Batch API dimezza i costi.
  - Il primo giro sull'arretrato (qualche migliaio di annunci) costa poco con Haiku.
- **(B) Nella routine Radar.** Usa l'abbonamento, ma 300 descrizioni al giorno pesano molto sui
  limiti settimanali.

**Consigliato: (A) a due stadi.** Un modello veloce legge tutto; un modello più forte, o la
routine Radar, rilegge solo gli annunci con fit ≥ 3 per il giudizio finale. Il modello lo sceglie
Gabriele.

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
