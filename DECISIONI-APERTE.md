# Decisioni aperte

**Come si usa:** quando una decisione è presa, si sposta in fondo in
*Decise* con la data e, se cambia una scelta di progetto, si registra in
`PROGETTO-RAG-Aziendale.md` §12 (nuova riga numerata). Quando è registrata, la
si cancella da qui. Lo stesso per le azioni: fatte → cancellate.

Aggiornato: 24/09/2026

---

## 1. Da decidere

| ID | Decisione | Opzioni | Proposta | Blocca |
|---|---|---|---|---|
| **D1** | **Modello di intelligenza artificiale per la chat** (`MODELLO_RAGIONAMENTO` in `.env`). **Scelto il 24/09/2026: `qwen3.8-27b-gsq-rco`** su llama-swap (locale), con escalation a un'API a pagamento solo su fallimento dimostrato | Modello locale su LM Studio / GPU aziendale · servizio esterno per le sole fonti «Può usare servizi esterni» · entrambi (decisioni 39–40, §11 dell'analisi) | Locale per partire (sovranità del dato); esterno solo su escalation dell'agente | Chat aperta agli utenti |
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
si puo' cercare su piu' dataset in una volta (unione, decisione 44).
L'isolamento vale sui backend che abbiamo gia' (Postgres, PGVector); il grafo
e' l'unico pezzo nuovo.

**Resta vero il requisito 3**: i grafi sono separati per dataset, quindi NON
esistono archi fra documenti di aree diverse. Cercare su piu' dataset unisce i
risultati, non i grafi.

**Direzione (confermata il 25/09/2026)**: costruire un grafo per ogni
COMBINAZIONE di aree, scelto in base ai gruppi di chi chiede. Aggira il
problema dei permessi — non servono ACL sugli archi, perché ogni grafo è
interamente consentito a chi lo usa: chi ha solo «acquisti» vede il grafo
«acquisti», chi ha «acquisti» + «commerciale» vede il grafo «acquisti+commerciale».
**Posticipato, non escluso**: si riprende dopo il problema dei cataloghi. Caso
d'uso vero: l'**area legale**, dove il collegamento fra documenti è la domanda
stessa («quali contratti citano questa policy»).

**Come costruirli (raffinato il 25/09/2026)**: NON a priori. **Base**: un grafo
per area + uno "tutti i documenti" (per il superutente). **Su richiesta**: i
grafi di aggregazione (acquisti+commerciale, ecc.) si costruiscono quando un
profilo li configura, non tutte le combinazioni possibili. Il grafo è un
artefatto derivato dal profilo (documenti + aree), quindi sempre ricostruibile.
Costi: latenza al primo uso di un profilo multi-area (una volta sola) e
invalidazione quando cambia un'area (il grafo che la contiene si marca "da
ricostruire").

**Punto in rosso**: `ENABLE_BACKEND_ACCESS_CONTROL` fallisce APERTO — se e'
falso i parametri dataset sono ignorati e la ricerca gira su tutti i dati.
Oggi il nostro filtro fallisce chiuso. La prova di 1-2 giorni deve cominciare
da una T1.14 fatta su Cognee, prima di qualunque valutazione sulla qualita'.
Analisi completa e ordine dei passi: `Sviluppo/PROBLEMI-APERTI.md` §6-bis.

**Requisiti di design per D13** (emersi il 19/09/2026):

