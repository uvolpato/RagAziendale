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

## 2. Il problema più grande: interpreta su materiale scadente, e non dice che sta interpretando

### Prima di tutto: interpretare è il mestiere del modello

Un modello linguistico **deve** poter rispondere a «un omaggio economico e
utile per i clienti di un fiorista». Un fiorista vende fiori: sassi decorativi
da vaso, un vasetto, un cuore di vetro sono proposte sensate, e ricavarle da un
catalogo che non ha nessun attributo «regalo» è precisamente il valore che
questo progetto cerca. Se bastassero le ricerche esatte, il sistema sarebbe
deterministico e non ci sarebbe un modello dentro.

**Il difetto non è il ragionamento.** È il materiale su cui ragiona, e il modo
in cui presenta il risultato.

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

**Due prodotti veri su otto pezzi.** Il modello ha ragionato su quello che
aveva, e aveva quasi niente.

### I tre difetti, separati

**2a. Il recupero sbaglia MODO su una domanda aperta.** «Dammi gli otto pezzi
più somiglianti a questa frase» è giusto per «quanto costa il DST2040» e
sbagliato per «cosa mi proponi»: qui servirebbe una PANORAMICA — prodotti di
categorie diverse, in fascia di prezzo — non gli otto vicini di casa. È lo
stesso difetto delle figure, dove i primi quattro per somiglianza erano quattro
quasi-doppioni e si è dovuto distribuirli per soggetto (`_sparse`).

**2b. Ha proposto un contenitore.** `K0300 300 ml` è una misura di confezione.
Non è interpretazione sbagliata: è non sapere cosa sia un articolo. Vedi §3.

**2c. Ha attribuito ai documenti un giudizio suo.** La chiusura — «queste
opzioni sono economiche, utili e adatte come omaggio» — suona come se il
catalogo lo dicesse. «Economiche» è vero e verificabile; «utili e adatte» è una
proposta del modello, ed è giusto che la faccia: deve **rivendicarla**, non
farla passare per un dato. La forma onesta è «il catalogo non li classifica per
uso; te li propongo io perché un fiorista li può abbinare ai bouquet».

Solo così la sua interpretazione diventa un consiglio valutabile invece di
un'affermazione non verificabile.

### Soluzioni candidate

| | cosa | costo | rischio |
|---|---|---|---|
| **A** (2a) | Per le domande aperte, recuperare in modo da COPRIRE: distribuire i pezzi fra documenti, categorie e fasce di prezzo invece di prendere i primi otto per somiglianza | un giorno | riconoscere una domanda «aperta» da una puntuale; e la copertura si paga in precisione sulle domande puntuali |
| **B** (2c) | Il prompt separa esplicitamente **cosa dicono i documenti** da **cosa propone il modello** | mezz'ora | i divieti di prompt hanno già fallito due volte oggi; una regola in positivo può andare meglio |
| **C** (2a) | Dare al modello un **indice delle categorie** della fonte (le intestazioni delle pagine ci sono già) invece dei soli pezzi, così ragiona sapendo cosa esiste | mezza giornata | non provato |

La **C** ha un argomento: il modello oggi vede otto frammenti e non sa cosa
contenga il catalogo. Un elenco delle categorie è esattamente il materiale su
cui una persona formulerebbe una proposta.

### Cosa misurare per scegliere

Non esiste nessun metro per le domande aperte, ed è il buco più grande del
banco di prova. Le 24 domande d'oro sono tutte **puntuali**: hanno un
`riscontro`, cioè una stringa che deve comparire. Una domanda come quella del
fiorista non ha un riscontro — ha risposte migliori e peggiori.

Servirebbe un metro diverso, per esempio: *delle voci proposte, quante sono
articoli veri (non formati), di quante categorie diverse, e in che fascia di
prezzo*. Sono tutte cose misurabili dall'indice, senza giudizio umano.

Le 4 domande **senza risposta** restano da misurare e sono un'altra classe
ancora: lì la risposta giusta è «non c'è».

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

## 5-bis. Nel Markdown la descrizione non dice DI QUALE figura parla

### L'evidenza

Nel `.md` di una pagina la descrizione prende il posto del segnaposto e basta:

```
DST2043 creme cream

Immagine: nessun testo The image shows a close-up of irregularly shaped,
light beige decorative stones...
```

Nessun riferimento al file. Il legame figura → descrizione esiste **solo
nell'ordine**, e solo dentro `_pezzi_dal_markdown_figure`: la figura n-esima
del blocco riceve la descrizione n-esima. Fuori da quella funzione il legame
non c'è più.

Conseguenze, tutte osservate:

- chi apre il `.md` — ed e' l'artefatto che si guarda quando una risposta e'
  sbagliata — **non puo' risalire all'immagine**;
- il modello riceve il pezzo e **non sa quale figura sta leggendo**, quindi non
  potrebbe citarla nemmeno volendo;
- il legame posizionale e' fragile: se il conto fra segnaposto e figure non
  torna, tutto slitta di uno e nessuno se ne accorge.

E' anche il motivo per cui tutto il lavoro sulle didascalie del 22/09 e' stato
in salita: si e' cercato di ricostruire a valle (dal testo attorno al
segnaposto) un'informazione che **esisteva a monte** ed e' stata buttata.

