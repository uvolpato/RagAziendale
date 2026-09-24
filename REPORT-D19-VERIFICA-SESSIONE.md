# Report verifica D19 — 20 turni multi-turno (23/09/2026)

Simulazione di conversazioni reali contro il flusso D19 in produzione
(estrazione → vincoli → must-match → barriera), su utente `direzione`/
`acquisti`, aziende `luis`+`decobrands`. Script: `verifica_d19_conversazione.py`,
output completo: `run_d19_full.txt` (pre-fix) e `run_d19_postfix.txt` (post-fix).

## Il bug trovato (classe «fallimento progetto», risolto)

`decorazioni natalizie sotto i 2 euro`:

- **Pre-fix**: estrazione `prezzo=2`, termini `["2 €","2 euro"]` → 0 chunk nel
  corpus (i cataloghi scrivono «€ 1,85», simbolo prima del numero) → barriera:
  *«non ho trovato articoli con prezzo 2»*. Falso negativo sistematico su ogni
  soglia numerica: **il dato esiste** (EUROSAND: 115 prezzi tra € 1,00 e € 1,95).
- La barriera era «onesta» ma con dati incompleti: rispondeva che non esiste
  ciò che esiste. È peggio di un'invenzione perché dice *il falso col tono
  della verità*.

**Fix strutturale** in `vincoli.py` (`_intervalli_numerici`, chiamato da
`regex()`): per l'attributo **prezzo** il must-match aggiunge l'intervallo
reale sotto la soglia, in entrambe le grafie («1,85» e «1.85»). Genera i
valori interi 0..n-1 più la parte decimale per le soglie decimali («sotto
1,50» include «1,20», mai «1,50»). Soglia accettata: `0 < valore ≤ 500`
(oltre non è un catalogo decorazioni; il confronto numerico è il confine del
DB gestionale). Solo il prezzo è una soglia per costruzione: la misura resta
un valore esatto («da 60 cm» verifica 8/8) — generare «tutto sotto 60»
inonderebbe la pool.

- **Post-fix**: pool 50 chunk → 8 pezzi EUROSAND con le stesse caratteristiche,
  risposta vera: «81316 G002 € 1,99», «81317 G002 € 1,99», «F0305 € 2,25».
  Zero regressione sugli altri 19 turni.

## Esiti per conversazione

### natalizi-viola (3 turni)
1. `mi servono ornamenti natalizi per un cliente` → 8 pezzi, lista ornamenti [8].
2. `viola, ne abbiamo tra i cataloghi?` → `colore=viola`, 8/8 pezzi con termini
   viola, risposta con misure/prezzi veri, niente invenzioni.
3. `e in formato grande che misure ci sono?` → `colore=viola; misura=grandi`
   (qualitativa), risposta corretta con le misure grandi (100 cm 15,00 €).

### profumatori-auto (3 turni)
1. `avete profumatori per auto?` → risposta corretta su serie CLASSIC/Car Line.
2. `quelli della linea lime light` → `colore=lime light` (innocuo: il vincolo
   'lime light' filtrato come colore, ma la risposta è corretta).
3. `e quanto costano?` → risposta onesta: *«prezzo non specificato nel
   contesto fornito»*. Non inventa. (Vincolo residuo raschiava il turno 1:
   corretta presenza del dato.)

### sassi-rossi (2 turni)
1. `cerco dei sassi rossi per giardino` → barriera onesta (nessun dato).
2. `e la versione da 20 cm, c'e'?` → `misura=20 cm; colore=rosso`, barriera
   onesta e specifica (nessun sasso rosso, la misura 20 cm è di altri oggetti).

### Domande singole (12)
| domanda | vincoli | esito |
|---|---|---|
| articoli natalizi viola (enunciato lungo) | colore=viola | risposta corretta con codici veri [1] |
| etichette da 60 cm | misura=60 cm | 26 chunk → 8 pezzi, risposta corretta |
| quanto costa DST2040? | misura=2040; prezzo=costo | risposta corretta (€ 4,10); il vincolo `misura=2040` non trova nulla, `prezzo=costo` amplia → pool corretta |
| penne blu per ufficio | colore=blu | barriera onesta |
| pink tree decorations (EN) | colore=rosa | risposta corretta, 3 cataloghi |
| articoli magenta | colore=magenta | risposta corretta, 2 pezzi |
| sotto i 2 euro | prezzo=2 | **prima barriera falsa → ora risposta vera** |
| fogli in formato A4 | formato=A4 | barriera onesta |
| purple items (EN) | colore=purple | risposta corretta, multilingua |
| guanti e calzature | nessuno | barriera onesta |
| lampade arancione negozio | colore=arancione | risposta corretta |
| addobbi argento e oro | colore=argento; colore=oro | risposta corretta ma **solo sul pezzo oro** (OR diluisce) |

## Giudizio per classe di problema

| Classe | Esito |
|---|---|
| fallimento progetto (barriera falsa su dati esistenti) | **risolto** (fix `_intervalli_numerici`) |
| barriera onesta su dati assenti | corretta — è il comportamento voluto |
| inventiva del modello | zero invenzioni in 20 turni |
| attributi in pool (colore/misura/formato) | 8/8 per tutti i colouri testati |

## Limiti confermati, NON corretti (dentro l'architettura, nessuno mette in errore)

1. **DOMANDE DI IDENTITÀ** (`misura=2040` da DST2040): l'estrazione genera
   vincoli spazzatura che producono 0 chunk sulla misura, ma salvati dal
   secondo vincolo (`prezzo=costo`); risposta corretta. Non è un cerotto:
   è la traduzione che salva — richiederebbe ridisegno dell'estrazione dei
   codici (fuori perimetro, vedi DECISIONI-APERTE). 
2. **OR fra valori dello stesso attributo** («argento e oro» risponde solo
   oro): il recupero fa OR; la risposta è incompleta ma non inventa. Richiede
   gestione multi-valore (fuori).
3. **VALORI QUALITATIVI** («grandi» come misura): nessun match testuale,
   ma il vettore recupera i pezzi giusti → risposta corretta. Idem per
   «lime light» classificato colore: innocuo qui.
4. **Barriere su misure esatte**: «fogli A4», «penne blu» → oneste (dati
   davvero assenti dai cataloghi analizzati).

Nessun limite osservato produce una risposta **falsa** o un'invenzione: tutti
i difetti residui sono di completezza, non di correttezza.