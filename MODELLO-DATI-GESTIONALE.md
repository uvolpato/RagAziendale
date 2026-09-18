# Modello dati dei gestionali

**Stato:** approvato e applicato — 18/09/2026 (tabelle vuote: il connettore non esiste ancora)
**SQL:** `Sviluppo/migrations/002_erp.sql`
**Decisioni:** 46–54 del registro in `PROGETTO-RAG-Aziendale.md`, §15 e §15.9

Questo documento descrive come l'assistente conserva i dati che arrivano dai
gestionali delle aziende: cosa si salva, come si tiene lo storico, come si
separano le aziende, e cosa resta fuori di proposito.

---

## 1. In breve

```
 Gestionale A (Integra)   Gestionale B   ...
        │                      │
   connettore A           connettore B          ← l'unica parte che conosce il gestionale
        │                      │
        └──────────┬───────────┘
                   ▼
   erp_storico.*   tabelle vere, con TUTTE le versioni di ogni record
                   ▼
   erp.*           viste: solo la versione attuale  ← quello che legge l'assistente
                   ▼
   Cube            metriche (fatturato, margine...) sopra corrente + storico

   arricchimenti.*  quello che aggiunge l'AI, con provenienza e revisione umana
```

| Schema | Contiene | Chi scrive | Chi legge |
|---|---|---|---|
| `erp_storico` | tutte le versioni di ogni record | solo la sincronizzazione | Cube, strumenti di analisi |
| `erp` | viste sulla versione corrente + contatti, giacenze fotografate, stato sync | sincronizzazione | assistente, Cube |
| `arricchimenti` | fatti dedotti dall'AI, collegamenti documento↔entità | job di arricchimento, revisori | assistente, Cube |
| `public` | `aziende`, `sources`, `chunks` e il resto della fase 1 | amministratori, ingestion | gate, assistente |

---

## 2. Principi

### 2.1 Indipendente dal gestionale

Integra è uno dei gestionali, non *il* gestionale. Il modello ha colonne sue,
con nomi di business (`soggetto`, `articolo`, `documento`), e ogni gestionale ha
un **connettore**: un insieme di viste SQL che traducono le sue tabelle in
queste colonne. È lo schema già collaudato nel portale B2B Luis (contratto
`b2b_*`): cambiando gestionale si riscrive il connettore, non il resto.

### 2.2 Accoglie il massimo, pretende il minimo

Non tutte le aziende tengono costi, lotti, scadenze, distinte base. Quindi:

- **quasi tutte le colonne sono facoltative**; obbligatorie solo quelle senza
  le quali la riga non ha senso (id, azienda, e poco altro);
- ogni tabella ha **`extra` (jsonb)**: quello che il gestionale ha e il modello
  non prevede finisce lì. All'import non si perde niente;
- se un campo in `extra` diventa importante (ci si fanno report), si **promuove
  a colonna** con una migrazione.

### 2.3 Tutto lo storico

Ogni modifica a un record del gestionale produce una **nuova versione**; la
precedente resta, chiusa. Da qui: andamento dei prezzi fornitore, margine di un
prodotto nel tempo, fatturato attribuito all'agente che seguiva il cliente
*allora*, articoli spostati di famiglia senza riscrivere le statistiche passate.

### 2.4 Più aziende

Un gestionale può contenere più aziende (Integra: `azi_cdazi`), un gruppo può
averle su gestionali diversi. Ogni riga appartiene a **un'azienda**, e l'azienda
è un **asse di permesso**: un utente vede solo le aziende a cui è abilitato.

### 2.5 L'assistente vede solo il presente

Le tabelle storiche contengono più versioni dello stesso cliente. Un modello
linguistico che scrive una query e dimentica il filtro sulla versione conta
tutto due o tre volte, con aria sicura. Per questo l'assistente legge le viste
`erp.*` (una riga per record, la versione attuale). Le domande sul passato
passano da Cube e da strumenti dedicati, che il filtro lo applicano sempre.

---

## 3. Chiavi

Ogni record ha un **`id` testuale** nel formato:

```
<codice della nostra azienda>:<id nel gestionale>
luis:20657
```

(Rivisto con la decisione 69, migrazione 005. Prima era
`<fonte>:<codice azienda nel gestionale>:<id>`.)

- **Lo spazio dei nomi è la NOSTRA azienda**, non il gestionale: ogni azienda
  ha il suo connettore e importa nel suo spazio. Due aziende sullo stesso tipo
  di gestionale, anche con lo stesso codice interno (`001` capita spesso), non
  si pestano mai i piedi: `luis:20657` e `decobrands:20657` sono record diversi.
