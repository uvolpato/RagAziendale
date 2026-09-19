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

## Amministrazione

Chi ha un ruolo `admin-*` dopo il login vede la scelta **Chat / Amministrazione**;
gli operatori vanno dritti in chat, senza pagine intermedie (decisione 55).

**https://assistente.localhost/amministrazione/** — servizio `amministrazione/`
(FastAPI). Schermate: Panoramica, Utenti, Gruppi, Profili amministrativi, Fonti,
Vedi come, Anomalie, Registro modifiche, Aspetto. Utenti e gruppi si gestiscono
qui con le API di Keycloak (decisione 68); importazioni in Dagster e monitoraggio
in Uptime Kuma restano collegamenti (decisione 60).

```bash
py amministrazione/esempio.py              # dati di esempio (solo sviluppo)
py amministrazione/esempio.py --rimuovi    # li toglie (il registro resta: non si cancella)
```

Le **aziende** si creano da *Impostazioni → Aziende* (Superutente): riga nel
database e gruppo Keycloak `azienda-<codice>` insieme; ogni azienda ha il suo
connettore al gestionale (decisione 69).

Amministratori di prova: `prova.super` (Superutente: tutto), `prova.admin`
(completo, senza struttura), `prova.accessi` (Gestione accessi, solo Luis),
`prova.revisore` (sola lettura). Password in `CREDENZIALI-SVILUPPO.md`.

**Gestori di gruppo** (`amministrazione/gestori.py`): chi sta in
`<gruppo>-gestori` entra nel pannello con la sola voce *I miei gruppi* e
aggiunge o toglie i colleghi di `<gruppo>`, e di nessun altro. Lo fa rispettare
Keycloak (deleghe FGAP), non il pannello. Di prova: `prova.rspp` (gestore di
`sicurezza`) e `prova.sicurezza` (addetto). Per creare un gruppo con i suoi
gestori, il Superutente crea in *Gruppi* `qualita` e `qualita-gestori` e ci
mette le persone: le deleghe si riallineano da sole. **Dopo aver creato persone
nuove** il gestore non le può aggiungere finché il Superutente non preme
*Riallinea i gestori* (o al prossimo avvio): Keycloak valuta la singola persona
solo con un permesso che la elenca (spiegazione in `gestori.py`).

## Cartelle dei documenti

Una cartella per gruppo (decisioni 61-64 e 71): chi sta nel gruppo vede i
documenti nell'assistente e li deposita nella cartella; se esiste
`<gruppo>-gestori`, i gestori la vedono e ne rispondono. Si collega da
*Fonti → Nuova fonte → Cartella*: nasce **in attesa**, si indicizza subito, e
l'assistente la usa solo quando qualcuno la attiva.

- **Dove stanno**: sotto `CARTELLE_PATH` (in sviluppo `Sviluppo/cartelle/`,
  fuori da git), montata in sola lettura nel servizio `ingestion`. Il percorso
  di una fonte è relativo, es. `luis/sicurezza`. In produzione: la
  condivisione di rete montata sull'host (D15: file server o Nextcloud).
- **Cosa si legge**: PDF (anche scansioni, con OCR), Word, PowerPoint, HTML,
  Markdown, testo, immagini. **Non** si leggono le sottocartelle che iniziano
  con `_` (`_bozze`, `_archivio`) né i fogli di calcolo (i prezzi vengono dal
  gestionale).
- **Servizio `ingestion`** (`ingestion/indicizza.py`): un giro ogni 5 minuti
  (`INTERVALLO_INDICIZZAZIONE`). File nuovi e cambiati entrano, i cancellati
  escono, gli illeggibili e i doppioni diventano anomalie del pannello. I
  vettori li chiede a LiteLLM (nome logico `embedding`), come l'orchestratore:
  il modello si cambia solo in `litellm-config.yaml`, e se cambia rispetto
  all'indice si fermano i vettori con un'anomalia critica. Se LM
  Studio è spento i pezzi entrano senza vettori (la ricerca testuale li trova
  già) e i vettori si aggiungono al giro dopo. I modelli di Docling sono
  nell'immagine: a regime non esce su internet.

