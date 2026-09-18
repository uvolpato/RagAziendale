# Valutazione del progetto: parere franco

> 19/09/2026. Scritta su richiesta, a metà percorso. Da rileggere a ogni cambio di fase.
> Non ripete le decisioni (sono in `PROGETTO-RAG-Aziendale.md` §12) né le scelte aperte (sono in `DECISIONI-APERTE.md`).

---

## 1. In una riga

**Le fondamenta sono buone, più di quanto serve oggi. Ma il progetto ha costruito tutto intorno alla risposta e ancora niente della risposta.** Nessun collega ha mai fatto una domanda e ricevuto una risposta da un documento vero. Questo è il rischio principale, e non è tecnico.

---

## 2. I punti di forza

| # | Punto | Perché conta |
|---|---|---|
| P1 | **La sicurezza è nel posto giusto.** Il filtro per gruppi, azienda e stato della fonte sta *dentro la query* di ricerca, non a valle. C'è un unico punto di uscita verso l'esterno (egress). Contaminazione e residenza dei dati sono tracciate | L'errore tipico dei sistemi RAG è mostrare un documento a chi non doveva vederlo. Qui è progettato per non succedere, ed è **verificato da test**: gate 15/15, deleghe 24/24, login 14/14, amministrazione 25/25 |
| P2 | **Sovranità del dato.** I modelli sono locali e il cloud si usa solo per le fonti approvate | È un vantaggio vero, sia commerciale sia normativo (GDPR, segreto industriale) |
| P3 | **Un'unica interfaccia di amministrazione**, con deleghe fini in Keycloak: chi gestisce gli accessi non può darsi più poteri | Pochi prodotti, anche commerciali, lo fanno bene. La scalata di privilegi è stata trovata, provata e chiusa |
| P4 | **Il modello dati del gestionale è pensato per durare.** Storico completo (SCD2), più aziende, chiavi indipendenti dal gestionale | Andamento dei prezzi e margini nel tempo si ottengono «gratis». Cambiare gestionale non rompe niente |
| P5 | **Tutto è scritto e motivato.** 70 decisioni con il loro perché, specifiche, decisioni aperte che si svuotano man mano | Chi arriva dopo capisce. Lo si vede già: le sessioni ripartono senza perdere il filo |
| P6 | **Prodotti esistenti, non codice scritto da zero**: LibreChat, Keycloak, Postgres/pgvector, Dagster, Caddy. Il codice nostro è circa 9.700 righe, di cui molte sono test | La parte da mantenere resta relativamente piccola |

---

## 3. Le criticità, in ordine di gravità

### C1. Lo scopo si è allargato prima della prima risposta ⚠️ la più grave

L'obiettivo della fase 1 (piano) era: *«un collega apre il browser, fa una domanda sui manuali e ottiene una risposta corretta, citata e filtrata. Niente altro.»*

Ad oggi:
- **manca la lettura dei documenti** (`ingestion/`, «da scrivere»);
- **manca il modello** per le risposte (D1);
- **mancano documenti veri** (il campione è generato);
- **gli utenti sono zero**.

Nel frattempo si sono costruiti:
- il pannello di amministrazione (circa 2.600 righe);
- il modello dati del gestionale;
- i connettori;
- le deleghe di Keycloak.

Sono in arrivo idee grandi: grafo dei concetti, agente personale, arricchimenti.

È esattamente il rischio che il documento di progetto (§14.1) aveva previsto: *«lo sviluppatore non ha niente da fare e costruisce infrastruttura su requisiti immaginati»*. Il criterio che ci siamo dati, **adozione reale, non completezza tecnica**, oggi non è misurabile.

**Soluzione.** Si congela tutto ciò che non porta alla prima risposta vera. Obiettivo di 4–6 settimane: **una cartella, un ufficio, 10 colleghi, domande vere**. Le idee restano tracciate (§13, «Evoluzioni future») ma non si toccano finché questo non è raggiunto.

### C2. La fase 0 non ha un proprietario in azienda

Mancano ancora:
- chi risponde di ogni fonte;
- la matrice dei permessi confermata;
- le domande vere dei colleghi;
- la cartella pilota (D12).

Nessuna di queste cose si risolve con il codice.

**Soluzione.** Serve **una persona dell'azienda con autorità**, con nome e scadenze, che porti le risposte. Senza di lei il progetto resta un ottimo prototipo.

### C3. Dipendenze esterne bloccate, e nessun piano per gestirle

Sono bloccate: la VPN verso Integra, l'utente di sola lettura (A2), la password da cambiare (A1) e l'hardware per i modelli (D1, fase 1b). Oggi lo stallo si gestisce cambiando argomento, e ogni cambio aggiunge scopo.

**Soluzione.**
- Ogni blocco ha **un responsabile e una data** in `DECISIONI-APERTE.md`.
- Mentre si aspetta, si lavora **solo sul percorso della prima risposta**, non su funzioni nuove.
- Per il modello, sul pilota con documenti non sensibili (manuali, procedure), si possono usare un modello cloud approvato o una GPU a noleggio. Si decide l'acquisto dopo, sapendo cosa serve (è già la regola della fase 1b).

