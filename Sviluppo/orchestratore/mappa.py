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
from psycopg.rows import tuple_row


def conta(conn, gruppi: list, aziende: list) -> dict:
    """I numeri delle tabelle, letti adesso e COME LI VEDE QUESTO UTENTE.

    Costa due millisecondi e vale un prompt che non mente: l'indicizzazione
    gira di notte e una mappa scritta a mano e' vecchia la mattina dopo.

    I permessi contano. Il 7/10/2026 la mappa diceva «le 50 fonti» a un
    utente che con `SELECT COUNT(*) FROM documenti` ne otteneva 14 — perche'
    l'ACL la impone il codice a ogni query — e la risposta e' stata «ho
    trovato un articolo con 14 elementi, ma non ho trovato nessun catalogo»:
    quella di chi non si fida del proprio risultato. Un numero che la query
    smentisce e' peggio di nessun numero.
    """
    visibili = """SELECT id FROM sources WHERE stato = 'attiva'
                    AND aziende && %(a)s::text[]
                    AND acl_groups && %(g)s::text[]"""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("""WITH v AS (%s)
                       SELECT (SELECT count(*) FROM immagini
                                WHERE source_id IN (SELECT id FROM v)),
                              (SELECT count(*) FROM chunks
                                WHERE source_id IN (SELECT id FROM v)),
                              (SELECT count(*) FROM documenti
                                WHERE source_id IN (SELECT id FROM v)),
                              (SELECT count(*) FROM indice
                                WHERE page IS NULL
                                  AND source_id IN (SELECT id FROM v)),
                              (SELECT count(*) FROM indice
                                WHERE page IS NOT NULL
                                  AND source_id IN (SELECT id FROM v))"""
                    % visibili, {"a": list(aziende), "g": list(gruppi)})
        foto, pezzi, fonti, doc, pag = cur.fetchone()
    return {"foto": foto, "pezzi": pezzi, "fonti": fonti,
            "indice_documenti": doc, "indice_pagine": pag}


def mappa_dati(conn, gruppi: list, aziende: list) -> str:
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
    n = conta(conn, gruppi, aziende)
    # L'INDICE SI DESCRIVE PER QUELLO CHE E'. Finche' era vuoto la mappa
    # diceva «non cercare qui», e quella frase e' sopravvissuta al giorno in
    # cui si e' riempito. Adesso la decide il conteggio, non la memoria.
    if n["indice_documenti"] or n["indice_pagine"]:
        indice = (
            "4. indice — la MAPPA dell'archivio: di ogni documento una "
            "descrizione di cosa contiene, di ogni pagina un riassunto. "
            "%d documenti e %d pagine. Colonne: source_id, documento, page, "
            "descrizione. Dice DOVE sta una cosa e con che parole l'archivio "
            "la chiama, prima che tu scriva la query: non sono righe di "
            "prodotto e non sono il testo del documento.\n"
            % (n["indice_documenti"], n["indice_pagine"]))
    else:
        indice = ("4. indice — la mappa dell'archivio, al momento VUOTA: su "
                  "questa non cercare, usa le altre tre.\n")
    # I conteggi stanno in una f-string a parte, e il resto resta un
    # letterale: il `%` degli esempi («non usare LIKE con '%'») e la
    # formattazione non si toccano.
    dove = (
        "DOVE STANNO I DATI\n"
        f"1. immagini — {n['foto']} foto di catalogo: QUI c'e' il PRODOTTO "
        "(un nastro, "
        "un vaso, una pietra). Colonne: id, source_id, documento, page, "
        "descrizione. Tutto quello che vedi della foto e' dentro `descrizione`, "
        "un unico campo di testo: non esistono colonne `object`, `material`, "
        "`colours`. Il testo e' in INGLESE e comincia cosi':\n"
        "     object: white ribbon with printed soccer balls | material: fabric "
        "ribbon | shape/size: narrow rectangular strip\n"
        "   Altro campione VERO, stessa tabella:\n"
        "     Object: clip with football motif | Material: plastic | "
        "Shape/size: rectangular body, 1.5 cm x 1.5 cm; rounded football head\n"
        f"2. chunks — {n['pezzi']} pezzi di testo: QUI c'e' la DESCRIZIONE, "
        "la policy, "
        "il manuale, il nome di prodotto, il codice, il prezzo. Colonne: id, "
        "source_id, documento, page, content. `content` e' il testo originale, "
        "nelle sue lingue, senza etichette.\n"
        f"3. documenti — le {n['fonti']} fonti: nome file, tipo, stato, pezzi, "
        "figure_totali. Serve per le domande sui documenti stessi.\n"
        + indice)
    return (
        dove +
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
        "QUANDO TORNA ZERO RIGHE — O POCHISSIME\n"
        f"Qui dentro ci sono {n['foto']} foto e {n['pezzi']} pezzi di testo. "
        "Se una cosa "
        "comune te ne da' una o due, la risposta non e' «non c'e'»: e' «la mia "
        "query e' troppo stretta». Una riga sola e' un sospetto, non un "
        "risultato, e dire alla persona che non c'e' niente sarebbe falso.\n"
        "Le due cause, in ordine di frequenza:\n"
        "- IL PLURALE. `~* 'balls'` trova 273 righe, `~* 'ball'` ne trova 609: "
        "la forma lunga esclude la corta, mai il contrario. Scrivi sempre la "
        "piu' corta — 'ball', 'ribbon', 'candle'.\n"
        "- TROPPI AND. Ogni condizione taglia. Tre parole unite con AND "
        "pretendono che tutte e tre stiano nella stessa didascalia, e le "
        "didascalie sono brevi. Togline una, la meno essenziale.\n"
        "Zero, dopo che hai provato davvero, non e' un errore: e' una "
        "risposta, e la dici. Ma non riscrivere la stessa query, e non dire "
        "«non c'e'» dopo un tentativo solo. Una risposta onesta vale piu' di "
        "una pagina inventata — e «non l'ho trovato» detto per pigrizia e' "
        "anch'esso una cosa non vera.\n"
    )
