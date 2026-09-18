# Specifica — Connettori ai gestionali e importazione dei dati

**Stato:** proposta da approvare — 18/09/2026
**Decisioni collegate:** 46–54 (replica ERP e modello canonico), 60 (Dagster per
le importazioni), 67 (filtro per azienda nel gate), 69 (un connettore per
azienda). **Propone la decisione 70** (§14).
**Documenti:** `MODELLO-DATI-GESTIONALE.md` (dove finiscono i dati),
`SPECIFICA-INTERFACCE-AMMINISTRAZIONE.md` (stile delle schermate).

Questa specifica descrive **come i dati dei gestionali arrivano nel modello
canonico**: quali pezzi esistono, chi li configura, come girano da soli, come
si proteggono le credenziali, e come si aggiunge in futuro un gestionale nuovo
senza toccare il resto.

---

## 1. Tre concetti, da non confondere

```
 CATALOGO DEI CONNETTORI          COLLEGAMENTI                     AZIENDE
 (codice, sviluppato da noi)      (configurati dal Superutente)    (Impostazioni → Aziende)
 ───────────────────────          ─────────────────────────────    ───────────────────────
 Integra        v1.0      ──►     «Integra sede» (tipo Integra)  ──►  Luis S.r.l.   codice 001
 (altri in futuro)                 host, db, utente, password     ──►  Decobrands     codice 002
                                                                        (se nello stesso Integra)
```

| Concetto | Che cos'è | Chi lo crea | Dove vive |
|---|---|---|---|
| **Connettore** (tipo) | Il *codice* che sa parlare con un certo gestionale: come ci si collega, quali tabelle legge, come le traduce nel modello canonico | Noi, sviluppatori | Nel repository: `Sviluppo/connettori/<tipo>/` |
| **Collegamento** | *Un'istanza configurata* di un connettore: questo server, questo database, queste credenziali | Il Superutente, dal pannello | Nel database (credenziali cifrate, §4) |
| **Abbinamento** | *Questa nostra azienda* legge da *questo collegamento*, con *questo codice azienda* dentro il gestionale | Il Superutente, dalla scheda dell'azienda | Nella tabella `aziende` |

**Perché separati:**
- Un gestionale **multi-azienda** (Integra lo è) ospita più aziende: un solo
  collegamento, più abbinamenti. Le credenziali si inseriscono una volta.
- Aziende su gestionali **diversi**: collegamenti diversi, anche di tipi diversi.
- Un gestionale **nuovo** si aggiunge scrivendo un connettore: non cambiano né
  il pannello, né il modello dati, né l'assistente.

**Resta vero (decisione 69):** i dati importati stanno nello spazio della
**nostra** azienda (`luis:20657`), non del collegamento. Due aziende non si
mescolano mai, qualunque gestionale usino.

---

## 2. Il catalogo dei connettori

### 2.1 Un connettore è una cartella

```
Sviluppo/connettori/
  base.py                 interfaccia comune + motore di importazione (uguale per tutti)
  conformita.py           kit di prove che OGNI connettore deve passare
  integra/
    manifesto.json        chi sono, cosa mi serve, cosa so fare
    connettore.py         le funzioni dell'interfaccia
    viste/                l'SQL che traduce Integra nel modello canonico
      soggetti.sql, articoli.sql, listini.sql, documenti.sql, ...
    README.md             prerequisiti lato gestionale (utente di sola lettura, ecc.)
  <tipo futuro>/
```

Il catalogo **non si configura**: è ciò che è installato. Il pannello lo legge
(§8.1). Aggiungere un gestionale = aggiungere una cartella e far passare il kit
di conformità.

### 2.2 Il manifesto

Descrive il connettore al resto del sistema. Il pannello costruisce da qui il
modulo per creare un collegamento, senza codice specifico per ogni gestionale.

