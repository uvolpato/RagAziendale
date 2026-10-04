# Progetto: ricerca multiagentica (D23)

Sostituisce `sql_agente` da solo. Stessa idea — il modello scrive la ricerca,
il codice mette i permessi — ma con l'operatore che oggi gli manca, con un
agente che verifica, e con le fonti che non si ricavano piu' dalla prosa.

Tutto quello che segue e' misurato sul database vivo il 30/09/2026, non
ipotizzato. Dove e' un'ipotesi lo dice.

---

## 1. Il fatto che decide il progetto

La mappa attuale (`mappa.py`) racconta al modello due tabelle e un operatore:
`~*`. **Non gli dice che esistono i vettori.** Eppure ci sono, con l'indice:

```
immagini.embedding  vector(1024)   hnsw (vector_cosine_ops)   15143 righe
chunks.embedding    vector(1024)   hnsw (vector_cosine_ops)   17582 righe
chunks              gin (to_tsvector('italian', content))
```

Sulla domanda «nastri bianchi con cuori rossi», in italiano, senza tradurre
niente, senza glossario, senza vincoli:

| ricerca | pagine giuste nelle prime 9 |
|---|---|
| `~*` letterale (quella di oggi) | 5 su 7 — perde p.33 e p.40 |
| `ORDER BY embedding <=>` | 6 su 7 |
| `WHERE ~* 'ribbon\|tape' ORDER BY embedding <=>` | **7 su 7** |

L'ibrido non e' una tecnica nuova: e' la stessa riga di SQL con due operatori
invece di uno. Il letterale fa da filtro grossolano, il vettore ordina.

**Conseguenza di progetto:** non si toglie l'SQL al modello e non si torna agli
strumenti muti. Si aggiunge il vettore alla lingua in cui il modello gia'
scrive. Un solo strumento, due operatori.

L'embedding pero' non lo puo' scrivere lui (sono 1024 numeri). Serve una
funzione che scrive lui e compila il codice:

```sql
-- quello che scrive il modello
SELECT documento, page, descrizione FROM immagini
WHERE descrizione ~* 'ribbon|tape'
ORDER BY SIMILE('nastri bianchi con cuori rossi') LIMIT 20

-- quello che esegue il codice (sostituzione prima dell'ACL)
ORDER BY embedding <=> %s::vector        -- %s = recupero.embedding(...)
```

`SIMILE('...')` e' l'unica cosa che il codice riscrive nella query, ed e' una
sostituzione testuale su una funzione che nel dialetto non esiste: non e'
interpretare la domanda al posto suo, e' dargli una penna che scrive vettori.
Con `SIMILE` il modello puo' anche cercare **solo** per significato, quando non
sa che parola usare — che e' il caso in cui oggi fallisce.

---

## 2. La mappa del DB

Da dare al modello. I numeri sono veri, letti dal database.

### Le tabelle di contenuto

| tabella | righe | cosa contiene | testo | vettore |
|---|---|---|---|---|
| `immagini` | 15 143 | una riga = **una foto** di prodotto | `descrizione`, INGLESE, a etichette (`object: … material: … Colours: …`) | `embedding` ✓ hnsw |
| `chunks` | 17 582 | il testo del documento, come nell'originale | `content`, **multilingue**, prezzi, codici, nomi, policy | `embedding` ✓ hnsw + FTS italiano |
| `documenti` | 14 | le fonti: nome, tipo, stato, `pezzi`, `figure_totali` | — | — |
| `indice` | **0** | che cosa c'e' in ogni pagina | `descrizione` | `descrizione_vec` |
| `glossario` | 6 308 | `voce` → `termini[]` imparati dal corpus | — | — |

Colonne comuni a `immagini`/`chunks`: `id, source_id, documento, page`.
`immagini` ha in piu' `percorso` (il file su disco) e `verdetto`.

Due cose da dire al modello in chiaro, perche' non sono deducibili:

- **`indice` e' vuota.** Non e' un errore: non e' ancora stata generata. Finche'
  lo e', la pre-selezione dei documenti (`INDICE_PRESELEZIONE=1`) non fa nulla.
- **`glossario` ha 6 308 voci** ed e' la memoria del vocabolario dei cataloghi.
  Oggi l'agente SQL non la vede. Va esposta come terzo operatore, non come
  espansione automatica: `TERMINI('nastri')` restituisce le parole che il corpus
  usa davvero, e il modello decide se metterle nella query.

### Cosa NON e' contenuto

`erp_storico.*`, `erp.*`, `arricchimenti.*`, `anomalie`, `gruppi_utente`,
`traces`, `pending_actions`, `sources`, `conversation_taint`. Non sono
dell'archivio: restano fuori dalla lista delle tabelle interrogabili.

### I permessi

Invariante, non negoziabile: ogni query passa per

```sql
<tabella>.source_id IN (SELECT id FROM sources
                        WHERE stato = 'attiva'
                          AND aziende    && %s::text[]
                          AND acl_groups && %s::text[])
```

Il codice lo inietta, il modello non lo scrive e non lo vede. Regola che lo
rende dimostrabile: **una tabella per query**. Con JOIN, UNION, subquery o CTE
il filtro coprirebbe solo il primo `FROM`. La regola c'e' gia' ed e' giusta —
si tiene.

### Le immagini su disco