- **Leggibile**: in un log o in una traccia si capisce subito da dove viene.
- **I riferimenti** fra tabelle (`soggetto_id`, `articolo_id`,
  `documento_id`...) usano lo stesso formato.
- **Niente chiavi esterne (FK)** fra le tabelle gestionali: si ricostruiscono in
  qualsiasi ordine e una sincronizzazione parziale non blocca le altre (stessa
  scelta del B2B). La coerenza la verifica il job di sincronizzazione.

Per le entità senza un id numerico nel gestionale (es. tabelle codici) il
connettore compone l'id dai campi della chiave naturale:
`luis:pagamento:007`.

> ponytail: chiave testuale. Se un giorno i join su decine di milioni di righe
> diventano il collo di bottiglia, si aggiunge una chiave surrogata intera.
> Non prima.

---

## 4. Lo storico (SCD tipo 2)

### 4.1 Colonne comuni a tutte le tabelle di `erp_storico`

| Colonna | Tipo | Significato |
|---|---|---|
| `versione` | bigint, PK | identificativo della versione (non del record) |
| `id` | text | identificativo del record (§3), uguale in tutte le sue versioni |
| `azienda` | text | `aziende.codice` |
| `extra` | jsonb | campi del gestionale che il modello non prevede |
| `hash_riga` | text | impronta dei campi significativi: se non cambia, non nasce una versione |
| `data_modifica_origine` | timestamptz | data di modifica dichiarata dal gestionale, se c'è |
| `registrato_dal` | timestamptz | da quando questa versione è quella vera |
| `registrato_al` | timestamptz | fino a quando; **NULL = versione corrente** |
| `cancellato` | boolean | il record è sparito dal gestionale |

Sono definite **una sola volta** (tabella modello `_versione`) e copiate in ogni
tabella con `LIKE ... INCLUDING ALL`: nessuna colonna dimenticata.

### 4.2 Due assi di tempo, da non confondere

| | Esempio | Da dove viene |
|---|---|---|
| `registrato_dal` / `registrato_al` | "il 3 maggio il listino in Integra è stato modificato" | sincronizzazione (tempo di sistema) |
| `in_vigore_dal` / `in_vigore_al` | "il nuovo prezzo vale dal 1° giugno" | gestionale (tempo di business) |

Un listino inserito il 3 maggio con validità dal 1° giugno ha
`registrato_dal = 3/5` e `in_vigore_dal = 1/6`. Entrambi servono: il primo dice
cosa sapevamo e quando, il secondo cosa valeva.

### 4.3 Garanzie date dal database

Non sono convenzioni del codice: sono vincoli, e il database rifiuta i dati che
li violano.

| Garanzia | Come |
|---|---|
| Per lo stesso record, **i periodi non si sovrappongono** → mai due versioni correnti, mai doppi conteggi | vincolo di esclusione `EXCLUDE USING gist (id WITH =, tstzrange(registrato_dal, registrato_al) WITH &&)` |
| Al massimo **una versione corrente** per record | indice unico su `id` dove `registrato_al IS NULL` |
| Una versione non può chiudersi prima di aprirsi | `CHECK (registrato_al > registrato_dal)` |

Verificato il 18/09/2026: una versione sovrapposta viene rifiutata con
`violates exclusion constraint "articoli_periodi_disgiunti"`.

### 4.4 Algoritmo di sincronizzazione (per ogni record letto dal connettore)

```
calcola hash_riga sui campi significativi
cerca la versione corrente con lo stesso id
  non esiste              → INSERT nuova versione
  esiste, hash uguale     → niente (il 95% dei casi)
  esiste, hash diverso    → UPDATE corrente SET registrato_al = adesso
                            INSERT nuova versione con registrato_dal = adesso
a fine lettura COMPLETA:
  versioni correnti il cui id non è arrivato → chiusa + nuova versione cancellato = true
```

- `registrato_dal` è l'**ora della sincronizzazione**, non la data di modifica
  del gestionale: quella non sempre esiste ed è a volte inaffidabile. La data
  del gestionale resta in `data_modifica_origine`.
- Le variazioni **fra una sincronizzazione e l'altra** si perdono (se un prezzo
  cambia due volte in 15 minuti, resta l'ultimo). Per prezzi, anagrafiche e
  condizioni è irrilevante.
- Le **cancellazioni** si vedono solo con una lettura completa. Per le tabelle
  grandi: lettura incrementale frequente (per data di modifica) + lettura
  completa notturna.
- Tutto in una **transazione per entità**: se fallisce, la versione corrente
  resta quella di prima (stessa garanzia del "full-swap" del B2B).

### 4.5 Viste correnti

Per ogni tabella `erp_storico.X` esiste la vista:

