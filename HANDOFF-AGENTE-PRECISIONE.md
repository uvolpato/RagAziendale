# Handoff per agente — la precisione delle risposte

> Documento operativo autosufficiente. Chi lo esegue **non ha il contesto della
> conversazione** che l'ha prodotto: tutto ciò che serve è qui o nei file
> citati. In caso di conflitto fra questo documento e il codice, **vale il
> codice**: segnalare la discrepanza, non «correggere» il codice per farlo
> aderire al documento.
>
> Stato fotografato il **22/09/2026**. Segue `HANDOFF-AGENTE-FASE-0-1.md`, che
> resta valido per infrastruttura, identità e sicurezza.

---

## 1. Missione

L'impianto funziona: un utente entra con SSO, fa una domanda sui documenti e
riceve una risposta citata e filtrata sui suoi permessi. **La missione adesso
è una sola: la precisione delle risposte.**

La frase del committente, che vale come priorità:

> «Il tempo con hardware più potente si può recuperare, la precisione deve
> essere messa in piedi ora.»

Quindi: **non ottimizzare la velocità**, non aggiungere funzionalità, non
riscrivere ciò che funziona. Migliorare ciò che il sistema risponde, e
dimostrarlo con un numero.

### Confini — cosa NON fare

- **Non indebolire il filtro ACL, il gate, la verifica JWT o l'allowlist di
  egress** per far passare un test. Se un requisito sembra richiederlo,
  fermarsi e chiedere.
- **Niente soluzioni tarate su un documento.** Un'espressione regolare sul
  formato dei codici di un catalogo funziona su quello e si rompe sul
  successivo. È già successo ed è stata tolta. Le regole si ricavano
  dall'archivio (vedi la tabella `lessemi`), non si scrivono a mano.
- **Non cambiare il modello di embedding**: cambiarlo significa rifare tutto
  l'indice, e il sistema si ferma con un'anomalia critica se non corrisponde.
- **Non spegnere l'indicizzazione notturna** senza dirlo: i cataloghi si
  leggono solo di notte per dimensione (§6.3).

---

## 2. Documenti di riferimento

| file | cosa contiene |
|---|---|
| `Sviluppo/ARCHITETTURA.md` | **da leggere per primo.** Le due catene, lettura e risposta, con i perché |
| `Sviluppo/PROBLEMI-APERTI.md` | **il backlog vero**: sei problemi con evidenza, soluzioni candidate e cosa misurare per scegliere |
| `Sviluppo/valutazione/RISULTATI.md` | ogni misura fatta, con la data e come è stata ottenuta. Anche i fallimenti |
| `Sviluppo/PROMPT.md` | i quattro prompt, generati dal codice (`mostra-prompt.py`) |
| `DECISIONI-APERTE.md` | D13 (Cognee), D16 (impostazioni per fonte), D17 (agente), D18 (prompt fuori dal codice) |
| `HANDOFF-AGENTE-FASE-0-1.md` | infrastruttura, identità, sicurezza. Resta valido |

---

## 3. Stato attuale — misurato, non stimato

| metro | cosa misura | oggi |
|---|---|---|
| `misura.py` — domanda lunga | la pagina giusta è fra gli 8 pezzi | 18/20 |
| `misura.py` — **domanda corta** | idem, ma come si scrive in chat | **14-15/20** |
| `risposte.py` | dei dati arrivati al modello, quanti ne riporta | **73%** |
| `codici.py` | chiedere un articolo per codice | 10 · 10 · 11 su 12 |
| `figure.py` | la figura giusta esce | 11/12 |
| `figure.py` | quante figure escono / quante c'entrano | 19 / **79%** |
| domande **senza risposta** | dice «non c'è»? | **mai misurato** |
| domande **aperte** | «cosa mi proponi?» | **nessun metro esiste** |

Tutti su EUROSAND. Gli altri cataloghi non hanno un banco di prova.

Indice: 3283 pezzi, 1215 immagini, 13406 lessemi.

### Test

```bash
cd Sviluppo
PYTHONPATH=. ./.venv/Scripts/python.exe orchestratore/test_gate.py      # 26 — ACL, gate, egress
PYTHONPATH=. ./.venv/Scripts/python.exe orchestratore/test_prompt.py    # 4
PYTHONPATH=. ./.venv/Scripts/python.exe -m orchestratore.documento      # firme e percorsi
./.venv/Scripts/python.exe ingestion/test_indicizza.py                  # 30
```