`immagini.percorso` punta a un file dentro `/cartelle` (montato in sola
lettura nell'orchestratore). Quindi una foto si puo' **riaprire e guardare**,
non solo leggerne la didascalia. E' quello che rende possibile l'agente 4.

---

## 3. L'architettura: uno stato e un coordinatore, non una catena

La prima versione di questo progetto era una catena:
`istradatore → cercatore → critico → osservatore → redattore`. Sbagliata, e
il motivo vale piu' del disegno.

**Una catena mette una decisione in ogni giuntura, e quelle decisioni
finiscono in codice.** Fra critico e redattore serviva sapere cosa fare dopo,
e la risposta era un `if`: «se c'e' un forse vai dall'osservatore, se non c'e'
nessun si torna a cercare, altrimenti scrivi». Sono tre righe di programma che
decidono la STRATEGIA della ricerca — cioe' esattamente il lavoro che volevamo
dare al modello. Il determinismo non era rientrato dalla finestra: non era mai
uscito, si era solo spostato dai nodi alle frecce fra i nodi.

Chi risponde in una conversazione non ha una catena. Ha davanti tutto quello
che sa — la domanda, cosa ha gia' provato, com'e' andata — e a ogni passo
sceglie la mossa successiva. E' un ciclo su uno STATO, non una pipeline.

```
    ┌──────────────────────────────────────────────┐
    │  STATO: la domanda, la conversazione intera, │
    │  le righe trovate, i verdetti, i giri fatti  │
    └───────────────────┬──────────────────────────┘
                        ▼
                  ANALISTA  ── che domanda e'? cosa NON dice?
                        │        (una volta, senza rami: aggiunge, non instrada)
                        ▼
                 COORDINATORE  ── guarda lo stato INTERO e sceglie la mossa
                        │
        ┌──────────┬────┴─────┬──────────┬──────────┐
        ▼          ▼          ▼          ▼          ▼
    cerca()×N  verifica()  guarda()  chiedi()  rispondi()
    in parallelo  CRITICO  OSSERVATORE  alla     → REDATTORE
                                        persona
        └──────────┴──────────┘
                   │
                   └──▶ torna al coordinatore con lo stato aggiornato
```

Resta **un solo bivio in codice**, e non decide niente: *il coordinatore ha
chiamato `rispondi` o `chiedi`?* Allora si scrive. Non e' il programma che
sceglie, e' il programma che ubbidisce.

### Le mosse si fanno insieme, perche' sono indipendenti

Cercare nelle foto e cercare nel testo non hanno niente da dirsi. Metterle in
fila e' una decisione che nessuno ha chiesto, presa dal codice per il solo
fatto di essere scritto una riga sotto l'altra.

Il coordinatore puo' chiamare `cerca` piu' volte **nello stesso giro**, e
quelle chiamate partono insieme (ognuna sulla propria connessione). Quante
strade aprire lo decide lui, guardando la domanda.

Onesta' sui numeri: il guadagno in secondi e' piccolo, perche' una query costa
150-900 ms mentre un giro del modello ne costa 2000-18000. Il guadagno vero e'
che il numero di strade non e' piu' una costante nel codice.

### Cosa ogni agente ha, e cosa non ha

| agente | vede | decide | NON puo' |
|---|---|---|---|
| **analista** | la sola domanda, non i dati | che domanda e', e cosa non dice | cercare, instradare |
| **coordinatore** | conversazione intera, stato intero, mappa dei dati | la mossa successiva | vedere o scrivere i permessi |
| **critico** | domanda + righe | riga per riga: si / no / forse | cercare, scrivere la risposta |
| **osservatore** | la FOTO vera + una domanda precisa | si / no / non si vede | leggere il database |
| **redattore** | solo le righe confermate | come si presenta | aggiungere righe, scegliere quali mostrare |

Il critico e l'osservatore sono **agenti**, non funzioni: hanno un prompt e un
giudizio. Dal punto di vista del coordinatore sono strumenti, e questo risponde
alla domanda «gli strumenti sono deterministici o agentici?» — `cerca` e'
deterministico (esegue SQL), `verifica` e `guarda` sono agentici.

### Le decisioni non si leggono con una regex

Ogni verdetto arriva come **chiamata di strumento** con uno schema, non come
testo da interpretare. La differenza non e' di stile: una regex ha sempre un
ramo «e se non l'ha scritto cosi'?», e quel ramo e' una scelta presa dal
programma proprio quando il modello non e' stato chiaro — il momento peggiore.
Con lo strumento, o la decisione c'e', o gliela si richiede.

### 1. Analista

Gira una volta, all'inizio, e scrive nello stato due righe: **l'ambito** (che
domanda e', e quindi dove sta la risposta) e **cosa manca** (quello che la
domanda non dice e che cambierebbe la risposta).

Serve perche' il coordinatore guarda i RISULTATI, e «ho trovato 16 righe
verificate» gli legge sempre come successo — anche quando la domanda era «mi
serve il rosso» e le 16 righe sono piatti, dischi e mollette tenuti insieme
solo dal colore. Chi guarda i risultati non si accorge che la DOMANDA era mal
posta: bisogna guardare la domanda, e guardarla prima.

Il criterio che gli si da' e' uno solo: *se cercassi cosi' com'e', quello che
torna sarebbe utile o sarebbe un mucchio? Se e' un mucchio, quello che lo rende
un mucchio e' la cosa che manca.* E l'errore opposto e' scritto accanto:
«nastri bianchi con cuori rossi» e' completa, non le manca la larghezza.

**Non e' un pezzo di catena.** Non ha un bivio, non instrada, non sceglie chi
viene dopo: aggiunge due righe allo stato. Un nodo senza rami non decide
niente — e' un altro paio d'occhi, non un vigile. Il suo giudizio finisce nello
stato che il coordinatore legge, ed e' il coordinatore a farne quello che
vuole (nella prova qui sotto lo ha seguito una volta e ignorato l'altra, tutte
e due le volte giustamente).

### 2. Coordinatore

Riceve la conversazione intera, la mappa dei dati e lo stato a parole (tutte
le righe con la loro descrizione e il loro verdetto, e quante mosse restano:
il tetto gli e' noto in anticipo, non gli casca addosso).

Il suo prompt non contiene un elenco chiuso di tipi di domanda. Contiene
quattro esempi di come cambia il lavoro — prodotto, domanda aperta, documento
di testo, domanda sull'archivio — e la riga che conta: *«se la domanda non
somiglia a nessuno di questi casi, trattala per quello che e'»*.

### 3. Critico

«Quello che e' stato chiesto C'E'?», riga per riga. Due errori opposti, scritti
nel prompt perche' li ha fatti tutti e due:

- **troppo largo**: il bianco su un articolo e i cuori su un altro non fanno un
  nastro bianco con cuori;
- **troppo stretto**: una riga e' UNA FOTO, e una foto di catalogo mostra piu'
  articoli — basta che ci sia quello chiesto. Una foto con tre nastri di cui
  uno bianco con cuori rossi va bene, e «bianco con cuori rossi E puntini»
  pure: il di piu' non toglie.

E una riga si scarta perche' non risponde, mai perche' non e' un prodotto: se
la domanda chiedeva un numero, la riga che porta il numero va bene.

### 4. Osservatore (VLM)

Prende `immagini.percorso`, legge il PNG da `/cartelle`, lo manda a
`qwen/qwen3-vl-4b` con UNA domanda precisa — «e' un nastro bianco con cuori
rossi?», non «descrivi l'immagine». La didascalia era stata scritta in
ingestione senza sapere che domanda sarebbe arrivata; qui la domanda c'e'.

**Vincolo hardware, da risolvere prima di accenderlo.** In `llama-swap.yaml`
`qwen3-14b` sta nel gruppo `confronto` (`exclusive: true`), il VLM nel gruppo
`grandi`: caricare il VLM **scarica il modello di chat**, e ogni verifica costa
due swap.

```
14B Q4_K_M   8,4 GB pesi + KV (32768 ctx, q8_0)   ≈ 10 GB
VLM 4B       2,3 GB + mmproj 0,8 GB + ctx 16384   ≈  3,7 GB
piccoli      embedding 0,33 + rerank 0,46         ≈  0,8 GB
                                            totale ≈ 14,5 su 16,3 GB
```

Ci sta, ma stretto (oggi la scheda e' a 12,7/16,3 col solo 14b). Va
**misurato** spostando `qwen3-14b` nel gruppo `grandi`, eventualmente
scendendo a `--ctx-size 16384` sulla chat. Per questo `GRAFO_OSSERVATORE`
nasce spento.

### 5. Il dubbio: `chiedi`

Una ricerca serve a qualcuno, e a volte la mossa piu' utile e' una domanda
invece di un'altra query. `chiedi` ferma il giro e fa UNA domanda alla
persona; la risposta torna al turno dopo, dentro la conversazione, che il
coordinatore riceve gia' intera. Non serve altro: la bidirezionalita' e' la
chat stessa.

Il prompt distingue la domanda utile da quella fastidiosa:

- **cattiva**: chiedere *prima* di aver guardato («che tipo di nastri?»). La
  persona non deve fare il lavoro al posto del sistema.
- **buona**: chiedere avendo visto i dati, quando la scelta e' davvero sua.
  «Ho trovato nastri bianchi con cuori rossi in tre larghezze: ti interessa un
  formato in particolare?»
- **buona**: quando la domanda si legge in due modi che portano in due posti
  diversi dell'archivio.
- **cattiva**: quando basta mostrare quello che si e' trovato.

### 6. Redattore

Vede solo le righe confermate e non puo' sceglierne un sottoinsieme. Cita
`[[3]]`, mai «pagina 27»: la pagina la mette il sistema. Se c'e' una domanda
da fare, chiude con quella, dopo aver mostrato quello che ha.

---

## 4. Le fonti: si cancella codice, non si aggiunge

Il trace del 30/09/2026 ha mostrato il difetto peggiore della catena attuale:
la risposta citava cinque pagine, **una sola e' diventata un collegamento**.
Causa in `main.py:_varianti` — il nome breve di un documento e' la prima parola
oltre tre lettere, e i cinque cataloghi Packara cominciano tutti con «Packara».
`_documento_vicino` risolveva «Pagina 27» su BOW COLLECTION invece che su
CELEBRATION, e la coda diceva «Fonti — pagine 5» sotto una risposta che ne
citava cinque: il sistema smentisce il proprio risultato corretto.

La causa non e' l'euristica sbagliata: e' che **le fonti si ricavano
rileggendo la prosa**. Il modello scrive «Pagina 30» e il codice indovina a
quale documento appartiene. Con il Redattore che emette `[[id]]`, l'id e' la
riga vera, con `source_id`, `documento` e `page` gia' dentro: il collegamento
si costruisce, non si indovina.

Spariscono `_varianti`, `_documento_vicino` e la finestra di 80 caratteri.
Il diff e' negativo.

---

## 5. Stato: implementato e misurato (30/09/2026)

`orchestratore/operatori.py` + `orchestratore/grafo.py`, accesi con `GRAFO=1`.
Spento di default: a flag spento il flusso di oggi e' identico, riga per riga.

Confronto sulla stessa domanda, «ho bisogni di nastri bianchi con cuori
rossi», contro il flusso attuale (`SQL_AGENTE=1`):

| metro | oggi | grafo |
|---|---|---|
| **citazioni diventate collegamento** | **1 su 5** | **7 su 7** |
| coda «Fonti» | diceva «pagine 5» sotto 5 citazioni | coincide col corpo, per costruzione |
| **pagine giuste citate** (ne esistono 7) | 5 | **7 su 7** |
| righe portate in risposta senza c'entrare | 20 su 27 nel pool | 0: il critico le scarta prima |
| secondi | 43 | 82 |

Le pagine giuste sono 5, 27, 29, 30, 33, 39, 40 del catalogo Packara
CELEBRATION: tutte e sette citate, tutte e sette cliccabili. Il flusso di oggi
ne citava cinque e ne collegava una.

Altre domande, per vedere se il coordinatore distingue davvero i casi:

| domanda | mosse | secondi | esito |
|---|---|---|---|
| «ciao, come va?» | coordinatore → redattore | 3 | non cerca, risponde al saluto |
| «quanti cataloghi ci sono?» | coordinatore → cerca → coordinatore → redattore | 4 | «in archivio ci sono 14 cataloghi» |
| «mi servono delle decorazioni» | cerca → verifica → rispondi | 115 | 18 articoli, 18 collegamenti |
| «mi serve il rosso» | analista → **chiedi** | 27 | non cerca: fa una domanda |

### Gli `if` che non reggevano

Rivedendo il codice riga per riga invece di difenderlo a blocco, tre
condizioni non erano «rendering e permessi» come avevo detto: erano decisioni
travestite.

1. **Il verdetto del VLM letto con `startswith(("si","yes"))`.** Un «direi di
   si» non cadeva in nessun ramo e la riga restava col verdetto vecchio, in
   silenzio: lo stesso ripiego-da-regex tolto dappertutto, sopravvissuto qui
   perche' il VLM descrive e non chiama strumenti. Riparato separando i due
   lavori: l'osservatore GUARDA e riporta la frase, il critico rilegge quella
   frase e rifa' il verdetto. Una riga appena guardata torna da verificare —
   prima il critico non riguardava mai una riga gia' giudicata, quindi la
   foto veniva aperta per niente.
2. **Il tetto sui passi scartava in silenzio la mossa appena scelta.** Il
   coordinatore sceglieva `cerca`, `_prossimo` buttava via la scelta e andava
   a scrivere: un giro sprecato e una decisione cancellata senza dirglielo.
   Ora all'ultimo giro gli si offrono solo le mosse che chiudono: il limite si
   vede prima di scegliere.
3. **Il critico non rivedeva mai una riga gia' giudicata** (vedi il punto 1).

Due condizioni le ho tenute, ma sono decisioni del codice e vanno chiamate
cosi', non nascoste fra il rendering:

- se dopo due richieste il coordinatore non decide, il codice sceglie
  `rispondi`. E' un guasto, e un guasto non lo si puo' far decidere a chi si
  e' guastato — ma la scelta e' del programma.
- le righe `forse` non arrivano al redattore. E' una lettura di «confermate»
  fatta dal codice: il coordinatore potrebbe volerle mostrare con una
  riserva, e non ha modo di dirlo.

### Quattro difetti trovati implementando

Tutti e quattro erano errori di specifica miei, non del modello, e tutti e
quattro riparati sulla classe e non sul caso:

1. il critico scartava p.5 perche' gli avevo chiesto se la riga INTERA fosse
   il prodotto — ma una riga e' una foto, e una foto di catalogo mostra piu'
   articoli: basta che ci sia quello chiesto;
2. il critico scartava una riga di conteggio perche' la scheda mostrava solo
   `descrizione`/`content`: la riga arrivava vuota e lui non la VEDEVA. Le
   colonne le sceglie il coordinatore, quindi la scheda le mostra tutte;
3. le decisioni si leggevano con una regex (`CERCARE: si`, `3: si — ...`), e
   ogni regex portava con se' un ramo di ripiego — cioe' una scelta presa dal
   codice proprio quando il modello non era stato chiaro. Ora ogni decisione
   e' una chiamata di strumento con uno schema;
4. **la catena stessa.** Le frecce fra i nodi contenevano la strategia («se
   c'e' un forse vai dall'osservatore, se non c'e' nessun si torna a
   cercare»): tre `if` che decidevano la ricerca al posto del modello. Da qui
   la riscrittura a stato + coordinatore descritta nella sezione 3.

### Il dubbio: prima non scattava mai, poi si'

Senza analista la mossa `chiedi` non e' mai stata usata, in nessuna prova —
nemmeno su «mi serve il rosso», che restituiva piatti, dischi di musza e
mollette per il bucato, tenuti insieme solo dal colore.

Non era un difetto di informazione: lo stato conteneva tutte le righe con la
loro descrizione, quindi il coordinatore *vedeva* che erano oggetti
scollegati. Il punto e' che «ho trovato 16 righe verificate» gli legge come
successo. Chi guarda i risultati non si accorge che la domanda era mal posta:
il critico verifica la corrispondenza («e' rosso?» — si, lo e'), non la
pertinenza al bisogno, e nessuno guardava la domanda.

Con l'analista, sulla stessa domanda:

```
mosse : analista | coordinatore | redattore            27 s
manca : «l'oggetto specifico a cui si riferisce il rosso»
uscita: «Cerchi un prodotto specifico con il colore rosso o semplicemente
         informazioni su oggetti rossi?»
```

Il sistema smette di cercare e chiede. Ed e' lo stesso sguardo che sull'altra
domanda produce «un prodotto con due attributi, sta nelle foto» — cioe' dove
cercare. Valutare la domanda e capirne l'ambito sono lo stesso lavoro.

**`chiedi` non e' stabile.** Su «mi serve il rosso», due esecuzioni della
stessa domanda: la prima ha chiesto senza cercare, la seconda ha cercato e
risposto con due articoli. Stessa temperatura, stesso stato. Il dubbio adesso
esiste e a volte scatta, ma non si puo' ancora dire che scatti quando serve.

**Altre due cose imperfette, misurate e non nascoste.** Sull'altra domanda l'analista
ha segnalato che «mancano la larghezza e il prezzo», che e' proprio
l'over-segnalazione contro cui il suo prompt mette in guardia: il coordinatore
lo ha ignorato e ha cercato, giustamente, ma il giudizio era sbagliato. E su
«mi serve il rosso» ha chiesto PRIMA di cercare, mentre il prompt di `chiedi`
dice di guardare prima i dati: qui e' l'esito giusto (cercare «rosso» non
serviva a niente) ma per una regola che avevo scritto al contrario.

**Le domande aperte restano senza metro.** «Mi servono delle decorazioni»
produce 18 articoli collegati in 115 secondi. Se sia una buona risposta non
lo sa nessuno, perche' non esiste ancora un modo di misurarlo. E 115 secondi
sono troppi comunque: 79 li spende il redattore a scrivere 18 voci.

---

## 5-ter. La domanda che NON si risolve (30/09/2026, dalla chat vera)

Presa dall'ultima conversazione reale, secondo turno:

> «hai dei kit per albero di natale ... palline rosse o gialle»
> -> «Non ho trovato nulla che risponda alla domanda.» (144 secondi)

**E' un falso negativo, e grosso.** In archivio ci sono 53 righe con
`bauble|Kugel` che sono rosse o gialle.

Come si e' arrivati a dirlo: non guardando la risposta, ma la traccia. Tre
chiamate a `interroga`, tutte e tre a **0 ms e 0 righe** — cioe' tutte e tre
RESPINTE dalla regola sulle frasi (`'Christmas tree kit'`, `'red ball'`). Il
database non e' mai stato interrogato. Un «non ho trovato» da un sistema che
non ha guardato non e' un risultato negativo, e' un risultato mancante, e nel
contesto del modello un rifiuto arrivava indistinguibile da «zero righe».

**Il grafo nuovo, sulla stessa domanda, fallisce anche lui.** Diversamente, e
peggio per noi, perche' toglie l'alibi:

```
cerca: WHERE descrizione ~* 'red|yellow' AND descrizione ~* 'ball'   -> 40 righe
critico: 0 confermate su 40
coordinatore: chiedi
```

`SIMILE` e `TERMINI` erano disponibili e **non li ha usati**. Ha scritto
«ball», che nei cataloghi non compare mai: loro dicono `Kugel` (84 righe) e
`bauble` (40). E' la seconda volta che si misura: il modello non sa di non
sapere la parola, quindi la regola «usa SIMILE quando non sai come la
chiamano» non scatta mai — non si puo' accorgersi della propria ignoranza di
un vocabolario.

**E anche se l'avesse usato, non bastava.** Provate quattro forme a mano:

| query | esito |
|---|---|
| `ORDER BY SIMILE('kit per albero di natale con palline rosse o gialle')` | ghirlande, non palline |
| `WHERE ~* 'red\|yellow' ORDER BY SIMILE(...)` | le stesse ghirlande |
| `WHERE ~* TERMINI('palline') ORDER BY SIMILE(...)` | **0 righe** |
| `WHERE ~* 'bauble\|kugel' ORDER BY SIMILE(...)` | le palline giuste |

Il vettore, su queste didascalie, separa male «bauble» da «garland» dentro
«decorazione natalizia»: funziona solo chi gia' sa la parola. E `TERMINI` qui
fa danno: il glossario per «pallina» restituisce
`busta, produttore, accessoires, kerzen` — rumore, che come filtro azzera
tutto. Il glossario ha 6308 voci e almeno una parte e' spazzatura: va
misurato quanto.

**Cosa manca davvero.** Non un operatore in piu': il giro. La parola giusta
era nei risultati della prima ricerca (fra le ghirlande c'erano «Christmas
ornaments»), e bastava LEGGERLA e ricercare — che e' letteralmente la cosa che
diciamo al modello di fare. Il coordinatore aveva ancora 2 mosse di 5 e ha
scelto di chiedere alla persona invece di guardare cosa gli era tornato.

Questa e' la domanda su cui misurare il prossimo lavoro. E' piu' dura di
«nastri bianchi con cuori rossi», dove il modello indovinava «ribbon» e
l'indovinello era giusto.

---

## 5-quater. Quattro tentativi sbagliati e uno giusto (1/10/2026)

Il caso della chat («kit per albero di natale ... palline rosse o gialle»)
e' diventato il banco di prova. Cronaca onesta, perche' il metodo conta piu'
del risultato.

**Quattro interventi, tutti della stessa famiglia, tutti a vuoto:**

| # | cosa ho aggiunto | esito |
|---|---|---|
| 1 | il «vicinato» sulle ricerche a vuoto | mai scattato: la query dava 40 righe, non zero — l'avevo attaccato al ramo sbagliato |
| 2 | i motivi del critico visibili nello stato | visti, ignorati |
| 3 | `rispondi` negato a chi non ha mai eseguito una query | non pertinente qui (`1ok/0ko`) |
| 4 | un paragrafo «quelle righe sono il vocabolario, non chiedere alla persona» | ha chiesto alla persona proprio quella parola |

Quattro volte la diagnosi era «gli manca informazione», e quattro volte
l'informazione c'era gia'. Il quarto tentativo e' quello che chiude la
questione: il testo diceva letteralmente «chiedere alla persona una parola
che ce l'hai sotto gli occhi non la aiuta», e lui ha risposto «hai un termine
specifico per kit per albero di Natale nei cataloghi?».

**Lezione di metodo:** quando due interventi di fila della stessa famiglia non
spostano niente, il terzo della stessa famiglia non li salvera'. Il problema
non era cosa il coordinatore LEGGE.

**L'intervento che ha cambiato le cose:** il coordinatore decideva con il
**ragionamento spento**. `_decide` usava `ragiona=False` per tutti, default
dei passi meccanici che avevo copiato sul giudizio piu' difficile del grafo.
In `agente.py` il ragionamento era acceso esattamente per questo motivo,
scritto nel commento: «col ragionamento il modello puo' capire "ho gia' la
risposta, mi fermo"».

```
ragiona=False : 1 query  -> 40 righe -> 0 confermate -> chiedi     0 collegamenti
ragiona=True  : 4 query su DUE tabelle, 5 passi -> risponde       10 collegamenti
```

Con il ragionamento il coordinatore insiste: prova `kit|set` + `ball|ornament`
(0 righe), stringe, passa a `chunks`, torna indietro. E' il ciclo che persegue
l'obiettivo, e prima semplicemente non c'era.

**Il difetto che il successo ha portato a galla, e la sua correzione.** Con
il ragionamento acceso, una esecuzione su tre rispondeva SENZA verificare:
`confermate=0` e dieci citazioni. La colpa non era del modello ma di tre
decisioni del CODICE in `_nodo_redattore`, e la peggiore mentiva:

```python
confermate = [... if verdetti[n] == "si" ...]   # buttava via i «forse»
if righe and not verdetti:
    confermate = righe                          # promuoveva a verificate righe mai guardate
```

Al loro posto non c'e' un altro `if` ma `_da_consegnare`, che **riporta e
basta**: ogni riga passa con l'etichetta vera (`si` / `forse` / `non
verificata`). L'unica che non passa e' quella che il CRITICO ha scartato, e
quella non e' una scelta del codice: e' obbedire a un agente che ha giudicato.
Il comportamento e' andato nei prompt — il redattore deve dire a chi legge
quando una riga non e' stata controllata, il coordinatore sa che `rispondi`
consegna anche il grezzo.

**I numeri finali**, due esecuzioni ciascuna dopo la correzione:

| domanda | prima di tutto | dopo |
|---|---|---|
| «kit albero di natale, palline rosse o gialle» | 0 collegamenti, domanda inutile, 3 volte su 3 | **4 e 8 collegamenti, tutti verificati** |
| «nastri bianchi con cuori rossi» | 5 citate / 1 collegata | **7 e 7, tutte verificate** |
| «ciao, come va?» | — | 1 mossa, nessuna ricerca |
| citazioni diventate collegamento | 1 su 5 | **100% in ogni esecuzione** |
| verifica saltata | — | 0 su 2 (era 1 su 3) |
| secondi | 43-144 | 69-130 |

La risposta di Natale ora e' concreta: palline rosse con codice articolo,
pagine 20 e 233 di INGE Holly&Jolly — trovate dopo che la prima query era
andata a vuoto e il coordinatore aveva allargato da solo a `ornament|ball`.

**Cosa NON e' stato esercitato:** in tutte e quattro le esecuzioni il
coordinatore ha chiamato `verifica`, quindi l'etichetta «non verificata» non
e' mai comparsa in una risposta vera. Il percorso c'e' ed e' provato dai test,
ma non l'ha ancora visto un utente.

---

## 5-quinquies. La chat vera del 30/09 sera: quattro cause, una sola mia diagnosi giusta

Due turni in chat col grafo acceso. Il primo va (palline rosse con codice
articolo, pagine 20 e 233). Il secondo, «niente gialle?», risponde con una
ghirlanda verde, della lametta e un quadro preso da una GUIDA DOGANALE.

Le cause, in ordine di quanto mi hanno fatto perdere tempo.

**1. `esiti` veniva riempito e mai letto** — la causa piu' grossa, e mia.
`_nodo_mosse` costruisce `esiti` con dentro le spiegazioni dei rifiuti
(«scrivi UNA parola per condizione»), lo zero-righe col vocabolario del
vicinato, l'esito della verifica. Lo stato lo portava e `_stato_a_parole` non
lo leggeva. Il coordinatore vedeva due contatori — «0 eseguite, 4 respinte» —
e non sapeva PERCHE': riscriveva la stessa query identica. Misurato: quattro
query, due uguali fra loro, 81 secondi, zero ricerche.

Questo ribalta la «lezione di metodo» della sezione precedente. Avevo concluso
che «il problema non era cosa il coordinatore LEGGE» dopo quattro tentativi a
vuoto — ma tre di quei quattro li avevo scritti dentro `esiti`. Non erano
ignorati: non gli sono mai arrivati. La diagnosi giusta era la prima, guardata
nel posto sbagliato.

**2. Il critico non riceveva la conversazione.** Analista, coordinatore e
redattore hanno `storia`; il critico aveva solo `stato['domanda']`, cioe' la
stringa «niente gialle?». Giudicata cosi', una ghirlanda verde con riflessi
gialli risponde davvero: ne ha confermate 14 su 20. L'unico agente senza il
contesto era quello il cui mestiere e' dire «questo risponde alla domanda».

**3. L'analista rispondeva con una CATEGORIA.** Su «niente gialle?»,
tre volte su tre: «Un prodotto con due attributi, sta nelle foto» — la frase
d'esempio del suo stesso prompt, che vale per mille domande e non contiene
la parola «gialle». Gli avevo chiesto «di cosa parla la domanda» dando come
esempi delle etichette, e restituiva un'etichetta. Il campo ora e' «cosa
vuole la persona ADESSO, per esteso, come lo spiegheresti a un collega che
non ha letto la chat», con i tre modi di sbagliarlo scritti accanto.

Effetto collaterale non cercato: e' sparita anche la sovra-segnalazione di
`manca`, che su una domanda completa adesso e' vuoto.

**4. `NOT ~*` non esiste in Postgres** (l'operatore e' `!~*`). Il modello lo
ha inventato e ci ha sbattuto contro per quattro query su cinque, 262
secondi. E' un fatto del dialetto che nessuno gli aveva detto: sta nella
mappa, accanto a «non usare LIKE con %».

**Un mio peggioramento, introdotto e ritirato lo stesso giorno.** Avevo
scritto nel prompt del coordinatore che un seguito «e' una RESTRIZIONE:
aggiungi il vincolo alla ricerca di prima». Il modello ha aggiunto il vincolo
come NEGAZIONE — cercava le palline ESCLUDENDO il giallo, che e' esattamente
quello che la persona voleva. Prima sbagliava per larghezza, dopo la mia
frase sbagliava per esclusione: una regola travestita da spiegazione, peggiore
del difetto che correggeva. Sostituita con il modo di leggere, non con la
regola — e spostata sull'analista, che e' l'agente il cui mestiere e' capire
la domanda.

**Non era nostro:** il nome storpiato nel link
(`GUIDA DGUIDA DOGANALE...pdfOGANALE...pdf`) e' il nome vero del file su
disco. L'ingestione ha registrato fedelmente qualcosa gia' corrotto
all'origine: 126 pezzi e 23 figure lo portano. Va rinominato nella cartella
condivisa e re-ingerito.

---

## 5-sexies. Cinque conversazioni avversarie (1/10/2026, notte)

Cinque dialoghi di piu' turni, scritti per rompere il sistema in modi diversi,
rigirati a ogni correzione. Ogni turno riceve le risposte VERE dei turni
precedenti.

| | mette alla prova |
|---|---|
| **A** nastri -> «e di quelli piu' larghi?» -> «quanto costano?» | seguito che restringe, poi salto dalle foto al testo |
| **B** «palloni giganti gonfiabili?» -> «cosa avete di sportivo?» | dire che non c'e', poi aprirsi |
| **C** «qualcosa di blu» -> «per un matrimonio» -> «si» | dovrebbe CHIEDERE, poi capire la risposta, poi un consenso nudo |
| **D** «ciao» -> «che documenti avete?» -> «quante foto ha Gasper?» -> «e nastri con le stelle?» | saluto, archivio, conteggio, cambio brusco |
| **E** «vasi con cuori rossi» -> «no, i cuori SUL vaso» | attributo legato all'oggetto, e una correzione |

### Difetti trovati e riparati

**1. Un contratto implicito che inganna il chiamante.** Il difetto piu'
vistoso del primo giro — l'analista che descrive la domanda PRECEDENTE — era
della simulazione, non del sistema: in produzione `_storia` contiene gia'
l'ultima domanda, il banco di prova la aggiungeva dopo. Stavo per correggere
un bug inesistente. Ma sotto c'era un difetto vero: `cerca()` si FIDAVA che il
chiamante mettesse la domanda in fondo. Adesso lo garantisce, in un punto
solo, per tutti e tre gli agenti.

**2. `ORDER BY SIMILE(...) DESC` e `WHERE SIMILE(...) > 0`.** Due trappole
insieme: una distanza e' sempre maggiore di zero (quindi non filtra), e `DESC`
restituisce le righe PIU' LONTANE. Il modello ha chiesto al database l'esatto
contrario di quello che voleva. Scritto nella mappa.

**3. «Tutte le informazioni sono state verificate», sotto sei righe
confermate su venti.** Riscritto anche DOPO un divieto esplicito nel prompt:
due tentativi, due fallimenti. Non e' un problema di severita' del testo: lo
stato di verifica e' PROVENIENZA, come il collegamento alla pagina e la coda
delle fonti — cose che il codice sa con certezza e che infatti non sbaglia
mai. Tolto di mano al redattore: `_nota_verifica` conta le righe davvero
citate e scrive «2 delle 3 voci qui sopra non sono state verificate una per
una». Stesso principio che aveva gia' risolto i collegamenti.

**4. `chiedi` scattava al contrario.** Non scattava su «qualcosa di blu» e
scattava su «quanto costano?» con ZERO query eseguite — cioe' chiedeva alla
persona di fare il lavoro. Ora non viene offerto finche' almeno una ricerca
non e' andata a buon fine, stesso schema gia' usato per `rispondi`, con
l'eccezione dell'ultima mossa perche' il turno deve poter chiudere.

**5. La regola sulle frasi era giusta ma troppo grossolana.** Guardava il
filtro intero: se UNA alternativa dentro i `|` aveva uno spazio, respingeva
tutto. `~* 'star|star motif|stellar'` respinta, con un messaggio che diceva
che quel filtro «non trova niente» — falso, `star` da sola ne trova quaranta.
Il modello non sapeva quale pezzo togliere e riscriveva lo stesso: quattro
query respinte, zero ricerche, «non trovo nastri con le stelle» su un archivio
che ne e' pieno. Adesso l'errore nomina l'alternativa colpevole e suggerisce
il filtro ripulito. Effetto: da 0 a 23 citazioni collegate.

**Lezione che si ripete:** i difetti 5 e quello di `esiti` sono la stessa
cosa. Non mancava il controllo, mancava che il controllo dicesse qualcosa di
AZIONABILE. Un errore che non indica quale pezzo e' sbagliato non e'
correggibile, e il modello ci sbatte contro finche' non esaurisce i passi.

**6. Ripeteva la stessa query.** «Quante foto ha Gasper?»: quattro SELECT
IDENTICHE, tutte eseguite, tutte a zero righe (`documento = 'Catalogo Gasper
Primavera Estate 2026'`, senza «.pdf»), poi si arrende. `agente.py` aveva il
controllo sulle ripetizioni e non l'avevo portato nel grafo. Adesso alla
seconda query identica riceve il fatto, non un divieto.

**7. Il «vicinato» non scattava sulle tabelle senza vettore.** Su `documenti`
non si puo' ordinare per significato, quindi dopo lo zero-righe il modello non
vedeva mai come sono scritti i nomi veri. Ma quelle tabelle sono piccole per
natura (14 righe): la risposta alla domanda «non c'e' niente o ho sbagliato le
parole?» e' mostrargli cosa contengono.

**8. E quando ha cominciato a scattare, era illeggibile.** `SELECT *` su
`documenti` riversava impronte da 64 caratteri e timestamp, e l'unico dato
utile — `figure_totali: 3352` — usciva troncato a meta' numero dal limite di
lunghezza. Adesso il vicinato mostra le colonne che aveva chiesto il MODELLO,
non tutte. L'aiuto c'era e non si poteva leggere: un difetto che senza
guardare l'output vero non si vede.

**Un bug introdotto da me e scoperto solo perche' ha fatto esplodere un
turno**: `NameError: TABELLE is not defined` (sta in `sql_agente`, non in
`operatori`). Le prove non toccavano quel ramo perche' vuole il database. Ora
`_prova()` ci passa dentro con `esegui` sostituita: ogni ramo non banale
lascia dietro un controllo eseguibile, o il primo che lo scopre e' l'utente.

### Dove siamo

Otto giri delle cinque conversazioni. Tempi: media ~66-80 secondi, contro
~130 del primo giro.

**Non sbaglia piu':**
- citazioni e collegamenti coincidono in OGNI turno di OGNI giro;
- il saluto non cerca mai (8-10 s);
- «palloni gonfiabili» dice che non ci sono, citando il quasi-uguale in vetro;
- l'analista segue la conversazione in tutti i seguiti, inclusa la correzione
  esplicita «no, intendevo i cuori SUL vaso»;
- la nota sulle righe non verificate compare quando serve, scritta dal codice;
- «nastri con le stelle» e «quanto costano?», che fallivano, adesso rispondono.

**Resta rotto, ed e' il caso che conta di piu':** «mi serve qualcosa di blu»
non fa scattare `chiedi` in nessuno degli otto giri. Cerca `~* 'blue'`, il
critico ne conferma 24 su 40, e la risposta elenca mappamondi presi da una
guida doganale — con sicurezza, senza avvisi. Il critico non puo' salvare la
situazione, perche' un mappamondo blu soddisfa DAVVERO «qualcosa di blu»:
nessuno nel grafo ha il compito di dire «questa domanda e' troppo vaga per
rispondere». L'analista lo vede (`manca: l'oggetto specifico`) e il
coordinatore sceglie di rispondere lo stesso.

E' la prossima cosa da decidere, ed e' una scelta di progetto prima che di
codice: quando una domanda e' troppo vaga, chi ha l'autorita' di fermare la
risposta?

---

## 5-septies. Il tempo: dove va, e cosa NON lo riduce (1/10/2026)

Profilo misurato su tre domande, non stimato:

| nodo | quota | chiamate/turno | ms per chiamata |
|---|---|---|---|
| **coordinatore** | **59-71%** | 3,7 | 14 200 |
| redattore | 12-22% | 1 | 13 500-21 600 |
| critico | 11-13% | 0,7 | 16 700 |
| analista | 3-9% | 1 | 2 900-7 600 |
| **cerca (database)** | **0,1-0,7%** | 2 | **50-375** |

**Il database e' gratis.** Il tempo e' tutto chiamate al modello, e sei
decimi sono il coordinatore chiamato quasi quattro volte a turno.

Le cinque ipotesi, classificate per quello che sono:

| | cosa e' | esito |
|---|---|---|
| streaming del redattore | idraulica | **fatto, rende 8s** (ne avevo previsti 17) |
| piu' ricerche in parallelo | prompt | **provato e RITIRATO**: +42% per chiamata, giri invariati |
| ragionamento a tratti | `if` in codice | non si fa |
| critico a pezzi paralleli | idraulica | da fare, ~8s |
| accorciare lo stato | `if` in codice, del tipo pericoloso | non si fa — e' il bug di `esiti` |

**Lo streaming** funziona (316 pezzi, citazioni risolte nel flusso, nessun
segnaposto che trapela) ma rende meno di quanto avevo detto: la prima parola
arriva al 67° secondo su 75, perche' il redattore parte comunque per ultimo.
Si risparmia la sua durata, non di piu'.

**Il parallelismo via prompt e' fallito.** Avevo aggiunto al prompt del
coordinatore i costi veri (50 ms una ricerca, 14 s un giro) per convincerlo ad
aprire piu' strade insieme. Risultato: coordinatore da 14,2 a 20,2 secondi per
chiamata, totale da 265 a 340 secondi, e il numero di giri **invariato**
(3,7 -> 4,0). Ventiquattro secondi a turno pagati per niente. Ritirato; con la
versione corta si torna a 17,4 s/chiamata e 299 s — parte del recupero, il
resto e' dentro la varianza di questo sistema.

**Conclusione onesta: il tempo non si riduce di molto senza toccare cose che
non vogliamo toccare.** Il 71% e' il coordinatore che pensa, e le tre leve che
lo accorcerebbero davvero rimettono il programma a decidere per il modello.
Restano pulite: il critico a pezzi paralleli, e `qwen3-8b` sui nodi meccanici
(che dipende dallo stesso nodo VRAM del VLM).

**Difetto trovato dallo streaming e corretto:** il redattore si inventava gli
URL — `[pagina 40](link vero)¹(https://example.com/page40)`. Un indirizzo che
scrive lui e' inventato per definizione: non ha modo di conoscerlo. Vietato
esplicitamente nel prompt, e nell'ultimo giro non compare piu'.

---

## 5-septies-bis. Il ragionamento lo decide un agente (1/10/2026)

Idea dell'utente, e ha reso piu' di tutto quello che avevo provato io.

Avevo classificato «ragionamento solo quando serve» come `if` in codice,
quindi da non fare — perche' pensavo a `ragiona = (c'e' stato un
fallimento)`, che e' una regola del programma su COME deve pensare il
modello. Ma se a deciderlo e' un AGENTE non e' determinismo: e' lo stesso
schema del critico e dell'analista.

Il posto giusto e' l'analista: giudica gia' la domanda, e' il suo mestiere, e
gira comunque — quindi la decisione costa zero chiamate. Gli si chiede una
terza cosa, «quanto e' impegnativa», con i casi scritti accanto (un saluto o
un dato secco: no; un nome che il catalogo scrive in un altro modo, una
risposta in due posti, una domanda aperta, un seguito: si). Nel dubbio,
impegnativa.

**Distingue 6 casi su 6**, inclusi quelli che conosciamo bene.

| | prima | dopo |
|---|---|---|
| tre domande, totale | 299 s | **174 s** |
| coordinatore, per chiamata | 17,4 s | **9,2 s** |
| coordinatore, chiamate | 11 | **9** |
| coordinatore, quota del tempo | 64% | 48% |
| «nastri bianchi con cuori rossi» | 85 s | **42 s** |

**Taglio del 42%.** E il collo di bottiglia si e' spostato: adesso il piu'
caro per singola chiamata e' il CRITICO, 18,5 secondi, un terzo del totale.
Quello si taglia con l'idraulica (righe indipendenti, chiamate parallele),
senza toccare nessuna decisione.

**Lezione:** la distinzione utile non e' «prompt contro codice», e' CHI
decide. Una regola del programma su come pensa il modello e' determinismo;
la stessa scelta presa da un agente il cui mestiere e' giudicare la domanda
non lo e'. Le tre leve che avevo scartato come «`if` in codice» andavano
riesaminate una per una con questo criterio, e non l'avevo fatto.

---

## 5-octies. L'autorita' di fermare una risposta (proposta, NON implementata)

Il caso aperto: «mi serve qualcosa di blu» risponde con sicurezza a una
domanda che nessuno ha capito. L'analista lo vede e lo scrive (`manca:
l'oggetto specifico`), il coordinatore lo legge e risponde lo stesso.

Il pezzo che manca non e' un controllo in piu': e' che **il giudizio
dell'analista non pesa niente**. Proposta: una dichiarazione impegnativa
accanto alla descrizione — «si puo' rispondere cosi' com'e': si / no», col
criterio che ha gia' nel prompt (*cercando cosi', torna qualcosa di utile o un
mucchio?*). Poi `rispondi` non viene offerto finche' il giudizio e' «no».
`chiedi` resta sempre disponibile: il sistema non si blocca, chiede.

E' lo schema gia' usato tre volte — non si offre una porta che lo stato ha
chiuso — con la differenza che conta: **a chiudere la porta e' un altro
agente**, il programma trasporta un verdetto e non lo emette. Due valvole,
perche' l'analista sbaglia: se il critico ha confermato delle righe
l'evidenza batte la previsione, e all'ultima mossa si chiude comunque.

Non implementata perche' cambia il carattere del prodotto: piu' prudente, piu'
lento, e qualche volta chiedera' quando non serviva. E' una scelta di
prodotto, non di codice.

Se non basta, la risposta piu' grossa e' un agente che legge la risposta GIA'
SCRITTA e giudica se serve a chi ha chiesto: oggi il critico verifica la
corrispondenza riga-domanda, nessuno verifica risposta-bisogno. Costa un'altra
chiamata da ~15 secondi.

---

## 5-nonies. Parallelismo: legato al server, non scritto a mano

Il critico spezzato in gruppi paralleli e' stato **provato, misurato e
ritirato**: da 18,5 a 29,4 secondi. Il motivo non era nel codice ma nel
server — in llama-swap il 14b girava con `--parallel 1`, quindi serve una
richiesta alla volta: i tre thread si accodavano, e tre chiamate
rispediscono tre volte prompt e conversazione.

**Regola generale:** qualunque parallelismo che passi dal MODELLO vale solo
se il server ha piu' slot. Quello sulle QUERY e' un'altra cosa e funziona
sempre, perche' li' a servire e' Postgres.

Quindi il numero non si scrive da nessuna parte: **lo si chiede al server**,
con `modello.slot()`. Il giorno che in produzione si alza `--parallel`, il
critico se ne accorge da solo e si spezza; oggi che ne vede 2, si spezza in
2. Nessuna modifica al codice, mai.

`_interroga_il_server()` e' **l'unica funzione che sa com'e' fatto il
server**: prova gli indirizzi noti (llama-swap mette ogni modello dietro
`/upstream/<nome>/`, llama.cpp espone `/props` sulla radice) e torna None se
non lo sa. Tutto il resto chiama `slot()` e ignora prodotti ed endpoint: se
un giorno si passa a vLLM o a un'API, **si riscrive solo quel corpo**.
`MODELLO_SLOT` lo forza, per un server che non lo dichiara.

Due dettagli che costano se li si sbaglia:

- un valore non scoperto NON si memorizza. Il caso tipico e' il modello non
  ancora caricato: ricordarsi quell'1 vorrebbe dire lavorare in fila per
  cinque minuti su un server che ne regge due (visto dopo un riavvio);
- `--ctx-size` in llama.cpp e' il contesto TOTALE diviso fra gli slot.
  Misurato: il coordinatore usa ~8.000 token di stato piu' 8.192 di
  ragionamento, quindi servono ~16.200 token PER SLOT. Oggi:
  `--parallel 2 --ctx-size 65536` (32.768 a slot, VRAM 14,7 su 16,3).
  `--parallel 4` vorrebbe 131.072 e la KV cache non entra.

---

## 6. Ordine di lavoro

1. **Allineare la configurazione** (vedi sezione 7): oggi gira una cosa
   diversa da quella scritta su disco, e finche' e' cosi' ogni confronto
   misura due sistemi credendo che sia uno.
2. **Metro per le domande aperte.** Viene prima di tutto il resto: senza, del
   grafo si puo' dire solo che collega le fonti, non che risponde meglio.
3. **I secondi.** 82 s contro 43, e 115 sulle domande aperte (79 li spende il
   redattore a scrivere 18 voci). Va capito se pesa il numero di voci o la
   lunghezza di ognuna, e se l'analista si puo' fondere col primo giro del
   coordinatore.
4. **L'analista che sovra-segnala** («manca la larghezza» su una domanda
   completa): innocuo oggi perche' il coordinatore lo ignora, ma e' un
   giudizio sbagliato e prima o poi verra' seguito.
5. **Osservatore**, dopo aver misurato la VRAM col 14b nel gruppo `grandi`.
6. **Cancellare `_varianti` e `_documento_vicino`** da `main.py`, quando il
   grafo sara' il flusso unico: con `[[n]]` non servono piu' a nessuno.

I punti 1, 2 e 3 non producono codice. Sono quelli che decidono se il resto
serve.

---

## 7. Da fare: la banca dei ricordi

Idea dell'utente: una memoria di «spunti furbi», presi dalle domande che hanno
richiesto piu' passaggi prima di capire cosa servisse davvero.

**Chi la usa, e come.** Il COORDINATORE, nello stato che gia' legge, accanto
al giudizio dell'analista. Non e' un agente nuovo e non e' una mossa nuova:
e' una riga in piu' davanti a chi decide — «altre volte, per domande simili,
hanno funzionato questi termini; questi no». Non riscrive niente: se il
suggerimento e' sbagliato, il modello lo scarta come scarta una riga che non
c'entra. E non la scrive nessun agente: la estrae il codice dalla traccia,
a turno finito.

**Il monito e' in casa.** `glossario` e' gia' questo, e ha 6308 voci fra cui
`pallina -> busta, produttore, accessoires, kerzen`: usarlo azzera la query.
Ha fallito per tre ragioni, e ognuna diventa la regola opposta:

| perche' il glossario e' rumore | regola |
|---|---|
| scrive quello che un modello PROPONE | si scrive solo quello che ha FUNZIONATO |
| non conserva da cosa viene | ogni ricordo porta la sua prova |
| viene APPLICATO in automatico | si OFFRE, e il modello puo' ignorarlo |

**Il ricordo**, estratto dalla traccia, senza che nessun modello lo scriva:

```
domanda           : le parole della persona, + embedding (serve al recupero)
ha funzionato     : i termini dentro i ~* della query che ha prodotto conferme
non ha funzionato : i termini delle query finite a zero o respinte
dove              : quale tabella
fonti             : i source_id delle righe confermate
```

I termini si tirano fuori dalla SQL con una regex sulle stringhe dentro `~*`:
**codice, non modello**. Non si puo' allucinare un termine che compare in una
query che ha davvero prodotto righe confermate. Se la query vincente era solo
`SIMILE`, il ricordo e' «qui e' bastato il significato, non filtrare».

**Quando si scrive:** solo se il turno ha richiesto piu' di un giro di ricerca
E si e' chiuso con conferme. Al primo colpo non c'e' lezione; senza conferme
memorizzeremmo un fallimento. Questo filtro taglia quasi tutto il volume.

**Perche' non marcisca:** ogni ricordo tiene due contatori — quante volte e'
stato offerto, e quante volte il turno che seguiva e' finito con conferme. Uno
offerto venti volte e mai correlato a un successo si cancella: e' una misura,
non un'opinione. E un tetto basso, qualche centinaio di righe: se la tabella
non si puo' leggere tutta a occhio e dire «questa e' spazzatura», marcira'
come l'altra.

**ACL, e non e' un dettaglio.** «bauble/Kugel» viene da Holly&Jolly, che
appartiene a certi gruppi: un ricordo e' vocabolario ESTRATTO DA DOCUMENTI e
va recuperato con lo stesso predicato su `sources` di tutto il resto,
altrimenti perde a un'area il vocabolario di un'altra.

**Cosa NON metterci:** consigli in prosa scritti dal modello («ricorda di
cercare bene»), che non sono falsificabili; e sinonimi generati senza una
ricerca che li abbia validati — cioe' il glossario.

**Limite, detto chiaro:** non salva la PRIMA persona che chiede. Il primo
«palline rosse» fallisce lo stesso. Serve dalla seconda volta in poi. E'
complementare alle correzioni della sezione 6, non un sostituto: il vicinato
aiuta la prima volta, il ricordo evita di ripagare lo stesso prezzo.

---

## 8. Da fare: razionalizzare i container

Sono cresciuti strada facendo e la cosa e' sfuggita di mano. I numeri, non
le impressioni (1/10/2026):

**16 servizi definiti**, 15 accesi. Solo `ingestion` e' spento, ed e' spento
per sbaglio dal 29/09 (vedi sezione 6).

**12 su 16 non compaiono in `ARCHITETTURA.md`**:

| documentati | assenti dall'architettura |
|---|---|
| orchestratore, postgres, azioni, ingestion | amministrazione, caddy, connettori, dagster-daemon, dagster-webserver, keycloak, librechat, mongo, oauth2-proxy, oauth2-proxy-immagini, oauth2-proxy-uptime, uptime-kuma |

**717 righe** fra `docker-compose.yml` (539) e l'override (178).

Cose che saltano all'occhio e andranno guardate con calma:

- **tre `oauth2-proxy`** (principale, immagini, uptime). Probabilmente giusto
  — proteggono origini diverse — ma non e' scritto da nessuna parte perche'
  ce ne vogliono tre invece di uno con piu' regole.
- **due servizi Dagster** (daemon e webserver) che, verificato oggi, fanno
  solo le importazioni ERP ogni 15 minuti: non toccano l'indicizzazione dei
  documenti, che gira in `ingestion` ed e' tutt'altra cosa. Il daemon logga
  «No heartbeat received, shutting down» in ciclo dal 18/09 — rumore noto,
  ma nessuno ha controllato se sia innocuo davvero.
- **`uptime-kuma` + un oauth2-proxy dedicato** per monitorare uno stack che
  gira su una macchina sola.
- due database (`postgres` e `mongo`), il secondo solo per LibreChat.

Non e' una pulizia da fare di corsa: e' un inventario da scrivere (a che
serve ognuno, chi lo chiama, cosa si rompe se lo spegni) e poi, con quello
in mano, decidere cosa unire. Il primo passo e' la tabella, non il
`docker compose rm`.

---

## 8-bis. Proposta, NON implementata: le righe come struttura, non come riga

**Da non fare senza chiedere** (deciso il 2/10/2026).

Oggi il foglio delle righe arriva al redattore cosi':

    [[1]] (controllato) (cuori si, blu non detto) (dal catalogo P.pdf) ribbon with heart motifs

Il difetto non e' che sia poco leggibile: e' che mette sullo stesso piano
cinque cose di natura diversa, tutte fra parentesi. `[[1]]` va SCRITTO tale
e quale; la descrizione va RACCONTATA; «controllato» va TRASMESSO;
«cuori si, blu non detto» e' un'ISTRUZIONE di onesta'; il catalogo e'
contesto. Una sintassi sola per cinque ruoli, e il modello sbaglia
esattamente li'.

E sopra a questo c'e' la cosa misurata tre volte il 2/10/2026: **il
redattore ricopia la forma che gli dai.** Ha scritto `**[SI]**` nella
risposta, poi `(verificata)` fra parentesi in mezzo alla prosa, poi «ho
trovato queste righe che parlano di nastri blu» — tre difetti con una causa
sola. Una riga di frammenti fra parentesi E' prosa annotata, quindi la
incolla come prosa.

La forma proposta non si puo' incollare in una frase:

    <articolo n="1">
      <scrivi>[[1]]</scrivi>
      <cosa>ribbon with heart motifs</cosa>
      <controllo>controllato</controllo>
      <avvertenza>cuori si, blu non detto</avvertenza>
    </articolo>

Costo: due o tre volte i token del foglio, su venti righe.

Cautela, e vale piu' dell'argomento: il 2/10/2026 ho misurato DUE volte che
riformattare cambia il comportamento in modo imprevedibile — spezzare un
paragrafo in due voci di elenco, stesse regole e stesso ordine, ha portato
il banco da 42/45 a 35/45. Quindi «meglio in principio» qui non vale: si
misura prima e dopo, con il giudice tarato, o non si fa.

## 8-ter. Tre cose misurate il 3-4/10/2026, e un buco nel banco

**La struttura schiacciata in prosa.** Sette difetti diversi, una causa sola:
la didascalia e' un record (`object:`, `material:`, `colours:`, `code:` —
completo nel 94% delle 15.143 righe) tenuto in un `text`, e sei agenti la
ri-analizzano a occhio a ogni turno. Il taglio a 300 caratteri tagliava via
`Colours:` e il critico scartava i sassi rossi perche' «red» stava al
carattere 450; l'embedding calcolato su tutto il blob fa somigliare «qualcosa
di blu» alle foto delle persone. Primo pezzo riparato: l'analista consegna la
richiesta SCOMPOSTA (`oggetto`, `attributi` col loro tipo, `dove`), lo stato
la dice in prosa coi dati dentro, e il coordinatore ha perso 1100 caratteri
di regole che rifacevano quel lavoro.

