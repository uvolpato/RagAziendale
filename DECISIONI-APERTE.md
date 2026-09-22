# Decisioni aperte

**Come si usa:** quando una decisione è presa, si sposta in fondo in
*Decise* con la data e, se cambia una scelta di progetto, si registra in
`PROGETTO-RAG-Aziendale.md` §12 (nuova riga numerata). Quando è registrata, la
si cancella da qui. Lo stesso per le azioni: fatte → cancellate.

Aggiornato: 19/09/2026

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
| **D13** | **Motore documentale open source** (lettura dei file, ricerca, grafo dei concetti) | **Cognee** (Apache 2.0; permessi per dataset; grafo + pgvector su Postgres) · RAGFlow (ottima lettura dei documenti, ma permessi solo «io / team» e accesso con Keycloak difettoso) · Onyx (permessi sui documenti solo nella versione a pagamento) · Open WebUI (seconda chat, clausola sul marchio) · LightRAG (solo spazi separati, nessun permesso) · R2R (manutenzione incerta) · motore di SWSB portato in casa | **Cognee dietro il nostro gate**: una fonte = un dataset. Il gate sceglie i dataset permessi (gruppi, azienda, stato) e Cognee cerca solo in quelli. Prima una prova di 1–2 giorni sulla cartella D12 con il modello D1 | Indicizzazione, grafo |

**Verificato il 22/09/2026** (documentazione Cognee, non ancora provato sul
codice): un dataset contiene i documenti **e i loro grafi**; i permessi sono
per dataset e mai per documento; `recall` cerca solo nei dataset consentiti, e
il percorso nel grafo puo' attraversare piu' dataset — quindi chi vede due
cataloghi trova anche gli archi fra i due. L'isolamento vale sui backend che
abbiamo gia' (Postgres, PGVector); il grafo e' l'unico pezzo nuovo.

**Punto in rosso**: `ENABLE_BACKEND_ACCESS_CONTROL` fallisce APERTO — se e'
falso i parametri dataset sono ignorati e la ricerca gira su tutti i dati.
Oggi il nostro filtro fallisce chiuso. La prova di 1-2 giorni deve cominciare
da una T1.14 fatta su Cognee, prima di qualunque valutazione sulla qualita'.
Analisi completa e ordine dei passi: `Sviluppo/PROBLEMI-APERTI.md` §6-bis.

**Requisiti di design per D13** (emersi il 19/09/2026):

