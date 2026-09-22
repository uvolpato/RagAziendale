# Problemi aperti, con le prove e le soluzioni candidate

Scritto il 22/09/2026, dopo una giornata di misure. Serve a **decidere dopo**,
non a proporre: ogni problema ha l'evidenza che lo dimostra, le soluzioni
possibili con il loro costo, e **cosa andrebbe misurato per scegliere**.

Regola che vale per tutto il documento: un numero senza data e senza come è
stato ottenuto non è un numero. Quelli qui sotto ce l'hanno; dove manca la
misura, è scritto «non misurato».

I risultati completi stanno in [valutazione/RISULTATI.md](valutazione/RISULTATI.md),
l'architettura in [ARCHITETTURA.md](ARCHITETTURA.md), i prompt in [PROMPT.md](PROMPT.md).

---

## 1. Lo stato, misurato

| metro | cosa misura | oggi |
|---|---|---|
| `misura.py` — domanda lunga | la pagina giusta è fra gli 8 pezzi | 18/20 |
| `misura.py` — **domanda corta** | idem, ma come si scrive in chat | **14-15/20** |
| `risposte.py` | dei dati arrivati al modello, quanti ne riporta | **73%** |
| `codici.py` | chiedere un articolo per codice | 10 · 10 · 11 su 12 |
| `figure.py` | la figura giusta esce | 11/12 |
| `figure.py` | quante figure escono / quante c'entrano | 19 / **79%** |
| domande senza risposta | dice «non c'è»? | **mai misurato** |

Tutti su EUROSAND. Gli altri cataloghi non hanno un banco di prova.

---

## 2. Il problema più grande: risponde bene a domande che non può rispondere

### L'evidenza

Chat del 22/09 sera: *«ho un cliente fiorista, vuole omaggiare i clienti con
qualcosa che costi poco e sia utile, cosa mi proponi?»*

Gli otto pezzi arrivati al modello:

```
[1] K0300 300 ml A 12 € 3,15        <- un FORMATO di confezione, non un prodotto
[2] BRILLANT HERZEN cuori 23 mm     <- prodotto
[3] ipuro time to glow: Feel the…   <- prosa pubblicitaria
[4] K0300 300 ml (icona)            <- di nuovo il formato
[5] GOOD MOOD «아로마 퍼퓸»            <- descrizione di una figura, in coreano
[6] HEAD NOTES Lime, turkish rose…  <- note olfattive
[7][8] IPURO FLORAL AMSTERDAM        <- prodotto
```

Il modello ne ha ricavato **sette proposte regalo**, con i prezzi presi dalle
righe dei formati, e ha chiuso con «queste opzioni sono economiche, utili e
adatte». Nessuna delle due affermazioni — «utile», «adatta come omaggio» — sta
da nessuna parte nei documenti.

### Perché succede

La domanda chiede un **giudizio** che il catalogo non contiene. Non esiste un
attributo «utile», «da regalo», «adatto a un fiorista». Il recupero fa il suo
mestiere (somiglianza semantica con «omaggio», «clienti», «poco costoso») e il
modello veste il risultato da risposta.

**È la classe di guasto più pericolosa**: non sbaglia un dato, sbaglia la
premessa, e suona bene. Un utente che non conosce il catalogo non ha modo di
accorgersene.

### Soluzioni candidate

| | cosa | costo | rischio |
|---|---|---|---|
| **A** | Il prompt deve DICHIARARE il criterio: «il catalogo non indica l'uso; questi sono gli articoli sotto € 3,50» | mezz'ora | il modello può ignorare la regola, come ha già fatto con altre |
| **B** | Usare la regola che c'è già e non usa: **una domanda di chiarimento** quando la domanda chiede un giudizio che i documenti non supportano | mezz'ora | distinguere «giudizio» da «ricerca» non è banale, e sbagliare significa chiedere chiarimenti sempre |
| **C** | Un passaggio che CLASSIFICA i pezzi prima di rispondere (prodotto / formato / prosa) e scarta quelli che non sono articoli | mezza giornata | serve un criterio generale per «è un articolo», e finora ogni criterio del genere si è rotto sul catalogo successivo |

### Cosa misurare per scegliere

