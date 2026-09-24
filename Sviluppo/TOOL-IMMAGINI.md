# Tool immagini: le figure degli articoli trovati, non della domanda

> Stato: proposto e implementato il 23/09/2026. Da verificare con il banco di
> prova prima di dichiararlo risolto.

## Il problema

Oggi le immagini si scelgono per **somiglianza vettoriale alla domanda**
(`recupero.immagini_pertinenti`): si embeddano le descrizioni delle figure e si
cerca la domanda nello stesso spazio. Funziona per le domande che *descrivono*
una figura («sassi rossi»), e sbaglia quando la domanda è un **articolo preciso**
o una categoria: su «profumatori per auto» il contesto testo risponde da IPURO
p.8, ma le figure che escono sono «cosmetics concept» e «list of ingredients» —
dentro la pagina giusta, fuori tema rispetto al prodotto che la risposta cita.

La radice è la stessa vista per il testo: l'immagine si cerca **prima** di sapere
quali articoli hanno risposto, quindi si cerca la cosa sbagliata. L'ordine
giusto è invertito: prima si trovano gli **articoli**, poi si recuperano le
**loro** figure e si verifica che siano pertinenti.

## Il legame che esiste (misurato)

Le descrizioni delle immagini (migrazione 013) hanno la forma
`<testo del catalogo che precede la figura> — <descrizione del modello visivo>`.
La prima metà contiene spesso il **codice articolo**:

```
FSA1040 weiß white — nessun testo      → immagine della sabbia FSA1040
FLK2078 bernstein amber — …            → immagine del FLK2078
```

Misurato il 23/09/2026 sull'indice: 14755 immagini su 15115 hanno descrizione;
di queste, il match per codice copre solo una minoranza (i codici hanno forme
diverse: `FLK2004`, `700 002 156`, `B41G1`, `P1039`, `K0300` — un solo pattern
non li prende tutti, e su molte figure il codice sta altrove o non c'è). Quindi
il match per codice è un **acceleratore**, non la soluzione.

## Il design: due livelli

```
1. la ricerca trova gli articoli (chunk con codice, nome, colore, misura)
        │
2. match deterministico (SQL): immagine la cui descrizione contiene il codice
   dell'articolo. Se trovata → è la foto dell'articolo, fine (nessun modello).
        │
3. per gli articoli rimasti senza immagine: VERIFICA LLM (una chiamata sola)
   dato l'articolo (la sua descrizione testuale) e le immagini candidate
   (le figure della stessa pagina), scegli per ogni articolo la figura che lo
   raffigura, o nessuna.
```

### Perché una chiamata sola, non una per articolo

La scelta «una vs N» si pone solo al livello 3 (il match per codice non è una
chiamata, è SQL). Al livello 3 conviene **una chiamata sola**:

- **Latenza**: una chiamata = un giro del modello. N articoli = N giri. Su 16 GB
  condivisi e 4-5 utenti in parallelo, ogni giro conta.
- **Contesto**: il modello che vede «articolo X + le sue 5 candidate, articolo Y
  + le sue 4» decide meglio che a casi isolati: confronta e scarta in modo
  coerente.
- **Economia**: le istruzioni del prompt si pagano una volta, non N volte.

## I confini

- **Niente permessi nel tool.** Le immagini candidate arrivano già filtrate: le
  si pesca fra quelle delle pagine dei chunk ammessi, e il filtro ACL resta in
  `sources` (gruppi ∩ aziende ∩ attiva), come per il testo e per il serving.
- **Niente invenzioni.** Se la verifica LLM non trova una figura pertinente per
  un articolo, l'articolo resta senza figura. È preferibile una figura in meno
  che una figura sbagliata sotto una risposta vera.
- **Degrado sicuro.** Se il modello non risponde (host giù), si tiene il match
  per codice, e in assenza di quello il comportamento di oggi.

## Cosa misurare per dichiararlo risolto

Il banco di prova `valutazione/figure.py` già misura «la figura giusta esce» e
«quante figure escono / quante c'entrano». Il tool va misurato su quelle stesse
colonne: oggi 11/12 (codici) e 19/79% (quante c'entrano). Il guadagno atteso è
sulle domande *di articolo* e *di categoria*, dove oggi escono figure fuori tema
pur con la pagina giusta.
