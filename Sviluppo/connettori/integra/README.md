# Connettore Integra

Legge il gestionale Integra su PostgreSQL **in sola lettura** e traduce le
tabelle del portale B2B nelle colonne del modello canonico
(`MODELLO-DATI-GESTIONALE.md` §5). La traduzione vive in `viste/*.sql`; il
resto (storico SCD2, controlli, anomalie, stato, scheduler) lo fa il motore
`base.py` e `scheda.py`, uguali per ogni gestionale.

**Stato:** contratto completo (`prova` con verifica della sola lettura,
`estrai` da vista, `aziende`, `schema`) e **tutte le viste scritte** per le
entita' che il B2B espone oggi (soggetti, articoli, listini, codici, documenti
di vendita — solo ordini —, giacenze). Sono **da verificare contro lo schema
reale** di Integra, non raggiungibile in sviluppo: i nomi delle colonne vengono
dal contratto B2B e da MODELLO §5, non dallo schema vero.

## Prerequisiti (prima di importare)

1. **Utente di sola lettura** sul gestionale (oggi il B2B usa `postgres`, che
   e' amministratore). Lo script e' in
   [`utente_sola_lettura.sql`](utente_sola_lettura.sql), da eseguire sul server
   Integra. `prova()` **rifiuta** il collegamento se l'utente e' superutente o
   puo' scrivere sulle tabelle lette (§4.3).

2. **Raggiungibilita':** il server dell'assistente deve arrivare al server
   Integra (in sviluppo: 192.168.1.41). Da verificare.

3. **Collegamento dal pannello** (decisione 70): il Superutente crea il
   collegamento in *Impostazioni → Gestionali*; le credenziali sono cifrate nel
   database (`CHIAVE_CREDENZIALI`, posseduta solo dal servizio `connettori`).
   Per la CLI prima del pannello, in `.env`:
   `CONNETTORE_INTEGRA_HOST`, `_PORTA`, `_DATABASE`, `_UTENTE`, `_PASSWORD`, `_SSL`.

## Importazione

- **A mano:** `py -m connettori.esegui --azienda luis --entita soggetti --completa`
- **Programmata:** lo scheduler del servizio `connettori` (frequenze in
  `pianificazioni`, predefinite in `connettori/scheda.py`) importa le entita'
  dovute. Serve un collegamento con credenziali salvate e l'azienda abbinata
  (*Impostazioni → Aziende*: collegamento + codice nel gestionale).
- **Kit di conformita':** `py -m connettori.conformita --tipo integra` (contro
  il gestionale vero, con l'utente di sola lettura).

## Da verificare / completare

| Cosa | Note |
|---|---|
| Nomi delle colonne nelle viste | da confermare sullo schema reale di Integra e sul contratto B2B |
| `documenti_vendita` | B2B espone solo gli ORDINI di vendita; DDT e fatture vanno aggiunti al B2B per avere il fatturato |
| `documenti_acquisto` | non nel B2B: quando arriva, servira' il filtro per `ciclo` anche nella rilevazione delle cancellazioni (`base.py` oggi non lo supporta) |
| `scadenze` | da individuare nel gestionale: entita' non ancora dichiarata nel manifesto |
| Prezzo netto (`leggi_diretto`) | da chiedere a chi conosce Integra (decisione 48): non si ricostruisce il motore prezzi |
