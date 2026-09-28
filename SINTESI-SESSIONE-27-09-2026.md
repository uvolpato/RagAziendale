# Sintesi sessione 27/09/2026

Due giornate di lavoro sullo stesso difetto: **le figure del sistema parlano
troppo forte, e il sistema non sa distinguere una figura che è informazione da
una che è illustrazione.** Qui c'è lo stato e il piano; i fatti e le prove sono
in `Sviluppo/PROBLEMI-APERTI.md` §10 e §11.

Regola che vale per tutta la sessione: **prima di ogni modifica, file, scopo,
rischi e verifica, e si aspetta il via.** Niente modifiche stasera.

## 1. Che cosa è vivo adesso

Il giro forzato su Muletti è **in corso**, non morto:

```
docker exec -d assistente-ingestion-1 sh -c \
  "cd /app && python indicizza.py --una-volta --forza --solo Muletti > /tmp/giro_muletti.log 2>&1"
```

- processi vivi nel container: `60` (la `sh -c`) e `66` (python), più `1` che è
  il servizio;
- lock `pg_advisory_lock` `7310061` preso: il servizio **cede** il turno, e
  `--una-volta` **aspetta** (`indicizza.py:1594-1596`: il giro manuale prende il
  lock in attesa, quello del servizio usa `try` e salta). Non sono in gara.

| Documento | Stato | Pezzi | Figure descritte |
|---|---|---|---|
| `Muletti/51760533.pdf` | indicizzato 20:33 | 228 | 100 / 100 |
| `Muletti/51814883.pdf` | indicizzato 21:06 | 401 | 265 / 265 |
| `Muletti/51857958.pdf` | **in corso** | — | pagine 266/266 finite, fase figure |
| `Muletti/ATS BG-Manuale Movimentazione e allegati (1).pdf` | errore delle 04:37, non riletto | 0 | — |

Da controllare domani: se `51857958` è finito, e l'`ATS BG` **non** è stato
riletto perché il `--solo Muletti` filtra sul percorso e il nome contiene
spazi/tra parentesi (attenzione a come viene filtrato).

**Una figura senza descrizione in tutto l'indice** (misurato): 24, di cui 23
sono le icone della presentazione di sicurezza. Nel log c'è **una** riga
`figura non descritta (URLError: … No address associated with hostname)`:
transitoria, l'host si risolve (`host.docker.internal` → 192.168.127.254).

## 2. Cosa è stato fatto nelle due giornate

- **unificata la descrizione delle figure**: un solo `_descrivi_figure()` per i
  due percorsi, stessa cache su disco, titolo opzionale. Provato con un harness
  (confine, titoli, cache, ritento col backoff).
- **portato in produzione** senza ricostruire l'immagine: copia in
  `/app/indicizza.py` + `docker compose restart ingestion`, verificato il
  container. L'immagine `assistente-ingestion:latest` è corrotta/parziale e non
  si ricostruisce.
- **chiuso il polling del pannello** (`app.js` ripollisce ogni 4 s durante il
  lavoro e ogni 20 s da fermo; il vecchio `if (inLettura)` non ripollivava più).
- **scritte** `ingestion/COSA-PROGUCE.md` e la sezione su Muletti in
  `ingestion/PRESTAZIONI.md`; corretti `IMPOSTAZIONI.md` e le 78 figure senza
  descrizione, che erano un numero sbagliato.
- chiusi i retry di A12: 14 documenti recuperati (10 su `sicurezza-decobrands`,
  6 su `sicurezza-luis`).

## 3. La discussione di stasera, in breve

Il punto di partenza è una domanda vera sulle **batterie dei muletti**: la
risposta era la descrizione di un'immagine (l'etichetta di trasporto, `UN
3480`), mentre il manuale aveva la procedura a poche righe di distanza. Sulla
pagina 60 ci sono **sei** descrizioni, tutte con le parole della domanda, e il
testo vero era **un pezzo su quattordici**.

Tre conclusioni, in quest'ordine di importanza:

1. **Il VLM vede ogni figura, sempre.** Il verdetto decide *a cosa serve* la
   descrizione, mai *se esiste*. Se si salta la figura, in un catalogo sparisce
   un prodotto: è la precisione che non si può degradare (A14).