```bash
docker compose logs -f ingestion                                          # cosa sta facendo
docker compose run --rm ingestion python indicizza.py --una-volta         # un giro subito
```

La verifica (`eval/verifica_cartelle.py`) prepara da sé la cartella di prova
`Sviluppo/cartelle/luis/sicurezza/` (PDF da `documenti_test/campione/`, che si
rigenerano con `genera_campione.py`), collega la fonte `sicurezza-luis`, la
indicizza e la attiva.

## Connettori ai gestionali

`connettori/` importa i dati dei gestionali nel modello canonico
(`MODELLO-DATI-GESTIONALE.md`, `SPECIFICA-CONNETTORI.md`). Il motore
(`base.py`) fa lo storico SCD2, i controlli e le anomalie; il connettore
(`connettori/<tipo>/`) traduce e basta.

```bash
py -m connettori.esegui --azienda luis --entita soggetti --completa   # importazione da riga di comando
```

- **Integra**: contratto completo (prova con verifica della sola lettura,
  estrai da vista); delle viste sono scritte quelle dell'anagrafica come
  riferimento. Le altre vanno scritte e **verificate contro lo schema reale**,
  non raggiungibile in sviluppo. Vedi `connettori/integra/README.md`.
- **`prova`**: connettore sintetico in memoria, per i test del motore.
- **Collegamenti** (decisione 70): il Superutente li crea dal pannello
  (*Impostazioni → Gestionali*), con "Prova collegamento" che verifica la sola
  lettura; le credenziali sono **cifrate nel database** con `CHIAVE_CREDENZIALI`,
  che possiede solo il servizio `connettori` (`servizio-connettori/`). Il
  pannello inoltra le password e non le vede mai (API `api/collegamenti`, area
  registro `gestionali`). L'abbinamento alle aziende sta in *Aziende*: si sceglie
  il collegamento e il codice nel gestionale viene scoperto dal collegamento.
- Credenziali in `.env` (`CONNETTORE_<TIPO>_*`) solo per la CLI
  `py -m connettori.esegui`, prima del pannello.
- **Importazione programmata** (decisione 60): la pianificano **Dagster**
  (servizi `dagster-webserver`/`dagster-daemon`, code location in `dagster/`,
  UI su `https://dagster.localhost`) e lo **scheduler ponte** `connettori/scheda.py`
  (disattivato con `SCHEDULER_OFF=1` quando gira Dagster). Entrambi chiamano lo
  stesso `/v1/esegui-dovute`: importano le entità dovute secondo le frequenze
  (`pianificazioni`, predefinite §7.5). Lo stato finisce in
  `erp.sincronizzazioni` e le anomalie in `anomalie`, visibili nella schermata
  **Importazioni** del pannello e in Panoramica. Guida: `dagster/README.md`.

⚠ **Il gate filtra nella query SQL** per gruppo, stato della fonte e azienda
(`recupero.py`: `acl_groups && gruppi AND aziende && aziende AND stato = 'attiva'`,
decisioni 53 e 67; test T1.14–T1.17 in `test_gate.py`). Le fonti sospese o in
attesa e quelle di un'altra azienda non rispondono.

## Verifiche

