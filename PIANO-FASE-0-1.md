# Piano di sviluppo — Fase 0 e Fase 1

> Operativo. Le motivazioni architetturali stanno in `PROGETTO-RAG-Aziendale.md`.
> Stima: 1-2 sviluppatori + 1 referente dati. **Fase 0: 3-4 settimane. Fase 1: 6-8 settimane.**

## Struttura del repository

L'implementazione vive tutta in `Sviluppo/`. Alla radice restano solo i documenti e le regole di repository.

```
RAG Aziendale/
├── avvia.cmd                      doppio clic: alza tutto e apre il browser
├── ferma.cmd                      ferma i servizi, i dati restano
├── PROGETTO-RAG-Aziendale.md      analisi, decisioni, rischi
├── PIANO-FASE-0-1.md              questo documento
├── .gitattributes                 eol=lf — governa tutto l'albero
└── Sviluppo/
    ├── docker-compose.yml         stack applicativo
    ├── docker-compose.override.yml  differenze di sviluppo (Windows)
    ├── docker-compose.inference.yml host GPU — forma di produzione, fase 1b
    ├── .env.example               → copiare in .env
    ├── Caddyfile                  reverse proxy applicativo
    ├── librechat.yaml             custom endpoint verso l'orchestratore
    ├── litellm-config.yaml        routing dei modelli — fase 1b
    ├── avvia.py                   porta su l'ambiente in ordine di dipendenza
    ├── migrate.py                 runner migrazioni, zero dipendenze
    ├── initdb/00-bootstrap.sql    CREATE DATABASE — solo al primo avvio
    ├── migrations/001_*.sql       schema — applicato da migrate.py
    ├── keycloak/realm-azienda.json.tmpl  template del realm (reso da avvia.py)
    ├── keycloak/README.md         note sul realm: JSON non ammette commenti
    ├── inference/Caddyfile        TLS + token davanti all'inferenza
    ├── eval/smoke_embeddings.py   T0.1 — embedding su LM Studio
    ├── eval/verifica_token.py     T1.1 / T1.3 — forma dei token
    ├── orchestratore/
    │   ├── egress.py              guardia di egress: unico client HTTP
    │   ├── identita.py            verifica JWT, nessun percorso alternativo
    │   ├── recupero.py            ricerca ibrida con filtro ACL nella query
    │   ├── gate.py                high-water-mark e contaminazione
    │   └── test_gate.py           12 test: T1.6-T1.14. L artefatto di conformita
    └── ingestion/                 ← da scrivere
```

**Tutti i comandi `docker compose` si lanciano da `Sviluppo/`.** I percorsi dentro i compose sono relativi al file, quindi restano validi.

**Obiettivo della fase 1:** un collega apre il browser, entra con le credenziali aziendali, fa una domanda sui manuali e ottiene una risposta **corretta, citata, e filtrata sul suo livello di accesso**. Niente altro.

---

# FASE 0 — Discovery (3-4 settimane)

Quasi tutta lavoro con l'azienda, non codice. È la fase che la gente salta e che poi fa fallire il progetto.

⚠️ **Non è sequenziale rispetto alla fase 1.** I passi 1-2 della fase 1 (infrastruttura, SSO) non dipendono da nessun deliverable qui e partono **subito, in parallelo**. Altrimenti lo sviluppatore resta senza lavoro e costruisce su requisiti immaginati.

⚠️ **Ogni deliverable ha un nome e una data, o non esiste.** "3-4 settimane" vale solo se qualcuno con autorità guida questa fase.