```json
{
  "tipo": "integra",
  "nome": "Integra",
  "versione": "1.0.0",
  "descrizione": "Gestionale Integra su PostgreSQL",
  "parametri": [
    {"chiave": "host",     "etichetta": "Server",           "tipo": "testo",    "obbligatorio": true},
    {"chiave": "porta",    "etichetta": "Porta",            "tipo": "numero",   "predefinito": 5432},
    {"chiave": "database", "etichetta": "Database",         "tipo": "testo",    "obbligatorio": true},
    {"chiave": "utente",   "etichetta": "Utente (sola lettura)", "tipo": "testo", "obbligatorio": true},
    {"chiave": "password", "etichetta": "Password",         "tipo": "segreto",  "obbligatorio": true},
    {"chiave": "ssl",      "etichetta": "Connessione cifrata", "tipo": "scelta",
     "valori": ["richiesta", "preferita", "disattivata"], "predefinito": "preferita"}
  ],
  "multi_azienda": true,
  "entita": {
    "soggetti":           {"strategia": "incrementale", "cancellazioni": "lettura_completa_notturna"},
    "articoli":           {"strategia": "incrementale", "cancellazioni": "lettura_completa_notturna"},
    "listini":            {"strategia": "completa_per_listino"},
    "documenti_vendita":  {"strategia": "incrementale"},
    "documenti_acquisto": {"strategia": "incrementale"},
    "scadenze":           {"strategia": "completa"},
    "giacenze":           {"strategia": "fotografia", "frequenza_minima": "giornaliera"}
  },
  "letture_dirette": ["giacenza", "fido", "prezzo_netto"]
}
```

- `tipo: "segreto"` → il campo si scrive e **non si rilegge mai** (§4).
- `entita` → cosa il gestionale sa fornire. Un'entità assente è **"non
  disponibile"**, non un errore: nella griglia delle importazioni compare
  così (spec amministrazione §6).
- `letture_dirette` → cosa si può chiedere in tempo reale (decisione 47, §6).

### 2.3 L'interfaccia comune

Ogni connettore implementa **le stesse funzioni**; il resto del sistema non sa
quale gestionale c'è dietro.

