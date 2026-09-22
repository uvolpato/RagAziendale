# Misure del recupero

Ogni riga è una misura fatta, con lo stato dell'indice in quel momento.
Si aggiunge in fondo, non si riscrive: il valore di questo file è il confronto.

Comando: vedi l'intestazione di `misura.py`.

---

## 21/09/2026 — riferimento di partenza

**Indice**: EUROSAND riletto con la correzione del titolo di pagina, **senza**
quella della coda OCR (commit `5bb305a`). Gli altri tre cataloghi hanno ancora
i pezzi del giro precedente. 4571 pezzi con vettore.

**Domande**: 20 con risposta, fornite dall'azienda. (Le 4 `senza_risposta` non
entrano in questa misura: si valutano sulla risposta, non sul recupero.)

| | Trovate nei primi 8 |
|---|---|
| Domanda com'è | **10/20 — 50%** |
| Domanda riscritta dal modello | **10/20 — 50%** |

La riscrittura recupera la 18 e perde la 11: **guadagno netto zero**. Il
risultato promettente del caso singolo «sassi rossi» (posizione 29 su 4571 →
posizione 4) **non si generalizza**. Senza queste venti domande la
riscrittura sarebbe stata messa in produzione come miglioria.

### Dove fallisce

| Categoria | Trovate |
|---|---|
| Specifiche Tecniche e Granulometrie | 4/4 |
| Certificazioni, Normative e Uso Esterno | 2/3 |
| Ricerca Materiali e Alternative | 3/5 |
| Assortimenti e Campionature | 1/3 |
| **Logistica, Volumi e Packaging** | **0/5** |

Mai trovate: 1, 2, 3, 4, 5, 6, 8, 16, 20.

**Non è un problema di lettura.** Verificato uno per uno: `Online Verpackung`,
`E5500`, `B02G1`, `MAK9018`, `DST1094`, `NEON` sono tutti nell'indice. Le
risposte stanno nelle pagine 97 e 105 — le tabelle di formati e prezzi in fondo
al catalogo, pagine di soli codici senza prosa. Sono gli stessi pezzi che
avevano fatto fallire «sassi rossi» (`DST2001 rot red`).

**Il difetto non è nella domanda: è nei pezzi che vengono da una tabella.**
L'embedding rappresenta bene una pagina con prosa e descrizione della figura, e
male una griglia di codici. Ogni intervento sul lato domanda — riscrittura,
sinonimi, rerank — lavora a valle di questo.

### Due falsi positivi del metro, corretti in questa misura

Il primo conteggio dava 11/20, ma due riscontri erano sbagliati miei:

- **domanda 17** risultava trovata su **FLEURAMI pagina 3**, un altro catalogo:
  il riscontro era la parola `outdoor`, troppo generica;
- **domanda 15** risultava trovata su **EUROSAND pagina 2**, che è l'indice del
  catalogo e contiene ogni nome di prodotto. `MICROPLASTIC FREE` vero sta alle
  pagine 28, 29 e 79.

Da qui i vincoli aggiunti a `trovata()`: il pezzo deve venire dal documento
giusto e non dalle pagine dell'indice. Con il metro stretto la domanda 16
(amfori) passa da «trovata» a «non trovata»: era anche quella l'indice.

---

## 21/09/2026 (sera) — cosa guadagnano ricerca esatta e pagina intera

Stesso indice della misura precedente. Strumenti nuovi in `recupero.py`:
`cerca_esatta`, `pagina`, `documenti_visibili`.

| | Trovate nei primi 8 |
|---|---|
| Oggi | 10/20 |
| Solo ricerca esatta sui nomi presi dalla domanda | **1/20** |
| Oggi + pagina intera | **11/20** |
| Unione dei due (quello che userebbe un agente) | **12/20** |

**+2 su venti.** Molto meno della previsione che avevo scritto (16-17/20).

### Perché la ricerca esatta rende così poco

Delle venti domande, **solo tre nominano qualcosa**: «Brillant / Metallic»,
«Amfori», «Marrakesch». Le altre diciassette **descrivono**: «sfere trasparenti
che trattengono l'acqua», «frammenti di vetro intorno ai 5 millimetri»,
«ciottoli neri con effetto specchio».

La premessa da cui ero partito — «chi compra cita i codici» — **non regge su
queste domande**. E c'è un problema più profondo, circolare: la ricerca esatta
ha risolto il caso «sassi rossi» *a mano* perché avevo già guardato i dati e
sapevo che esisteva la stringa `rot red`. Chi fa la domanda non lo sa. Un
agente potrebbe usarla solo **dopo** aver già trovato qualcosa da cui pescare
il termine — cioè dopo una ricerca riuscita.

