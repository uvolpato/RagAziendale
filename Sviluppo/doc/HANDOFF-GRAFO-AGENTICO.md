# Handoff — il grafo agentico, da qui in avanti

Scritto il 6/10/2026 perché la sessione sta finendo i token. Chi prende in
mano questo deve poter continuare senza rileggere una conversazione di dodici
ore. Tutto quello che c'è qui dentro è misurato, non ricordato; dove non lo è,
c'è scritto.

Leggi anche `doc/NOTTE-DOCUMENTI.md` (la notte del 5/10, cosa ha pagato e cosa
no) e `doc/LETTURA-E-MAPPA.md` (la mossa `leggi` e la mappa che mentiva).

---

## 1. Dove siamo, con i numeri

| banco | esito |
|---|---|
| cataloghi (`valutazione/banco.py`, 10 casi × 3) | **26/30** |
| documenti (`valutazione/documenti.py`, 6 casi × 3) | **5/18** col giudice severo |

Ultima misura del 6/10, col vicinato che entra fra le righe: cataloghi 26/30
(`archivio` 3/3, `pappagallo` 3/3, falliscono solo `non-promettere` 0/3 e
`aperta-tema` 2/3), documenti 5/18 (`rimanda invece di rispondere` su
`procedura`, `modelli` e `struttura`; `assente` 3/3, cioè non inventa).

I documenti erano a 8/18 con la data nel redattore, che però costava 4 punti ai
cataloghi: è il punto 6 della sequenza, e vale +3 appena la si dà solo dove
serve.

Questi due numeri sono il metro. Non si decide niente senza di loro, e non si
confrontano numeri ottenuti con giudici diversi: il giudice dei documenti è
stato irrigidito il 6/10 (vedi §5) e i numeri prima di quel cambio non sono
comparabili.

## 1-bis. ATTENZIONE: il banco intero e il sottoinsieme non misurano la stessa cosa

Scoperto il 6/10/2026 e non ancora spiegato. Lo stesso caso, stesso codice,
stessa ora:

| come | `pappagallo` |
|---|---|
| da solo, 3 giri | **0/3** |
| con 3 casi prima, 3 giri | 1-2/3 |
| dentro il banco intero (9 casi prima), 3 giri | **3/3**, due volte |
| da solo, 7 giri | 1/7, due volte |

L'esito peggiora quanto meno la batteria è lunga, in modo monotono col numero
di turni che hanno girato prima. Il meccanismo non è noto: candidati sono la
cache dei prompt sul server dei modelli, lo stato del giudice (nel banco
intero è già stato chiamato ventisette volte) o un effetto d'ordine nel banco.

**Conseguenza operativa, da rispettare finché non è spiegato: contano solo le
corse del BANCO INTERO.** Il sottoinsieme (`banco.py 3 nome,nome`) serve a
LEGGERE le risposte e le prove del giudice, non a dare un punteggio. Il
6/10 ho applicato e poi tolto tre modifiche basandomi su numeri da
sottoinsieme, e una delle conclusioni che ne ho tratto («questa modifica ha
rotto `pappagallo`») era falsa: quel caso era già così da solo.

**Prima cosa da fare su questo**: capire il meccanismo. Se è la cache dei
prompt, il banco va eseguito con la cache disattivata o con un ordine
mescolato; se è il giudice, va isolato. Un metro che dipende da quanti casi
l'hanno precedutro non misura il sistema: misura anche sé stesso.

**Il tempo vero di una misura: una coppia di banchi è 45-50 minuti.** Il
«tempo mediano complessivo» che il banco stampa è la somma delle mediane per
caso, cioè UN giro per caso: non è il tempo d'orologio. Per tre giri su dieci
casi sono 30 turni più 30 chiamate al giudice.

Come si lancia (il banco gira DENTRO il container, `psycopg` sull'host non c'è):