| Funzione | Cosa fa | Chi la usa |
|---|---|---|
| `prova(parametri)` | Si collega e verifica: raggiungibile, credenziali valide, **sola lettura**, versione del gestionale riconosciuta | Pannello, pulsante "Prova collegamento" |
| `aziende(parametri)` | Elenca le aziende presenti nel gestionale (codice, ragione sociale, partita IVA) | Pannello, abbinamento (§5) |
| `estrai(entita, codice_azienda, dal=cursore)` | Restituisce le righe **già nelle colonne del modello canonico**, a blocchi, con `data_modifica_origine` e `extra` | Motore di importazione (§7) |
| `identificativi(entita, codice_azienda)` | Solo gli id presenti oggi: serve a riconoscere le **cancellazioni** senza rileggere tutto | Motore, lettura completa |
| `leggi_diretto(cosa, codice_azienda, chiave)` | Giacenza, fido, prezzo netto in tempo reale | Orchestratore (strumenti dell'assistente) |
| `schema()` | Impronta delle tabelle sorgente (colonne e tipi) | Controllo "il gestionale è cambiato" (§7.4) |

**Il connettore traduce e basta.** Non scrive nel nostro database, non decide
le versioni storiche, non segnala anomalie: tutto questo lo fa il **motore**,
uguale per tutti (`base.py`). Un connettore nuovo è quindi piccolo, e non può
rompere le garanzie dello storico.

### 2.4 Il kit di conformità

`conformita.py` gira su ogni connettore contro un gestionale di prova (o una
copia) e verifica:
- `prova()` rifiuta credenziali sbagliate e un utente **con** permessi di scrittura;
- `estrai()` restituisce solo colonne canoniche esistenti, tipi corretti, id nel
  formato atteso, nessun dato personale escluso (IBAN, mandati: modello dati §7);
- due estrazioni consecutive senza modifiche danno le stesse impronte (niente
  versioni fantasma);
- `leggi_diretto()` risponde entro il tempo massimo (2 s).

Un connettore che non passa il kit **non entra nel catalogo**.

---

## 3. Collegamenti

### 3.1 Campi

| Campo | Note |
|---|---|
| Nome | leggibile: «Integra sede», «Integra filiale» |
| Tipo | dal catalogo; non si cambia dopo la creazione |
| Parametri | dal manifesto del tipo; i segreti cifrati (§4) |
| Stato | **da provare** · **funzionante** · **non raggiungibile** · **credenziali non valide** · **troppi permessi** · **disattivato** |
| Ultima prova | quando, esito, versione del gestionale trovata, aziende trovate |
| Aziende abbinate | quali nostre aziende leggono da qui |

### 3.2 Prova collegamento

Si esegue **sempre** prima di salvare, e a richiesta. Esiti in chiaro:

| Esito | Messaggio | Si salva? |
|---|---|---|
| Tutto ok | «Collegato a Integra 2026.1, trovate 3 aziende» | Sì |
| Non raggiungibile | «Il server 192.168.1.41 non risponde sulla porta 5432» | Sì, come *non raggiungibile* (può essere la rete di oggi) |
| Credenziali | «Utente o password non validi» | No |
| **Troppi permessi** | «L'utente può modificare i dati del gestionale: serve un utente di sola lettura» | **No** (§4.3) |
| Gestionale non riconosciuto | «Tabelle attese non trovate: è davvero Integra?» | No |

### 3.3 Regole

- Nessuna eliminazione se ha aziende abbinate: prima si abbinano altrove o si
  disattivano. Disattivare un collegamento ferma le importazioni delle sue aziende.
- Ogni modifica va nel **registro modifiche** (area «accessi» o nuova area
  «gestionali»): *che cosa* è cambiato, **mai i valori dei segreti**
  («password cambiata»).
- Cambiare host o database di un collegamento che ha già importato dati chiede
  una conferma esplicita: se è un *altro* gestionale, i record non
  corrisponderebbero più.

---

## 4. Credenziali: dove e come

### 4.1 La scelta (rivede una regola precedente)

Finora: credenziali dei gestionali **solo in `.env`**. Con i collegamenti
creati dal Superutente, le credenziali entrano dal pannello. Due strade:

| | Nel database, cifrate (**proposta**) | Solo in `.env` |
|---|---|---|
| Chi le inserisce | Il Superutente, dal pannello | Chi installa, sul server |
| Nuovo gestionale o password cambiata | Dal pannello, subito | Accesso al server e riavvio |
| Rischio | Il database contiene segreti cifrati: serve anche la chiave per usarli | Un file sul server |
| Adatto a | Un prodotto installato presso clienti diversi | Una sola installazione gestita da noi |

**Proposta: nel database, cifrate**, con queste garanzie:

1. **Cifratura autenticata** (AES + HMAC, formato Fernet della libreria
   `cryptography`). *Nuova dipendenza, motivata:* la crittografia non si scrive
   a mano, e `cryptography` è già dipendenza indiretta di PyJWT.
2. **La chiave non sta nel database**: `CHIAVE_CREDENZIALI` in `.env`. Chi ruba
   solo il database (backup, dump) non ha i segreti. Senza chiave, il servizio
   non parte (come `identita.autocontrollo`).
3. **Sola scrittura**: un segreto salvato non torna mai indietro, né al
   pannello né alle API né nei log. Si può solo sostituire. Il campo mostra
   «impostata il 18/09/2026 da prova.super».
4. **Chi cifra e decifra**: solo il servizio `connettori`, in memoria, al
   momento del collegamento. Il pannello di amministrazione **non** ha la
   chiave: quando il Superutente inserisce una password, il pannello la
   **inoltra al servizio `connettori`**, che la cifra e la salva. Il pannello
   non vede mai né la chiave né il segreto salvato.
5. **Rotazione della chiave**: le righe portano la versione della chiave;
   `CHIAVE_CREDENZIALI_PRECEDENTE` permette di ricifrare senza fermare nulla.

### 4.2 Tabella

```sql
CREATE TABLE collegamenti (
    id            text PRIMARY KEY,             -- slug: 'integra-sede'
    nome          text NOT NULL,
    tipo          text NOT NULL,                -- dal catalogo
    parametri     jsonb NOT NULL DEFAULT '{}',  -- SOLO i non segreti
    segreti       bytea,                        -- JSON dei segreti, cifrato (Fernet)
    versione_chiave smallint,
    stato         text NOT NULL DEFAULT 'da_provare',
    ultima_prova  jsonb,                        -- esito, versione, aziende trovate
    segreti_cambiati_da text, segreti_cambiati_il timestamptz,
    attivo        boolean NOT NULL DEFAULT true,
    creato_il     timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE aziende ADD COLUMN collegamento text REFERENCES collegamenti(id);
-- (connettore diventa ricavabile dal collegamento; codice_origine resta)
ALTER TABLE aziende ADD CONSTRAINT un_abbinamento_per_codice
    UNIQUE (collegamento, codice_origine);   -- la stessa azienda del gestionale in UNA sola nostra azienda
```

### 4.3 Sola lettura, verificata

Il connettore si collega **solo con un utente di sola lettura** sul
gestionale. Non è una raccomandazione: `prova()` lo verifica (per Integra:
nessun privilegio di INSERT/UPDATE/DELETE sulle tabelle lette, nessun ruolo
superutente) e **rifiuta** il collegamento altrimenti.

> Oggi il portale B2B legge Integra con l'utente amministratore `postgres`.
> Per l'assistente serve un utente dedicato: vedi `connettori/integra/README.md`
> (da scrivere) con lo script di creazione.

---

## 5. Abbinamento alle aziende

Nella scheda dell'azienda (Impostazioni → Aziende):
- **Collegamento**: scelta tra quelli attivi.
- **Azienda nel gestionale**: se il connettore è multi-azienda, un **elenco
  delle aziende trovate** da `aziende()` («001 — Luis S.r.l.», «002 — ...»),
  non un campo libero. Senza collegamento funzionante, campo libero con
  avviso.
- **Da quando importare** (facoltativo): per non trascinare vent'anni di
  documenti. Anagrafiche e listini si importano sempre per intero.

**Regole:**
- Una coppia *collegamento + codice azienda* abbinata a **una sola** nostra
  azienda (vincolo nel database).
- Un'azienda del gestionale **non abbinata non si importa**.
- Cambiare l'abbinamento di un'azienda che ha già dati chiede conferma: i nuovi
  record avranno origini diverse; lo storico resta (modello dati §4).

---

## 6. Letture dirette

Giacenza attuale, fido residuo e prezzo netto per il cliente non si replicano
(decisione 47): si chiedono al gestionale **nel momento della domanda**, tramite
`leggi_diretto()` del connettore dell'azienda.

- Le chiama l'**orchestratore** (strumenti dell'assistente), attraverso il
  servizio `connettori`: l'orchestratore non ha credenziali (§4).
