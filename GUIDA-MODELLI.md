# Guida — scegliere e configurare i modelli

**Domanda a cui risponde:** quale modello usa l'assistente, se se ne possono
usare più d'uno, e dove si configurano.

**In una riga:** i modelli si configurano **solo in LiteLLM** (file +
`.env`); l'orchestratore li conosce per **nome logico** e non sa di quale
provider siano. Sì, se ne usano più d'uno: oggi sono già previsti **quattro**
ruoli distinti.

---

## 1. I quattro ruoli (nomi logici)

L'orchestratore chiama LiteLLM con questi nomi. Cambiare il modello vero
dietro un nome non tocca il codice, solo la configurazione.

| Nome logico | A cosa serve | Stato oggi |
|---|---|---|
| `embedding` | vettori dei documenti (ingestion + domanda) | **attivo**: `bge-m3` locale, 1024 dim. Non si cambia senza reindicizzare |
| `ragionamento` | risponde alle domande (modello principale) | **scelto**: `qwen3.8-27b-gsq-rco` su LM Studio, contesto al massimo dei 16 GB |
| `veloce` | titoli, classificazione, riscrittura query, fallback | **da scegliere** (ripiega sul principale finché l'eval non dice se regge) |
| `ragionamento_interno` | turni con dati che non lasciano l'azienda (fase 1b) | **fase 1b** |

La rotta `ragionamento_interno` è decisa dal **gate** (`orchestratore/gate.py`):
se un turno tocca una fonte `interno`, il modello è quello interno; altrimenti
quello principale. Non lo decide il codice del nodo di risposta.

---

## 2. Dove si configurano (due posti, mai nel codice)

### 2.1 `.env` — il modello e la chiave

```
MODELLO_RAGIONAMENTO=          # es. openai/gpt-4o  | anthropic/claude-... | deepseek/...
MODELLO_API_KEY=               # chiave del provider
MODELLO_API_BASE=              # vuoto per i provider pubblici

MODELLO_VELOCE=                # modello economico
MODELLO_VELOCE_API_KEY=
MODELLO_VELOCE_API_BASE=
```

Formato di `MODELLO_*`: `<prefisso-litellm>/<id-del-modello>`. Prefissi
comuni: `openai/`, `anthropic/`, `gemini/`, `mistral/`, `deepseek/`,
`together_ai/`, `openrouter/`, `azure/`, `bedrock/`, `hosted_vllm/`,
`lm_studio/`. L'id esatto va verificato sulla documentazione del provider.

### 2.2 `litellm-config.yaml` — il collegamento nome logico → modello

Il file ha già i blocchi `ragionamento` e `veloce` **commentati**: si
scommentano e si riempiono. Il modello viene letto dalle variabili `.env`
con la sintassi `${...}` e `os.environ/...`, così il file resta committabile
senza segreti.

```yaml
model_list:
  - model_name: ragionamento
    litellm_params:
      model: ${MODELLO_RAGIONAMENTO}
      api_key: os.environ/MODELLO_API_KEY
      api_base: os.environ/MODELLO_API_BASE   # vuoto se il provider è pubblico

  - model_name: veloce
    litellm_params:
      model: ${MODELLO_VELOCE}
      api_key: os.environ/MODELLO_VELOCE_API_KEY
      api_base: os.environ/MODELLO_VELOCE_API_BASE

  - model_name: embedding        # già attivo
    litellm_params:
      model: lm_studio/text-embedding-bge-m3-embeddings
      api_base: os.environ/EMBEDDING_URL
```

Dopo la modifica: `docker compose restart litellm`.

### Perché il codice non c'entra

Il servizio `litellm` in `docker-compose.yml` riceve le `MODELLO_*` una per
una e monta `litellm-config.yaml` in sola lettura. L'orchestratore parla solo
con LiteLLM (`http://litellm:4000`) usando i nomi logici
(`LLM_RAGIONAMENTO=ragionamento`, `LLM_VELOCE=veloce`, `LLM_EMBEDDING=embedding`
nel suo blocco `environment:`). Nessuna chiave arriva all'orchestratore, a
LibreChat o al pannello (decisione 22/40).

---

## 3. Come si sceglie il modello principale

Modello scelto: **`qwen3.8-27b-gsq-rco` su LM Studio** (locale, sviluppo).
Contesto: il massimo che i 16 GB reggono — si alza **in LM Studio** finché il
modello ci sta in memoria (12947 era il valore iniziale). Decisione D1 del
committente, da confermare con l'eval sulle domande d'oro
(`eval/DOMANDE-CRITICHE.md`) prima di considerarla definitiva.

I criteri che contano per **questa** architettura, in ordine:

1. **affidabilità del tool calling** con argomenti corretti (il più importante)
2. **tenuta su contesto lungo** (RAG: prompt da 20k+ token)
3. **qualità in italiano**
4. **disciplina nelle citazioni** (non inventare fonti)
5. **costo per risposta completata**, non per richiesta
6. esiste una versione **ospitabile in casa**? (rilevante per la fase 1b)

Procedura consigliata:
1. compilare `.env` e `litellm-config.yaml` con un candidato
2. `docker compose restart litellm`
3. verificare con `smoke_embeddings.py` per l'embedding e una prova di chat
4. girare l'eval sulle domande d'oro e confrontare più candidati sullo stesso
   identico set di domande

⚠️ **Contesto e memoria:** 12947 token era stretto per un RAG (i criteri
partono da 20k+). Si alza al massimo dei 16 GB in LM Studio: a 27B conta
soprattutto la quantizzazione — con una quantizzazione aggressiva il contesto
può salire parecchio prima di toccare il tetto di memoria. Se l'eval mostra
risposte troncate o citazioni perse, si torna su questo punto.

---

## 4. Più modelli insieme: sì

- `ragionamento` e `veloce` possono essere **due modelli diversi** o lo
  stesso all'inizio (poi `veloce` si abbassa quando l'eval dice che regge).
