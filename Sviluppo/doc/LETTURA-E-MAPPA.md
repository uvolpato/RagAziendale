# La lettura dei documenti, e la mappa che mente

Preparato il 5/10/2026, dopo il banco a 21/30 e una lettura di come fanno
gli altri. Prima le cose verificate nel codice e nel database, poi le
modifiche in ordine. Ogni numero qui dentro è stato letto, non ricordato.

## Fatto 1 — «il modello non può aprire un documento» è falso

Te l'ho detto ieri e non regge. `cerca` è SQL libero, e `chunks` ha
`documento`, `page`, `content`: un documento si apre già, con

    SELECT page, content FROM chunks WHERE documento ~* 'PROGETTO' ORDER BY page

Quello che è vero è più stretto: il risultato esce **tagliato**, perché
`sql_agente.LIMITE_RIGHE = 40` e `MAX_CARATTERI = 6000` (commento in
sorgente: «non svuotiamo il contesto con 40 righe enormi»). Seimila
caratteri sono una pagina e mezzo: di un documento da 30 KB il modello ne
vede un quinto e non sa che è un quinto.

Quindi non manca la **capacità**: mancano due cose diverse.

1. un percorso di lettura dimensionato per un documento, invece di uno
   dimensionato per quaranta didascalie di catalogo;
2. la frase che dica al modello che quel percorso esiste. Nella mappa
   `chunks` è presentata come un posto dove *cercare*, mai come un
   documento da *aprire*.

## Fatto 2 — la causa misurata di `archivio` 0/3

`orchestratore/mappa.py`, nel prompt del coordinatore, parola per parola:

> «4. indice — l'indice dei cataloghi (che cosa c'è in ogni pagina).
> Colonne: documento, page, testo. Al momento è **VUOTA**, quindi su questo
> **non cercare**: usa le altre tre.»

Letto nel database stasera:

| | mappa | vero |
|---|---|---|
| indice, livello documento | vuota | **50** |
| indice, livello pagina | vuota | **1865** |
| chunks | 17582 | 18973 |
| fonti | «le 14 fonti» | 20 attive, 50 righe |
| immagini | 15143 | 15143 ✓ |

E `<dove_si_cerca>` in `grafo.py` elenca tre tabelle — `immagini`,
`chunks`, `documenti`. `indice` non c'è.

Quindi la mossa `esplora` consegna righe dell'indice a un modello a cui
due prompt hanno detto che l'indice è vuoto e che non è fra i posti dove
si guarda. Il sintomo che il banco ha misurato tre giri su tre —
`analista > coordinatore > esplora > coordinatore > redattore` →
«Non ho trovato nessun catalogo» — è il comportamento **coerente** di un
modello a cui è stato detto che quella roba non esiste.

