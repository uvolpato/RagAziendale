# Handoff per agente — Fasi 0 e 1

> Documento operativo autosufficiente. Chi lo esegue **non ha il contesto
> della conversazione** che ha prodotto il progetto: tutto ciò che serve è qui
> o nei file citati. In caso di conflitto fra questo documento e il codice,
> **vale il codice**: segnalare la discrepanza, non "correggere" il codice
> per farlo aderire al documento.
>
> Stato fotografato il 18/09/2026.

---

## 1. Missione

Portare la **fase 1** a completamento: un utente entra con SSO aziendale, fa
una domanda sui documenti e riceve una risposta **corretta, citata e filtrata
sui propri permessi**. Niente altro.

L'infrastruttura, l'identità e il livello di sicurezza sono **già fatti e
verificati**. Mancano: il server HTTP dell'orchestratore, l'ingestion dei
documenti e la valutazione del retrieval.

### Confini — cosa NON fare

- **Non scegliere il modello LLM.** È una decisione del committente (§7).
- **Non ingerire documenti marcati `interno`.** In fase 1 il corpus sensibile
  resta fuori dall'indice per costruzione.
- **Non indebolire gate, filtro ACL, verifica JWT o allowlist di egress** per
  far passare un test o una funzionalità. Se un requisito sembra richiederlo,
  fermarsi e chiedere.
- **Non aggiungere dipendenze o servizi** non elencati in §5 senza motivarlo.
- **Non modificare** `docker-compose.inference.yml` (è la forma di produzione
  della fase 1b) né `Prototipi/`.
- **Non toccare `Sviluppo/.env` né i file resi** (`librechat.yaml`,
  `keycloak/realm-azienda.json`): si modificano i `.tmpl` e `.env.example`.

---

## 2. Documenti di riferimento

| File | Quando leggerlo |
|---|---|
| `PROGETTO-RAG-Aziendale.md` | Il *perché* di ogni scelta. §11 sovranità, §12 registro delle 45 decisioni, §14 rischi |
| `PIANO-FASE-0-1.md` | Il piano umano originale, con i test T0.x/T1.x numerati |
| `Sviluppo/README.md` | Avvio, verifiche, personalizzazione, **tabella delle trappole** |
| `Sviluppo/keycloak/README.md` | Realm, client, utenti di prova |
| `Sviluppo/branding/tema-keycloak/README.md` | Tema della pagina di login |
| `Sviluppo/CREDENZIALI-SVILUPPO.md` | Credenziali di sviluppo (generato, in `.gitignore`) |

Le decisioni sono numerate nel §12 dell'analisi: citarle per numero nei
commenti e nei messaggi, es. *"decisione 22: solo il proxy ha le chiavi"*.

---

## 3. Stato attuale — verificato

### Ambiente

Windows 11, Docker Desktop (WSL2), GPU RTX 5060 Ti 16 GB, Python 3.14 via
`py`, `uv`. LM Studio nativo su `localhost:1234` con `bge-m3` caricato.

**Avvio:** `avvia.cmd` nella radice, oppure `cd Sviluppo && py avvia.py`.
Idempotente. Avvia **solo** postgres, keycloak, caddy, mongo, librechat.

### Cosa funziona

| Area | Stato | Prova |
|---|---|---|
| Schema DB + migrazioni | ✅ | `py migrate.py` |
| Keycloak: realm, gruppi, client, utenti prova, i18n it/en/de | ✅ | `py eval/verifica_token.py` |
| SSO LibreChat ↔ Keycloak, logout che chiude il SSO, nessuna pagina intermedia | ✅ | `eval/verifica_login.py` — 13/13 |
| Pagina di login con tema aziendale | ✅ | `eval/verifica_pagina_login.py` — 12/12 |
| Gate, ACL, egress, contaminazione | ✅ | `-m orchestratore.test_gate` — 12/12 |
| Embedding locale bge-m3, 1024 dimensioni | ✅ | `py eval/smoke_embeddings.py` |

Comandi dalla cartella `Sviluppo/`, con il venv:
`./.venv/Scripts/python.exe <script>`. Se il venv manca:
`uv venv .venv && uv pip install --python .venv psycopg[binary] "pyjwt[crypto]" httpx`

### Cosa NON funziona ancora — ed è atteso

