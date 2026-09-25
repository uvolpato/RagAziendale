# JEV — modello di valutazione per il rerank

**In una riga:** JEV è un modello di **valutazione** (non chat, non reranker
classico) che giudica un testo contro domande "tipizzate" e risponde con scelte,
punteggi e probabilità booleane. L'idea è usarlo **al posto del reranker BGE**
come "giudice" di pertinenza.

## Cos'è

- **Nome/modello:** `typesafe-ai/jev` — TypeSafe AI, modello "System One".
- **Tipo:** evaluation (classificazione, routing, rubric assessment, verifica).
- **Contesto:** 32.000 token. **Output:** 0 token liberi — risponde in forma
  strutturata (scelta / punteggio / booleano), non in prosa.
- **Prezzo:** $0,042 per 1M token in ingresso (economico).
- **Dove gira:** cloud — Vercel AI Gateway, oppure Typesafe / DigitalOcean.
  NON è un modello locale: serve una chiamata API esterna.

## Come funziona (il pattern)

```ts
const result = await evaluate({
  model: 'typesafe-ai/jev',
  state: 'The support agent issued a full refund to the customer.',
  questions: {
    refunded: { type: 'boolean', instructions: 'Was a refund issued?' },
  },
})
```

Un **`state`** (il testo da valutare) + **`questions`** tipizzate (boolean,
score, choice) → JEV risponde con il verdetto. Più domande in parallelo in una
sola richiesta.

## Perché ci interessa (idea di rerank)

Il rerank attuale è `bge-reranker-v2-m3`: un cross-encoder che fa un coseno fra
domanda e pezzo. L'idea è sostituirlo (o affiancarlo) con **JEV come giudice**:

- per ogni pezzo candidato: "questo pezzo risponde alla domanda?" → booleano o
  punteggio;
- si usano i verdetti come punteggio di rerank, al posto del coseno BGE.

È la via **LLM-as-judge**: un modello che *capisce* la domanda e il pezzo, invece
di misurarne solo la somiglianza. Potrebbe colmare proprio il gap multilingue /
concettuale che il coseno non vede («sassi» → «deco rocks»).

## Tradeoff e punti aperti

| Vantaggio | Rischio / costo |
|---|---|
| giudica con comprensione, non con coseno | **cloud**: latenza di rete + dipendenza esterna (da dichiarare in egress) |
| economico ($0,042/1M) | JEV è "System One" (veloce, piccolo): **non è detto conosca il dominio** it/de/en delle decorazioni |
| output strutturato, facile da usare | 40–150 pezzi × domanda = tante richieste: va misurata la latenza totale |
| più domande in parallelo in una richiesta | formato `evaluate` non standard: serve un adattatore OpenAI-compatible |

## Da verificare prima di adottarlo

1. JEV capisce davvero il dominio (decorazioni, it/de/en)? Test sui casi che il
   coseno sbaglia («sassi rossi» → «deco rocks»).
2. Latenza su 150 pezzi per domanda (il rerank oggi è ~2,6 s locale).
3. Endpoint esatto via Vercel AI Gateway e formato della risposta (JSON schema).
4. Egress: host esterno da aggiungere alla allowlist.

## Fonte

https://vercel.com/ai-gateway/models/jev — https://typesafe.ai/blog/introducing-system-one-models-and-jev
