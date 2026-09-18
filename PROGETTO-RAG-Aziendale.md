# RAG / LLM Wiki Aziendale — Progetto sintetico e valutazione di fattibilità

> Documento di lavoro — v1.0 — 17/09/2026

**Obiettivo.** Un assistente aziendale multiutente, accessibile via chat (e in prospettiva via voce), capace di rispondere su documenti, dati anagrafici, dati analitici e listini, di generare documenti (preventivi, offerte, lettere) e di interagire con agenda e sistemi aziendali — con segmentazione rigorosa per livello di accesso.

---

## 1. Fattibilità — onesta, per capability

| Cosa | Fattibilità | La verità |
|---|---|---|
| RAG su manuali, cataloghi, procedure, contratti | **Alta (95%)** | Tecnologia matura e noiosa. Funziona. |
| Segmentazione per livello di accesso | **Alta tecnicamente / Media organizzativamente** | Il codice è 2 settimane. Definire *chi può vedere cosa* in azienda sono 2 mesi di riunioni. È qui che muoiono questi progetti. |
| Lookup anagrafici ("dammi il contatto di Rossi SpA") | **Alta** | Non è RAG, è una chiamata API. Facile e affidabile. |
| Analytics ("fatturato Lombardia Q2 vs Q1, escluso intercompany") | **Media** | Solo con metriche pre-definite in un semantic layer. **Text2SQL libero sullo schema dell'ERP: no.** 60–80% di accuratezza su schemi reali — e un numero sbagliato sul fatturato brucia la fiducia in modo irreversibile. |
| Preventivi | **Media-Alta** | Se il motore prezzi resta l'ERP (sconti, listini, scaglioni) e l'LLM solo raccoglie requisiti e compila: funziona. Se l'LLM calcola i prezzi: disastro legale. |
| Generazione documenti (offerte, lettere, schede) | **Alta** | Template + dati. Non far "scrivere il PDF" all'LLM. |
| Agenda — lettura e proposta slot | **Alta** | |
| Agenda — scrittura, invio inviti, mail | **Media** | Fattibile, ma richiede conferma umana su ogni azione esterna. |
| Assistente **vocale** realtime in italiano | **Media** | Tecnicamente risolto (LiveKit + Whisper + TTS). Ma: latenza, rumore d'ufficio, e il 90% degli utenti alla scrivania preferisce scrivere. Valore reale solo mani-occupate: magazzino, auto, cantiere, service. **Fase 5, non fase 1.** |
| "Agente personale autonomo" | **Bassa come autonomia / Alta come copilota** | Agenti che agiscono da soli sui sistemi aziendali non sono affidabili quanto il marketing dice. Copilota che propone e l'umano conferma: sì, subito. |

**Il vero collo di bottiglia non è l'AI.** È che i dati aziendali sono sparsi, sporchi e senza owner. Realisticamente **70% dell'effort è plumbing dati e ACL**, 20% integrazione, 10% "AI".

**Rischio sottovalutato.** Un catalogo PDF di un fornitore terzo può contenere testo iniettato che istruisce l'agente. Se l'agente ha accesso all'ERP e alla mail, quello è un vettore d'attacco reale. Regola: **i documenti sono dati, non istruzioni**, e nessuna azione con effetti esterni senza conferma umana.

---

## 2. Architettura — 4 livelli

```
+-- INTERFACCIA ----------------------------------------------+
|  Chat web/mobile   +   (fase 5) voce realtime               |
+------------------------------+------------------------------+
+-- ORCHESTRATORE -------------v------------------------------+
|  LLM + tool calling. Decide: cerco un documento?            |
|  chiamo una API? genero un file? Nessuna logica di          |
|  business qui. Log di ogni chiamata.                        |
+------+-------------+-------------+-------------+------------+
       |             |             |             |
+------v-----+ +-----v------+ +----v-----+ +-----v------+
| RETRIEVAL  | |   DATI     | |  AZIONI  | | DOCUMENTI  |
| documenti  | | STRUTTURATI| |  agenda  | | da template|
| vector+BM25| | semantic   | |  mail    | |            |
|            | | layer + API| |  ERP     | |            |
+------+-----+ +-----+------+ +----+-----+ +------------+
+------v-------------v-------------v--------------------------+
|  IDENTITA e ACL — Keycloak/OIDC. Ogni query porta           |
|  identita. Filtro PRIMA del retrieval, mai dopo.            |
+-------------------------------------------------------------+
```

**Le due regole non negoziabili**

1. **ACL pre-filter.** I permessi sono metadata sui chunk nel vector DB e Row-Level Security in Postgres. Il filtro si applica *nella query di ricerca*, non dopo. Mai "l'LLM sa che non deve dirlo" — il prompt non è un controllo di sicurezza.
2. **I numeri non li produce l'LLM.** L'LLM sceglie *quale* metrica interrogare; il valore arriva dal database. Se un numero non passa da una query deterministica, non esce.

---

## 3. Stack open source

| Ruolo | Scelta | Perché / attenzione |
|---|---|---|
| **Frontend chat multiutente** | **LibreChat** (MIT) o **Open WebUI** | Open WebUI ha cambiato licenza nel 2025: BSD-3 con clausola di branding — sopra i 50 utenti non si può rimuovere il branding senza licenza commerciale. **Da verificare con il legal prima di adottarlo.** LibreChat è MIT pulito, ha RBAC, SSO e MCP. |
| **Identità / SSO / ruoli** | **Keycloak** (Apache 2) | Probabilmente già presente, oppure Entra ID. Single source of truth dei permessi. Non reinventare. |
| **Orchestrazione agentica** | **LangGraph** (Python), client **OpenAI-compatible** verso il proxy | È il "gestore di agenti" dello stack. Grafo esplicito, nodi testabili, `interrupt()` per le conferme, replay per il debug. **Nessun SDK di provider nel codice**: il nodo chiama un nome logico e non sa quale modello ci sia dietro. Checkpointer Postgres dalla fase 3. Vedi §10. |
| **Modello di ragionamento** | **da scegliere** — nessun default | Scelta di progetto, non vincolo dell'architettura: si prende con i numeri dell'eval. Criteri e costo della neutralità al §11.9 |
| **Routing dei modelli** | **LiteLLM** come proxy, attivo dalla fase 1 | Owner unico del routing e **unico possessore delle chiavi dei provider**. Cambiare modello = una riga di config, senza deploy. Vedi §11.9. |
| **Embedding + reranker** | **bge-m3** + **bge-reranker-v2-m3** su **Infinity** (MIT) | Locali per obbligo, non per scelta: in ingestion passa ogni chunk di ogni documento. Un container per entrambi i ruoli — Ollama non fa reranking. 1024 dimensioni, come `initdb`. Vedi §11.7. |
| **Parsing documenti** | **Docling** (MIT, IBM) | Oggi il migliore su PDF complessi, tabelle, cataloghi, scansioni. Qui si vince o si perde il progetto: chunk sbagliati = risposte sbagliate. |
| **Pipeline RAG** | **RAGFlow** (Apache 2) *headless*, oppure **LlamaIndex** / **Haystack** come libreria | RAGFlow se si vuole ingestion + citazioni già assemblati; libreria se serve controllo. |
| **Ricerca ibrida + ACL** | **Qdrant** (Apache 2) o **pgvector** | pgvector se c'è già Postgres e < ~2–3M chunk: un sistema in meno. Qdrant se serve scalare o filtri payload complessi. Serve **ibrido** (vettoriale + BM25): codici articolo e part number il vettoriale li sbaglia. |
| **Dati strutturati** | **Cube** (Apache 2) o **WrenAI** (AGPL) | Semantic layer: "fatturato", "cliente attivo", "margine" definiti **una volta**; l'LLM interroga solo quelli. È ciò che rende affidabile la parte analytics. |
| **Azioni / integrazioni** | **MCP servers** + **n8n** o **Windmill** (AGPL) | n8n è "sustainable use license", non OSS puro: ok internamente, da verificare. |
| **Generazione documenti** | **docxtpl** + **Gotenberg** | Template Word/ODT mantenuti dal marketing, conversione PDF. Zero LLM nel rendering. |
| **Osservabilità + eval** | **Langfuse** (MIT core) + **Ragas** | Non opzionale. Senza tracce e un set di ~100 domande d'oro non si sa se una modifica migliora o peggiora. |
| **Modelli** | API (Claude) + **vLLM** on-prem per il sensibile | Full on-prem raddoppia il costo e dimezza la qualità. Ibrido: cloud per il ragionamento, locale (Qwen3 / Mistral) per embedding e dati che non possono uscire. |
| **Voce (fase 5)** | **LiveKit Agents** + **faster-whisper** + **Piper/Kokoro** | LiveKit gestisce VAD, barge-in, latenza — la parte difficile. Whisper large-v3 sull'italiano è buono. |

---

## 4. Come far interagire i sistemi — il modello di integrazione

Il rischio non è usare molti progetti open source: è usarli **come applicazioni complete**, ognuna con la propria UI, il proprio database utenti e la propria idea di permessi. Cinque login diversi e cinque copie divergenti delle ACL. La soluzione non è "usarne meno", è **dare a ogni concern un solo owner** e far comunicare tutto su due soli contratti.

### 4.1 Un owner per concern

| Concern | Owner unico | Nessun altro lo fa |
|---|---|---|
| Identità, ruoli, livelli di accesso | Keycloak | LibreChat, n8n, RAGFlow **non** hanno utenti propri |
| Conversazione e UI | LibreChat | RAGFlow e n8n girano headless, UI disattivata |
| Decisione "quale strumento uso" | Orchestratore | Non n8n, non il prompt |
| Ricerca documentale | Retrieval service | |
| Metriche di business | Cube | Nessuno scrive SQL a mano altrove |
| Azioni esterne | MCP servers | |
| Tracce e valutazione | Langfuse | |

**Criterio di selezione di ogni progetto open source:** funziona *headless*, parla *OIDC*, accetta permessi *dall'esterno*. Molti progetti RAG non superano questo test — ed è il vero filtro con cui scegliere.

### 4.2 Contratto 1 — l'identità viaggia, non si duplica

```
Utente --> Keycloak --> JWT { sub, groups, access_level, dept }
JWT --> LibreChat --> Orchestratore --> [ogni servizio a valle]
```

Ogni servizio a valle **verifica la firma del JWT** e ricava da sé il filtro di accesso. L'orchestratore non "dichiara" i permessi: li trasporta. Così un bug nell'orchestratore non diventa un data leak.

Variante pragmatica per un team piccolo: un gateway valida il token una volta e inietta header firmati su rete privata. Accettabile se e solo se i servizi non sono raggiungibili dall'esterno del cluster.

### 4.3 Contratto 2 — ogni capability è un MCP server

Invece di integrazioni REST ad hoc una per sistema, ogni capability espone gli stessi strumenti tipizzati:

```
orchestratore
  |-- mcp-documenti    : cerca_documenti(query, filtri)
  |-- mcp-anagrafiche  : trova_cliente(...), dettaglio_cliente(...)
  |-- mcp-metriche     : interroga_metrica(metrica, dimensioni, periodo)  -> Cube
  |-- mcp-listini      : prezzo(articolo, cliente, quantita)              -> ERP
  |-- mcp-agenda       : slot_liberi(...), crea_evento(...)      [conferma]
  |-- mcp-documenti-out: genera_offerta(template, dati)          [conferma]
```

Conseguenza pratica: **aggiungere una capability = aggiungere un container**, senza toccare l'orchestratore né il frontend. È questo che rende l'architettura incrementale invece di un big bang.

Gli strumenti con effetti esterni (`crea_evento`, invio mail, scrittura su ERP) restituiscono una *proposta*, non un fatto compiuto: l'orchestratore la mostra e l'utente conferma.

**MCP non è una proprietà dei prodotti.** È un adattatore sottile (100–300 righe) che scrivi tu davanti a un prodotto. Tre categorie:

| | Componenti | Ruolo |
|---|---|---|
| **Infrastruttura** — invisibile all'LLM | Keycloak, Postgres, Qdrant, Docling, Gotenberg, Langfuse | Non vengono mai invocati in conversazione |
| **Adattatori MCP** — li scrivi tu | `mcp-documenti`, `mcp-metriche`, `mcp-listini`, `mcp-anagrafiche`, `mcp-agenda`, `mcp-documenti-out` | Poche operazioni tipizzate; dentro chiamano i prodotti |
| **Client MCP** | LibreChat / orchestratore | Consuma, non espone |

Cosa **non** deve mai diventare MCP:

- **Keycloak.** L'LLM non "chiede se l'utente ha il permesso". Se il controllo di accesso diventa una tool call, l'LLM può decidere di non farla.
- **Il database.** Un `mcp-postgres` con un tool `query(sql)` è text2SQL travestito, con in più il bypass delle ACL.
- **La pipeline di ingestion.** Gira su eventi, non su richiesta.
- **Langfuse.** Osserva, non viene invocato.