1. **Architettura a tre livelli**: Docling = *estrazione* (testo, tabelle, immagini, OCR) · Cognee = *grafo + ricerca* · gate = *permessi*. Non si sostituiscono, si completano. Cognee legge i formati nativi (PDF, DOCX, …): niente obbligo di convertire tutto in Markdown; l'OCR di Docling serve solo per scansioni/foto.
2. **Immagini fuori da Cognee**: Docling estrae le immagini → volume dedicato con ID stabile. Il VLM (qwen multimodale) genera una **descrizione** che entra **nel testo** di Cognee con un **riferimento esplicito** `[immagine:id]`. In risposta, l'orchestratore risolve `id → immagine`, la passa al VLM, e il modello sa di doverla integrare perché il riferimento sta nel testo recuperato. Il VLM fa due lavori: (a) descrivere le immagini all'ingestion, (b) rispondere integrandole.
3. **Archi concettuali trasversali** (fra documenti di aree diverse) — **POSTICIPATI**, non esclusi (corretto il 25/09/2026). Il 19/09 erano stati chiusi perché un arco trasversale con ACL sui singoli archi richiederebbe fork/ristrutturazione del sorgente, costo da settimane + revisione di sicurezza esterna, e il rischio di un bug è fuga di dati (il costo peggiore). La direzione che evita il problema è il **grafo per combinazione di aree** (vedi sopra): niente ACL per arco, ogni grafo è consentito a chi lo usa. Si riprende dopo il problema dei cataloghi; caso d'uso vero l'area legale.
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
| **D17** | **L'orchestratore diventa un agente?** Oggi è una catena fissa: token → embedding → ricerca con ACL nella query → gate → prompt → modello. Un agente deciderebbe da sé quali strumenti chiamare e quante volte | Catena fissa, come adesso · **agente che decide solo la ricerca** (quante interrogazioni, con che parole, quando fermarsi), con recupero e permessi che restano codice · agente pieno, con i permessi fra i suoi strumenti | **Agente pieno con LangGraph** (framework MIT, non un ciclo custom), strumenti = le funzioni esistenti di `recupero.py` (`cerca`, `cerca_esatta`, `pagina`, `immagini_pertinenti`, `documenti_visibili`) esposte come tool, guardrail «l'agente decide COSA cercare, mai COSA può vedere». Cervello: 27B locale (`qwen3.8-27b-gsq-rco`) con escalation a un'API a pagamento solo su fallimento dimostrato. **Deciso il 24/09/2026** — vedi `SINTESI-SESSIONE-24-09-2026.md` | Niente oggi. Serve per le domande a più passi: «confronta i prezzi di EUROSAND e FLEURAMI» oggi fa una ricerca sola |
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

