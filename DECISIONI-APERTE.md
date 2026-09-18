# Decisioni aperte

**Come si usa:** quando una decisione è presa, si sposta in fondo in
*Decise* con la data e, se cambia una scelta di progetto, si registra in
`PROGETTO-RAG-Aziendale.md` §12 (nuova riga numerata). Quando è registrata, la
si cancella da qui. Lo stesso per le azioni: fatte → cancellate.

Aggiornato: 18/09/2026

---

## 1. Da decidere

| ID | Decisione | Opzioni | Proposta | Blocca |
|---|---|---|---|---|
| **D1** | **Modello di intelligenza artificiale per la chat** (`MODELLO_RAGIONAMENTO` in `.env`, oggi vuoto) | Modello locale su LM Studio / GPU aziendale · servizio esterno per le sole fonti «Può usare servizi esterni» · entrambi (decisioni 39–40, §11 dell'analisi) | Locale per partire (sovranità del dato); esterno solo per fonti approvate | Chat aperta agli utenti |
| **D2** | **Approvare `SPECIFICA-CONNETTORI.md`** (decisione 70 proposta) | Sì · con modifiche | — | Sviluppo dei connettori |
| **D3** | **Da quando importare i documenti** (ordini, DDT, fatture) | Tutto lo storico · dal 2020 · ultimi N anni | Ultimi **5 anni**; anagrafiche e listini sempre interi | Prima importazione |
| **D4** | **Frequenze delle importazioni** | Quelle di `SPECIFICA-CONNETTORI.md` §7.5 · altre | Confermare §7.5 (documenti ogni 15 min, anagrafiche ogni ora, listini di notte, giacenze alle 6) | Configurazione di Dagster |
| **D5** | **Gruppi e responsabili delle fonti** (chi vede clienti, listini, documenti, scadenze…) | Proposta in `MODELLO-DATI-GESTIONALE.md` §8 · altra | Partire dalla proposta, farla confermare a chi risponde dei dati | Fonti gestionali attive |
| **D6** | **Notifiche delle anomalie critiche** | Email · Teams · entrambe · nessuna (solo pannello) | Email per le critiche, per iniziare | Monitoraggio in produzione |
| **D7** | **Doppia approvazione per «Può usare servizi esterni»** (chi propone ≠ chi approva) | Sì · no | No per ora; lo stato «In attesa di approvazione» è già pronto | — |
| **D8** | **Da dove arrivano gli utenti** | Gestiti a mano nel pannello · dalla directory aziendale (Active Directory / Entra ID) | Directory aziendale se esiste: niente password da gestire, uscite automatiche | Messa in produzione |
| **D9** | **Il Revisore legge anche le conversazioni della chat?** | Solo configurazione e registro · anche le conversazioni | **Solo configurazione** (privacy dei dipendenti) | — |
| **D10** | **Arricchimento con l'AI dei dati personali** (referenti, dipendenti) | Sì, con valutazione d'impatto (DPIA) · no | No finché non c'è una DPIA | Arricchimenti |
| **D11** | **Ricerche sul web sulle aziende clienti** | Sì, come proposte da confermare, solo aziende · no | Sì, con revisione umana, mai su persone | Arricchimenti |
| **D12** | **Prima cartella di documenti per il pilota** | Quale cartella, quale ufficio | Documenti non sensibili, con un responsabile (manuali, cataloghi) | Indicizzazione, prova vera della chat |

---

## 2. Da sapere (informazioni, non scelte)

| ID | Domanda | A chi | Perché serve |
|---|---|---|---|
| **S1** | **Decobrands che gestionale usa?** Stesso Integra di Luis con un altro codice azienda, o un altro? | Amministrazione Decobrands | Un collegamento condiviso o un connettore nuovo |
| **S2** | **In Integra le righe dei documenti hanno il costo?** | Chi conosce Integra | Margini esatti anche sul passato |
| **S3** | **Prezzo netto per cliente in Integra**: esiste una funzione o vista da interrogare? | Fornitore di Integra | Lettura in diretta del prezzo (decisione 48: non si ricostruisce) |
| **S4** | **`psg_liberon1` è sempre il multiplo di vendita?** (campo libero di `prosoggetti`) | Chi gestisce Integra | Condizioni di acquisto corrette |
| **S5** | **La VPN verso Integra sarà sempre attiva sul server di produzione?** | Sistemisti | Oggi funziona solo con la VPN accesa (vedi *Decise*): in produzione serve un collegamento stabile |

---

## 3. Azioni da fare (non decisioni)

| ID | Azione | Chi | Note |
|---|---|---|---|
| **A1** | **Cambiare la password dell'utente `postgres` di Integra** | Sistemisti di Integra | È circolata in chiaro. Rimossa dal repository B2B e dalla sua cronologia il 18/09/2026; resta solo in `Luis Srl - B2B/SEGRETI-LOCALI.md` (locale, escluso da git) |
| **A2** | **Creare l'utente di sola lettura su Integra** per l'assistente | Sistemisti di Integra | Tabelle: vedi `SPECIFICA-CONNETTORI.md` §10. Il connettore rifiuta utenti che possono scrivere |
| **A3** | **Revocare il token GitHub** che era nella configurazione git del B2B | Tu, da GitHub → Settings → Developer settings → Personal access tokens | Tolto dalla configurazione il 18/09/2026, ma resta valido finché non lo revochi (ed è comparso nell'output di una sessione) |
| **A4** | **Inviare il tag ripulito del B2B** (permesso negato alla sessione) | Tu | `git -C "C:\Progetti\Luis Srl - B2B" push --force origin refs/tags/v0.1.0-wizard-ai` — finché non lo fai, il tag su GitHub punta alla cronologia vecchia, con la password |
| **A5** | **Riallineare le altre copie del repository B2B** (server di produzione, altri PC) | Tu / chi fa i rilasci | La cronologia è stata riscritta: `git fetch && git reset --hard origin/master` oppure un clone nuovo. Un `git pull` normale reintrodurrebbe i commit vecchi |
| **A6** | **Cancellare il backup della cronologia vecchia** quando non serve più | Tu | `C:\Progetti\_backup-segreti\` — contiene ancora la password |
| **A7** | **Commit e push del progetto RAG** | Tu decidi quando | Molte modifiche della sessione non sono ancora in git |
| **A8** | **Estendere il prototipo** con Utenti, Gruppi, Profili, Aziende | Designer | `SPECIFICA-INTERFACCE-AMMINISTRAZIONE.md` §7b e §11b |

---

## 4. Decise (da registrare in §12 e poi cancellare)

| Data | Decisione | Esito |
|---|---|---|
| 18/09/2026 | Credenziali dei gestionali | **Nel database, cifrate** (chiave in `.env`, sola scrittura) — parte della decisione 70 |
| 18/09/2026 | Chi crea l'utente di sola lettura su Integra | **I sistemisti proprietari del sistema** → azione A2 |
| 18/09/2026 | Il server raggiunge Integra (192.168.1.41)? | **Sì, con la VPN attiva** → domanda S5 per la produzione |
