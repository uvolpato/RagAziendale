# Specifica interfacce — Portale e Amministrazione

**Per:** design (prototipo navigabile)
**Stato:** da prototipare — 18/09/2026 (rev. 2: soluzione mista, decisione 60)
**Contesto tecnico:** `PROGETTO-RAG-Aziendale.md` §15 e decisioni 55–60, `MODELLO-DATI-GESTIONALE.md`

Questo documento descrive **cosa** deve mostrare e permettere ogni schermata,
**chi** la vede e **in quali stati** può trovarsi. Il **come** — layout,
gerarchia visiva, componenti — è compito del design. Dove il documento indica
un componente ("tabella", "pannello laterale") è un suggerimento, non un
vincolo, salvo nella sezione 2.

---

## 1. Il prodotto in due righe

Un assistente aziendale con chat. Gli **operatori**, dopo il login, entrano
direttamente nella chat e non vedono nulla di questo documento. Gli
**amministratori** dopo il login scelgono fra **Chat** e **Amministrazione**.

Chi la usa: responsabile IT, responsabile dati o controllo di gestione, DPO o
revisore. Persone competenti ma **non sviluppatori**: niente gergo tecnico
nell'interfaccia (vedi §2.6).

### 1.1 Cosa è nostro e cosa no

L'amministrazione è **mista**: dove esiste uno strumento open source affidabile
si usa quello; si costruisce solo ciò che esiste unicamente in questo progetto.

| Area | Con cosa | Nel prototipo |
|---|---|---|
| Utenti, gruppi, ruoli e profili di amministrazione, delega per azienda | **Keycloak** — console di amministrazione | **No**: solo collegamento (§5.1) |
| Importazioni dai gestionali (esecuzioni, frequenze, avvio, storico, controlli di qualità) | **Dagster** | **No**: collegamento + riepilogo in Panoramica |
| Stato dei sistemi e notifiche di servizio | **Uptime Kuma** | **No**: collegamento + riepilogo in Panoramica |
| **Schermata di scelta** | nostro | **Sì** — §4 |
| **Panoramica** | nostro | **Sì** — §6 |
| **Fonti** (chi vede cosa, residenza dei dati, aziende) | nostro | **Sì** — §7 |
| **Vedi come** | nostro | **Sì** — §8 |
| **Anomalie** da tutti i sistemi | nostro | **Sì** — §9 |
| **Registro modifiche** | nostro | **Sì** — §10 |
| **Aspetto** (palette, logo, testi) | nostro | **Sì** — §11 |

Tutti gli strumenti usano **lo stesso login** (Keycloak): passando dalla nostra
amministrazione a Dagster o a Uptime Kuma non si reinseriscono le credenziali.
Gli strumenti esterni **hanno il loro aspetto**: la palette configurabile vale
per le nostre schermate, non per loro. È un compromesso accettato.

---

## 2. Vincoli di design

### 2.1 Palette configurabili ⚠ vincolo principale

Il prodotto viene installato presso **aziende diverse**, ognuna con i propri
colori. La palette **non è fissa**: il design deve funzionare con qualsiasi
colore di marchio.

**Regole:**

1. **Solo token di ruolo, mai colori scritti a mano.** Ogni colore
   dell'interfaccia viene da una variabile CSS di ruolo (`--azione`,
   `--superficie`, `--testo`...). Nessun `#0d6efd` nei componenti: cambiare
   palette significa cambiare i token, non i componenti.
2. **Il colore del marchio non ha significato.** Gravità e stati (errore,
   attenzione, ok, informazione) hanno **token semantici propri**, separati dal
   colore del marchio. Se un cliente ha il marchio rosso, i pulsanti principali
   non devono sembrare errori, e un'anomalia critica deve restare
   riconoscibile.
3. **Il significato non dipende solo dal colore.** Gravità e stati hanno
   sempre anche **icona e testo** (daltonismo, stampa, palette sfortunate).
4. **Contrasto garantito per costruzione.** Per ogni colore di sfondo usato
   come superficie di un'azione esiste il token del testo che ci va sopra
   (`--azione` + `--azione-testo`). Minimo WCAG AA (4,5:1 per il testo
   normale).
5. **Chiaro e scuro.** Ogni palette ha le due varianti e segue
   `prefers-color-scheme`, come la pagina di login.