Resta utile come **seconda mossa** (lo strumento diverso al secondo giro, §9),
non come prima linea.

### Cosa resta in piedi

I due interventi con la posizione misurata:

- **codici a una lettera** (`E5500`, `F0305`, `B02G1`) riconosciuti come riga
  di tabella, cosi' ricevono il titolo di pagina → domande 1, 2, 4, cioè la
  categoria che fa 0/5;
- **candidati da 30 a 150 + rerank** → domande 3, 4, 6, 20, la cui risposta sta
  già fra la posizione 26 e la 59.

Sono previsioni anche queste. Il numero vero lo dà la rimisura.

---

## 22/09/2026 — due sguardi sulla pagina + rerank

**Indice**: EUROSAND letto in DUE modi e indicizzato con entrambi —
`LETTURA=pagina` (il VLM guarda la pagina e produce Markdown strutturato) piu'
Docling (frammenti con le descrizioni delle figure). 786 pezzi contro i 361 di
prima. **Rerank** attivo: 150 candidati riordinati dal cross-encoder, poi 8 al
modello.

| | Trovate nei primi 8 |
|---|---|
| Riferimento del 21/09 (solo Docling) | 10/20 — 50% |
| Solo VLM, senza rerank | 5/20 — 25% |
| Due sguardi, senza rerank | 11/20 — 55% |
| **Due sguardi + rerank** | **17/20 — 85%** |
| Due sguardi + rerank, domanda riscritta | 15/20 — 75% |

Per categoria, tutte piene tranne tre domande:

| Categoria | Prima | Adesso |
|---|---|---|
| Logistica, Volumi e Packaging | **0/5** | **4/5** |
| Ricerca Materiali e Alternative | 3/5 | 4/5 |
| Specifiche Tecniche e Granulometrie | 4/4 | 4/4 |
| Certificazioni, Normative | 2/3 | 2/3 |
| Assortimenti e Campionature | 1/3 | **3/3** |

Mai trovate: **5** (sfere inox sfuse), **8** (neon), **16** (amfori).

### Cosa ha fatto la differenza

**Non i due sguardi da soli** (11/20), **non il rerank da solo**: insieme.
I due sguardi mettono nell'indice il materiale che mancava — le tabelle di
formati e prezzi che Docling non vedeva — ma raddoppiano i pezzi e si fanno
concorrenza per gli stessi 8 posti. Misurato prima del rerank: le risposte
delle domande 1, 5 e 6 stavano in posizione 27, 45 e 21, cioe' **dentro
l'indice e fuori dalla finestra**. Il rerank e' quello che le tira dentro.

### La riscrittura della domanda fa PEGGIO

15/20 contro 17/20: perde le domande 7 e 9. E' la seconda misura che la boccia
(il 21/09 dava guadagno zero). **Resta spenta.**

### Costo

~2 s per domanda in piu' (150 pezzi riordinati dal cross-encoder), sulla GPU
dove il modello e' gia' residente.

### Le tre che restano

- **16 (amfori)**: la dichiarazione e' un LOGO, non testo. Nessuna ricerca la
  trova perche' nell'indice non c'e'. E' un problema di lettura.
- **8 (neon)**: la risposta era in posizione 322 prima del rerank, cioe' fuori
  anche da 150 candidati.
- **5 (sfere inox sfuse)**: guardata a mano il 22/09/2026, e il `riscontro`
  e' giusto — sbaglia il recupero. I primi otto risultati vengono tutti da un
  ALTRO catalogo (INGE Holly&Jolly): «Kugel, 10 cm, Kunststoff», palline di
  plastica natalizie. La domanda dice «sfere metalliche giganti da 10 o 15
  CENTIMETRI», EUROSAND scrive «sfere di acciaio 100/150 MM», e l'altro
  catalogo dice letteralmente «10 cm». Il reranker ha preferito la
  corrispondenza letterale sbagliata, e la parola «metalliche» non e' bastata
  a scartare la plastica.

  Due cose che nessun ritocco al recupero risolve: **le unita' di misura** (cm
  contro mm) e **la concorrenza fra cataloghi** su una parola comune. Sono da
  tenere fra i casi di prova quando si toccheranno le misure o il confine fra
  fonti.

---

## 22/09/2026 — le descrizioni dentro il testo: 18/20

