# Cosa produce un giro di indicizzazione

Due destinazioni, e tenerle distinte è la chiave per capire il resto:

1. **l'indice** — la banca dati, cioè ciò che il pannello mostra e che l'assistente
   cerca davvero;
2. **le tracce su disco** — i file di lavoro sotto `<cartella>/_sorgenti/`, che
   servono a non ripagare i modelli e a non rileggere i PDF.

La cosa che va saputa per prima, perché da sola spiega quasi tutte le
dispersioni: **le tracce su disco sono l'impronta del lettore, non del documento**.
Non descrivono il file, descrivono il modo in cui è stato letto.

## 1. Le tracce su disco

Una cartella per documento, sotto la cartella della fonte:

```
<cartella>/_sorgenti/<sha256(nome del file)[:16]>/
```

Il prefisso `_` non è una cortesia per questa cartella: la regamma generale è che
**una cartella il cui nome inizia con `_` non viene letta** (`indicizza.py:18`,
la stessa che esclude `_bozze` e `_archivio`). Il lavorato non entra quindi mai
nell'indice come documento della fonte, e non viene nemmeno contato fra le
sottocartelle nascoste.

| Cartella | Contenuto | Chi la scrive |
|---|---|---|
| `immagini/NN_<n>.png` | le figure ritagliate, già ridotte (`MAX_LATO` 1600 px) | **entrambi** i percorsi |
| `markdown-11f3d27b/NNNN.md` | il testo come l'ha letto il VLM, un file per blocco di `PAGINE_PER_BLOCCO` pagine | solo percorso `pagina` |
| `markdown-figure/NNNN.md` | lo stesso testo con la descrizione di ogni figura intrecciata | solo percorso `pagina` |
| `descrizioni-64d50954.json` | `{nome file immagine: descrizione}`, la cache delle descrizioni | **entrambi** i percorsi |

I due hash **non sono arbitrari**: sono l'impronta del prompt.

| Hash | Prompt |
|---|---|
| `11f3d27b` | `ISTRUZIONI_PAGINA` |
| `64d50954` | `ISTRUZIONI_FIGURA` |

**Due buchi, trovati il 27/09/2026 sera.** Il versioning è gratis e si paga da
solo, ma la chiave è incompleta:

1. **il modello non c'è nella chiave** (`indicizza.py:2100` fa l'hash di
   `ISTRUZIONI_FIGURA` e basta): cambiando modello le descrizioni vecchie
   vengono riusate senza dire niente, e un esperimento sul modello diventa un
   no-op silenzioso;
2. **dentro il documento la mappa è per nome file** (`{nome immagine:
   descrizione}`): nome uguale e immagine cambiata → la descrizione vecchia
   sopravvive, cioè quando si ridisegna una pagina e si rilegge.