6. **Pochi token.** Un cliente configura **4–6 colori** al massimo (marchio,
   eventualmente secondario, sfondo, superficie): tutto il resto è derivato.
   Il design indichi quali token sono **configurabili** e quali **derivati**, e
   come si derivano (es. `--azione-hover` = marchio scurito del 10%,
   `color-mix()`).

**Punto di partenza:** i token di ruolo esistenti
(`Sviluppo/branding/tema-keycloak/login/resources/css/paletta-colori.md` e
`tema-azienda.css`) e `Prototipi/tokens.css`. Vanno **estesi**, non
sostituiti: login, schermata di scelta e amministrazione condividono gli stessi
token.

**Token semantici da aggiungere** (proposta, il design può rinominarli):

| Token | Uso |
|---|---|
| `--stato-critico` / `--stato-critico-sfondo` | anomalie critiche, errori bloccanti |
| `--stato-errore` / `--stato-errore-sfondo` | errori |
| `--stato-attenzione` / `--stato-attenzione-sfondo` | avvisi, degradato |
| `--stato-ok` / `--stato-ok-sfondo` | riuscito, attivo, sano |
| `--stato-info` / `--stato-info-sfondo` | informativo, in corso |
| `--stato-neutro` / `--stato-neutro-sfondo` | ignorato, disattivato, sconosciuto |
| `--residenza-interno` / `--residenza-cloud` | i due valori di residenza delle fonti (§7.1) |

**Il prototipo deve dimostrarlo:** un selettore di palette (solo nel
prototipo) che alterna **almeno tre palette**:

| Palette | Marchio | Perché |
|---|---|---|
| Decobrands | `#0d6efd` blu | la palette reale di oggi |
| Verde | es. `#2e7d32` | vicina a "ok": verifica che il marchio non si confonda con lo stato ok |
| Rossa | es. `#c62828` | il caso peggiore: il marchio non deve sembrare un errore |

Ciascuna in chiaro e in scuro.

### 2.2 Coerenza con ciò che esiste

- La **pagina di login** (Keycloak, tema `azienda`) e la **chat** (LibreChat)
  esistono già. Schermata di scelta e amministrazione devono sembrare **lo
  stesso prodotto**.
- Font: **Inter**, come il login.
- Logo e nome dell'azienda sono configurabili (oggi: `branding/logo.png`, nome
  in `.env`).

### 2.3 Dispositivi

- **Desktop first**: l'amministrazione si usa in ufficio, con tabelle dense.
- **Tablet**: tutto consultabile e utilizzabile.
- **Telefono**: consultazione e presa in carico delle **anomalie** (una
  notifica arriva, si apre dal telefono, si prende in carico) e della
  Panoramica. Le altre schermate possono essere solo consultabili.

### 2.4 Lingue

Italiano, inglese, tedesco (come il login). Il prototipo può essere solo in
italiano, ma i layout devono reggere testi **tedeschi** più lunghi del 30–40%
(etichette, pulsanti, intestazioni di colonna).

### 2.5 Accessibilità

WCAG 2.1 AA: contrasto, navigazione completa da tastiera, focus visibile, tabelle
con intestazioni vere, stati non affidati al solo colore (§2.1.3).

### 2.6 Linguaggio

L'interfaccia parla **di business**, non di tecnologia:

| Non scrivere | Scrivere |
|---|---|
| ACL, `acl_groups` | Chi può vederla |
| residency `interno` / `cloud_ok` | Resta in azienda / Può usare servizi esterni |
| realm, client, token | — (non compaiono) |
| SCD, versione, hash | — (non compaiono) |
| sync | Importazione |
| chunk | — (si parla di documenti e pagine) |
| Keycloak, Dagster, Uptime Kuma | Gestione utenti, Gestione importazioni, Monitoraggio sistemi (il nome del prodotto può comparire piccolo, come sottotitolo del collegamento) |

---

## 3. Ruoli di amministrazione

### 3.1 Principio: ruoli separati e aggregabili

- Un **ruolo** è un permesso atomico: dà accesso a **una** area.
- Un **profilo** è un insieme di ruoli con un nome ("Responsabile IT",
  "Controllo di gestione"). Si assegna un profilo, o singoli ruoli, a una
  persona.
