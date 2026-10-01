"""La mappa dei dati: cosa c'è dentro, in che lingua, e come si cerca.

Il modello non può scrivere una buona query se non sa dove guarda. Fino a ora
gliene davamo la domanda e gli strumenti, e lui ci metteva dentro parole
italiane che nei cataloghi non ci sono: cercava «palloni da calcio» in un
campo dove si scrive «soccer balls» (misurato il 30/09/2026: 0 riscontri su
«pallon» e «fußball», il prodotto vero era a p.22 con «printed soccer
balls»). Non era un modello scarso: era un modello cieco.

Qui la mappa si ricava dai DATI, non dalla memoria di chi scrive il prompt:
i conteggi sono veri, il campione di descrizioni e' il testo che il modello
vedra davvero, e la lingua emerge dai dati («object:», «Material:»,
«heart», non «cuore»). Cambiando i cataloghi la mappa cambia con loro.

Non e' una regola che decide al posto del modello: e' l'indice di un
catalogo. Il modello legge, capisce e scrive la query, come farebbe io.
"""

def mappa_dati(termini_rari: str = "") -> str:
    """La mappa dei dati, da mettere nel prompt dell'agente.

    VOLUTAMENTE CORTA. La prima versione elencava a mano i sinonimi di
    quattro oggetti («diffusore» -> diffusor/duft/fragrance, «nastri» ->
    ribbon/tape...): sistemava le domande di quel giorno e falliva su tutte
    le altre, che e' il difetto che si voleva togliere. Elencare parole e'
    una scorciatoia: il vocabolario NON sta nel prompt, sta nei dati, e il
    modello lo scopre da solo se glielo si dice una volta. Solo quattro cose
    non sono deducibili da uno sguardo e quindi stanno qui: dove stanno i dati,
    che forma hanno, che lingua hanno, e cosa significa «zero righe».
    """
    return (
        "DOVE STANNO I DATI\n"
        "1. immagini — 15143 foto di catalogo: QUI c'e' il PRODOTTO (un nastro, "
        "un vaso, una pietra). Colonne: id, source_id, documento, page, "
        "descrizione. Tutto quello che vedi della foto e' dentro `descrizione`, "
        "un unico campo di testo: non esistono colonne `object`, `material`, "
        "`colours`. Il testo e' in INGLESE e comincia cosi':\n"
        "     object: white ribbon with printed soccer balls | material: fabric "
        "ribbon | shape/size: narrow rectangular strip\n"
        "   Altro campione VERO, stessa tabella:\n"
        "     Object: clip with football motif | Material: plastic | "
        "Shape/size: rectangular body, 1.5 cm x 1.5 cm; rounded football head\n"
        "2. chunks — 17582 pezzi di testo: QUI c'e' la DESCRIZIONE, la policy, "
        "il manuale, il nome di prodotto, il codice, il prezzo. Colonne: id, "
        "source_id, documento, page, content. `content` e' il testo originale, "
        "nelle sue lingue, senza etichette.\n"
        "3. documenti — le 14 fonti: nome file, tipo, stato, pezzi, "
        "figure_totali. Serve per le domande sui documenti stessi.\n"
        "4. indice — l'indice dei cataloghi (che cosa c'e' in ogni pagina). "
        "Colonne: documento, page, testo. Al momento e' VUOTA, quindi su questo "
        "non cercare: usa le altre tre.\n"
        "\n"
        "UNA tabella sola per query: i permessi si applicano a una tabella alla "
        "volta. Se ti servono due fonti, fai due chiamate.\n"
        "\n"
        "Metti SEMPRE `documento` e `page` nella SELECT: e' quello che citi alla "
        "persona. Ogni riga di `immagini` e' UNA FOTO, quindi la stessa pagina "
        "appare piu' volte se ha piu' prodotti: quando elenchi le pagine, "
        "raggruppale tu e non ripeterle.\n"
        "Metti nella SELECT anche `descrizione` (o `content`) se poi descrivi i "
        "prodotti: con le sole pagine non sai cosa c'e' davvero, e finisci per "
        "descrivere un prodotto che non hai guardato o per consigliare una "
        "pagina che non contiene quello che ti chiedono. Le descrizioni sono il "
        "tuo controllo: se tornano righe che non sono nastri quando ti "
        "chiedono nastri, la WHERE e' troppo larga (ti manca un termine) e va "
        "corretta.\n"
        "\n"
        "COME SI CERCA\n"
        "La domanda e' in italiano, i dati no (inglese, tedesco, francese). La "
        "parola italiana dell'oggetto non e' mai nei dati: «palloni da calcio» "
        "si scrive «soccer balls», non «palloni».\n"
        "Ma NON fidarti di una parola che ti viene in mente. Il vocabolario e' "
        "nei dati, e lo scopri leggendo: cerca un termine solo (quello che sai "
        "per certo, o una parola inglese che ti suona) e LEGGI le descrizioni "
        "che tornano. Se la parola giusta e' «Duftkerze» e tu avevi scritto "
        "«diffusore», solo quella descrizione te la fa trovare.\n"
        "Operatori: `descrizione ~* 'white'` trova le righe che contengono "
        "quella lettera per lettera, e NON e' una parola intera: «ribbon» "
        "trova anche «ribbons», ma «ribbons» NON trova «ribbon». Metti la "
        "singolare e copri entrambe. Il `|` dentro la stessa ~* e' una O "
        "(«'soccer|football'» trova l'una o l'altra); per il E metti due "
        "condizioni con AND. Non usare LIKE con '%', usa ~*.\n"
        "UNA PAROLA PER CONDIZIONE, e vale in TUTTE E DUE le tabelle. `~*` "
        "cerca una sequenza esatta di lettere, spazi compresi: due parole "
        "dentro lo stesso `~*` le trova solo se nel dato stanno in "
        "quell'ordine e senza niente in mezzo.\n"
        "- in `immagini` non ci stanno quasi mai: la descrizione e' fatta di "
        "etichette («Object:», «Colours:», «Material:») e «white» e «ribbon» "
        "sono separate da altre parole. `~* 'white ribbon'` da' zero righe, "
        "`~* 'white' AND ~* 'ribbon'` ne da' ventuno.\n"
        "- in `chunks` e' prosa, e la tentazione e' di crederlo possibile: non "
        "lo e'. «red christmas balls» non trova «Christmas balls, red» ne' "
        "«red glitter Christmas balls» — basta una virgola o un aggettivo in "
        "mezzo. Stessa regola, stesso motivo.\n"
        "Quindi: una parola per condizione, i sinonimi dentro `|` "
        "(«'ribbon|bänder|tapes'»), e ogni attributo (colore, oggetto, "
        "motivo) e' una condizione a parte unita con AND.\n"
        "\n"
        "QUANDO TORNA ZERO RIGHE\n"
        "Zero non e' un errore, e' una risposta. Non riscrivere la stessa query: "
        "cambia qualcosa (togli un termine dall'AND, allarga i sinonimi dentro "
        "un termine con |, prova l'altra tabella) e se resta zero dillo all'utente "
        "che non hai trovato. Una risposta onesta vale piu' di una pagina "
        "inventata.\n"
        + (f"\nParole che il glossario ha gia' imparato su questi dati:\n{termini_rari}"
           if termini_rari else "")
    )