- Tempo massimo 2 s; oltre, la risposta dice «dato in tempo reale non
  disponibile» invece di un numero vecchio.
- Stessi permessi del resto: chi chiede la giacenza di Luis deve essere
  abilitato a Luis e al gruppo che vede quella fonte.

---

## 7. Importazione autonoma

### 7.1 Chi fa girare cosa

```
 Dagster (orari, storico, avvio a mano)             decisione 60
      │  per ogni azienda × entità
      ▼
 servizio `connettori`  ── motore (base.py) ──► connettore del tipo giusto ──► gestionale
      │                     │                                                (sola lettura)
      │                     ├─ area di sosta  (erp_sosta.*)
      │                     ├─ fusione storica (erp_storico.*, SCD2)
      │                     ├─ controlli di qualità
      │                     └─ segnala_anomalia() / chiudi_anomalia()
      ▼
 erp.sincronizzazioni (stato per azienda × entità)  ──►  Panoramica del pannello
```

- **Autonoma** = gira da sola agli orari configurati, senza il pannello e
  senza la chat. Il pannello **legge** lo stato; "Avvia ora" passa da Dagster.
- **Prima di Dagster** (sviluppo): stesso motore da riga di comando,
  `py -m connettori.esegui --azienda luis --entita soggetti`.
- **Un'esecuzione alla volta** per azienda × entità (blocco nel database): due
  importazioni sovrapposte non creano versioni doppie.

### 7.2 Il flusso di un'esecuzione

1. **Blocco** su (azienda, entità); `sincronizzazioni.esito = 'in_corso'`.
2. **Estrazione** a blocchi da `estrai()` → area di sosta `erp_sosta.<entità>`
   (tabella di lavoro, svuotata a ogni esecuzione).
3. **Controlli prima di toccare lo storico** (§7.4). Se uno è bloccante: stop,
   lo storico resta com'era, anomalia.
4. **Fusione** nell'`erp_storico` con l'algoritmo del modello dati §4.4
   (impronta uguale → niente; diversa → chiude e apre versione; sparito in una
   lettura completa → `cancellato`). **Una transazione per entità.**
5. **Aggiornamento** di `sincronizzazioni`: cursore, righe lette, versioni
   nuove, durata, esito; `chiudi_anomalia()` per i problemi rientrati.

### 7.3 Strategie (dal manifesto)

| Strategia | Quando | Cancellazioni |
|---|---|---|
| `incrementale` | Il gestionale ha una data di modifica affidabile | Lettura degli **identificativi** notturna |
| `completa` | Tabelle piccole o senza data di modifica | Implicite |
| `completa_per_listino` | Listini (grandi, ma interrogabili per listino) | Per listino |
| `fotografia` | Giacenze: una fotografia al giorno in `erp.giacenze_istantanee` | — |

### 7.4 Controlli di qualità → anomalie