| | Motivo |
|---|---|
| **La chat apre ma i messaggi danno errore** | Manca il server HTTP dell'orchestratore (task A1) |
| `docker compose up` senza nomi di servizio **fallisce** | `orchestratore/` non ha `Dockerfile`; `ingestion/` non esiste. `avvia.py` li salta di proposito |
| `litellm` non parte | `MODELLO_RAGIONAMENTO` è vuoto in `.env` (decisione del committente) |

---

## 4. Invarianti — non violabili

Queste sono le proprietà che rendono il sistema sicuro. Ogni modifica deve
lasciarle vere, e i test esistenti le verificano.

1. **Il filtro ACL sta nella query SQL**, mai dopo il recupero. Passa da
   `sources.acl_groups && gruppi_del_token`. Le ACL non si duplicano sui
   chunk. → `recupero.py`, test T1.14.
2. **Nessun percorso permissivo sull'identità.** Header assente, vuoto o
   malformato → rifiuto. Mai un utente di default. LibreChat sostituisce i
   placeholder non risolti con **stringhe vuote**: un ramo permissivo
   farebbe fallire aperto in silenzio. → `identita.py`, T1.7b.
3. **`iss` si valida sull'hostname esterno** (`https://sso.localhost/...`),
   **le chiavi si scaricano da quello interno** (`http://keycloak:8080/...`).
   Due variabili distinte.
4. **Un solo client HTTP in uscita**: `egress.client()`. Host non dichiarato
   in `EGRESS_INTERNAL`/`EGRESS_EXTERNAL` → errore anche su turno pulito.
   Non creare altri client `httpx`/`requests`. → `egress.py`, T1.8b.
5. **High-water-mark sulla conversazione.** Un chunk `interno` contamina
   tutta la conversazione, per sempre. Senza rotta interna configurata:
   **rifiuto esplicito**, mai risposta parziale. → `gate.py`, T1.9/T1.10.
6. **I numeri non li produce il modello.** Cifre solo da query deterministiche.
7. **Solo il proxy LiteLLM possiede le chiavi dei provider** (decisione 22/40).
   Nessuna chiave di modello in LibreChat né nell'orchestratore.
8. **Il modello si chiama per nome logico** (`ragionamento`, `veloce`,
   `embedding`) via client OpenAI-compatibile. Nessun SDK di provider nel
   codice (decisione 39).
9. **`sources.residency = 'cloud_ok'` richiede firma**: vincolo CHECK nel DB.
   Non aggirarlo con INSERT diretti in test di produzione.

---

## 5. Mappa del codice

```
Sviluppo/
├── avvia.py                 avvio in ordine; rende i template; scrive le credenziali
├── migrate.py               migrazioni SQL numerate, zero dipendenze
├── migrations/001_*.sql     sources, chunks, conversation_taint, index_meta, traces, pending_actions
├── docker-compose.yml       stack; override.yml = differenze di sviluppo Windows
├── librechat.yaml.tmpl      custom endpoint -> orchestratore, header Authorization con il token OIDC
├── litellm-config.yaml      rotte logiche; `ragionamento` vuota per scelta
├── keycloak/*.tmpl          realm reso da avvia.py
├── branding/                logo.png e favicon.ico, montati su chat E login
├── orchestratore/
│   ├── egress.py            unico client HTTP + allowlist + contextvar di contaminazione
│   ├── identita.py          verifica JWT (PyJWT + JWKS), gruppi, autocontrollo all'avvio
│   ├── recupero.py          ricerca ibrida RRF con filtro ACL; degrado su solo BM25
│   ├── gate.py              high-water-mark, contaminazione, rifiuto fail-closed
│   └── test_gate.py         12 test = artefatto di conformità
└── eval/                    verifiche eseguibili (token, login, pagina, embedding)
```

**Stack Python dell'orchestratore:** FastAPI, uvicorn, psycopg 3, PyJWT,
httpx, LangGraph (vedi `orchestratore/requirements.txt`). **Nessun ORM**
(decisione 42): SQL a mano con psycopg.

---

## 6. Backlog — in ordine di esecuzione

Ogni task ha criteri di accettazione **eseguibili**. Un task è finito quando
i criteri passano, i test esistenti restano verdi, e il README è aggiornato.

### A1 — Server HTTP dell'orchestratore ⭐ sblocca la chat

**Crea:** `orchestratore/app.py`, `orchestratore/Dockerfile`, aggiunta a
`avvia.py` del passo di avvio.