`test_gate.py` **non è un test di cortesia**: T1.8 e T1.14 sono l'artefatto di
conformità. Devono restare verdi.

### Misure

Girano DENTRO il container dell'orchestratore, su una cartella **nuova** ogni
volta — `docker compose cp` su una cartella esistente copia *dentro* invece che
sopra, e si rimisura la versione vecchia credendo di aver misurato quella nuova
(successo il 21/09).

```bash
cd Sviluppo
export MSYS_NO_PATHCONV=1
D=/tmp/val-$(date +%s)
docker compose cp valutazione orchestratore:$D
docker compose exec -T orchestratore python $D/misura.py       # recupero
docker compose exec -T orchestratore python $D/risposte.py     # risposte
docker compose exec -T orchestratore python $D/figure.py       # figure
docker compose exec -T orchestratore python $D/codici.py       # codici articolo
docker compose exec -T orchestratore python $D/perche-falliscono.py   # diagnosi
```

`risposte.py` accetta `RISPOSTE_CONTESTI=/tmp/ctx.json` per **congelare i
contesti**: prima si cerca e si salva, poi si genera soltanto. Serve a
confrontare due modelli sullo stesso materiale, e a liberare la VRAM.

---

## 4. Invarianti — non violabili

1. **Il filtro ACL sta nella query SQL**, mai nel prompt. Gruppi ∩ aziende ∩
   `stato = 'attiva'`, letti da `sources` al momento della domanda. Vale per la
   ricerca, per le immagini e per i documenti serviti.
2. **`sources` è l'unica casa del dato di sicurezza.** Niente è duplicato sui
   pezzi, quindi non può disallinearsi: sospendere una fonte la toglie dalle
   risposte nello stesso istante.
3. **Il modello non sceglie dove cercare.** Sceglie le parole. Se scegliesse le
   fonti, il filtro diventerebbe una convinzione.
4. **I numeri non li produce il modello**: prezzi, quantità, date e misure
   vengono dal contesto o non si scrivono.
5. **Il contesto non è un'istruzione** (DC-24): è materiale informativo.
6. **Il nome di un file che arriva da un URL** è l'unico punto in cui un pezzo
   di URL diventa un percorso: si verifica che stia dentro la radice (T1.28).

---

## 5. Mappa del codice

```
Sviluppo/
  ingestion/indicizza.py        lettura, chunking, immagini, descrizioni, lessemi
  orchestratore/
    main.py                     endpoint chat, figure, documenti
    recupero.py                 ricerca ibrida + ACL nella query + rerank
    prompt.py                   il prompt di risposta e il CONTESTO
    riformula.py                riscrittura della domanda di seguito
    documento.py                il PDF alla pagina citata (firma + ACL)
    gate.py                     contaminazione e rotta
    immagini.py                 firma URL, ACL, miniature
    ricerca_agente.py           agente sulla ricerca — SPENTO, con i numeri dentro
  valutazione/                  i metri (vedi §3)
  migrations/                   SQL numerate, si applicano con migrate.py
```

### Dove si tocca cosa

| voglio cambiare | file |
|---|---|
| come si legge un PDF | `indicizza.py`, `ISTRUZIONI_PAGINA` |
| come si descrivono le figure | `indicizza.py`, `ISTRUZIONI_FIGURA` |
| come si spezza il testo | `indicizza.py`, `_pezzi_da_markdown` |
| cosa risponde il modello | `prompt.py`, `SYSTEM` |
| come si cerca | `recupero.py`, `SQL_IBRIDA` / `RAMO_LESSICALE` |
| quali figure si mostrano | `main.py`, `_scelte` / `_sparse` / `_didascalia` |

