# Architettura: come un documento diventa una risposta

Due catene, che si toccano solo nel database.

```
   CARTELLA                                          CHAT
      │                                                │
      │  ingestion/indicizza.py                        │  orchestratore/main.py
      ▼                                                ▼
   leggere ──► spezzare ──► vettori ──► [ POSTGRES ] ──► cercare ──► gate ──► rispondere
```

Chi legge non sa niente di chi risponde, e viceversa. Fra i due c'è Postgres:
tre tabelle (`chunks`, `immagini`, `lessemi`) e la tabella dei permessi
(`sources`). Tutto quello che segue è il dettaglio di quelle due frecce.

I numeri citati sono misurati, non stimati, e stanno in
[valutazione/RISULTATI.md](valutazione/RISULTATI.md) con la data. I prompt
sono in [PROMPT.md](PROMPT.md), generati dal codice.

---

## 1. La lettura: da un PDF a dei pezzi di testo

`ingestion/indicizza.py`, un giro ogni 5 minuti. File nuovi e cambiati
entrano, i cancellati escono, gli illeggibili diventano anomalie del pannello.

I PDF si leggono a **blocchi di 6 pagine**, ognuno in un processo figlio che
poi muore: Docling non restituisce la memoria e su un catalogo da 107 pagine
il processo arrivava al tetto di 4 GB.

### Due modi di leggere, e non è una preferenza

| | `LETTURA=docling` (predefinito) | `LETTURA=pagina` |
|---|---|---|
| chi legge il testo | Docling: layout, tabelle, OCR | un VLM che **guarda** la pagina |
| va bene per | prosa: manuali, procedure, normative | cataloghi a griglia |
| chi estrae le immagini | Docling | Docling, in entrambi i casi |

La ragione è misurata. Su EUROSAND pagina 7 Docling trovava **zero tabelle e
nessun titolo**: una griglia di prodotti con le foto non ha, per lui, la forma
di una tabella. Il VLM la legge come la legge una persona. Al contrario, sui
documenti di prosa il VLM fa peggio, e il prompt «catalogo» imporrebbe una
griglia che lì non esiste.

Oggi è una variabile per fonte (`LETTURA_PAGINA`); diventerà un'impostazione
del pannello con la **D16**.

### Le figure vanno dove stavano le figure

Questa è la parte che è cambiata il 22/09/2026, ed è il cuore del percorso a
pagina:

1. Docling converte il blocco → le immagini **e** il Markdown di ogni pagina,
   con i segnaposto `<!-- image -->` nel punto giusto.
2. Si scarica il VLM per lasciare la VRAM a Docling; poi lo si riaccende.
3. Il VLM descrive ogni figura **sapendo il titolo della pagina** da cui
   viene. Senza contesto, un ritaglio di 221×149 px di sassi rossi diventa
   «possibly dried fruit or processed food».
4. Ogni descrizione prende il posto del suo segnaposto.
5. Quel Markdown si salva in `_sorgenti/<hash>/markdown-figure/NNNN.md` e si
   spezza con lo stesso chunker.

**Perché conta**: staccata, una descrizione è un pezzo che dice «sfere di
acciaio lucido» e nient'altro — nessun codice, nessun prezzo, nessun formato.
Per entrare negli otto posti deve battere i pezzi che quei dati ce li hanno, e
perde. Messa dove stava la figura, i due pezzi diventano uno. Misurato:
17/20 → 18/20.

Il file su disco **è** quello che finisce nell'indice. Quando una risposta
sarà sbagliata si apre quel file, invece di dedurre.

### Come si spezza

`_pezzi_da_markdown` segue la **struttura** invece di indovinarla: le
intestazioni fanno da contesto, le righe di tabella e le voci di elenco
diventano pezzi distinti con quel contesto davanti.

Prima c'era un'espressione regolare sul codice articolo. Funzionava su un
catalogo e si rompeva sul successivo — i codici a una lettera dei formati non
li vedeva. Una regola che vale per un documento solo è un debito, non una
soluzione.

### Cosa si salva, e cosa si ricalcola

| | dove | quando si rifà |
|---|---|---|
| Markdown del VLM | `_sorgenti/<hash>/markdown-<impronta>/` | se cambia il PDF o il prompt |
| immagini | `_sorgenti/<hash>/immagini/` | a ogni rilettura |
| Markdown con le descrizioni | `_sorgenti/<hash>/markdown-figure/` | a ogni rilettura |
| frequenze delle parole | tabella `lessemi` | a fine giro, se qualcosa è cambiato |

L'impronta nel nome della cartella è l'hash del prompt: **cambiare una virgola
nel prompt di lettura vuol dire rileggere tutte le pagine**, mezz'ora per
catalogo. È voluto — un indice metà con un prompt e metà con un altro è
peggio di un indice vecchio — ma va saputo prima di toccarlo.