**Contratto:** endpoint OpenAI-compatibile, perché LibreChat lo consuma come
un modello (`librechat.yaml.tmpl`).

- `POST /v1/chat/completions` con `stream: true` → **SSE** in formato
  `chat.completion.chunk`, chiuso da `data: [DONE]`. Supportare anche
  `stream: false`.
- `GET /v1/models` → un modello `assistente-v1`.
- `GET /health`.
- Identità da `Authorization: Bearer <token>`, conversazione da
  `X-Conversation-Id` (entrambi inviati da LibreChat).

**Flusso per richiesta:**
```
identita.verifica -> gruppi
recupero.embedding(domanda)          # None se LM Studio non risponde
recupero.cerca(conn, domanda, gruppi, qvec)
gate.applica(conn, conversation_id, righe)   # RispostaRifiutata -> testo di rifiuto
risposta
traces: una riga per turno
```

**Modalità senza modello** (obbligatoria, perché `MODELLO_RAGIONAMENTO` è
vuoto): se la rotta non è configurata, la risposta è l'elenco dei passaggi
recuperati con **documento e pagina**, chiaramente etichettata come
"passaggi pertinenti, nessuna sintesi". Quando il modello c'è, stesso flusso
con la generazione via `LITELLM_BASE_URL` per nome logico.

**All'avvio** (`identita.autocontrollo` + nuovo controllo):
- configurazione incompleta o JWKS irraggiungibile → il servizio **non parte**;
- se `index_meta` ha una riga, ri-embeddare `canary_frase` e confrontare
  `canary_hash`: se diverso → **non parte** (il modello di embedding è
  cambiato e il recall calerebbe in silenzio).

**Criteri di accettazione:**
- [ ] `py avvia.py` avvia anche l'orchestratore; `GET /health` → 200
- [ ] **Modalità senza modello (criterio esplicito, default oggi)**: con
      `MODELLO_RAGIONAMENTO` vuoto, da LibreChat la chat streamma i passaggi
      recuperati etichettati "passaggi pertinenti, nessuna sintesi"
      (documento + pagina), con più chunk SSE — mai un errore "modello non
      configurato"
- [ ] L'orchestratore è esposto **solo sulla rete interna del compose** e ha
      un healthcheck Docker; `GET /health` → 200
- [ ] Errori HTTP espliciti: 401 al token assente/scaduto (ma nessun
      retry-loop da LibreChat), 400 → testo di rifiuto (T1.10)
- [ ] Da LibreChat, loggati come `prova.vendite`, una domanda produce una risposta in streaming
- [ ] T1.5 — l'orchestratore riceve i gruppi dal token inoltrato da LibreChat
- [ ] T1.15 — domanda senza chunk pertinenti → "non trovo", mai un'invenzione
- [ ] T1.16 — ogni passaggio citato è risolvibile a documento + pagina esistenti
- [ ] T1.17 — lo streaming arriva progressivamente (più chunk SSE, non uno solo)
- [ ] T1.3c — LM Studio spento → risposta degradata su BM25 con avviso, non errore
- [ ] T1.23 — alterare `index_meta.canary_hash` → l'orchestratore non parte
- [ ] T1.10 — sorgente `interno` recuperata senza rotta interna → testo di rifiuto
- [ ] `test_gate.py` resta 12/12, `verifica_login.py` 13/13

**Dove sbagliare è facile:** il token arriva da `LIBRECHAT_OPENID_ACCESS_TOKEN`
e scade in 15 minuti — un 401 dopo inattività è corretto, non un bug.

### A2 — Ingestion

**Crea:** `ingestion/` con `Dockerfile` e script; usa il servizio `ingestion`
già dichiarato nel compose (profilo `tools`, on demand:
`docker compose run --rm ingestion`).

**Flusso:** Docling (parsing con tabelle e pagine) → chunk → embedding via
`EMBEDDING_URL`/`EMBEDDING_MODEL` (LM Studio, endpoint OpenAI `/embeddings`)
→ upsert in `chunks` con `source_id`, `documento`, `page`, `content_hash`.

**Regole:**
- Ingerire **solo** sorgenti presenti in `sources` con `residency = 'cloud_ok'`.
  Una sorgente assente o `interno` → saltata con messaggio.
- Idempotente: `UNIQUE (source_id, documento, page, content_hash)` + `ON CONFLICT DO NOTHING`.
- Al primo indice, scrivere `index_meta`: modello, dimensioni (1024),
  `canary_hash` = sha256 dell'embedding di `canary_frase` arrotondato.