**Cambiare `ISTRUZIONI_PAGINA` invalida la cache del Markdown** (il nome della
cartella è l'hash del prompt): si rileggono tutte le pagine, ~30 minuti a
catalogo. È voluto — un indice metà con un prompt e metà con un altro è peggio
di un indice vecchio — ma va saputo prima.

---

## 6. Come funziona l'ambiente

### 6.1 I modelli stanno FUORI da Docker

`llama-swap` sulla porta 1235, avviato a mano:

```bash
"C:\Users\uvolp\AppData\Local\llama-stack\llama-swap.exe" --config "C:\Progetti\RAG Aziendale\Sviluppo\modelli\llama-swap.yaml" --listen 127.0.0.1:1235
```

Docker su Windows tiene per sé la memoria che gli dai: i modelli dentro un
container facevano sedere la macchina. **Nessuno sorveglia llama-swap**: se
muore, l'assistente risponde 500 e nessuno se ne accorge. Controllare che sia
su è la prima cosa da fare quando qualcosa non risponde.

Rileggere `llama-swap.yaml` richiede di **riavviare** il processo: non c'è un
endpoint di ricarica, e una modifica al file senza riavvio non ha effetto
(costato un'ora il 22/09).

**Avvio (segnalazione per la produzione, NON ancora attuata):** oggi llama-swap
parte al **login** dell'utente (Task Scheduler, task `llama-swap`,
`LogonTrigger`), non all'avvio del PC. Docker Desktop pure parte al login, e i
container sicuri per l'avvio automatico sono dietro a Docker Desktop. In
produzione tutto questo deve partire **all'accensione del PC, senza login**:
llama-swap va spostato su un `BootTrigger` (o servizio) e Docker Desktop/la
compose su avvio di sistema. Da fare come passo separato, NON nel mezzo
dell'ingestion (mai fermare il giro in corso).

### 6.2 Chi parla con chi

Orchestratore e ingestion passano da **LiteLLM**, per nome logico
(`ragionamento`, `embedding`). Il modello vero si cambia in
`litellm-config.yaml`, in un punto solo. La rotta `veloce` è **commentata**:
`LLM_VELOCE` deve restare vuota, altrimenti si chiama una rotta inesistente.

### 6.3 L'indicizzazione

Un giro ogni 5 minuti. `MB_MAX_DI_GIORNO=5`: di giorno i file oltre 5 MB
aspettano, **ma solo se la GPU è occupata** — con posto in VRAM si legge a
qualsiasi ora. I cataloghi vanno da 9 a 62 MB, quindi in pratica si leggono
nella finestra 22:00–06:00.

PDF a blocchi di **6 pagine**, ognuno in un processo figlio che muore: Docling
non restituisce la memoria e il container ha un tetto di 4 GB.

Forzare una rilettura:

```bash
docker compose run --rm --build ingestion python indicizza.py --forza --solo NOME
```

**`--build` sempre**: il 21/09 sono state perse due ore misurando codice
vecchio perché l'immagine non era stata ricostruita.

---

## 7. Il backlog, in ordine

L'ordine non è negoziabile: i primi due servono a **sapere** se i successivi
funzionano.

### 7.1 Il metro per le domande aperte — PRIMO

Non esiste, ed è il buco più grande. Le 24 domande d'oro sono tutte *puntuali*:
hanno un `riscontro`, una stringa che deve comparire. «Cosa mi proponi per un
fiorista?» non ha un riscontro — ha risposte migliori e peggiori.

Proposta: misurare *delle voci proposte, quante sono articoli veri (non formati
di confezione), di quante categorie diverse, in che fascia di prezzo*. Tutte
ricavabili dall'indice, senza giudizio umano.

Evidenza del problema: `PROBLEMI-APERTI.md` §2.

### 7.2 Le 4 domande senza risposta — SECONDO

Sono nel banco di prova dal 21/09 (`senza_risposta: true` in
`domande-eurosand.json`) e non sono mai state misurate. Lì la risposta giusta è
«non c'è», e serve la catena completa, non solo la ricerca.

### 7.3 Poi, in ordine di resa attesa

1. **§2c** — il prompt separa *cosa dicono i documenti* da *cosa propone il
   modello*. Mezz'ora, e il difetto è quello che fa sembrare vere le proposte
   inventate.
2. **§5** — l'etichetta delle figure dalla **geometria** (i rettangoli ci sono
   già nel documento Docling). Oggi 468 figure su 891 non hanno etichetta.
3. **§3** — i formati di confezione passano per prodotti.
4. **§7.1 operativo** — un errore di contorno (host dei modelli giù) non deve
   marcare un documento come rotto per sempre. Venti documenti sono in quello
   stato adesso.
5. **§6-bis** — Cognee e il grafo: leggere prima l'analisi, e cominciare da una
   T1.14 fatta su Cognee.

