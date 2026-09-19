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
