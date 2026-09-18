# Esempi di file e documenti

Esempi pronti all'uso per tre scopi: il prototipo dell'amministrazione, la
valutazione del retrieval e la generazione di documenti. I dati sono **fittizi
ma coerenti tra loro** — stesse aziende, stesse persone, stessi codici articolo
e le stesse fonti citate dal progetto. Servono a costruire schermate, test e
template; non sostituiscono i dati veri.

**Riferimenti:** `SPECIFICA-INTERFACCE-AMMINISTRAZIONE.md` §13.5 · `PIANO-FASE-0-1.md`
0.5 · `PROGETTO-RAG-Aziendale.md` §1 e decis. 16 · `MODELLO-DATI-GESTIONALE.md`.

---

## 1. Dati di esempio per il prototipo dell'amministrazione

Richiesti da `SPECIFICA-INTERFACCE-AMMINISTRAZIONE.md` §13.5. Coerenti con le
anomalie di §9.4 e con i nomi già citati dal documento (`m.rossi` in §9.4,
`L. Bianchi` in §10).

### 1.1 Aziende

| Codice | Ragione sociale | Partita IVA | Codice gestionale |
|---|---|---|---|
| `azi_luis` | Luis S.r.l. | 01234567890 | 01 |
| `azi_decobrands` | Decobrands S.r.l. | 09876543210 | 02 |

L'asse di permesso è l'azienda (MODELLO-DATI-GESTIONALE §2.4). L'esempio
"amministratore limitato alla sola Luis" (§3.4) ha `aziende = [azi_luis]` e
menu identici ma nessun dato di Decobrands.

### 1.2 Gruppi

| Gruppo | Chi ci sta |
|---|---|
| Direzione | un amministratore "ruoli" e uno "sistemi" |
| Amministrazione | full admin, revisore |
| Vendite | admin "fonti", operatori |
| Acquisti | admin "anomalie" |
| Magazzino | operatori, admin "importazioni" |

### 1.3 Utenti (trentina)

Colonne: email · nome e cognome · gruppo · ruolo di amministrazione · aziende abilitate.

| Email | Nome e cognome | Gruppo | Ruolo amm. | Aziende |
|---|---|---|---|---|
| l.bianchi@azienda.it | Luca Bianchi | Amministrazione | ruoli (full admin) | tutte |
| m.rossi@azienda.it | Mario Rossi | Vendite | accessi | tutte |
| g.ferraro@azienda.it | Giulia Ferraro | Vendite | fonti | tutte |
| f.colombo@azienda.it | Franco Colombo | Vendite | fonti | azi_luis |
| s.ricci@azienda.it | Sara Ricci | Vendite | operatore (solo chat) | tutte |
| a.marino@azienda.it | Andrea Marino | Vendite | operatore (solo chat) | azi_luis |
| e.gallo@azienda.it | Elena Gallo | Acquisti | anomalie | tutte |
| p.conti@azienda.it | Paolo Conti | Acquisti | anomalie | azi_luis |
| d.ruggeri@azienda.it | Davide Ruggeri | Acquisti | operatore (solo chat) | tutte |
| l.moretti@azienda.it | Laura Moretti | Amministrazione | importazioni | tutte |
| n.leoni@azienda.it | Nicola Leoni | Amministrazione | anomalie | tutte |
| m.barbieri@azienda.it | Michela Barbieri | Amministrazione | revisore | tutte |
| r.fontana@azienda.it | Roberto Fontana | Direzione | sistemi | tutte |
| c.esposito@azienda.it | Chiara Esposito | Direzione | revisore | tutte |
| g.lombardi@azienda.it | Gabriele Lombardi | Direzione | accessi | tutte |
| v.marchetti@azienda.it | Valentina Marchetti | Vendite | fonti | azi_luis |
| f.basile@azienda.it | Federico Basile | Vendite | anomalie | azi_luis |
| s.pellicci@azienda.it | Simone Pellicci | Magazzino | importazioni | tutte |
| a.greco@azienda.it | Alessia Greco | Magazzino | operatore (solo chat) | azi_luis |
| l.sala@azienda.it | Luca Sala | Magazzino | operatore (solo chat) | azi_luis |
| m.costa@azienda.it | Marco Costa | Vendite | operatore (solo chat) | azi_decobrands |
| g.ruggiero@azienda.it | Gaia Ruggiero | Amministrazione | revisore | tutte |
| t.sartori@azienda.it | Tommaso Sartori | Acquisti | operatore (solo chat) | azi_luis |
| b.ferri@azienda.it | Beatrice Ferri | Vendite | accessi | azi_luis |
| o.blasi@azienda.it | Omar Blasi | Magazzino | anomalie | azi_luis |
| i.milani@azienda.it | Ilaria Milani | Vendite | revisore | tutte |
| e.rosati@azienda.it | Emanuele Rosati | Amministrazione | fonti | azi_luis |
| s.carella@azienda.it | Sofia Carella | Vendite | accessi | tutte |
| r.negri@azienda.it | Riccardo Negri | Direzione | sistemi | azi_luis |
| d.manzoni@azienda.it | Diletta Manzoni | Vendite | accessi | azi_decobrands |