| Controllo | Soglia (configurabile per entità) | Gravità | Bloccante |
|---|---|---|---|
| Righe crollate rispetto all'ultima lettura completa | −50% | Critica | **Sì**: quasi sempre un collegamento rotto, non dati spariti |
| Schema del gestionale cambiato (`schema()`) | colonna nuova / sparita | Avviso / Errore | Solo se sparisce una colonna mappata |
| Collegamento non raggiungibile | 3 tentativi | Errore | Sì |
| Importazione in ritardo | 2 × frequenza prevista | Avviso | — |
| Partite IVA duplicate, prezzi a zero, riferimenti a articoli inesistenti | presenza | Avviso | No |

Le anomalie usano `segnala_anomalia()` con impronte stabili
(`importazione:<azienda>/<entità>:<controllo>`): si contano, non si
moltiplicano (decisione 57).

### 7.5 Frequenze iniziali proposte

| Entità | Frequenza |
|---|---|
| Documenti di vendita e acquisto | ogni 15 minuti (incrementale) |
| Soggetti, articoli | ogni ora (incrementale) + lettura completa notturna |
| Listini | ogni notte |
| Scadenze | ogni ora |
| Giacenze (fotografia) | ogni giorno alle 06:00 |

Si modificano in Dagster (Gestione importazioni).

---

## 8. Interfaccia (Superutente)

Nuova sezione **Gestionali** in *Impostazioni*, solo Superutente (il Revisore
legge). Stesso stile e componenti delle altre schermate.

### 8.1 Connettori (catalogo)

Sola lettura: i connettori installati, con versione, descrizione, entità
fornite, letture dirette, esito del kit di conformità. Per chi deve capire
*cosa è possibile collegare*.

### 8.2 Collegamenti

- **Elenco**: nome, tipo, server/database (non segreti), stato con semaforo,
  ultima prova, aziende abbinate.
- **Nuovo collegamento**: scelta del tipo → modulo generato dal manifesto →
  **Prova collegamento** (obbligatoria) → salva. I campi segreti sono di sola
  scrittura.
- **Scheda**: parametri, «password impostata il … da …», **Cambia password**,
  **Prova collegamento**, aziende trovate nel gestionale e quali sono abbinate,
  disattiva.

### 8.3 Aziende (esistente, si estende)

- Campo **Collegamento** al posto di "Gestionale".
- **Azienda nel gestionale** scelta dall'elenco trovato (§5).
- **Da quando importare**.

### 8.4 Importazioni

Già previste: griglia azienda × entità in Panoramica (dati da
`sincronizzazioni`), dettaglio ed esecuzioni in Dagster.

---

## 9. Sicurezza — riepilogo

| Regola | Dove si applica |
|---|---|
| Utente di **sola lettura** sul gestionale, verificato | `prova()`, kit di conformità |
| Segreti cifrati, chiave fuori dal database, mai riletti | servizio `connettori`, tabella `collegamenti` |
| Solo il servizio `connettori` decifra; pannello e orchestratore no | architettura (§4.1) |
| Il servizio `connettori` raggiunge solo gli host dei collegamenti attivi | allowlist di rete, come `egress` dell'orchestratore (invariante 4) |
| Dati personali esclusi (IBAN, mandati) | viste del connettore + kit di conformità |
| Ogni modifica a collegamenti e abbinamenti nel registro, senza segreti | pannello |
| **Il gate deve filtrare per azienda prima del primo dato di due aziende** | decisione 67, backlog A6: **prerequisito** |

---

## 10. Il primo connettore: Integra

**Base di partenza:** le viste del portale B2B (`DB Integra - Luis/b2b_*.sql`),
che coprono clienti, destinazioni, pagamenti, ordini e righe, prodotti,
listini, giacenze e tabelle codici. Si riscrivono nelle colonne canoniche e su
**`postgres_fdw`** o lettura diretta, non su `dblink` (i filtri arrivano al
gestionale: il B2B segnala letture lente dei listini).

| Entità canonica | Tabelle Integra | Stato |
|---|---|---|
| soggetti, soggetti_ruoli | `clienti`, `cliazi` | da vista B2B |
| indirizzi | `destinazioni` | da vista B2B |
| articoli | `prodotti`, `classivoci`, `entleg` | da vista B2B |
| articoli_fornitori | `prosoggetti` | nuovo (campi da confermare, es. `psg_liberon1`) |
| listini, listini_righe | `listest`, `listini` | da vista B2B |
| documenti vendita | `movtest`, `movrig` (`mvt_natmov`) | B2B ha solo gli **ordini**: servono DDT e fatture |
| documenti acquisto | `movtest`, `movrig` | **nuovo** |
| scadenze | da individuare | **nuovo** |
| giacenze (fotografia) | `maginv`, `maginvt` | da vista B2B |
| codici | `tabpag`, `tabpor`, `tabspe`, `vettori`, `classivoci` | da vista B2B |