E una cosa che va sapere prima di promettere qualcosa: **cambiare un prompt
NON rilegge un documento già indicizzato.** La cache è versionata, ma la
decisione di rileggere guarda solo dimensione, data e hash del file
(`indicizza.py:1205` e `:1216`). Un documento già indicizzato tiene per sempre
le descrizioni del prompt con cui è stato costruito, e toccare il file non
serve. Dopo una modifica al prompt il corpus è quindi **diviso in due senza
dirlo**. Il rimedio è già nel codice per il modello di embedding
(`index_meta`, `indicizza.py:989` e `:998`: se il modello è cambiato non scrive
un vettore in più e apre un'anomalia); va allargato al prompt. Se ne parla in
`Sviluppo/PROBLEMI-APERTI.md` §10.

Cambiare un prompt non sovrascrive niente: crea una cartella nuova, e quella
vecchia **resta per sempre**, perché `_butta_sorgenti` cancella `immagini/` o
l'intera cartella, mai le cartelle di un prompt superato. Dopo qualche mese di
prompt tweaking ci si ritrovano due o tre `markdown-<hash>` e un peso morto
piccolo ma crescente (misurato il 27/09/2026: ~3 MB su 2,5 GB, cioè trascurabile
— ma è rumore, e dice che la pulizia non esiste).

Il nome del file immagine (`0012_7.png`) è **pagina + ordine di comparsa**, non
un'idelta stabile: se il PDF cambia, i nomi cambiano. È il motivo per cui
`_registra_immagini` cancella e riscrive tutte le immagini di un documento a ogni
rielaborazione, e per cui `_butta_sorgenti` esiste.

Il `markdown-*` serve a una cosa sola: rileggere un catalogo costa pochi secondi
invece dei trenta minuti di lettura. Il `descrizioni-*.json` serve allo stesso
scopo per le figure (891 chiamate al modello visivo su un catalogo da 894
immagini, mezz'ora: `indicizza.py:2106`).

## 2. L'indice

Tre tabelle. `documenti` dice *cosa è successo*, `chunks` e `immagini` sono
*quello che si cerca*.

### `documenti` — una riga per file

| Colonna | Significato |
|---|---|
| `stato` | `indicizzato`, `errore`, `vuoto` (nessun testo), `escluso` (regola di sistema, es. prezzi) |
| `impronta` | hash del contenuto: se non cambia, il file non si rilegge |
| `in_lettura`, `pagine_fatte/totali`, `figure_fatte/totali` | l'avanzamento, per il pannello |
| `errore` | il motivo, mostrato in chiaro |

### `chunks` — i pezzi di testo

`content` + `embedding vector(1024)`, con indice full-text italiano (`gin`) e
indice vettoriale HNSW per la cosine. Univoci su
`(source_id, documento, page, content_hash)`, così rileggere lo stesso testo non
duplica nulla.

### `immagini` — le figure

`percorso` (dove sta il PNG), `descrizione` (che cosa rappresenta) e
`embedding vector(1024)`. Univoci su `(source_id, percorso)`.

**Il vincolo che conta**: senza `descrizione` non c'è nemmeno `embedding`, e
senza embedding la figura non è ritrovabile con una domanda. Il pannello
chiama «senza vettori» il caso di una descrizione assente, ed è la ragione per
cui esiste `completa_vettori` (ripara i vettori mancanti) ma **non** ripara le
descrizioni mancanti: quelle le rifà il VLM, e oggi le rifà solo rileggendo il
documento.

## 3. Chi decide come si legge

`come_leggere()` (`indicizza.py:1691`):

```python
return "pagina" if fonte in FONTI_A_PAGINA else LETTURA
```

`FONTI_A_PAGINA` è una **lista scritta a mano** nel `.env`:

```
LETTURA_PAGINA=acquisti-decobrands
```

Quindi «catalogo» non è un concetto del codice: è *questa cartella è nella
lista*. Niente riconosce il documento, e la scelta vale:

- **per cartella**, non per file: una cartella con dentro sia cataloghi che prosa
  non si può spezzare;
- **solo per i PDF** (`indicizza.py:765`): un `.docx` o un `.pptx` dentro
  `acquisti-decobrands` va comunque da Docling.

## 4. I due percorsi a confronto

| | `pagina` (VLM pagina per pagina) | `docling` (parser vero) |
|---|---|---|
| testo | il VLM guarda la pagina resa a immagine | Docling estrae il livello testuale, le tabelle, il layout |
| perché | il testo di un catalogo è una griglia di immagini: il livello testuale contiene solo frammenti di codice articolo, e serve il contesto visivo per associare la figura alle parole | un manuale ha un livello testuale buono: il VLM sarebbe uno spreco |
| costo | ~30 min per 107 pagine | pochi secondi |
| figure | ritagliate e descritte da noi | ritagliate e descritte da noi — **stesso prompt, stesso modello** |
| tracce lasciate | tutte e quattro | `immagini/` e `descrizioni-*.json` |

La differenza sul **testo** è reale e va tenuta. Quella sulle **figure** non
aveva nessuna fondamento e fino al 27/09/2026 ha prodotto un indice con due
stili di descrizione e 23 icone su 25 immagini lasciate senza descrizione
(la presentazione di sicurezza, le cui figure Docling classifica come icone e
banner e non descrive). Da allora la descrizione è **un posto solo**:
`_descrivi_figure` → `_descrivi_col_titolo`, con la cache su disco comune.

Restano due asimmetri minori, entrambe da decidere:

- le figure diventano ricercabili con due meccanismi diversi
  (`markdown-figure` nei cataloghi, `_pezzi_dalle_figure` in prosa). Funzionano
  entrambi, ma è la stessa idea scritta due volte;
- in prosa non c'è la cache `markdown-*`, e **non serve**: Docling rilegge in
  pochi secondi, quindi non c'è niente da pagare due volte.

## 5. Cosa c'è oggi, misurato

27/09/2026, dopo il giro forzato su Muletti. I numeri sono una fotografia: il
`--forza` riapre i documenti anche senza che il file sia cambiato, quindi il
conteggio «senza descrizione» dipende da quanto è avanzato il giro, non dalla
sola qualità dell'indice.

| Fonte | Lettura | Immagini | Senza descrizione |
|---|---|---|---|
| `acquisti-decobrands` | `pagina` | 15 120 | 0 |
| `magazzino-decobrands` | `docling` | 100 + 265 + 266 (in corso) | **0** |
| `sicurezza-decobrands` | `docling` | 25 | 23 |
| `sicurezza-luis` | `docling` | 1 | 1 |

I documenti Muletti riletti col codice unificato:

| Documento | Pezzi | Figure descritte |
|---|---|---|
| `Muletti/51760533.pdf` | 228 | 100 / 100 |
| `Muletti/51814883.pdf` | 401 | 265 / 265 |
| `Muletti/51857958.pdf` | in corso (pagine 266/266, fase figure) | — |
| `Muletti/ATS BG-Manuale Movimentazione e allegati (1).pdf` | non riletto, errore del 27/09 mattina | — |

Restano **24** figure senza descrizione in tutto l'indice: 23 sono le icone
della presentazione di sicurezza (che Docling classifica come icone e banner e
non descrive) e 1 rimasta indietro. Il `--forza` le ha sistemate nei manuali;
**sui cataloghi la precisione è già intera e va tenuta così**.

## 6. Cosa manca

1. **Riparare le descrizioni mancanti senza rileggere il documento.** Le PNG ci
   sono e la cache JSON è lì: manca solo un passaggio come `completa_vettori`,
   che oggi ripara i vettori ma non le descrizioni.
   **Correzione del 27/09 sera:** ripararle è la cosa giusta nei cataloghi (lì la
   figura *è* il prodotto) e in prosa non è automatica — una descrizione in più
   è anche un pezzo di testo in più che compete col testo vero. La domanda da
   fare al modello è «questa figura aggiunge qualcosa che il testo della pagina
   non dice già?», e il suo verdetto decide. Vedi `Sviluppo/PROBLEMI-APERTI.md` §10.
2. **Ripulire le cartelle di prompt superate**, o almeno non generarne una nuova
   a ogni modifica.
3. **Stampare in `index_meta` la versione del prompt delle figure**, accanto al
   modello di embedding che c'è già: senza, cambiando il prompt il corpus si
   divide in due in silenzio. Meccanismo esistente, una colonna in più.

La **lista `LETTURA_PAGINA` è superata**: la domanda utile non è «è un
catalogo?», che nessuno controlla, ma «questa figura aggiunge qualcosa che il
testo della pagina non dice già?», che è una domanda al modello e vale per tutte
le fonti senza elenchi. Il modello del verdetto e l'invariante della precisione
sono in `Sviluppo/PROBLEMI-APERTI.md` §10.

Vedi anche `IMPOSTAZIONI.md` per le manopole e `PRESTAZIONI.md` per i tempi.