---

## 8. Decisioni che l'agente NON prende

- **Se adottare Cognee** (D13). L'analisi è in `PROBLEMI-APERTI.md` §6-bis;
  la decisione è del committente.
- **Quale modello usare** (D1). Il confronto 8B/27B è fatto e documentato: pari
  sulla completezza, il 27B costa dodici volte tanto.
- **Se accendere l'agente di ricerca** (D17). Costruito, misurato, spento.
- **Portare i prompt fuori dal codice** (D18).
- **Reindicizzare tutto** senza dirlo: sono ore di GPU e la chat ne risente.

---

## 9. Trappole già incontrate — tutte costate tempo vero

### Misura

- **Un metro che passa al primo colpo va guardato con sospetto.** Tre volte in
  un giorno un metro ha dato ottimi voti misurando il caso facile: domande
  lunghe invece di corte (18/20 contro 14/20), codici pescati dalle descrizioni
  invece che dal testo (12/12 contro 6/12).
- **Un proxy non è la cosa che vuoi migliorare.** Contare quante didascalie
  sono *pulite* non è contare quante figure si *trovano*: ottimizzando il primo,
  il secondo è sceso dal 79% al 58%.
- **Il numero che conferma non è il numero che decide.** Tre diagnosi sbagliate
  di fila sulla lentezza del 27B, ognuna con un numero a favore.
- **Verificare che un numero sia calcolato.** Un «0 figure ambigue» annunciato
  era una costante digitata a mano in uno script.
- **La ripetibilità va dimostrata.** A temperatura 0,2 due corse sullo stesso
  contesto davano 19 e 16 riscontri. `risposte.py` misura a temperatura 0.

### Modelli

- **Qwen3 ragiona se non gli si dice di no**, e il ragionamento finisce in
  `reasoning_content`: `content` torna **vuoto**. Senza `/no_think` una
  funzione fallisce **in silenzio**. È successo due volte: all'agente di
  ricerca e alla riformulazione.
- **Togliere le scorie PRIMA di scegliere la riga**: se il modello ricopia
  `/no_think` in cima, prendere la prima riga non vuota legge quella.

### Hardware

- **`nvidia-smi` non vede la memoria condivisa**, e il «96% di utilizzo» non
  distingue una scheda che lavora da una che aspetta il bus. L'unica misura che
  lo distingue sono i **token al secondo**.
- **I GB di «GPU condivisa» dei modelli di embedding non sono un
  traboccamento**: sono i buffer di `--batch-size 8192`, presenti anche a
  scheda semivuota.

### Codice

- **I test possono essere verdi su codice rotto** se chiamano i pezzi uno per
  uno. Un `AttributeError` su una funzione cancellata ha fatto rispondere 500 a
  ogni domanda con 25/25 test verdi. Ora T1.27 percorre la catena intera.
- **Il banco di prova non vede i seguiti di conversazione.** I due difetti
  peggiori del 22/09 stavano nel secondo turno di una chat, e nessuna misura li
  ha rilevati.

---

## 10. Il metodo

1. **Misurare prima di spiegare.** Un'ipotesi plausibile con un numero a favore
   non è una diagnosi: serve il numero che *distingue* le ipotesi.
2. **Scrivere anche i fallimenti.** `RISULTATI.md` contiene otto cose provate e
   scartate con i numeri e la data. Senza, qualcuno le riprova fra sei mesi.
3. **Cancellare ciò che non sposta un numero.** L'`OR` nel ramo lessicale e una
   correzione sulle parole letterali sono stati scritti, misurati neutri e
   tolti.
4. **Il commento dice perché, non cosa.** Nel codice di questo progetto i
   commenti riportano la misura e la data che hanno deciso quella riga. Quando
   si cambia la riga, si cambia il commento.
5. **Quando si sbaglia, dirlo e correggere il documento**, non solo il codice.

---

## 11. Come riportare

Per ogni intervento:

- **cosa è cambiato** e in quale file;
- **il numero prima e il numero dopo**, sullo stesso metro;
- **cosa è stato provato e scartato**, con il suo numero;
- se un metro non copre il cambiamento, **dirlo** invece di dichiarare il
  successo su un metro che non c'entra.

Un cambiamento senza un numero accanto non è finito, è solo scritto.