- Una persona può avere **più profili e più ruoli**: i permessi si sommano.
- Chi non ha nessun ruolo è un **operatore**: va dritto in chat.

Ruoli e profili **si gestiscono in Keycloak** (non serve una schermata
nostra): i ruoli sono ruoli composti, i profili sono gruppi.

### 3.2 Ruoli

| Ruolo | Nome nell'interfaccia | Cosa permette | Dove agisce |
|---|---|---|---|
| `admin-accessi` | Gestione accessi | Utenti: gruppi operativi, aziende abilitate, attivazione. **Non** assegna ruoli di amministrazione | Keycloak (delega limitata alle sue aziende) |
| `admin-fonti` | Gestione fonti | Registro delle fonti | nostra amministrazione |
| `admin-importazioni` | Gestione importazioni | Avvio, frequenze, storico delle importazioni | Dagster |
| `admin-anomalie` | Gestione anomalie | Presa in carico, assegnazione, risoluzione | nostra amministrazione |
| `admin-sistemi` | Gestione sistemi | Monitoraggio dei sistemi, aspetto | Uptime Kuma, nostra amministrazione |
| `admin-ruoli` | Gestione amministratori | Assegna ruoli e profili di amministrazione | Keycloak |
| `admin-revisore` | Revisore | **Sola lettura su tutto**, compreso il registro modifiche. Per DPO e revisori | ovunque, in lettura |