1. **Architettura a tre livelli**: Docling = *estrazione* (testo, tabelle, immagini, OCR) · Cognee = *grafo + ricerca* · gate = *permessi*. Non si sostituiscono, si completano. Cognee legge i formati nativi (PDF, DOCX, …): niente obbligo di convertire tutto in Markdown; l'OCR di Docling serve solo per scansioni/foto.
2. **Immagini fuori da Cognee**: Docling estrae le immagini → volume dedicato con ID stabile. Il VLM (qwen multimodale) genera una **descrizione** che entra **nel testo** di Cognee con un **riferimento esplicito** `[immagine:id]`. In risposta, l'orchestratore risolve `id → immagine`, la passa al VLM, e il modello sa di doverla integrare perché il riferimento sta nel testo recuperato. Il VLM fa due lavori: (a) descrivere le immagini all'ingestion, (b) rispondere integrandole.
3. **Archi concettuali trasversali** (fra documenti di aree diverse) — **ESCLUSI** (decisione del 19/09/2026). Verifica Cognee: il permesso è a livello *dataset* (isolamento EBAC, grafi fisicamente separati per dataset), non a livello di *arco/nodo*. Un arco trasversale con ACL sui singoli archi richiederebbe fork/ristrutturazione del sorgente, costo da settimane + revisione di sicurezza esterna, e il rischio di un bug è fuga di dati (il costo peggiore). Il valore reale del cross-area è già servito da: (a) dati strutturati via SQL + semantic layer (decisione 6), (b) ricerca su più dataset per chi ha accesso a più aree (unione, decisione 44). Si riapre **solo** se emerge un caso concreto e nominato di "navigazione da concetto a concetto".
4. **Cognee e le immagini** (verificato sulla documentazione): Cognee tratta i **file immagine standalone** (`.png`, `.jpg`, scansioni) trascrivendoli in testo (VLM + OCR) — **non** estrae né conserva le immagini annidate dentro i PDF. Quindi: **Docling resta indispensabile** per estrarre le immagini dai PDF (cataloghi), Cognee da solo non le vede. Il ruolo di Docling si semplifica (solo estrazione di qualità, niente chunk/embedding). La lacuna "immagini dentro i PDF" va confermata nella prova pratica.
| **D14** | **Agente personale per ogni utente** (posta, calendario, attività) | **Agenti di LibreChat + server MCP con accesso delegato del singolo utente** · OpenClaw (nato per uso personale, con accesso a terminale e skill esterne) · Letta · piattaforma a parte | Agenti di LibreChat: sono già dentro la chat, con Keycloak e con i permessi per utente. Ogni utente collega il **proprio** account di posta, quindi l'agente agisce solo come lui. Invio, risposta, inoltro e accettazione di inviti sempre con conferma. La posta si legge solo con il modello locale. La memoria personale va in Cognee, in un dataset privato di ogni utente | Agente personale |
| **D16** | **Impostazioni di lettura per fonte** (oggi valgono per tutte le cartelle allo stesso modo: sono variabili d'ambiente del servizio) | Tutto globale, come adesso · un piccolo insieme di valori per fonte (colonna `impostazioni jsonb` su `sources`, modificabile dal pannello) · **profili** scelti quando si collega la fonte («Cataloghi prodotto», «Procedure e policy», «Scansioni»), con la possibilità di ritoccare i singoli valori | **Profili con ritocchi**: chi collega una cartella sceglie che tipo di documenti contiene, non quanti dpi vuole. Sotto, il profilo imposta i valori; il pannello mostra quelli **efficaci** e quali sono stati cambiati rispetto al profilo | Niente oggi. Serve quando le fonti diventano eterogenee: succede già ora |

**Requisiti di design per D16** (emersi il 20/09/2026, misurando cataloghi veri):

1. **Perché serve.** Le fonti chiedono lavori diversi. Il catalogo IPURO ha
   prodotto **324 immagini su 41 pagine** (~8 per pagina), ognuna descritta da
   un modello visivo: è il grosso del tempo di lettura. Le cartelle di
   sicurezza, 37 documenti, hanno prodotto **zero immagini**. Con le
   impostazioni globali, chi indicizza procedure paga il costo dei cataloghi, e
   chi indicizza cataloghi non può alzare la risoluzione senza rallentare tutti.
2. **Cosa può cambiare per fonte** (costo e qualita' della lettura, nessun
   effetto sui permessi): estrarre le immagini sì/no; descriverle sì/no e con
   quale modello; risoluzione delle figure (`images_scale`, `MAX_LATO`, scala
   dell'immagine mandata al modello); soglia dell'area sotto la quale una
   figura non si descrive; pagine per blocco; lingue dell'OCR; limiti di orario
   e di dimensione (`FINESTRA_NOTTE`, `MB_MAX_DI_GIORNO`).
3. **Cosa NON può cambiare per fonte**, o cambia solo con la stessa
   approvazione che attiva la fonte: l'esclusione dei fogli con i prezzi
   (decisione 72), gruppi e aziende (ACL), residenza, allowlist di egress.
   Sono regole su *cosa entra nell'indice e chi lo vede*: se diventano una
   casella per cartella, prima o poi qualcuno la spunta per fretta.
4. **Le soglie della GPU restano della macchina, non della fonte**:
   `VRAM_MINIMA_MB`, `GPU`, `LMSTUDIO_HOST` descrivono l'hardware su cui gira il
   servizio, non il contenuto della cartella.
5. **Il pannello mostra i valori efficaci**, non solo quelli scelti: chi guarda
   una fonte deve capire *perché* un file è stato saltato o letto in un certo
   modo, senza aprire il codice né il `.env`.
6. **Pochi valori, default sensati.** Il rischio non è tecnico: è una pagina
   di impostazioni che nessuno sa compilare. Per questo la proposta è partire
   dai profili e non dalla ventina di manopole.

**Prezzi nelle descrizioni delle immagini** (emerso il 20/09/2026, precisa la
decisione 72). Indicizzando un catalogo, il modello che descrive le figure ha
trascritto un listino: «F0305 370 ml 12 € 2,15 F0405 500 ml 12 € 2,85…». La
decisione 72 tiene i prezzi fuori dall'indice perché vengono dal gestionale, e
il controllo esisteva solo sui fogli di calcolo: dalle immagini rientravano
senza che nessuno se ne accorgesse. Ora c'è il filtro, ma **è un parametro**
(`PREZZI_DESCRIZIONI=escludi|ammetti`), perché il caso contrario è altrettanto
reale: su un **catalogo fornitore** quel prezzo è l'unico che esiste — nel
gestionale non c'è — e una domanda come «trovami dieci articoli sotto i 10 euro
con queste caratteristiche» senza quei numeri non si può soddisfare.
Da decidere insieme a D16 (è una delle impostazioni per fonte):

- default **escludi**, e si ammette solo dove il documento È la fonte
  autorevole del prezzo (cataloghi fornitore, listini dei fornitori);
- quando si ammette, la risposta deve citare **documento e data**, perché un
  prezzo di catalogo invecchia: è l'opposto del prezzo del gestionale, che è
  valido adesso;
- prezzi da documenti e prezzi dal gestionale non vanno mai mescolati nella
  stessa risposta senza dire da dove vengono.

**Cosa succede quando due fonti hanno impostazioni diverse** (analisi del
20/09/2026). Oggi non succede niente: un `pg_advisory_lock` garantisce **un solo
giro** in tutto il sistema, le fonti si leggono una dopo l'altra in ordine di
id, i file uno dopo l'altro, i blocchi di pagine uno dopo l'altro. Due letture
non si sovrappongono mai. Ma le impostazioni per fonte introducono quattro
punti che vanno previsti **prima** di scriverle, non dopo:

7. **Le impostazioni viaggiano col file, non nell'ambiente.** Oggi
   `_opzioni_pdf()` legge variabili globali, e il processo figlio che converte
   un blocco le rilegge da lì. Se restano globali, basta un domani in cui due
   letture si sovrappongono e il file della fonte A viene letto con i parametri
   della B: non dà errore, produce solo un indice peggiore, e non se ne accorge
   nessuno per mesi. Vanno **passate come argomento** fino al processo figlio.
8. **Un modello visivo per fonte fa ballare la VRAM.** Cambiare VLM fra una
   fonte e l'altra significa scaricare e ricaricare ~2,3 GB. Finché si processa
   **una fonte per intero prima di passare alla successiva**, il costo è una
   volta per fonte e si accetta; se si mescolano i file diventa un'altalena.
   Altro motivo per tenere l'ordine «fonte per fonte».
9. **Lo scarico del modello di chat NON è per fonte.** Quando l'indicizzazione
   lo scarica, l'assistente tace **per tutti** e per tutta la durata del giro,
   non della fonte. Finestra oraria e soglia di dimensione si possono decidere
   per fonte; la decisione di liberare la VRAM resta del giro, come
   `VRAM_MINIMA_MB` e `GPU` restano della macchina (punto 4).
10. **Una fonte non deve affamare le altre.** Un catalogo da 312 pagine tiene
    il giro occupato per venti minuti: se quella fonte riceve documenti di
    continuo, le altre non vengono lette mai. Serve un tetto di **N file per
    fonte per giro**, poi si passa alla prossima e si riprende al giro dopo.
11. **Il modello di embedding non diventa per fonte, mai.** I vettori di fonti
    diverse vivono nello stesso spazio e si confrontano fra loro: due modelli
    danno punteggi che non vogliono dire niente. È già protetto da
    `index_meta` (canary) e lì deve restare.
12. **Se un giorno si leggeranno due file in parallelo** (`ingestion/PRESTAZIONI.md`
    §3.5): solo file della **stessa fonte** — stesse impostazioni, stesso
    modello già caricato — e solo se la VRAM libera regge due conversioni.
    Parallelizzare fonti diverse è il modo più rapido per ottenere insieme
    l'altalena dei modelli e il difetto del punto 7.

| **D15** | **Dove stanno le cartelle dei documenti e chi fa rispettare i permessi di scrittura** (modello: una cartella per gruppo, scritta e letta dai suoi membri; una cartella generale letta da tutti e scritta da pochi gruppi) | Condivisione Windows con gruppi di Active Directory (serve D8: Keycloak collegato ad AD) · **Nextcloud con cartelle di gruppo e accesso con Keycloak** (stessi gruppi, caricamento dal web e sincronizzazione da PC; in futuro anche SharePoint e Google Drive) · caricamento dal nostro pannello | Se l'azienda ha già un file server con AD → quello. Altrimenti Nextcloud. **Da sapere prima**: c'è un file server? c'è AD? Il modello (decisione 71) è fatto e funziona su cartelle locali: manca solo dove stanno i file veri | Cartelle dei gruppi in produzione |
| **D17** | **L'orchestratore diventa un agente?** Oggi è una catena fissa: token → embedding → ricerca con ACL nella query → gate → prompt → modello. Un agente deciderebbe da sé quali strumenti chiamare e quante volte | Catena fissa, come adesso · **agente che decide solo la ricerca** (quante interrogazioni, con che parole, quando fermarsi), con recupero e permessi che restano codice · agente pieno, con i permessi fra i suoi strumenti | **Agente sulla sola ricerca**, e non prima che l'indice sia stabile. LangGraph è già fra le dipendenze dell'orchestratore | Niente oggi. Serve per le domande a più passi: «confronta i prezzi di EUROSAND e FLEURAMI» oggi fa una ricerca sola |
| **D18** | **I prompt escono dal codice?** Oggi i quattro prompt del sistema sono stringhe Python: la risposta in chat (`orchestratore/prompt.py`), la riformulazione (`orchestratore/riformula.py`), la lettura della pagina col VLM e la descrizione delle figure (`ingestion/indicizza.py`). Cambiare una parola richiede di modificare il codice e ricostruire l'immagine | Restano nel codice · file di configurazione montato · **campo nel database, modificabile dal pannello**, con lo storico delle versioni | **Nel database con lo storico**, e per FONTE dove ha senso (i due prompt di lettura), globale per i due di risposta. Ma non prima che la precisione sia a regime: finché i prompt cambiano ogni giorno, il codice è il posto giusto e git è lo storico. Attenzione: `ISTRUZIONI_PAGINA` è l'impronta della cache del Markdown (`IMPRONTA_PROMPT`), quindi cambiarlo rilegge tutte le pagine — chi lo modifica dal pannello deve saperlo | Niente oggi. Serve quando a scrivere i prompt non sarà più chi tocca il codice. Segnalato il 22/09/2026, misurando che una sola regola aggiunta al prompt di risposta portava la completezza dal 46% al 73%: ogni prova di quel tipo oggi costa una ricostruzione dell'immagine |

**Requisiti di design per D17** (emersi il 21/09/2026):

1. **L'agente decide COSA cercare, mai COSA può vedere.** Il recupero resta una
   funzione con i gruppi come parametro obbligatorio, e fra gli strumenti
   dell'agente non esiste niente che li cambi. È la riga che separa questa
   decisione da una fuga di dati: oggi i gruppi stanno nella `WHERE` e non
   c'è percorso che li salti, e deve restare vero anche dopo.
2. **`test_gate.py` deve continuare a significare qualcosa.** Le sue 19
   affermazioni sono verdi perché sono rami di codice. Se una diventa «il
   modello si ricorda di chiamare lo strumento giusto», non è più una
   verifica: è una speranza. Nessuna regola di sicurezza passa dal prompt.
3. **Il costo sta in VRAM, non in righe.** Ogni passo dell'agente è un giro
   del modello: una domanda che oggi costa una chiamata ne costerebbe tre o
   quattro. Su questa macchina sono 16 GB condivisi con Docling, con 4–5
   persone in parallelo. Prima di decidere serve la misura, non la stima:
   quante domande vere hanno davvero bisogno di più di una ricerca.
4. **Il guadagno vero è il confronto fra documenti.** Le domande a un passo
   sono già servite bene dalla catena fissa; quelle che oggi falliscono sono
   quelle che richiedono due ricerche e un confronto. Se la misura del punto
   3 dice che sono poche, la risposta giusta a D17 è «no».
5. **Da fare dopo**, non prima: indice stabile e «sassi rossi» verificato.
   Questa modifica cambia la forma di ogni risposta, e mescolarla a un
   difetto di recupero aperto renderebbe impossibile capire cosa ha rotto
   cosa.
6. **Misurato su una conversazione vera** (21/09/2026, cinque turni sui
   profumatori): dei tre difetti trovati, **un agente non ne avrebbe corretto
   nessuno**. Due blocchi «Fonti» di seguito — il modello ricopiava l'elenco
   del turno prima perché glielo rimandavamo indietro nella cronologia: il
   difetto sta in *cosa gli passi*, non in come decide, e un agente l'avrebbe
   ricopiato allo stesso modo. Otto voci per due pagine nell'elenco delle
   fonti: formattazione, cioè codice. Il modello che scrive «consulta il PDF»
   mentre il sistema gli allega le figure sotto: non sa cosa farà il sistema
   dopo di lui, e resta vero anche da agente — a meno che allegare le immagini
   non diventi uno *strumento* suo, e allora vale il punto 1.

   Il caso in cui l'agente avrebbe aiutato **non compare** in quella
   conversazione: nessuno ha chiesto un confronto fra due cataloghi. È il
   punto 4 visto dall'altra parte — prima di decidere serve sapere quante
   domande vere sono di quel tipo, e finora le osservate sono zero su cinque.

---

## 2. Da sapere (informazioni, non scelte)

| ID | Domanda | A chi | Perché serve |
|---|---|---|---|
| **S1** | **Decobrands che gestionale usa?** Stesso Integra di Luis con un altro codice azienda, o un altro? | Amministrazione Decobrands | Un collegamento condiviso o un connettore nuovo |
| **S2** | **In Integra le righe dei documenti hanno il costo?** | Chi conosce Integra | Margini esatti anche sul passato |
| **S3** | **Prezzo netto per cliente in Integra**: esiste una funzione o vista da interrogare? | Fornitore di Integra | Lettura in diretta del prezzo (decisione 48: non si ricostruisce) |
| **S4** | **`psg_liberon1` è sempre il multiplo di vendita?** (campo libero di `prosoggetti`) | Chi gestisce Integra | Condizioni di acquisto corrette |
| **S5** | **La VPN verso Integra sarà sempre attiva sul server di produzione?** | Sistemisti | Oggi funziona solo con la VPN accesa (vedi *Decise*): in produzione serve un collegamento stabile |
| **S6** | **Che posta e calendario usate?** Microsoft 365, Google Workspace o un altro server di posta | Sistemisti | Serve per scegliere il connettore MCP e per registrare l'app con le autorizzazioni delegate |

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
| **A8** | **Estendere il prototipo** con Utenti, Gruppi, Profili, Aziende | Designer | `SPECIFICA-INTERFACCE-AMMINISTRAZIONE.md` §7b e §11b |

---

## 4. Decise (da registrare in §12 e poi cancellare)

| Data | Decisione | Esito |
|---|---|---|
| 18/09/2026 | Credenziali dei gestionali | **Nel database, cifrate** (chiave in `.env`, sola scrittura) — parte della decisione 70 |
| 18/09/2026 | Chi crea l'utente di sola lettura su Integra | **I sistemisti proprietari del sistema** → azione A2 |
| 18/09/2026 | Il server raggiunge Integra (192.168.1.41)? | **Sì, con la VPN attiva** → domanda S5 per la produzione |