- Il **fallback** è già predisposto in `litellm-config.yaml`
  (`router_settings.fallbacks`, oggi commentato): se il principale non
  risponde, LiteLLM ripiega sul `veloce` invece di non rispondere.
- `embedding` è un modello separato per costruzione (locale, obbligatorio:
  in ingestion passa il testo di ogni chunk di ogni documento).
- `ragionamento_interno` si aggiunge in fase 1b: stessa interfaccia
  OpenAI-compatible, è il gate a decidere quale rotta usare.

⚠️ **Sviluppo su 16 GB:** embedding, inferenza e OCR non girano tutti insieme
sulla stessa macchina. In LM Studio si carica un modello alla volta (o si
scala la RAM): lo sviluppo lo fa a turni, non in parallelo.

---

## 5. Trappole

| Trappola | Come si evita |
|---|---|
| Cambiare `embedding` | **reindicizzare**: i vettori di modelli diversi non si confrontano. L'indicizzazione se ne accorge da sola (`index_meta`) e smette di scrivere vettori con un'anomalia critica |
| `api_base` valorizzato per un provider pubblico | lasciarlo **vuoto**: un base sbagliato manda le richieste da un'altra parte |
| Modello con id sbagliato | verificare l'id esatto sulla doc del provider; `drop_params: true` evita errori sui parametri non riconosciuti |
| L'orchestratore non parla col provider | è giusto: parla solo con LiteLLM. Il provider lo raggiunge LiteLLM, l'unico con la chiave |
| `EGRESS_EXTERNAL` vuoto | va compilato con l'host del provider scelto: il gate consente solo host dichiarati (le chiamate verso il provider esterno saranno vietate sui turni contaminati) |

---

## 6. Configurazioni per hardware — «tutto in VRAM»

**Il principio**: la configurazione migliore è quella in cui **tutti i modelli
che servono stanno in VRAM insieme**, e nessuno deve essere scaricato per far
posto a un altro. Ogni scarico è tempo perso, chat che tace, complessità nel
codice. Quando la memoria non basta, si sceglie *cosa* rinunciare — non si
finge che ci stia tutto.

**L'unico modello fisso è `embedding`.** Gli altri si cambiano in base alla
scheda. L'embedding no: i vettori dell'indice sono suoi, cambiarlo significa
rileggere tutto (§5, `index_meta`).

### 6.1 Quanto occupa ciascuno (misurato il 20/09/2026, RTX 5060 Ti 16 GB)

| Componente | VRAM | Note |
|---|---|---|
| `bge-m3` (embedding) — **sempre** | 0,44 GB | fisso, non negoziabile |
| Chat: Qwen3-8B GSQ Q3 | 4,12 GB + cache | cache: 144 KB per token, divisa fra gli slot |
| Chat: Qwen3 27B GSQ | 13,05 GB + cache | a 8k di contesto occupa 13,9 GB in tutto |
| Docling (lettura documenti) | 2,1-2,7 GB di picco | solo mentre legge un blocco: fra un blocco e l'altro torna a zero |
| VLM descrizioni: glm-ocr | 2,27 GB | trascrive, **non descrive**: niente colori |
| VLM descrizioni: mineru2.5 | 1,86 GB | descrive, ma con token da ripulire e qualche degenerazione |
| VLM descrizioni: Qwen2.5-VL 7B | ~4,7 GB (stimato) | da provare |
| Descrizioni fatte dal 27B multimodale | — | nessuna VRAM in più: è già caricato per la chat. **9 s per immagine** |

### 6.2 Le configurazioni