```sql
CREATE VIEW erp.X AS
SELECT * FROM erp_storico.X
WHERE registrato_al IS NULL AND NOT cancellato;
```

Sono create in automatico dalla migrazione, per tutte le tabelle storiche.

---

## 5. Tabelle

Legenda della colonna **Integra**: il campo del contratto `b2b_*` (o della
tabella Integra) da cui il connettore lo ricava; `—` = non disponibile oggi nel
contratto B2B, da mappare.

### 5.1 `aziende` — le aziende gestite

Non storicizzata: poche righe. Si amministra dal pannello, **Impostazioni →
Aziende**, solo il Superutente (decisione 69). **Ogni azienda ha il suo
connettore** al gestionale di riferimento e importa tutto nel proprio spazio di
chiavi (§3).

| Colonna | Tipo | Obbl. | Significato | Integra |
|---|---|---|---|---|
| `codice` | text PK | ✔ | slug stabile (minuscole, cifre, trattini): è nelle chiavi dei record e nel gruppo Keycloak `azienda-<codice>`. **Non si cambia** | — |
| `ragione_sociale` | text | ✔ | | — |
| `partita_iva` | text | | | — |
| `connettore` | text | | tipo di connettore: `integra`, ...; vuoto = non ancora collegata | `integra` |
| `codice_origine` | text | | codice dell'azienda DENTRO il gestionale, se è multi-azienda | `azi_cdazi` (`001`) |
| `attiva` | boolean | ✔ | disattivata = importazioni ferme, dati conservati | — |
| `note`, `creata_il` | | | | — |

**Due posti da tenere allineati**: la riga qui e il gruppo Keycloak
`azienda-<codice>`, che abilita le persone. Il pannello li crea insieme e, se
uno fallisce, annulla l'altro. Le aziende **non si eliminano** (dati importati,
fonti e registro vi fanno riferimento): si disattivano con motivo.

**Le credenziali del collegamento** al gestionale stanno in `.env`, mai qui né
nel pannello.

### 5.2 `soggetti` — anagrafica unica

Clienti, fornitori, agenti, vettori, prospect. Molti gestionali (Integra
compreso) hanno un'anagrafica unica: un fornitore che è anche cliente è **un**
soggetto con **due** ruoli (§5.3).

| Colonna | Tipo | Significato | Integra (`b2b_clienti`) |
|---|---|---|---|
| `codice` | text | codice anagrafico | `codice_cliente` |
| `tipo_soggetto` | text | `societa` \| `persona_fisica` \| `ente` | da `forma_giuridica` |
| `ragione_sociale`, `ragione_sociale_2` | text | | idem |
| `cognome`, `nome` | text | solo ditte individuali | idem |
| `forma_giuridica` | text | | idem |
| `partita_iva`, `codice_fiscale` | text | | idem |
| `codice_sdi`, `pec` | text | fatturazione elettronica | —, `pec` |
| `email`, `telefono`, `web` | text | recapiti **aziendali generici** | idem |
| `indirizzo`, `cap`, `citta`, `provincia`, `regione`, `nazione` | text | sede legale | idem (`stato` → `nazione`) |
| `settore` | text | ATECO o classificazione interna | — |
| `classificazioni` | jsonb | `{"zona":"NE","categoria":"GDO"}`, codici in `codici` | `codice_zona`, ... |
| `note` | text | | — |

Indici: partita IVA; ragione sociale a **trigrammi** (trova "rossi arred" anche
se scritto male).

### 5.3 `soggetti_ruoli` — ruolo e condizioni commerciali

Storicizzate apposta: se il cliente cambia agente o listino, **il passato resta
attribuito alle condizioni di allora**.

| Colonna | Tipo | Significato | Integra |
|---|---|---|---|
| `soggetto_id` | text ✔ | | `id_cliente` |
| `ruolo` | text ✔ | `cliente` \| `fornitore` \| `agente` \| `vettore` \| `prospect` | tipo anagrafica (`C`/`F`) |
| `codice_ruolo` | text | codice cliente/fornitore nel gestionale | `codice_cliente` |
| `agente_id` | text | | `codice_agente` |
| `listino` | text | | `codice_listino` (`--`/null → default) |
| `pagamento`, `porto`, `spedizione`, `valuta`, `codice_iva`, `zona`, `categoria` | text | codici in `codici` | idem |
| `vettore_id` | text | | `codice_vettore` |
| `sconti` | numeric[] | sconti di testata in cascata, quanti che siano | — |
| `fido`, `fido_scadenze` | numeric | **per l'analisi** (vedi §7) | `fido_totale`, `fido_scadenze` |
| `bloccato`, `motivo_blocco` | | | — |
| `provvigione_pct` | numeric | per il ruolo agente | — |
| `attivo_dal`, `attivo_al` | date | | — |

