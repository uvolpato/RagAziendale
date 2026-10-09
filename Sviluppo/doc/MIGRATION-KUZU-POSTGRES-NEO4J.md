# Dal Kuzu a PostgreSQL (o a Neo4j): cosa serve davvero

> Stato: **analisi, nessuna modifica**. Niente e' stato cambiato a runtime e il
> grafo Kuzu attuale resta intatto. Questo documento mette per iscritto cosa
> cambierebbe, in che ordine, e cosa si perde.

## La domanda da cui si parte, riscritta con i numeri

Il testo di partenza chiede se eliminare Kuzu in favore di PostgreSQL o
accoppiarlo a Neo4j. La risposta e' **si', ma non per il motivo addotto, e non
adesso**. Il parallelismo e' il problema, ma quello che misura e' la GPU, non
il database.

Le misure sono reali, non stime: 211 secondi per documento (74 minuti di giro,
21 documenti nuovi, 2 slot reali di llama-swap), ~10 nodi per documento,
~4.100 token per documento.

| Documenti | Giorni di GPU (2 slot) | Nodi stimati | Archi stimati |
|---|---|---|---|
| 1.000 | 2,4 | ~10.000 | ~25.000 |
| 10.000 | 24 | ~100.000 | ~250.000 |
| 100.000 | **245** | ~1.000.000 | ~2.500.000 |

Questi numeri cambiano la priorita'. **Il database non e' il collo di bottiglia
a nessuna di queste scale: lo e' la generazione.** Per arrivare a 100.000
documenti servono 245 giorni di GPU. Un database che regge un milione di nodi
non serve a nulla se non si arriva al milione di nodi.

## I due guasti, da tenere separati

Il testo di partenza li mescola, e per questo rischia di indicare la cura
sbagliata.

**Guasto 1 — la GPU non risponde (quello che c'e' adesso).** 36 chiamate a
`qwen3-14b` in coda contro 2 slot, `429 Too many requests`. Non e' un problema
di database: anche su PostgreSQL, 10 richieste su 2 slot danno 10 errori.
Chiuso lato client (rate limit esplicito, `chunks_per_batch=2`, un solo
`cognify` per giro invece di uno per file). Verificato: **0 `429`** in un'ora di
corsa continua.

**Guasto 2 — letture bloccate dalle scritture (quello che arriverà).** In Kuzu
una transazione di scrittura prende il lock sull'intero file: un utente che
interroga mentre l'ingestione scrive viene servito DOPO lo scrittore. E' un
limite architetturale, non una taratura. PostgreSQL lo risolve con MVCC, Neo4j
perche' ha un modello transazionale diverso. Ma questo guasto diventa
misurabile solo quando l'ingestione gira per settimane — cioe' al punto 3
della scala qui sotto, non al punto 1.

Lo stato di fatto, verificato:

- `llama-swap` `/upstream/qwen3-14b/props` -> `total_slots: 2`.
- Cognee 1.6.2 spara 10 chiamate in parallelo
  (`MAX_CONCURRENT_ENTITY_LLM_CALLS = 10`, costante hardcoded in
  `cognee/tasks/memify/consolidate_entity_descriptions/constants.py`).
- `is_local_llm()` (`cognee/infrastructure/llm/config.py:48`) riconosce come
  locali solo `ollama`, `llama_cpp` e il prefisso `lm_studio/`. Noi siamo
  `custom` con `openai/qwen3-14b`: **non ci riconosce**, tiene il default da
  cloud a 60 RPM. Il valore va scritto per esplicito, altrimenti non viene.
- Volume Kuzu `assistente_cognee-dati`, un file per dataset in
  `/dati/sistema/databases/<system>/<dataset>.pkl`.
- Read-only concorrente verificato: 8 aperture su 8, lock solo transitorio.

## Perche' gli schemi attuali non reggono la scala che hai in mente

Tre cose che vanno rifatte ben prima di migrare il database, e che costano
meno:

1. **L'ingestione e' tutto-or-nothing.** Oggi `cognify` gira su un dataset
   intero e fallisce se un documento fallisce. Su 100.000 documenti serve a
   lotti, con coda e ripresa: un batch fallito non deve ricominciare tutto.
   E i dataset gia' pronti devono rispondere agli utenti mentre gli altri si
   ingeriscono.
2. **Il grafo non viene interrogato per quello che e'.** Un milione di nodi con
   query ricorsive in Postgres e' lento; in Neo4j e' il caso d'uso naturale.
   Ma a 10.000 nodi la differenza non la senti, e oggi ne abbiamo 366.
3. **La GPU e' il tetto, e si alza.** Oggi 2 slot su 14 GB. Il 70B Q4 su 128 GB
   (5K, `AGENTS.md`) porta a ~4 slot: 245 giorni diventano ~120. Meglio ancora:
   togliere l'LLM dall'estrazione dei nodi e lasciarlo alla sola sintesi, dove
   serve.

## Le due strade

### A. PostgreSQL come unica casa (vettori + grafo)