| # | Deliverable | Cosa è | Perché |
|---|---|---|---|
| 0.1 | **Inventario sorgenti candidate** | Le 3 sorgenti della fase 1 — non tutte — con volumi, formati, **proprietario** e **qual è la versione autoritativa** | Se nessuno sa qual è la versione buona, quella sorgente **non entra**. Tre versioni di un listino indicizzate = citazione sicura e sbagliata, che è peggio di nessuna risposta |
| 0.2 | **Matrice ACL delle 3 sorgenti** | Ruoli × sorgenti → chi può vedere cosa | Tre righe, non quaranta. L'inventario completo è un problema della fase 2 |
| 0.3 | **Approvazioni di residenza** | Default **`interno`**; servono 2-3 sorgenti approvate `cloud_ok`, **con il nome di chi firma** | Approvare 3 sorgenti innocue lo fa chiunque in dieci minuti; approvarne 40 è un progetto. Così la firma *sblocca copertura* invece di essere un permesso che l'IT rincorre |
| 0.4 | **20 domande reali** | Raccolte da **ticket helpdesk, mail all'ufficio tecnico, canali interni** — non scritte da zero. Per il retrieval basta *"quale documento deve stare nei primi 5"* | Le persone non sanno scrivere domande di eval in astratto: scrivono banalità o domande impossibili. Le domande vere esistono già scritte. Le altre 80 le genera il pilota, gratis e reali per costruzione |
| 0.5 | **Campione documenti** | 30-50 documenti veri non sensibili — **i peggiori che ci sono, non i più belli**. Ripartizione e regole di raccolta in `ESEMPI-FILE-DOCUMENTI.md` §2 | I PDF finti non hanno tabelle ruotate né scansioni storte. Se il campione è pulito, il test mente |
| 0.6 | **Valutazione retrieval** | Script ~100 righe sulla macchina di sviluppo | ⭐ **La mossa a maggior rendimento del progetto.** Decide embedding e chunking prima di cementarli, e converte l'incognita più grande in un numero in due giorni |

## 0.6 — Il primo codice: valutazione del retrieval

Gira sulla macchina di sviluppo (GPU 16GB), non richiede infrastruttura.

| Installazione | Cosa fa | Perché |
|---|---|---|
| **Python 3.12 + uv** | Ambiente | `uv` risolve le dipendenze in secondi invece di minuti |
| **Docling** | PDF → testo strutturato con tabelle e pagine | La qualità del parsing decide la qualità delle risposte. Da provare sui documenti peggiori che hai |
| **MinerU 2.5** e **GLM-OCR** — già su LM Studio | Estrazione documenti alternativa, con OCR | ⭐ Trovati nella lista modelli della macchina di sviluppo. **Vale un confronto con Docling sui documenti scansionati**, che è il punto dove Docling è più debole. Zero costo: sono già lì |
| **LM Studio** | Serve `bge-m3` su `/v1/embeddings` | Nativo su Windows, fuori da Docker. **Nessun rerank**: vedi la sostituzione sotto |
| **Postgres + pgvector** (locale o container) | Indice di prova | Stesso motore della produzione: i numeri sono trasferibili |

**I quattro test, ognuno decide qualcosa:**

| Test | Misura | Cosa decide |
|---|---|---|
| **T0.1 Recall embedding** | recall@10 su 30 domande, bge-m3 vs embedding cloud di riferimento | Se bge-m3 sta entro pochi punti → **la sovranità sull'embedding è gratis**. Attenzione ai codici articolo e part number, dove il vettoriale sbaglia |
| **T0.2 Fusione ibrida** | recall@5: solo vettoriale, solo BM25, **fusione RRF**, e — se l'endpoint esiste — **+ cross-encoder** | ⭐ Quattro colonne, una tabella. RRF costa zero servizi ed è il metro su cui giudicare il reranker: senza la base gratuita non sai se il cross-encoder guadagna qualcosa. Se il vettoriale perde sui codici articolo e RRF recupera, il reranker può aspettare la fase 1b |
| **T0.3 Dimensione chunk** | recall a 512 / 1024 / 2048 token | bge-m3 arriva a 8192: per i manuali tecnici i chunk grandi spesso vincono. Non dare per scontato il contrario |
| **T0.4 Throughput** | chunk/secondo in batch | Dice se indicizzare il corpus è questione di minuti o di giorni, e quanto costa il reindexing futuro |

⚠️ **Lock-in:** bge-m3 produce **1024 dimensioni**. Cambiare modello di embedding dopo = ricalcolare tutto il corpus. Questo test non è esplorativo.

#### Verificato su LM Studio — 17/09/2026

Eseguito con `Sviluppo/eval/smoke_embeddings.py` (solo stdlib: `py Sviluppo/eval/smoke_embeddings.py` dalla radice).

**1. Il reranker su LM Studio non è utilizzabile. Misurato, non supposto.**

Nessun endpoint di rerank: `/v1/rerank`, `/rerank`, `/v1/reranking` rispondono tutti `Unexpected endpoint or method`. E LM Studio lo elenca come **`text-embedding-bge-reranker-v2-m3`**: lo tratta come modello di embedding.

