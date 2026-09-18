# Assistente aziendale — ambiente di sviluppo

**Doppio clic su `avvia.cmd`** nella radice del progetto. Controlla Docker e
Python, alza tutto in ordine, apre il browser. Se qualcosa non va, dice cosa
e resta aperto.

`ferma.cmd` ferma i servizi lasciando i dati nei volumi.

Da riga di comando, l'equivalente:

```bash
cd Sviluppo
cp .env.example .env     # solo la prima volta, poi compilare
py avvia.py
```

Poi apri **https://assistente.localhost** — vai diretto a Keycloak, senza
pagina di login intermedia.

**Le credenziali stanno in `CREDENZIALI-SVILUPPO.md`** — utenti di prova, admin
di Keycloak, connessione al database, comando per il certificato. È
**rigenerato da `avvia.py` a ogni avvio** leggendo `.env`, quindi non può
diventare obsoleto, ed è in `.gitignore`.

> Il browser avvisa sul certificato: è la CA interna di Caddy. Per togliere
> l'avviso, importa `certs/caddy-root.crt` fra le autorità radice fidate.

## Verifiche

```bash
py eval/verifica_token.py                          # T1.1, T1.3  forma dei token
./.venv/Scripts/python.exe eval/verifica_login.py  # T1.4  login SSO completo
./.venv/Scripts/python.exe -m orchestratore.test_gate   # T1.6-T1.14  gate, ACL, egress
py eval/smoke_embeddings.py                        # T0.1  embedding su LM Studio
```

Le prime due volte serve il venv: `uv venv .venv && uv pip install --python .venv psycopg[binary] "pyjwt[crypto]" httpx`

## Personalizzazione cliente

Nuovo cliente = **un blocco di variabili in `.env` + due file in `branding/`**.
Nessun fork, nessun rebuild dell'immagine.

### Immagini — un file per cosa, valido su chat e login

| Cosa | File da sostituire | Dove si vede |
|---|---|---|
| **Logo** | `branding/logo.png` | tile 48×48 sopra il titolo del login, e logo della chat |
| **Icona dell'indirizzo** (favicon) | `branding/favicon.ico` | scheda del browser, su entrambe le pagine |

Un solo file per entrambe le destinazioni: il compose lo monta dove serve —
la favicon su **sei** percorsi diversi (le cinque taglie di LibreChat più il
`.ico` di Keycloak), perché i browser la scalano da sé.

⚠️ I gemelli in `branding/tema-keycloak/login/resources/img/` sono solo
**punti di innesto** per quei mount: modificarli non ha effetto.

### Testi — in `.env`

| Cosa | Variabile | Dove si vede |
|---|---|---|
| **Nome azienda** | `NOME_AZIENDA` | `displayName` del realm → titolo della finestra del login |
| Nome assistente | `NOME_ASSISTENTE` | titolo della chat e voce del modello |
| Messaggio di benvenuto | `MESSAGGIO_BENVENUTO` | chat (accetta `{{user.name}}`) |
| Piè di pagina | `PIE_DI_PAGINA` | chat |
| **Disclaimer da accettare** | `TITOLO_DISCLAIMER`, `TESTO_DISCLAIMER` | modale al primo accesso |
| Link condizioni e privacy | `URL_DISCLAIMER`, `URL_PRIVACY` | facoltativi |

### Testi della pagina di login — nei messaggi del tema

Non stanno in `.env`: sono tradotti, quindi vivono in
`branding/tema-keycloak/login/messages/messages_{it,en,de}.properties`.

| Cosa | Chiave |
|---|---|
| Titolo | `loginAccountTitle` |
| **Sottotitolo** | `azSottotitolo` |
| Nota sotto il pulsante | `azNotaSso` |
| Etichette, "Ricordami", "Password dimenticata?" | chiavi standard di Keycloak |

⚠️ **L'apostrofo va raddoppiato** (`all''assistente`): con uno solo la riga
viene ignorata in silenzio.

### Aspetto della pagina di login

`branding/tema-keycloak/` — vedi il README lì dentro, è il brief per il
designer. È un **tema Keycloak**, non LibreChat: la pagina di login non
appartiene alla chat.