- Quasi-duplicati: due chunk di **documenti diversi** con similarità > 0,95 →
  registrarli per revisione umana (tabella o file di report), non scartarli.

**Criteri:**
- [ ] T1.11 — nessun chunk senza `source_id`, `documento`, `content_hash`
- [ ] T1.12 — nessun chunk di sorgente `interno` nell'indice
- [ ] T1.13 — reingestione due volte → stesso numero di righe
- [ ] T1.3d — interrompere a metà e rilanciare → completa senza duplicati
- [ ] T1.22 — due documenti quasi identici → segnalati nel report
- [ ] `index_meta` popolata e coerente con il modello in uso

**Nota:** `bge-m3` accetta fino a 8192 token; la dimensione del chunk va
decisa col task A3, non a priori. Usare un parametro, default 1024.

### A3 — Valutazione del retrieval (deliverable 0.6)

**Crea:** `eval/valuta_retrieval.py`.

**Input** (forniti dal committente, vedi §7): cartella di 30–50 documenti
veri e file di 20 domande nel formato:
```
domanda;documento_atteso;pagina_attesa
```
Modello di partenza e domande di collaudo: `eval/DOMANDE-CRITICHE.md` (§8
per il formato CSV; le categorie di sicurezza DC-07…DC-24 vanno tradotte in
test automatici, non nel CSV).

**Misura e stampa in tabella:**
- T0.1 recall@10 del solo vettoriale
- T0.2 recall@5: solo vettoriale / solo BM25 / fusione RRF
- T0.3 recall a chunk 512 / 1024 / 2048
- T0.4 throughput di ingestion in chunk/secondo