Da notare: a «quali cataloghi hai?» risponde `SELECT ... FROM documenti`,
50 righe, e la mappa lo dice («per le domande sull'archivio stesso»). Il
modello non ci è arrivato perché `esplora` si vende come l'indice dei
documenti e poi non gli lascia niente in mano.

## Fatto 3 — la classe, non i tre numeri

Il docstring di `mappa.py` promette l'opposto di quello che il file fa:

> «Qui la mappa si ricava dai DATI, non dalla memoria di chi scrive il
> prompt: i conteggi sono veri … Cambiando i cataloghi la mappa cambia con
> loro.»

`mappa_dati()` non prende la connessione e non tocca il database: è una
stringa costante. Era vera il giorno in cui è stata scritta.

**Questa è la classe del difetto**, e i tre numeri sbagliati sono solo il
primo caso che l'ha scoperta: una mappa che dichiara di seguire i dati ed
è una fotografia decade ogni notte in cui l'indicizzazione gira. Correggere
i numeri a mano li rimette giusti per oggi e li rimette sbagliati domani —
è il rattoppo. Calcolarli è la riparazione.

## Come lo fanno gli altri

Misurato o pubblicato, non dedotto:

- **La coppia canonica** è `list_docs()` → metadati e riassunto per
  documento, e `read_doc(doc_name) -> str` → il documento intero. È il
  nostro indice più la mossa che ci manca.
- **A-RAG** dà tre livelli (lessicale, semantico, `chunk_read`) e la
  regola «devi aprire il contenuto intero prima di rispondere». La
  variante col solo embedding search consuma *più* token e rende *meno*.
- **LangChain deep agents**: la ricerca non torna testo, torna
  **percorsi**; il testo viene scaricato e letto a parte, e il contesto
  dell'orchestratore resta pulito. Un tool di elenco non ce l'hanno:
  l'indice è un vantaggio nostro.
- **SERVAL** (EMNLP 2025) è la nostra indicizzazione: un VLM descrive la
  pagina, un text encoder la embedda, si cerca sul testo. Batte gli
  encoder visuali multi-vettore, 63,4 nDCG@5 su ViDoRe-v2. E finisce al
  recupero: nessuna verifica a valle.

In tutti e tre, quello che torna dalla ricerca è **la maniglia del passo
dopo** — un nome, un percorso. E in tutti e tre lo strumento è codice,
mentre la scelta di quando usarlo è prompt. Nessuno mette in un `if`
quando leggere.

## Le modifiche, in ordine, ognuna con la sua misura

Una per volta, e il banco in mezzo: è la regola che stanotte ho violato
attribuendo al cartografo un calo che non era suo.

### 1. La mappa si calcola dai dati — `mappa.py`

`mappa_dati(conn)` legge i conteggi e decide da sé cosa dire dell'indice:
se ha righe lo presenta come un posto dove guardare, se è vuoto lo dice.
Nessuna decisione sottratta al modello: sono fatti, come i campioni di
didascalia che la mappa già pesca dai dati.

Tocca i due chiamanti (`agente.py:762`, `grafo.py:822`), che hanno già la
connessione.

### 2. `indice` entra fra i posti dove si guarda — `grafo.py`

In `<dove_si_cerca>`, con cosa contiene: una descrizione per documento e
un riassunto per pagina, e che non sono righe di prodotto.

### 3. Si misura

Se `archivio` torna a 3/3, la causa era questa, e `leggi` si giudica sul
resto del banco invece che su un caso che non era suo.

### 4. La mossa `leggi` — la lettura vera

`leggi(documento, page?)`: apre un documento (o una pagina) per nome, SQL
puro, le righe entrano nello stato con `documento` e `page` come quelle
di `cerca`, quindi chi scrive le cita come cita tutto il resto.

- è il `read_doc` / `chunk_read` degli altri;
- ha un tetto suo, dimensionato su un documento, non i 6000 caratteri
  pensati per quaranta didascalie;
- **non tocca i cataloghi.** Là la scala è già completa — `esplora`
  mappa, `cerca` righe, `guarda` apre la foto — e lo standard misurato
  resta dov'è.

La scelta di quando leggere sta nel prompt. Nessun `if` nel chiamante.

### 5. Il cartografo torna in gioco

È in `scratchpad/grafo-con-cartografo.py`. A 25/30 non era lui il
problema, e la base intanto si è spostata a 21-22.

## Una cosa mia da disfare

Stamattina ho cambiato il testo degli esiti di `esplora` in «questa è una
MAPPA, non sono righe … il passo che viene adesso è `cerca`». La prima
metà è vera e struttura: quelle righe non entrano in `righe`, e dirlo è
onesto. **L'ultima frase no**: prescrive la mossa successiva, cioè fa
esattamente quello di cui ti lamentavi — gli chiede una scelta e poi la
decide io. Va via quando `leggi` esiste, perché da quel momento il passo
dopo non è sempre `cerca`.

E va detto che l'ho scritta credendo che la causa fosse la frase sulla
citabilità, prima di leggere la mappa. La causa era il Fatto 2.

## Un rischio, non una modifica

Nel database ci sono **tre** tabelle `documenti`: `public` (le fonti
dell'archivio, 50 righe), `erp` (vista) e `erp_storico`. Il modello
scrive `documenti` senza schema e oggi risolve su `public` per
`search_path`. Funziona, ed è fragile: una query che atterra su `erp`
torna imponibili e IVA a chi ha chiesto un catalogo. Da tenere d'occhio,
non da toccare adesso.