```bash
py eval/verifica_token.py                          # T1.1, T1.3  forma dei token
./.venv/Scripts/python.exe eval/verifica_login.py  # T1.4  login SSO completo
./.venv/Scripts/python.exe -m orchestratore.test_gate   # T1.6-T1.17  gate, ACL, egress
./.venv/Scripts/python.exe eval/verifica_deleghe.py     # deleghe Keycloak: nessuno si alza i permessi
./.venv/Scripts/python.exe -m amministrazione.test_amministrazione   # permessi, contrasto, DB
./.venv/Scripts/python.exe eval/verifica_amministrazione.py          # login, permessi sulle API, flussi
./.venv/Scripts/python.exe eval/verifica_cartelle.py   # cartella di gruppo: pannello, indicizzazione, gate, gestore
./.venv/Scripts/python.exe ingestion/test_indicizza.py # regole dell'indicizzazione (cosa si legge, pezzi)
./.venv/Scripts/python.exe -m connettori.test_connettori    # motore SCD2, connettori (sintetico)
./.venv/Scripts/python.exe -m connettori.test_collegamenti # segreti cifrati, catalogo, vincoli DB
./.venv/Scripts/python.exe -m connettori.test_servizio     # servizio connettori: niente segreti in chiaro
./.venv/Scripts/python.exe -m connettori.test_scheda       # scheduler e fotografia (giacenze)
./.venv/Scripts/python.exe -m connettori.conformita --tipo prova   # kit di conformita'
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
| Ingestion | Cartelle locali sì (`ingestion/`). Mancano: la condivisione vera (D15), gli altri connettori (SharePoint, Google Drive…), l'"indicizza ora" dal pannello |
| Importazione ERP reale | il motore `connettori/`, Dagster e le viste Integra ci sono; mancano la **verifica delle colonne** sullo schema reale, un gestionale raggiungibile con utente di sola lettura, e (per `documenti`) il filtro per `ciclo` nella rilevazione delle cancellazioni |

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
| `docker compose cp` fallisce con `read-only file system` | Il container ha bind mount in sola lettura (tema, logo). Per dare un file a `kcadm` si passa da stdin: `kcadm.sh ... -f - < file.json` |
| Chi gestisce gli accessi si aggiunge al profilo "Amministratore completo" | Con i ruoli classici (`manage-users`) le deleghe non vengono valutate. `admin-accessi` NON ha ruoli di realm-management: permessi FGAP v2 in `keycloak/deleghe.py` |
| La password di un amministratore si reimposta da "Gestione accessi" | Un `reset-password` esplicito su tutti gli utenti scavalca il divieto sui gruppi. Senza, Keycloak lo ricava da `manage` e il divieto vale |
| Modifiche al template del realm che non compaiono | `--import-realm` non tocca un realm esistente: `keycloak/deleghe.py` (a ogni avvio) lo allinea, solo aggiungendo |
| Dopo il login la chat arriva con 7 redirect invece di 4 | Il callback passa dal portale (`/amministrazione/dopo-login`), che decide chat o scelta. Nessuna pagina disegnata: e' il costo della scelta per gli amministratori |
| Permesso delegato rifiutato con `400 unknown_error` | Le deleghe v2 accettano solo `decisionStrategy: UNANIMOUS`. "Uno qualsiasi di questi ruoli" = UNA politica che elenca piu' ruoli |
| Il Revisore elenca gli utenti ma prende 403 aprendone uno | In Keycloak 26.7 la lettura delegata "di tutti gli utenti" vale per gli elenchi, non per `/users/{id}` e `/users/{id}/groups`. Il pannello costruisce l'indice solo con letture di elenco (utenti, gruppi, membri) |
| Il Revisore non legge un gruppo-profilo | Su un gruppo con permessi SPECIFICI quello generale non vale piu', nemmeno in lettura: serve un permesso di lettura specifico anche li' |
| Un amministratore con ruolo assegnato direttamente non e' protetto | I divieti stanno sui gruppi-profilo: i ruoli `admin-*` si danno solo tramite profili. `verifica_deleghe.py` fallisce se ne trova uno diretto |
| Uscendo dalla console di Keycloak il pannello restava aperto ma non funzionava | Logout = fine della sessione SSO: al primo 401 da Keycloak il pannello chiude la propria sessione e rimanda al login |
| La lettura completa di `documenti_vendita` cancella i documenti di acquisto | `documenti_vendita` e `documenti_acquisto` condividono le tabelle `documenti`/`documenti_righe`: la rilevazione delle cancellazioni va filtrata per `ciclo`, altrimenti l'importazione di uno segna come sparito l'altro. `base.py` oggi non la supporta: documenti in un secondo momento |
| Un connettore emette una colonna fuori dal modello e il motore non protesta | Il motore inserisce solo le colonne canoniche e ignora il resto: a prendere l'intrusione (colonne sconosciute, IBAN) e' il **kit di conformita'**, non l'importazione |