Almeno 3 operatori (paradosso voluto nel prototipo: **non vedono la scelta**,
dal login vanno dritti in chat, §4).

### 1.4 Fonti (una ventina)

Colonne come da §7.1: nome · tipo · provenienza · chi la vede · aziende · uso
servizi esterni · responsabile · stato.

| Fonte | Tipo | Provenienza | Chi la vede | Aziende | Uso esterni | Responsabile | Stato |
|---|---|---|---|---|---|---|---|
| Manuali tecnici | Documenti | `\\server\tecnico\manuali` | Vendite, Direzione | tutte | Resta in azienda | Ufficio tecnico | Attiva |
| Listini di vendita | Dati gestionali | Integra — azi_luis | Vendite, Direzione | azi_luis | Resta in azienda | Contabilità | Attiva |
| Procedura qualità | Documenti | SharePoint «Qualità» | Amministrazione | tutte | Resta in azienda | Qualità | Attiva |
| Cataloghi fornitori | Documenti | SharePoint «Acquisti» | Acquisti, Vendite | azi_luis | Resta in azienda | Acquisti | Attiva |
| Schede prodotto Decobrands | Documenti | Caricamento manuale | Vendite | azi_decobrands | Può usare servizi esterni | Marketing | Attiva |
| Contratti in corso | Documenti | `\\server\legale\contratti` | Direzione | tutte | Resta in azienda | Legale | Sospesa |
| Anagrafiche clienti | Dati gestionali | Integra — azi_luis | Vendite | azi_luis | Resta in azienda | Vendite | Attiva |
| Ordini e DDT | Dati gestionali | Integra — azi_luis | Vendite, Magazzino | azi_luis | Resta in azienda | Magazzino | Attiva |
| Normative e FAD | Documenti | Cartella di rete `\\server\formazione` | tutti | tutte | Può usare servizi esterni | HR | In attesa di approvazione |
| Lettere e comunicazioni | Documenti | SharePoint «Comunicazioni» | Vendite, Direzione | azi_luis | Resta in azienda | Vendite | Attiva |
| Tabelle codici e prezzi fornitori | Dati gestionali | Integra — azi_luis | Acquisti | azi_luis | Resta in azienda | Acquisti | Attiva |
| Verbali riunioni | Documenti | Caricamento manuale | Direzione | azi_luis | Resta in azienda | Direzione | Attiva |
| Bilanci e budget | Documenti | Caricamento manuale | Direzione | azi_decobrands | Resta in azienda | Contabilità | Attiva |
| Listini vendita Decobrands | Dati gestionali | Integra — azi_decobrands | Vendite, Direzione | azi_decobrands | Può usare servizi esterni | Contabilità | Attiva |
| Manuali d'uso prodotti | Documenti | `\\server\tecnico\manuali\uso` | tutti | tutte | Può usare servizi esterni | Ufficio tecnico | Attiva |
| Reminder commerciali | Documenti | E-mail archiviata | Vendite | azi_luis | Resta in azienda | Vendite | Attiva |
| Schede di sicurezza (SDS) | Documenti | `\\server\tecnico\sds` | Magazzino | azi_luis | Resta in azienda | Qualità | Attiva |
| Distinte base | Dati gestionali | Integra — azi_luis | Ufficio tecnico | azi_luis | Resta in azienda | Ufficio tecnico | Attiva |
| Bollettini prezzi fornitori | Documenti | Caricamento manuale | Acquisti | azi_luis | Resta in azienda | Acquisti | Attiva |
| Archivio commesse | Dati gestionali | Integra — azi_decobrands | Vendite, Direzione | azi_decobrands | Può usare servizi esterni | Direzione | Attiva |

Il campo **"Uso di servizi esterni"** è il più delicato (§7.1): il valore
predefinito è sempre "Resta in azienda" e va reso riconoscibile dappertutto.

### 1.5 Anomalie (§9.4, riproposte)