Reindicizzato EUROSAND con le descrizioni delle figure messe **al posto dei
segnaposto `<!-- image -->` del Markdown di Docling**, invece che come pezzi
staccati in fondo alla pagina. 770 pezzi, 891 immagini, 36 minuti.

| | prima (17/09 sera) | ora |
|---|---|---|
| trovate, domanda com'e' | 17/20 | **18/20** |
| trovate, domanda riscritta | 15/20 | 17/20 |
| in posizione #1 | — | **15/20** |

Guadagna la **5** (sfere inox), che prima non si trovava mai: ora e' al #4.
Non e' la misura in centimetri ad essere stata capita — e' che la descrizione
della figura delle sfere ora sta nello stesso pezzo del codice e del formato,
quindi il cross-encoder vede una cosa sola invece di due mezze.

### Perche' funziona

Staccata, una descrizione e' un pezzo che dice «sfere di acciaio lucido» e
nient'altro: nessun codice, nessun prezzo, nessun formato. Per entrare negli
otto posti deve battere i pezzi che quei dati ce li hanno — e perde. Messa
dove stava la figura, i due pezzi diventano uno: la prosa che risponde alla
domanda descrittiva viaggia insieme ai dati che servono a rispondere.

### Restano fuori

- **16 (amfori)**: sempre un LOGO. Non e' recupero, e' lettura. Invariata.
- **8 (neon)**: era in posizione 322, ora e' fuori dai primi 8 ma dentro i 150
  candidati. Non misurata piu' a fondo.

### La riscrittura resta spenta

17/20 contro 18/20: terza misura, terza bocciatura. Perde la 9.

### Cosa si puo' ancora togliere

`TESTO_DOCLING=no` rimette le descrizioni staccate: e' la forma di prima, e
serve a rimisurare questa stessa tabella se un altro PDF si comportasse
diversamente. Non e' un'opzione da esporre, e' il banco di prova.

---

## 22/09/2026 pomeriggio — il metro misurava il caso facile

Una chat vera: «ho bisogno di sassi rossi» → una riga con un codice solo,
mentre il metro segnava 18/20. Le domande d'oro sono **lunghe** (tre righe,
scritte da chi conosce il dominio); in chat si scrivono **tre parole**. Il
18/20 non diceva niente su quelle.

Aggiunta al file delle domande la colonna `corta`: la stessa domanda come la
scriverebbe una persona, ricavata usando **solo parole gia' presenti nella
domanda lunga**, mai dalla risposta attesa.

| | trovate |
|---|---|
| domanda lunga | 18/20 (90%) |
| **domanda corta** | **14/20 (70%)** |