`sconti` è un array invece di `sconto_1..4`: alcuni gestionali ne hanno due,
altri sei.

### 5.4 `indirizzi`

Sede, destinazioni di consegna, indirizzi di fatturazione, magazzini.

| Colonna | Significato | Integra (`b2b_indirizzi_clienti`) |
|---|---|---|
| `soggetto_id` ✔ | | `id_cliente` |
| `tipo` | `sede` \| `consegna` \| `fatturazione` \| `magazzino` | `codice_tipo_destinazione`, `flag_spedizione` |
| `ragione_sociale`, `indirizzo`, `indirizzo_2`, `cap`, `citta`, `provincia`, `regione`, `nazione` | | idem |
| `latitudine`, `longitudine` | per giri consegna e mappe | — |
| `abituale` | destinazione predefinita | `flag_abituale` |
| `zona`, `agente_id` | | `codice_zona`, `codice_agente` |
| `vettore_id`, `porto` | **se valorizzati sovrascrivono** quelli del soggetto | `codice_vettore`, `codice_porto` |
| `giorni_preparazione`, `orari_consegna` | | `giorni_preparazione`, — |

### 5.5 `articoli`

| Colonna | Significato | Integra (`b2b_prodotti`) |
|---|---|---|
| `codice` ✔ | | `pro_cod` |
| `descrizione`, `descrizione_estesa` | | `pro_descr`, — |
| `tipo_articolo` | `merce` \| `servizio` \| `semilavorato` \| `materia_prima` \| `kit` | `cod_clv_tipoarticolo` |
| `unita_misura`, `unita_misura_acquisto`, `fattore_conversione` | | — |
| `classificazioni` | jsonb: `{"famiglia":"VAS","linea":"L1","gruppo_merc":"04","gruppo_stat":"12"}` | `cod_famiglia`, `cod_linea`, `cod_gruppo_merceologico`, `cod_gruppo_statistico` |
| `attributi` | jsonb: caratteristiche tecniche specifiche del settore `{"diametro_cm":30}` | `cod_diametro_esterno`, `cod_altezza` |
| `marca`, `codice_iva` | | — |
| `peso_netto`, `peso_lordo`, `volume`, `lunghezza`, `larghezza`, `altezza` | | — |
| `pezzi_per_confezione`, `pezzi_per_pallet` | | — |
| `gestione_lotti`, `gestione_matricole`, `gestito_a_magazzino` | | — |
| `scorta_minima`, `punto_riordino` | | — |
| `fornitore_preferenziale_id` | | da `prosoggetti` |
| `costo_standard`, `costo_ultimo`, `costo_medio` | **storicizzati**: danno il margine nel tempo | — (listino `VENCU` = costo ultimo) |
| `ubicazione` | | `ubicazione` |
| `pubblicato_web` | | `incluso_b2b` |
| `obsoleto` | | `prodotto_obsoleto` |

**Perché `classificazioni` e `attributi` sono jsonb:** ogni azienda classifica
gli articoli a modo suo (famiglia/linea per Luis, reparto/categoria per un
altro) e ha caratteristiche tecniche proprie (diametro per i vasi, voltaggio per
le lampade). Colonne fisse andrebbero bene per un'azienda sola. Le descrizioni
dei codici di classificazione stanno in `codici` (tipo `famiglia`, `linea`...).
Se Cube deve raggruppare spesso su una classificazione, la si promuove a colonna.

Indici: codice; descrizione a trigrammi.

### 5.6 `articoli_codici` — codici alternativi

Servono alla domanda "cos'è l'articolo XYZ-123?" quando XYZ-123 è il codice
del fornitore, o l'EAN letto da un'etichetta.

| Colonna | Significato | Integra |
|---|---|---|
| `articolo_id` ✔ | | `pro_id` |
| `tipo` ✔ | `ean` \| `fornitore` \| `cliente` \| `alternativo` \| `produttore` | |
| `codice` ✔ | | `codice_alternativo`, `codice_esterno`, `psg_cod1..3` |
| `soggetto_id` | il fornitore/cliente a cui appartiene il codice | `psg_clacod` |

### 5.7 `articoli_fornitori` — condizioni di acquisto

| Colonna | Significato | Integra (`prosoggetti`, tipo `F`) |
|---|---|---|
| `articolo_id`, `fornitore_id` ✔ | | `psg_proid`, `psg_clacod` |
| `codice_fornitore`, `descrizione_fornitore` | | `psg_cod1`, `psg_descr` |
| `prezzo_acquisto`, `valuta`, `sconti` | | — / `psg_tprcod` |
| `quantita_minima` | | `psg_minord` |
| `multiplo` | | `psg_liberon1` ⚠ campo libero, da confermare |
| `lotto_riordino` | | `psg_lottoriord` |
| `giorni_consegna` | | `psg_ggcons` |
| `preferenziale` | | — |