### Il nome del file: attenzione a come si legge

`50_1.png` **non** e' «la prima immagine di pagina 50». La prima parte e' la
pagina, la seconda e' un **contatore globale del documento**:

```python
for n, (img, pagina, descr) in enumerate(immagini, start=da_indice):
    nome = f"{pagina or 0}_{n}.png"
```

`da_indice` prosegue di blocco in blocco. Pagina 7 di EUROSAND comincia a
`7_51.png`.

### Soluzioni candidate

| | cosa | costo | rischio |
|---|---|---|---|
| **A** | Nel Markdown, scrivere il riferimento accanto alla descrizione: `Immagine [7_51.png]: …` | un'ora + rilettura | il riferimento entra nel testo indicizzato: va visto se sporca la ricerca (un nome file e' un termine rarissimo, quindi l'IDF lo peserebbe molto) |
| **B** | Come A, ma il riferimento **non entra nell'embedding**: si tiene in una colonna a parte e si ricompone quando serve | mezza giornata | piu' codice, ma niente effetti sulla ricerca |
| **C** | Lasciare il `.md` pulito e salvare a fianco una mappa `pagina → [file, descrizione]` | un'ora | l'artefatto leggibile resta senza il legame, che e' meta' del motivo per cui lo si salva |

La **A** e' la piu' semplice e va misurata prima di sceglierla: il rischio non
e' teorico, perche' dal 22/09 il ramo lessicale pesa i termini rari e un nome
di file e' il termine piu' raro che ci sia.

### Cosa misurare

