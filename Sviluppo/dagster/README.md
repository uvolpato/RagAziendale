# Guida — importazioni programmate dai gestionali

Come i dati del gestionale (Integra) entrano nell'assistente, da soli, secondo
un piano. Questa guida spiega **come funziona** e **come si usa**, non i
dettagli del codice (quelli stanno in `SPECIFICA-CONNETTORI.md`,
`MODELLO-DATI-GESTIONALE.md` e nei commenti dei file).

---

## 1. Il flusso, in breve

```
 Dagster (orologio + storico + UI)          ← pianifica
        │  POST /v1/esegui-dovute
        ▼
 servizio `connettori`  (unico con la chiave)
        │  decifra le credenziali, costruisce il connettore
        ▼
 motore `base.importa`  (storico SCD2, controlli, anomalie)
        │
        ▼
 erp_storico.*  +  erp.sincronizzazioni  +  anomalie
        │
        └── il pannello (schermata Importazioni) legge lo stato
```

Chi fa cosa:

| Componente | Ruolo | Dove |
|---|---|---|
| **Dagster** | orologio, storico esecuzioni, UI | `dagster/` + servizi `dagster-webserver`/`dagster-daemon` |
| **servizio connettori** | unico possessore di `CHIAVE_CREDENZIALI`; decifra e importa | `servizio-connettori/` → `connettori/servizio.py` |
| **connettore** | traduce le tabelle del gestionale nel modello canonico | `connettori/<tipo>/` (es. `integra/`) |
| **motore** | storico SCD2, idempotenza, controlli, anomalie | `connettori/base.py` |
| **pianificazione** | "quali importazioni sono dovute" | `connettori/scheda.py` + tabella `pianificazioni` |

Il punto chiave: **Dagster non importa nulla**. Fornisce solo l'orologio e lo
storico. Il lavoro vero lo fa il servizio `connettori`, che è anche l'unico a
conoscere le password (cifrate nel database con `CHIAVE_CREDENZIALI`).

---

## 2. I pezzi che devono essere a posto

Prima che un'importazione possa riuscire:

1. **Un collegamento** al gestionale (pannello → *Impostazioni → Gestionali*):
   tipo, server, database, utente **di sola lettura**, password (cifrata). Il
   pulsante **Prova collegamento** verifica connessione e sola lettura *prima*
   di salvare.
2. **Un'azienda abbinata** (*Impostazioni → Aziende*): scegli il collegamento e
   il **codice nel gestionale** (scoperto dal collegamento). I dati arrivano
   nello spazio `<azienda>:<id>`.
3. **Raggiungibilità** del gestionale dal server + **utente di sola lettura**
   creato sul gestionale (`connettori/integra/utente_sola_lettura.sql`).
4. Le **viste** (`connettori/integra/viste/*.sql`) verificate contro lo schema
   reale: sono scritte dalle mappature documentate, da confermare.

---

## 3. Le frequenze

Predefinite in `connettori/scheda.py` (§7.5):

| Entità | Frequenza |
|---|---|
| documenti_vendita | ogni 15 minuti |
| soggetti, articoli | ogni ora |
| listini, codici | ogni notte |
| giacenze (fotografia) | ogni giorno |

Si modificano per azienda nella tabella `pianificazioni` (migrazione 007):
assenza di riga = predefinito; `attiva=false` = disattivata. Oggi la tabella si
tocca da SQL o dal codice; l'interfaccia la offrirà Dagster (decisione 60).

La schedule di Dagster gira ogni 15 minuti (`importazioni_ogni_15_minuti`) e
chiama `/v1/esegui-dovute`, che importa **solo ciò che è dovuto** secondo le
frequenze: se un'entità è già stata importata da poco, non si ripete.

---

## 4. Come si usa

**Dal pannello:**
- *Impostazioni → Gestionali*: crea/verifica il collegamento.
- *Impostazioni → Aziende*: abbina l'azienda al collegamento + codice.
- *Controllo → Importazioni*: griglia azienda × tipo di dato (esito, quando,
  righe, errore) con **Avvia ora** su ogni cella.
- *Strumenti → Gestione importazioni*: la UI di Dagster (storico run, log,
  avvio manuale), a `https://dagster.localhost`, dietro **SSO aziendale**
  (oauth2-proxy + Keycloak): entri con le credenziali aziendali, solo gli
  amministratori (gruppo `/amministratori`).

**Dalla UI di Dagster** si possono vedere le run passate, i log, lanciare
`importa_singola` (config: collegamento, azienda, entità) o
`importazioni_programmate`.

---

## 5. Note e limiti (ponytail)

- **Scheduler ponte disattivato**: con Dagster attivo, il ciclo dentro il
  servizio connettori è spento (`SCHEDULER_OFF=1`). Per tornare al ponte (se
  Dagster non è disponibile), svuota quella variabile.
- **Errori nel sistema generale**: se un run di Dagster fallisce (es. il
  servizio connettori non risponde), il job lo segnala alle **anomalie** del
  pannello (`/v1/anomalia`, impronta `dagster:esegui-dovute`), e le chiude
  (`/v1/chiudi-anomalia`) quando il giro successivo riesce. Così i problemi non
  restano solo nei log di Dagster.
- **Storage di Dagster in SQLite** (volume `dagster-data`): al primo avvio
  webserver e daemon migrano insieme e può comparire un
  `table alembic_version already exists` innocuo. In produzione si passa a
  Postgres (`dagster-postgres` + `dagster.yaml`).
- **SSO**: Dagster è dietro oauth2-proxy + Keycloak (client `dagster`, creato e
  allineato da `keycloak/deleghe.py`); in sviluppo oauth2-proxy salta solo il
  confronto issuer configurato/scoperto (i token restano validati).
- **Cancellazioni** (SCD2): si rilevano solo sulla lettura completa. Le entità
  incrementali (soggetti, articoli) avranno bisogno di una lettura completa
  notturna separata.
- **`documenti_acquisto` e `scadenze`** non sono ancora nel connettore Integra:
  il B2B oggi espone solo gli ordini di vendita.