| Gravità | Sistema | Titolo | Azienda |
|---|---|---|---|
| Critica | Sistemi | Il servizio dei modelli AI non risponde | — |
| Critica | Qualità dati | Righe clienti calate del 62% nell'ultima importazione | azi_luis |
| Errore | Importazioni | Importazione listini fallita: gestionale non raggiungibile (×12) | azi_luis |
| Errore | Documenti | 3 PDF non leggibili nella fonte "Cataloghi fornitori" | azi_decobrands |
| Avviso | Qualità dati | 14 clienti con partita IVA duplicata | azi_luis |
| Avviso | Qualità dati | 57 articoli con prezzo di listino a zero | azi_decobrands |
| Avviso | Importazioni | Nuova colonna nel gestionale non prevista: `pro_liberon3` | azi_luis |
| Avviso | Accessi | 23 accessi falliti in 10 minuti per l'utente m.rossi | — |
| Avviso | Accessi | 4 utenti senza azienda abilitata: non vedono nessun dato | azi_luis |
| Avviso | Modelli AI | 5 domande rifiutate: servizio interno non disponibile per dati riservati | — |
| Info | Modelli AI | Il modello per la ricerca è cambiato: indice da ricostruire | — |

---

## 2. Campione documenti per la valutazione del retrieval

Deliverable 0.5 del piano: 30-50 documenti **veri e non sensibili** — con la
regola esplicita "**i peggiori che ci sono, non i più belli**": i PDF finti non
hanno tabelle ruotate né scansioni storte, e se il campione è pulito il test
mente. Questo campione alimenta lo script di valutazione del retrieval
(recall@10/@5 con e senza reranker) e il confronto dell'embedding bge-m3 con
un riferimento cloud.

### 2.1 Ripartizione consigliata (~40 documenti)

| Categoria | Quantità | Casistiche che devono esserci dentro |
|---|---|---|
| Manuali tecnici | 6 | indice/TOC, paragrafi nidificati, tabelle lunghe, note a piè di pagina |
| Procedure operative | 6 | passi numerati, diagrammi di flusso, firme di approvazione |
| Schede e listini prodotto | 8 | **codici articolo e part number**, prezzi, colonne non allineate |
| Cataloghi fornitori (scansioni) | 6 | scansioni storte, tabelle ruotate di 90°, testi a più colonne |
| Documenti di vendita/acquisto | 6 | fatture / DDT / preventivi con totali ripetuti e righe non lineari |
| Lettere e comunicazioni | 4 | miscele cartacee digitalizzate + native, intestazioni e timbri |
| "Peggiori casi" vari | 4 | watermark, testo a 90°, note a margine, immagini senza testo |

### 2.2 Regole di raccolta

- **Veri, non sensibili**: manuali, procedure, schede tecniche, listini fittizi
  anonimizzati. Niente dati personali, contratti, buste paga, dati riservati.
- Da mettere in una cartella dedicata (es. `Sviluppo/valutazione/campione/`),
  uno per una: nome file = `CAT-<progressivo>-<descrizione>.pdf`.
- Tenere il **gradevole dentro**: se i 6 cataloghi oppure i 4 "peggiori"
  risultano tutti leggibili, il test è già inquinato.
- Le **domande valgono più del codice**: servono 20-30 domande che i colleghi
  farebbero davvero e che il campione possa risolvere.

### 2.3 Esempi di domande vere (da adattare al campione reale)

1. "Quanto costa il codice ART-0421 nel listino del 2026?"
2. "Qual è il part number del ricambio del modello TB-240?"
3. "Che tolleranza dichiara la scheda tecnica del TUBO-0209?"
4. "Da quando la procedura di reso richiede la firma del responsabile?"
5. "Quale fornitore produce la lamiera con codice interno 5671?"
6. "Il catalogo fornitore del 2024 riporta ancora il codice 9910?"
7. "Che condizioni di consegna prevede il preventivo PRE/2026/0143?"
8. "Qual è la procedura per segnalare un non conforme?"
9. "Il DDT 3210 è stato spedito franco destino o in porto franco?"
10. "Che scadenza ha la garanzia sull'articolo ELD-90X?"
11. "C'è una distinta base aggiornata per l'articolo 1001?"
12. "La lettera del 15 maggio a cui si riferisce il fornitore parla di cosa?"
13. "Che unità di misura ha l'articolo LAM-0450 e cosa pesa?"
14. "Quale tabella nel manuale stabilisce la portata massima in percentuale?"
15. "Il prezzo del 2023 dell'articolo 5671 era più alto o più basso dell'ultimo?"
16. "Quanti colli dichiara la fattura FR/2025/8871?"
17. "C'è un'eccezione alla regola dei 30 giorni nel manuale acquisti?"
18. "Che categoria merceologica ha il codice 4001 secondo il catalogo 2022?"
19. "La scheda di sicurezza SDS-2210 che rischi principali indica?"
20. "Quale documento parla della doppia firma sugli ordini sopra i 10.000 euro?"

