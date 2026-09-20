# Prestazioni dell'indicizzazione — misure e piano

Misurato il 20/09/2026 sulla macchina di sviluppo (RTX 5060 Ti 16 GB, container
con tetto di 4 GB di RAM), leggendo cataloghi prodotto veri. Serve a decidere
**dove** ottimizzare, non a dire che il sistema è lento: un documento che ieri
era «non leggibile» oggi entra in dieci minuti.

## 1. Il punto di partenza

| Misura | Valore |
|---|---|
| CATALOGO IPURO: 41 pagine, 9,4 MB, 324 immagini | 524-594 s → **~13 s/pagina** |
| EUROSAND: 107 pagine, 59 MB | **~9-10 s/pagina** |
| Stesso blocco di 6 pagine letto su CPU invece che su GPU | ~135 s/pagina — **13× più lento** |
| Picco di memoria per blocco (dopo la scrittura delle immagini blocco per blocco) | 2,5 GB su 4 |
| Memoria fra un blocco e l'altro | 250-870 MB |
| Occupazione della GPU durante l'indicizzazione | 78% |
| VRAM di Docling | 2,1-2,7 GB |

Il salto vero è già stato fatto: **GPU invece di CPU**. Il resto sono margini.

## 2. Dove va il tempo — ipotesi, non certezze

Due sospetti, in ordine di peso presunto:

1. **Le descrizioni delle immagini.** Su un catalogo ci sono ~8 immagini per
   pagina e ognuna passa a glm-ocr via HTTP **una alla volta**
   (`concurrency=1`), fra 0,2 e 2,3 s l'una: potenzialmente 8-15 s per pagina,
   cioè la maggior parte del tempo.
2. **L'OCR su CPU.** Tesseract gira sul processore mentre la GPU aspetta, e nei
   log compaiono molti tentativi falliti di riconoscimento dell'orientamento
   (`OSD failed`) su pagine che sono immagini.

**Nessuna delle due è dimostrata.** Prima di ottimizzare serve un giro con i
tempi separati per fase — resa del PDF, layout, tabelle, OCR, descrizioni,
embedding — su una decina di pagine di catalogo: ~20 righe di strumentazione.
Senza quella misura si rischia di accelerare la parte sbagliata: se l'OCR fosse
il 70% del tempo, parallelizzare le descrizioni sposterebbe poco.

## 3. Interventi software, in ordine di resa attesa

| # | Intervento | Resa attesa | Costo | Rischio |
|---|---|---|---|---|
| 1 | **Descrizioni in parallelo**: `concurrency` da 1 a 4. glm-ocr su LM Studio ha già `PARALLEL 4`, quindi la capacità c'è e non costa VRAM | grossa sui cataloghi | una riga | basso |
| 2 | **Non descrivere le immagini decorative**: oggi la soglia è il 5% dell'area della pagina, e infatti si descrivono anche loghi e cornici (324 immagini su 41 pagine). Al 15% si dimezzano le chiamate perdendo pochissimo per la ricerca | grossa | un parametro | basso |
| 3 | **OCR sulla GPU**: RapidOCR (ONNX) al posto di Tesseract. I modelli si scaricano quando si costruisce l'immagine, quindi la regola «niente rete a regime» resta rispettata | media-grossa sui PDF scansionati, nulla sui nativi | mezza giornata | medio: va confrontata la qualità sull'italiano prima di adottarlo |
| 4 | **Blocchi più grandi**: i 6 pagine servivano quando le immagini si accumulavano in memoria; ora il picco non dipende più dalla lunghezza del file. A 12 pagine si dimezzano i caricamenti dei modelli (qualche secondo per blocco) | piccola | un parametro | basso: verificare che il picco resti sotto i 4 GB |
| 5 | **Due file in parallelo** (vincoli in `DECISIONI-APERTE.md` D16, punto 12: stessa fonte, stesse impostazioni, VRAM sufficiente): con 6,3 GB liberi ci stanno due conversioni da 2,5 | fino al doppio, **solo se** la GPU non è già satura | alto | alto: tocca il lock che garantisce «un giro alla volta» |

L'ordine consigliato è 1 e 2 (mezza giornata, nessun costo), poi la misura del
§2 per decidere se vale il 3. Il 5 per ultimo, se mai.

## 4. Hardware

| Opzione | Costo indicativo | Cosa risolve |
|---|---|---|
| **Seconda GPU dedicata** (usata: 3060 12 GB, 4060 Ti 16 GB) | 250-450 € | La resa migliore per euro. L'indicizzazione smette di contendere la VRAM alla chat: niente finestra notturna, niente soglia `VRAM_MINIMA_MB`, docling-serve sempre acceso con i modelli già caricati (si risparmiano anche i caricamenti per blocco). Serve uno slot PCIe libero e un alimentatore adeguato. **Renderebbe inutile metà della complessità scritta per farli convivere** (decisione 73) |
| **CPU con più core** | — | Resa delle pagine, OCR e scrittura dei PNG sono tutti su CPU: è la parte che la GPU aspetta. Da valutare sul processore reale della macchina |
| **RAM di sistema** | 100-200 € | Oggi il container ha un tetto di 4 GB perché la VM di Docker ne ha pochi: con più RAM si sale a 8 GB e si possono usare blocchi più grandi (§3.4) |
| **GPU singola più grande** (3090 24 GB usata, 5090 32 GB) | 700-2.500 € | Fa convivere tutto con abbondanza e serve **anche** al modello di chat in produzione con 5 utenti in parallelo. Se però l'obiettivo è la sola indicizzazione, la seconda GPU piccola rende di più |
| NVMe più veloce | — | Ininfluente: si scrivono ~25 MB di immagini per catalogo |

## 5. La domanda che decide se vale la pena

**Questi cataloghi da 60-227 MB sono un'eccezione o normale amministrazione?**
Se il corpus è fatto soprattutto di procedure e policy da 1-2 MB,
l'indicizzazione è già abbastanza veloce e ottimizzarla sarebbe tempo speso
male: meglio spenderlo sulla qualità delle risposte. Se invece i cataloghi
arrivano di continuo, il §3.1 e §3.2 si fanno subito e la seconda GPU diventa
un acquisto sensato.