2. **Il verdetto lo dà il modello, per figura, e si registra.** Tre esiti, non
   due: `tabella` (trascritta nel `.md`), `informazione` (pezzo a sé), `corredo`
   (solo per scegliere e mostrare). La domanda da fare: *«aggiunge qualcosa che
   il testo della pagina non dice già?»*. Nessuna bandierina: una lista scritta
   a mano è la comprensione altrimenti, e la sua granularità è sbagliata (pagina
   60 e 62 dello stesso manuale non si somigliano).
3. **Il confronto rende inutile `LETTURA_PAGINA`.** Su una griglia il testo non
   c'è (misurato: zero tabelle su EURSAND p7 e p76), quindi la figura vince da
   sola: il catalogo diventa una conseguenza, non una categoria dichiarata.

Due cose che ho propose e **vanno ritirate**:

- il passaggio di riparazione delle descrizioni mancanti, in questa forma: in
  prosa avrebbe aggiunto pezzi competitori del testo, cioè peggiorato il difetto;
- il chunker «manterrà le figure a corredo fuori dal recovery con un peso
  diverso»: un peso non risolve, e il peso è una sintesi, non una prova.

## 4. I due fatti che cambiano il piano

**Il chunker non è un modello né un plugin: sono 40 righe nostre**
(`indicizza.py:1848`). Non scorre, segue la struttura, e al posto del crossing
**ripete il titolo** su ogni pezzo (`:1862`). Non ha un tetto di dimensione
(A15). E la libreria che già c'è ed è adatta è **Docling**
(`docling.chunking.HybridChunker`, MIT, già dipendenza): sa già la struttura del
documento, mentre noi la ri-indoviniamo dai caratteri del Markdown. LangChain,
LlamaIndex e Unstructured **non** sono installati e non li aggiungerei.

**Cambiare il prompt non rilegge un documento già indicizzato.** La cache è
versionata sull'hash del prompt (`indicizza.py:2100`), quindi l'esperimento è
reversibile; ma la decisione di rileggere guarda solo dimensione, data e hash
del file (`:1205`, `:1216`). Senza un intervento, dopo un cambio di prompt il
corpus è **diviso in due in silenzio**. Il rimedio c'è già: `index_meta`
stampa il modello di embedding e `:998` blocca tutto se cambia. Va allargato al
prompt delle figure.

## 5. I primi tre passi di domani

Nessuno richiede una decisione nuova: sono tutte verifiche.

1. **Chiudere il buco di §11.** Quale dei tre `.md` di un catalogo finisce
   nell'indice, e **da dove escono i codici articolo** che fanno 10/12. Una
   query. Se i cataloghi sono indicizzati in parte come figure, la domanda di
   §10 riguarda il catalogo, non la prosa.
2. **I due numeri di partenza**: le 24 domande vere di
   `Sviluppo/eval/DOMANDE-CRITICHE.md` e la precisione sui cataloghi, che
   diventa **soglia fissa**, non una valutazione.
3. **Solo dopo**, e solo se i numeri li reggono: il prompt del verdetto, la
   versione in `index_meta`, il tetto del chunker.

## 6. Le trappole di questa macchina (sono parse)

- `docker exec ... ps` **non funziona**: nel container non c'è `ps`. Per i
  processi usare `/proc/[0-9]*/cmdline`.
- **PowerShell mangia le virgolette** di un `python -c` con SQL dentro: otto
  errori di fila. Scrivere lo script su file, `docker cp`, eseguire. È il modo
  che funziona.
- `python indicizza.py --help` **avvia un giro completo** (A12).
- Il messaggio `lettura interrotta: il servizio si e' fermato mentre leggeva`
  **accusa la memoria a torto**: `indicizza.py:1167-1172` deduce «quasi
  certamente per memoria» da un `in_lettura` lasciato, senza prove. Se a fermarlo
  è stato un modello giù, l'operatore viene mandato nel posto sbagliato.
- Il pannello mostra `Non leggibile` per un file **in lavorazione**: `stato` ha
  solo quattro valori e `errore` è il modo in cui un file in corso si registra
  (`:1243-1248`).
- `--forza` **rilegge tutto**: la cache tiene, ma le figure vengono
  ridescritte. Un giro forzato sul corpus sono ore.