`GRAPH_DATABASE_PROVIDER: postgres` sul nostro Postgres, gia' nel compose con
il database `cognee`. Un servizio, un backup, una transazione che copre grafo
e vettori: il vantaggio vero e' la semantica ACID attraverso entrambi i dati.
Costo: `pgvector` al posto di LanceDB. Il dataset `postgres_demo` e' gia'
presente in questo ambiente ed e' la prova che il percorso funziona.

**Fino a ~100.000 nodi questa e' la strada giusta.**

### B. PostgreSQL (vettori) + Neo4j (grafo)

Due database, due backup, due reti. Si sceglie se la traversata del grafo
diventa il collo di bottiglia reale, cioe' oltre il milione di nodi. Oggi, con
366, e' sovradimensionamento.

## Si puo' trasferire il grafo da Kuzu? Si', e la lettura e' gia' scritta

Non esiste un percorso di migrazione ufficiale Cognee Kuzu -> Postgres: il
formato interno del grafo e' legato al file Kuzu. Ma il grafo e' nodi e archi
con proprieta' note, e la traduzione e' lettura piu scrittura.

1. **Esportare da Kuzu.** Lettura read-only con
   `kuzu.Database(path, read_only=True)`, poi `MATCH (n) RETURN n.id, n.name,
   n.type` e `MATCH (a)-[r]->(b) RETURN a.id, r, b.id`. **Gia' fatto e
   verificato**: `Sviluppo/cognee/grafo_live.py` fa esattamente questa lettura
   sul grafo reale.
2. **Generare gli embedding** dei nodi, con lo stesso modello
   (`text-embedding-bge-m3`, 1024 dimensioni) e la stessa normalizzazione.
   Senza questo i confronti nel nuovo database non sono confrontabili.
3. **Scrivere in Postgres** dentro `cognee`: tabelle nodi e archi, vettori in
   `pgvector`. **Gli ID devono restare identici** a quelli di Kuzu, altrimenti
   i riferimenti incrociati si rompono.
4. **Confrontare i conteggi** Kuzu contro Postgres. Il confronto e' la verifica,
   non il fatto che lo script finisca senza errori.

Cosa **non** migra: le ACL vivono nel Postgres di Cognee e restano intatte, ma
le associazioni dataset->grafo vanno ricostruite.

**Il percorso corto, se si vuole evitare il rischio di uno script:** il
re-ingest. 100.000 documenti con il rate limit corretto sono ~245 giorni di
GPU, quindi da fare per fasi e su quello che serve. La migrazione via script
costa meno GPU ma richiede fiducia nel codice e verifica. Da scegliere quando la
GPU non e' il vincolo.

## Piano per fasi, con le soglie

L'ordine e' quello che conta: ogni fase e' giustificata da un numero, non da
un'allarmistica.

**Fase 0 — GPU e code (subito, senza sceelte di database).**
 Alzare gli slot sul 70B Q4, togliere l'LLM dall'estrazione dei nodi, mettere
 l'ingestione a lotti con ripresa. *Soglia*: 100.000 documenti in catalogazione
 senza superare 50 documenti in coda di inferenza. E' la fase che, da sola,
 regge la crescita fino a ~10.000 documenti.

**Fase 1 — misura del blocco di lettura (quando l'ingestione gira su un
 dataset grande).** Utenti simulati che interrogano mentre un batch scrive.
 *Soglia*: se la latenza di ricerca resta sotto 2 s durante la scrittura, Kuzu
 regge e **non si migra**. Il test non e' ancora stato fatto, ed e' la cosa che
 il testo di partenza chiede: nessuna delle due, non c'e' un test di carico con
 utenti simultanei. Costa un pomeriggio e decide meglio di tutta questa
 documentazione.

**Fase 2 — PostgreSQL (alla prima evidenza del blocco di lettura, o comunque
 prima di 100.000 nodi).** `postgres_demo` e' gia' pronto: la prova costa un
 pomeriggio. Migrazione dati per script, con confronto dei conteggi.

**Fase 3 — Neo4j (oltre ~1.000.000 di nodi).** Solo se le query ricorsive in
 Postgres si fanno sentire, con numeri alla mano.

Il punto che chiude: **non si sceglie il database prima di sapere se il
database e' il problema.** Oggi non lo e', e i 245 giorni della tabella sono un
problema di hardware, non di architettura dati.

## Cosa non fare

- Non alzare `--parallel` di llama-swap per risolvere un problema di database:
  richiederebbe `--ctx-size 131072` e la KV cache non entra (gia' scritto in
  `PROGETTO-MULTIAGENTE.md:915`).
- Non fidarsi di uno script di migrazione senza il confronto dei conteggi.
- Non comprare il server perche' «il sistema non scala»: non scala perche' 2
  slot non bastano, e questo numero non cambia cambiando database.
- Non partire da Neo4j: aggiunge un servizio da sorvegliare per un problema che
  si puo' verificare prima con un test da un pomeriggio.