```bash
cd "C:/Progetti/RAG Aziendale/Sviluppo"
D=/tmp/val-$(date +%s)
docker compose cp valutazione orchestratore:$D
MSYS_NO_PATHCONV=1 docker compose exec -T orchestratore sh -c "cd /app && python $D/banco.py 3 > /var/tmp/b.txt 2>&1"
MSYS_NO_PATHCONV=1 docker compose exec -T orchestratore sh -c 'sed -n "/IL BANCO/,/TOTALE/p" /var/tmp/b.txt'
```

Una cartella NUOVA ogni volta (`cp` su una esistente copia DENTRO e si rimisura
la versione vecchia). `MSYS_NO_PATHCONV=1` serve perché Git Bash su Windows
traduce `/var/tmp/...` in un percorso Windows. Un sottoinsieme di casi:
`banco.py 3 archivio,non-promettere`.

Dopo ogni modifica al codice dell'orchestratore: `docker compose restart
orchestratore`.

---

## 2. Lo stato dell'albero di lavoro — DA LEGGERE PRIMA DI TOCCARE GIT

Niente di quello che segue è committato. `git status` mostra 17 file
modificati e 5 non tracciati, e **non sono tutti miei**: in `amministrazione/`,
`keycloak/`, `cognee/prova.py`, `cognee/permessi.py` e nei `valutazione/prova-*`
c'è lavoro dell'utente.

**Mai `git checkout --` su un file in quell'elenco.** Il 4/10 ho distrutto
così del lavoro non committato dell'utente. Se devi annullare una modifica
tua, applica la patch inversa, non il checkout.

Modifiche mie di questa sessione, per file:

- `orchestratore/mappa.py` — `mappa_dati(conn)` calcola i conteggi dal
  database (prima era una stringa costante che diceva «indice VUOTO» con 1865
  pagine dentro). Nuova `conta(conn)`.
- `orchestratore/grafo.py` — mossa `leggi` (+ `_mossa_leggi`), `indice` fra i
  posti dove si guarda, il capitolo degli operatori scelto su `dove`, il
  vicinato che entra fra le righe a query vuota, la frase degli esiti di
  `esplora`.
- `orchestratore/operatori.py` — `mappa_operatori(dove)` restituisce UN
  capitolo; il capitolo 2 conosce `leggi` (prima diceva «prendine venti col
  SIMILE», che è l'istruzione opposta).
- `valutazione/banco.py` — `CAMPIONE_DA`, e il filtro dei casi accetta una
  lista separata da virgole.
- `valutazione/documenti.py` (nuovo) — il banco dei documenti.
- `valutazione/prova-leggi.py` (nuovo) — le tre cose che `leggi` deve fare.
- `valutazione/prova-scelta.py` (nuovo) — la scelta della mossa in
  isolamento, con e senza il nostro prompt. **È lo strumento più utile che ho
  costruito**: costa due minuti invece di 45 e dice se un difetto è nostro o
  del modello.
- `PROBLEMI-APERTI.md` — §6-ter, il lint dell'impianto.
- `doc/` — i tre documenti.

L'utente ha proposto commit e push come rete prima del lavoro sul grafo. Non
l'ho fatto: decidilo con lui, e se committi, separa le sue modifiche dalle mie.

---

## 3. La sequenza da fare, in ordine

Ogni punto ha l'attesa scritta. **Una modifica per volta, col banco in mezzo.**
Il 5/10 ne ho infilate cinque insieme e ho perso sei punti sui cataloghi senza
sapere quale li avesse presi: recuperarli è costato tre misure da 45 minuti.

### 1. L'`id` al critico (sblocca tutto il resto)

Oggi le righe sono identificate dalla **posizione nella lista**: il critico
risponde `riga: 3`, il codice scrive `righe[n-1]["_motivo"]`, i verdetti sono
`{numero: sì/no}` e le citazioni `[[n]]` sono quel numero.

Finché è così, nessun canale condiviso e nessun ramo parallelo è sicuro: due
nodi che accodano insieme spostano le posizioni, e i verdetti finiscono sulle
righe sbagliate — cioè il sistema cita a chi legge la pagina di un altro
prodotto.

Da fare: il critico riceve l'`id` e risponde con l'`id`; i verdetti si
indicizzano per `id`; la numerazione `[[n]]` si assegna SOLO quando si scrive
(`_scheda()` accetta già i numeri dall'esterno, il parametro `numeri`).
Attenzione alle righe senza `id` (un `SELECT count(*)` non ne ha): servono una
chiave surrogata.

*Atteso: il banco non si muove. Se si muove, è un bug, non un miglioramento.*

### 2. Il checkpointer

**Non è igiene, è la cosa che rende il lavoro possibile.** Con i checkpoint si
rigioca un turno dal nodo del coordinatore con un prompt diverso, in secondi,
invece di rifare 30 turni in 45 minuti. Dopo questo punto ogni misura
successiva costa una frazione.

LangGraph salva lo stato a ogni transizione (Postgres o SQLite) e permette di
riprendere e rigiocare. Serve un `thread_id` per conversazione e cambia come
`grafo.cerca()` invoca il grafo compilato.

*Atteso: nessun effetto sulla qualità. Verificare che il banco non si muova.*

### 3. Lo stato al coordinatore con dei CAMPI

Oggi riceve tutto in prosa (`_stato_a_parole`) e risponde in json. Tutti gli
agenti sono così: interrogati in prosa, rispondono in json. La lezione già
imparata dal progetto — **un campo batte tre frasi**, misurato sul critico il
4/10 — non è mai stata applicata agli ingressi.

Il caso che lo dimostra: su «quanti cataloghi ci sono?» il coordinatore ha
chiamato `esplora` quattro volte di fila. Lo stato gli dice «righe trovate: 0»
in un paragrafo; come campo sarebbe `mosse_fatte: {esplora: 3, cerca: 0}`, e
«ho già esplorato tre volte senza mai cercare» diventa un dato invece di una
deduzione.

*Atteso: il ciclo su `esplora` si chiude senza una frase che lo vieti.*

### 4. Il canale `righe` con riduttore

Dopo il punto 1 è sicuro. `righe: Annotated[list, aggiungi_righe]`, dove il
riduttore fonde per `id` (aggiorna se c'è, accoda se no), preserva l'ordine e
taglia a `MAX_RIGHE`. Oggi quella logica vive dentro `_nodo_mosse`, ed è il
motivo per cui **solo quel nodo** può produrre righe.

*Atteso: neutro. Rende vero «qualunque nodo produce righe».*

### 5. `esplora` produce righe

Le descrizioni dell'indice entrano come righe non verificate. È il terzo caso
della famiglia «materiale visibile e non consegnabile» (vedi §4).

*Misura: documenti, caso `rimanda`.*

### 6. La data solo quando le righe sono testo

La sezione `<da_quando_vale_quello_che_dici>` nel redattore vale **+3 sui
documenti e −4 sui cataloghi** (misurato nelle due direzioni). È fuori posto,
non sbagliata: una didascalia di catalogo non ha una data, un pezzo di
documento sì. Va data in base al tipo di righe in mano. Il testo è salvato in
`scratchpad/da-quando.txt` della sessione; se non c'è più, sta nel git stash
di nessuno — riscrivilo, è in `NOTTE-DOCUMENTI.md` §6.

### 7. I termini di ricerca come campo dall'analista

L'analista estrae già `oggetto` e `attributi` e non li passa **tradotti**. Il
coordinatore li riscrive da capo in italiano e la query non trova niente: il
6/10, in chat, su «kit per decorare l'albero di natale» ha scritto
`~* '\mkit' AND ~* 'decorare' AND ~* 'albero'` su didascalie inglesi. Zero
righe, inevitabile.

*Misura: il caso dell'albero di Natale, e `banco.py` intero.*

### 8. Json con nomi-imperativi al redattore

Oggi riceve tag XML (`<scrivi_questo>`, `<descrivi>`, `<di_anche>`) e il
motivo NON è prestazionale — non è mai stato misurato. I nomi dei tag sono
**ordini**, non nomi di campi, e il redattore ricopia la forma che gli mostri:
quando riceveva `1. [SI] ... [Catalogo X.pdf, pagina 142]` scriveva
`**[SI]**` e la pagina a mano, tre volte su tre. La stessa cosa si fa in json
con chiavi-imperative. L'utente ha chiesto esplicitamente questa misura.

### 9. `RetryPolicy` sui nodi

Il 6/10 il giudice è morto con un 500 del server dei modelli e ho perso un
caso.

### 10. `interrupt()` su `chiedi`

Oggi `chiedi` **finisce** il turno: la domanda esce e al turno dopo si
ricostruisce tutto da zero, righe comprese. Con `interrupt()` il turno si
sospende e riprende con lo stato in mano. Richiede i punti 1 e 4.

### 11. `Send` + sottografi: i due rami

Documenti e cataloghi come due rami che girano insieme, ognuno col suo
capitolo di prompt e il suo stato, e la fusione per riduttore.

**Provato il 6/10 nella versione corta — due letture dentro lo stesso loop — e
costa 21/30 contro 26/30.** La colpa non è delle due letture: in un loop
condiviso `_prossimo` manda a scrivere appena una mossa chiude, quindi la
giunzione deve scegliere fra chi lavora e chi vuole chiudere, e le due scelte
possibili sono entrambe decisioni di codice (rimandare chi chiude costa
`generica` e `aperta-regalo`, un punto ciascuno; far vincere chi chiude butta
il lavoro dell'altro ramo).

Con `Send` ogni ramo è un sottografo con stato proprio e la giunzione è un
riduttore: nessuna scelta. È la versione che può funzionare. Ultimo perché è
la più grossa e perché la base deve essere ferma.

---

## 4. Il difetto di fondo, che è UNO

Tutti i fallimenti misurati in questa sessione hanno la stessa forma:
**materiale che il modello VEDE e non può CONSEGNARE**, oppure **una decisione
già presa e poi ri-derivata**. Tre volte in ventiquattro ore:

| dove | cosa succede |
|---|---|
| `esplora` | righe d'indice visibili, non consegnabili → nega di avere i cataloghi che ha appena elencato |
| `vicinato` | righe vere dichiarate «non si citano» → va a `proponi` e consegna categorie generiche |
| analista → coordinatore | `oggetto` e `attributi` estratti e mai passati → query in italiano, zero righe |

Il vicinato è stato riparato il 6/10: entra fra le righe, e misurato subito
dopo dà cataloghi 26/30 (il massimo della sessione) e documenti 5/18
(invariati). Non ha ancora pagato, ma non costa, e toglie il meccanismo che
mandava `proponi` a inventare categorie. Gli altri due sono i punti 5 e 7.

Il redattore può citare SOLO le righe che stanno nello stato. Tutto ciò che
esce dall'archivio con `id`, `documento` e `page` dovrebbe diventare una riga —
non verificata, che il critico esiste per giudicare. Oggi c'è un solo tubo che
produce righe citabili (`cerca`, più `leggi` da ieri) e tre vicoli ciechi che
producono prosa.

---

## 5. Il metro, e i suoi errori

`valutazione/documenti.py` riusa il giudice di `banco.py` cambiando quattro
cose: le domande, chi legge le risposte, la tabella del campione (`chunks`
invece di `immagini`) e due colpe in più — «spaccia per attuale una cosa
datata» e «rimanda la domanda invece di rispondere».

Il giudice ha sbagliato due volte, e le due volte le ho trovate **leggendo le
risposte a mano**. Fallo anche tu la prima volta che cambi il metro:

1. accusava «nega avendo» una risposta GIUSTA («LiteLLM non è più utilizzato»)
   esibendo come prova un frammento di configurazione in una guida vecchia. Un
   documento che nomina una cosa non smentisce «non è più usata».
2. PROMUOVEVA una risposta che rimandava la domanda all'utente.

Un metro di cui non ti fidi è peggio di nessun metro.

---

## 6. Le regole del progetto che vincolano le soluzioni

Dall'`AGENTS.md` e dalle parole dell'utente, e non sono negoziabili:

- **mai una soluzione per il caso specifico.** Criterio: se la soluzione
  sparisce quando il caso cambia (colore → misura → formato), è un posticcio.
- **uno scrive, l'altro legge.** L'indicizzazione e l'orchestratore non si
  mischiano.
- **i cataloghi vanno bene: quello standard si mantiene.** 26/30 è il
  pavimento, non il bersaglio.
- **la comprensione sta nell'agente, non in `if` sparsi nel chiamante.** La
  bussola: «come lo farei io, che ho davanti tutta la conversazione?»
- **il modello è una variabile.** Prima di dire «il modello non ce la fa», va
  separato il difetto di codice dal limite di conoscenza — e per quello c'è
  `prova-scelta.py`.
- risposte all'utente **sintetiche**. Niente approfondimenti non richiesti.

---

## 7. Le trappole in cui sono cascato — non ripeterle

1. **Più di una modifica fra due misure.** Costo: sei punti e tre ore.
2. **Attribuire un numero senza rimisurare la base.** Ho dato la colpa di un
   calo a un agente nuovo; rimisurando, la base si era spostata da sola.
3. **Togliere una frase per principio.** «Il passo che viene adesso è `cerca`»
   sembrava diluire la scelta del modello. Togliendola: `archivio` da 3/3 a
   0/3, tre giri su tre. Quello che sembra ridondante a volte porta peso.
4. **Misurare mentre il modello è occupato.** Ho lanciato le prove di scelta
   durante un banco: si sono contesi la GPU e la misura è stata buttata.
   Controlla sempre `docker ps` per un `cognee-run` o un'indicizzazione in
   corso.
5. **La shell di Windows mangia i backtick e `\n` negli heredoc.** Le patch si
   scrivono come file python nello scratchpad e si lanciano, non si incollano
   nella shell. `/tmp` lato Windows non esiste.
6. **Mai sperimentare su dati che una pipeline sta scrivendo**, e mai lasciare
   un container orfano che tiene il lock di Kuzu: il 6/10 ne ho perso 26
   documenti in silenzio.

---

## 8. Aperti, misurati, non risolti

- **`non-promettere` 0/3 in OGNI configurazione della notte.** Non si è mai
  mosso: è il difetto più stabile che abbiamo. «Ci sono anche con i cuori
  blu?» → promette cuori blu citando righe con cuori rossi.
- **`aperta-tema` e `pappagallo` oscillano fra 0/3 e 3/3** fra corse identiche:
  quella è varianza del campionamento, ±2-3 punti sul totale. Non inseguirla.
- **documenti: `rimanda invece di rispondere` su 3 casi su 6.** Il redattore
  scrive «hai bisogno di ulteriori dettagli?» con cinque `[[n]]` attaccati, e
  nel percorso non c'è nessun `chiedi`. Prima di toccare un prompt va capito
  da dove nasce quella domanda.
- **contenuto sbagliato a monte**: «guida doganale sulle importazioni» porta un
  README di dagster sulle «importazioni programmate dai gestionali». Due
  accezioni, e `SIMILE` (bi-encoder) non le separa. Un giudice che legge
  domanda e riga insieme sì — l'utente ha proposto `rizzo-flow` (JEV locale,
  decisioni tipizzate, ~50 ms, nessuna chiamata fuori). Da provare sul caso
  `procedura`, dopo aver liberato il campo.
- **il grafo di cognee perde la provenienza**: 97 righe su 76 nomi, il nome è
  il basename (`README` sette volte) e il percorso originale non c'è. Si
  ripara all'`add`, scrivendo il percorso in `external_metadata`. Vedi
  `PROBLEMI-APERTI.md` §6-ter.
- **il lint dell'impianto** (copertura fra i tre magazzini) non è scritto.
  `chunks` e `indice` sono allineati, 50 e 50.