---

## 2. L'indice

```sql
chunks   (source_id, documento, page, content, embedding vector(1024))
immagini (source_id, documento, page, percorso, descrizione, embedding)
lessemi  (parola, pezzi)          -- in quanti pezzi compare ogni parola
sources  (acl_groups[], aziende[], stato, residency)
```

`sources` è **l'unica casa del dato di sicurezza**. Niente è duplicato sui
pezzi, quindi non può disallinearsi: sospendere una fonte la toglie dalle
risposte nello stesso istante, senza reindicizzare.

I vettori sono a 1024 dimensioni perché li fa bge-m3. Cambiare modello di
embedding significa **rifare tutto l'indice**, e il sistema se ne accorge da
solo: se il modello non corrisponde, i vettori si fermano con un'anomalia
critica invece di mescolare due spazi diversi.

---

## 3. La ricerca

`orchestratore/recupero.py`. Due rami che si fondono, più un riordino.

```
domanda ─┬─► vettoriale (bge-m3, coseno)      ─┐
         │                                     ├─► RRF ─► cross-encoder ─► 8 pezzi
         └─► lessicale (solo i termini RARI)  ─┘
                   ▲
                   └── il filtro ACL è QUI, nella WHERE, in tutti e due i rami
```

### Il filtro sta nella query

Gruppi ∩ aziende ∩ `stato = 'attiva'`, dentro l'SQL. **Mai nel prompt.** Un
prompt si convince, una `WHERE` no. Un pezzo che non è consentito non può
comparire, qualunque cosa dica il modello — e infatti il rerank, che riordina,
non può far entrare niente che la query non avesse già lasciato passare.

### Il ramo lessicale cerca solo le parole rare

Fino al 22/09/2026 usava `plainto_tsquery`, che mette i termini in **AND**:
«ciottoli neri lucidi» pretendeva tutte e tre le parole nello stesso pezzo, e
in tutto l'indice non ce n'era nessuno. Per mesi, su ogni domanda di più di
una parola, il ramo ha restituito l'insieme vuoto: **l'ibrida era vettoriale
travestita**, e si vedeva nei numeri senza che nessuno li leggesse così
(«vettore 14/20, fusione 14/20» erano lo stesso numero perché erano la stessa
ricerca).

Oggi pesa ogni parola per quanto è **rara** (IDF: `ln(totale / in quanti pezzi
compare)`) e cerca solo quelle sotto un pezzo su mille — cioè i codici
articolo e i nomi propri. Su una domanda normale in italiano nessuna parola ci
arriva, quindi il ramo **tace** e resta la sola ricerca vettoriale, che lì fa
meglio da solo.

Con una eccezione di sostanza: in modalità degradata (host dei modelli spento)
il lessicale è l'**unica** ricerca, e allora cerca tutti i termini. Tacere
vorrebbe dire non rispondere.

I conteggi non sono un elenco di parole da ignorare scritto a mano: li conta
l'archivio. Su un archivio di ricette «forno» sarebbe comune e «bergamotto»
raro, senza toccare niente.

### Il riordino

Il cross-encoder (bge-reranker-v2-m3) guarda domanda e pezzo **insieme**, su
150 candidati. È quello che ha portato il recupero da 11/20 a 17/20: i due
rami mettono il materiale giusto nell'indice ma si fanno concorrenza per gli
stessi otto posti, e senza rerank le risposte giuste stavano in posizione 27,
45 e 21 — dentro l'indice e fuori dalla finestra.

### Cosa è stato provato e buttato

| | esito |
|---|---|
| riscrivere la domanda in termini di catalogo | bocciata **quattro volte**; inventa attributi («Marrakesch» → «sfere in resina», che sono di metallo) |
| `OR` al posto dell'`AND` nel lessicale | neutro, e peggiore sui codici: `ts_rank_cd` non pesa la rarità |
| agente che cerca più volte | 14/20 contro 14/20, a 11,7 s contro 1,1 s |

Stanno qui perché una cosa provata e scartata è informazione: senza questa
riga qualcuno la riprova fra sei mesi.

---

## 4. Il gate

`orchestratore/gate.py`, fra la ricerca e il prompt. Due cose:

- **contaminazione**: se fra i pezzi c'è una fonte `interno`, il turno diventa
  interno e resta interno. Se la rotta interna non è configurata, si **rifiuta**
  di rispondere invece di rispondere da una rotta qualsiasi.
- **rotta**: quale modello. Non lo sceglie l'utente — l'endpoint espone un
  modello solo.

---

## 5. La risposta

`prompt.SYSTEM` + il CONTESTO numerato + la conversazione. Il contesto è
dichiarato anche quando è vuoto (`CONTESTO: nessun documento pertinente
trovato`): il modello deve sapere di non avere materiale, non trovarsi il
campo vuoto.

