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
