# L'orchestratore come agente — parere e progetto

> Richiesta del 21/09/2026: un agente per ogni utente, sempre attivo, che in chat
> possa creare subagenti; e valutare se trasformare i servizi in server MCP.
>
> Questo documento è un **parere argomentato**, non una decisione presa. Le
> decisioni vanno in `DECISIONI-APERTE.md` (D17 è già aperta sul tema).
> Ogni numero qui dentro è misurato il 21/09/2026 sulla macchina di sviluppo,
> non stimato. Dove non ho misurato, lo scrivo.

---

## 1. Il parere in tre righe

**Sul recupero: sì, l'agente serve, e oggi ho la prova.** Quello che ho fatto a
mano per far funzionare «sassi rossi» — riprovare con parole diverse, guardare
la pagina invece del frammento — è esattamente un ciclo di agente.

**Su «un agente sempre attivo per ogni utente»: la formula mette insieme tre
cose diverse**, e due delle tre non hanno bisogno di un agente. Vanno separate
prima di costruire, o si paga un prezzo alto per una cosa che si poteva avere
gratis.

**Su MCP: sì per gli strumenti di sola lettura e i sistemi esterni, no per i
servizi che scrivono o che fanno rispettare le regole.** MCP cambia *chi
chiama* un servizio, non quanto è indipendente: i servizi sono già indipendenti.

---

## 2. Le misure da cui parte tutto

Il 21/09/2026, domanda «ho bisogno di sassi rossi», 4571 pezzi indicizzati.

| Cosa | Risultato |
|---|---|
| Ricerca così com'è | pezzo giusto in **posizione 29 su 4571** → invisibile |
| Ricerca testuale | `plainto_tsquery` mette in AND: **zero risultati**, non contribuisce |
| Domanda riscritta dal modello | **posizione 4** — una chiamata, 7,7 s |
| Modello come giudice di pertinenza, un colpo solo | sceglie *stella di carta rossa*, *pompon marrone*: **inaffidabile** |
| Reranker cross-encoder (bge-reranker-v2-m3) | discrimina bene (+5,54 contro −11,04), 2,6 s su 40 brani |
| Lo stesso reranker su «sassi rossi» | −2,19: **non basta da solo**, serve la riscrittura prima |
| Prompt aperto («elencami i sinonimi») | il modello è andato in **generazione infinita** |

Due conclusioni che contano per il progetto:

1. **Il ciclo funziona.** Una seconda ricerca con parole diverse porta il pezzo
   da invisibile a primi posti. È il singolo intervento con più resa misurata.
2. **Il giudizio è la parte debole.** Il modello che abbiamo sbaglia a dire
   «questo risponde / questo no», e su un prompt aperto non si ferma. Un agente
   amplifica entrambe le cose: più passi significano più occasioni di sbagliare
   con convinzione, non meno.

Il punto 2 non è un argomento contro l'agente. È l'elenco di cosa va costruito
*attorno* al ciclo perché il ciclo sia affidabile.

---

## 3. «Un agente per ogni utente, sempre attivo»

La formula contiene tre cose distinte. Separarle costa niente e chiarisce molto.

### 3a. Sessione calda — **serve, ed è gratis**

Tenere il contesto della persona fra un messaggio e l'altro: chi è, i suoi
gruppi, di cosa si stava parlando. Oggi ogni turno riparte da zero.

Non richiede un agente: è una sessione. Costa memoria, non GPU.

### 3b. Memoria personale — **è già D14**

Cosa quella persona ha chiesto in passato, le sue preferenze, il suo
vocabolario. `DECISIONI-APERTE.md` D14 la colloca già in Cognee, in un dataset
privato per utente. Vale la pena farla, ma è una decisione già aperta altrove:
non serve riaprirla qui.

### 3c. Autonomia in assenza della persona — **è un prodotto diverso**

Un agente che *fa cose* mentre l'utente non c'è: sorveglia i documenti nuovi,
prepara risposte, avvisa. È l'unica delle tre che richiede davvero «sempre
attivo».