Perdono la 1, la 6, la 9 e la 14. La 5 invece migliora (#4 → #1).

### Tre ipotesi, due smentite

1. **La riscrittura della domanda le recupera?** 14 → 15. Una sola, e intanto
   INVENTA: «secchio piu' grande in litri» → «Secchio da 10 litri»,
   «Marrakesch» → «sfere in resina» (sono metallo), «amfori» → «amfora in
   ceramica». Quarta bocciatura, e stavolta anche pericolosa: mette nella
   ricerca attributi che nessuno ha detto. **Resta spenta.**

2. **I pezzi sono troppo grossi e si somigliano tutti?** Misurata la banda fra
   il 1o e il 50o risultato: **0,095 con la domanda corta contro 0,078 con
   quella lunga**. E' piu' LARGA, non piu' stretta. Ipotesi sbagliata.

3. **E' la catena (fusione, rerank) a buttarle fuori?** Ablazione: vettore
   14/20, fusione 14/20, completa 14/20. Il rerank sposta (perde la 1,
   recupera la 15) ma il totale e' identico. **Non e' la catena.**

Le 6, 9 e 14 non si trovano nemmeno col solo vettore. Cosa aggiunge la
domanda lunga: il vocabolario che fa da ponte. La 6 lunga dice «effetto molto
lucido, quasi a **specchio**» e i pezzi giusti sono SPIEGELSAND, SPIEGEL
GRANULAT — *mirror sand*. «Ciottoli neri lucidi» quel ponte non ce l'ha.
Resta aperto.

## 22/09/2026 — il metro che mancava: la RISPOSTA

Fino a qui si misurava solo il RECUPERO. La chat rotta mostrava l'altra meta':
otto brani da sette pagine arrivati al modello, e una riga in uscita.

`valutazione/risposte.py`: per ogni domanda corta conta quanti dei riscontri
stanno **nel contesto** e quanti di quelli arrivano **nella risposta**. Conta
solo quelli che erano nel contesto: un dato che compare nella risposta e non
nel contesto non e' merito, e' invenzione.

| | riscontri riportati |
|---|---|
| prompt di prima | 12 su 26 — **46%** |
| **con la regola di completezza** | **19 su 26 — 73%** |

La differenza e' UNA riga aggiunta al prompt. Le altre otto regole erano
tutte divieti (non inventare, non produrre numeri, non ripetere): nessuna
diceva *quanto* riportare. La 18 passa da 1/4 a 4/4.

La regola e' scritta per non gonfiare le risposte corte: «se la risposta e'
una sola, una frase basta — non lasciare fuori niente, non allungare».

**Da tenere d'occhio**: la completezza puo' diventare prolissita', e questo
metro non la vedrebbe (conta i riscontri, non la lunghezza). La colonna
`righe` e' li' per quello.

---

## 22/09/2026 — l'ibrida non era ibrida (e l'OR non e' la cura)

L'ablazione diceva «vettore 14/20, fusione 14/20» e l'avevo letta come «la
catena non c'entra». La lettura giusta era un'altra: **meta' della catena non
e' mai partita**.

`plainto_tsquery` mette i termini in AND. Per «ciottoli neri lucidi» chiede
`'ciottol' & 'ner' & 'lucid'`, e in tutto l'indice i pezzi con tutte e tre le
parole sono **zero**. Il ramo lessicale torna vuoto, la RRF fonde il
vettoriale con niente, e i due numeri erano uguali perche' erano la stessa
ricerca. Su ogni domanda in italiano di piu' di una parola, l'ibrida e'
vettoriale.

### L'OR: provato e rimesso com'era

| | recupero (corte) | risposte |
|---|---|---|
| AND (oggi) | 14/20 | 73% |
| OR | 14/20 | 73% |

Neutro. E **peggiore dove doveva aiutare**: «quanto costa il DST2040» in OR
trova 31 pezzi e i primi cinque non contengono il codice — sono pezzi pieni di
«quanto» e «costa», piu' tre pagine di una cheat sheet sulle password.
`ts_rank_cd` non pesa la rarita': un termine raro vale come uno comune.

`DST2040` cercato da solo funziona gia' benissimo, ed e' il caso per cui il
ramo lessicale esiste. Un termine solo: AND e OR sono la stessa cosa.

**La correzione vera e' l'IDF**: pesare i termini per quanto sono rari.
Postgres non lo fa, serve una tabella delle frequenze dei lessemi aggiornata
quando si indicizza. Non e' una riga, ed e' il prossimo passo di questo ramo.

### Il metro delle risposte non era ripetibile

A temperatura 0,2 (quella di produzione) due corse sullo STESSO contesto
davano 19 riscontri e 16. Con quel rumore non si distingue un miglioramento
da una fluttuazione, e per poco non attribuivo al cambio dell'OR un calo che
era del modello. `risposte.py` ora misura a **temperatura 0**: misura una
configurazione un po' diversa da quella vera, ed e' il prezzo per avere un
numero confrontabile con quello di ieri.

---

## 22/09/2026 — l'IDF: il ramo lessicale comincia a esistere

Tabella `lessemi` (migrazione 016): per ogni parola dell'archivio, in quanti
pezzi compare. Riscritta quando si indicizza, un secondo su 3112 pezzi.
13405 lessemi. Nessun elenco di parole scritto a mano: conta l'archivio.

Il ramo lessicale ora cerca **solo i termini rari** e li ordina per rarita'
(IDF: ogni parola vale ln(totale / in quanti pezzi compare)).

### Il metro che mancava: chiedere un articolo per CODICE

Le 24 domande d'oro non contengono nessun codice, ed e' giusto — chi compra
non li sa a memoria. Ma chi li sa li scrive, ed e' l'unico caso per cui il
ramo lessicale esiste: sui codici il vettoriale non puo' funzionare, DST2040 e
DST2043 sono due prodotti diversi e due punti vicinissimi.

`valutazione/codici.py` PESCA i codici dall'indice — non li scrivo io — fra
quelli che compaiono in pochissimi pezzi, e costruisce tre forme di domanda.
Cambia da solo quando cambia l'archivio.

| forma | prima | dopo |
|---|---|---|
| `DST2040` | 10/12 | 10/12 |
| «quanto costa il DST2040» | 9/12 | **10/12** |
| «avete ancora disponibile il DST2040?» | 10/12 | **11/12** |

### La soglia di rarita' e' stata misurata, non scelta

| soglia | codici (36 prove) | risposte (26 riscontri) |
|---|---|---|
| nessuna (prima) | 29 | 19 |
| 1 pezzo su 10 | 31 | **17** |
| 1 pezzo su 100 | 31 | **17** |
| **1 pezzo su 1000** | **31** | **19** |

A 1 su 10 il ramo si sveglia anche per parole come «palline»: gli stessi
riscontri restano nel contesto, ma il modello ne riporta due di meno. A 1 su
1000 — cioe' al massimo 3 pezzi su 3112, la definizione di un codice — il
guadagno sui codici resta e la perdita sparisce.

Avevo previsto che a togliere il ripiego su «tutti i termini» le risposte
sarebbero tornate al 73%. **Sbagliato**: restavano al 65%. A spostarle e'
stata la soglia, non il ripiego.

### Cosa NON e' cambiato

Recupero 18/20 (lunghe) e 14/20 (corte), risposte 73%. L'IDF non tocca le
domande in italiano, e non doveva: li' il ramo lessicale tace.

**ATTENZIONE**: la soglia e' misurata su 3112 pezzi. Su un archivio molto piu'
grande «un pezzo su mille» sono centinaia di pezzi, e va rimisurata — non
dedotta. Per questo e' `LESSEMA_COMUNE_SU` e non un numero nella query.

### Il ramo tace, ma non quando e' solo

I test T1.15 e T1.16 hanno bocciato la prima versione, e avevano ragione:
girano SENZA vettore (modalita' degradata, host dei modelli spento) e li' il
lessicale e' l'unica ricerca. Con la regola stretta anche per loro, una fonte
attiva smetteva di rispondere del tutto.

Quindi la regola e' doppia, e la differenza e' di sostanza:

- **ricerca ibrida**: nessun termine raro → il ramo tace. Il vettoriale c'e'
  e su quelle domande fa meglio da solo.
- **solo testo**: nessun termine raro → si cercano tutti. Tacere vorrebbe dire
  non rispondere, e quella modalita' esiste per rispondere qualcosa.

Corretto anche un guasto che nessun metro avrebbe visto: `totale` leggeva
`lessemi_stato` come una riga di tabella. Su un impianto nuovo, con quella
tabella vuota, la CTE non avrebbe dato nessuna riga e **la ricerca sarebbe
tornata muta** — non imprecisa, muta. Ora e' uno scalare con un valore di
ripiego.

---

## 22/09/2026 — l'agente sulla ricerca (D17): spento, con un numero dietro

`orchestratore/ricerca_agente.py`: cerca, GUARDA cosa e' tornato, riprova con
altre parole. Al massimo tre ricerche. Sceglie le PAROLE, mai i permessi: i
gruppi restano nella WHERE.

| | trova | costo |
|---|---|---|
| ricerca sola | 14/20 | **1,1 s a domanda** |
| agente, 3 giri | 14/20 | 11,7 s a domanda |

Dieci volte il costo, zero guadagno.

### Due guasti trovati per strada, tutti e due miei

**Il primo: l'agente misurava se stesso.** Prima versione senza «/no_think» e
con 60 token di budget. Qwen3 e' un modello a ragionamento ibrido: se non gli
si dice di non pensare, pensa, e il pensiero finisce in `reasoning_content` —
`content` torna VUOTO. L'agente leggeva «niente da cercare» e si fermava al
primo giro. Venti domande misurate: 14/20 identici alla ricerca sola, due
secondi in piu' a domanda, e ZERO seconde ricerche. Il numero sembrava una
risposta e non era nemmeno una misura.

**Il secondo: senza ragionare non giudica.** Messo «/no_think» come si deve,
rispondeva — e rispondeva BASTA anche davanti a risultati palesemente
sbagliati. Non si accorgeva di aver fallito. Lasciandolo ragionare distingue:
propone «ciottoli neri brillanti» dove aveva sbagliato e dice BASTA dove
aveva trovato. Ma costa undici secondi, su OGNI domanda, anche su quelle che
andavano bene al primo colpo.

### Cosa NON dimostra

La D17 nasceva per le domande a PIU' PASSI — «confronta i prezzi di EUROSAND e
FLEURAMI» — e fra le 24 domande d'oro non ce n'e' nessuna cosi'. Qui e' stato
provato sul ponte di vocabolario, che e' un altro problema. Per il suo scopo
dichiarato resta **non misurato**, e il metro non esiste ancora.

Il modulo resta, spento (`RICERCA_AGENTE=si` per accenderlo). Si accende
quando i numeri lo diranno.
