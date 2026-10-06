# La notte dei documenti — 5/6 ottobre 2026

Obiettivo dato: rendere la ricerca sui documenti affidabile e puntuale, per
contenuto e per giudizio, senza determinismo sui casi specifici.

Obiettivo **non raggiunto**. Quello che c'è adesso e non c'era ieri è un
metro, una causa misurata e due correzioni che hanno pagato. Qui sotto tutto,
coi numeri, incluse le tre cose che ho sbagliato.

## 1. La causa del calo dei cataloghi: la mappa mentiva

`mappa.py` diceva al coordinatore «indice — al momento è **VUOTA**, quindi su
questo **non cercare**» mentre l'indice aveva 50 documenti e 1865 pagine, e
`<dove_si_cerca>` non nominava `indice` affatto. La mossa `esplora` gli
consegnava righe da un posto che per lui non esisteva: su «quanti cataloghi
ci sono?» rispondeva «non ho trovato nessun catalogo», **tre giri su tre**.

Il docstring del file prometteva «i conteggi sono veri, cambiando i cataloghi
la mappa cambia con loro»; `mappa_dati()` era una stringa costante. Anche
chunks (17582 → 18973) e le fonti (14 → 50) erano fermi.

**Riparato**: `mappa_dati(conn)` legge i conteggi, e l'indice si descrive per
quello che è o dice di essere vuoto. Non si correggono tre numeri: decadono
di nuovo alla prossima indicizzazione.

**Misurato: 21/30 → 26/30.**

## 2. La mossa `leggi`

Prima non c'era un modo di APRIRE un documento. Tecnicamente `cerca` lo fa,
ma esce tagliato a 40 righe e 6000 caratteri — numeri tarati su quaranta
didascalie di catalogo: di un documento da 30 KB il modello ne vede un quinto
e non sa che è un quinto.

`leggi(documento, da)` apre un documento per nome, con un tetto suo, e se è
lungo dice **da quale pezzo chiedere il seguito** — la maniglia per il passo
dopo, come fanno `read_doc` e `chunk_read` negli altri sistemi. Le pagine
lette diventano righe con `documento` e `page`, quindi si citano come tutte le
altre. Collaudo in `valutazione/prova-leggi.py`: apre, continua senza
ripetere, resta dentro i permessi.

Sui cataloghi non è mai stata scelta in 30 giri — ed è corretto, là la scala
era già completa (`esplora` mappa, `cerca` righe, `guarda` apre la foto). Sul
banco dei documenti è stata scelta **6 volte**.

## 3. Non era il modello: era il prompt

Con `leggi` nel flusso, il 14b non la sceglieva mai. `valutazione/prova-scelta.py`
isola la decisione — stesso stato, stesso `esplora` eseguito davvero, stessi
strumenti — e cambia solo il contorno:

| | col nostro prompt | con cinque righe di prompt neutro |
|---|---|---|
| `leggi` scelta | **0 volte** | **6 volte** |
| scelte giuste | 1/6 | 8/12 |

La causa precisa non era un bias generico. Era una frase, l'ultima del
capitolo 2 della mappa degli operatori:

> «Una domanda che chiede un GIUDIZIO — "a che punto siamo", "come è
> strutturato", "cosa manca" — … **Non cercare la riga giusta: prendine venti
> col SIMILE**»

Nominava esattamente quelle domande e prescriveva `cerca`. Era la cosa giusta
quando aprire un documento non si poteva. Riscritta, `leggi` passa da 0 a 2 su 4.

Il resto del prompt restava un prompt da catalogo: quaranta righe su `~*`,
confini di parola, plurali e didascalie inglesi arrivavano anche su «a che
punto siamo». Dando **un capitolo solo**, scelto su `dove` (che l'analista
azzecca 6 volte su 6), `leggi` arriva a 2 su 4 con i cataloghi intatti.

**Quel `dove` è una decisione di codice e non dovrebbe esserci.** Sta scritto
nel sorgente insieme al motivo per cui stanotte è rimasta.

## 4. I due rami in parallelo: provati, costano 5 punti

Far leggere l'archivio due volte nello stesso giro — un ramo col capitolo dei
cataloghi, uno con quello dei testi, in parallelo — toglie la decisione dal
codice e dà **lo stesso** 2/4 sulla scelta della mossa. Ma sul banco dei
cataloghi: **26/30 → 21/30**, 354s → 463s.

La colpa non è delle due letture, è della **giunzione**. `_prossimo` manda a
scrivere appena una mossa chiude, quindi quando un ramo vuole lavorare e
l'altro vuole chiudere bisogna scegliere, e le due scelte possibili sono
entrambe decisioni di codice: rimandare chi chiude (`generica` e
`aperta-regalo` perdono un punto ciascuno — vivono di `chiedi`) o buttare il
lavoro dell'altro ramo.