**Conferma indipendente:** [RAGFlow issue #8116](https://github.com/infiniflow/ragflow/issues/8116) — un progetto RAG maturo ha la stessa astrazione per provider e per LM Studio non l'ha implementata: `The LmStudioRerank has not been implement`. Issue **chiusa senza soluzione**, perché non c'è niente contro cui implementare. Il loro modello era `text-embedding-qwen.qwen3-reranker-0.6b`: LM Studio prefissa `text-embedding-` a tutti i reranker, sistematicamente.

Da notare: **la documentazione di RAGFlow indicava LM Studio come provider di rerank.** Chi ci ha creduto ha perso tempo.

Servito così, produce **spazzatura silenziosa** — è il modo di fallire previsto, e si è verificato:

| Modello | Caso "garanzia" | Caso "ART-4471" | Margine sul 2° |
|---|---|---|---|
| **bge-m3** (1024 dim) | ✅ corretto | ✅ corretto | **+0,250 / +0,231** |
| mxbai-embed-large (1024) | ✅ corretto | ✅ corretto | +0,146 / +0,159 |
| nomic-embed-text (768) | ✅ corretto | ✅ corretto | +0,144 / +0,159 |
| **bge-reranker-v2-m3** | ❌ **sbagliato** | ❌ **sbagliato** | **−0,087 / −0,051** |

Il reranker mette al primo posto il documento **non pertinente**, con tutte le similarità schiacciate fra 0,85 e 0,97 — la firma tipica di una testa di classificazione sottoposta a pooling sbagliato: tutto assomiglia a tutto. **Restituisce numeri plausibili e ordina a caso.** Se qualcuno lo avesse collegato senza questa verifica, il retrieval sarebbe peggiorato senza un solo errore nei log.

**2. bge-m3 confermato: 1024 dimensioni**, combacia con `vector(1024)` in `initdb`. Ed è il migliore dei tre: separazione ~60% più larga, **anche sul caso codice articolo** (+0,231), che era il dubbio principale.

**3. Id esatto del modello:** `text-embedding-bge-m3-embeddings` (non il nome HuggingFace). Già in `.env.example`.

⚠️ **Questo non è T0.1.** Sono 2 domande e 6 documenti scritti a tavolino: dimostrano che la tubatura funziona, che le dimensioni combaciano e che il reranker è inutilizzabile. **Non dicono nulla sul recall del corpus vero**, dove i documenti sono quasi-duplicati e il gergo è il vostro.

#### Conseguenza: RRF è la base della fase 1

| Opzione | Ruolo |
|---|---|
| **Fusione RRF di vettoriale + BM25** | ✅ **Base della fase 1.** Zero servizi in più, la ricerca era già ibrida |
| Cross-encoder via LM Studio | ❌ **Escluso: verificato inutilizzabile** |
| LLM-as-reranker via `/v1/chat/completions` | ~1-2s contro 50ms di un cross-encoder. Ripiego, non soluzione |
| `llama-server --reranking` nativo | Un .exe, niente Docker, rerank vero. **La via d'uscita se T0.2 dice che RRF non basta** |

Il reranker serviva a compensare un generatore interno più debole (§11.5 del documento di analisi), e **quel generatore arriva in fase 1b** — insieme all'host GPU che fa rerank come si deve. In fase 1, con Opus 5 a generare, RRF basta quasi certamente.

*ponytail: misurare la base gratuita prima di adottare il servizio in più. Qui la misura ha anche evitato di collegare un componente rotto.*

## Uscita dalla fase 0

- [ ] 3 sorgenti con proprietario e versione autoritativa identificati
- [ ] Matrice ACL delle 3 sorgenti + 2-3 approvazioni `cloud_ok` firmate
- [ ] 20 domande reali con il documento che le soddisfa
- [ ] Embedding, reranker e dimensione chunk **scelti sui numeri**
- [ ] Sapere quali corpus **vanno ripuliti prima** di essere indicizzati — è lavoro manuale, va messo a budget
- [ ] Deciso quale ERP e quanti utenti (sblocca fase 2 e dimensionamento)

---

# FASE 1 — MVP documentale (6-8 settimane)

## Installazioni, in ordine di dipendenza

| # | Componente | Cosa fa | Perché questo |
|---|---|---|---|
| 1 | **Sviluppo:** Docker Desktop su Windows (macchina fisica, GPU 16GB). **Produzione:** VM Linux (4 vCPU / 16 GB) | Host di tutto | Docker Desktop su una macchina *fisica* va benissimo: il problema della nested virtualization riguardava solo il server virtuale. Stesso `docker-compose.yml`, le differenze in `docker-compose.override.yml` |
| 2 | **Postgres 17 + pgvector** | Archivio documenti, registro sorgenti, taint, tracce, + DB di Keycloak | Un'istanza, due database: Keycloak non merita un container di DB suo |
| 3 | **Keycloak 26** | Identità, gruppi, livelli di accesso | Owner unico dei ruoli. Nessun altro componente ha utenti propri |
| 4 | **Caddy** | Unico punto esposto, TLS | 8 righe di config. Il resto vive su rete privata |
| 5 | **MongoDB 7** | Utenti e storico chat di LibreChat | Requisito di LibreChat, non una scelta |
| 6 | **LibreChat** | Lo sportello: chat, SSO, storico, upload, mobile | MIT, RBAC, OIDC. Non lo modifichiamo: è una scatola chiusa |
| 7 | **LM Studio** sulla macchina di sviluppo, **nativo, fuori da Docker** | Embedding dentro il perimetro | Endpoint OpenAI-compatible su `:1234/v1`. ⚠️ **Non ha rerank** (non esiste nell'API OpenAI) e **non autentica**. Dall'app è un endpoint HTTP come un altro: la topologia è `EMBEDDING_URL`, non codice — quindi il passaggio all'host GPU di produzione (`docker-compose.inference.yml`) è un cambio di variabile |
| 8 | **LiteLLM** — attivo dalla fase 1 | Routing dei modelli per nome logico | **Unico componente con le chiavi dei provider.** Il modello principale non ha default: la rotta `ragionamento` si compila in `.env` e la scelta si prende con l'eval. L'orchestratore chiama `ragionamento`/`veloce` e non sa quale modello risponda |
| 9 | **`ingestion/`** (Python + Docling) | Documenti → chunk etichettati → pgvector | On demand, non un servizio. Il file-watcher quando servirà |
| 10 | **`orchestratore/`** (Python + LangGraph) | Endpoint OpenAI-compatible, JWT, gate, grafo | Il codice nostro: ~400 righe |

**Solo l'orchestratore possiede `ANTHROPIC_API_KEY`.** Nessun altro container.

## Sequenza di lavoro

### Passo 1 — Infrastruttura e identità (settimana 1)

#### 1.0 Macchina di sviluppo — verificato il 17/09/2026

| # | Verifica | Esito | Nota |
|---|---|---|---|
| a | **Line ending** | ✅ | `.gitattributes` con `eol=lf` alla radice. Un CRLF in `Sviluppo/initdb/01-databases.sql` farebbe fallire Postgres in modi incomprensibili: **è l'inciampo classico** |
| b | **Porte 80 e 443 libere** | ✅ | Nessuna in ascolto. Caddy può prenderle |
| c | **Memoria WSL2** | ✅ | 10,4 GB e 6 CPU assegnati al VM Docker: sufficienti |
| d | **GPU nei container** | ✅ | **NVIDIA RTX 5060 Ti, 16 GB** — architettura Blackwell, generazione attuale: FP8, FlashAttention, CUDA piena. Nessuno dei limiti Volta discussi al §11.8 |
| e | **Docker** | ✅ | 29.5.2, Compose v5.1.4, contesto `desktop-linux` |
| f | **Porta 5432** | ⚠️ **occupata** | `postgres.exe` come servizio Windows. L'override pubblica su **5433** lato host; dentro la rete del compose resta `postgres:5432`. Per un client SQL dall'host: `localhost:5433` |
| g | **LM Studio dai container** | ✅ **senza "Serve on Local Network"** | Sorpresa utile: Docker Desktop instrada `host.docker.internal` anche verso i servizi in loopback. Verificato HTTP 200 da dentro un container con LM Studio su `127.0.0.1:1234`. ⚠️ **È un comportamento di Docker Desktop, non di Docker su Linux** — irrilevante in produzione, dove l'host di inferenza è una macchina remota |
| h | **Nome del modello** | ✅ | `text-embedding-bge-m3-embeddings`, verificato su `GET /v1/models` |
| i | ~~Firewall sulla 1234~~ | — | **Non serve più:** la porta resta su loopback, quindi non è esposta alla LAN. Tenere spento "Serve on Local Network" è la scelta più sicura |
| j | **Avvio automatico LM Studio** | ⬜ da fare | `lms server start` all'accesso, modello precaricato. È la postazione di qualcuno: si riavvia e va in sospensione |
| k | **Hostname** | ⬜ da fare | `.env` usa `assistente.localhost` / `sso.localhost`: i browser li risolvono a 127.0.0.1 senza toccare il file hosts. `tls internal` darà un avviso: accettarlo o importare la root CA di Caddy |
| l | **Spazio disco** | ⚠️ 64 GB liberi su 468 | Gli image della fase 1 sono ~4 GB, quindi basta. Ma Docker ha **78 GB recuperabili**: `docker system prune -a --volumes` se serve spazio (attenzione: cancella anche volumi non usati di altri progetti) |

#### Due trappole incontrate, e come sono chiuse

**1. Keycloak 26 non sostituisce `$(env:VAR)` nei file di import.** I valori finiscono nel realm come **stringhe letterali**, e il sintomo è `Invalid user credentials` — che non dice nulla. Per questo il realm è un **template** (`realm-azienda.json.tmpl`) reso da `avvia.py` prima dell'avvio: dimenticare quel passo costa mezza giornata, quindi non è un passo di README, è codice. *Il template ha estensione `.json.tmpl` perché Keycloak importa ogni `*.json` nella cartella montata.*

**2. Keycloak rifiuta le chiavi JSON non riconosciute.** I campi `_commento` che avevo messo per documentare il realm facevano fallire l'import con `Unrecognized field`. JSON non ammette commenti: le spiegazioni stanno in `keycloak/README.md`.

*Corollario su Git Bash: `docker compose exec` con percorsi assoluti richiede `MSYS_NO_PATHCONV=1`, altrimenti `/opt/keycloak/...` viene tradotto in un percorso Windows.*

⚠️ Le redirect URI nel client Keycloak devono combaciare con `APP_HOST`. In produzione cambiano: è l'unica cosa da rifare al passaggio sulla VM.

#### 1.1 Attività

| Attività | Uscita |
|---|---|
| Da `Sviluppo/`: `cp .env.example .env`, generare i segreti, poi **`py avvia.py`** | ✅ **fatto.** Rende il realm, alza Postgres, migra, alza Keycloak — in quest'ordine, idempotente |
| Schema DB con `py migrate.py`: `sources`, `chunks`, `conversation_taint`, `index_meta`, `traces`, `pending_actions` | ✅ **fatto e verificato** |
| Realm Keycloak **importato da file**, non cliccato: gruppi, client `librechat` + `orchestratore`, mapper `groups`, **audience mapper** | ✅ **scritto** — da verificare al primo avvio |

| Test | Perché |
|---|---|
| **T1.1** ✅ Token emesso per ogni utente di prova, un gruppo per profilo | **Verificato** con `py eval/verifica_token.py` |
| **T1.2** ✅ Utente disattivato in Keycloak → `invalid_grant: Account disabled`; riattivato → token riemesso | **Verificato.** L'identità ha un solo proprietario, e non serve toccare nient'altro |
| **T1.3** ✅ Il token contiene `groups` e `aud` include `orchestratore` | **Verificato.** `groups` esatti per tutti tre gli utenti, `aud=['orchestratore']`, durata 900s. Senza audience mapper la verifica a valle fallirebbe. *Niente `access_level`: i permessi sono un'intersezione di gruppi, non una gerarchia numerica* |
| **T1.3e** ✅ `INSERT` di una sorgente `cloud_ok` **senza firma** → rifiutato dal database | **Verificato.** Il vincolo `cloud_ok_richiede_firma` rende la policy inaggirabile: non è una convenzione, è un CHECK |
| **T1.3f** ✅ `py migrate.py` due volte → la seconda non fa nulla; modificare una migrazione già applicata → errore | **Verificato.** Una migrazione modificata a posteriori farebbe divergere i database in silenzio |
| **T1.3b** `GET /v1/models` su LM Studio raggiungibile **da dentro un container** | Se "Serve on Local Network" è spento, tutto il resto sembra rotto per ragioni misteriose |
| **T1.3c** ⭐ LM Studio spento o modello scaricato → il sistema risponde **degradato con BM25**, non va in errore | **In sviluppo questo non è resilienza, è necessità:** è la postazione di qualcuno, si riavvia e va in sospensione. La ricerca ibrida rende il fallback gratuito |
| **T1.3d** Ingestion interrotta a metà e rilanciata → riprende senza duplicare | Corollario di T1.3c: se la macchina si sospende durante l'indicizzazione, l'ingestion deve essere idempotente |

### Passo 2 — Sportello e SSO end-to-end (settimana 2)

| Attività | Uscita |
|---|---|
| Mongo + LibreChat, `OPENID_REUSE_TOKENS=true`, scope **senza** `offline_access` (da Keycloak 26.1 rompe l'SSO) | Login aziendale dentro LibreChat |
| `librechat.yaml`: custom endpoint verso l'orchestratore, header `Authorization: Bearer {{LIBRECHAT_OPENID_ACCESS_TOKEN}}` | L'identità viaggia |
| Orchestratore: scheletro che risponde `/v1/chat/completions` in eco, con verifica JWT contro il JWKS | Primo giro completo |
| **Self-check all'avvio**: se manca l'header di autenticazione in configurazione, il servizio **non parte** | Fallimento rumoroso invece che silenzioso |
| **Versione di LibreChat pinnata** a un tag rilasciato | Ogni aggiornamento rilancia T1.5/T1.6 prima della promozione |

| Test | Perché |
|---|---|
| **T1.4** SSO: browser → Keycloak → LibreChat, un solo login | ⭐ **Primo milestone mostrabile** |
| **T1.5** L'orchestratore riceve il token e ne estrae `groups` | È il perno dell'architettura: se fallisce, cambia il frontend |
| **T1.6** ✅ Firma alterata, `aud` sbagliata, token assente → rifiutato | Tre test separati. Un JWT non verificato non è sicurezza |
| **T1.7** `ANTHROPIC_API_KEY` presente **solo** nell'ambiente dell'orchestratore | Test automatizzabile: ispeziona l'env dei container. Un componente con la chiave scavalca il gate |
| **T1.7b** ✅ Header vuoto, solo `Bearer`, `Basic` → rifiutato. Nessun utente di default | ⚠️ Vedi il pericolo sotto: è il fallimento più probabile e il più silenzioso |

⚠️ **Il fallimento silenzioso dell'identità.** La documentazione di LibreChat è esplicita: **i placeholder non risolti diventano stringhe vuote**, non testo letterale. Una configurazione sbagliata o un aggiornamento non danno un errore: danno una richiesta con header vuoto. Con una verifica JWT sciatta il sistema **fallisce aperto** e nessuno se ne accorge. Regola: **nessun percorso alternativo senza token valido.**

### Passo 3 — Il gate, prima di tutto il resto (settimana 3)

Si scrive **prima** del retrieval e della risposta. È l'unico punto dove un bug è una fuga di dati.

| Attività | Uscita |
|---|---|
| Registro `sources` popolato dalle matrici della fase 0, **default `interno`** | Solo le 2-3 sorgenti approvate sono `cloud_ok`; tutto il resto è interno e **non ingerito** |
| Nodo gate: calcolo del taint high-water-mark sui chunk e sui risultati dei tool | Fail closed |
| `conversation_taint`: una volta contaminata, la conversazione resta interna | Lo storico torna al modello a ogni turno |
| Ramo `qa_interno` nel grafo, oggi contenente solo il rifiuto | La topologia è definitiva, il corpo dei nodi no |

| Test | Perché |
|---|---|
| **T1.8** ✅ Per ogni sorgente `interno`: query che la recupererebbe → **assert a livello di trasporto** che nessuna richiesta sia uscita verso un host di `EGRESS_EXTERNAL` | ⭐ **L'artefatto di compliance.** Passa oggi perché rifiuta, passerà identico quando instraderà sull'host interno. Assert sul trasporto, non applicativo: quello lo aggira il percorso che ti sei dimenticato |
| **T1.8b** ✅ Richiesta verso un host **non dichiarato** in nessuna delle due allowlist → errore, **anche su turno pulito** | "Solo localhost" non vale più: l'host di inferenza è remoto. Così una dipendenza aggiunta distrattamente non diventa un canale di uscita silenzioso |
| **T1.9** ✅ Una conversazione contaminata resta interna anche su turni successivi puliti | La contaminazione è della conversazione |
| **T1.10** ✅ Rotta interna non configurata + taint → rifiuto esplicito, **non** risposta parziale | Fail closed. Uno stub permissivo è peggio di niente |

### Passo 4 — Ingestion (settimana 3-4)

| Attività | Uscita |
|---|---|
| Docling → chunk (dimensione scelta in T0.3) → embedding via Infinity → upsert | Corpus non sensibile indicizzato |
| Ogni chunk porta `source_id`, `acl_groups`, `residency`, `page` | Retro-etichettare dopo = ri-ingestion completa |
| Indici: GIN su `acl_groups`, HNSW sull'embedding, GIN full-text italiano | Ibrido: i part number il vettoriale li sbaglia |

| Test | Perché |
|---|---|
| **T1.11** Nessun chunk senza `source_id`, `acl_groups`, `residency` | Vincolo NOT NULL + test. Un chunk senza etichetta è un buco |
| **T1.12** Nessun chunk `interno` presente nell'indice in fase 1 | Ciò che non è indicizzato non può trafilare per un bug |
| **T1.13** Reindicizzare due volte non duplica | L'ingestion verrà rilanciata decine di volte |
| **T1.22** ⭐ Due chunk da **documenti diversi** con similarità >0.95 → **flag per revisione umana**, non silenzio | Tre versioni dello stesso listino indicizzate = il sistema cita con sicurezza quella sbagliata. Una risposta errata *con citazione* è credibile, quindi peggio di nessuna risposta |
| **T1.23** ⭐ Caricare in LM Studio una **quantizzazione diversa** dello stesso modello → l'orchestratore **non parte** | LM Studio non dichiara la revisione: confrontare il nome non basta. Il **canary** in `index_meta` — l'embedding di una frase fissa, ri-calcolato all'avvio e confrontato — becca qualsiasi cambio, anche silenzioso. Altrimenti gli embedding si spostano e **il recall cala senza alcun errore** |

### Passo 5 — Il grafo e le risposte (settimana 4-6)

| Attività | Uscita |
|---|---|
| Grafo LangGraph: `retrieval → gate → [ramo] → risposta` | Stateless, senza checkpointer |
| Ricerca ibrida **con filtro ACL nella query**, mai dopo | Pre-filter, non post-filter |
| Reranker sui primi 30 → primi 5 al modello | Il guadagno misurato in T0.2 |
| Chiamata al modello via proxy, per **nome logico** (`ragionamento`), schema OpenAI-compatible, streaming SSE | Il nodo non sa quale modello né quale provider. Il modello si cambia in `.env` + `litellm-config.yaml`, senza deploy |
| Citazioni obbligatorie: documento + pagina | Una risposta senza fonte non è verificabile |

| Test | Perché |
|---|---|
| **T1.14** ✅ ⭐ Magazzino e vendite fanno la **stessa domanda**: il magazzino non vede il listino riservato, vendite sì. Assert sulla **query SQL**, non sulla risposta del modello | Il test più importante del sistema. Il prompt non è un controllo di sicurezza |
| **T1.15** Domanda senza chunk pertinenti → *"non trovo"*, non un'invenzione | L'allucinazione su un manuale tecnico costa credibilità immediata |
| **T1.16** Ogni affermazione della risposta ha una citazione risolvibile a documento+pagina | Citazione inventata = peggio di nessuna citazione |
| **T1.17** Streaming: i token arrivano a LibreChat progressivamente | Senza, l'utente pensa sia bloccato |
| **T1.18** Token in ingresso e in uscita registrati per ogni risposta, con una **baseline** salvata | Sostituisce il test sul caching di Anthropic, che lo schema neutro non espone. Senza baseline non si nota un raddoppio di costo dopo un cambio di modello o di prompt |
| **T1.24** ⭐ Cambiare `MODELLO_RAGIONAMENTO` in `.env`, riavviare **solo** `litellm` → il sistema risponde con il modello nuovo, **zero modifiche al codice** | È il test del requisito "il modello lo scelgo io" (decisione 38). Se per cambiare modello serve toccare l'orchestratore, la sostituibilità è dichiarata e non reale |
| **T1.25** Il tool calling del modello scelto regge **10 chiamate consecutive** con argomenti valorizzati correttamente | Criterio 1 della scelta del modello. I provider non sono equivalenti nel rispettare lo schema dei tool, e un argomento sbagliato su una chiamata ACL-sensibile non può passare |

### Passo 6 — Misura e pilota (settimana 6-8)

| Attività | Uscita |
|---|---|
| Tracce in `traces`: prompt, chunk recuperati, latenza, token, taint, **se il retrieval ha trovato qualcosa, se l'utente ha riformulato, pollice su/giù** | Tracciare ≠ Langfuse. Ma senza questi tre campi un pilota fallito dà un fatto binario e nessuna azione |
| **Revisione esterna** di una giornata su gate e filtro ACL | Il codice di sicurezza non può restare letto da una sola persona. L'assicurazione più economica del progetto |
| Harness di eval sulle 100 domande d'oro, eseguibile con un comando | Da qui in poi ogni modifica si misura |
| Retention dello storico chat (es. 90 giorni) + cancellazione al cambio ruolo | Le vecchie chat contengono dati che l'utente oggi non potrebbe più vedere |
| Backup Postgres e Mongo | Dati aziendali a tutti gli effetti |
| **Pilota con 10-12 colleghi** scelti fra chi cerca documenti **per lavoro** — ufficio tecnico, inside sales, assistenza. **Non i manager** | Con 5-8, se due vanno in ferie non c'è segnale. I manager non fanno ricerca documentale |

**Se il pilota va male, la diagnosi viene dalle tracce, non dalle opinioni:**

| Sintomo | Causa | Cosa si aggiusta |
|---|---|---|
| Niente recuperato | buco nel corpus | ingestione di altre sorgenti |
| Recuperato ma sbagliato | chunking o ranking | di nuovo T0.2 e T0.3 |
| Recuperato giusto, risposta brutta | prompt o modello | il pezzo più facile |

| Test | Perché |
|---|---|
| **T1.19** L'eval gira con un comando e produce un numero confrontabile | Senza, non sai se una modifica migliora o peggiora |
| **T1.20** Restore da backup su ambiente pulito | Un backup non verificato non è un backup |
| **T1.21** 10 utenti concorrenti: latenza prima risposta < 3s | Sopra, la gente smette di usarlo |

## Uscita dalla fase 1

- [ ] Login aziendale unico, nessuna password nuova
- [ ] Risposte citate su 2-3 corpus a diverso livello di accesso
- [ ] **T1.8 e T1.14 verdi** — gate e ACL provati, non sperati
- [ ] Eval eseguibile con un comando, numero di riferimento registrato
- [ ] 10-12 colleghi la usano **spontaneamente** per due settimane
- [ ] Gate e filtro ACL passati da una **revisione esterna**

Il penultimo punto è il vero criterio. Se non si verifica, il problema è la qualità dei documenti o del parsing, non il modello — e le fasi successive vanno rimandate, non accelerate. Le tracce dicono quale dei tre.

---

## Stime oneste

| | |
|---|---|
| Fasi 0+1 con **1-2 sviluppatori dedicati** | 9-12 settimane |
| Fasi 0+1 con **1 sviluppatore part-time** | 4-5 mesi di calendario |
| Costo API in sviluppo | qualche centinaio di € |
| **Tempo di persone non-sviluppatrici** | **2-3 settimane** — referente dati, colleghi per le domande, la firma sulla residenza |

⚠️ L'ultima riga è quella che sfugge ai piani. Se non è formalmente allocata, arriva dalla buona volontà e arriva tardi. **È il vero costo del progetto.**

Probabilità (§14.9 del documento di analisi): consegna tecnica della fase 1 ~85%, **adozione ~50-60%** — e dipende quasi solo dalla qualità dei documenti, che oggi nessuno conosce. Ecco perché 0.6 va fatto per primo.

---

## Fuori scope in fase 1 — da rifiutare esplicitamente

| Cosa | Quando |
|---|---|
| Dati strutturati, fatturato, anagrafiche | Fase 2 |
| Preventivi e generazione documenti | Fase 3 |
| Agenda, mail, azioni con conferma | Fase 4 |
| Voce | Fase 5 |
| Modello locale e corpus sensibile | Fase 1b, in parallelo all'acquisto GPU |
| Langfuse, checkpointer LangGraph, `interrupt()` | Fase 2-3 |
| Acquisto GPU | **Dopo** l'eval, mai prima |

*Ogni voce spostata in fase 1 ritarda il primo utilizzo reale, che è l'unica cosa che dice se il progetto ha senso.*