**Vince l'ordine, non la ripetizione.** Una regola scritta in DUE punti del
prompt usciva sbagliata 9 volte su 9 (`impegnativa` su domanda non
rispondibile). Scritta UNA volta, come cancello in testa al blocco e prima
dell'elenco dei casi, esce giusta 9 su 9. Un campo che dipende da un altro
deve leggere per primo la sua dipendenza: messa in mezzo alle alternative, la
dipendenza non vale.

**Tagliare un prompt costa piu' che aggiungerci.** Tolti 561 caratteri da
`MAPPA_OPERATORI` credendoli spiegazioni ridondanti: il banco e' passato da
29/30 a 22/30 e 21/30, due giri. Dentro c'erano tre frasi con numeri
misurati, e una sola — l'esempio concreto `'red' AND 'stone|rock|pebble|
gravel'` — era la differenza fra otto sassi rossi veri e una risposta che
presentava del MUSCHIO come un sasso rosso.

**L'ancoraggio del confine di parola in codice: misurato e NON adottato.**
`~*` cerca lettere anche dentro le altre parole, e il modello scrive male il
token: `'\mbblue'` e `'\mb\mblue'`, query lecite che non possono
corrispondere a niente, 2 giri su 2 su «hai nastri blu lucidi». Mettere il
confine nel compilatore chiude la classe, e sul banco e' neutro (27/30
contro 29/30, e sul caso che si muoveva la differenza era UNA riga su 44:
rumore). Ma sui dati costa recall dove il banco non guarda — su `chunks`,
tedesco: `kugel` 1431 -> 1191 (-17%), `stern` 362 -> 292 (-19%), `ball`
1534 -> 1409, `band` -9%, `kerze` -4%. Le parole composte con la coda
(«Weihnachtskugel», «football») sono esattamente come si cerca nei
documenti. Costo largo, beneficio stretto: non adottato, il difetto resta
noto. Il diff e' in `/scratchpad/ancoraggio.patch`.

