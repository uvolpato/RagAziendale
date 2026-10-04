"""Indice dei documenti e delle pagine: QUESTO LEGGE (D21).

Su una domanda APERTA («regalo per una ragazza di 30 anni») l'agente riprova
con parole che non esistono nel catalogo, perche' non sa COSA contiene
l'archivio. Qui si tiene, per ogni documento e per ogni pagina, una DESCRIZIONE
del suo contenuto e il suo VETTORE (migrazione 021). Due livelli, una tabella
`indice`:

- `page IS NULL` = descrizione del documento intero (cataloghi, manuali);
- `page = N`    = riassunto della pagina N (schede tecniche, tabelle, sezioni).

La ricerca a due stadi: prima si cercano le DESCRIZIONI (poche righe, non
migliaia di chunk) e si scopre QUALI documenti/pagine c'entrano; poi la ricerca
dettagliata resta DENTRO quei documenti.

Le descrizioni le SCRIVE l'indicizzazione, in `ingestion/descrizioni.py`, in
coda al giro: le scrive il modello di chat leggendo i pezzi gia' estratti, e
non ci sta in VRAM insieme a Docling. Qui non si scrive niente: questo e'
l'agente che risponde, e un servizio che risponde non indicizza.

Le ACL restano dove sono sempre state: la descrizione NON si duplica sui pezzi,
e `pertinenti` filtra sulle stesse `sources` (gruppi ∩ aziende ∩ attiva).
"""

from psycopg.rows import tuple_row




def pertinenti(conn, qvec, gruppi, quanti=4):
    """I documenti piu' pertinenti alla domanda (page NULL), filtrati per ACL.

    Stesso filtro di `recupero.cerca`: gruppi ∩ aziende ∩ stato='attiva'. Torna
    (source_id, documento) in ordine di vicinanza. Se non c'e' nessuna
    descrizione torna lista vuota, e il chiamante cerca su tutto come oggi."""
    if qvec is None:
        return []
    from .identita import aziende as aziende_di
    aziende = aziende_di(gruppi)
    if not gruppi or not aziende:
        return []
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            """SELECT i.source_id, i.documento
               FROM indice i
               JOIN sources s ON s.id = i.source_id
               WHERE i.page IS NULL
                 AND s.acl_groups && %(gruppi)s::text[]
                 AND s.aziende && %(aziende)s::text[]
                 AND s.stato = 'attiva'
               ORDER BY i.descrizione_vec <=> %(qvec)s::vector
               LIMIT %(quanti)s""",
            {"gruppi": gruppi, "aziende": aziende, "qvec": qvec, "quanti": quanti},
        )
        return [(r[0], r[1]) for r in cur.fetchall()]


def pagine_pertinenti(conn, qvec, gruppi, quanti=8):
    """Le pagine piu' pertinenti alla domanda (page N), filtrate per ACL.

    Stessa regola di `pertinenti`, ma a livello pagina. Torna (source_id,
    documento, page). Se l'indice pagina non e' ancora generato, lista vuota."""
    if qvec is None:
        return []
    from .identita import aziende as aziende_di
    aziende = aziende_di(gruppi)
    if not gruppi or not aziende:
        return []
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            """SELECT i.source_id, i.documento, i.page
               FROM indice i
               JOIN sources s ON s.id = i.source_id
               WHERE i.page IS NOT NULL
                 AND s.acl_groups && %(gruppi)s::text[]
                 AND s.aziende && %(aziende)s::text[]
                 AND s.stato = 'attiva'
               ORDER BY i.descrizione_vec <=> %(qvec)s::vector
               LIMIT %(quanti)s""",
            {"gruppi": gruppi, "aziende": aziende, "qvec": qvec, "quanti": quanti},
        )
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]


def descrizioni_visibili(conn, gruppi):
    """Le descrizioni dei documenti (page NULL) che questa persona puo' vedere.

    Servono alla via ASTRATTA dell'agente: generare categorie ANCORATE a cio'
    che il catalogo contiene davvero, invece di inventare «zaini e cappelli»."""
    from .identita import aziende as aziende_di
    aziende = aziende_di(gruppi)
    if not gruppi or not aziende:
        return []
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            """SELECT i.documento, i.descrizione
               FROM indice i
               JOIN sources s ON s.id = i.source_id
               WHERE i.page IS NULL
                 AND s.acl_groups && %(gruppi)s::text[]
                 AND s.aziende && %(aziende)s::text[]
                 AND s.stato = 'attiva'
               ORDER BY i.documento""",
            {"gruppi": gruppi, "aziende": aziende},
        )
        return [(r[0], r[1]) for r in cur.fetchall()]


def vicine(conn, qvec, gruppi, documenti=4, pagine=8):
    """Le descrizioni piu' vicine alla domanda: documenti E pagine, col TESTO.

    `pertinenti` e `pagine_pertinenti` tornano i nomi, e servono a restringere
    una query. Qui serve altro: far LEGGERE al coordinatore di cosa parlano i
    documenti, perche' il suo problema non e' restringere — e' non sapere come
    l'archivio chiama le cose, e tirare a indovinare una parola.

    Torna (documento, page, descrizione) con `page` None per il livello
    documento. Stesso filtro ACL dei fratelli: gruppi, aziende, sorgente
    attiva."""
    if qvec is None:
        return []
    from .identita import aziende as aziende_di
    aziende = aziende_di(gruppi)
    if not gruppi or not aziende:
        return []
    fuori = []
    with conn.cursor(row_factory=tuple_row) as cur:
        for solo_documenti, quanti in ((True, documenti), (False, pagine)):
            if not quanti:
                continue
            cur.execute(
                """SELECT i.documento, i.page, i.descrizione
                   FROM indice i
                   JOIN sources s ON s.id = i.source_id
                   WHERE (i.page IS NULL) = %(solo)s
                     AND s.acl_groups && %(gruppi)s::text[]
                     AND s.aziende && %(aziende)s::text[]
                     AND s.stato = 'attiva'
                   ORDER BY i.descrizione_vec <=> %(qvec)s::vector
                   LIMIT %(quanti)s""",
                {"solo": solo_documenti, "gruppi": gruppi, "aziende": aziende,
                 "qvec": qvec, "quanti": quanti})
            fuori += [(r[0], r[1], r[2]) for r in cur.fetchall()]
    return fuori

def _prova():
    print("indice: import ok")