### C4. Troppi pezzi da far girare per un'azienda di queste dimensioni

Oggi i servizi sono circa 10: LibreChat, MongoDB, Keycloak, Postgres, Caddy, orchestratore, amministrazione, connettori, Dagster e LiteLLM, più l'host per i modelli. Le proposte ne aggiungono altri (Cognee, Nextcloud).

Ognuno va aggiornato, salvato, sorvegliato. Il passaggio a Keycloak 26.7 ha già mostrato quanto costa un aggiornamento: sessioni, `offline_access`, FGAP.

**Soluzione.**
- **Versioni bloccate** e **suite di verifica** (che c'è già) da far girare a ogni aggiornamento.
- **Piano di backup e ripristino provato** prima della produzione: oggi non esiste.
- Regola: **si aggiunge un servizio solo se ne toglie lavoro scritto a mano, mai «perché servirà»**.

### C5. Una sola persona, e il codice l'ha scritto un'AI

Il progetto dipende da una persona. Gran parte del codice e delle configurazioni più delicate (deleghe FGAP, gate) è stata scritta da un assistente AI. Funziona ed è testata, ma in produzione qualcuno deve saperla **leggere, correggere e aggiornare** senza l'assistente.

**Soluzione.**
- In produzione, l'IT (interna o un fornitore) prende in carico lo stack, con passaggio di consegne sui tre punti delicati: gate, deleghe Keycloak, migrazioni.
- La documentazione c'è già: va verificata da chi dovrà usarla.

### C6. La qualità dei modelli locali è ancora un'ipotesi

Tutta l'architettura presume un modello locale abbastanza bravo:
- a rispondere citando le fonti;
- a usare gli strumenti (agente personale);
- a estrarre concetti (grafo).

I modelli piccoli sono deboli proprio su questo, e il confronto 14B / 32B / 70B non è ancora stato fatto.

**Soluzione.** È già previsto: la valutazione con le domande vere, prima dell'acquisto. Il punto è **farla presto**, perché cambia cosa ha senso costruire (per esempio: il grafo con un 14B potrebbe non valere lo sforzo).

### C7. Rischi legali sui lavoratori (Italia)

Il registro delle domande, l'agente che legge la posta e un Revisore che potesse leggere le chat possono configurarsi come **controllo a distanza dei lavoratori** (art. 4 dello Statuto dei lavoratori), oltre al GDPR. Servono informativa, possibile valutazione d'impatto (DPIA) e, a seconda dei casi, accordo sindacale o autorizzazione.

**Soluzione.** Verifica con il consulente del lavoro o il DPO **prima** della produzione. D9 (il Revisore legge solo la configurazione) va già nella direzione giusta.

### C8. Igiene: segreti e lavoro non salvato

- Le password e il token finiti nei repository sono stati tolti dalla cronologia, ma **non ancora cambiati o revocati** (A1, A3, A4).
- Nel progetto RAG c'è **molto lavoro non ancora salvato in git**: amministrazione, connettori, gate per azienda.

**Soluzione.**
- Cambiare password e token questa settimana.
- Salvare in git (commit) il lavoro fatto a ogni pezzo funzionante, non a fine giornata.

### C9. Strumento interno o prodotto? Non è deciso

Alcune scelte (connettori a catalogo, «installato presso clienti diversi», più aziende) guardano a un **prodotto**; il piano descrive uno **strumento interno**. Un prodotto costa 3–5 volte di più: installazione, aggiornamenti presso terzi, supporto, licenze.

**Soluzione.** Decidere esplicitamente. Finché non è deciso, vale «strumento interno»: le scelte da prodotto già fatte vanno bene, ma non se ne aggiungono altre.

### C10. Dipendenza da LibreChat

LibreChat è usato un po' «contro natura»: caricamento dei file bloccato nel proxy, orchestratore come finto modello. Un aggiornamento può rompere queste integrazioni.

**Soluzione.**
- Versione bloccata, e `verifica_login.py` a ogni aggiornamento (c'è già).
- Rivalutare quando LibreChat avrà le attività pianificate e le approvazioni: potrebbero semplificare il nostro lavoro, non complicarlo.

---

## 4. Cosa farei adesso, in ordine

1. **Commit** di tutto il lavoro fatto. **Cambiare password e token** (A1, A3, A4).
2. **Nominare il referente aziendale** della fase 0, e scegliere il primo ufficio pilota (D12).
3. **Lettura dei documenti dalle cartelle**, sul modello a cartelle per gruppo, e prima misura della qualità della ricerca sul campione.
4. **Scegliere il modello per il pilota** (D1), anche provvisorio.
5. **10 colleghi, 2 settimane, domande vere.** Si misura: quante domande, quante risposte giuste, chi torna a usarlo.
6. Solo dopo: grafo (D13), gestionale (quando la VPN funziona), agente personale (D14).

---

## 5. Il giudizio complessivo

**Il progetto è fattibile e ben impostato. Il suo rischio non è fallire tecnicamente: è arrivare tecnicamente completo e non usato.** La base di sicurezza e amministrazione è già sopra la media. Ora va spesa tutta l'energia sulla prima risposta utile a un collega, e sul verificare che qualcuno la voglia davvero.