**IL BUCO: il banco non misura la ricerca.** Giudica la RISPOSTA — mente?
promette? nega? — e non chiede mai «ha trovato le 7 pagine che esistono».
Il 3/10 due difetti da giorni (il taglio a 300 caratteri e il `~*` senza
confini) l'hanno attraversato con 27/30; il 4/10 una modifica che costa il
19% di recall su `chunks` non ha mosso un punto. Serve un banco della
RICERCA: dieci-quindici domande con le pagine vere lette a mano una volta,
su entrambe le tabelle, e due numeri — quante trovate, quante giuste.
Nessun giudice, gira in secondi, si lancia dopo ogni modifica.

## 8-quater. Un campo vale tre prompt (4/10/2026)

Su «ho bisogno di sassi rossi» seguito da «ne hai anche di viola?» il sistema
ha risposto con un ventaglio di campioni RAL e una bobina di filo sintetico.
L'analista aveva capito (9 giri su 9: `oggetto='sasso'`, `attributi=[viola]`),
il coordinatore ha scritto `WHERE ~* '\mviolet|\mpurple'` senza l'oggetto — 1
giro su 9, la chat ha pescato quello — e il critico, seconda difesa, ha
confermato due righe su venti.

Il critico applicava il cancello sull'oggetto su 18 righe su 20 e lo scriveva:
«non un sasso: un vaso», «non un sasso: nastri». Le due che passavano dicevano
«viola disponibile» e «viola confermato»: l'oggetto non era nominato. Sono le
righe con `Colours:` lunghissimo — un vaso da terra con quaranta varianti RAL —
dove la prova sull'attributo e' schiacciante e l'attenzione non torna piu'
sulla cosa.