Qui va detto chiaro: **un agente che agisce con i permessi di una persona
assente è la cosa più delicata di tutto il progetto.** Se sbaglia, sbaglia
senza testimoni. Prima di costruirla servono: un registro di cosa ha fatto e
perché, un limite netto a cosa può fare da solo (leggere sì, scrivere e
mandare no), e la revoca immediata. Non è impossibile — è che è un progetto a
sé, non un effetto collaterale del ciclo di recupero.

### Quanto costa davvero «sempre attivo», su questa macchina

| | |
|---|---|
| GPU | 16,3 GB |
| Modelli residenti oggi | chat 4,12 + VLM 3,33 + embedding 0,44 = **7,9 GB** |
| Docling mentre legge | fino a **6,1 GB** misurati |
| `PARALLEL` del modello di chat | 4 |

Con 4-5 persone, cinque agenti «sempre attivi» che pensano davvero non girano
in parallelo: si mettono in coda sullo stesso modello. «Sempre attivo»
diventerebbe «sempre in attesa». Questo **non** è un argomento contro — è la
misura di cosa comprare se lo si vuole: per 5 agenti con 3-4 passi ciascuno
servono o una GPU molto più grande (48 GB) o una seconda macchina dedicata
all'inferenza, che `docker-compose.inference.yml` già prevede come forma.

---

## 4. Agente e subagenti sul recupero

### La regola che non si negozia

Già scritta in D17 punto 1, qui si ripete perché è la riga che separa il
progetto da una fuga di dati:

> **L'agente decide COSA cercare, mai COSA può vedere.**

Il recupero resta una funzione con i gruppi come parametro obbligatorio. Fra
gli strumenti dell'agente non esiste niente che li cambi, e i gruppi non
passano mai dal modello: li mette il server, presi dalla sessione. Se questa
regola cade, `test_gate.py` smette di essere una verifica e diventa una
speranza.

### Un subagente per fonte — l'idea buona

Il confine del subagente coincide con il confine dei permessi: ogni subagente
nasce ristretto ai documenti di **una** fonte, e riceve quella fonte solo se la
persona ci ha accesso. La regola qui sopra diventa **strutturale invece che
promessa**: un subagente non può cercare dove non arriva, perché non ha il
collegamento.

In più risolve il caso che oggi la catena fissa perde: «confronta i prezzi di
EUROSAND e FLEURAMI» sono due ricerche e un confronto.

### Il ciclo minimo

```
riscrivi la domanda  →  cerca  →  guarda cosa è tornato
                                        ↓
                          risponde?  ──sì──→  rispondi con le fonti
                                        │
                                        no
                                        ↓
                          riprova con altre parole (max N)
                                        ↓
                               ancora no → dillo
```

La parte difficile è il rombo, ed è quella su cui il modello che abbiamo è
debole (misurato: sceglie pompon marroni). Tre difese, tutte da costruire:

- **Il ciclo è limitato.** Massimo N ricerche, N piccolo (2-3). Un agente che
  può girare all'infinito, prima o poi gira all'infinito — è già successo oggi
  con un prompt aperto.
- **Le risposte del modello sono vincolate**, non libere: schema JSON o
  grammatica. «Quali brani rispondono» è una lista di numeri, non prosa.
- **Chi verifica non è chi risponde.** Un passo separato controlla che ogni
  affermazione citata esista davvero nel pezzo citato. È l'unica difesa contro
  l'agente che itera convinto verso la risposta sbagliata.

### «Non lo so» deve restare una risposta possibile

Oggi l'assistente sa dire «non è disponibile alcuna informazione». Un agente
che ha già speso quattro ricerche ha una pressione strutturale a produrre
*qualcosa*. Va misurato esplicitamente: fra le domande di prova ce ne devono
essere alcune **senza risposta nei documenti**, e l'agente deve continuare a
dire di no.

---

## 5. MCP: dove sì e dove no

MCP dà a un modello un modo standard di chiamare strumenti, con scoperta
automatica. È utile quando **il modello è il chiamante**.