### 5.8 `articoli_componenti` — distinta base

| Colonna | Significato |
|---|---|
| `articolo_id`, `componente_id` ✔ | padre, figlio |
| `quantita`, `unita_misura`, `scarto_pct`, `ordinamento` | |

### 5.9 `listini` e `listini_righe`

**Listini BASE**, di vendita e di acquisto. **Non** sono il prezzo che paga il
cliente: quello lo calcola il motore prezzi del gestionale (scaglioni, prezzi
riservati, sconti in cascata, promozioni), che non si replica e non si
ricostruisce (decisione 48).

`listini`:

| Colonna | Significato | Integra (`b2b_listini_testata`) |
|---|---|---|
| `codice` ✔ | | `codice_listino` |
| `descrizione` | | `descrizione_listino` |
| `ciclo` | `vendita` \| `acquisto` | — |
| `con_iva`, `valuta` | | `listino_con_iva`, `codice_valuta` |
| `in_vigore_dal`, `in_vigore_al` | | — |
| `obsoleto` | | `listino_obsoleto` |

`listini_righe`:

| Colonna | Significato | Integra (`b2b_listini_righe`) |
|---|---|---|
| `listino_id`, `articolo_id` ✔ | | `codice_listino`, `id_prodotto` |
| `soggetto_id` | valorizzato = prezzo riservato a quel soggetto | `id_cliente` se `tipo_cliente = 'C'` |
| `prezzo`, `sconti` | | `prezzo_listino`, `sconto_1..4` |
| `quantita_da`, `quantita_a` | scaglioni | idem |
| `in_vigore_dal`, `in_vigore_al` | | `data_inizio_validita`, `data_fine_validita` |

### 5.10 `documenti` — testate

Preventivi, ordini, DDT, fatture, note di credito, resi — **di vendita e di
acquisto**. I documenti di acquisto danno subito lo storico dei prezzi
fornitore, anche degli anni passati.

| Colonna | Significato | Integra (`b2b_ordini_clienti`) |
|---|---|---|
| `ciclo` ✔ | `vendita` \| `acquisto` | |
| `tipo` ✔ | `preventivo` \| `ordine` \| `ddt` \| `fattura` \| `nota_credito` \| `reso` \| `altro` | da `mvt_natmov` |
| `tipo_origine` | il codice del gestionale, per non perdere sfumature | `mvt_natmov` (`ORD`) |
| `numero`, `serie`, `anno` | | `numero_ordine`, `serie`, `anno_ordine` |
| `data_documento`, `data_registrazione` | | `data_ordine`, `data_registrazione` |
| `data_consegna_prevista`, `data_consegna` | | `data_trasporto`, — |
| `soggetto_id` | | `id_cliente` |
| `indirizzo_consegna_id`, `indirizzo_fatturazione_id` | | `id_destinazione_merce`, `id_destinazione_fattura` |
| `agente_id`, `listino`, `pagamento`, `porto`, `spedizione`, `vettore_id` | | `codice_*` |
| `valuta`, `cambio` | | `codice_valuta`, — |
| `sconti` | sconti di testata | `sconto_1..4`, `sconto_finale` |
| `imponibile`, `iva`, `totale` | | `importo_imponibile`, `importo_iva`, — |
| `spese_trasporto`, `spese_incasso`, `peso_totale`, `colli` | | — |
| `riferimento_soggetto`, `data_riferimento_soggetto` | il "vs. ordine n." | `riferimento_ordine_cliente`, `data_riferimento_ordine` |
| `stato`, `stato_origine` | normalizzato + originale | `stato_saldo`, `flag_fatturato` |
| `canale` | `b2b` \| `agente` \| `telefono` \| `ecommerce` | `mvt_liberoc5 = 'B2B'` |
| `utente_origine`, `note` | | `utente_inserimento`, `note_ordine` |

⚠ Il contratto B2B oggi espone **solo gli ordini di vendita**. DDT, fatture e
documenti di acquisto vanno aggiunti al connettore Integra: sono loro a dare il
fatturato.

### 5.11 `documenti_righe`