---

## 3. Documenti generabili dall'assistente

Obiettivo di prodotto §1: "generare documenti (preventivi, offerte, lettere)".
Vincolo di §3.1: **template + dati — non far "scrivere il PDF" all'LLM**. Il
modello riempie i campi da `erp` (soggetti, articoli, listini, documenti), il
template li impagina; ogni azione con effetti esterni resta dietro conferma
umana (`pending_actions`, `interrupt()` — decisione: nessuna azione esterna
senza conferma).

### 3.1 Preventivo

Esempio strutturato sugli stessi campi di `erp.documenti` e `documenti_righe`
(MODELLO-DATI-GESTIONALE §5.10-5.11).

**Testata**

| Campo | Esempio |
|---|---|
| Serie / numero | PRE / 2026/0143 |
| Data | 18/09/2026 |
| Cliente | Ergon S.p.A. — codice cliente `C-1042` (cliente di azi_luis) |
| Agente | G. Ferraro |
| Listino | Vendita ITA |
| Pagamento | D/P 60 gg d.f.f.m. |
| Porto | franco destino |
| Spedizione | vettore DHL a carico del fornitore |
| Valuta | EUR |
| Sconti di testata | 15% + 5% (cascata) |
| Validità | 90 giorni |

**Righe**

| Riga | Articolo | Descrizione | Q.tà | Prezzo unit. | Sconto riga |
|---|---|---|---|---|---|
| 1 | ART-0421 | Modulo illuminazione LED tecnico 24/230V | 40 | 145,00 | 10% |
| 2 | TUBO-0209 | Profilo tubolare zincato 0209 | 120 | 12,40 | — |
| 3 | ELD-90X | Elettronica di controllo remote 90X | 12 | 318,00 | 5% |

Totali: imponibile, IVA 22%, spese trasporto se non incluse, totale documento.

### 3.2 Scheda prodotto

Stessa origine dati di `erp.articoli`/`listini` + `arricchimenti` (descrizione
generata, §9.3 di MODELLO-DATI-GESTIONALE):

| Campo | Esempio (ART-0421) |
|---|---|
| Codice interno | ART-0421 |
| Codici alternativi | 0421; EAN 8058470021421 |
| Descrizione breve | Modulo illuminazione LED tecnico |
| Famiglia / categoria | Illuminazione / Moduli LED |
| Unità di misura | pz |
| Peso | 0,42 kg |
| Prezzo di listino (PI/2026) | 145,00 EUR |
| Condizioni particolari | minima 10 pz |
| Note | esenti da dichiarazione (prodotto non sensibile alla residenza) |

### 3.3 Lettere

Tutte template + dati. Tre da portare al pilota:

1. **Lettere di accompagnamento** dei preventivi/ordini: intestazione,
   destinatario da `soggetti` + `indirizzi`, rinvio al numero documento.
2. **Solleciti di pagamento**: cliente + `scadenze` scadute, gradazione del
   tono per fascia di ritardo (30/60/90 gg), mai toni da "vecchia lettera".
3. **Conferme d'ordine ricevuto** via canale b2b/agente: iterazione articoli,
   date di consegna prevista, condizioni.

### 3.4 Regole trasversali

- **Residenza (decisione 16)**: i documenti commerciali sono destinati a uscire
  — ma la **residenza è una proprietà della fonte/cliente**, non del documento
  generato. Un preventivo verso un cliente che ha abilitato solo servizi interni
  si genera con materiale che "resta in azienda".
- **Conferma umana**: generazione → anteprima → invio **solo dopo conferma**
  (oggi `pending_actions`, poi `interrupt()` di fase 3). Niente azioni esterne
  automatiche.
- **Tracciabilità**: ogni documento citato in chat deve risolversi
  a documento + pagina esistenti (§8 del handoff); i generati partono dai dati
  `erp`, che hanno provenienza e storico.
- **I documenti sono dati, non istruzioni** (rischio di prompt injection da
  cataloghi di terzi): il contenuto recuperato non pilota mai una generazione.

---

*Fittizio e coerente: codici, prezzi e persone qui sono esemplificativi —
aggiornare il documento quando i dati veri diventano disponibili.*