- **Multi-azienda**: `azi_cdazi` è il codice nel gestionale; `aziende()` lo
  legge dall'anagrafica aziende di Integra.
- **Letture dirette**: giacenza (ultima fotografia di inventario + movimenti),
  fido (`cliazi`), prezzo netto (**da chiedere a chi conosce Integra**: il B2B
  ricostruisce la logica prezzi, noi non dobbiamo — decisione 48).

---

## 11. Le due aziende di oggi

| | Luis S.r.l. | Decobrands |
|---|---|---|
| Gestionale | Integra (192.168.1.41, database `integra`) | **da sapere** |
| Codice nel gestionale | `001` | **da sapere** (002 nello stesso Integra?) |
| Utente di sola lettura | **da creare** (oggi il B2B usa `postgres`) | idem |
| Raggiungibile dal server dell'assistente | **da verificare** | idem |

Se Decobrands sta nello **stesso Integra**: un collegamento, due abbinamenti.
Se sta su **un altro gestionale**: serve il suo connettore (§2) prima di
importare.

---

## 12. Criteri di accettazione

- [ ] Kit di conformità verde per Integra, compreso il rifiuto di un utente con
  permessi di scrittura.
- [ ] Un collegamento si crea dal pannello; il segreto non compare mai in
  risposte API, log, registro, dump della tabella (è cifrato).
- [ ] Senza `CHIAVE_CREDENZIALI` il servizio `connettori` non parte.
- [ ] Prima importazione completa di Luis: righe in `erp_storico`, stato in
  `sincronizzazioni`, griglia della Panoramica verde.
- [ ] Seconda importazione senza modifiche: **zero versioni nuove**.
- [ ] Modifica di un cliente nel gestionale → **una** versione nuova, la
  precedente chiusa.
- [ ] Collegamento irraggiungibile → anomalia, storico intatto; torna
  raggiungibile → anomalia chiusa automaticamente.
- [ ] Calo simulato delle righe del 60% → importazione fermata, anomalia critica.
- [ ] Due aziende nello stesso gestionale → record separati per spazio di nomi.
- [ ] Filtro per azienda nel gate (A6) attivo e testato **prima** del punto
  precedente.

---

## 13. Fasi

1. **Prerequisiti**: filtro per azienda nel gate (A6); utente di sola lettura
   su Integra; raggiungibilità dal server.
2. **Motore e Integra da riga di comando**: `base.py`, connettore Integra,
   kit di conformità, prima importazione di Luis. Collegamento configurato a
   mano (script), senza pannello.
3. **Collegamenti nel pannello**: tabella, cifratura, schermate §8,
   abbinamento con scoperta delle aziende.
4. **Dagster**: orari, storico delle esecuzioni, "Avvia ora".
5. **Letture dirette** negli strumenti dell'assistente.
6. **Secondo connettore**, quando servirà.

---

## 14. Decisione proposta (70) e domande aperte

**Decisione 70 (proposta):** catalogo dei connettori nel codice, collegamenti
configurati dal Superutente con credenziali **cifrate nel database** (chiave in
`.env`, sola scrittura, decifrate solo dal servizio `connettori`), abbinati
alle aziende con vincolo di unicità; motore di importazione comune che fa
storico, controlli e anomalie; connettori che si limitano a tradurre.

**Da decidere o sapere:**
1. Credenziali cifrate nel database (proposta) oppure solo in `.env`?
2. **Decobrands**: quale gestionale? Stesso Integra con un altro codice azienda?
3. Chi crea l'**utente di sola lettura** su Integra, e su quali tabelle?
4. Il server dell'assistente **raggiunge** 192.168.1.41?
5. **Da quando** importare i documenti (tutto lo storico o dal 2020…)?
6. Nel gestionale le **righe dei documenti hanno il costo**? (margini, modello dati §5.11)
7. Il **prezzo netto** in Integra: esiste una funzione o vista da interrogare, o
   va chiesto al fornitore del gestionale?

⚠ **Da sistemare a prescindere:** la password dell'utente `postgres` di Integra
è scritta in chiaro in file del progetto B2B, anche nella sua cronologia git.
Va cambiata, e l'assistente userà un utente suo.
