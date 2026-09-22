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