`avvia.py` rende `librechat.yaml` dal template a ogni avvio: il file reso non
va modificato a mano, si perde.

## Cosa non c'è ancora

| | |
|---|---|
| Endpoint `/v1/chat/completions` | `orchestratore/` ha gate, ACL ed egress con i test; manca il server HTTP. **Quindi la chat si apre ma i messaggi danno errore** |
| Modello | `MODELLO_RAGIONAMENTO` in `.env` è vuoto: è una scelta di progetto (§11.9 dell'analisi) |
| Ingestion | `ingestion/` da scrivere. Serve la cartella dei documenti |

## Se qualcosa si rompe

```bash
py avvia.py --stato              # cosa gira, cosa manca
docker compose logs librechat | grep -i openid
docker compose logs keycloak | grep -iE "error|import"
docker compose down -v && py avvia.py    # ricostruisce tutto da zero
```

⚠️ `down -v` cancella anche il database: in sviluppo va bene, altrove no.

## Trappole già incontrate, per non ricascarci

| Sintomo | Causa |
|---|---|
| `Invalid user credentials` con la password giusta | Keycloak 26 non sostituisce le variabili nei file di import → il realm si rende da template |
| `Unrecognized field` all'import del realm | JSON non ammette commenti, e Keycloak rifiuta le chiavi ignote → note in `keycloak/README.md` |
| `only requests to HTTPS are allowed` | openid-client accetta la discovery OIDC solo su HTTPS → LibreChat passa da Caddy e si fida della sua CA |
| `Offline tokens not allowed` al callback | Gli utenti importati non avevano `default-roles-azienda`, che contiene `offline_access` |
| Dopo il login, ogni redirect chiede **di nuovo la password** | Da Keycloak 26.1, un login con scope `offline_access` crea solo la sessione offline e **cancella quella SSO**. Lo scope di LibreChat non include `offline_access`: il refresh token online basta |
| `avvia.py` fa partire LibreChat mentre Keycloak risponde 503 | Da Keycloak 26.x "Listening on" arriva prima della fine dell'avvio: si aspetta "Bootstrap completed" |
| `autenticazione con password fallita` su Postgres | Due cluster PostgreSQL nativi occupano 5432 **e** 5433 → in sviluppo si usa 55432 |
| `getaddrinfo failed` su `*.localhost` | I browser lo risolvono (RFC 6761), il resolver di Windows no → gli script lo mappano a 127.0.0.1 |
| `Ignoring extra certs ... No such file` | Bind mount non ancora visibile all'avvio → `avvia.py` riavvia LibreChat una volta |
| Clicco **Disconnetti** e rientro in chat | LibreChat chiude solo la propria sessione; quella SSO di Keycloak resta aperta e il redirect automatico rientra senza chiedere nulla → Caddy intercetta `/login` e passa dall'`end_session_endpoint` |
| Una pagina **lampeggia** per un istante | `OPENID_AUTO_REDIRECT` agisce lato client: la SPA si disegna e solo poi salta a Keycloak → Caddy fa un 302 incondizionato sulla radice e riscrive l'atterraggio del callback su `/c/new`, così la catena termina sempre |
| Ciclo di redirect **invisibile ai test** | Un redirect sulla radice condizionato al cookie sembra corretto lato server ma cicla nel browser: i cookie sono `SameSite=Strict` e `sso.localhost` è un sito diverso da `assistente.localhost`. **httpx non implementa SameSite**, quindi i test passavano. Soluzione: nessuna condizione sul cookie, e atterraggio su un percorso diverso da `/` |
| `Too many login attempts` su pagina bianca | Rate limiter di LibreChat: 7 tentativi ogni 5 minuti, e i **ban durano 2 ore**. In sviluppo `LOGIN_MAX=100`, `LOGIN_WINDOW=1`, `BAN_VIOLATIONS=false` |
| Modifiche al tema Keycloak che non compaiono | Keycloak mette i temi in **cache**: in sviluppo `KC_SPI_THEME_CACHE_THEMES=false` |
| `loginTheme` non applicato dopo la modifica al template | `--import-realm` non tocca un realm esistente: `docker compose down -v && py avvia.py`, oppure `kcadm update realms/azienda -s loginTheme=azienda` |
