# Specifica — Gli strumenti dell'agente, condizionati dal livello dell'utente

Stato: **bozza da valutare** (28/09/2026).

Scopo: definire quali strumenti ha l'agente (il modello che decide la ricerca) e
come ogni strumento dipende dal **livello dell'utente**. Il livello non lo
inventa l'orchestratore: lo decide **l'SSO** (Keycloak), che è l'unica fonte di
verità su identità e ruoli.

## Principio guida

L'agente riceve gli strumenti che il SUO utente può usare, e niente di più.
Il modello non può chiamare uno strumento che non gli è stato passato: quindi
il privilegio non si scala, per costruzione — non serve un `if` di controllo
dopo, perché lo strumento proprio non c'è.

Il perimetro di lettura (quali documenti vede) resta nell'**ACL della query**
(`gruppi` × `aziende`, già così). Questo documento riguarda **quali strumenti
esistono**, non quali righe toccano.

## Gli strumenti, divisi per livello

### Livello 1 — lettura (qualunque utente autenticato)

Gira già, e filtra con l'ACL:

| strumento | cosa fa |
|---|---|
| `cerca` | cerca nel testo dell'archivio |
| `cerca_figure` | cerca i prodotti per attributo visivo (colore, forma) |
| `cerca_esatta` | cerca un codice/termine esatto |
| `grep` | conta/dove compare una parola (per capire se una cosa esiste) |
| `pagina` | legge tutti i passi di una pagina |
| `documenti` | elenca i documenti visibili e cosa contengono |

### Livello 2 — scrittura (ruolo «editore» o equivalente)

Non esiste ancora:

| strumento | cosa fa |
|---|---|
| `salva_ricerca` | riassume domanda+risposta in un `.md` e lo scrive nella cartella della conoscenza |
| `salva_nota` | scrive una nota libera nella conoscenza |

Il file scritto entra nell'indicizzazione al giro dopo e diventa conoscenza
aziendale cercabile.

### Livello 3 — esterno (ruolo «internet» o equivalente)

Non esiste ancora:

| strumento | cosa fa |
|---|---|
| `cerca_web` | cerca su internet, dentro l'allowlist di egress già esistente |

## Il modello dei permessi: SSO decide

Il token (JWT di Keycloak) porta i **gruppi** dell'utente. L'orchestratore già
li legge (`_gruppi_della_richiesta`). La mappa è:

| gruppo (nel token) | strumenti aggiunti |
|---|---|
| qualunque gruppo | lettura (livello 1) |
| gruppo scrittura (nome da decidere) | + `salva_ricerca`, `salva_nota` |
| gruppo internet (nome da decidere) | + `cerca_web` |

Oggi i gruppi che conosciamo: `acquisti`, `magazzino`, `azienda-*` (perimetro
dati), `amministratori` (pannelli di amministrazione). Per i livelli 2 e 3 si
propone di aggiungere in Keycloak uno o due gruppi nuovi — è una decisione di
SSO, non di codice.

## Meccanismo di implementazione (solo schema)

1. `STRUMENTI` in `agente.py` smette di essere una lista statica e diventa una
   funzione dei gruppi del token: `strumenti_per(gruppi)`.
2. `_esegui` riceve solo gli strumenti della lista: uno strumento non presente
   non è eseguibile.
3. La scrittura (`salva_*`) scrive nella cartella della conoscenza. Vincolo noto:
   l'orchestratore monta `cartelle` in sola lettura, quindi serve un mount
   annidato in scrittura SOLO sulla cartella della conoscenza (o un meccanismo
   equivalente). Da decidere dove e con quale fonte.

## Decisioni aperte (da valutare insieme)

1. **Nome dei gruppi** per scrittura e internet: `editori` / `amministratori`?
   riusiamo un gruppo esistente o ne creiamo di nuovi in Keycloak?
2. **Il livello internet** va solo agli amministratori, o a un gruppo dedicato?
3. **Cartella della conoscenza**: per azienda (`decobrands/knowledge`) o unica
   condivisa? E con quale ACL?
4. **Cosa riassume `salva_ricerca`**: solo la risposta appena data, o l'intero
   scambio del turno?
5. **Ordine di priorità**: da dove si comincia? (proposta: `salva_ricerca`,
   perché era la richiesta iniziale).