I metri esistono gia' e coprono entrambi i rischi: `misura.py` (il recupero
peggiora se i nomi file sporcano il testo?) e `figure.py` (la figura giusta
esce di piu'?). Basta rileggere EUROSAND e confrontare con 18/20, 14/20, 11/12
e 79%.

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

## 6-bis. Il grafo: Cognee e la divisione per area

Non e' un problema nuovo: e' la **D13**, decisa il 19/09/2026 («Cognee dietro
il nostro gate, una fonte = un dataset») e mai eseguita. Qui si scrive cosa si
e' verificato il 22/09 leggendo la documentazione, perche' la decisione era
stata presa su un elenco di funzionalita', non toccando la cosa.

### Perche' un grafo servirebbe davvero

Mappato sui problemi di questo documento:

| problema | il grafo aiuta? |
|---|---|
| §2a domanda aperta: servono categorie, non gli 8 vicini | **plausibilmente si'** — un grafo si percorre («prodotti di categoria X», «sotto € 3,50»), i vettori no |
| §3 i formati di confezione passano per prodotti | **plausibilmente si'** — l'estrazione a entita' INTERPRETA invece di spezzare |
| §6 ponte di vocabolario (ciottoli lucidi → SPIEGELSAND) | **plausibilmente si'** — gli alias multilingue stanno gia' nei titoli delle pagine |
| §5 figura ↔ codice | **no** — e' impaginazione del PDF, a monte di qualsiasi motore |
| §4 il modello racconta le figure | **no** — e' prompt |
| §7 debito operativo | **no**, e ne aggiunge |

«Plausibilmente» e non «si'»: **non e' stato verificato**. Le tre righe in alto
sono ipotesi ragionevoli, non misure.

### L'idea: un grafo per area, scelto in base ai permessi

Proposta dell'utente il 22/09. Coglie un problema che filtrare a valle non
risolve: **in un grafo il valore sta negli archi**, e se il cammino A→B→C passa
per un documento non consentito si perde il legame fra A e C. Un grafo
costruito gia' entro i confini non ha quel problema.

**E' il modello nativo di Cognee**, non un adattamento:

| l'idea | Cognee (documentazione, 22/09/2026) |
|---|---|
| un grafo per area | «un dataset e' un contenitore logico di documenti **e dei loro grafi**» |
| permessi per area, non per documento | «tutti i permessi sono a livello di dataset, **mai per singolo documento**» |
| l'utente cerca nel grafo che gli spetta | «`recall` limita le interrogazioni ai soli dataset su cui ha permesso di lettura» |

E coincide con il nostro modello: le ACL stanno su `sources`, mai duplicate sui
pezzi. I backend che supportano l'isolamento comprendono quelli che abbiamo
gia' (Postgres, PGVector); il grafo sarebbe l'unico pezzo nuovo (Kuzu, Neo4j).

### Quello che Cognee NON da', e che e' il cuore dell'idea

**ATTENZIONE.** Il 22/09 avevo scritto qui che «il percorso attraversa i
confini fra dataset, quindi chi vede due cataloghi trova anche gli archi fra i
due». **E' sbagliato**, e la smentita sta nel nostro stesso documento: il
requisito 3 della D13, verificato il 19/09/2026, dice che i permessi sono a
livello dataset con **grafi fisicamente separati per dataset**, e che gli archi
trasversali fra aree diverse sono ESCLUSI.

Avevo preso per buona una frase («i grafi combinati sono navigabili da una
porzione all'altra») che veniva da un **brevetto generico** trovato cercando
sul web, non dalla documentazione di Cognee. Due fonti mescolate.

La distinzione che conta:

| | Cognee |
|---|---|
| **cercare** su piu' dataset (unione dei risultati) | si' — e' la decisione 44 |
| **archi FRA** un documento di un'area e uno di un'altra | **no**: grafi separati |

Quindi la proposta dell'utente ha due parti, e Cognee ne copre una:

| | Cognee |
|---|---|
| un grafo per area, scelto in base ai permessi | **si'**, e' il modello nativo |
| **un grafo per un GRUPPO di aree**, con archi fra le aree | **no**, va costruito |

### Perche' l'idea riapre una decisione gia' chiusa

Il 19/09 gli archi trasversali erano stati esclusi con un argomento solido:
metterci sopra le ACL richiederebbe di modificare il sorgente di Cognee, e un
difetto li' significa **fuga di dati** — il costo peggiore.

**L'idea dell'utente aggira proprio quell'obiezione**: non si mettono ACL sugli
archi, si costruisce **un grafo separato per ogni combinazione di aree**. Ogni
grafo e' interamente consentito a chi lo usa, quindi non serve nessun permesso
a livello di arco — che era l'unica cosa impraticabile.

E' il motivo per cui vale la pena riaprire la decisione, con il costo vero sul
tavolo: N grafi da costruire e tenere aggiornati, e la sospensione di una fonte
che smette di essere immediata (vedi il confronto qui sotto).

Questo cambia anche il senso della variante «un grafo solo con la provenienza
sugli archi»: quella tiene i permessi vivi ma, se gli archi trasversali non
esistono, non aggiunge niente rispetto a oggi sul caso «confronta due
cataloghi». Le due varianti non sono equivalenti — **servono a cose diverse**,
e vanno scelte sapendo quale problema si vuole risolvere.

Fonti: [Datasets](https://docs.cognee.ai/core-concepts/multi-user-mode/permissions-system/datasets),
[Architecture](https://docs.cognee.ai/core-concepts/architecture),
[Search](https://docs.cognee.ai/core-concepts/main-operations/legacy-operations/search).

### N grafi materializzati, o uno con la provenienza sugli archi

Due realizzazioni della stessa idea:

| | N grafi materializzati | un grafo, archi con provenienza |
|---|---|---|
| estrazione a entita' | una volta, poi N assemblaggi | **una volta** |
| **sospendere una fonte** | serve ricostruire | **immediato, come oggi** |
| nuovo profilo di permessi | nuovo grafo | niente da fare |
| un documento cambia | tocca ogni grafo che lo contiene | un aggiornamento |
| velocita' di percorso | massima | si paga il filtro |

La riga che pesa e' la seconda. Oggi sospendere una fonte la toglie dalle
risposte **nello stesso istante**, perche' il permesso e' una `WHERE` letta
adesso — ed e' una proprieta' difesa in tre punti (ricerca, immagini,
documenti). Con grafi precalcolati diventano istantanee.

Proposta: **un grafo solo, provenienza sugli archi, filtro al momento della
domanda**. Se il filtro in percorso risultasse lento, ALLORA si materializzano
i profili piu' frequenti — e a quel punto e' una cache con la sua
invalidazione, non il meccanismo di sicurezza. Il meccanismo di sicurezza resta
uno, ed e' quello che rende dimostrabile la T1.14.

### Il punto in rosso: un interruttore che fallisce APERTO

Dalla documentazione:

> Senza isolamento: se `ENABLE_BACKEND_ACCESS_CONTROL` e' falso, i parametri
> dataset vengono **ignorati** durante le ricerche, e le interrogazioni girano
> su **tutti i dati del sistema, indipendentemente dai permessi**.

Una configurazione sbagliata non da' errore: risponde con tutto. Oggi il nostro
filtro fallisce CHIUSO — se la `WHERE` sparisce spariscono i risultati, non
appaiono quelli degli altri.

Non e' un motivo per scartare Cognee. E' il motivo per cui la prova di 1-2
giorni deve **cominciare** da una T1.14 fatta su Cognee: stessa domanda, due
utenti, e la dimostrazione che il secondo non vede i dati del primo. Prima di
qualunque valutazione sulla qualita' delle risposte.

### Costi da mettere in conto

- **L'estrazione a entita' e' una passata del modello su tutto l'archivio.**
  Misura vicina: 891 chiamate al VLM = 25 minuti per catalogo. Una passata su
  ~3000 pezzi con l'8B, a ~1 s l'una, e' almeno un'ora — e va rifatta a ogni
  cambio del prompt di estrazione, come gia' succede con `IMPRONTA_PROMPT`.
- **Un servizio in piu' da sorvegliare**, e oggi non sorvegliamo nemmeno
  llama-swap (§7.2).
- **I confini vanno rispettati in INGESTIONE, non solo in ricerca.** Se
  l'estrazione unisce due prodotti perche' compaiono in cataloghi di aziende
  diverse, quel nodo unificato e' gia' una perdita, prima ancora del percorso.

### L'ordine che si propone

1. **Il metro sulle domande aperte** (§2). Senza, non si saprebbe dire se
   Cognee ha migliorato qualcosa — ed e' la trappola in cui si e' caduti tre
   volte il 22/09.
2. **T1.14 su Cognee**: l'isolamento si dimostra, non si legge nella
   documentazione.
3. **Prova di 1-2 giorni su EUROSAND**, cosi' i numeri si confrontano con
   quelli di oggi invece che a impressione.

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

---

## 10. Le figure fanno rumore: la descrizione compete col testo

Nato la sera del 27/09/2026, da una domanda vera. È il problema che oggi
pesa di più sulla qualità delle risposte, ed è l'unico su cui la correzione
proposta **cambia il significato dell'indice esistente**: per questo va
documentato con più cura del solito.

### L'evidenza

Domanda: «ho bisogno di informazioni di sicurezza sulle batterie dei muletti,
ne hai?». La risposta che è arrivata era la **descrizione di un'immagine**:
l'etichetta di trasporto per batterie al litio, col codice `UN 3480` citato
come ciò che si vede in figura.

Il manuale (`magazzino-decobrands/Muletti/51760533.pdf`) contiene invece, nella
zona 58-62, **14 chunk e 9 figure**; e **6 delle descrizioni sono tutte sulla
pagina 60**:

```
60 :: Object: hazardous materials transport label
60 :: Object: hazardous material transport label
60 :: Object: transport classification label for lithium-ion batteries
60 :: Object: transportation safety label for lithium-ion batteries
```

Tutte e sei contengono, in forma diverse, le parole della domanda: *etichetta
di sicurezza*, *batterie al litio*, *trasporto*. Il testo vero c'era ed è
quello che c'è in un manuale di sicurezza — «2.2.1 Trasporto di batterie
funzionanti», `UN 3480`, IMDG, `LQ` — ma era **un pezzo su quattordici**, e ha
perso.

Il punto non è che la risposta sia falsa: l'informazione c'era. È che è
arrivata nella forma sbagliata, come foto di un'etichetta invece che come
procedura, e **sei descrizioni di sei ritagli diversi della stessa etichetta**
hanno battuto il paragrafo che la dice una volta sola.

### Perché succede

`_pezzi_dalle_figure` (`indicizza.py:2049`) crea **un chunk di testo per ogni
descrizione di figura**, in prosa come nei cataloghi. Quindi ogni figura che
non dice niente di nuovo entra lo stesso nell'indice vettoriale del testo
vero, con lo stesso diritto, e su una domanda con parole-chiave esplicite
vince: sei pezzi corti e densi contro un paragrafo.

Non è un difetto di ranking. È che **manca un'informazione**: il sistema sa
cosa c'è nella figura, ma non sa se quella figura è informazione o illustrazione,
e non può saperlo guardando l'indice.

### Due soluzioni che vanno scartate, e perché

**Il passaggio di riparazione delle descrizioni mancanti, come era stato
proposto il 27/09 mattina.** L'argomento era giusto: 78 figure senza
descrizione sono 78 prodotti non ricercabili. Ma in prosa quelle 78
descrizioni sarebbero diventate **78 pezzi competitori del testo** — cioè
avrebbe peggiorato esattamente il difetto di questa sezione. La riparazione
resta valida per i cataloghi (lì la figura *è* il prodotto) e va ripensata per
la prosa.

**Un flag per fonte, `figure_informazione` su `sources`.** Proposta come
«concetto unico che alimenta lettore, prompt e ricerca», e **sbagliata per due
motivi indipendenti**:

1. *viola `AGENTS.md`.* La regola è «come faresti tu, l'assistente»: io non ho
   nessun flag che mi dica se un'immagine è informazione o corredo, lo capisco
   dal contesto. Una lista di cartelle è la stessa comprensione, scritta a mano
   una volta e mai riletta.
2. *la granularità è sbagliata.* Dentro lo stesso manuale la pagina 60 e la
   pagina 62 non si somigliano: la prima ha un'etichetta di trasporto, la
   seconda una freccia e un simbolo di riciclo. Su 96 pagine nessun criterio a
   priori le divide.

### Il verdetto: lo decide il modello, per figura, e si registra

Un concetto solo, e una sola cosa ne discende. Non è un'impostazione: è una
**valutazione del modello, memorizzata accanto alla descrizione**, con una riga
di perché e un conteggio nel pannello. Un giudizio che non posso prevedere è
accettabile solo se posso **guardarlo**; e l'errore che si tollera è uno per
figura, visibile e rilavorabile — non una voce di elenco sbagliata per sempre e
in silenzio.

Tre verdetti, non due, e il terzo è stato trovato dall'utente:

| verdetto | cosa diventa |
|---|---|
| **tabella** | trascritta nel `.md`, cerca ed è letta come testo: è testo reso come immagine |
| **informazione** | la descrizione diventa un pezzo a sé |
| **corredo** | la descrizione resta per scegliere e mostrare la figura, non entra nella ricerca |

**La domanda da mettere davanti al modello non è «questa figura è
informazione?».** È:

> questa figura aggiunge qualcosa che il testo di questa pagina non dice già?

che è la stessa cosa portata di un livello, e regge anche la pagina 60 senza
che nessuno debba decidere nulla.

**Invariante, scritta perché l'utente l'ha detta e va tenuta:** il VLM vede
**ogni figura, sempre, senza eccezioni**. Il verdetto decide *a cosa serve* la
descrizione, mai *se esiste*. Se domani la figura, in un catalogo un prodotto
sparisce in silenzio: è la precisione sui cataloghi che oggi è buona e che non
può degradare.

### Perché il verdetto rende inutile la lista dei cataloghi

Il confronto è «la figura aggiunge ciò che il testo della pagina non dice già?».
Su una pagina a griglia il testo **non c'è**: misurato il 21/09 su EUROSAND,
pagine 7 e 76, Docling trova **zero tabelle** e i titoli mancano o sono
invertiti. Testo vuoto → la figura aggiunge tutto → **vince da sola**. Su un
manuale il testo c'è, la figura decorativa non aggiunge niente, e perde.

Una sola regola, nessun elenco: il catalogo non è una categoria dichiarata, è
una **conseguenza**. È il degrado pulito — se la pagina è stata letta male, le
figure prendono il posto del testo — e non richiede di sapere in anticipo
quale sia il caso.

Lo stesso ragionamento dà la **neutralità del prompt**: il prompt di oggi è
scritto per i cataloghi e viene dato anche ai manuali, dove produce
«object: **product catalogue page** with table entries» (pagina 61 di un manuale
del muletto). Non due prompt: uno neutro sul tipo di documento, che usa il
titolo della pagina che già riceve.

### Cosa non risolve

La pagina 60 ha **sei** figure legittimamente informative che ripetono lo stesso
 fatto. Se il modello le giudica tutte «informazione» competono tutte e sole
per la stessa domanda. La classificazione non lo risolve, perché lì non c'è
niente da classificare male: è **ripetizione**. Classe diversa, ancora aperta.
Forse esce gratis dal confronto col testo — se il modello riconosce che tutte e
sei non aggiungono niente, nessuna diventa pezzo — ma è un'ipotesi.

### Che cosa cambia nell'indice, e con quale criterio si misura

Cambiare il prompt **non** rilegge un documento già indicizzato, e va detto
perché è la cosa che rende ogni misura qui sotto valida:

- la cache delle descrizioni è **versionata dal prompt**
  (`IMPRONTA_FIGURA = sha256(ISTRUZIONI_FIGURA)[:8]`, `indicizza.py:2100`),
  quindi cambiare la stringa produce descrizioni nuove e **non distruttive**:
  quelle vecchie restano su disco e si torna indietro cambiando la stringa al
  contrario. L'esperimento è reversibile per costruzione;
- ma la decisione di **rileggere** guarda solo dimensione, data e hash del file
  (`indicizza.py:1205` e `:1216`). Nessuna traccia del prompt. Un documento
  già indicizzato non viene riletto e i suoi chunk tengono per sempre le
  descrizioni vecchie; toccare il file non serve.

Il risultato sarebbe un **corpus spezzato in due in silenzio** — ieri con una
definizione di «descrizione», domani con un'altra, e nel database niente che
dica quale hai davanti. Misurare le 24 domande su un corpus così non misura
l'idea: misura il rumore, e la fa morire per la ragione sbagliata.

La riparazione **esiste già nel codigo e va allargata di una colonna**.
`index_meta` (`indicizza.py:989`) stampa già con quale modello di embedding è
stato costruito l'indice, e `:998` fa esattamente il gioco che serve:

```python
if meta[0] != modello or meta[1] != len(esempio):
    anomalia(conn, "indice:modello-cambiato", "critico", ...)
    return False
```

se il modello è cambiato **non scrive un vettore in più** e apre un'anomalia
che dice cosa è successo. Il prompt delle figure è la stessa cosa: cambia in
silenzio il significato di quello che è già nell'indice, e oggi non lo
controlla nessuno. Stampo la versione accanto al modello, stesso controllo,
stessa anomalia. Il prompt resta liberissimo da cambiare — è il punto — ma il
sistema smette di fingere che i pezzi di ieri e quelli di oggi siano la stessa
cosa, e lo «stale» diventa un elenco che il pannello sa contare invece di una
mia impressione.

### Due buchi della cache, trovati leggendo per il determinismo

Determinismo giusto, non solo stabile:

1. **la chiave non contiene il modello** (`indicizza.py:2100`): cambio modello e
   le descrizioni vecchie vengono riusate senza dire niente. E `AGENTS.md` vuole
   che si provi un 70B o DeepSeek: quell'esperimento oggi sarebbe un no-op
   silenzioso;
2. **dentro il documento la mappa è per nome file**
   (`{nome immagine: descrizione}`): nome uguale e immagine cambiata → la
   descrizione vecchia sopravvive. Proprio quando si ridisegna una pagina e si
   rilegge, cioè quando la rilavorazione serve.

### I chunk non fanno crossing, e oggi non serve che lo facciano

Verificato: `overlap` **non compare** in tutto `indicizza.py`. Il chunker
(`_pezzi_da_markdown`, `:1848`) non scorre, **segue la struttura**: su un titolo,
una voce di elenco, una riga di tabella. Al posto del crossing c'è un trucco
all'riga `:1862`:

```python
fuori.append((f"{titolo}\n{testo}" if titolo else testo, pagina))
```

ogni pezzo **ripete il titolo** che ha sopra, e quindi si regge in piedi da
solo. Su un catalogo funziona benissimo, perché ogni prodotto ha un titolo.

Su un manuale no, e il difetto è **vivo adesso**: il chunking è già per pagina
(`:873`, dentro un ciclo), quindi una procedura che dalla pagina 2 passa alla 3
si spezza, e il pezzo sulla pagina 3 non ha titolo — resta un paragrafo nudo
che non sa di cosa parla.

Quindi la proposta dell'utente — unire i `.md` di pagina in **un** documento e
solo dopo spezzare — **migliora il sistema**: non conferma come funziona oggi,
è il contrario. Con tre avvertenze:

1. **la pagina è l'unità del giudizio, non quella del pezzo.** Se si taglia sul
   confine di pagina, metà dei problemi peggiorano: sono due cose diverse e
   vanno tenute separate;
2. **serve un segnaposto di pagina che il chunker legga**, e che **chiuda** il
   blocco. Oggi `---`/`***`/`___` vengono **saltati senza chiudere** (`:1868`),
   e un `#` chiude ma diventa il titolo del pezzo, che si chiamerebbe «Pagina
   60»;
3. **il testo va giudicato, non solo la figura**: se Docling sbaglia (titolo
   invertito a pag. 76) e lo trattiamo come buono, il confronto sbaglia. Va detto
   al modello che il testo può essere sbagliato, e che in quel caso la figura
   conta di più.

### Cosa misurare per scegliere

Niente si scrive prima di due numeri, e il primo è una **soglia fissa**:

1. **la precisione sui cataloghi, prima e dopo.** Non «si valuta»: sotto il
   valore di oggi il cambiamento non entra. È l'unica cosa che rende vera
   l'invariante, perché se il verdetto sbaglia su una figura di catalogo quel
   prodotto è perso in silenzio;
2. **le 24 domande vere** di `Sviluppo/eval/DOMANDE-CRITICHE.md`, prima e dopo.
   Se il numero non si muove, l'idea è solo più complicata di `LETTURA_PAGINA` e
   va buttata — e non avremo scritto niente da tenere.

Un metro che oggi non esiste e che va aggiunto: **quante descrizioni di figura
compaiono nelle risposte** (`l'immagine mostra` e equivalenti), per non
confondere «la figura ha fatto perdere la procedura» con «il modello ha
raccontato la figura» (§4).

### Costi, detti per intero

- cambiare il prompt **ridedescrive ogni figura**: Muletti sono dieci minuti,
  il corpus sono ore. Si paga solo quando si cambia idea;
- con il prompt neutrale e il confronto col testo, si passa da una chiamata per
  **figura** a una per **pagina**: meno chiamate, e il modello vede tutte le
  figure della pagina insieme al testo, che è metà del giudizio;
- i trenta minuti di lettura `pagina` per i cataloghi **spariscono** se il
  primo passaggio diventa Docling anche lì (che ci mette dei secondi, e già
  succede). Le chiamate al VLM sono le stesse che si pagano oggi, o meno.

### Quello che non si fa

- **niente bandierina per fonte, documento o giro**: il giorno che esiste, il
  fatto torna ad avere due padrini e siamo al punto di partenza;
- **niente chunker linguistico, per ora**: sono migliaia di chiamate su un
  manuale di 300 pagine, e butta via una conoscenza costata (la regex sui codici
  articolo è stata tolta il 21/09 perché funzionava su un catalogo e si rompeva
  sul successivo). Il modello è una variabile, non un muro: si muove quando si è
  misurato il soffitto, e qui non si è ancora misurato se il problema sia il
  taglio dei pezzi;
- **niente percorso di rilavoratura solo-figure prima di sapere se l'idea
  regge**: le PNG sono già su disco e si potrebbe, ma è codice nuovo. Prima si
  rilegge per intero un documento e si misura; il percorso economico si scrive
  se e solo se la misura lo giustifica.

---

## 11. Tre copie del Markdown di un catalogo, e non si sa quale finisce nell'indice

Trovato la sera del 27/09/2026 guardando i file a mano, non con una misura.
Non è ancora risolto: è un problema **aperto con la causa quasi trovata**, e
blocca §10, perché l'invariante di §10 è «la precisione sui cataloghi non
degrade» e un numero di cui non si sa da dove viene non si può difendere.

### L'evidenza

`_sorgenti/0a74d4a9a280179c/`, il documento di acquisti, contiene **tre** alberi
Markdown più le immagini:

| cartella | file | byte | chi la scrive |
|---|---|---|---|
| `immagini/` | 2 617 PNG | 552 MB | entrambi i percorsi |
| `markdown-11f3d27b/` | 312 | 381 KB | `ISTRUZIONI_PAGINA`, cioè il VLM che legge la pagina |
| `markdown-d095e6fc/` | 312 | 328 KB | la lettura con Docling |
| `markdown-figure/` | 309 | 1,1 MB | Docling con le descrizioni al posto dei segnaposto |

I due hash sono l'impronta dei due prompt diversi, quindi le due cartelle
documentano **due letture diverse dello stesso file**. Contenuto della pagina 7:

```
markdown-11f3d27b/0007.md      markdown-d095e6fc/0007.md
Pink Affair                    # Pink Affair
Pink Affair verführt mit …     | codice | colore | misura | confezione | prezzo |
Pink Affair seduces with …     | --- | --- | --- | --- | --- |
---                            |  |  |  |  |  |
![Image](https://example.com/image.jpg)
5
```

Tre cose, tutte vere e tutte importanti:

1. **il `.md` che oggi finisce nell'indice non ha i codici articolo.** Il VLM
   che legge la pagina produce un titolo, due righe di prosa e un
   segnaposto **finto**: `https://example.com/image.jpg`, un URL che non
   esiste. Nessun codice, nessuna tabella, un'immagine sola per pagina che
   contiene tutti i prodotti;
2. **Docling sulla stessa pagina dà una tabella vuota** (intestazioni e
   separatore, nessuna riga) — la conferma del 21/09 su EUROSAND p7 e p76;
3. **`markdown-figure/0007.md` sembra fatto solo di descrizioni, e sembra
   sbagliato**: 2 617 immagini su 312 pagine sono ~8 per pagina, quindi il file
   è davvero quasi tutto descrizione. I segnaposti sono **zero per
   costruzione**: `nei_segnaposti` *sostituisce* il segnaposto con la
   descrizione. Il file contiene anche il testo della pagina, ma in mezzo a otto
   descrizioni.

### La parte che non torna davvero

La fonte è in `LETTURA_PAGINA=acquisti-decobrands`, quindi il percorso attivo è
`_pagine_col_vlm` (`indicizza.py:850`), che chiama `_markdown_pagina` e
`_salva_markdown` e **non chiama mai `nei_segnaposti` né scrive
`markdown-figure/`**. Le altre due cartelle sono quindi **avanzi di una
lettura con Docling**, cioè roba morta di una versione passata.

Degli tre alberi, quindi, **uno solo è vivo — ed è il più povero**: prosa e un
link finto, nessun codice.

### Il buco aperto, e perché è il primo da chiudere

**Da dove escono i codici articolo** che fanno `codici.py` 10/12, se non sono in
nessuno dei tre file? La risposta più probabile è `immagini.descrizione`, che
`arricchite` (`indicizza.py:2025`) costruisce come `prima — dopo — descrizione`
prendendo il testo attorno al segnaposto. Se è così, **i cataloghi sono
indicizzati in parte come figure, non come testo**, e allora la domanda «le
figure entrano nella ricerca?» di §10 non è una questione di prosa: riguarda
il catalogo stesso, che è il caso che non si può permettere di perdere.

Una query chiude il punto: elencare i `documento` della fonte acquisti e
contare quanti chunk contengono `object:`. (La prima tentativa è fallita per il
nome del file, non per il dato: `EUROSAND.pdf` non esiste in `documenti`.)

### Una conseguenza sul disegno di §10

Il modello **oggi non vede dove sta una figura nella pagina**, perché nel `.md`
del percorso `pagina` non c'è un segnaposto vero. Quindi la proposta dell'utente
— un `.md` per pagina **con i segnaposto al posto giusto** — non è un
miglioramento del chunker: è un **pre-requisito** del verdetto. Senza la posizione
della figura nel testo, la domanda «aggiunge qualcosa che il testo non dice
già?» non ha risposta, perché il modello non sa cosa sta guardando.

---

## 12. Spostare i file dentro una fonte: non deve costare una rilettura

Richiesta dell'utente il 28/09/2026. Prima, spostare un file in una sottocartella
(o rinominarlo) lo faceva uscire dall'indice come «sparito» e rientrare come
«nuovo»: cancellazione + rilettura completa (Docling + figure + vettori), perché
la chiave era `(source_id, percorso_relativo)`.

### Cosa è stato fatto

`indicizza_fonte` ora riconosce lo spostamento **dall'impronta** (sha256 del
contenuto, che c'era già): se un file presente ha la stessa impronta di un
documento già indicizzato sotto un altro percorso della STESSA fonte, si fa una
rinomina del `documento` in `documenti`/`chunks`/`immagini` e **non si rilegge
niente**. Il vecchio percorso è marcato `spostati` e non viene cancellato.

Costo: un `UPDATE` e un hash del file. Le immagini restano al loro posto su
disco (il loro `percorso` non cambia), cambia solo l'etichetta `documento`.

### Da fare

1. **Verifica reale** (non fatta): spostare un file in una sottocartella e
   controllare che il giro stampi `spostato:` e che `documenti`/`chunks`/
   `immagini` abbiano il percorso nuovo con gli stessi pezzi, senza rilettura.
2. **Fuori dalla fonte** (non fatto, e non richiesto): spostare in un'ALTRA fonte
   resta una rilettura completa, perché cambia il `source_id` (chi vede il
   documento). Se un giorno servirà, è un'altra mossa: riconoscere la stessa
   impronta anche fra fonti e spostare `chunks`/`immagini` da una `source_id`
   all'altra, aggiornando anche le ACL.

---

## 13. Configurazione di indicizzazione: tarare il parallelismo sulla macchina

Richiesta dell'utente il 28/09/2026. L'indicizzazione deve essere **più veloce**
(soprattutto i cataloghi) e i parametri di parallelismo devono essere **tarabili**
sulla macchina che ospita il servizio, non fissi nel codice.

### Cosa è già stato fatto

- Descrizioni delle figure **in parallelo** (`PARALLELO_FIGURE`, default 6):
  erano il costo dominante dei cataloghi (2617 immagini su INGE = ~45 min in
  serie), ora vanno a lotti verso llama-swap.

### Cosa si vuole fare (da progettare, non ancora fatto)

1. **Docling a blocchi di pagine in parallelo** (non due documenti, ma due
   GRUPPI di pagine): il loop dei blocchi di 6 pagine diventa concorrente. Nota:
   ogni blocco è un processo figlio che carica i suoi modelli (~3 GB), quindi
   "2 blocchi" costa in VRAM come "2 documenti" (~6 GB). Il vantaggio del blocco
   è che sta DENTRO il loop esistente, non richiede il pool per-file.
2. **Aumentare `PARALLELO_FIGURE`** (tarabile): da decidere il numero giusto su
   macchina, non a naso.
3. **Embedding delle immagini in parallelo**: come le descrizioni, anche i
   vettori delle descrizioni (`vettori()` in `_registra_immagini`) vanno a lotti,
   non in serie.
4. **Un unico blocco di configurazione** («configurazione indicizzazione»):
   raccogliere `PARALLELO_FIGURE`, il parallelismo Docling, quello embedding e
   l'OCR in variabili d'ambiente documentate, così la stessa immagine si tara
   sulla macchina (CPU core, VRAM) senza toccare codice.

### Fatto da misurare prima di decidere

- **Dov'è il collo: OCR (CPU) o layout/tabelle (GPU)?** L'OCR è Tesseract su CPU
  (`TesseractCliOcrOptions`, nessun campo device), layout/tabelle su GPU. Se il
  collo è l'OCR, il parallelismo a blocchi aiuta molto (CPU a tanti core); se è
  la GPU, si contendono e il guadagno è minore. Misura pulita: convertire la
  stessa pagina con `do_ocr=True` e `do_ocr=False` e confrontare tempo + testo.
- Se l'OCR è il collo, la leva è anche **cambiare motore** (EasyOCR con
  `use_gpu`, o RapidOCR) o **spegnerlo** quando il livello di testo del PDF
  basta.

---

## 14. I cataloghi perdono il CONTESTO del prodotto: "sassi rossi" non trova "DST2001 rot red"

Trovato il 28/09/2026 su una domanda vera. Il sistema risponde "ho trovato
oggetti rossi ma non sassi" quando EUROSAND ha i sassi rossi (`DST2001 rot red`).

### L'evidenza

- `DST2001 rot red` è dentro un **chunk da 732 caratteri con ~30 colori**
  (`DST2040 weiß white … DST2012 hellgrau light grey … DST2001 rot red …`), e
  **non c'è la parola "sassi/pietre/stone" da nessuna parte**.
- Il titolo del prodotto sta in un **altro** chunk, sulla stessa pagina:
  `DEKOSTEINE 9 - 13 mm  deco rocks | pierres décoratives | pietre decorative`.
- Risultato: la domanda "sassi rossi" non può agganciare "DST2001 rot red",
  perché il pezzo ha 30 colori ma zero contesto "sassi". Misurato: anche
  aggiungendo al glossario "pietre ciottoli dekosteine", `DST2001` non esce.

### La causa

Il chunking del percorso Docling **appiattisce la tabella colori**: Docling non
la riconosce come tabella (sono codici in riga), quindi `_markdown_per_pagina`
li mette tutti in un unico pezzo, **senza** ripetere il titolo del prodotto
davanti. Il vecchio `_righe_di_tabella` (percorso a pagina, poi rimosso) faceva
esattamente il contrario: un colore per pezzo, col titolo davanti.

### Cosa fare (senza rifare l'indicizzazione)

Il difetto è nel testo già indicizzato. Non serve rifare Docling/VLM: serve un
**ri-chunk del testo già estratto + ri-embedding**. Per ogni pagina di catalogo:

1. trovare il titolo del prodotto (il chunk con "deco rocks / pietre
   decorative", o simili);
2. trovare i chunk con i codici variante (`DST2001 rot red` = codice + colore);
3. spezzarli **un codice per pezzo**, col titolo davanti;
4. ri-embedding dei nuovi pezzi.

È una migrazione mirata (niente Docling, niente VLM: solo spezzamento + vettori).

### Una seconda cosa trovata a fianco: il glossario è rumoroso

`glossario` contiene voci tipo `pietre -> rocks, ghiaia, cailloux, produttore,
steine, busta`: sinonimi multilingue (che `bge-m3` già fa) MESI a rumore da
estrazione cattiva ("produttore", "busta"). Per questo era giusto non metterlo
nella query del testo (diluisce), ma il rumore danneggia anche il regex delle
figure. Da ripulire all'origine (`estrai_glossario`).