I due rami senza decisioni di codice esistono in un'altra forma: **due loop
indipendenti**, ognuno coi suoi giri, una sola giunzione davanti al redattore.
È una modifica di architettura e va fatta sapendo quanto vale il lato
documenti — per quello adesso c'è un metro.

## 5. Il metro dei documenti, e i suoi due errori

`valutazione/documenti.py`: sei domande, campione preso da `chunks`, il
giudice di `banco.py` con due colpe in più.

- **spaccia per attuale una cosa datata** — la metà «giudizio». L'archivio
  contiene la storia: una nota del 18/09 dà LiteLLM «in analisi», il 24/09 è
  stato tolto.
- **rimanda la domanda invece di rispondere** — aggiunta dopo aver visto il
  sistema rispondere «hai ulteriori dettagli sui modelli?» con cinque righe
  citate sotto.

Il metro ha sbagliato due volte, e le due volte le ho trovate leggendo le
risposte a mano — che è il motivo per cui quella lettura è obbligatoria:

1. accusava «nega avendo» una risposta **giusta** («LiteLLM non è più
   utilizzato»), esibendo come prova un frammento di configurazione in una
   guida vecchia. Un documento che nomina una cosa non smentisce «non è più
   usata».
2. **promuoveva** una risposta che rimandava la domanda. Da lì la quarta colpa.

## 6. I numeri dei documenti

| | giudice v1 | giudice v2 (più severo) |
|---|---|---|
| prima misura in assoluto | 11/18 | — |
| con la data nelle affermazioni | 13/18 | 8/18 |
| col capitolo documenti nel redattore | — | 6/18 |

La regola **«da quando vale quello che dici»** nel redattore ha pagato:
`stato` 2→3, `procedura` 1→3, `struttura` 2→3. È la difesa che §6-ter aveva
già indicato, e vale per ogni affermazione di stato.

Il capitolo per i documenti nel redattore è costato 2 punti ed è stato tolto:
la domanda di rimando non nasce dalla mancanza di istruzioni al redattore.

## 6-bis. Lo stato finale, e il conto da pagare

Ultima misura della notte, con tutto quello che è rimasto in piedi:

| | inizio notte | fine notte |
|---|---|---|
| cataloghi | 26/30 (dopo la mappa) | **20/30** |
| documenti | 11/18 (giudice v1) | **8/18** (giudice v2) |

I cataloghi hanno perso sei punti, e non so quale modifica li ha presi:
dalla misura a 26/30 se ne sono accumulate cinque senza una misura in mezzo —
la mossa `leggi`, la riscrittura del capitolo 2, il capitolo scelto su `dove`,
la frase degli esiti di `esplora`, la data nel redattore. È lo stesso errore
del punto 8.2, fatto su cinque modifiche invece di due.

**Il primo lavoro non è una funzione nuova: è la bisezione.** Una modifica per
volta contro `banco.py`, finché i sei punti hanno un nome.

Quello che terrei comunque, perché si giustifica da solo: la mappa calcolata
dai dati (il prompt diceva «l'indice è vuoto» con 1865 pagine dentro) e la
mossa `leggi` (sui cataloghi non viene mai scelta, quindi lì non costa).
Il sospetto principale è la data nel redattore: ha pagato 4 punti sui
documenti, e nello stesso passaggio le colpe «promette» sui cataloghi sono
aumentate.

## 7. Dove sta il difetto, adesso

**`rimanda invece di rispondere`, tre casi su sei.** Il redattore scrive «hai
bisogno di ulteriori dettagli?» con cinque `[[n]]` attaccati, e nel percorso
non c'è nessun `chiedi`. Prima di toccare un altro prompt va capito da dove
nasce quella domanda — `chiarimento` che sopravvive, la guida, o il redattore
che davanti a righe che non c'entrano ripiega.

E sotto, un difetto di **contenuto**: su «cosa dice la guida doganale sulle
importazioni?» il recupero porta un README di dagster sulle «importazioni
programmate dai gestionali». Due accezioni della stessa parola, e la ricerca
semantica non le separa. Il documento giusto esiste e ha un nome che
l'indice conosce: è esattamente il caso in cui `esplora` + `leggi` dovrebbero
battere `cerca`, e non succede.

## 8. Le tre cose che ho sbagliato stanotte

1. Ho **tolto per principio** la frase che diceva al coordinatore «adesso
   muoviti» dopo `esplora`, chiamandola diluizione della scelta del modello.
   Misurato: con la frase 26/30, senza 21/30, e il difetto torna identico tre
   giri su tre.
2. Ho infilato **due modifiche nello stesso giro** (la mossa `leggi` e quella
   frase), e per un'ora ho attribuito il calo alla mossa. `leggi` non era
   nemmeno stata chiamata: zero volte in 30 giri.
3. Ho lanciato le prove di scelta **mentre il banco girava**: si sono contesi
   il modello, quella misura è stata buttata.