**Criteri:**
- [ ] Un comando produce la tabella completa su un indice di prova separato
      (non sporcare l'indice principale)
- [ ] Funziona anche con LM Studio spento, riportando solo le colonne BM25
- [ ] Il reranker **non** va usato via LM Studio: è verificato inutilizzabile
      (non espone `/rerank` e servito come embedding ordina a caso)

**Bloccato su input umano.** Se gli input mancano: consegnare lo script con un
mini-corpus sintetico di esempio e fermarsi.

### A4 — Correzioni minori note

| # | Problema | Correzione |
|---|---|---|
| A4.1 | `avvia.py` — la funzione `dc()` cattura l'output e, se `docker compose` fallisce, mostra solo il traceback: **l'errore vero di Docker va perso** | stampare `stderr` del processo prima di sollevare |
| A4.2 | `branding/tema-keycloak/README.md` cita ancora `branding/logo.svg` e `favicon.png`, oggi `logo.png` e `favicon.ico` | **RISOLTO 18/09** — `Sviluppo/README.md` e `tema-keycloak/README.md` allineati a `logo.png`/`favicon.ico`; eval P6 ora valida `img/logo.png` come PNG (il compose era già corretto) |
| A4.3 | LibreChat usa l'immagine `:latest` | **RISOLTO 18/09** — pinnato `ghcr.io/danny-avila/librechat:v0.8.7` (ultima stable al 18/09/2026; `v0.8.8-rc*` sono pre-release). Il pin va fatto **prima** di verificare A1 (contratto `X-Conversation-Id`): rilanciare T1.4 e quegli A1-criteri a valle del pin |
| A4.4 | Traces: registrare anche `retrieval_vuoto`, `riformulazione`, `feedback` (colonne già in schema) | necessari alla diagnosi del pilota |

### A5 — Pilota (passo 6 del piano)

Solo dopo A1–A3. Retention dello storico chat, backup e restore di Postgres
e Mongo, test di carico con 10 utenti concorrenti (primo token < 3 s).
Dettagli in `PIANO-FASE-0-1.md`, passo 6.

---

## 7. Decisioni che l'agente NON prende

Fermarsi e chiedere quando uno di questi blocca il lavoro:

| Decisione | Blocca | Chi |
|---|---|---|
| Quale modello LLM (`MODELLO_RAGIONAMENTO`) | la generazione in A1, `litellm` | committente — criteri in analisi §11.9 |
| Documenti di prova e 20 domande | A3 | committente |
| Quali sorgenti sono `cloud_ok` e **chi firma** | A2 su dati reali | referente compliance |
| Quale ERP | fase 2 | committente |
| Se il percorso locale ristretto è accettato | fase 1b | committente |

---

## 8. Trappole già incontrate

Leggere **prima** di diagnosticare un comportamento strano. La tabella
completa è in `Sviluppo/README.md`; queste sono le più probabili:

| Sintomo | Causa |
|---|---|
| `Unknown authentication strategy "openid"` dopo un riavvio | CA di Caddy non ancora visibile a LibreChat → `py avvia.py` riavvia e ricontrolla. **Non usare `docker compose up -d librechat` da solo** |
| Modifiche a `Caddyfile` senza effetto | Caddy non ricarica da solo → `docker compose restart caddy` |
| `autenticazione con password fallita` con la password giusta | Due Postgres nativi su 5432 e 5433 → porta **55432** |
| `getaddrinfo failed` su `*.localhost` da Python | Mappare `*.localhost` a 127.0.0.1 nel test (vedi `eval/verifica_login.py`) |
| Test HTTP verde ma ciclo di redirect nel browser | **httpx ignora `SameSite`**: un test lato server non vede le policy del browser |
| `Too many login attempts` | Rate limiter di LibreChat; in sviluppo già rilassato |
| Mount `read-only file system` all'avvio | Un bind mount annidato su un file che non esiste nella cartella montata: creare il punto di innesto |
| `docker compose exec ... /opt/...` fallisce da Git Bash | Serve `MSYS_NO_PATHCONV=1` |
| Una modifica al realm template non si applica | `--import-realm` non tocca un realm esistente: `kcadm` o `down -v` |
| Stringa nei `.properties` ignorata | Apostrofo non raddoppiato |
| File servito con 200 ma non renderizzato | Verificare che sia valido (es. SVG con `--` in un commento XML) |

**Principio operativo** (analisi §14.10): ogni capacità di un componente
terzo su cui si appoggia una decisione va **provata con una chiamata** prima
di costruirci sopra. Le tabelle di compatibilità nelle documentazioni sono
aspirazionali.

---

## 9. Convenzioni

- **Lingua:** codice, commenti, messaggi e documenti in **italiano**.
- **Commenti:** spiegano il *perché* e il modo di fallire evitato, non il
  *cosa*. Le semplificazioni deliberate portano il prefisso `ponytail:` con
  il limite noto e quando rivederle.
- **Minimalismo:** stdlib prima delle dipendenze, una funzione prima di una
  classe, nessuna astrazione con una sola implementazione.
- **Test:** file eseguibili con `assert`, nessun framework. Stile di
  `test_gate.py`: decoratore `prova`, rollback dopo ogni test, output
  `PASS/FAIL` con descrizione. Ogni logica non banale lascia un test.
- **Test di sicurezza a livello di trasporto e di SQL**, mai solo sulla
  risposta del modello.
- **Segreti:** mai nel codice né nei template; solo `.env`. I template usano
  `${VAR}` e vengono resi da `avvia.py`.
- **Windows:** percorsi con spazi (`C:\Progetti\RAG Aziendale`) sempre tra
  virgolette; line ending LF garantiti da `.gitattributes`.
- **Documentazione:** ogni task aggiorna `Sviluppo/README.md` e, se cambia
  una scelta, il registro delle decisioni in `PROGETTO-RAG-Aziendale.md` §12
  (nuova riga numerata, mai riscrivere quelle esistenti: marcare la
  revisione).

---

## 10. Definizione di "fatto" per la fase 1

- [ ] Login SSO, nessuna password locale
- [ ] Risposte citate su 2–3 corpus a diverso livello di accesso
- [ ] T1.8 e T1.14 verdi — gate e ACL provati, non supposti
- [ ] Eval del retrieval eseguibile con un comando, numero di riferimento registrato
- [ ] Tutte le suite in §3 verdi
- [ ] README aggiornato, trappole nuove aggiunte alla tabella

Il criterio vero (adozione di 10–12 colleghi per due settimane) è del
committente, non dell'agente.

---

## 11. Come riportare

A fine di ogni task, un messaggio con:

1. **Cosa è stato fatto**, file creati e modificati.
2. **Criteri di accettazione**: quali passano, con l'output del comando.
3. **Cosa non è stato fatto e perché** — esplicito, mai omesso.
4. **Discrepanze** trovate fra documenti e codice.
5. **Trappole nuove** incontrate, da aggiungere al README.

Non dichiarare un test verde senza averlo eseguito. Un risultato plausibile
non verificato è peggio di uno dichiarato mancante.