| **D19** | **I vincoli di attributo diventano un ramo del recupero** (colore, misura, formato, prezzo). Oggi un «10 articoli viola» risponde inventando: "viola" si diluisce nel vettore della frase intera e il ramo lessicale la zittisce di proposito (parola comune). Misurato il 23/09/2026 su traces 235: 0 pezzi viola su 8 nel contesto, e il modello elenca 10 viola | Flusso di progetto (schema qui sotto) · solo ri-pesare le parole (bge-m3 sparse) · niente | **Flusso di progetto**: estrarre il vincolo dalla domanda, tradurlo nei valori che il corpus usa davvero, must-match testuale sulla co-occorrenza, barriera di coerenza | Risposte con attributi inventati (classe «fallimento progetto»: il sistema mette in errore l'utente) |
| **D23** | **Cosa si fa coi cataloghi** (corregge `VALUTAZIONE-ORCHESTRATORE-AGENTE.md` §9) | Estrarre la struttura (prodotto → codice → prezzo) · **indicare i punti** che soddisfano la ricerca (pagine e figure, "nastri blu" → "guarda qui, qui e qui") | **Indicare i punti**: i cataloghi si trattano come un testo o un manuale, non come un database. I codici vengono dall'**anagrafica articoli** (ERP, `SPECIFICA-CONNETTORI.md`), mai dal catalogo | Definisce il successo: una risposta "guarda qui" vale più di un codice estratto male |

**Requisiti di design per D19** (emersi il 23/09/2026, misurati):

1. **Il fallimento è nel recupero, non nel modello.** Da traces 235: il vettore
   della frase intera diluisce «viola» (i natalizi viola, pag. 219/221 INGE,
   non entrano nei 150 candidati); il ramo lessicale tace per costruzione
   («viola» in 74 pezzi su 3112 ≫ soglia 1/1000); finale 0/8 pezzi viola nel
   contesto. Un «catalogo viola» verità-di-sproloquio: il dato c'è, il recupero
   non lo vede.
2. **Niente tabella di sinonimi fisica, niente DB di articoli.** Non avremo mai
   una tabella con tutti gli attributi: cerchiamo un catalogo, non un gestionale.
   Le misure han mostrato che la mappa la fa il modello:
   * bge-m3 (il nostro embedding) è multilingue: «viola» sta vicino a
     `violet` 0.723, `lila` 0.585, `purpur` 0.513, e il primo colore *diverso*
     (`rot`) arriva a 0.442. I sinonimi vengono fuori da soli, al primo posto.
   * Ma le soglie pulite non esistono: `rot~grün` fa 0.54, più di `viola~purpur`
     0.51. I colori si raggruppano ma non si separano → il confronto è un
     **ranking** sui primi N vicini, mai una soglia.
   * Sulla **domanda intera** il ranking si rompe: `lila` scende sotto `schwarz`
     e `weiss` (0.417 contro 0.428/0.410) e `purpur` frana al 7° posto.
     Conclusione: **l'estrazione del vincolo dalla frase è obbligatoria**, il
     confronto col corpus va fatto sulla parola sola, mai sulla frase intera.
3. **Il flusso di progetto** (dalla domanda alla risposta):
   * **estrazione** (nuova, davanti a `recupero`): il modello scompone
     intent/vincoli. Provato il 23/09/2026 su 8 domande reali: colore=viola da
     «violaceo», formato=60 cm, prezzo<10, quantità ridondanti assorbite;
     «sassi rossi» → vincoli vuoti (il colore resta nell'intent, degradazione
     sicura verso il comportamento di oggi). Il modello usato è `ragionamento`
     (l'argomento sarebbe `veloce`, non configurato).
   * **traduzione** (nuova): il valore del vincolo si proietta sui valori che
     il corpus usa davvero (`viola` → {lila, violet, purpur}) cercando gli
     embedding dei valori estratti dai chunk, per ranking sui primi N.
   * **must-match** (nuova nel recupero): i pezzi che **contengono** almeno un
     sinonimo (`content ~* 'lila|violet|purpur'`) — co-occorrenza testuale, un
     AND sui fatti, non una somiglianza. Il vincolo è ammissibilità, non voto.
   * **barriera di coerenza** (nuova, dopo il rerank): se 0 pezzi soddisfano il
     vincolo, si risponde «non ci sono articoli viola nei documenti» **senza
     chiamare il modello**. È il punto che toglie l'invenzione dall'engramma:
     diventa un esito del flusso, non un comportamento.
   * **prompt**: una regola in più, complementare alla barriera — «rispondi solo
     di articoli che il contesto indica con quel valore».
4. **Perimetro classe.** `colore` è il primo attributo, ma lo schema regge
   misura/formato/prezzo senza toccare l'architettura: cambia solo il modo di
   estrarre i valori dal corpus (cella `colore`, cella `misura`…). Quando non
   c'è vincolo, il flusso è esattamente quello di oggi: zero regressione.
5. **Confine del disegno (23/09/2026)**: questo flusso è per la **ricerca
   documentale sui cataloghi**, dove l'attributo non esiste come dato (è solo
   una parola nel testo) e il must-match testuale è l'unica via. Quando si
   affronterà la **ricerca degli articoli sul DB gestionale**, lì gli attributi
   **ci sono come dati** (colonne colore, misura, formato, prezzo): i vincoli
   torneranno filtri strutturati sulla colonna vera (es. pattern Qdrant
   `Filter(must=[...])`), con un filtro diretto invece del must-match testuale.
   L'estrazione del vincolo (Passo 2) resta comune ai due mondi; il disegno di
   oggi deve restare compatibile con quel futuro senza condizionarlo — si tiene
   aperta l'opzione strutturata senza renderla prerequisito del flusso
   documentale.
6. **Da fare dopo il consenso su D19**: (a) estrazione dei valori dal corpus
   e loro embedding; (b) estensione di `recupero.py` col ramo must-match;
   (c) barriera di coerenza; (d) regola nel prompt; (e) replay del caso «10
   articoli viola» come verifica, poi le domande critiche di
   `Sviluppo/eval/DOMANDE-CRITICHE.md`.

**Prove collegate** (riproducibili): `replica*.py`, `sinonimi.py`,
`diluisce.py`, `estrazione.py` in `C:\Users\uvolp\AppData\Local\Temp\opencode\`,
copiati in `/tmp` del container orchestratore.

**Implementazione e verifica (23/09/2026) — stato D19**:

Implementato. `vincoli.py` (nuovo), `recupero.cerca(... vincolo=)` e
barriera in `main.py` (blocco 3-5). Il flusso è quello del disegno, con due
aggiustamenti emersi in verifica:

1. **Confini di parola nel must-match** (`regex()` usa `\m…\M`, PostgreSQL).
   Senza, `~* 'viola'` matcha anche `violazione` nelle policy di sicurezza e
   la barriera non scatta mai (misurato: 27 chunk veri per `viola\b`, prima
   migliaia). E' un intervento sul flusso, non un cerotto: i confini di parola
   sono la condizione necessaria del must-match per qualunque attributo.
2. **Istruzione per i numeri**: i valori numerici (misura, prezzo) vanno
   espressi con l'unità («60 cm», «2 €»), mai la sola cifra («2» matca in
   4246/10594 chunk, ~40% dell'indice).

Verifica su 16 domande inventate + probe a colpo su DB (utente direzione/
acquisti, aziende luis+decobrands, vedi REPORT-D19-VERIFICA.md):
- Il caso che ha scoperto D19 passa da 0/8 a 8/8 pezzi con termini viola.
- La barriera scatta davvero sui colori assenti (ciclamino/fuksia/ecru/
  salmone: 0 chunk nel corpus) e interrompe il turno senza chiamare il
  modello.
- Limiti trovati (tutti dentro l'architettura, nessuno richiede di rifare il
  flusso): (1) il prezzo è una soglia numerica, non una stringa — la barriera
  può nascondere dati scritti come «1,80 €»; (2) le domande di identità
  (codice articolo) possono far allucinare all'estrazione vincoli fasulli che
  degradano la pool o producono barriera falsa; (3) l'espansione sinonimi può
  essere troppo larga (magenta → rosa/pink diluisce); (4) pagine-palette che
  citano ogni colore bypassano la barriera («turchese» → FARBCODES).
- Prossimo passo consigliato: decide su prezzo (1) — accettare il limite o
  aggiungere l'intervallo numerico al must-match; è l'unico che può produrre
  una risposta silenziosamente sbagliata.

**Bug grave prezzo risolto (23/09/2026)**: «decorazioni natalizie sotto i
2 euro» → barriera falsa «non esistono articoli con prezzo 2» quando i prezzi
1,00–1,95 esistono (EUROSAND; i cataloghi scrivono «€ 1,85», simbolo prima
del numero, e «2 €» senza simbolo davanti non matcha). Falso negativo
sistematico su **qualunque** soglia numerica → classe «fallimento progetto»,
non cerotto. Fix strutturale in `vincoli.py` (`_intervalli_numerici`,
chiamata da `regex()`): per `attributo == "prezzo"` il must-match aggiunge
l'intervallo reale sotto la soglia in entrambe le grafie («1,85» e «1.85»),
con soglia 0 < valore ≤ 500 (oltre non è un catalogo decorazioni: il
confronto numerico vero è il confine col DB gestionale).

- Genera `\m(?:0|1|…|n-1)[,.]\d{2}\M` per i valori interi sotto la soglia
  («sotto i 2» → 0,e 1,xx), e per le soglie decimali («sotto 1,50») limita i
  decimi della parte intera uguale («1,00–1,49», mai «1,50»).
- Solo `prezzo` è una soglia per costruzione («sotto», «meno di»): la misura
  è un valore esatto («da 60 cm», verificato 8/8) — generare «tutto sotto
  60» inonderebbe la pool. È la classe del vincolo, non un cerotto.
- Test: `_prova()` ampliato (soglia intera, decimale, 0,99, codice «2040»
  che deve restare senza intervalli), `python3 -m orchestratore.vincoli`,
  e verifica end-to-end su 20 turni: il caso passava da barriera falsa a
  risposta vera («81316 G002 € 1,99», «81317 G002 € 1,99», EUROSAND),
  nessuna regressione su altri 19 turni (barriere oneste, identità DST2040,
  misure, colori multilingua).

Restano aperti i limiti (2)–(4) e il limite (1) ora coperto solo per il
prezzo: (2) i vincoli fasulli dell'estrazione sulle domande di identità
(es. `misura=2040` da DST2040) non sono corretti ma la risposta risulta
giusta; (3) l'OR fra più valori dello stesso attributo diluisce(«argento e
oro» risponde solo sul pezzo oro); (4) i valori qualitativi come misura
(«grandi») restano senza corrispondenza testuale ma la risposta è corretta
perché il vettore recupera i pezzi giusti.

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
| **A9** | **Libreria personale di note** — documenti per persona che entrano nell'indice (cercabili) ma non si condividono; strumento `nota` dell'agente | Io (agente) | ACL a livello utente (`sub` dal token, oggi c'è solo gruppo/azienda); note embeddate al volo dall'orchestratore, filtro `proprietario` nella CTE `consentite`. Progettata in sessione 25/09, rinviata per fare prima la memoria agentica |

---

## 4. Decise (da registrare in §12 e poi cancellare)

| Data | Decisione | Esito |
|---|---|---|
| 18/09/2026 | Credenziali dei gestionali | **Nel database, cifrate** (chiave in `.env`, sola scrittura) — parte della decisione 70 |
| 18/09/2026 | Chi crea l'utente di sola lettura su Integra | **I sistemisti proprietari del sistema** → azione A2 |
| 18/09/2026 | Il server raggiunge Integra (192.168.1.41)? | **Sì, con la VPN attiva** → domanda S5 per la produzione |