**Regole trasversali:**
- **Sola lettura di cortesia**: ogni amministratore vede **in sola lettura** le
  aree vicine alla sua (es. `admin-anomalie` vede le fonti, per capire
  un'anomalia). Vedi §3.4.
- **Nessuno si alza i permessi da solo** (garantito da Keycloak).
- **Il primo `admin-ruoli`** si crea dalla console di Keycloak, una volta sola.

### 3.3 Ambito per azienda

Ogni ruolo vale **solo per le aziende a cui l'amministratore è abilitato**. Il
responsabile IT della Luis gestisce utenti, fonti e anomalie della Luis, non
quelli di Decobrands. Un amministratore di gruppo è abilitato a tutte.

Nelle **nostre** schermate:
- il **selettore di azienda** nell'intestazione (§5.2) mostra solo le aziende
  abilitate;
- elenchi e contatori considerano solo quelle aziende;
- un oggetto che riguarda più aziende (es. una fonte comune al gruppo) è
  modificabile solo da chi è abilitato a **tutte** le aziende coinvolte: gli
  altri lo vedono in sola lettura, con la spiegazione.

**Limite degli strumenti esterni:** Dagster e Uptime Kuma **non conoscono le
aziende**. Per questo i loro collegamenti compaiono solo agli amministratori
abilitati a **tutte** le aziende. Un amministratore limitato a una sola azienda
vede lo stato delle importazioni e dei sistemi **solo nel riepilogo della
Panoramica** (§6), già filtrato per le sue aziende.

### 3.4 Matrice ruoli × schermate

`M` = modifica · `L` = sola lettura · `—` = voce di menu assente · `↗` = collegamento a strumento esterno

| Schermata | accessi | fonti | importazioni | anomalie | sistemi | ruoli | revisore |
|---|---|---|---|---|---|---|---|
| Panoramica (§6) | L | L | L | L | L | L | L |
| Fonti (§7) | L | M | L | L | — | — | L |
| Vedi come (§8) | M¹ | M¹ | — | — | — | M¹ | M¹ |
| Anomalie (§9) | L² | L² | L² | M | L | — | L |
| Registro modifiche (§10) | L³ | L³ | L³ | L³ | L³ | L | L |
| Aspetto (§11) | — | — | — | — | M | — | L |
| ↗ Gestione utenti (Keycloak) | ↗ | — | — | — | — | ↗ | ↗ |
| ↗ Gestione importazioni (Dagster) | — | — | ↗⁴ | — | — | — | ↗⁴ |
| ↗ Monitoraggio sistemi (Uptime Kuma) | — | — | — | — | ↗⁴ | — | ↗⁴ |

¹ Vedi come è una lettura, ma rivela i permessi di un'altra persona: la sua
consultazione viene registrata.
² Solo le anomalie della propria area (es. `admin-accessi` vede quelle di login
e permessi).
³ Solo le modifiche della propria area.
⁴ Solo se abilitato a tutte le aziende (§3.3).

**Il prototipo deve permettere di cambiare ruolo** (selettore solo del
prototipo) per vedere menu, collegamenti e sola lettura cambiare. Includere un
caso "amministratore limitato alla sola Luis".

---

## 4. Schermata di scelta (dopo il login)

**Chi la vede:** solo chi ha almeno un ruolo di amministrazione. Gli operatori
**non la vedono mai**: dal login vanno dritti in chat, senza pagine intermedie.

**Scopo:** scegliere dove andare. Deve essere **immediata**: due scelte, nessuna
lettura richiesta.

**Contenuto:**
- logo e nome dell'azienda (come il login);
- saluto con il nome della persona;
- due scelte grandi:
  - **Chat** — "Fai domande sui dati e sui documenti aziendali"
  - **Amministrazione** — "Fonti, anomalie, accessi, importazioni"
    - **segnale** se ci sono anomalie critiche aperte nelle sue aree (es. un
      pallino con "3 critiche"): è il motivo più probabile per entrare in
      amministrazione invece che in chat;
- "Ricorda la mia scelta" (la volta successiva va dritto lì; cambiabile
  dall'intestazione dell'amministrazione);
- esci.

**Aspetto:** stessa famiglia visiva del login, stessa card centrata o simile.
Il passaggio login → scelta deve sembrare un unico flusso.

**Stati:** normale; con anomalie critiche; con più scelte in futuro (il layout
deve reggere una terza voce, es. "Report", senza essere ridisegnato).

---

## 5. Struttura comune dell'amministrazione

### 5.1 Navigazione

Voci di menu, **filtrate per ruolo** (§3.4): una voce senza permesso non
compare.

```
Panoramica
Dati
  Fonti
  Vedi come
Controllo
  Anomalie             (contatore delle aperte)
  Registro modifiche
Impostazioni
  Aspetto
Strumenti                          ← collegamenti esterni, stesso login
  Gestione utenti ↗                (Keycloak)
  Gestione importazioni ↗          (Dagster)
  Monitoraggio sistemi ↗           (Uptime Kuma)
```

I **collegamenti esterni** devono essere riconoscibili come tali (icona di
uscita, eventuale nome del prodotto come sottotitolo) e aprirsi in una nuova
scheda: l'utente deve capire che sta passando a un altro strumento, con un
altro aspetto, e che la nostra amministrazione resta aperta.

### 5.2 Intestazione

- logo e nome dell'azienda;
- **selettore di azienda**: "Tutte le aziende" oppure una specifica. Filtra
  tutte le nostre schermate. Mostra solo le aziende abilitate (§3.3). Se
  l'amministratore ne ha una sola, il selettore diventa un'etichetta;
- **vai alla chat** (sempre visibile);
- persona: nome, ruoli (consultabili), "cambia schermata iniziale", esci.

### 5.3 Pattern ricorrenti (da definire una volta, riusati ovunque)

| Pattern | Dove | Note |
|---|---|---|
| **Tabella** con ricerca, filtri, ordinamento, paginazione | fonti, anomalie, registro | righe dense; filtri attivi visibili e rimovibili; contatore risultati |
| **Pannello di dettaglio** | anomalie, fonti | si apre senza perdere la lista (pannello laterale o pagina, a scelta del design) |
| **Badge di stato** | ovunque | icona + testo + colore semantico (§2.1) |
| **Conferma** di azioni rilevanti | uso di servizi esterni, ignora anomalia | dice cosa succede, non "Sei sicuro?" |
| **Motivo obbligatorio** | ignora anomalia, rendi una fonte utilizzabile da servizi esterni, sospendi una fonte | campo di testo nella conferma; finisce nel registro modifiche |
| **Sola lettura** | per ruolo (§3.4) | controlli visibili ma disattivati, con il perché: "Richiede il ruolo Gestione fonti" |
| **Notifica transitoria** | dopo un salvataggio | "Salvato", "Anomalia presa in carico" |
| **Data relativa + assoluta** | ovunque | "12 min fa" con la data completa al passaggio del mouse / sotto |
| **Chip azienda** | ovunque ci sia un dato aziendale | nome breve; con più aziende va sempre mostrato |
| **Collegamento esterno** | menu, Panoramica, dettaglio anomalia | icona di uscita, apre una nuova scheda |

### 5.4 Stati di ogni schermata

Ogni schermata va disegnata anche in:
- **caricamento**;
- **vuoto** — con il perché e cosa fare (es. Fonti: "Nessuna fonte registrata.
  Le fonti le aggiunge chi installa il sistema.");
- **errore** — il servizio non risponde: cosa è successo e se i dati mostrati
  sono vecchi;
- **sola lettura** (§5.3);
- **filtro senza risultati**.

---

## 6. Panoramica

**Scopo:** in dieci secondi, "va tutto bene? se no, dove guardo?". È anche
l'unico posto dove un amministratore **limitato a un'azienda** vede importazioni
e stato dei sistemi (§3.3).

**Contenuto** (ogni blocco visibile solo se la persona ha il ruolo relativo):

| Blocco | Mostra | Porta a |
|---|---|---|
| Anomalie | aperte per gravità (critiche, errori, avvisi); nuove nelle ultime 24 ore; le 3 più gravi | Anomalie (§9) |
| Importazioni | **griglia azienda × tipo di dato** (sotto) | Dagster ↗ (se abilitato), altrimenti niente |
| Stato sistemi | semaforo complessivo + i sistemi non verdi | Uptime Kuma ↗ (se abilitato) |
| Fonti | fonti in attesa di approvazione, fonti senza responsabile | Fonti (§7) |
| Accessi | utenti senza azienda abilitata (non vedono niente: probabile errore di configurazione) | Gestione utenti ↗ |
| Ultime modifiche | le 5 più recenti | Registro modifiche (§10) |

**Griglia delle importazioni** (in sola lettura; i dati vengono dal nostro
database, già filtrati per azienda):

| | Clienti | Articoli | Listini | Documenti vendita | Documenti acquisto | Scadenze | Giacenze |
|---|---|---|---|---|---|---|---|
| **Luis** | ✔ 12 min fa | ✔ 12 min fa | ⚠ 2 h fa (in ritardo) | ✖ fallita | — non disponibile | ✔ ieri | ✔ 06:00 |
| **Decobrands** | ✔ | ✔ | ✔ | ✔ | ✔ | — | — |

Ogni cella: esito dell'ultima esecuzione, quanto tempo fa, segnale di ritardo.
"Non disponibile" = il gestionale di quell'azienda non fornisce quel dato:
**stato normale**, non un errore. Una cella fallita porta all'anomalia
collegata.

**Stati:** tutto verde (deve **sembrare** tranquillo, non vuoto); situazione
critica (deve saltare all'occhio subito); amministratore limitato a una
sola azienda.

---

## 7. Fonti

Il **registro delle fonti**: ogni insieme di dati che l'assistente può usare —
una cartella di documenti (manuali, cataloghi, procedure) o un'area dei dati
gestionali (clienti, listini, ordini...).

### 7.1 Elenco

| Colonna | Esempio |
|---|---|
| Nome | Manuali tecnici · Listini di vendita |
| Tipo | Documenti / Dati gestionali |
| Provenienza | Cartella di rete `\\server\tecnico\manuali` · SharePoint «Ufficio tecnico» · Caricamento manuale · Integra — Luis |
| Chi può vederla | Vendite, Direzione |
| Aziende | Luis · Tutto il gruppo |
| Uso di servizi esterni | **Resta in azienda** / **Può usare servizi esterni** |
| Responsabile | Ufficio tecnico |
| Contenuto | 1.240 documenti · 18.300 pagine / 21.770 righe |
| Aggiornata | 3 ore fa |
| Stato | Attiva / In attesa di approvazione / Sospesa |

**"Uso di servizi esterni" è il campo più delicato dell'intera
amministrazione.** Dice se i contenuti di questa fonte possono essere elaborati
da un servizio di intelligenza artificiale **fuori dall'azienda**. Il valore
predefinito è sempre "Resta in azienda". Deve essere **riconoscibile a colpo
d'occhio** in ogni punto in cui compare (token `--residenza-*`, icona e testo).

**Filtri:** tipo, gruppo, azienda, uso di servizi esterni, stato, "senza
responsabile".

### 7.2 Dettaglio / modifica

- nome, descrizione, tipo, percorso (sola lettura: lo imposta chi installa);
- **provenienza**: da dove arrivano i contenuti. Per i documenti il canale
  principale è una **cartella collegata** (condivisione di rete, SharePoint,
  OneDrive, Google Drive...): l'ufficio responsabile continua a usarla come
  oggi, l'assistente la rilegge da solo. Mostrare il tipo di collegamento, il
  percorso e l'esito dell'ultima lettura;
- **documenti della fonte**: elenco con stato di ciascuno — *indicizzato*,
  *illeggibile* (collegato all'anomalia), *escluso* (con il motivo: cartella
  `_archivio`, tipo di file non ammesso, es. listini Excel);
- **Carica documenti** (seconda fase, solo per fonti con provenienza
  "Caricamento manuale", cioè senza una cartella): trascina i file, vedi
  l'avanzamento dell'indicizzazione, sostituisci o ritira un documento;
- **chi può vederla**: scelta di gruppi operativi (l'elenco dei gruppi viene
  da Gestione utenti). Almeno uno obbligatorio;
- **aziende**: una, più di una o "tutto il gruppo". Obbligatorio, nessun valore
  predefinito;
- **responsabile** e **versione di riferimento** ("quale versione del listino
  vale"): obbligatori per attivare la fonte;
- contenuto: numero documenti/pagine o righe, ultimo aggiornamento, errori di
  lettura (collegamento alle anomalie);
- **chi la vede davvero**: numero di persone che oggi vedono questa fonte, con
  l'elenco (utile prima di allargare o restringere);
- modifiche recenti (dal registro).

### 7.3 Flusso "Può usare servizi esterni"

Passare da "Resta in azienda" a "Può usare servizi esterni" è un'**approvazione
formale**, non un interruttore:

1. l'interfaccia spiega in chiaro la conseguenza: "I contenuti di questa fonte
   potranno essere inviati a [nome del servizio esterno] per elaborare le
   risposte.";
2. **motivo obbligatorio**;
3. conferma con **nome di chi approva e data**, che restano visibili sulla
   fonte ("Approvato da L. Bianchi il 18/09/2026");
4. il ritorno a "Resta in azienda" è invece immediato (è la direzione sicura),
   ma registrato.

Se in futuro servirà la **doppia approvazione** (chi propone ≠ chi approva),
lo stato "In attesa di approvazione" è già previsto: il design lo consideri.

---

## 8. Vedi come

**Scopo:** rispondere a "perché Mario non trova il listino?" senza entrare con
le sue credenziali. È l'unica vista che unisce utenti (da Keycloak) e fonti
(nostre): nessuno strumento esterno la offre.

**Interazione:** scegli un utente (ricerca per nome o email) → la schermata
mostra, **per quell'utente**:
- gruppi operativi e aziende abilitate (sola lettura, con collegamento a
  Gestione utenti ↗ per modificarli);
- **fonti visibili**: nome, tipo, aziende, uso di servizi esterni;
- **fonti NON visibili**, ciascuna con il **motivo** in chiaro: "non è nel
  gruppo Vendite", "non è abilitato all'azienda Decobrands";
- (seconda fase) cosa vede di una specifica tabella gestionale, es. "vede solo
  i clienti dell'agente 012".

**Avvertenze da rendere evidenti:**
- utente **senza azienda abilitata** → "Non vede nessun dato";
- utente **senza gruppi operativi** → "Vede solo le fonti aperte a tutti";
- utente **disattivato**.

La consultazione è registrata nel registro modifiche (§3.4 nota ¹): mostrarlo in
modo discreto ("Questa consultazione viene registrata").

---

## 9. Anomalie

**La schermata più importante del controllo.** Raccoglie in un solo posto i
problemi di **tutti** i sistemi: importazioni e controlli di qualità (da
Dagster), servizi non raggiungibili (da Uptime Kuma), lettura dei documenti,
modelli di intelligenza artificiale, login, permessi.

**Non è un registro di eventi.** Ogni riga è **un problema** da gestire, non una
riga di log: se lo stesso problema si ripete 200 volte, resta **una** anomalia
con un contatore di 200.

### 9.1 Elenco

| Colonna | Esempio |
|---|---|
| Gravità | Critica / Errore / Avviso / Info (icona + testo + colore) |
| Titolo | "Importazione listini Luis fallita" · "Righe clienti calate del 62%" |
| Sistema | Importazioni · Documenti · Modelli AI · Accessi · Sistemi · Qualità dati |
| Azienda | Luis (o "—" se non riguarda un'azienda) |
| Occorrenze | 1 · 47 |
| Prima / ultima volta | 2 giorni fa / 5 min fa |
| Stato | Aperta · Presa in carico · Risolta · Ignorata |
| Assegnata a | L. Bianchi |

**Vista predefinita:** aperte e prese in carico, ordinate per gravità e poi per
ultima occorrenza.

**Filtri:** gravità, sistema, azienda, stato, assegnata a me, periodo.

### 9.2 Dettaglio

- titolo, gravità, sistema, azienda, stato;
- **cosa significa e cosa fare**, in chiaro (es. "Le righe dei clienti sono
  calate del 62% rispetto all'importazione precedente. Di solito indica un
  problema del collegamento, non clienti davvero cancellati. Verifica il
  connettore prima di rileggere tutto.");
- **collegamento all'oggetto** coinvolto: la fonte (nostra), oppure
  l'esecuzione in Gestione importazioni ↗ o il servizio in Monitoraggio
  sistemi ↗ (solo se abilitato, §3.3);
- dettaglio tecnico **richiudibile** (dati grezzi, errore originale);
- **cronologia**: occorrenze (grafico semplice nel tempo), cambi di stato,
  commenti — chi e quando;
- **azioni**:
  - prendi in carico (assegna a me) / assegna a...;
  - commenta;
  - **risolvi** (nota facoltativa);
  - **ignora** (motivo obbligatorio + fino a quando: "per 7 giorni", "per
    sempre"). Un'anomalia ignorata che si ripresenta dopo la scadenza torna
    aperta.

### 9.3 Chiusura automatica

Molte anomalie **si chiudono da sole** quando il controllo torna a passare (la
importazione successiva riesce, il servizio torna raggiungibile). Vanno
distinte visivamente da quelle risolte da una persona: "Risolta
automaticamente". Se si ripresenta, torna aperta con la cronologia completa.

### 9.4 Esempi realistici per il prototipo

| Gravità | Sistema | Titolo | Azienda |
|---|---|---|---|
| Critica | Sistemi | Il servizio dei modelli AI non risponde | — |
| Critica | Qualità dati | Righe clienti calate del 62% nell'ultima importazione | Luis |
| Errore | Importazioni | Importazione listini fallita: gestionale non raggiungibile (×12) | Luis |
| Errore | Documenti | 3 PDF non leggibili nella fonte "Cataloghi fornitori" | Decobrands |
| Avviso | Qualità dati | 14 clienti con partita IVA duplicata | Luis |
| Avviso | Qualità dati | 57 articoli con prezzo di listino a zero | Decobrands |
| Avviso | Importazioni | Nuova colonna nel gestionale non prevista: `pro_liberon3` | Luis |
| Avviso | Accessi | 23 accessi falliti in 10 minuti per l'utente m.rossi | — |
| Avviso | Accessi | 4 utenti senza azienda abilitata: non vedono nessun dato | Luis |
| Avviso | Modelli AI | 5 domande rifiutate: servizio interno non disponibile per dati riservati | — |
| Info | Modelli AI | Il modello per la ricerca è cambiato: indice da ricostruire | — |

---

## 10. Registro modifiche

**Scopo:** chi ha cambiato cosa, quando e perché. Serve alle verifiche, al DPO
e a capire "da quando Mario non vede più il listino?".

Contiene le modifiche fatte **nelle nostre schermate** (fonti, anomalie,
aspetto, consultazioni di Vedi come). Le modifiche a utenti, gruppi e ruoli
restano nel registro di Keycloak: il nostro registro mostra un **collegamento**
("Modifiche agli accessi ↗").

**Elenco:**

| Colonna | Esempio |
|---|---|
| Quando | 18/09/2026 10:42 |
| Chi | L. Bianchi |
| Area | Fonti · Anomalie · Aspetto · Vedi come |
| Azione | "Ha reso la fonte Listini utilizzabile da servizi esterni" |
| Oggetto | Listini di vendita (collegamento) |
| Azienda | Luis |
| Motivo | se richiesto dall'azione |

**Dettaglio:** **prima / dopo** affiancati, con le differenze evidenziate.

**Filtri:** periodo, persona, area, azienda, oggetto.
**Esporta** (CSV) per le verifiche periodiche.

Il registro è **in sola lettura per tutti**, senza eccezioni.

---

## 11. Aspetto

**Chi:** `admin-sistemi`. Rende concreta la configurabilità della palette
(§2.1).

**Contenuto:**
- **logo** (caricamento, anteprima su chiaro e scuro) e **icona del browser**;
- **nome dell'azienda**, nome dell'assistente, messaggio di benvenuto, piè di
  pagina, testo delle condizioni d'uso;
- **palette**: solo i 4–6 colori configurabili (§2.1.6), con selettore
  colore e campo esadecimale;
- **anteprima dal vivo**, in chiaro e in scuro, di: pagina di login,
  schermata di scelta, un pezzo di amministrazione (tabella con badge di
  stato, pulsante principale);
- **verifica del contrasto automatica**: se un colore scelto non garantisce la
  leggibilità, avviso in chiaro ("Il testo sul pulsante sarà poco leggibile")
  e proposta di correzione;
- **ripristina predefiniti**; salva.

Oggi l'aspetto si configura da file (`branding/`, `.env`): questa schermata è
di **seconda fase**. Va comunque prototipata, perché definisce **quali** token
sono configurabili e **come** si derivano gli altri.

---

## 12. Fuori da questo prototipo

- **Gestione utenti, gruppi, ruoli e profili**: Keycloak;
- **gestione delle importazioni**: Dagster;
- **monitoraggio dei sistemi e relative notifiche**: Uptime Kuma;
- notifiche per le anomalie critiche (email / Teams): da decidere;
- soglie dei controlli di qualità dei dati: si configurano in Dagster;
- configurazione dei modelli di intelligenza artificiale;
- filtri di riga ("l'agente vede solo i suoi clienti"): seconda fase, entrerà in
  Vedi come (§8);
- report e cruscotti di business: non sono amministrazione.

---

## 13. Cosa consegnare

1. **Prototipo HTML navigabile**, statico: navigazione, pannelli, filtri e
   dialoghi di conferma funzionanti in modo simulato; nessuna logica reale. I
   collegamenti esterni possono puntare a una pagina segnaposto ("Qui si apre
   Gestione utenti").
2. **Selettori solo del prototipo**, chiaramente separati dall'interfaccia vera:
   - palette (almeno tre, §2.1) × chiaro/scuro;
   - ruolo / profilo, compreso "amministratore limitato alla Luis" (§3.4);
   - stato della schermata (normale, vuoto, errore, caricamento).
3. **Token**: un file di token esteso a partire da `Prototipi/tokens.css`, con
   indicato quali sono **configurabili** e come si **derivano** gli altri.
4. **Componenti**: tabella, filtri, badge di stato, pannello di dettaglio,
   conferma con motivo, sola lettura, notifica transitoria, chip azienda,
   semaforo, collegamento esterno.
5. **Dati di esempio realistici**, in italiano: aziende Luis S.r.l. e
   Decobrands; gruppi Vendite, Magazzino, Amministrazione, Direzione, Acquisti;
   una trentina di utenti; una ventina di fonti; le anomalie di §9.4.
   Materiale pronto in `ESEMPI-FILE-DOCUMENTI.md` §1.

**Da non fare** (lezioni dal prototipo del login):
- niente attributi di servizio dello strumento di design nell'HTML
  consegnato (es. `data-od-id`);
- niente colori scritti a mano nei componenti (§2.1.1);
- niente testi di prova ("Lorem ipsum", "Ripeti la demo") nelle schermate
  finali;
- il prototipo non deve fingere funzioni che non esistono: se un'azione è di
  seconda fase, va mostrata come tale.

---

## 14. Domande aperte

*(Decise il 18/09/2026: i documenti entrano da cartelle collegate; il caricamento in chat è disattivato in fase 1 — decisioni 61–64.)* (non bloccano il prototipo)

1. Notifiche per le anomalie critiche: email, Teams, entrambe?
2. Doppia approvazione per "Può usare servizi esterni" (§7.3)?
3. Gli utenti arrivano dalla directory aziendale (Active Directory / Entra) o
   sono gestiti a mano?
4. Ruolo `admin-revisore`: vede anche il contenuto delle domande fatte in chat,
   o solo la configurazione? (Implicazioni privacy: oggi la risposta proposta è
   **solo configurazione**.)