Criterio: *l'LLM deve decidere a runtime di invocarlo, con argomenti, in mezzo a una conversazione?* → tool MCP. Altrimenti è plumbing.

Sugli **MCP server ufficiali dei vendor**: sono generici per design (espongono tutta la superficie API, o SQL arbitrario). Così perdi l'ACL shaping e il modello si trova centinaia di tool tra cui scegliere. Riferimento di implementazione, non produzione.

**Regola di forma:** gli strumenti si modellano sugli *intent di business*, non sui sistemi. Non `mcp-sap` e `mcp-sharepoint`, ma `prezzo()` e `cerca_documenti()`. Altrimenti la topologia dei sistemi finisce nello spazio decisionale dell'LLM: deve sapere *dove* sta un dato prima di chiederlo — fragile, e si rompe a ogni migrazione.

### 4.4 Ingestion — una sola pipeline, guidata da eventi

```
SharePoint / file share / MinIO
        | (evento: file nuovo o modificato)
        v
worker:  Docling (parse) -> chunk -> embed -> upsert
        v
Qdrant/pgvector  (payload: acl_groups, source, versione, data)
```

Una pipeline, un formato di metadata, un punto in cui si decidono le ACL. RAGFlow, se usato, sta **dentro** questo worker: non è una seconda porta d'ingresso dei documenti.

### 4.5 Esempio end-to-end

> *"Quanto abbiamo fatturato con Rossi SpA quest'anno e qual è il suo sconto di listino sulla serie X?"*

