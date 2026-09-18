# Specifica prototipo amministrazione — per open design

**Input per lo strumento di prototyping.** Produce da questa specifica un
**prototipo HTML statico navigabile** dell'amministrazione aziendale, con
logica simulata, palette multiple (chiaro/scuro) e dati di esempio realistici.

**File compagni (leggere prima di partire):**
- `Prototipi/tokens.css` — token di ruolo esistenti da estendere, non sostituire;
- `documenti_test/ESEMPI-FILE-DOCUMENTI.md` §1 — dati di esempio (aziende, utenti, fonti, anomalie).
- `Prototipi/login-sso-keycloak.html` — la pagina di login esistente, per la
  coerenza visiva (stessi token, stesso font).

**Cosa NON fare (regole d'uscita):**
- niente attributi di servizio del tool di design nell'HTML consegnato (es. `data-od-id`);
- niente colori esadecimali scritti a mano nei componenti: ogni colore da token di ruolo;
- niente testi di prova ("Lorem ipsum", "Ripeti la demo") nelle schermate finali;
- niente funzioni che non esistono: le azioni di seconda fase si mostrano come tali;
- niente build, framework o runtime: HTML+CSS+JS vanilla, funzionante aprendo il file.

---

## 1. Cosa è questo prodotto

Amministrazione dell'assistente AI aziendale (RAG). Schermate per gestire
**fonti dati, anomalie, registro modifiche e aspetto**, con ruoli distinti per
area. Parte dopo la pagina di login Keycloak esistente e la **schermata di
scelta** (Chat / Amministrazione). Gli **operatori** (ruolo non di
amministrazione) non vedono mai la scelta: dal login vanno dritti in chat.

Impostazione di tono: **strumento di lavoro, non marketing**. Tabelle dense,
prima il contenuto, zero decorazione. Deve sembrare lo stesso prodotto del
login e della chat.

## 2. Due schermate fuori dall'amministrazione (da prototipare per coerenza)

1. **Login** — esiste già (`Prototipi/login-sso-keycloak.html`, tema `azienda`).
2. **Schermata di scelta** (dopo il login):
   - logo e nome azienda (come il login), saluto con il nome della persona;
   - due scelte grandi: **Chat** — "Fai domande sui dati e sui documenti
     aziendali"; **Amministrazione** — "Fonti, anomalie, accessi, importazioni",
     con **segnale** se ci sono anomalie critiche nelle sue aree (es. pallino
     "3 critiche");
   - "Ricorda la mia scelta" (la volta dopo va dritto lì, cambiabile
     dall'intestazione dell'amministrazione); esci;
   - layout che regge una terza voce futura ("Report") senza essere ridisegnato.

## 3. Identità visiva e token

- Font **Inter** (come il login). Desktop first; tablet e telefono OK per scelta,
  panoramica e **presa in carico anomalie**.
- Chiaro e scuro (segue `prefers-color-scheme`), come la pagina di login.
- Stessi token di `tema-azienda.css`/`Prototipi/tokens.css`, **estesi** con i
  token semantici di stato e residenza:

| Token | Uso |
|---|---|
| `--stato-critico` / `--stato-critico-sfondo` | anomalie critiche, errori bloccanti |
| `--stato-errore` / `--stato-errore-sfondo` | errori |
| `--stato-attenzione` / `--stato-attenzione-sfondo` | avvisi, degradato |
| `--stato-ok` / `--stato-ok-sfondo` | riuscito, attivo, sano |
| `--stato-info` / `--stato-info-sfondo` | informativo, in corso |
| `--stato-neutro` / `--stato-neutro-sfondo` | ignorato, disattivato, sconosciuto |
| `--residenza-interno` / `--residenza-cloud` | i due valori di residenza delle fonti |

**Regole palette (vincolo principale):**
1. solo token di ruolo, mai colori scritti a mano nei componenti;
2. il colore del marchio **non ha significato**: gravità/stati usano token
   semantici separati (marchio rosso ≠ errore);
3. il significato non dipende solo dal colore: gravità e stati hanno sempre
   **icona + testo**;
4. contrasto per costruzione: ogni superficie d'azione ha il token del testo
   che ci sta sopra (`--azione`/`--azione-testo`); minimo WCAG AA;
5. chiaro e scuro per ogni palette;
6. pochi token configurabili (4–6), il resto derivato (`--azione-hover`
   = marchio scurito/schiarito via `color-mix()`).

**Palette di riferimento (anche i valori in allegato):**

| Palette | Marchio | Perché |
|---|---|---|
| Decobrands | `#0d6efd` blu | la palette reale di oggi |
| Verdi | `#2e7d32` | il marchio non deve confondersi con lo stato ok |
| Rossa | `#c62828` | il caso peggiore: il marchio non deve sembrare un errore |

Ciascuna in chiaro e in scuro.

## 4. Menù (filtrato per ruolo)

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
Strumenti                          ← collegamenti esterni, stessa login
  Gestione utenti ↗                (Keycloak)
  Gestione importazioni ↗          (Dagster)
  Monitoraggio sistemi ↗           (Uptime Kuma)
```

I collegamenti esterni si riconoscono (icona di uscita, nome del prodotto a
sottotitolo) e aprono una nuova scheda; puntano a una pagina segnaposto
("Qui si apre Gestione utenti").

**Intestazione:** logo e nome azienda, scelta ricordata (Chat ⇄ Amministrazione),
profilo, esci. **Piè di pagina:** nome assistente, condizioni d'uso.

## 5. Ruoli e permessi

Colonne: `accessi` · `fonti` · `importazioni` · `anomalie` · `sistemi` · `ruoli` · `revisore`.
`M` = modifica · `L` = sola lettura · `—` = voce di menu assente · `↗` = collegamento esterno.

| Schermata | accessi | fonti | importazioni | anomalie | sistemi | ruoli | revisore |
|---|---|---|---|---|---|---|---|
| Panoramica | L | L | L | L | L | L | L |
| Fonti | L | M | L | L | — | — | L |
| Vedi come | M¹ | M¹ | — | — | — | M¹ | M¹ |
| Anomalie | L² | L² | L² | M | L | — | L |
| Registro modifiche | L³ | L³ | L³ | L³ | L³ | L | L |
| Aspetto | — | — | — | — | M | — | L |
| ↗ Gestione utenti | ↗ | — | — | — | — | ↗ | ↗ |
| ↗ Gestione importazioni | — | — | ↗⁴ | — | — | — | ↗⁴ |
| ↗ Monitoraggio sistemi | — | — | — | — | ↗⁴ | — | ↗⁴ |

¹ Vedi come è una lettura ma rivela i permessi altrui: la consultazione viene registrata.
² Solo le anomalie della propria area. ³ Solo le modifiche della propria area.
⁴ Solo se abilitato a tutte le aziende.

Il prototipo deve **permettere di cambiare ruolo** (selettore solo del
prototipo) e vedere menu, collegamenti e sola lettura cambiare. Includere il
caso **"amministratore limitato alla sola Luis"**: menu identici, ma nessun
dato di Decobrands.

## 6. Pattern ricorrenti (una sola volta, riusati ovunque)

- **Tabella** con ricerca, filtri, ordinamento, paginazione; righe dense;
  filtri attivi visibili e rimovibili; contatore risultati;
- **Pannello di dettaglio** che si apre senza perdere la lista (laterale o
  pagina, a scelta);
- **Badge di stato**: icona + testo + colore semantico;
- **Conferma di azioni rilevanti**: dice cosa succede, non "Sei sicuro?";
- **Motivo obbligatorio** (in campo di testo nella conferma) per: ignora
  anomalia, rendi una fonte utilizzabile da servizi esterni, sospendi una
  fonte; finisce nel registro modifiche;
- **Sola lettura per ruolo**: controlli visibili ma disattivati, con il perché
  ("Richiede il ruolo Gestione fonti");
- **Notifica transitoria** dopo un salvataggio ("Salvato", "Anomalia presa in carico");
- **Data relativa + assoluta**: "12 min fa" con la data completa al mouse sopra;
- **Chip azienda** su ogni dato aziendale (con più aziende va sempre mostrato);
- **Collegamento esterno**: icona di uscita, nuova scheda.

## 7. Stati di ogni schermata

Ogni schermata va disegnata anche in: **caricamento**, **vuoto** (con il
perché e cosa fare, es. Fonti: "Nessuna fonte registrata. Le fonti le aggiunge
chi installa il sistema."), **errore** (il servizio non risponde: cosa è
successo e se i dati mostrati sono vecchi), **sola lettura**, **filtro senza
risultati**.

## 8. Schermate (dettaglio)

### 8.1 Panoramica
"In dieci secondi: va tutto bene? se no, dove guardo?". Unico posto dove un
admin limitato a un'azienda vede importazioni e stato sistemi. Blocchi
(visibili solo se la persona ha il ruolo relativo):

| Blocco | Mostra | Porta a |
|---|---|---|
| Anomalie | aperte per gravità; nuove nelle ultime 24 h; le 3 più gravi | Anomalie |
| Importazioni | griglia azienda × tipo di dato | Dagster ↗ (se abilitato) |
| Stato sistemi | semaforo complessivo + sistemi non verdi | Uptime Kuma ↗ |
| Fonti | in attesa di approvazione; senza responsabile | Fonti |
| Accessi | utenti senza azienda abilitata | Gestione utenti ↗ |
| Ultime modifiche | le 5 più recenti | Registro modifiche |

**Griglia importazioni** (sola lettura, dati dal database filtrati per azienda):

| | Clienti | Articoli | Listini | Doc vendita | Doc acquisto | Scadenze | Giacenze |
|---|---|---|---|---|---|---|---|
| **Luis** | ✔ 12 min fa | ✔ 12 min fa | ⚠ 2 h fa (in ritardo) | ✖ fallita | — non disponibile | ✔ ieri | ✔ 06:00 |
| **Decobrands** | ✔ | ✔ | ✔ | ✔ | ✔ | — | — |

"Non disponibile" = il gestionale non fornisce quel dato: **stato normale**.
Una cella fallita porta all'anomalia collegata. Stati: tutto verde (deve
sembrare tranquillo, non vuoto); situazione critica (deve saltare all'occhio).

### 8.2 Fonti
Registro delle fonti: documenti (cartelle/manuali/cataloghi) o dati gestionali.
**Elenco** — colonne: Nome · Tipo · Provenienza · Chi può vederla · Aziende ·
Uso di servizi esterni · Responsabile · Contenuto · Aggiornata · Stato.
Filtri: tipo, gruppo, azienda, uso esterni, stato, "senza responsabile".

**"Uso di servizi esterni" è il campo più delicato**: valori **Resta in
azienda** (default) / **Può usare servizi esterni**; riconoscibile a colpo
d'occhio ovunque (token `--residenza-interno`/`--residenza-cloud`, icona e test).

**Dettaglio/modifica:** nome, descrizione, tipo, percorso (sola lettura: lo
impone chi installa); provenienza (canale tipo cartella collegata, percorso,
esito ultima lettura); **documenti della fonte** con stato per ciascuno —
*indicizzato*, *illeggibile* (collegato all'anomalia), *escluso* (con motivo);
azioni: rendi utilizzabile da servizi esterni (conferma + motivo), sospendi
(conferma + motivo); premessa: il valore predefinito è sempre "Resta in azienda".

### 8.3 Vedi come
**Scopo:** "cosa vede Mario?", in anteprima configurabile per ruolo/profilo e
per domanda d'esempio (quali fonti risponde, quali dati mostra, cosa nasconde).
Consultazione registrata (nota: svela i permessi di un'altra persona).

### 8.4 Anomalie
**Elenco** — gravità, sistema, titolo, azienda, data, stato. Filtri per
gravità, stato, sistema, azienda. La colonna gravità ha icona + testo.
**Dettaglio** — descrizione, della fonte collegata quando c'è, suggerimenti,
collegamenti esterni (es. al sistema che la genera); azioni: **prendi in
carico**, **ignora** (conferma + motivo obbligatorio), **chiudi**. Chiusura
automatica segnalata quando prevista.

**Dati del prototipo** (da `documenti_test/ESEMPI-FILE-DOCUMENTI.md` §1.5 — usare quelli):

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

### 8.5 Registro modifiche
Chi ha cambiato cosa, quando e perché (per verifiche, DPO, "da quando Mario
non vede più il listino?"). Colonne: Quando · Chi · Area · Azione · Oggetto ·
Azienda · Motivo (se richiesto). Dettaglio **prima / dopo** affiancato con le
differenze evidenziate. Filtri: periodo, persona, area, azienda, oggetto.
**Esporta CSV**. Le modifiche ad accessi restano in Keycloak: collegamento
"Modifiche agli accessi ↗".

### 8.6 Aspetto
(schermata di **seconda fase**, va comunque prototipata: definisce quali token
sono configurabili e come si derivano gli altri)
Logo (caricamento, anteprima chiaro/scuro) e icona browser; nome azienda e
assistente, messaggio di benvenuto, piè di pagina, condizioni d'uso; palette
(4–6 colori, selettore colore + campo esadecimale); **anteprima dal vivo** in
chiaro e scuro di login, scelta e un pezzo di amministrazione; **verifica
contrasto automatica** (avviso e proposta correzione se un colore non garantisce
leggibilità); ripristina predefiniti; salva.

## 9. Selettori SOLO del prototipo

Separati in modo riconoscibile dall'interfaccia vera:
1. **Palette** × chiaro/scuro: Decobrands, Verde, Rossa (barra di controllo);
2. **Ruolo/profilo** (cambiare ruolo dal vivo, compreso "admin limitato alla
   sola Luis");
3. **Stato della schermata**: normale · vuoto · errore · caricamento · sola lettura · filtro senza risultati.

## 10. Dati di esempio (riferimento rapido)

Coerenti con `documenti_test/ESEMPI-FILE-DOCUMENTI.md` §1 (per gli elenchi
completi usare quel file: 30 utenti, 20 fonti):
- **Aziende:** Luis S.r.l. · Decobrands S.r.l.
- **Persone chiave:** m.rossi (Vendite, accessi) — l'utente con 23 accessi
  falliti; l.bianchi (Amministrazione, ruoli/full admin) — autore di modifiche
  nel registro.
- **Fonti da mostrare almeno:** Manuali tecnici (1.240 doc · 18.300 pagine,
  Attiva), Listini di vendita (Integra — Luis), Cataloghi fornitori (3 PDF
  illeggibili → anomalia collegata), Schede prodotto Decobrands (unica attiva
  con "Può usare servizi esterni"), Contratti in corso (Sospesa).
- Numeri e totali coerenti tra Panoramica, Anomalie e dettagli.

## 11. Consegna

1. Prototipo **HTML statico navigabile**: navigazione, pannelli, filtri,
   dialoghi di conferma, anteprima palette e cambio ruolo funzionanti in modo
   simulato; nessuna logica reale.
2. Selettori SOLO del prototipo (§9), separati dall'interfaccia vera.
3. **File dei token** (CSS esteso da `Prototipi/tokens.css`) con indicati i
   token **configurabili** e i **derivati** con la regola di derivazione.
4. **Componenti**: tabella, filtri, badge di stato, pannello di dettaglio,
   conferma con motivo, sola lettura, notifica transitoria, chip azienda,
   semaforo, collegamento esterno.
5. **Accesso rapido** alla revisione: un file `README-di-consegna.md` con le
   scelte di design fatte (pannello laterale vs pagina, naming dei token
   semantici, derivazioni) e i punti aperti.