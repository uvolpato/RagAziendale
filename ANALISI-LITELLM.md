# Analisi — integrare LiteLLM nel pannello

**Stato:** analisi — 18/09/2026
**Decisioni collegate:** 13/40 (LiteLLM proxy, nomi logici), 38 (modello scelto dal
committente), 60 (strumenti esterni dietro oauth2-proxy), 68 (integrazione di
Keycloak nel pannello)
**File:** `litellm-config.yaml`, `docker-compose.yml` (servizio `litellm`),
`amministrazione/app.py` (STRUMENTI), `orchestratore/` (consuma `/v1/*`)

Questa analisi risponde alla domanda: **quali "funzioni base" di LiteLLM vanno
integrate nel pannello, e come**, applicando lo stesso criterio usato per
Keycloak (decisione 68).

---

## 1. Cosa è LiteLLM qui, in una riga

Il **proxy di routing dei modelli**: unico possessore delle chiavi dei provider
(decisione 22/40). L'orchestratore conosce solo i nomi logici (`ragionamento`,
`veloce`, `embedding`) e chiama un'API OpenAI-compatible; quale modello e quale
provider rispondano è una riga di `litellm-config.yaml`. Il modello non è scelto
(decisione 38): la rotta `ragionamento` è vuota in `.env`.

---

## 2. La superficie di LiteLLM (le "funzioni base")

LiteLLM espone **tre** cose diverse, da non confondere:

| Superficie | Cosa fa | A chi serve | Integrare? |
|---|---|---|---|
| **API OpenAI-compatible** (`/v1/chat/completions`, `/v1/embeddings`, ...) | fa parlare l'orchestratore col modello | orchestratore | ❌ non è "integrazione", è il contratto interno |
| **API di amministrazione** (REST, autenticata con la master key) | `/key/*` (chiavi), `/model/*` (modelli/rotte), `/spend/*` (costi), `/health/*` (stato), `/user/*` | chi amministra | ✅ è l'equivalente dell'**Admin API di Keycloak** |
| **UI (dashboard)** | mostra modelli, chiavi, costi, log | chi amministra | ✅ è l'equivalente della **console di Keycloak** |

Quindi "integrare le funzioni base" significa lavorare sulle ultime due righe,
**non** sull'API OpenAI (che è solo il protocollo interno fra orchestratore e
proxy).

⚠️ Da verificare sulla versione installata: l'elenco esatto degli endpoint di
amministrazione (`/key/info`, `/key/generate`, `/model/info`, `/spend/logs`,
`/health/liveliness`, ...) e la loro auth (master key `Authorization: Bearer
sk-...`). È una REST API, documentata su docs.litellm.ai.

---

## 3. Il pattern Keycloak, e come si applica

Come abbiamo fatto per Keycloak (decisione 68):

| Keycloak | LiteLLM |
|---|---|
| **Admin API** (utenti, gruppi, ruoli) chiamata dal pannello **col token di chi lavora** | **API di amministrazione** (chiavi, modelli, costi) chiamata dal pannello **con la master key** |
| **Console** → collegamento "Configurazione avanzata" (solo Superutente) | **UI/dashboard** → collegamento "Modelli AI" (dietro SSO, admin) |
| **Realm** importato da file + `deleghe.py` che lo allinea | **`litellm-config.yaml`** + `.env`, oggi solo file |
| Ruoli `admin-*` e deleghe | permessi nel pannello (chi vede modelli/costi) |

**Differenza importante (perché LiteLLM è più semplice da integrare):**
- Keycloak ha un'autenticazione delegata fine (FGAP), LiteLLM ha **una sola
  chiave master**. Il pannello che chiama LiteLLM "con i permessi di chi lavora"
  non è possibile a grana fine: o si usa la master key (e si vedono tutti i
  modelli/costi), o niente. Quindi nel pannello l'integrazione è **in lettura
  per gli amministratori**, non per-operatore.
- Le **chiavi dei provider** stanno solo in LiteLLM (come `CHIAVE_CREDENZIALI`
  nei connettori): il pannello non le vede mai.

---

## 4. Le funzioni base, in ordine di valore

| # | Funzione | Cosa dà | Dove | Quando |
|---|---|---|---|---|
| 1 | **Vedere modelli/rotte e stato** | "quale modello risponde", "è raggiungibile" | pannello (lettura `/model/info`, `/health/*`) + UI | ora |
| 2 | **Vedere i costi** | spend per modello/chiave, per il budget | pannello (lettura `/spend/*`) | fase 2 |
| 3 | **Gestire modelli/rotte** | cambiare modello senza file | pannello (scrittura `/model/*` → riscrive la config) | fase 2, se serve |
| 4 | **Gestire chiavi/utenti del proxy** | creare chiavi con limiti | pannello (scrittura `/key/*`) | fase 2/3 |
| 5 | **Log delle chiamate** | debugging del routing | UI (non nel pannello) | quando serve |

I primi due sono **lettura**: a basso rischio, alto valore (il budget e la salute
dei modelli sono ciò che l'amministrazione vuole vedere subito). Gli altri sono
**scrittura** e vanno fatti solo quando c'è un bisogno reale, non in anticipo.

---

## 5. Raccomandazione

1. **Ora (fatto):** collegamento "Modelli AI" nel pannello → UI di LiteLLM dietro
   oauth2-proxy + Keycloak (`https://modelli.localhost`, solo amministratori),
   come Dagster e Uptime Kuma.
2. **Subito dopo la scelta del modello:** in Panoramica (o in una scheda "Modelli")
   mostrare in **sola lettura** quali modelli sono configurati e se i provider
   rispondono, chiamando l'API di amministrazione con la master key. Una
   funzione del pannello, ~50 righe.
3. **Fase 2:** gestire le rotte dal pannello invece che dal file (come `deleghe.py`
   ha allineato il realm di Keycloak), e mostrare i costi.
4. **Mai dal pannello:** le chiavi dei provider (restano in LiteLLM/`.env`).

**In sintesi:** LiteLLM si integra come Keycloak — UI dietro SSO (link) + API di
amministrazione letta dal pannello (in lettura, non per-operatore per via della
singola master key) — ma con una superficie molto più piccola: modelli, costi,
salute. Non c'è nulla da "riscrivere" nel pannello di ciò che LiteLLM fa già.