1. LibreChat inoltra domanda + JWT (`access_level: 3`, `dept: sales`).
2. Orchestratore → `mcp-anagrafiche.trova_cliente("Rossi SpA")` → `id: C-10432`.
3. → `mcp-metriche.interroga_metrica("fatturato", cliente=C-10432, periodo=YTD)` → Cube applica RLS sul livello 3 → **412.300 €** (numero dal DB, non dall'LLM).
4. → `mcp-listini.prezzo(serie="X", cliente=C-10432)` → **sconto 18%** dall'ERP.
5. → `mcp-documenti.cerca_documenti("condizioni serie X", acl_groups del token)` → clausola contrattuale con citazione.
6. L'LLM compone la risposta **citando le fonti**; Langfuse registra le 4 chiamate e la latenza.

Se al punto 3 l'utente fosse di livello 1, Cube restituirebbe vuoto — e la risposta sarebbe "non hai accesso a questo dato", non un numero filtrato a posteriori.

### 4.6 I due anti-pattern da evitare

- **UI stacking.** Più applicazioni ognuna con il proprio login. Gli utenti si perdono e i permessi divergono silenziosamente. Una sola UI; tutto il resto headless.
- **Logica di business disegnata in una GUI.** Flussi decisionali dentro canvas n8n o LangFlow: non testabili, non versionabili, non revisionabili. n8n va usato per il *plumbing* dei connettori, mai per le decisioni.

### 4.7 Fase 1: quattro container, non otto

```
keycloak | librechat | postgres+pgvector | ingestion-worker
```

I contratti OIDC + MCP esistono dal giorno uno; i servizi si aggiungono uno per fase **senza rifacimenti**. È il contratto a essere definito subito, non lo stack completo.

---

## 5. Fasi

| Fase | Contenuto | Tempo (1–2 dev + 1 referente dati) |
|---|---|---|
| **0 — Discovery** | Matrice ACL (ruoli × fonti), inventario sorgenti, 100 domande d'oro reali | 3–4 settimane |
| **1 — MVP documentale** | Chat + SSO + RAG su 2–3 corpus a diverso livello di accesso, con citazioni | 6–8 settimane |
| **2 — Dati strutturati** | Semantic layer + tool anagrafiche/clienti + 10–15 metriche | 8–10 settimane |
| **3 — Produzione documenti** | Preventivi con pricing dall'ERP + template + approvazione umana | 6–8 settimane |
| **4 — Azioni e agenda** | Calendario, mail, ticket — tutto con conferma | 6–8 settimane |
| **5 — Voce** | Solo se le fasi 1–4 sono adottate davvero | 8–12 settimane |

**Sistema completo: 12–18 mesi.** Primo valore percepito: mese 3.

Criterio di passaggio tra fasi: **adozione reale**, non completezza tecnica. Se dopo la fase 1 nessuno usa la chat, le fasi successive sono soldi buttati — e il problema sarà la qualità dei documenti, non il modello.

---

## 6. Decisioni aperte

Le decisioni già prese stanno nel **§12, registro delle decisioni**. Restano aperte:

1. **Quale ERP/gestionale?** La replica è decisa (§15, decisione 46); resta da sapere **come si legge** l'ERP — accesso al database, viste, API o esportazioni — perché decide il metodo di sincronizzazione. *Blocca la fase 2.*
2. **Quanti utenti e quanti livelli di accesso?** Sotto 50 utenti cambiano licenze e architettura; sopra 200 servono cache e tuning per tenant.
3. **Team disponibile?** 1 dev part-time → stack più assemblato. 2–3 dev dedicati → conviene costruire l'orchestratore.
4. **Chi firma la matrice di residenza?** Serve un nome, non un ruolo. Senza, in fase 2 quella matrice diventa una decisione tecnica presa da chi scrive il codice — che è il posto sbagliato. *Blocca la fase 1b.*
5. **Il percorso locale ristretto è accettato?** Sui dati sensibili si risponde con citazioni, non si costruiscono preventivi. Se serve l'agente completo anche lì, il budget hardware cambia di categoria (§11.3).

Risolto in corso d'opera: il **vincolo on-prem** non è totale ma selettivo — vale per le sorgenti marcate `interno` (§11).

---

## 7. L'architettura spiegata semplice

### 7.1 L'analogia dell'ufficio

Immagina di assumere un **segretario** bravissimo a parlare ma che **non sa nulla** dell'azienda. Non ha in testa nessun dato. Però sa esattamente a quale ufficio girare ogni domanda. Tu vai allo sportello e chiedi; lui va negli uffici dietro, raccoglie, torna e risponde in italiano.

| Nell'ufficio | Nell'architettura | Prodotto |
|---|---|---|
| Il badge che porti al collo | Identità e livello di accesso | **Keycloak** |
| Lo sportello dove parli | La chat che vedi | **LibreChat** |
| Il segretario che smista | Il coordinatore | **codice proprio** + Claude |
| L'archivio dei documenti | Documenti cercabili | **Docling** + **pgvector** |
| L'ufficio contabilità | Numeri e fatturato | **Cube** → database |
| L'ufficio commerciale | Clienti e listini | **ERP** |
| La tipografia interna | Produzione documenti | **docxtpl** + **LibreOffice/Gotenberg** |
| Il registro delle telefonate | Cosa è stato chiesto e risposto | **Langfuse** |

**MCP** è il *modulo prestampato* con cui il segretario fa richiesta a ogni ufficio. Sempre lo stesso formato per tutti gli uffici. Niente altro.

### 7.2 Esempio A — un preventivo, passo per passo

> **Utente:** *"Prepara un preventivo per Rossi SpA: 40 pezzi della serie X."*

1. **Il badge.** **Keycloak** ha già stabilito: *Marco, ufficio vendite, livello 3*. L'informazione è attaccata alla domanda e la segue in ogni passaggio.
2. **Lo sportello.** **LibreChat** prende la frase e la passa dietro. Non decide, non cerca, non calcola.
3. **Il coordinatore capisce.** Con Claude: *"mi servono tre cose: chi è Rossi SpA, quanto costa la serie X per lui, il modello di offerta."*
4. **Ufficio commerciale.** `trova_cliente("Rossi SpA")` → **ERP**: `C-10432, pagamento 60gg, agente Bianchi`.
5. **Listini.** `prezzo(serie X, C-10432, qta 40)` → **ERP**: `78 €/pz, sconto 18%, totale 3.120 €`.
   👉 Il prezzo lo calcola l'ERP. Il coordinatore **non fa aritmetica**: prima o poi sbaglierebbe uno scaglione di sconto, e lo scoprireste dal cliente.
6. **Archivio.** `cerca_documenti("condizioni di garanzia serie X")` → i PDF letti da **Docling** e indicizzati in **pgvector** restituiscono la clausola **con documento e pagina**.
7. **Tipografia.** `genera_offerta(modello, dati)` → **docxtpl** compila il modello Word del marketing, **LibreOffice headless** produce il PDF.
8. **Risposta.** *"Preventivo pronto: 40 pezzi serie X, 3.120 € netti, pagamento 60gg. Garanzia 24 mesi (Condizioni 2026, pag. 12). Ecco il PDF — confermi l'invio a Rossi?"*
9. **Registro.** **Langfuse** ha annotato le quattro richieste, i tempi e le risposte. Se domani il preventivo risulta sbagliato, sai quale ufficio ha dato il dato errato.

### 7.3 Esempio B — la stessa domanda, accesso diverso

Un magazziniere, livello 1, chiede *"quanto abbiamo fatturato con Rossi SpA?"*.

Il coordinatore compila lo stesso modulo verso la contabilità. **Cube** guarda il badge e risponde: **vuoto**. Non un numero arrotondato, non un "non te lo posso dire": non esiste risultato. Il coordinatore può solo dire *"questo dato non è nel tuo livello di accesso"*.

👉 **Il coordinatore non ha mai visto il numero.** Non deve ricordarsi di tacere: non gli è arrivato. È la differenza tra sicurezza vera e sicurezza sperata.

### 7.4 Perché non un unico programma

1. **Un solo badge.** Se ogni ufficio avesse la sua lista di autorizzati, a ogni assunzione ne aggiorneresti cinque — e una la dimenticheresti.
2. **Ogni dato ha una sola casa.** Nessuno tiene una seconda copia che poi divergerà.
3. **Aggiungi un ufficio senza rifare lo sportello.** L'agenda al mese 8: nuovo servizio, un modulo in più. Chat, badge e archivio non si toccano.
4. **Sai sempre chi ha sbagliato.** Numero errato → guardi il registro. In un programma unico è un'indagine.

### 7.5 Le tre cose da ricordare

1. **Il coordinatore parla, gli uffici sanno.** Claude formula le frasi; i dati vengono da un sistema, mai dalla sua memoria.
2. **I numeri non li inventa nessuno.** Ogni cifra è il risultato di una query. Se non passa da un sistema, non esce.
3. **Il badge filtra alla fonte.** Il dato vietato non viene nascosto: non viene mai estratto.

---

## 8. Implementazione dell'interfaccia — verificato

### 8.1 Cosa apre l'utente

Un browser su un indirizzo interno, es. `https://assistente.azienda.it`. Dietro, un reverse proxy punta a **LibreChat**. Solo LibreChat e Keycloak sono esposti; orchestratore, database e MCP server vivono su rete privata e **non sono raggiungibili dal browser**. L'utente vede una sola applicazione.

### 8.2 Il login

```
1. Apri assistente.azienda.it
2. LibreChat: "Accedi con account aziendale"  →  redirect a Keycloak
3. Keycloak: SSO se già autenticato, altrimenti credenziali (+MFA)
4. Keycloak rimanda un codice a LibreChat
5. LibreChat scambia il codice con un token:
      { sub: "marco.r", groups: ["vendite"], access_level: 3 }
6. LibreChat crea un cookie di sessione per il browser
```

- Il **browser** ha solo il cookie di sessione LibreChat.
- Il **token Keycloak** resta lato server, dentro LibreChat. Non passa mai dal browser.
- Il token dura 5–15 minuti e si rinnova contro Keycloak. **Disattivi l'utente in Keycloak → è fuori al prossimo rinnovo**, senza toccare LibreChat.
- I ruoli si gestiscono **solo in Keycloak** (gruppi `vendite`, `amministrazione`, `direzione` + claim `access_level`). LibreChat li riceve, non li decide.

### 8.3 Lo storico delle chat

Le conversazioni le salva **LibreChat nel suo MongoDB**, una per utente, privata.

- **L'orchestratore è stateless.** A ogni messaggio LibreChat rispedisce tutta la conversazione. Puoi riavviarlo, scalarlo, aggiornarlo senza perdere nulla.
- ⚠️ **Lo storico è un archivio di dati sensibili.** Se un utente vedeva il fatturato e poi cambia ruolo, le vecchie chat **contengono ancora quei numeri**. Il filtro vale sulle domande nuove, non sul passato. Serve una policy (retention 90 giorni, o cancellazione al cambio ruolo) decisa **in fase 1**, non scoperta in audit.
- Il DB di LibreChat rientra in backup, GDPR e diritto di cancellazione.

### 8.4 Come LibreChat parla con l'orchestratore

LibreChat sa parlare con endpoint **compatibili OpenAI**. L'orchestratore ne espone uno: `POST /v1/chat/completions`. LibreChat crede di parlare con un modello; in realtà parla con il coordinatore. Nella tendina l'utente vede una voce sola.

Guadagno: **nessuna modifica al codice di LibreChat**. Storico, upload file, ricerca, mobile, condivisione sono gratis. Se domani cambi frontend, l'orchestratore non cambia.

```yaml
# librechat.yaml
endpoints:
  custom:
    - name: "Assistente Aziendale"
      baseURL: "http://127.0.0.1:8000/v1"
      apiKey: "${ORCHESTRATOR_KEY}"
      models:
        default: ["assistente-v1"]
        fetch: false
      titleConvo: true
      headers:
        Authorization: "Bearer {{LIBRECHAT_OPENID_ACCESS_TOKEN}}"
        X-Conversation-Id: "{{LIBRECHAT_BODY_CONVERSATIONID}}"
```

```ini
# .env
OPENID_ISSUER=https://sso.azienda.it/realms/azienda/.well-known/openid-configuration
OPENID_CLIENT_ID=librechat
OPENID_CLIENT_SECRET=...
OPENID_SCOPE=openid profile email offline_access
OPENID_REUSE_TOKENS=true
OPENID_REUSE_MAX_SESSION_AGE_MS=900000
```

**Verificato sulla documentazione (settembre 2026):**

- Esistono sette placeholder OIDC per gli header dei custom endpoint: `{{LIBRECHAT_OPENID_TOKEN}}`, `..._ACCESS_TOKEN`, `..._ID_TOKEN`, `..._USER_ID`, `..._USER_EMAIL`, `..._USER_NAME`, `..._EXPIRES_AT`.
- **Keycloak è supportato esplicitamente** per il token reuse (con Auth0 ed Entra).
- `offline_access` è **obbligatorio**: senza refresh token i placeholder restano vuoti.
- Con `OPENID_REUSE_TOKENS=true` il refresh token nel cookie è emesso **da Keycloak, non da LibreChat**.
- **Nessuna patch necessaria.** Il rischio identificato in prima analisi è chiuso.

Due avvertenze:

1. In Keycloak serve un **audience mapper** sul client, così l'orchestratore può validare il campo `aud`. Senza, la firma è valida ma l'audience sbagliata.
2. Esistono anche header più semplici (`X-User-ID: {{LIBRECHAT_USER_ID}}`, `X-User-Email`, `X-User-Role`) che funzionano senza token reuse, ma **non sono un confine di sicurezza**: sono header scritti da LibreChat. Per le ACL usare il JWT, che si verifica contro il JWKS di Keycloak.

### 8.5 Il flusso reale

```
browser ──POST──> LibreChat ──(salva msg in Mongo)
                      │
                      └──POST /v1/chat/completions──> ORCHESTRATORE
                                                          │
                                         ┌────────────────┼────────────────┐
                                    Claude API      mcp-listini      mcp-documenti
                                    (decide)        (ERP: 3.120€)    (pgvector)
                                                          │
                      ┌───────────────────────────────────┘
                      │  streaming SSE
browser <──stream──── LibreChat ──(salva risposta in Mongo)
                      Langfuse registra tutto
```

Il ciclo interno: chiama Claude con la lista degli strumenti → Claude chiede `prezzo(...)` → esegui → rimandi il risultato → Claude chiede `cerca_documenti(...)` → esegui → Claude scrive la risposta. Tre-quattro giri, poi streaming del testo.

### 8.6 Due dettagli pratici

**Il PDF.** Non far passare un file dentro il protocollo della chat. Il servizio documenti scrive su MinIO (o su file share) e restituisce un **link firmato a scadenza breve**; la risposta contiene il link.

**La conferma.** L'orchestratore non invia: restituisce la proposta come testo. L'utente risponde "sì" e al giro successivo l'azione viene eseguita. Il "sì" va legato a un'azione specifica, altrimenti il modello può ricostruirne una diversa:

```sql
pending_actions(conversation_id, tool, args_json, expires_at)
-- ponytail: scadenza 10 minuti, una sola azione pendente per conversazione
```

---

## 9. Infrastruttura

### 9.1 La decisione

**Docker su VM Linux.** Il vincolo iniziale era "evitare Docker", ma il problema reale era diverso: Docker *su Windows* richiede WSL2 o Hyper-V, quindi nested virtualization se il server è una VM. **Docker su Linux non ha questo requisito**: i container sono processi con namespace, nessuna virtualizzazione coinvolta.

Quindi: una VM Linux (Debian/Ubuntu, 4 vCPU / 16 GB) sullo stesso hypervisor, nessuna nested virtualization, un `docker-compose.yml`.

Alternative valutate e scartate:

| Opzione | Esito |
|---|---|
| Nested virtualization sulla VM Windows | Praticabile (un flag sull'hypervisor), ma su Hyper-V costa dynamic memory e live migration |
| Podman su Windows | Nessun vantaggio: usa WSL2, stesso problema |
| Container Windows in process isolation | Girerebbero senza virtualizzazione, ma tutte le immagini che servono sono Linux |
| Installazione nativa su Windows | Funziona, costa riproducibilità → **Piano B, §9.4** |

Cosa si recupera scegliendo Docker: Langfuse self-hosted, Gotenberg, Qdrant, RAGFlow e vLLM tornano disponibili; rollback di un'immagine; una configurazione sola sotto git.

### 9.2 I file della fase 1

L'implementazione vive in **`Sviluppo/`**; alla radice restano i documenti e `.gitattributes`. Tutti i comandi `docker compose` si lanciano da `Sviluppo/`.

| File (sotto `Sviluppo/`) | Contenuto |
|---|---|
| `docker-compose.yml` | stack applicativo: 5 servizi + ingestion on demand |
| `docker-compose.override.yml` | differenze di sviluppo (Windows, ricarica a caldo) |
| `docker-compose.inference.yml` | host GPU — forma di produzione, fase 1b |
| `.env.example` | tutte le variabili, con le note sui vincoli |
| `Caddyfile` | unico punto esposto, TLS |
| `librechat.yaml` | il custom endpoint verso l'orchestratore |
| `litellm-config.yaml` | routing dei modelli — fase 1b |
| `initdb/01-databases.sql` | i due database, pgvector, `sources`, `chunks` con le ACL, `conversation_taint`, `index_meta`, `pending_actions` |
| `inference/Caddyfile` | TLS + token davanti all'inferenza |
| `eval/smoke_embeddings.py` | test degli embedding su LM Studio |
| `orchestratore/`, `ingestion/` | ← da scrivere |

I servizi:

```
caddy          unico punto esposto (80/443)
postgres       pgvector: archivio + pending_actions + DB di Keycloak
keycloak       badge
mongo          utenti e storico chat di LibreChat
librechat      sportello
orchestratore  il codice proprio (endpoint OpenAI-compatible)
ingestion      Docling — on demand: docker compose run --rm ingestion
```

Tre scelte deliberate, marcate nei file:

- **Un solo Postgres, due database.** Keycloak non merita un container di DB proprio.
- **`mcp-documenti` gira in-process** dentro l'orchestratore. Diventa un container separato quando arriva il secondo MCP server.
- **Ingestion on demand**, non un servizio. Il file-watcher quando servirà davvero.

Avvio:

```bash
cd Sviluppo
cp .env.example .env    # compilare
docker compose up -d
docker compose run --rm ingestion
```

### 9.3 Langfuse

Langfuse v3 aggiunge ClickHouse, Redis e uno storage S3: quattro container per una UI che in fase 1 non serve ancora. In fase 1 le tracce vanno in una tabella Postgres (~30 righe di codice); Langfuse si aggiunge in fase 2 come compose override, quando la UI di analisi inizia a servire davvero.

*ponytail: tracciare ≠ Langfuse. Il tracciamento è obbligatorio dal giorno uno, il suo cruscotto no.*

⚠️ **Le tracce contengono il testo dei prompt.** Quindi l'osservabilità ha una dimensione di sovranità: **Langfuse Cloud è escluso** per i turni sul percorso interno, e in generale le tracce dei turni marcati `interno` restano nel Postgres interno. Vale anche per l'opzione Cloud citata come alternativa al self-hosting: utilizzabile solo se il percorso interno viene escluso dall'export.

### 9.4 Piano B — nativo su Windows, se la VM Linux non arriva

Valutato e praticabile: sette processi come servizi Windows, con cinque sostituzioni. Riferimento da tenere in caso l'IT non conceda la VM.

| Prodotto | Nativo su Windows | Come |
|---|---|---|
| **Keycloak** | ✅ supportato | ZIP + JDK 21, `kc.bat start` |
| **LibreChat** | ✅ documentato | Node 20+, `npm ci && npm run backend` |
| **MongoDB** | ✅ | installer MSI, servizio Windows |
| **PostgreSQL** | ✅ | installer EDB |
| **pgvector** | ⚠️ | binari precompilati, altrimenti build MSVC — verificare |
| **Docling** | ✅ | `pip install docling` (OCR: Tesseract a parte) |
| **Orchestratore + MCP** | ✅ | codice proprio |
| **Reverse proxy** | ✅ | Traefik è un singolo .exe, oppure IIS + ARR |
| **Cube** (fase 2) | ✅ | npm |
| **n8n** | ✅ | npm |
| **Langfuse** | ❌ | Next.js + ClickHouse + Redis + S3 |
| **Gotenberg** | ❌ | esiste solo come container |
| **Qdrant** | ⚠️ | build ufficiali Linux/Docker; su Windows va compilato |
| **RAGFlow** | ❌ | solo docker-compose, molti servizi |
| **vLLM** | ❌ | Linux/CUDA |

### Le sostituzioni

| Invece di | Usa | Perdi |
|---|---|---|
| Langfuse self-hosted | **Langfuse Cloud** o una tabella `traces` in Postgres | niente di essenziale in fase 1 |
| Gotenberg | **LibreOffice headless** (`soffice --convert-to pdf`) | nulla |
| Qdrant | **pgvector** — già la raccomandazione | solo su volumi enormi |
| RAGFlow | **Docling + worker proprio** — già la raccomandazione | ~2 settimane di lavoro |
| vLLM | **Ollama** (build Windows nativa) | throughput, non qualità |

Sette processi registrati come servizi Windows con `nssm`: `traefik.exe`, keycloak (JDK 21), librechat (Node 20), mongod, postgres+pgvector, orchestratore, ingestion-worker.

Il costo: si perde la riproducibilità — aggiornamento manuale, e il rollback di una versione rotta significa reinstallare. Mitigazione: uno script PowerShell che installa e aggiorna tutto con le versioni fissate, tenuto sotto git. Infrastruttura come script invece che come compose.

---

## 10. Il gestore di agenti

### 10.1 La scelta: LangGraph con l'SDK dentro i nodi

**LangGraph dalla fase 1**, nella forma seguente:

```
✅ LangGraph per il grafo
✅ client OpenAI-compatible verso il proxy LiteLLM, per nome logico
❌ niente langchain-anthropic (ne alcun wrapper di provider)
❌ niente checkpointer in fase 1 (invoke stateless) → si accende in fase 3
```

⚠️ **Questa è una revisione.** La versione precedente chiamava l'SDK Anthropic nativo dentro i nodi, per conservare thinking adattivo, `effort` e i breakpoint del prompt caching. Il requisito **"il modello di riferimento lo scelgo io"** (decisione 38) rende quella scelta insostenibile: un SDK di provider dentro i nodi *è* un vincolo di provider. Vedi §11.9 per il costo esatto.

Il nodo chiama un endpoint OpenAI-compatible per **nome logico** (`ragionamento`, `veloce`), e non sa quale modello né quale provider ci sia dietro. Nessun wrapper di libreria: un client HTTP e uno schema di richiesta.

**Guadagno inatteso:** anche il modello interno della fase 1b è OpenAI-compatible via vLLM. Quindi i due percorsi — cloud e interno — usano **lo stesso codice**, e il caso speciale "nativo" sparisce. Il gate decide la rotta, non il corpo del nodo.

Dove stanno le quattro funzioni di un "gestore di agenti":

| Funzione | Chi la fa |
|---|---|
| Ciclo multi-step sui tool | **LangGraph** — grafo esplicito, nodi testabili senza chiamare il modello |
| Stato della conversazione | **LibreChat/Mongo** — l'orchestratore resta stateless |
| Human-in-the-loop | **`interrupt()`** di LangGraph (fase 3) — sostituisce `pending_actions` |
| Tracce, retry, replay | nodi + tabella `traces`, poi Langfuse |

**Costo dichiarato:** 2-3 giorni in più in fase 1 rispetto al solo Tool Runner dell'SDK, che sarebbe bastato per il flusso a un tool della fase 1. Motivo per pagarli subito: `interrupt()` fatto bene vale più di una tabella `pending_actions` con scadenza a 10 minuti, la fase 3 arriva prima di quanto sembri, e **la topologia del grafo è la decisione cara — il corpo dei nodi è quella economica** (§11.6).

*Dipendenze pinnate: l'ecosistema LangChain cambia in fretta.*

### 10.2 Le quattro strade, e perché questa

| Approccio | Chi fornisce harness / deploy | Verdetto |
|---|---|---|
| Loop manuale (`while stop_reason == "tool_use"`) | tu / tu | Funziona, ~50 righe, ma hook e ramificazioni te li riscrivi |
| Tool Runner dell'SDK Anthropic | SDK / tu | Ottimo e gratuito, ma senza grafo né `interrupt()` durevole |
| **LangGraph + SDK nei nodi** | LangGraph / tu | ✅ **Scelto.** Grafo, interrupt, replay; nessun wrapper del modello |
| Managed Agents | Anthropic / Anthropic | ❌ I nostri tool (ERP, Postgres, MCP interni) sono su rete privata: il sandbox di Anthropic non li raggiunge |
| Claude Agent SDK | SDK / tu | ❌ È Claude Code come libreria: tool built-in di filesystem e bash. Forma sbagliata per tool di business ristretti |

### 10.3 Dettaglio MCP importante

Il connettore MCP remoto della Messages API (`mcp_servers` + `mcp_toolset`) fa connettere **i server di Anthropic** all'URL del server MCP. I nostri MCP server sono interni e non raggiungibili da internet: giustamente, dato che parlano con l'ERP.

Quindi l'orchestratore si connette **localmente** ai propri MCP server e ne espone i tool a Claude come tool normali. Nessuna differenza per il modello, e nessun buco nel firewall.

### 10.4 Cosa è stato scartato, e perché

| Strumento | Perché no |
|---|---|
| **Dify / Flowise / LangFlow** | Vogliono essere frontend + RAG + auth insieme: collidono con LibreChat e con la regola dell'owner unico (§4.1). E mettono la logica di business in una GUI — l'anti-pattern del §4.6 |
| **CrewAI / AutoGen** | Multi-agente con ruoli che dialogano. Ci serve l'opposto: chiamate a tool deterministiche con ACL, non agenti che deliberano |
| **Temporal** | Workflow engine, non gestore di agenti. Ha senso solo con approvazioni multi-giorno |
| **`langchain-anthropic`** | Il wrapper, non LangGraph: arretra sulle funzionalità dell'API di Claude (thinking adattivo, `effort`, breakpoint del caching). L'SDK nativo si chiama dentro i nodi |

### 10.5 Cosa si accende dopo, e quando

Il grafo c'è dalla fase 1; queste sue funzioni si attivano per fase:

| Funzione LangGraph | Quando | Perché non subito |
|---|---|---|
| **Checkpointer Postgres** | fase 3 | In fase 1 l'orchestratore è stateless per scelta: lo stato è di LibreChat. Accenderlo prima creerebbe **due proprietari** dello stesso stato, contro il §4.1 |
| **`interrupt()`** | fase 3 | Non ci sono conferme da gestire finché non si generano preventivi |
| **Subgraph / sub-agenti** | fase 4+ | |

*ponytail: il grafo si scrive una volta, i nodi si riempiono per fase. Accendere il checkpointer prima che serva significa possedere uno stato di cui non si ha bisogno, e doverlo riconciliare con Mongo.*

### 10.6 Parametri del modello — schema neutro

Il modello è una variabile di progetto (§11.9), quindi si usa il denominatore comune OpenAI-compatible:

- **`model`** = nome logico (`ragionamento`, `veloce`), risolto dal proxy.
- **Streaming obbligatorio**: LibreChat consuma SSE, e lo streaming evita i timeout HTTP.
- **`tools`** con JSON Schema, e **validazione dell'input a valle** di ogni tool: la fedeltà del tool calling varia sensibilmente fra provider, e un argomento malformato su una chiamata ACL-sensibile non può passare.
- **`temperature` bassa** e output strutturato dove serve determinismo.
- `max_tokens` esplicito e timeout brevi: un provider lento non deve bloccare la conversazione.

**Parametri specifici di provider** (thinking, effort, breakpoint di caching, budget) si passano **solo** attraverso i `litellm_params` della rotta, mai dal codice del nodo. Con `drop_params: true` un parametro ignoto viene scartato invece di far fallire la richiesta — indispensabile quando il modello cambia.

*Conseguenza: le leve di costo/qualità che erano gratuite con un SDK nativo diventano lavoro per-provider. Il conto è al §11.9.*

---

## 11. Sovranità del dato e sostituibilità del modello

Requisito: il modello deve essere sostituibile con uno locale. **Il driver è la sovranità del dato, non il costo** — e la distinzione cambia tutte le scelte a valle.

### 11.1 Il conto onesto: locale ≠ più economico

| | Costo reale |
|---|---|
| API, 100 utenti × 20 domande/giorno | qualche centinaio di € / mese su Opus, meno con caching |
| Locale, modello 27-32B usabile | GPU 6-9k € una volta, oppure 1,5-3k € / mese in cloud |

**Il locale costa più dell'API su scala aziendale.** Va scelto per la sovranità, non per la bolletta. Se il driver fosse il costo, la leva giusta sarebbe il **prompt caching** su system prompt e lista tool (stabili) più `effort` tarato per rotta — e un'astrazione provider-neutra fa perdere entrambi. Portabilità e risparmio in parte si mordono la coda.

### 11.2 Come si distingue un dato sensibile

La tentazione è un classificatore: un modello legge il contenuto e decide. **Non funziona, per un motivo circolare:** per classificare devi mandare il contenuto al classificatore. Se è in cloud, hai già esfiltrato ciò che volevi proteggere; se è locale, stai già pagando la GPU — e applichi una decisione *probabilistica* esattamente dove non te la puoi permettere.

**La sensibilità non è una proprietà del contenuto. È una proprietà della sorgente.** E la sorgente è nota con certezza al momento dell'ingestion: quale share, quale libreria SharePoint, quale tabella dell'ERP, quale gruppo Keycloak. La classificazione è quindi **strutturale, deterministica, decisa una volta sola** — non inferita a ogni query.

#### Due assi ortogonali

**Residenza ≠ ACL.** È il punto più sottovalutato del progetto:

| | Può uscire | Non può uscire |
|---|---|---|
| **Accesso largo** | manuali tecnici, procedure | catalogo completo con listini |
| **Accesso ristretto** | verbali del CdA (se la policy lo consente) | buste paga, contratti, valutazioni |

Il catalogo con i prezzi lo leggono tutti i commerciali, ma non deve finire nel training di terzi. Una busta paga è ristretta *e* non deve uscire. Due colonne, due decisioni, **due proprietari diversi**: le ACL le decide chi gestisce i ruoli, la residenza chi risponde di compliance.

#### Il registro delle sorgenti

Non una colonna in più, una tabella:

```sql
sources(id, percorso_o_tabella, acl_groups, residency, owner, approvato_da, approvato_il)
```

Venti-quaranta righe: **è la matrice di residenza come dato, non come foglio Excel.** Auditabile, versionata, con il nome di chi l'ha firmata.

`chunks` porta `source_id` e `residency` **dalla prima ingestion**. Retro-etichettare non è un UPDATE: è ricostruire da quale sorgente veniva ciascun chunk. Se non l'hai registrato, non puoi — ri-ingestion completa.

### 11.3 La regola di routing: high-water-mark

> Se **qualunque** chunk recuperato — o qualunque risultato di tool — è marcato `interno`, **tutto il turno** gira sul modello locale. Nessuna eccezione, nessuna euristica.

È la logica dei livelli di classificazione: l'output prende l'etichetta più alta di qualsiasi input. Ed è **dimostrabile con un test**, dove un classificatore darebbe solo una stima.

Due conseguenze:

**La contaminazione è della conversazione, non del turno.** Lo storico torna al modello a ogni messaggio: una volta che un dato sensibile entra, quella conversazione resta interna fino alla fine. Più semplice e più sicuro che ripulirla.

```sql
conversation_taint(conversation_id, livello, da_quando)
```

Tre colonne. L'header `X-Conversation-Id` già configurato in `librechat.yaml` (§8.4) serve a questo.

**L'utente lo deve vedere.** Una conversazione che passa al modello interno diventa più lenta e meno brillante; senza spiegazione sembra un guasto. Una riga basta: *"risposta elaborata sul modello interno — i dati richiesti non lasciano l'azienda."*

### 11.4 L'anonimizzazione: rete di sicurezza, non cancello

Idea valutata: un modello anonimizza i dati, così possono uscire.

**Dove funziona:** pseudonimizzazione reversibile di identificatori su payload *strutturati e limitati*. `Rossi SpA → CLIENTE_A`, P.IVA, IBAN, indirizzi; si manda in cloud; si rimappa in locale. Presidio (Microsoft, MIT) lo fa con regex deterministiche per le entità italiane — codice fiscale, P.IVA, IBAN — che sono testabili.

**Dove si rompe, in tre modi tutti fatali:**

1. **Re-identificazione dal contesto.** *"CLIENTE_A di Bolzano, settore funivie, 4,2M di fatturato"* — ce ne sono tre. Anonimizzare i nomi non anonimizza un dataset piccolo: è il fallimento classico della k-anonymity, ed è definitivo sulla propria base clienti.
2. **I numeri non si anonimizzano.** Se devi ragionare sul fatturato vero, il fatturato vero deve uscire — ed è spesso *quello* il dato sensibile.
3. **La recall non è mai 100%.** Un nome in una tabella scansionata, un indirizzo in un blocco firma, una persona identificata dal ruolo (*"il direttore dello stabilimento di Vercelli"*). Un miss è esattamente la fuga che il sistema doveva prevenire, e **non sai che è avvenuta.**

Alla domanda *"sono usciti dati sensibili?"*, **"probabilmente no, l'anonimizzatore ha il 97% di recall"** non è una risposta difendibile. Il routing per sorgente permette di dire: *"no, per costruzione, ed ecco il test."*

**Decisione:** anonimizzazione come **difesa in profondità**, non come cancello. Routing deterministico per decidere dove va la richiesta, **e** mascheramento degli identificatori sul percorso cloud comunque — così una sorgente etichettata male produce una fuga mascherata invece che in chiaro. Deterministico come cancello, probabilistico come rete di sicurezza. Mai il contrario.

### 11.5 Cinque ruoli, non due

| Ruolo | Dove | Dimensione | Fase |
|---|---|---|---|
| **Embedding** | locale, **obbligatorio** | 568M (bge-m3) | 1 |
| **Reranker** | locale | 568M (bge-reranker-v2-m3) | 1 |
| **Utility** (titoli, riscrittura query) | locale | 7-8B Q4 | 2 |
| **Ragionamento sensibile** | locale | 27-32B ← la GPU serve per questo | 1b |
| **Ragionamento generale** | cloud, capacità piena | Claude Opus 5 | 1 |

#### Il percorso locale deve essere più stretto, non solo più debole

Il modello locale è debole su una cosa specifica: **orchestrare sei tool scegliendo bene gli argomenti**. È invece quasi competitivo su *leggere chunk e rispondere citando le fonti* — **se il retrieval è buono**.

E il retrieval si compra con lavoro, non con parametri: parsing Docling fatto bene, ricerca ibrida, reranker, chunk dimensionati. **Spendere sul retrieval compensa il modello più debole**, ed è molto più efficiente che inseguire un 70B.

Quindi: le domande sensibili ottengono **document-QA con citazioni, uno o due tool, nessun multi-step autonomo**. Non ottengono l'agente che costruisce preventivi.

#### La linea cade dove serve, quasi da sola

Il corpus sensibile — HR, contratti, verbali — è fatto di **documenti da leggere**. L'orchestrazione complessa — preventivi, listini, analytics — vive su dati **commerciali**, già destinati a uscire verso clienti e fornitori.

**L'eccezione che farà male è una sola:** dati del personale incrociati con analytics (*"costo del personale per reparto, trend 12 mesi"*) — sensibile **e** orchestrazione. Ma è già risolta da una decisione presa per altri motivi: quel numero esce da **Cube**, con metriche pre-definite e SQL deterministico. Il modello scegle il nome della metrica e formula la frase; non fa aritmetica. Un 27B sa fare quelle due cose.

*Il semantic layer, scelto al §3 per l'affidabilità, si ripaga qui per la sovranità. È il segno che l'architettura è coerente e non incollata.*

### 11.6 Strade separate ora, con un modello solo

Decisione: in esercizio **un modello** (cloud), ma **le due strade già scavate**. Cinque meccanismi da costruire in fase 1.

**1. Il registro delle sorgenti** (§11.2), popolato subito. In fase 1 tutto è `cloud_ok`; le sorgenti sensibili si marcano `interno` **subito**, e la conseguenza è pulita: **non vengono ingerite.** Il corpus sensibile aspetta, non trafila.

**2. Il gate: fail closed, non fail open.** Non uno stub che logga e continua. Con un solo modello configurato:

> taint = `interno` **e** rotta locale non configurata → **rifiuta di rispondere**
> *"Questa domanda richiede fonti che non lasciano l'azienda. Il modello interno non è ancora attivo."*

Uno stub permissivo è **peggio di niente**: insegna a ignorarlo, e il giorno che accendi il locale scopri che non ha mai funzionato. Un gate che rifiuta davvero viene testato dalla realtà ogni giorno. In fase 1 non scatterà quasi mai — le sorgenti sensibili non sono indicizzate — quindi costo UX zero.

**3. La forma del grafo: due rami, uno è un vicolo cieco.**

```
retrieval → [taint?] ─ cloud_ok → agente completo (tool set intero, multi-step)
                     └ interno  → qa_interno (1-2 tool, solo citazioni)
                                   ↑ oggi contiene solo il rifiuto
```

**La topologia del grafo è la decisione cara. Il corpo dei nodi è quella economica.** Mettendo il ramo adesso, non si ristruttura mai.

**4. La tabella `conversation_taint`** (§11.3).

**5. Il test, che è l'artefatto di compliance.** Per ogni sorgente `interno` nel registro: una query che la recupererebbe, e l'assert che **nessuna richiesta sia uscita verso un host di `EGRESS_EXTERNAL`**.

Il meccanismo conta: **assert a livello di trasporto, non applicativo.** Si patcha il client HTTP e si verifica ogni destinazione. Un assert applicativo lo aggira il percorso che ti sei dimenticato; il trasporto no.

L'enforcement è un'**allowlist di egress dichiarata**, non "solo localhost" — perché l'host di inferenza è esso stesso una macchina remota (§11.10):

| Lista | Contenuto | Regola |
|---|---|---|
| `EGRESS_INTERNAL` | host di inferenza, Postgres, Keycloak | sempre consentiti |
| `EGRESS_EXTERNAL` | `api.anthropic.com` | **vietati** su turno contaminato |
| non dichiarato | qualsiasi altro host | **errore**, non warning, in ogni caso |

L'ultima riga è la più importante: un host non dichiarato fa fallire la richiesta anche su un turno pulito. Così una dipendenza nuova aggiunta distrattamente non diventa un canale di uscita silenzioso.

Quel test passa oggi (perché rifiuta) e deve passare identico il giorno che arriva il modello locale (perché instrada in locale). **Stesso test, due epoche.** È quello che si mostra al revisore.

#### Il costo di farlo ora

| | Tempo |
|---|---|
| Ora: colonna, registro, ramo del grafo, tabella taint, gate con rifiuto, test sul trasporto | **1-2 giorni** sopra la fase 1 |
| Dopo: ri-ingestion completa, ristrutturazione del grafo, audit di ogni percorso verso il cloud | **settimane**, e l'audit non si chiude mai con certezza |

Costa poco perché **non si costruisce il percorso locale: si costruisce la forma e l'applicazione della regola.** Il modello è l'ultima cosa che si innesta, in quattro righe di `litellm-config.yaml`.

### 11.7 Cosa è locale dal giorno uno

Tre cose, anche con un modello solo.

**1. L'embedding.** Si calcola su *ogni chunk di ogni documento* in ingestion. Se è cloud, il documento sensibile **è già uscito prima che qualcuno faccia una domanda** — nessun gate a runtime può rimediare. L'embedding locale non è prematuro: **è la precondizione perché il corpus sensibile sia ingeribile.**

**2. Il reranker.** Vede il testo dei chunk recuperati. Locale per lo stesso motivo.

**3. Una sola chiave.** **Solo l'orchestratore possiede la chiave Anthropic.** Nessun altro componente. Se un componente ha la chiave, esiste un percorso al modello che non passa dal gate.

#### Il componente: Infinity, non Ollama

**Ollama non fa reranking** — nessun endpoint di rerank. Servirebbe un secondo servizio. **Infinity** (MIT) serve embedding *e* reranking in un container, con bge-m3 e bge-reranker-v2-m3. Alternativa più rigida ma molto veloce: **TEI** di HuggingFace (Apache 2).

*LM Studio è uno strumento di valutazione, non un componente: app desktop con GUI, perfetta per provare modelli in mezz'ora, non va su un server. Nota: il reranking non esiste nell'API OpenAI — va verificato se LM Studio lo espone; le strade certe sono `llama-server --reranking`, Infinity o TEI.*

#### La macchina di sviluppo copre tre ruoli su quattro

GPU da 16GB disponibile:

| Ruolo | VRAM (FP16, con batch) |
|---|---|
| Embedding bge-m3 | ~2-3 GB |
| Reranker bge-reranker-v2-m3 | ~2-3 GB |
| Utility 7-8B Q4 | ~5 GB |
| **Totale** | **~10-11 GB ✅** |
| Ragionamento 27-32B + 32k contesto | ❌ 24-32 GB |

**Tre su quattro si validano subito, a costo zero.** Solo il generatore grande richiede un noleggio.

#### I quattro test che decidono qualcosa

1. **Recall dell'embedding sul dominio.** 30 domande reali, corpus campione, **recall@10**, confronto con un embedding cloud come riferimento. Se bge-m3 sta entro pochi punti, la sovranità sull'embedding è gratis. La domanda vera non è l'italiano generico: sono **codici articolo e part number**, dove il vettoriale tipicamente sbaglia.
2. **Guadagno del reranker.** **Recall@5 prima e dopo**, stesse 30 domande. Salto grosso → la scommessa "32B + retrieval eccellente invece di 70B" si rafforza. **Salto piccolo → il problema è il chunking, non l'ordinamento** — scoperta più utile della prima.
3. **Dimensione dei chunk.** bge-m3 gestisce 8192 token, molto più dei 512 tipici: per i manuali tecnici è un vantaggio reale. Provare 512 / 1024 / 2048 contro il recall.
4. **Throughput di ingestion.** Chunk/secondo in batch: serve a sapere se la finestra di indicizzazione è minuti o giorni, e a dimensionare il reindexing quando il chunking cambierà (cambierà).

⚠️ **Lock-in:** bge-m3 produce **1024 dimensioni**, che combaciano con la `vector(1024)` di `initdb`. Cambiare modello di embedding dopo significa **ricalcolare tutto il corpus**. Questo test non è esplorativo: è la scelta di un impegno pluriennale, e va fatto con le 30 domande vere.

### 11.8 Hardware: il conto

#### La metrica giusta non è il numero di parametri

27B a Q4 sono ~16GB **di pesi**. Poi serve la KV cache, e il nostro caso è RAG: chunk recuperati + definizioni dei tool + storico. A 32k di contesto sono altri 6-8GB.

**"27B su 16GB" è vero per un chatbot con prompt corti, marginale per RAG.** Realisticamente **24-32GB** per un'istanza utile. La capacità non è il collo di bottiglia: 100 utenti × 20 domande/giorno con picco 15-20/minuto li serve un nodo singolo con vLLM.

#### Macchina valutata: DGX Station V100 (ETB refurb, €7.877)

4× Tesla V100 32GB SXM2, NVLink 300GB/s, 256GB ECC, 1500W, tower, 1 anno di garanzia. Offerta pulita, ma **V100 è Volta, SM 7.0** — sotto la soglia di diverse cose che servono a noi:

| Cosa | Requisito | V100 |
|---|---|---|
| **AWQ** (quantizzazione 4-bit mainstream) | SM 7.5+ | ❌ |
| **FlashAttention 2** | SM 8.0+ | ❌ fallback su kernel lenti |
| **BF16** | Ampere+ | ❌ solo FP16 |
| **FP8** | Ada/Hopper+ | ❌ |
| **CUDA 13** | — | ❌ **Volta rimossa dai target di build** |

Le prime due mordono dove fa più male: le quantizzazioni con cui un 27B sta in poca VRAM sono in parte precluse, e **manca FlashAttention proprio sul contesto lungo**, che è il nostro caso. La quinta è strutturale: `sm_70` non è più target di compilazione, quindi si resta pinnati a CUDA 12.x e a versioni vecchie di PyTorch e vLLM **per sempre**, su una macchina il cui scopo è far girare modelli che non esistono ancora. L'OS di serie è Ubuntu 18.04, fuori supporto dal 2023.

Gira ancora bene: llama.cpp (kernel propri), GPTQ con kernel vecchi, FP16 non quantizzato. Non è inutile: è un sentiero che si restringe.

**E soprattutto: 128GB non ci servono.** Il dimensionamento dice 24-32GB. 128GB di Volta risolvono un problema di capacità che non abbiamo, e creano un problema di supporto software che oggi non abbiamo.

#### Se la strada è il refurbished, la generazione giusta è Ampere o Ada

| Opzione | ~Prezzo | Verdetto |
|---|---|---|
| **RTX 6000 Ada 48GB** | €6-7k | ⭐ Miglior fit: tower, 300W, silenziosa, FP8, 48GB con margine, anni di runway |
| RTX PRO 6000 Blackwell 96GB | €8-9k | Più VRAM del necessario, architettura attuale, una scheda sola |
| A100 40GB refurb | €6-8k | Il refurbished giusto: BF16, FA2, AWQ, CUDA supportata |
| DGX Station V100 128GB | €7,9k | Più VRAM per euro, architettura sbagliata |

Una generazione sopra compra BF16, FlashAttention, AWQ e supporto CUDA per tutto il decennio, allo stesso prezzo.

**Dove il DGX vincerebbe:** se servisse un **70B** — cioè se il percorso locale ristretto (§11.5) venisse rifiutato e si volesse l'agente completo anche sui dati sensibili. In quel caso è la porta più economica per 128GB con NVLink, scommettendo su un'architettura deprecata. **Non è una scelta di hardware: è la stessa decisione del §11.5, vestita da preventivo.**

#### Non comprare prima dell'eval

Le 100 domande d'oro non esistono ancora. Sono loro che dicono se basta un 27B, se serve un 70B, o se basta un 14B perché il corpus sensibile è fatto di contratti ben strutturati. **Comprare prima dell'eval è comprare prima di conoscere il requisito** — e fra le due risposte ci sono €3.000 e un'architettura diversa.

1. **Fase 1 su corpus non sensibile.** Zero GPU d'acquisto, embedding e reranker sulla macchina di sviluppo. 6-8 settimane, primo valore.
2. **Costruire l'eval** sulle domande sensibili reali, con Claude come riferimento di qualità.
3. **Noleggiare una GPU un pomeriggio** — €2-3/ora su RunPod o Lambda. Provare 14B, 32B, 70B con **retrieval identico a produzione**: unica variabile il modello.
4. **Poi comprare**, sapendo cosa.

*Un pomeriggio da €20 decide un acquisto da €8.000: è il miglior rapporto di tutto il progetto.*

⚠️ **Non mettere l'acquisto GPU sul percorso critico della fase 1.** Il corpus sensibile e il modello locale sono la **fase 1b**, in parallelo all'approvvigionamento. Altrimenti il progetto sta fermo tre mesi in attesa di un preventivo hardware, senza niente da mostrare. Il gate e la colonna `residency` si scrivono subito; la rotta locale si accende quando la macchina arriva.

### 11.9 Implementazione della sostituibilità

**LiteLLM come servizio**, non una libreria nel codice. Il routing dei modelli diventa un concern con owner unico, come Keycloak per l'identità (§4.1). Cambiare modello è **una riga in `litellm-config.yaml`**, senza toccare il codice né ridistribuire.

Nel codice resta **una funzione** che parla col modello; il resto dell'orchestratore conosce solo i nomi logici `ragionamento`, `veloce`, `embedding`, e in fase 1b `ragionamento_interno`.

Coerente con lo scarto di `langchain-anthropic` (§10.4): il punto di sostituzione **sale a livello di servizio** invece di scendere nel codice. Un proxy si riconfigura a caldo, una libreria va ridistribuita.

#### Nessun modello di riferimento imposto — decisione 38

**Il modello principale è una scelta di progetto, non un vincolo dell'architettura.** `litellm-config.yaml` non ha default: la rotta `ragionamento` va compilata, e la scelta si prende con i numeri dell'eval sulle domande d'oro.

Criteri che contano per *questa* architettura, in ordine:

| # | Criterio | Perché pesa qui |
|---|---|---|
| 1 | **Affidabilità del tool calling** con argomenti corretti | Tutto l'agente ci si appoggia. Un ID cliente inventato è un preventivo sbagliato |
| 2 | Tenuta su **contesto lungo** | RAG: prompt da 20k+ token fra chunk, tool e storico |
| 3 | Qualità in **italiano** | |
| 4 | **Disciplina nelle citazioni** | Una fonte inventata è peggio di nessuna fonte |
| 5 | **Costo per risposta completata**, non per richiesta | Un modello che serve tre giri non è più economico |
| 6 | Esiste una versione **ospitabile in casa**? | Rilevante per la fase 1b |

#### Il prezzo, in chiaro

La neutralità di provider costa, e va detto prima di scoprirlo in bolletta:

| Si perde | Impatto |
|---|---|
| **Breakpoint espliciti del prompt caching** | La leva di costo più forte su un carico RAG, dove system prompt e lista tool sono stabili e grandi. Alcuni provider fanno caching implicito, altri no |
| **Dial di ragionamento/effort** | La leva qualità/costo dentro lo stesso modello |
| **Ottimizzazioni di streaming dei tool** | Marginale qui |
| **Fedeltà uniforme del tool calling** | Il rischio funzionale vero: lo schema OpenAI è il denominatore comune, e i provider non sono equivalenti nel rispettarlo |

Recuperabili **per-provider** attraverso i `litellm_params` della rotta — ma è lavoro che va rifatto a ogni cambio di modello. È il prezzo della libertà di scelta, ed è un prezzo accettabile se la scelta conta.

#### Il guadagno

Un solo percorso di chiamata invece di due. Il modello interno della fase 1b è OpenAI-compatible via vLLM, quindi **cloud e interno usano lo stesso codice**: il caso speciale "nativo" sparisce, e il gate decide la rotta senza che il nodo sappia nulla.

#### Senza eval la sostituibilità è finta

Modello sostituibile senza eval = lo sostituisci e non sai di aver peggiorato. Con le **100 domande d'oro** della fase 0, *"Qwen3 locale regge?"* diventa una risposta numerica in venti minuti invece di un'opinione.

Era già in fase 0. Ora è **la condizione perché il requisito di sostituibilità sia reale e non dichiarato.**

#### Container

```
litellm    routing dei modelli (owner unico)
infinity   embedding + reranker in casa — la GPU da 16GB basta
vllm       fase 1b: il 27-32B, quando la macchina arriva
```

### 11.10 L'host di inferenza è una macchina separata

La macchina con GPU **non** è il server applicativo: sono due host distinti, con cicli di vita e di aggiornamento diversi. Questo cambia il vocabolario e introduce un confine che prima non c'era.

#### "Locale" era la parola sbagliata

Il termine corretto è **interno**: dentro il *perimetro aziendale*, non dentro la *macchina*. Il confine che conta per la sovranità è la rete dell'azienda, non il bordo del processo. Dall'applicazione, **i modelli interni sono endpoint HTTP come qualsiasi altro servizio** — cambia solo che l'endpoint è dentro il perimetro.

Conseguenza operativa: due stack separati.

```
Sviluppo/docker-compose.yml             app: caddy, postgres, keycloak,
                                             mongo, librechat, orchestratore
Sviluppo/docker-compose.inference.yml   GPU: proxy TLS, infinity, (1b) vllm
```

In sviluppo girano sulla stessa macchina, in produzione su host diversi. **L'unica differenza è il valore di `EMBEDDING_URL`** — la topologia è configurazione, non codice.

#### Il nuovo confine di trust

Il testo di **ogni chunk di ogni documento**, compresi quelli `interno`, ora attraversa la LAN. "Stessa macchina" era una garanzia implicita che non c'è più. Tre requisiti, non opzionali:

1. **TLS + token** davanti ai servizi di inferenza. HTTP nudo su LAN non è una risposta difendibile in audit.
2. **Firewall:** solo il server applicativo raggiunge le porte di inferenza.
3. **Nessuna rotta verso internet** dall'host GPU, a parte il download iniziale dei modelli. Poi si chiude.

#### Due rischi nuovi, due guardie

**L'host di inferenza diventa una dipendenza al momento della query.** Se è giù, non si può nemmeno calcolare l'embedding della domanda: niente ricerca vettoriale, sistema fermo.

> **Mitigazione: fallback su BM25.** La ricerca è già ibrida (§3): se l'host di inferenza non risponde, si serve il solo full-text con un avviso esplicito. Degradato ma vivo. Se manca solo il reranker, si servono i risultati non riordinati. *Guadagno gratuito reso possibile da una scelta fatta per altri motivi.*

**Indice e modello possono divergere in silenzio.** Qualcuno reinstalla la macchina GPU, `bge-m3` arriva a una revisione diversa, gli embedding si spostano e **il recall cala senza alcun errore**. Nessuno se ne accorge per mesi.

> **Mitigazione: tabella `index_meta`.** Registra modello, revisione e dimensioni con cui l'indice è stato costruito. All'avvio l'orchestratore confronta con quanto l'host dichiara e **rifiuta di partire** se non combaciano. Sei righe di codice contro un degrado invisibile.

#### Un guadagno inatteso

Il piano "noleggiare una GPU un pomeriggio" (§11.8) diventa **una variabile d'ambiente**: si punta `EMBEDDING_URL` e `LLM_RAGIONAMENTO_INTERNO` all'host noleggiato e si testa **con il codice di produzione, senza modifiche**. Il confronto 14B / 32B / 70B è pulito per costruzione.

---

## 12. Registro delle decisioni

| # | Decisione | Scelta | Motivo in una riga | Stato |
|---|---|---|---|---|
| 1 | Punto di partenza | Integrare progetti esistenti, non costruire da zero | Il valore è nel plumbing dati e ACL, non nel riscrivere una chat | ✅ |
| 2 | Frontend | **LibreChat** (MIT) | RBAC, SSO, MCP, licenza pulita; Open WebUI ha vincoli di branding sopra i 50 utenti | ✅ |
| 3 | Identità | **Keycloak**, owner unico dei ruoli | Un solo posto dove promuovere o disattivare una persona | ✅ |
| 4 | Contratto di integrazione | **OIDC** per l'identità + **MCP** per gli strumenti | Aggiungere una capability = aggiungere un container | ✅ |
| 5 | Identità verso l'orchestratore | `{{LIBRECHAT_OPENID_ACCESS_TOKEN}}` negli header del custom endpoint | Verificato sulla documentazione: **nessuna patch necessaria** | ✅ verificato |
| 6 | Dati strutturati | **Semantic layer** (Cube), non text2SQL libero | Un numero sbagliato sul fatturato brucia la fiducia in modo irreversibile | ✅ |
| 7 | Ricerca | **pgvector** ibrido (vettoriale + BM25) | Un sistema in meno; i part number il vettoriale li sbaglia | ✅ |
| 8 | Parsing | **Docling** | Qui si vince o si perde: chunk sbagliati = risposte sbagliate | ✅ |
| 9 | Infrastruttura | **Docker su VM Linux** | Docker *su Windows* vuole nested virtualization; su Linux no | ✅ |
| 10 | Orchestrazione | **LangGraph** dalla fase 1, SDK Anthropic dentro i nodi | `interrupt()` vale più di `pending_actions`; nessuna tassa di astrazione | ✅ |
| 11 | Linguaggio | **Python** | LangGraph, Docling, vLLM, Presidio, Ragas: un runtime invece di due | ✅ |
| 12 | Wrapper del modello | **Nessuno** (`langchain-anthropic` escluso) | L'API di Claude si muove veloce; il wrapper arretra | ✅ |
| 13 | Routing dei modelli | **LiteLLM** come proxy, nomi logici nel codice | Cambiare modello = una riga di config, senza deploy | ✅ |
| 14 | Driver del locale | **Sovranità del dato**, non costo | Il locale costa più dell'API su questa scala | ✅ |
| 15 | Classificazione del sensibile | **Per sorgente**, non per contenuto | Un classificatore LLM è circolare e probabilistico | ✅ |
| 16 | Residenza e ACL | **Due assi ortogonali**, due proprietari | Il catalogo è pubblico dentro e riservato fuori | ✅ |
| 17 | Regola di routing | **High-water-mark**, contaminazione a livello conversazione | Dimostrabile con un test, non stimata | ✅ |
| 18 | Anonimizzazione | **Difesa in profondità, non cancello** | 97% di recall non è una risposta difendibile in audit | ✅ |
| 19 | Percorso locale | **Più stretto**: document-QA con citazioni, 1-2 tool | Il locale è debole sull'orchestrazione, non sulla lettura | ⚠️ da confermare |
| 20 | Strade separate | **Da subito**, con un modello solo; gate **fail closed** | 1-2 giorni ora, settimane dopo | ✅ |
| 21 | Embedding e reranker | **Locali dal giorno uno**, su **Infinity** | Precondizione perché il corpus sensibile sia ingeribile; Ollama non fa rerank | ✅ |
| 22 | Chiave API | **Solo l'orchestratore la possiede** | Un componente con la chiave è un percorso che scavalca il gate | ✅ |
| 23 | Corpus sensibile | **Non ingerito** finché il gate non è rodato | Ciò che non è indicizzato non può trafilare per un bug | ✅ |
| 24 | Hardware | **Non comprare prima dell'eval**; se refurb, Ampere/Ada non Volta | Un pomeriggio da €20 decide un acquisto da €8.000 | ✅ |
| 25 | Modello cloud | **Claude Opus 5**, thinking adattivo, streaming | Il costo si governa con `effort`, non scendendo di modello | ✅ |
| 26 | Osservabilità | Tabella `traces` in fase 1, Langfuse in fase 2; tracce interne **non** in cloud | Tracciare ≠ Langfuse; le tracce contengono i prompt | ✅ |
| 27 | Residenza, default | **`interno`**; la fase 1 parte da 2-3 sorgenti approvate `cloud_ok` | Approvare 3 sorgenti lo fa chiunque, 40 è un progetto: la firma sblocca invece di permettere | ✅ rev. §14.7 |
| 28 | Sequenza fase 0 / fase 1 | **Sovrapposte**: infrastruttura e SSO partono subito | Sequenziale, lo sviluppatore costruisce su requisiti immaginati | ✅ rev. §14.1 |
| 29 | Domande d'oro | **20 raccolte** da ticket e mail, non 100 scritte da zero; il pilota genera le altre | Le persone non sanno scrivere domande di eval in astratto | ✅ rev. §14.3 |
| 30 | Ammissione di una sorgente | Serve **proprietario + versione autoritativa**, altrimenti non entra | Tre versioni di un listino indicizzate = citazione sicura e sbagliata | ✅ rev. §14.2 |
| 31 | Autenticazione | **Nessun fallback** senza token valido + self-check all'avvio + versione LibreChat pinnata | I placeholder non risolti diventano stringhe vuote: il fallimento è silenzioso | ✅ rev. §14.5 |
| 32 | Pilota | **10-12 utenti** che cercano documenti per lavoro, non manager; `traces` strumentate per la diagnosi | 5-8 non danno segnale, e un fallimento binario non dice cosa aggiustare | ✅ rev. §14.4 |
| 33 | Revisione del codice critico | **Una giornata esterna** su gate e filtro ACL | L'assicurazione più economica del progetto | ✅ rev. §14.6 |
| 34 | Host di inferenza | **Macchina separata**, stack proprio; dall'app è un endpoint HTTP | La GPU non è il server applicativo. "Interno" = dentro il perimetro, non dentro la macchina | ✅ §11.10 |
| 35 | Enforcement del gate | **Allowlist di egress dichiarata**; host non dichiarato = errore anche su turno pulito | "Solo localhost" non vale più: l'host di inferenza è remoto | ✅ §11.10 |
| 36 | Resilienza del retrieval | **Fallback su BM25** se l'host di inferenza non risponde | Senza embedding della query non c'è ricerca vettoriale: sistema fermo | ✅ §11.10 |
| 37 | Coerenza indice/modello | Tabella **`index_meta`** con canary; l'orchestratore non parte se il modello di embedding cambia | Una reinstallazione della macchina GPU farebbe calare il recall in silenzio | ✅ §11.10 |
| 38 | **Modello di riferimento** | **Nessuno imposto.** La rotta `ragionamento` è vuota in configurazione e si compila con i numeri dell'eval | Il modello è una scelta di progetto, non un vincolo dell'architettura | ✅ §11.9 |
| 39 | Chiamata al modello | **Client OpenAI-compatible verso il proxy**, per nome logico. Nessun SDK di provider nel codice | *Revisione della 12:* un SDK di provider dentro i nodi **è** un vincolo di provider | ⚠️ **rivede la 12** |
| 40 | LiteLLM | **Attivo dalla fase 1**, unico possessore delle chiavi dei provider | *Revisione:* era in `profiles: ["fase1b"]` perché senza modelli da instradare non serviva. Con la 38 la rotta da instradare c'è dalla fase 1 | ⚠️ **rivede la 13** |
| 41 | Costo della neutralità | Accettato: si perdono caching esplicito e dial di effort; recuperabili per-provider nei `litellm_params` | La libertà di scelta si paga in leve di ottimizzazione. Prezzo accettabile se la scelta conta | ✅ §11.9 |
| 42 | **ORM** | **Nessuno** (Prisma valutato e scartato). SQL a mano con psycopg | Prisma è TypeScript, non ha il tipo `vector`, e la query che conta (ibrida + RRF + sottoquery ACL) è SQL comunque. Un ORM scavalcato proprio dove serve non si ripaga | ✅ |
| 43 | **Migrazioni** | **File SQL numerati** in `migrations/` + `migrate.py` (~90 righe, zero dipendenze) | `initdb/` gira solo su volume vuoto: è bootstrap, non gestione dello schema. Fra fase 1 e fase 4 lo schema cambia almeno cinque volte | ✅ |
| 44 | Livelli di accesso | **Intersezione di gruppi**, non livello numerico | Un `access_level` numerico si rompe alla prima eccezione organizzativa; `acl_groups && groups_del_token` esprime di più ed è già indicizzato | ✅ |
| 45 | Firma della residenza | **Vincolo CHECK nel database**: `cloud_ok` impossibile senza `approvato_da` e `approvato_il` | Trasforma le decisioni 27 e 30 da convenzione a vincolo. Verificato: l'INSERT senza firma viene rifiutato | ✅ verificato |
| 46 | **Dati dell'ERP** | **Replica su tabelle nostre** (`erp.*`), sola lettura, ERP master | L'obiettivo è arricchire i dati, anche con l'AI: su un'interrogazione in tempo reale non c'è nulla su cui arricchire | ⚠️ **rivede §4.3 e §7.2** — §15 |
| 47 | Dati "da adesso" | Prezzo netto per cliente, giacenza, fido: **sempre dall'ERP**, al momento dell uso | La replica è in ritardo per costruzione; un preventivo con il prezzo di ieri è un errore verso il cliente | ✅ §15.3 |
| 48 | Motore prezzi | **Non si replica e non si ricostruisce** | Replicare i listini non replica la logica di sconto; ricostruirla in SQL significa sbagliarla | ✅ §15.3 |
| 49 | Arricchimenti AI | Tabelle separate (`arricchimenti.*`), **mai sovrascrivere un campo ERP**, provenienza e stato di revisione su ogni riga | Il dato di sistema e quello derivato devono restare distinguibili | ✅ §15.5 |
| 50 | Permessi sui dati strutturati | Tabelle e viste nel registro `sources`, default `interno`, RLS nel database, contaminazione estesa alle righe | Altrimenti la replica diventa il modo di aggirare le regole dei documenti | ✅ §15.6 |

---

## 13. Prossimi passi

### Immediati — non richiedono decisioni aperte

1. **Script di valutazione del retrieval** (~100 righe, gira sulla macchina di sviluppo). Indicizza un campione, misura recall@10 e @5 con e senza reranker, stampa una tabella. **Serve materiale**: qualche decina di documenti veri non sensibili (manuali, procedure) e 20-30 domande che i colleghi farebbero davvero. *Quelle valgono più del codice.*
2. ~~Correzioni al `docker-compose.yml`~~ — **fatto:** variabili di LibreChat separate (non vede più `ANTHROPIC_API_KEY`), `ollama` sostituito, LiteLLM spostato a `profiles: ["fase1b"]`, host di inferenza estratto in uno stack proprio, `index_meta` con canary nello schema.
3. **`orchestratore/`** in Python con LangGraph. **Primo pezzo: il gate con il suo test sul trasporto**, prima di tutto il resto — è l'unico punto dove un bug è una fuga di dati.
4. **Import del realm Keycloak** come script, così la configurazione è riproducibile invece che cliccata.

### Fase 0 — in parallelo, con l'azienda (non prima: §14.1)

5. **Matrice ACL e matrice di residenza delle 3 sorgenti della fase 1** — tre righe, non quaranta. Default `interno`; servono 2-3 approvazioni `cloud_ok` e il nome di chi le firma (§14.7).
6. **Proprietario e versione autoritativa** di ogni sorgente candidata. Senza risposta, la sorgente non entra (§14.2).
7. **20 domande reali**, raccolte da ticket helpdesk, mail all'ufficio tecnico e canali interni — non scritte da zero. Per il retrieval basta *"quale documento deve stare nei primi 5"*. Le altre 80 le genera il pilota (§14.3).

### Fase 1b — quando la fase 1 è in uso

8. **Eval sulle domande sensibili**, con Claude come riferimento di qualità.
9. **Noleggio GPU un pomeriggio**: 14B / 32B / 70B a retrieval costante.
10. **Acquisto**, sapendo cosa. Poi accensione della rotta `ragionamento_interno` e ingestion del corpus sensibile.

### Il criterio che governa tutto

**Adozione reale, non completezza tecnica.** Se dopo la fase 1 nessuno usa la chat, le fasi successive sono soldi buttati — e il problema sarà la qualità dei documenti, non il modello.

---

## 14. Rischi e mitigazioni

Valutazione critica del progetto, concentrata sulle fasi 0 e 1. Tre di questi rischi sono **difetti emersi rivedendo il piano operativo**, e hanno prodotto correzioni già applicate a `PIANO-FASE-0-1.md`.

### 14.0 Verdetto

La **fase 1 è tecnicamente solida e piccola**: dieci componenti quasi tutti pronti, ~400 righe di codice proprio, e l'unica vera incognita tecnica (l'inoltro dell'identità da LibreChat) è verificata e risolta.

**Il rischio non è tecnico.** È che la fase 0 non finisca mai, e che i documenti aziendali siano peggio di quanto chiunque ammetta.

### 14.1 La fase 0 non ha un proprietario — *difetto corretto*

Quattro deliverable su sei richiedono **tempo di altre persone**, e nessuno è un compito tecnico. Le fasi di discovery si bloccano sempre per tre ragioni prevedibili: la matrice ACL obbliga a decisioni che si preferisce evitare (*"un commerciale junior può vedere i margini?"*); le domande d'oro non sono di nessuno, quindi non le scrive nessuno; e **intanto lo sviluppatore non ha niente da fare e costruisce infrastruttura su requisiti immaginati.**

"3-4 settimane" vale **solo se qualcuno con autorità la guida**. Altrimenti sono tre mesi.

**Mitigazione — sovrapporre, non sequenziare:**

| Da | A |
|---|---|
| Fase 0 → poi fase 1 | Passi 1-2 della fase 1 (infrastruttura, SSO) **partono subito**: non dipendono dalle matrici |
| "La matrice ACL" | La matrice ACL **delle 3 sorgenti della fase 1**. Tre righe, non quaranta |
| "100 domande d'oro" | **20 domande** da un workshop di 90 minuti con 5 persone |
| Deliverable senza titolare | Ogni deliverable ha **un nome e una data**, o non esiste |

### 14.2 La qualità dei documenti è il primo killer — *difetto corretto*

Il piano trattava il parsing come una riga di elenco (Docling). Non basta. I documenti aziendali veri sono PDF scansionati storti, Excel usati come database, Word con l'informazione critica nel piè di pagina, tabelle spezzate su due pagine.

E soprattutto **duplicati e versioni**: `Listino_2025_DEF_rev2_USARE_QUESTO.pdf`.

⚠️ **Questo è il rischio grave.** Se tre versioni di un listino sono indicizzate, il sistema **citerà con sicurezza quella sbagliata**. È peggio di non rispondere: una risposta sbagliata *con citazione* è credibile.

**Mitigazione:**

- Fase 0, per ogni sorgente: **chi la possiede e qual è la versione autoritativa.** Se nessuno risponde, **quella sorgente non entra in fase 1.** Regola dura, da tenere.
- Test in ingestion: due chunk da documenti diversi con similarità >0.95 → **flag per revisione umana**, non silenzio.
- Preferire sorgenti con un ciclo di vita (DMS, wiki) alle cartelle di rete. *Una cartella di rete è dove i documenti vanno a morire.*
- Da dire a voce alta: **alcuni corpus vanno ripuliti prima di essere indicizzati, ed è lavoro manuale che nessuno ha messo a budget.**

### 14.3 Le domande d'oro portano troppo peso — *difetto corretto*

Nel piano facevano tre lavori (qualità del retrieval, scelta del chunking, decisione da €8.000 sulla GPU) su un artefatto che richiede domanda + risposta attesa + documento sorgente. Cento fatte bene sono 20-30 ore di lavoro di qualcuno — e **le persone non sanno scrivere domande di eval in astratto**: scrivono banalità o domande impossibili.

**Mitigazione — raccogliere, non chiedere:**

- Le domande vere **esistono già scritte**: ticket dell'helpdesk, mail all'ufficio tecnico, il canale dei "come si fa".
- Partire da **20**. Il pilota genera le altre 80 gratis, e sono reali per costruzione.
- Standard di giudizio più economico all'inizio: per il retrieval serve solo **"quale documento deve stare nei primi 5"**, non la risposta completa. Due minuti per domanda invece di venti.

### 14.4 Il criterio di adozione non dice *perché*

*"5-8 colleghi la usano spontaneamente"* è il criterio giusto, ma se fallisce produce un fatto binario e nessuna azione.

**Mitigazione — strumentare la diagnosi.** `traces` registra se il retrieval ha trovato qualcosa, se l'utente ha riformulato, se ha dato pollice giù (LibreChat ha il feedback). Un pilota fallito dà allora una causa:

| Sintomo | Causa | Cosa si aggiusta |
|---|---|---|
| Niente recuperato | buco nel corpus | ingestione di altre sorgenti |
| Recuperato ma sbagliato | chunking o ranking | di nuovo i test T0.2 e T0.3 |
| Recuperato giusto, risposta brutta | prompt o modello | il pezzo più facile |

E **5-8 utenti sono pochi**: se due vanno in ferie non c'è segnale. **10-12**, scelti fra chi cerca documenti per lavoro — ufficio tecnico, inside sales, assistenza. **Non i manager**, che non fanno ricerca documentale.

### 14.5 Il fallimento silenzioso dell'identità

La documentazione di LibreChat è esplicita: **i placeholder non risolti diventano stringhe vuote**, non testo letterale. Quindi una configurazione sbagliata o un aggiornamento non producono un errore rumoroso: producono una richiesta con **header `Authorization` vuoto**. Con una verifica JWT sciatta, il sistema **fallisce aperto** e nessuno se ne accorge.

**Mitigazione, tre linee di difesa:**

1. L'orchestratore **rifiuta qualsiasi richiesta senza token valido, senza percorso alternativo.** Nessun fallback, nessun utente anonimo.
2. **Self-check all'avvio:** se la configurazione non contiene l'header di autenticazione, il servizio **non parte**.
3. **Versione di LibreChat pinnata**; ogni aggiornamento rilancia T1.5/T1.6 prima della promozione.

### 14.6 Bus factor su 400 righe di codice critico

Gate, filtro ACL e verifica JWT scritti da una persona, con la premessa dichiarata che "nessuno metterà mano al codice". Significa che **il codice di sicurezza non è mai stato letto da un secondo paio di occhi** — e che tra 18 mesi nessuno potrà modificare la logica ACL.

**Mitigazione:** i test *sono* la revisione — T1.8 e T1.14 vanno scritti per essere leggibili come specifica da un non-autore. Più **una giornata di revisione esterna sul solo gate e filtro ACL**: è l'assicurazione più economica del progetto.

### 14.7 La firma sulla residenza è un blocco politico travestito da compito

Nessuno vuole firmare, perché firmare significa prendersi la conseguenza. Può bloccare la fase 1b a tempo indefinito.

**Mitigazione — invertire l'incentivo:**

> **Default: `interno`.** La fase 1 parte con **2-3 sorgenti esplicitamente approvate `cloud_ok`**.

Approvare tre sorgenti innocue (manuali, procedure) lo fa chiunque in dieci minuti; approvarne quaranta è un progetto. E la firma diventa un atto **che sblocca copertura**, richiesto dal business perché vuole di più, invece di un permesso che l'IT rincorre.

*Questo sostituisce l'impostazione precedente, che dava a tutta la fase 1 il default `cloud_ok`.*

### 14.8 Cosa **non** è un rischio

Elencato per non sprecare preoccupazione dove non serve:

| | Perché è tranquillo |
|---|---|
| Lo stack tecnologico | Tutto maturo, niente scommesse |
| La scala | 100 utenti sono niente: un nodo singolo basta |
| La qualità del modello sul document-QA | Opus 5 con retrieval decente non è il punto debole |
| L'integrazione LibreChat ↔ orchestratore | Verificata, nessuna patch necessaria |

### 14.9 I numeri onesti

| | Valutazione |
|---|---|
| Fase 1 consegna **tecnicamente** | **~85%** — i pezzi sono noti |
| Fase 1 viene **adottata** | **~50-60%** — dipende quasi solo dalla qualità dei documenti, oggi sconosciuta |
| Visione completa (fasi 1-5) | **~30-40%** — non per infattibilità, ma perché progetti così vengono superati da riorganizzazioni, cicli di budget e sponsor che cambiano ruolo. La consegna a fasi è la mitigazione, e c'è |

**Tempi reali:** fasi 0+1 sono 9-12 settimane con 1-2 sviluppatori **dedicati**. Con un solo sviluppatore part-time: **4-5 mesi di calendario.**

**Costo vero:** non l'API (qualche centinaio di euro in sviluppo), ma le **2-3 settimane di tempo di persone non-sviluppatrici** — referente dati, colleghi per le domande, la firma sulla residenza. Se non è formalmente allocato, arriva dalla buona volontà e arriva tardi.

### 14.10 Verificare per test, non per tabella di documentazione

Due verifiche, due esiti opposti, entrambi decisivi:

| Verificato | Documentazione diceva | Misura | Risparmio |
|---|---|---|---|
| Inoltro identità LibreChat → orchestratore | *forse serve una patch* (issue aperta) | **Funziona nativamente**, sette placeholder OIDC | Evitato un cambio di frontend |
| Rerank su LM Studio | RAGFlow lo elencava **fra i provider supportati** | **Non esiste l'endpoint**; il modello servito come embedding **ordina a caso** con punteggi plausibili | Evitato di collegare un componente rotto che non dà errori |

Il secondo è il caso peggiore possibile: un componente che **restituisce numeri credibili e sbagliati**. Nessun log, nessuna eccezione, solo risposte peggiori. Confermato indipendentemente da [RAGFlow issue #8116](https://github.com/infiniflow/ragflow/issues/8116), chiusa senza soluzione.

**Principio operativo:** ogni capability di un componente terzo su cui si appoggia una decisione architetturale va provata con una chiamata, prima di entrare nel piano. Costa dieci minuti. Le tabelle di compatibilità nelle documentazioni sono aspirazionali più spesso di quanto sia comodo credere.

### 14.11 La mossa a maggior rendimento

**Il deliverable 0.6, subito.** Prendere 30-50 documenti veri — **i peggiori che ci sono, non i più belli** — e far girare la valutazione del retrieval.

Converte l'incognita più grande del progetto in **un numero**, in due giorni, prima di aver impegnato un euro. Recall buono → la fase 1 è quasi fatta. Recall brutto → si è scoperto adesso, e non fra quattro mesi, che **il vero progetto era la pulizia dei documenti.**

---

## 15. Integrazione con l'ERP — replica e arricchimento

### 15.1 La scelta

**I dati dell'ERP si replicano su tabelle nostre**, invece di interrogare
l'ERP a ogni domanda. Rivede l'impostazione dei §4.3 e §7.2, che prevedevano
chiamate in tempo reale per anagrafiche e listini, e chiude la domanda
aperta n. 1 del §6.

Motivo principale: **l'obiettivo non è solo leggere i dati dell'ERP, ma
aumentarli** — con informazioni che l'ERP non ha, prodotte anche dall'AI.
Su un'interrogazione in tempo reale non c'è nulla su cui arricchire.

Vantaggi che vengono con la stessa scelta:

| Vantaggio | Perché conta |
|---|---|
| **Arricchimento** | Colonne e tabelle che l'ERP non ha: attributi estratti dalle schede tecniche, classificazioni, collegamenti con i documenti |
| **Join con i documenti** | Dati dell'ERP e `chunks` nello stesso database: *"documenti che riguardano questo cliente"*, *"manuali di questo articolo"* |
| **Disaccoppiamento** | L'ERP non regge carichi da chat, spesso non ha API decenti, e non deve rallentare perché qualcuno fa domande |
| **Storico** | L'ERP sovrascrive; la replica può conservare le versioni per i trend |
| **Semantic layer** | Cube (§3) lavora su SQL: una replica in Postgres è la sua sorgente naturale |
| **Permessi nostri** | Filtri sulle righe applicati nel nostro database, con le stesse regole dei documenti |

### 15.2 Tre strati

```
ERP (master, mai scritto)
  │  sincronizzazione in sola lettura
  ▼
erp.*            copia FEDELE, solo le colonne che servono
  │
  ├──► arricchimenti.*   tabelle NOSTRE, legate per chiave, con provenienza
  │
  ▼
Cube             metriche e dimensioni definite una volta, con i permessi
```

- **`erp.*` è una copia, non una fonte.** Il master resta l'ERP. Nessuna
  scrittura verso l'ERP, nessuna modifica manuale alle tabelle replicate: la
  prossima sincronizzazione la cancellerebbe.
- **`arricchimenti.*` non sovrascrive mai un campo dell'ERP.** Si affianca:
  `categoria_erp` resta com'è, `categoria_ai` sta in un'altra tabella.
  Chi legge deve sempre poter distinguere il dato di sistema da quello
  derivato.
- **Cube** espone solo metriche definite — la regola del §3 contro il
  text2SQL libero vale anche qui.

### 15.3 Cosa NON si replica: i dati che devono essere veri *adesso*

La replica è in ritardo per costruzione. Per la maggior parte dei dati va
benissimo; per tre no:

| Dato | Perché | Come |
|---|---|---|
| **Prezzo netto per cliente** | sconti a cascata, promozioni, condizioni commerciali: un preventivo con un prezzo di ieri è un errore verso il cliente | chiamata all'ERP al momento del preventivo (`mcp-listini`, §4.3) |
| **Giacenza** | cambia di minuto in minuto | idem |
| **Fido e blocchi amministrativi** | un cliente bloccato stamattina non deve ricevere un'offerta nel pomeriggio | idem |

⚠️ **La trappola più grande: reimplementare il motore prezzi.** Replicare i
listini **non** replica la logica di calcolo. Se la replica diventa la base
per calcolare prezzi, si finisce a ricostruire in SQL le regole di sconto
dell'ERP — e a sbagliarle. Il prezzo di un preventivo lo calcola l'ERP,
sempre (§1). I listini replicati servono a **cercare e confrontare**, non a
quotare.

### 15.4 Sincronizzazione

Da decidere quando si conosce l'ERP (§6, domanda 1), perché dipende da cosa
l'ERP permette di leggere. Principi validi in ogni caso:

- **Incrementale** dove possibile (data di ultima modifica o numero di
  versione), completa per le tabelle piccole.
- **Le cancellazioni vanno gestite esplicitamente**: un aggiornamento
  incrementale non vede le righe sparite. Senza, la replica tiene clienti o
  articoli che nell'ERP non esistono più.
- **Frequenza per tabella**, non unica: anagrafiche ogni notte, ordini ogni
  ora, fatturato storico ogni notte.
- **La deriva dello schema va rilevata**: un aggiornamento dell'ERP che
  rinomina una colonna deve fermare la sincronizzazione con un errore, non
  riempire la replica di valori vuoti.
- **Ogni tabella registra l'ultima sincronizzazione riuscita**: le risposte
  devono poter dire *"dato aggiornato alle 03:00"*.

*ponytail: uno script Python per tabella, con un "segnalibro" sull'ultima
modifica, lanciato a orari fissi. La cattura in tempo reale delle modifiche
(CDC) solo se un dato dimostra di servire più fresco di così — ed è più
probabile che la risposta giusta sia la chiamata diretta del §15.3.*
Se le tabelle diventano molte, **dlt** (Python, Apache 2) è lo strumento più
leggero per non riscrivere a mano la gestione dei segnalibri.

### 15.5 Arricchimento con l'AI

È il motivo della scelta, quindi va progettato con la stessa cura del gate.

**Dove rende di più**, in ordine di valore stimato:

| Arricchimento | Esempio | Perché rende |
|---|---|---|
| **Collegamenti documento ↔ entità** | questo manuale riguarda gli articoli DB-4471 e DB-4472; questo contratto riguarda il cliente C-10432 | abilita le domande che incrociano dati e documenti, oggi impossibili |
| **Attributi estratti dai documenti** | dimensioni, materiali, compatibilità letti dalle schede tecniche PDF | riempie i buchi dell'anagrafica articoli, che di solito sono tanti |
| **Descrizioni ricercabili** | una descrizione in linguaggio naturale per ogni articolo, con il suo embedding | *"divano sfoderabile per esterno"* trova l'articolo anche se l'ERP ha solo un codice |
| **Classificazioni** | categoria, segmento di clientela, settore | analisi che l'ERP non sa fare |

**Regole — tutte obbligatorie:**

1. **Provenienza su ogni riga derivata**: modello, versione, data, fonte usata,
   confidenza, e **stato di revisione** (`proposto` / `approvato` /
   `respinto`).
2. **Mai sovrascrivere un campo dell'ERP** (§15.2).
3. **I numeri non li produce l'AI** (§2, regola 2). L'AI può *estrarre* un
   valore da un documento, citandone la pagina; non può *stimarlo*.
4. **Le risposte dicono quando un'informazione è derivata**: *"secondo la
   scheda tecnica (estrazione automatica, non verificata)"*.
5. **Revisione umana prima dell'uso operativo** per ciò che finisce in un
   preventivo o verso un cliente.

**È il lavoro ideale per l'elaborazione massiva** di cui si era parlato al
§11: lotti notturni, nessuna fretta, volumi alti. Quindi è il candidato
naturale per il **modello interno** (fase 1b) o per le API batch dei
provider, che costano circa la metà. Non passa dalla chat e non ha i vincoli
di latenza dell'orchestratore.

⚠️ **Ricerca web sui clienti**: tecnicamente facile, va decisa con cautela.
Porta dati esterni di affidabilità variabile dentro la base aziendale, e sui
clienti persone fisiche è un trattamento di dati personali — vedi §15.7.

### 15.6 Permessi e residenza valgono anche per le tabelle

Oggi le regole di accesso e residenza (§11.2) sono sul registro `sources`,
pensato per i documenti. **Vanno estese ai dati strutturati**, altrimenti la
replica diventa il modo di aggirarle:

- **Ogni tabella o vista replicata è una voce del registro `sources`**, con
  `acl_groups` e `residency`. Default `interno`, come per i documenti
  (decisione 27).
- **Filtro sulle righe nel database** (Row-Level Security di Postgres) o nel
  contesto di sicurezza di Cube — mai nel prompt.
- **Alcune colonne sono più sensibili della tabella**: margini, costi, fidi.
  Stanno in viste separate con permessi propri, non nella stessa vista dei
  prezzi di listino.
- **La contaminazione vale anche per le righe**: una risposta che usa una
  tabella `interno` contamina la conversazione esattamente come un chunk
  `interno` (§11.3). Il gate va esteso ai risultati dei tool, come già
  previsto.

### 15.7 Rischi

| Rischio | Mitigazione |
|---|---|
| **Dati vecchi presentati come attuali** | data di sincronizzazione esposta nelle risposte; dati "da adesso" letti dall'ERP (§15.3) |
| **Due verità** | ERP master, replica in sola lettura, arricchimenti separati e marcati |
| **Motore prezzi ricostruito in SQL** | vietato per progetto: il prezzo di un preventivo viene sempre dall'ERP |
| **Arricchimenti sbagliati usati come fatti** | provenienza, confidenza, revisione umana, dichiarazione nella risposta |
| **Dati personali arricchiti** | clienti persone fisiche e dipendenti: arricchire con l'AI è un trattamento nuovo. **Serve una valutazione d'impatto (DPIA)** prima di farlo, non dopo |
| **Replica come scorciatoia per aggirare i permessi** | tabelle nel registro `sources`, RLS, contaminazione estesa (§15.6) |

### 15.8 Decisioni aperte

1. **Quale ERP** e come si legge: accesso diretto al database, viste
   dedicate, API, esportazioni. Decide il metodo di sincronizzazione.
2. **Quali dati servono "adesso"** oltre ai tre del §15.3.
3. **Tolleranza di ritardo** per ogni gruppo di tabelle.
4. **Arricchimento di dati personali**: sì o no, e con quale base giuridica.
5. **Ricerca web sui clienti**: sì o no.

Tutto questo è **fase 2**. Non cambia nulla della fase 1, tranne una cosa
da tenere in mente già ora: il registro `sources` deve poter descrivere
anche tabelle, non solo cartelle di documenti.

---

## Fonti verificate

- [LibreChat — Custom Endpoint Object Structure](https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/custom_endpoint)
- [LibreChat — OpenID Connect Token Reuse](https://www.librechat.ai/docs/configuration/authentication/OAuth2-OIDC/token-reuse)
- [LibreChat docs PR #755 — OIDC token placeholders](https://github.com/LibreChat-AI/librechat.ai/pull/755)
- [LibreChat issue #9444 — forward internal JWT (aperta, non necessaria per questa architettura)](https://github.com/danny-avila/LibreChat/issues/9444)
- [RAGFlow issue #8116 — rerank LM Studio non implementato](https://github.com/infiniflow/ragflow/issues/8116) — conferma indipendente della misura del 17/09/2026
- [LibreChat — Custom Endpoints quick start](https://www.librechat.ai/docs/quick_start/custom_endpoints)