Le figure delle pagine che hanno risposto si aggiungono in coda, come
miniature firmate. Le scrive il **sistema**, non il modello: la firma dell'URL
è quello che permette al browser di scaricare un'immagine senza mandare
nessuna identità.

**La regola che mancava.** Fino al 22/09/2026 le otto regole del prompt erano
tutte divieti — non inventare, non produrre numeri, non ripetere. Nessuna
diceva *quanto* riportare, e il risultato si misura: dei 26 dati che
rispondevano alla domanda e stavano nel contesto, le risposte ne riportavano
12 (46%), quasi sempre in **una riga**. Una riga aggiunta, e sono 19 (73%).

---

## 6. I metri

Non c'è un metro solo, perché non c'è un modo solo di sbagliare.

| metro | cosa misura | oggi |
|---|---|---|
| `misura.py` | la pagina giusta è fra gli 8 pezzi? domanda lunga e **corta** | 18/20 · **14/20** |
| `risposte.py` | dei dati arrivati al modello, quanti ne riporta | **73%** |
| `codici.py` | chiedere un articolo per codice | 10/12 · 10/12 · 11/12 |
| `ablazione.py` | quale pezzo della catena porta il risultato | vettore 14 · fusione 14 · completa 14 |
| `perche-falliscono.py` | il pezzo è assente, lontano o vicino? | tre diagnosi, tre correzioni diverse |
| `agente.py` | cercare più volte conviene? | no: 14/20 a 11,7 s |

### Due lezioni che valgono più dei numeri

**Il metro misurava il caso facile.** Le 24 domande fornite dall'azienda sono
lunghe e ben formate. In chat si scrivono tre parole. Il 18/20 non diceva
niente sulle domande vere, e infatti una chat reale ha risposto con una riga
mentre il metro segnava 90%. La colonna `corta` esiste da allora.

**Un metro non ripetibile non è un metro.** A temperatura 0,2 due corse sullo
stesso contesto davano 19 riscontri e 16: con quel rumore un miglioramento e
una fluttuazione sono indistinguibili. `risposte.py` misura a temperatura 0.

---

## 7. I modelli

Tutti **fuori da Docker**, su llama-swap (`modelli/llama-swap.yaml`), porta
1235. Docker su Windows tiene per sé la memoria che gli dai, e i modelli
dentro un container facevano sedere la macchina.

| | cosa fa | VRAM | gruppo |
|---|---|---|---|
| bge-m3 | vettori | 0,33 GB | `piccoli`, **persistente** |
| bge-reranker-v2-m3 | riordino | 0,46 GB | `piccoli`, **persistente** |
| Qwen3 8B | chat | 5,5 GB | `grandi` |
| Qwen3-VL 4B | legge pagine e figure | 4,3 GB | `grandi` |
| Qwen3.8 27B | confronto | 12 GB | `confronto`, **esclusivo** |

`persistent` non è una preferenza: embedding e rerank servono a **ogni**
domanda e pesano 0,79 GB in due. Scaricarli per far posto a un modello grande
sarebbe un pessimo affare. Per questo si può provare il 27B senza toccare
niente: è esclusivo verso i grandi, non verso i piccoli, e la ricerca continua
a funzionare mentre lui risponde.

Chi parla con chi: l'orchestratore e l'ingestion passano da **LiteLLM**, per
nome logico (`ragionamento`, `embedding`). Il modello vero si cambia in
`litellm-config.yaml`, in un punto solo.

---

## 8. Le righe che il progetto non attraversa

1. **I permessi non passano dal prompt.** Mai. Sono nella `WHERE`.
2. **Il modello non sceglie dove cercare.** Sceglie le parole. Se scegliesse
   le fonti, il filtro ACL diventerebbe una convinzione.
3. **I numeri non li produce il modello.** Prezzi, quantità, date e misure
   vengono dal contesto o non si scrivono.
4. **Il contesto non è un'istruzione.** È materiale informativo, e il prompt
   lo dice.
5. **Niente correzioni tarate su un documento.** Una regola che vale per un
   catalogo e si rompe sul successivo non è una soluzione: è il prossimo
   guasto, con più codice attorno.

---

## 9. Cosa manca, e si sa

- **Il ponte di vocabolario.** «Ciottoli neri lucidi» deve arrivare a
  SPIEGELSAND, *mirror sand*. La riscrittura è bocciata, l'agente pure. Aperto.
- **La lettura dei loghi.** Una dichiarazione che è un'immagine non entra
  nell'indice (domanda 16). Non è ricerca, è lettura.
- **Le 4 domande senza risposta** non sono mai state misurate: servono a
  verificare che il sistema dica «non c'è», e quel metro non esiste.
- **I prompt stanno nel codice** (**D18**): ogni prova costa una ricostruzione
  dell'immagine.
- **Le impostazioni valgono per tutte le fonti** (**D16**).