| Hardware | Chat | Descrizioni immagini | Lettura documenti | Tutto in VRAM? |
|---|---|---|---|---|
| **16 GB — velocità** (oggi) | Qwen3-8B, 16k contesto | VLM piccolo dedicato | in GPU, sempre | **sì**: 4,1 + 2,3 + 0,44 + 2,7 ≈ 9,6 GB |
| **16 GB — qualità chat** | Qwen3 27B | dal 27B stesso, in un secondo passaggio | in GPU, ma **solo** quando il 27B è scaricato (finestra notturna) | no: si alternano |
| **24 GB** | Qwen3 27B | dal 27B stesso | in GPU, sempre | **sì**: 13,9 + 0,44 + 2,7 ≈ 17 GB |
| **32 GB** | 27B con contesto ampio e più slot | dal 27B | in GPU, sempre | **sì**, con margine per 5 utenti |
| **Due GPU** (anche una piccola in più) | su GPU 1 | su GPU 2 | docling-serve su GPU 2 | **sì**, e i due lavori non si disturbano mai |

### 6.3 Cosa si cambia, riga per riga

| Configurazione | `.env` |
|---|---|
| Chat | `MODELLO_RAGIONAMENTO=lm_studio/<id>` (l'id esatto da `lms ls`) |
| Descrizioni con VLM dedicato | `VLM_DESCRIZIONI=api`, `VLM_MODELLO=<id>`, `VLM_URL=http://host.docker.internal:1234/v1` |
| Descrizioni spente | `VLM_DESCRIZIONI=off` (le immagini si estraggono lo stesso) |
| Lettura su GPU locale | `DOCLING_URL=` vuoto, `GPU=auto`, `VRAM_MINIMA_MB=3500` |
| Lettura su seconda GPU o server | `DOCLING_URL=https://<host>:7443/docling` |
| Convivenza impossibile (16 GB con il 27B) | `FINESTRA_NOTTE=22:00-06:00`, `MB_MAX_DI_GIORNO=5` |
| Convivenza possibile (24 GB+, due GPU) | `FINESTRA_NOTTE=` vuoto, `MB_MAX_DI_GIORNO=0`: l'indicizzazione lavora sempre, senza mai toccare la chat |

### 6.4 Quello che manca al quadro

- **Descrizioni fatte dal modello di chat** (configurazioni «16 GB qualità», «24 GB», «32 GB»): il codice oggi sa chiedere le descrizioni **solo durante la lettura**, a un VLM dedicato. Il passaggio in sottofondo — prendere le immagini già salvate, farle descrivere dal modello di chat quando è caricato, aggiungere i pezzi all'indice — va scritto: serve una colonna `descrizione` su `immagini`, il completamento (come `completa_vettori`) e un freno per non rubare il modello a chi sta chattando.
- **Qwen2.5-VL** non è mai stato provato: è il candidato per la configurazione «16 GB velocità», dove glm-ocr non basta perché non descrive.
- **Le due GPU** non sono provate: il codice le regge già (`DOCLING_URL`), ma nessuno ha verificato la configurazione reale.

---

## 7. Il server dei modelli: llama-swap, fuori da Docker

*Cambiato il 21/09/2026. Prima: LM Studio come server, sulla porta 1234.*

### 7.1 Perché

Due motivi, entrambi misurati.

**Il rerank.** LM Studio non espone nessun endpoint di reranking:
`POST /v1/rerank` risponde *"Unexpected endpoint"*. Il modello `bge-reranker-v2-m3`
era già scaricato e inutilizzabile. Con llama.cpp funziona: 2,6 s su 40 brani,
e discrimina bene (+5,54 per una corrispondenza vera contro −11,04 per una
frase fuori tema).

**I modelli non devono girare in Docker.** Su Windows, Docker Desktop si tiene
memoria e GPU e non le restituisce: la macchina si siede. I modelli stanno
sull'host, i container li raggiungono con `host.docker.internal` — che è già il
modo in cui il progetto funzionava con LM Studio.

### 7.2 Cos'è

**llama-swap** (MIT, un binario Go) davanti a **llama-server** di llama.cpp
(MIT, build ufficiale CUDA per Windows). Fa quello che faceva LM Studio — un
endpoint solo, i modelli si caricano su richiesta, TTL per scaricarli — più
`/v1/rerank`, e con un'interfaccia web che mostra anche le richieste e le
risposte, utile quando una risposta arriva vuota.

```
%LOCALAPPDATA%\llama-stack\
    bin\llama-server.exe      build ufficiale CUDA + DLL del runtime
    llama-swap.exe
```

Configurazione: `Sviluppo/modelli/llama-swap.yaml` (nel repository).
Avvio:

```
%LOCALAPPDATA%\llama-stack\llama-swap.exe ^
  --config "...\Sviluppo\modelli\llama-swap.yaml" --listen 127.0.0.1:1235
```

Interfaccia: <http://127.0.0.1:1235/ui/>

### 7.3 Cosa NON cambia

I **nomi** dei modelli sono identici a quelli che esponeva LM Studio, di
proposito: `litellm-config.yaml` e il codice non cambiano di una riga. Si
sposta solo la porta, da 1234 a 1235.

I file `.gguf` restano nella cartella di LM Studio: **niente da riscaricare**.
LM Studio resta installato come **gestore** dei modelli — è comodo per
scaricarne di nuovi — ma non fa più il server. Se un giorno esce di scena, si
sposta la cartella e si cambia la macro `modelli` nel YAML.

### 7.4 Il modello di chat si scarica in un altro modo

`ingestion/indicizza.py` scarica il modello di chat per far posto a Docling
(`scarica_llm`, lock `BLOCCO_LLM`). Usava l'SDK Python di LM Studio; ora usa
`POST /api/models/unload/<id>` di llama-swap. Variabili: `MODELLI_HOST` e
`MODELLO_CHAT` (prima `LMSTUDIO_HOST`).

Ricaricarlo continua a non essere compito dell'indicizzazione: llama-swap lo
riavvia alla prima richiesta con i parametri del suo YAML. È la stessa
proprietà che aveva il caricamento su richiesta di LM Studio, ed è il motivo
per cui quella funzione scarica e basta.

### 7.5 VRAM

Misurato dopo il passaggio, con tutti e quattro i modelli configurati e la
chat in uso: **11,3 GB su 16,3**. Lo YAML li tiene tutti e quattro nello stesso
gruppo (`swap: false`), perché insieme stanno in 8,7 GB e lasciano spazio a
Docling (picco misurato 6,1 GB). Se non dovesse bastare, la modifica è una
riga: `swap: true` fa in modo che chat e VLM non siano mai caricati insieme,
al prezzo di una ricarica a ogni passaggio.

### 7.6 Provato

Tutti e quattro i ruoli, attraverso llama-swap, il 21/09/2026:

| | Esito |
|---|---|
| Descrizione immagini (Qwen3-VL 4B + `mmproj`) | trascrive il tedesco del catalogo: *"Kugel, 20 cm, in PoS-Disp., Glas…"* |
| Rerank (bge-reranker-v2-m3) | −0,73 per le pietre rosse contro −11,01 per una frase su Parigi |
| Embedding (bge-m3) | 1024 dimensioni, attraverso LiteLLM |
| Chat (Qwen3 8B GSQ) | risponde attraverso LiteLLM |

Il VLM era il rischio vero: è l'unico che ha bisogno del file `mmproj` per
vedere davvero le immagini invece di trattarle come testo.

### 7.7 Avvio automatico

llama-swap non e' un servizio Windows: e' un programma. Per farlo partire da
solo c'e' un'**operazione pianificata** chiamata `llama-swap`, che scatta
all'accesso dell'utente e riprova tre volte se fallisce.

```
schtasks /query /tn llama-swap          stato
schtasks /run   /tn llama-swap          avvia adesso
schtasks /end   /tn llama-swap          ferma
schtasks /delete /tn llama-swap /f      toglie l'avvio automatico
```

Oppure da `taskschd.msc`, cercando `llama-swap`.

Avvio a mano, se serve:

```
%LOCALAPPDATA%\llama-stack\llama-swap.exe ^
  --config "C:\Progetti\RAG Aziendale\Sviluppo\modelli\llama-swap.yaml" ^
  --listen 127.0.0.1:1235
```

Come si vede se e' su: <http://127.0.0.1:1235/ui/>, oppure
`curl http://127.0.0.1:1235/v1/models`. Se i container rispondono
"Connection refused" verso `host.docker.internal:1235`, e' questo che manca.

### 7.8 TTL zero: tutti i modelli restano in memoria

Scelta del 21/09/2026: nessun modello si scarica da solo per inattivita'. Il
costo di una ricarica non e' l'attesa in se' — e' che cade addosso a chi sta
facendo la domanda, e durante una lettura di documenti si paga a ogni blocco.

**Misurato con tutti e quattro residenti: 11,1 GB su 16,3.**

Restano 5,1 GB liberi, e qui c'e' un fatto da tenere presente: **il picco
misurato di Docling e' 6,1 GB**. Con tutti e quattro caricati, la lettura dei
documenti NON ci sta, e ripiegherebbe sul processore.

La valvola esiste gia' e continua a funzionare: l'indicizzazione scarica il
modello di chat (4,1 GB) prima di leggere, lasciando 9,2 GB liberi — e
`ttl: 0` non lo impedisce, perche' vuol dire «non scaricarlo da solo», non
«non si puo' scaricare». llama-swap lo ricarica alla prima domanda successiva.