Tre modi di chiederlo in PROSA, tutti e tre misurati sullo stesso input
congelato (20 righe, 3 giri), tutti e tre a zero:

| | falsi positivi su 20 |
|---|---|
| prompt attuale | 3, 3, 3 |
| cancello riscritto a cascata con «e' SEVERAMENTE VIETATO guardare i colori» | 3, 3, 1 — e un falso positivo IN PIU' sui sassi rossi |
| la regola dell'elenco colori subordinata all'oggetto | 3, 3, 3 |
| il motivo che deve NOMINARE l'oggetto | 3, 3, 3 |

Poi un CAMPO: `oggetto` nello schema dello strumento `verdetti`, obbligatorio
e **prima** di `esito`. Zero falsi positivi, 3 giri su 3.

**I campi si scrivono nell'ordine in cui stanno nello schema, quindi l'ordine
dei campi e' l'ordine del ragionamento.** Dichiarare che cosa si sta guardando
prima di poter dire se va bene non e' una regola che il modello puo' saltare:
e' la forma di quello che deve consegnare. Vale la stessa cosa che valeva per
l'analista — la struttura nel passaggio, non nella prosa — e vale per i blocchi
di prompt in un altro modo: li' la posizione che conta non e' «ultima» ma
«attaccata al dato» (il cancello sull'oggetto spostato in fondo al prompt del
coordinatore: da 6 su 6 a 0 su 6).

Il campo, alla prima versione, costava: «Decorative rocks in various sizes»
dichiarato «decorazioni» e scartato, cioe' un sasso vero perso. La descrizione
chiedeva di copiare quello che la didascalia scrive dopo `object:`, e copiare
due parole non dice quale sia la cosa. Chiedendo il NOME e non il modo in cui
e' fatta — «di "decorative rocks" la cosa e' "rocks"» — il richiamo e' tornato
intero: 0 falsi positivi, 6 sassi su 6, 4 nastri su 4. Banco 29/30 in 289s.

Niente di deterministico: il codice non legge il campo nuovo. `_giudica`
prende `riga`, `esito` e `motivo` e il resto passa. Il confronto fra l'oggetto
dichiarato dal critico e quello dell'analista NON si fa in Python — sarebbe la
cura del caso di oggi e il difetto di domani intatto.

## 9. Una nota di configurazione, non di progetto

Il container in esecuzione ha `SQL_AGENTE=1`. Su disco `.env` e
`docker-compose.yml` dicono `0`. Quello che risponde in chat non e' quello che
e' scritto nella configurazione: va allineato prima di misurare qualsiasi cosa,
o si confronteranno due sistemi diversi credendo che sia lo stesso.
