# Sintesi — deciso, provato, riprovato, scartato (sessione 24/09/2026)

> Riassunto di ciò che questa sessione ha deciso e di ciò che è stato provato,
> misurato e scartato. Le decisioni definitive vanno poi registrate nel
> `PROGETTO-RAG-Aziendale.md` §12 e in `DECISIONI-APERTE.md`.

---

## 1. La decisione di fondo (cambia la direzione)

**I cataloghi non si estraggono in struttura: si indicano i punti.**

- **Catalogo (PDF)** = trovare e indicare *dove* sta ciò che si cerca — le
  pagine, le figure — esattamente come un testo ("di cosa parla") o un manuale
  ("come si fa"). Alla domanda "nastri blu" la risposta giusta è *"guarda qui,
  qui e qui"* con le immagini, **non** un codice.
- **Codici** = anagrafica articoli (ERP), un'altra storia: il connettore è già
  specificato in `SPECIFICA-CONNETTORI.md`. Non si estrae alcun dato strutturato
  dal catalogo.

Questa decisione **corregge** la conclusione di
`VALUTAZIONE-ORCHESTRATORE-AGENTE.md` §9, che indicava come direzione di lungo
periodo "estrarre la struttura (prodotto → codice → formato → prezzo)". Quella
direzione è **abbandonata**: era la risposta a un malinteso su cosa l'utente
vuole dall'assistente.

**Le altre due decisioni:**

- **Agente** = **LangGraph** (framework MIT, maturo), non un ciclo custom. Era
  già la scelta del progetto (decisione #10, `PROGETTO-RAG-Aziendale.md` §12) e
  la fase 5 di `VALUTAZIONE-ORCHESTRATORE-AGENTE.md`. Gli strumenti restano le
  funzioni esistenti di `recupero.py` (`cerca`, `cerca_esatta`, `pagina`,
  `immagini_pertinenti`, `documenti_visibili`), esposte come tool.
- **Modello** = **27B locale** (`qwen3.8-27b-gsq-rco` su llama-swap) con
  **escalation a un'API a pagamento** solo quando il 27B fallisce in modo
  dimostrato (risposta vuota, loop, malformata). L'agente passa sempre da un
  router che decide quale cervello usare.

---

## 2. Provato, riprovato, scartato (in ordine)

| # | Cosa | Esito | Verdetto |
|---|---|---|---|
| 1 | Prompt lettura pagina **"catalogo"** (imponeva una tabella con codice/colore/prezzo) | Su pagine-foto inventava tabelle e codici (N12345…) su immagini senza testo | **Scartato** → sostituito dal prompt neutro (niente struttura imposta) |
| 2 | Prompt lettura pagina **neutro** (trascrivi ciò che vedi, non inventare, non ripetere) | Pagina 34/64: "Immagine + descrizione pacco regalo", niente tabelle inventate. Resta un lieve eco di ripetizione sul VLM 4B | **Adottato** |
| 3 | Prompt figura con colori aggregati (`Colours:`) | Produce i colori, ma non distingue **tinta unita** da **fantasia**: i pieni sono solo "smooth texture", la parola "solid/plain" non compare mai | **Da correggere**: aggiungere esplicitamente "solid/plain" vs "printed" |
| 4 | `/no_think` per spegnere il ragionamento | Funziona sull'8B; **non funziona sul 27B** (variante "rco" = reasoning sempre attivo) | **Da gestire**: sopprimere `reasoning_content` nello stream + budget token |
| 5 | Tabella **sinonimi** multilingue (D20) | Ponte linguistico vero ("sassi"→"pebbles, kiesel"), ma non risolve il problema di fondo | Resta come fonte, non è la soluzione |
| 6 | **Vincoli must-match** (D19) su colore/misura/prezzo | Funziona per colore ("nastri blu" trova le pagine giuste), ma "senza motivi" non è un attributo enumerabile | Resta; non copre "tinta unita" |
| 7 | Agente **via unica + routing 4 vie** (D22) | Misurato: 14/20 come la ricerca sola, costo ×10 | **Spento** |
| 8 | Agente **"cerca-guarda-riprova"** (D17, `ricerca_agente.py`) | 14/20, costo ×10, zero guadagno sulle 20 domande d'oro | **Spento** (surrogato, non agente vero) |
| 9 | INGE fuori dalla ricerca | 1434 chunk + 2612 immagini rimossi; file invariato → non re-indicizzato | **Fatto**, in attesa di test |

---

## 3. Fatti verificati (questa sessione)

- **Il 27B risponde** correttamente e fa **tool-calling corretto** (alla richiesta
  "nastri blu" restituisce `cerca_catalogo {"query":"nastri blu","colore":"blu"}`).
- **Il 27B è la variante "rco"** = emette sempre `reasoning_content` prima del
  `content`; con budget token piccolo il `content` resta vuoto.
- **Il recupero trova già** le pagine giuste per "nastri blu" (EVERYDAY p19 con
  `79 | blu / blue`). Il difetto è **a valle**: il modello riassume male e
  inventa "non esistono".
- **Il colore è staccato dal codice**: la descrizione della figura dice
  `Colours: blue, Code: n/a` (o "Packara" al posto del codice), mentre la tabella
  ha `79 | blu / blue`. Sono due chunk separati.
- **"senza motivi" non è cercabile**: il set di attributi è
  `{colore, misura, formato, prezzo}`; "tinta unita / fantasia" non esiste nel
  modello dati, e i nastri pieni non sono mai marcati "solid".
- **Reindex dei 4 Packara completato** (BOW, CELEBRATION, EVERYDAY, SPRING-SUMMER,
  exit 0) con il prompt pagina neutro.
- **LangGraph** è presente nel container (0.6.8) ma era stato tolto da
  `requirements.txt`; **ripristinato** (`langgraph==0.6.8`).

---

## 4. La spiegazione di fondo (perché "non migliora")

Tre cause, nessuna risolvibile da un solo modello:

1. **Il cervello è piccolo** (8B): va in loop, perde il testo nel ragionamento.
2. **I dati sono spezzati**: colore e codice in chunk diversi, "solid" mai scritto.
3. **Il sistema inventa invece di dire "non so"**.

L'equazione è: **risposta = cervello × strumenti × dati**. Il 27B + escalation
sistema il cervello; l'agente (LangGraph) dà gli strumenti; l'ingestion mette i
dati (scrivere "solid/plain", descrivere onestamente la figura). Nessuna delle tre
da sola basta.

---

## 5. Prossimi passi

1. Collegare il 27B (`MODELLO_RAGIONAMENTO` nel `.env`) e gestire
   `reasoning_content` nello stream + budget token.
2. Correggere il prompt-figura: dire esplicitamente "solid/plain" vs "printed".
3. Costruire l'agente LangGraph (strumenti `recupero.*`, ciclo, subagenti per
   fonte) con router 27B → API.
4. Ritestare "nastri blu" / "sassi rossi" e misurare.