**Le 4 domande senza risposta esistono nel banco di prova dal 21/09 e non sono
mai state misurate.** Sono esattamente questa classe: il sistema deve dire
«non c'è». Prima di scegliere fra A, B e C, quel metro va costruito e fatto
girare — altrimenti si sceglie a sensazione.

---

## 3. Le righe di formato passano per prodotti

### L'evidenza

`K0300 300 ml A 12 € 3,15` è **un contenitore**, non un articolo. È arrivato al
modello due volte su otto pezzi ed è diventato la prima proposta della
risposta. Lo stesso vale per `F0305 370 ml`, `F0405 500 ml`, `E5500 5.5 l`.

### Perché succede

Il chunker segue la struttura del Markdown: una riga di tabella diventa un
pezzo. Una riga di formati e una riga di articoli hanno la **stessa forma**.
Nel 21/09 un'espressione regolare sul codice articolo provava a distinguerli:
funzionava su un catalogo e si rompeva sul successivo (non vedeva i codici a
una lettera), ed è stata tolta.

### Soluzioni candidate

| | cosa | costo | rischio |
|---|---|---|---|
| **A** | Il TITOLO della pagina è già davanti a ogni pezzo: una pagina «VERPACKUNGEN / packings» è una pagina di formati. Usarlo come segnale di ranking, non come filtro | mezza giornata | dipende dal titolo, che su alcune pagine manca |
| **B** | Non distinguere, ma dire al modello che il contesto contiene anche formati di confezione | mezz'ora | sposta il problema sul modello, che oggi non rispetta già alcune regole |
| **C** | Lasciar stare: chi legge ha il **collegamento alla pagina** e verifica in un clic | zero | l'errore resta, ma diventa verificabile |

### Cosa misurare

Non esiste un metro. Servirebbe: «delle voci elencate in una risposta, quante
sono articoli veri?» — sulla falsariga di `figure.py`, pescando dall'indice.

---

## 4. Il modello racconta le figure, e gli è vietato

### L'evidenza

La risposta al fiorista contiene sette volte «L'immagine mostra…», con frasi
come «probabilmente decorativi o per artigianato». Il prompt lo vieta
esplicitamente dal 22/09 sera:

> Non ricopiare mai il testo della descrizione di una figura: serve a te per
> capire cosa mostra, non è una frase da mostrare all'utente.

Il modello **parafrasa** invece di ricopiare, e formalmente non disobbedisce.
Nella chat precedente aveva anche chiuso con «non è possibile visualizzarle
direttamente qui» mentre il sistema gli attaccava sotto sei immagini — quello
è un divieto esplicito, violato.

### Perché succede

Le descrizioni delle figure stanno nel CONTESTO come tutto il resto (ed è
voluto: sono quelle che fanno funzionare le domande descrittive). Il modello
le vede e le usa. Un divieto formulato come «non ricopiare» non copre
«riassumi».

### Soluzioni candidate

| | cosa | costo | rischio |
|---|---|---|---|
| **A** | Riformulare il divieto in positivo: «delle figure parla solo se aggiungono un dato che il testo non ha» | mezz'ora | i divieti di prompt hanno già fallito due volte su questo |
| **B** | **Togliere le descrizioni dal contesto della risposta**, lasciandole solo nell'indice per la ricerca | un'ora | si perderebbero le domande descrittive: le descrizioni nel testo valgono 17/20 → 18/20 (misurato) |
| **C** | Marcarle nel contesto in modo che siano chiaramente materiale di servizio (es. un prefisso) | un'ora | non misurato se cambia qualcosa |

### Cosa misurare

Facile, e non esiste: contare quante risposte contengono «l'immagine mostra» o
equivalenti. Si aggiunge a `risposte.py` in dieci righe.

---

## 5. Metà delle figure non ha un'etichetta

### L'evidenza, misurata sulle 891 figure di EUROSAND