| Colonna | Significato | Integra (`b2b_righe_ordini`) |
|---|---|---|
| `documento_id` ✔ | | `id_ordine` |
| `numero_riga` | | `ordine_riga` |
| `tipo_riga` | `articolo` \| `descrittiva` \| `spesa` \| `omaggio` \| `sconto` | — |
| `articolo_id` | | `id_prodotto` |
| `codice_articolo`, `descrizione` | **come scritti sul documento**, anche se l'articolo sparisce | `codice_prodotto`, `descrizione_riga` |
| `quantita`, `unita_misura` | | idem |
| `prezzo_listino`, `sconti`, `prezzo_netto`, `importo` | prezzi **applicati allora** | idem, `sconto_1..4` |
| `codice_iva` | | — |
| `costo_unitario` | se il gestionale lo registra: **margine esatto e retroattivo** | — da verificare |
| `provvigione_pct` | | — |
| `magazzino`, `lotto` | | — |
| `data_consegna_prevista` | | — |
| `quantita_evasa`, `quantita_fatturata` | | —, `quantita_fatturata` |
| `riga_origine_id` | riga d'ordine da cui nasce la riga di DDT/fattura | — |
| `note` | | `note_riga` |

### 5.12 `documenti_collegamenti`

Ordine → DDT → fattura. Colonne: `documento_id`, `documento_origine_id`.

### 5.13 `scadenze`

"Chi è in ritardo coi pagamenti", "cosa dobbiamo pagare questa settimana".

| Colonna | Significato |
|---|---|
| `soggetto_id` ✔ | |
| `documento_id` | fattura di riferimento |
| `ciclo` | `attiva` (da incassare) \| `passiva` (da pagare) |
| `data_scadenza`, `importo` | |
| `importo_pagato`, `data_pagamento` | |
| `pagamento`, `insoluto` | |

### 5.14 `movimenti_magazzino`

| Colonna | Significato |
|---|---|
| `data_movimento`, `articolo_id` ✔, `magazzino`, `causale` | |
| `segno` | +1 carico, −1 scarico |
| `quantita`, `valore`, `lotto` | |
| `documento_id`, `soggetto_id` | origine del movimento |

### 5.15 `codici` — tabelle codici del gestionale

Pagamenti, porti, spedizioni, vettori, zone, famiglie, linee, causali,
magazzini: tutte **codice → descrizione**, quindi una tabella sola.

| Colonna | Significato | Integra |
|---|---|---|
| `tipo` ✔ | `pagamento`, `porto`, `famiglia`, ... | `b2b_tabpag`, `b2b_tabpor`, `b2b_tabspe`, `b2b_vettori`, `classivoci` |
| `codice` ✔, `descrizione` | | `*_cod`, `*_descr` |
| `padre` | gerarchie (famiglia → linea) | — |
| `dettagli` | jsonb con gli attributi specifici: `{"tipo_scadenza":2,"sconto_cassa":0}` | `pag_tiposcad`, `pag_scontocassa`... |

---

## 6. Eccezioni allo storico

### 6.1 `erp.contatti` — dati personali, niente storico

Referenti dei clienti e dei fornitori (nome, ruolo, email, telefono,
cellulare). **Si sovrascrivono**: il GDPR chiede minimizzazione e cancellazione,
e uno storico dei dati personali va contro entrambe. Cancellato il referente nel
gestionale, sparisce anche qui.

### 6.2 `erp.giacenze_istantanee` — fotografie periodiche

Una riga per `(data, azienda, articolo, magazzino)` con esistenza, impegnato,
ordinato, disponibile, valore. Tipicamente una fotografia al giorno: da qui
rotazione e andamento delle scorte. È già storica per natura, non serve SCD.

### 6.3 `erp.sincronizzazioni` — stato della sincronizzazione

Una riga per `(azienda, entità)`: cursore, inizio, fine, esito, righe lette,
versioni nuove, errore. L'assistente la usa per dire **"dati aggiornati al
..."**: una risposta su dati replicati senza la data di aggiornamento è una
risposta a metà.

---

## 7. Cosa NON si legge da qui

Decisione 47, invariata. Tre dati cambiano di continuo e una risposta vecchia di
un quarto d'ora fa danni:

| Dato | Dove lo prendiamo per rispondere | Cosa teniamo qui |
|---|---|---|
| Giacenza attuale | **in diretta** dal gestionale | fotografie giornaliere, per l'analisi |
| Fido residuo, blocchi | **in diretta** | storico del fido assegnato, per l'analisi |
| Prezzo netto per il cliente | **in diretta** (motore prezzi del gestionale) | listini base e prezzi applicati nei documenti |