Va detta una cosa sulla premessa: **i servizi sono già indipendenti.** Container
separati, API HTTP, oauth2-proxy davanti, Keycloak per l'identità. Trasformarli
in server MCP non aggiunge indipendenza — cambia chi li chiama. E per tutto ciò
che scrive, passare da «lo chiama il codice» a «lo chiama il modello» è un
peggioramento della sicurezza, non un miglioramento.

| Servizio | MCP? | Perché |
|---|---|---|
| Recupero documenti | **sì**, con i gruppi messi dal server | È lo strumento che l'agente deve chiamare più volte |
| Connettore ERP (sola lettura) | **sì**, per utente | Già previsto in `SPECIFICA-CONNETTORI.md`; l'agente ne ha bisogno per le domande sui dati |
| Posta e calendario | **sì**, per utente | È esattamente D14, già deciso in quella direzione |
| Immagini dei documenti | no | Le serve il browser con la sessione, non il modello |
| Pannello di amministrazione | **no** | Scrive permessi e configurazione: deve restare guidato da una persona |
| Indicizzazione | **no** | Nessun modello deve poter far ripartire una lettura |
| Gate | **no** | È la cosa che fa rispettare le regole: non può essere uno strumento che il modello sceglie se chiamare |

Regola riassuntiva: **MCP per leggere e per i sistemi esterni; mai per ciò che
cambia la configurazione o applica i permessi.**

---

## 6. Il progetto, per fasi

Ogni fase finisce con una misura. Le fasi 1-3 sono piccole e potrebbero rendere
la 4 e la 5 molto meno necessarie: vanno fatte prima anche per questo.

### Fase 0 — il metro *(blocca tutto il resto)*

25-30 domande vere, come le fanno i commerciali, con la risposta attesa. Alcune
**senza risposta** nei documenti. Misura: quante volte il pezzo giusto entra
nei primi 8, e quante volte l'assistente dice correttamente «non c'è».

Serve l'azienda: le domande non me le posso inventare, o taro tutto su casi che
il sistema già gestisce. È il vero collo di bottiglia del progetto, non la GPU.

### Fase 1 — riscrittura sempre attiva

`riformula.py` esiste e oggi riscrive solo per sciogliere i pronomi. Farla
riscrivere **sempre**, in registro di catalogo. Misurato: posizione 29 → 4, una
chiamata, 7,7 s.

### Fase 2 — dare la pagina, non il frammento

`DST2001 rot red` da solo è muto; la pagina dice DEKOSTEINE pietre decorative.
È l'altra metà del vantaggio che avevo io a mano, e non costa nessuna chiamata
in più. Da misurare.

### Fase 3 — reranker come servizio vero

`bge-reranker-v2-m3` è già scaricato, in formato GGUF. Va servito da
`ghcr.io/ggml-org/llama.cpp:server-cuda` come servizio di `docker-compose.yml`,
con la cartella dei modelli montata in sola lettura. Misurato: 2,6 s su 40
brani, discrimina bene. *Non* usare i binari interni di LM Studio.

Lo stesso servizio serve anche le **decisioni tipizzate con probabilità** del
§7, che LM Studio non sa dare (`logprobs: null`): un motore in più, non due.

### Fase 4 — il ciclo

Cerca → giudica → riprova, limitato, con risposte vincolate e un passo di
verifica separato. È il primo vero agente. Da fare **dopo** la fase 0, perché
senza metro non si sa se migliora o peggiora.

### Fase 5 — subagenti per fonte

Parallelismo e confine dei permessi nello stesso disegno. Abilita il confronto
fra cataloghi.

### Fase 6 — sessione e memoria per utente

I punti 3a e 3b. La 3c (autonomia in assenza) è un progetto a sé e non entra
qui.

### Fase 7 — MCP dove ha senso

Secondo la tabella del §5, partendo dal connettore ERP che è già specificato.

---

## 7. Decisioni tipizzate invece di prosa — e rizzo-flow

Valutato il 21/09/2026 su richiesta: <https://github.com/Rizzo-AI-Academy/rizzo-flow>
(Apache 2.0, Python ≥ 3.11, runtime MLX, modelli Spark-X2.5).