| | figure |
|---|---|
| hanno testo prima del loro segnaposto | 423 |
| **non hanno NULLA prima** (figure consecutive nell'ordine di lettura) | **468** |

Con la finestra su entrambi i lati, 199 figure avevano **più di un codice** in
didascalia — ed è il motivo per cui, a «sassi rossi», uscivano due figure blu
che si trascinavano «DST2001 rot red» dalla riga accanto. Delimitando al
segnaposto precedente le ambigue scendono a 53, ma le mute salgono a 468.

### Perché succede

Su una pagina di catalogo il codice sta **sotto** la sua fotina. Docling
linearizza la pagina in ordine di lettura e quella disposizione si perde:
spesso emette una fila di immagini e poi una fila di testo.

### Soluzioni candidate

| | cosa | costo | rischio |
|---|---|---|---|
| **A** | **Geometria**: ogni figura e ogni blocco di testo hanno il loro rettangolo (verificato: pagina 7 ha 28 figure e 131 blocchi con le coordinate). L'etichetta è il blocco più vicino, di norma quello sotto | mezza giornata + rilettura | va scelta la regola «sotto entro N punti, altrimenti il più vicino», e va misurata |
| **B** | Chiedere al VLM che legge la PAGINA di marcare le figure con il loro codice | un giorno | provato il 22/09 e accantonato: se il modello marca 27 figure e Docling ne ritaglia 28, ogni codice slitta sulla foto sbagliata |
| **C** | Lasciar stare: la didascalia dice «pagina 7» ed è cliccabile | fatto | l'etichetta resta assente per metà delle figure |

Oggi è in vigore la **C**. La **A** è la correzione vera e i dati ci sono già.

### Cosa misurare

`figure.py` ha già la colonna giusta: «figure mostrate / quante c'entrano»,
oggi 19 / 79%. Una etichetta corretta su tutte le figure deve farla salire.

---

## 6. Le domande corte perdono 4 domande su 20

### L'evidenza

| | trovate |
|---|---|
| domanda lunga (come l'ha scritta l'azienda) | 18/20 |
| **domanda corta** (come si scrive in chat) | **14-15/20** |

Perdono la 1, la 6, la 9 e la 14. Diagnosi: non è la catena (vettore 14,
fusione 14, completa 14) e non è la granularità dei pezzi (la banda fra il 1º e
il 50º risultato è più LARGA con le corte: 0,095 contro 0,078).

Quello che la domanda lunga aggiunge è il **ponte di vocabolario**: la 6 lunga
dice «effetto molto lucido, quasi a **specchio**» e i pezzi giusti sono
SPIEGELSAND, *mirror sand*. «Ciottoli neri lucidi» quel ponte non ce l'ha.

### Soluzioni candidate

| | cosa | costo | esito noto |
|---|---|---|---|
| **A** | Riscrivere la domanda in termini di catalogo | — | **bocciata 4 volte**, e inventa attributi («Marrakesch» → «sfere in resina», che sono di metallo) |
| **B** | Agente che cerca più volte guardando i risultati | — | **costruito e spento**: 14/20 contro 14/20, a 11,7 s contro 1,1 |
| **C** | Un dizionario di sinonimi di dominio, costruito **dall'archivio** (le pagine dei cataloghi sono multilingue: «pietre decorative \| deco rocks \| pierres décoratives» sta scritto nei titoli) | un giorno | non provato |
| **D** | Indicizzare anche una traduzione dei titoli | un giorno | non provato |

La **C** è l'unica non ancora esplorata, e ha un argomento a favore: il ponte
che manca è **già scritto nei documenti**, nei titoli multilingue.

---

## 7. Debito operativo (non è ricerca, è manutenzione)

### 7.1 Un'interruzione del server dei modelli marca i documenti come rotti

**Venti documenti** di `sicurezza-decobrands` sono in stato `errore` con
`ConnectError: Name or service not known`: sono le letture tentate mentre
llama-swap era spento. Un documento in errore **si riprova solo quando il file
cambia**, quindi restano rotti per sempre.

Soluzione: distinguere un errore del DOCUMENTO (illeggibile, corrotto) da un
errore di CONTORNO (host dei modelli giù, rete). Il secondo non è una proprietà
del file e va riprovato al giro dopo. Mezza giornata.

### 7.2 Nessuno sorveglia llama-swap

Sta fuori da Docker — scelta giusta, i modelli non devono girare nei container
— ma se muore, l'assistente risponde 500 e nessuno se ne accorge finché un
utente non scrive. Il 22/09 è successo: era rimasto spento dopo un riavvio a
mano. In produzione dev'essere un servizio con riavvio automatico.

### 7.3 Il messaggio d'errore punta al componente sbagliato

Quando llama-swap è giù, l'utente legge *«500 su litellm:4000»*: LiteLLM sta
benissimo, è il server dei modelli a non rispondere. Chi legge va a guardare
Docker, dove è tutto verde. Mezz'ora.

### 7.4 I prompt stanno nel codice (**D18**)

Ogni prova costa una ricostruzione dell'immagine. Il 22/09 una sola riga
aggiunta al prompt di risposta ha portato la completezza dal 46% al 73%: prove
del genere devono costare minuti.

### 7.5 Le impostazioni valgono per tutte le fonti (**D16**)

`LETTURA_PAGINA`, `TESTO_DOCLING`, `DPI_PAGINA` sono variabili d'ambiente
globali con l'elenco delle fonti dentro. Diventano impostazioni per fonte.

---

## 8. Cosa è stato provato e SCARTATO (con i numeri)

Vale quanto il resto: senza questa sezione qualcuno le riprova fra sei mesi.

| | esito | data |
|---|---|---|
| Riscrittura della domanda in termini di catalogo | bocciata 4 volte, e inventa attributi | 21-22/09 |
| `OR` al posto dell'`AND` nel ramo lessicale | neutro (14/20 e 73% identici) e peggiore sui codici | 22/09 |
| Agente che cerca più volte | 14/20 contro 14/20, a 11,7 s contro 1,1 | 22/09 |
| Qwen3.8 27B al posto dell'8B | 18/26 contro 19/26, a ~25 s contro ~2 | 22/09 |
| Ramo lessicale sulle parole dell'utente invece che sulla riscrittura | risultato identico riga per riga | 22/09 |
| Soglia di rarità a 1 su 10 e 1 su 100 | le risposte perdono 2 riscontri | 22/09 |
| Etichetta dal solo testo precedente | 11/12 → 10/12, 79% → 58% | 22/09 |
| Distinguere «non hai nominato niente» da «non ce l'ho» | «sassi rossi» dà `sass` come termine raro: in un archivio in tedesco la parola italiana è rara quanto un codice | 22/09 |

---

## 9. Le lezioni sul metodo, che sono costate più del codice

**Un metro che passa al primo colpo va guardato con sospetto.** Tre volte in un
giorno un metro ha dato ottimi voti misurando il caso facile:

| metro | prima versione | versione onesta |
|---|---|---|
| domande d'oro | 18/20 (lunghe) | **14/20** (corte, come si scrive davvero) |
| figure | 12/12 (codici pescati dalle descrizioni) | **6/12** (pescati dal testo) |
| didascalie | «0 ambigue» | **53** — quello zero era una costante digitata a mano |

**Un proxy non è la cosa che vuoi migliorare.** Contare quante didascalie sono
*pulite* non è contare quante figure si *trovano*: ottimizzando il primo, il
secondo è sceso dal 79% al 58%.

**Il numero che conferma non è il numero che decide.** Sulla lentezza del 27B
ho incolpato tre cose (contesto, buffer dei modelli piccoli, memoria
condivisa), ognuna con un numero a favore. Nessuna era la causa: con la scheda
vuota fa 2,6 token/s lo stesso. L'unica misura che distingueva — i token al
secondo — l'ho presa per ultima.

**Due trappole di misura sull'hardware**, entrambe hanno ingannato:
`nvidia-smi` **non vede la memoria condivisa**, e il «96% di utilizzo» non
distingue una scheda che lavora da una che aspetta il bus; e i GB di «GPU
condivisa» dei modelli di embedding **non sono un traboccamento**, sono i
buffer di `--batch-size 8192`.

**I test non coprivano la catena.** Un `AttributeError` su una funzione
cancellata ha fatto rispondere 500 a ogni domanda, con tutti i test verdi:
chiamavano i pezzi uno per uno. Ora T1.27 percorre `_immagini_per_la_domanda`
da capo a fondo.

**Il banco di prova non vede i seguiti di conversazione.** I due difetti
peggiori della giornata — la riscrittura che inventava i nomi dei cataloghi, e
la riformulazione che falliva in silenzio senza `/no_think` — stavano nel
secondo turno di una chat, e nessuna misura li ha rilevati. Li ha trovati
l'utente.