Esclusi per **minimizzazione** (nessuna domanda dell'assistente ne ha bisogno):
IBAN, ABI, CAB, BIC, mandati SDD.

---

## 8. Più aziende e permessi

L'azienda è il **terzo asse di permesso**, accanto ai due della fase 1:

| Asse | Domanda | Dove sta |
|---|---|---|
| Gruppi | *chi* può vedere | `sources.acl_groups` ∩ gruppi del token |
| Residenza | *dove* può andare (interno / cloud) | `sources.residency` |
| **Azienda** | *di quale azienda* | `azienda` su ogni riga ∩ aziende del token |

**Regole:**

1. Ogni riga dei dati gestionali ha `azienda` (obbligatoria).
2. Ogni sorgente documentale dichiara le sue aziende (`sources.aziende`),
   **senza default permissivo**. Un documento comune a tutto il gruppo le elenca
   tutte.
3. Ogni utente riceve dal token le aziende a cui è abilitato: gruppi Keycloak
   dedicati (es. `azienda-luis`, `azienda-decobrands`). **Nessuna azienda nel
   token = nessun dato**, come per i gruppi.
4. Il gate filtra per **intersezione**, esattamente come per i gruppi.
5. Tracce, contaminazione (taint) e arricchimenti portano l'azienda, per
   l'audit.

⚠ **Da fare insieme alla migrazione, non dopo:** colonna `sources.aziende`,
estrazione delle aziende dal token in `identita.py`, filtro in `recupero.py`,
test nel gate. Per questo la migrazione 002 **non** contiene ancora la colonna
`sources.aziende` né le righe del registro sorgenti per le tabelle ERP: una
colonna di permesso che il codice non applica dà solo una falsa sicurezza.

**Registro sorgenti per le tabelle ERP** (decisione 50) — proposta da
approvare da chi risponde dei dati:

| Sorgente | Tabelle | Gruppi proposti |
|---|---|---|
| `erp-articoli` | articoli, codici articolo, codici | tutti |
| `erp-listini` | listini, listini_righe | vendite, amministrazione, direzione |
| `erp-soggetti` | soggetti, ruoli, indirizzi, contatti | vendite, amministrazione, direzione |
| `erp-documenti-vendita` | documenti/righe ciclo vendita | vendite, amministrazione, direzione, magazzino |
| `erp-documenti-acquisto` | documenti/righe ciclo acquisto, articoli_fornitori | acquisti *(gruppo da creare in Keycloak)*, amministrazione, direzione |
| `erp-scadenze` | scadenze | amministrazione, direzione |
| `erp-magazzino` | movimenti, giacenze_istantanee | magazzino, direzione |

Oltre al livello tabella serviranno **filtri di riga** (RLS) — es. l'agente vede
solo i suoi clienti — quando esisteranno gli strumenti che interrogano queste
tabelle.

---

## 9. Arricchimenti

Tutto quello che non viene dal gestionale. **Non sovrascrive mai** una colonna
gestionale.

### 9.1 `arricchimenti.fatti`

Un fatto su un'entità, con la sua provenienza. Esempi: settore del cliente
dedotto dal suo sito; materiale dell'articolo estratto dalla scheda tecnica.

| Colonna | Significato |
|---|---|
| `azienda`, `entita`, `chiave` | a cosa si riferisce (`soggetto`, `luis:4512`) |
| `attributo`, `valore` | `settore`, `"arredo giardino"` |
| `fonte` | `chunk:123` \| URL \| `regola:<nome>` — **obbligatoria** |
| `metodo` | modello o regola che l'ha prodotto — **obbligatorio** |
| `fiducia` | 0–1 |
| `stato` | `proposto` → `confermato` \| `scartato` |
| `rivisto_da`, `rivisto_il` | chi ha confermato |

**Vincoli:**
- `confermato` **senza** `rivisto_da` e `rivisto_il` è rifiutato dal database:
  la revisione umana è un vincolo, non un'abitudine.
- un solo fatto vivo per `(entità, chiave, attributo, fonte)`: rilanciare il job
  non duplica.

### 9.2 `arricchimenti.collegamenti`

"Il manuale cita l'articolo LU3210". Colonne: `chunk_id` (FK a `chunks`, con
cancellazione a cascata: reindicizzato il documento, i collegamenti vecchi
spariscono), `entita`, `chiave`, `metodo`, `fiducia`.

### 9.3 Descrizioni generate

Non hanno una tabella: vanno in `chunks`, con una sorgente dedicata (es.
`erp-articoli-schede`) e `documento = 'articolo:<id>'`. Così riusano ricerca
ibrida, permessi e citazioni che esistono già.

---

## 10. Esempi di interrogazione

**Il presente** (quello che fa l'assistente):

```sql
SELECT codice, descrizione, costo_ultimo
FROM erp.articoli
WHERE codice = 'LU3210';
```

**Com'era a una data:**

```sql
SELECT costo_ultimo
FROM erp_storico.articoli
WHERE id = 'luis:20657'
  AND tstzrange(registrato_dal, registrato_al) @> '2026-03-01'::timestamptz;
```

**Andamento del prezzo d'acquisto da un fornitore** (dai documenti, retroattivo):

```sql
SELECT date_trunc('month', d.data_documento) AS mese,
       avg(r.prezzo_netto)                   AS prezzo_medio
FROM erp.documenti d
JOIN erp.documenti_righe r ON r.documento_id = d.id
WHERE d.ciclo = 'acquisto' AND d.tipo = 'fattura'
  AND d.soggetto_id = 'luis:2'
  AND r.articolo_id = 'luis:20657'
GROUP BY 1 ORDER BY 1;
```

**Margine nel tempo di un prodotto** (costo sulla riga se c'è, altrimenti il
costo articolo in vigore alla data del documento):

```sql
SELECT date_trunc('month', d.data_documento) AS mese,
       sum(r.importo) AS ricavo,
       sum(r.quantita * coalesce(r.costo_unitario, a.costo_medio)) AS costo
FROM erp.documenti d
JOIN erp.documenti_righe r ON r.documento_id = d.id
LEFT JOIN erp_storico.articoli a
       ON a.id = r.articolo_id
      AND tstzrange(a.registrato_dal, a.registrato_al) @> d.data_documento::timestamptz
WHERE d.ciclo = 'vendita' AND d.tipo = 'fattura'
  AND r.articolo_id = 'luis:20657'
GROUP BY 1 ORDER BY 1;
```

**Fatturato per l'agente che seguiva il cliente allora** (non quello di oggi):

```sql
SELECT ru.agente_id, sum(d.imponibile)
FROM erp.documenti d
JOIN erp_storico.soggetti_ruoli ru
  ON ru.soggetto_id = d.soggetto_id AND ru.ruolo = 'cliente'
 AND tstzrange(ru.registrato_dal, ru.registrato_al) @> d.data_documento::timestamptz
WHERE d.ciclo = 'vendita' AND d.tipo = 'fattura'
GROUP BY 1;
```

Queste query le scrive Cube o uno strumento dedicato, **non** il modello
linguistico al volo (§2.5).

---

## 11. Connettore: cosa deve fornire

Per ogni gestionale, una cartella `connettori/<gestionale>/` con:

1. **Accesso in sola lettura**: utente dedicato sul database del gestionale
   (mai l'amministratore), limitato alle tabelle necessarie; o API; o
   esportazioni. Credenziali solo in `.env`.
2. **Una vista per entità** del modello, con le colonne di §5 e in più:
   - `id` nel formato `<fonte>:<azienda>:<id>` (§3);
   - `codice azienda nel gestionale`, per risolvere `aziende.codice`;
   - `extra` con i campi non mappati;
   - `data_modifica_origine`, se il gestionale la tiene (abilita la lettura
     incrementale).
3. **La mappatura dei tipi**: i codici documento del gestionale → `tipo`
   normalizzato (`ORD` → `ordine`, ...); stati; ruoli.
4. **Le letture in diretta** (§7): giacenza, fido, prezzo netto.

Il **connettore Integra** parte dalle viste del portale B2B (`DB Integra -
Luis/b2b_*.sql`), riscritte su `postgres_fdw` (i filtri arrivano al gestionale,
a differenza di `dblink`) e completate con DDT, fatture, documenti di acquisto,
`prosoggetti`, `classivoci`.

---

## 12. Fuori perimetro, per ora

| Modulo | Perché non ora | Come si aggiungerà |
|---|---|---|
| Contabilità generale (piano dei conti, prima nota) | il fatturato si ricava dai documenti; la prima nota serve a pochi | stesso schema `_versione` + vista corrente |
| Produzione (cicli, ordini di produzione) | nessuna domanda raccolta finora | idem |
| HR / paghe | dati personali delicati, non vengono dal gestionale commerciale | valutazione privacy (DPIA) prima |
| CRM (opportunità, attività) | spesso in un sistema separato | idem |
| Cespiti | idem | idem |

---

## 13. Punti aperti

1. ~~Approvazione del modello~~ — approvato e applicato il 18/09/2026.
2. **Permessi per azienda** nel gate (§8), da fare nello stesso rilascio.
3. **Gruppi e owner** del registro sorgenti per le tabelle ERP.
4. **Connettore Integra**: il gestionale registra il **costo sulle righe** dei
   documenti? Espone DDT, fatture, documenti di acquisto?
5. **Utente di sola lettura** su Integra (oggi il B2B usa l'amministratore
   `postgres`).
6. **Frequenze di sincronizzazione** per entità.
7. **`psg_liberon1`** = multiplo di vendita sempre? (campo libero, da
   confermare con chi gestisce Integra).