**Cosa fa.** Non fa scrivere testo al modello: legge la probabilità di una
risposta **vincolata** e restituisce una decisione tipizzata — `{"choice":
"billing", "probabilities": {...}}` — in ~250 ms, su API HTTP (porta 8017).
Non è un framework di agenti: non fa RAG, non ha subagenti, non parla MCP. È
uno strumento specializzato per **il passo di giudizio**.

**Perché è pertinente.** Colpisce esattamente il punto debole del §2: il
modello come giudice di pertinenza sbaglia e non si ferma. E ha una cosa che
vale la pena rubare a prescindere dal progetto: **l'astensione come esito di
prima classe** (`__insufficient__` → `insufficient_evidence`, `uncertain`,
`out_of_range`). È la risposta al rischio del §4, «non lo so deve restare una
risposta possibile»: lì l'astensione non è un caso limite, è un valore di
ritorno previsto.

È anche provato su hardware nostro: *"tested on Windows 10 with RTX 5060 Ti
16 GB"*, e non richiede il toolkit CUDA installato.

### La misura che decide

Provato a ottenere la stessa cosa con il modello che c'è già, vincolando la
risposta a una lettera invece che a prosa libera:

| | |
|---|---|
| Giudizio a prosa libera | 17,8 s — e sceglie *pompon marrone* |
| Giudizio vincolato, stesso modello | **1,1 s** |

Un ordine di grandezza, senza installare niente. **Ma le probabilità non
arrivano**: LM Studio risponde `logprobs: null`, e con `max_tokens` basso il
contenuto esce vuoto perché i token se li prende il ragionamento (è lo stesso
motivo per cui esiste `SUFFISSO_SISTEMA=/no_think`).

**llama.cpp i logprobs li restituisce**, e llama.cpp serve già per il reranker
della fase 3. Un servizio solo darebbe rerank **e** decisioni tipizzate con
probabilità.

### Parere

**L'idea sì, la dipendenza no — non adesso.** Quattro ragioni:

1. **Maturità**: 47 stelle, 21 commit, un solo autore. Per un sistema che deve
   vivere anni in azienda è una dipendenza che può sparire. La licenza Apache
   permette di copiarlo in casa, il che attenua ma non annulla.
2. **VRAM**: +5 GiB (4B a 8 bit) o +3,4 GiB (1.7B). Oggi 7,9 GiB di modelli più
   6,1 di picco Docling fanno 14 su 16,3: non ci sta mentre legge.
3. **La probabilità va tarata.** Lo dice il loro README: le distribuzioni
   escono *"extremely peaked (0.9999)"* e le soglie pensate per Jev non si
   trasferiscono. Taratura su dati etichettati nostri — cioè, di nuovo, la
   fase 0.
4. **Sarebbe il terzo motore di inferenza** sulla stessa macchina, accanto a
   LM Studio e al llama.cpp della fase 3.

### Cosa si prende comunque

Tre cose, da realizzare con llama.cpp nella fase 3, senza dipendenze nuove:

- **Decisione tipizzata, non prosa.** Già nel §4 fra le difese del ciclo; qui
  c'è il numero che lo giustifica (1,1 s contro 17,8 s).
- **L'astensione come valore di ritorno**, non come caso limite.
- **La soglia tarata su dati veri**, invece di fidarsi della probabilità
  grezza. Vale per qualunque modello, non solo per il loro.

Da riguardare se il progetto matura, o se l'esperimento con llama.cpp non
regge.

---

## 8. Cosa non farei

- **Non trasformerei tutto in MCP.** Non aggiunge indipendenza e toglie
  controllo dove serve di più.
- **Non partirei dalla fase 4.** Le fasi 1-3 costano poco e si misurano subito;
  se bastano, l'agente è complessità che non serve mantenere.
- **Non costruirei l'autonomia in assenza** insieme al resto. Ha bisogno di un
  suo disegno su registro, limiti e revoca.
- **Non toglierei la catena fissa.** Se l'agente non converge entro N passi,
  deve poter ripiegare sul comportamento di oggi, che una risposta la dà.
