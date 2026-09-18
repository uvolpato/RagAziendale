# Connettore Integra

Legge il gestionale Integra su PostgreSQL **in sola lettura** e traduce le
viste del portale B2B nelle colonne del modello canonico
(`MODELLO-DATI-GESTIONALE.md` §5). La traduzione vive in `viste/*.sql`; il
resto (storico SCD2, controlli, anomalie, stato, scheduler) lo fa il motore
`base.py` e `scheda.py`, uguali per ogni gestionale.

**Come si legge:** il gestionale non si interroga direttamente. Esiste un
**database parallelo `rag`** che espone le viste `rag_*` e legge Integra con
`postgres_fdw`. Il connettore punta a quel database. Le definizioni delle viste
sono in [`rag-viste.sql`](rag-viste.sql) (da creare sul database `rag`).

## Viste `rag_*` → modello canonico

| Vista `rag_*` | Entità canonica | Note |
|---|---|---|
| `rag_prodotti` | `articoli`, `articoli_codici` | classificazioni/attributi in jsonb; `codice_alternativo`/`codice_esterno` → `articoli_codici` (tipo `alternativo`/`esterno`) |
| `rag_clienti` | `soggetti`, `soggetti_ruoli` | il **soggetto** è `id_master` (anagrafica unica); il **ruolo** è il cliente (`cla_tipo='C'`) con le condizioni commerciali |
| `rag_indirizzi_clienti` | `indirizzi` | `soggetto_id` = `id_cliente` (che nella vista è `cli_cdcli` = id_master) |
| `rag_ordini_clienti` | `documenti` (vendita) | solo **ordini** (`mvt_natmov='ORD'`) |
| `rag_righe_ordini` | `documenti_righe` | niente data modifica: si rilegge per intero |
| `rag_listini_testata` | `listini` | |
| `rag_listini_righe` | `listini_righe` | |
| `rag_tabpag`, `rag_tabpor`, `rag_tabspe` | `codici` | tipi `pagamento`, `porto`, `spedizione` |

## Prerequisiti (prima di importare)

1. **Utente di sola lettura** sul database `rag`. Lo script e' in
   [`utente_sola_lettura.sql`](utente_sola_lettura.sql), da eseguire come
   superutente su quel database. `prova()` **rifiuta** il collegamento se
   l'utente e' superutente o puo' scrivere sulle viste lette (§4.3).

2. **Raggiungibilita':** il server dell'assistente deve arrivare al server del
   database `rag` (in sviluppo: 192.168.1.41). Da verificare.

3. **Collegamento dal pannello** (decisione 70): il Superutente crea il
   collegamento in *Impostazioni → Gestionali*; le credenziali sono cifrate nel
   database (`CHIAVE_CREDENZIALI`, posseduta solo dal servizio `connettori`).
   Per la CLI prima del pannello, in `.env`:
   `CONNETTORE_INTEGRA_HOST`, `_PORTA`, `_DATABASE` (= `rag`), `_UTENTE`,
   `_PASSWORD`, `_SSL`.

## Importazione

- **A mano:** `py -m connettori.esegui --azienda luis --entita soggetti --completa`
- **Programmata:** lo scheduler del servizio `connettori` (frequenze in
  `pianificazioni`, predefinite in `connettori/scheda.py`) importa le entita'
  dovute. Serve un collegamento con credenziali salvate e l'azienda abbinata
  (*Impostazioni → Aziende*: collegamento + codice nel gestionale).
- **Kit di conformita':** `py -m connettori.conformita --tipo integra` (contro
  il gestionale vero, con l'utente di sola lettura).

## Da completare

| Cosa | Note |
|---|---|
| **Multi-azienda** | le viste `rag_*` hanno `azi_cdazi = '001'` **hardcoded**: vanno parametrizzate (o duplicate per azienda) prima di Decobrands |
| `aziende()` | non esiste una vista `rag_aziende`: servirà una vista sull'anagrafica aziende di Integra |
| `articoli_fornitori` | manca la vista su `prosoggetti`: entità rimossa dal manifesto finché non arriva |
| `giacenze` | manca la vista su `maginv`/`maginvt`: entità (fotografia) rimossa dal manifesto |
| `documenti_acquisto` | non nelle viste: quando arriva, servirà il filtro per `ciclo` nella rilevazione delle cancellazioni (`base.py` non lo supporta) |
| `scadenze` | da individuare nel gestionale |
| DDT e fatture | `rag_ordini_clienti` espone solo gli ORDINI: senza, niente fatturato |
| `vettori`, `famiglie`, `linee` (in `codici`) | non c'è la vista: i codici coprono solo pagamento/porto/spedizione |
| Prezzo netto (`leggi_diretto`) | da chiedere a chi conosce Integra (decisione 48): non si ricostruisce il motore prezzi |

## Dati esclusi (minimizzazione, §7)

- **IBAN, ABI, CAB, BIC, mandato SDD**: presenti in `rag_clienti` e
  `rag_pagamenti_clienti` ma **mai letti** (la vista `rag_pagamenti_clienti`
  non è nemmeno fra le `TABELLE_LETTE`). Il kit di conformità le segnala come
  difetto se un connettore le emette.
