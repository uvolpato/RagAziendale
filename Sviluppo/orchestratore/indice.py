"""Indice dei documenti e delle pagine: descrizione + ricerca a due stadi (D21).

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

La descrizione la scrive il modello UNA volta, leggendo i pezzi gia' indicizzati
(il titolo di ogni pagina, o il testo della pagina): non si rilegge nessun PDF.

Le ACL restano dove sono sempre state: la descrizione NON si duplica sui pezzi,
e `pertinenti` filtra sulle stesse `sources` (gruppi ∩ aziende ∩ attiva).
"""
import os

from psycopg.rows import tuple_row

from orchestratore import egress, modello, recupero

PRINCIPALE = os.environ.get("LLM_RAGIONAMENTO", "ragionamento")
SECONDI = float(os.environ.get("INDICE_TIMEOUT", "30"))

ISTRUZIONI = (
    "Ti do le intestazioni delle pagine di un documento aziendale. Scrivi in "
    "una frase breve (max 30 parole) di cosa parla il documento nel suo "
    "complesso: quali tipi di prodotti o argomenti contiene.\n"
    "Se e' un catalogo, elenca le categorie principali di prodotti. Se e' una "
    "policy o una guida, di' l'argomento.\n"
    "Non inventare dettagli che le intestazioni non mostrano. Scrivi in "
    "italiano, solo la frase, niente introduzioni.\n"
    "/no_think"
)

ISTRUZIONI_PAGINA = (
    "Ti do il testo di una pagina di un documento aziendale (catalogo, manuale, "
    "scheda tecnica). Scrivi in una frase breve (max 20 parole) di cosa parla "
    "questa pagina: quali prodotti, formati, argomenti o istruzioni contiene.\n"
    "Se la pagina elenca solo prodotti simili, riassumili in una categoria. Se "
    "e' una tabella di formati o prezzi, dillo. Non inventare dettagli che il "
    "testo non mostra. Scrivi in italiano, solo la frase, niente introduzioni.\n"
    "/no_think"
)


def _chiedi(messaggi) -> str:
    return modello.chiedi(messaggi)


def _intestazioni(conn, source_id, documento, limite=120):
    """Le prime righe dei chunk di un documento: sono i titoli delle pagine.

    Un campione, non tutto: bastano a dire di cosa parla il documento, e un
    catalogo ha centinaia di pagine. Si prende la prima riga non vuota di ogni
    chunk, un chunk per pagina, senza doppioni."""
    righe = []
    viste = set()
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            """SELECT page, content FROM chunks
               WHERE source_id = %s AND documento = %s
               ORDER BY page, id LIMIT %s""",
            (source_id, documento, limite * 3),
        )
        for page, content in cur.fetchall():
            if page in viste:
                continue
            viste.add(page)
            prima = next((r.strip() for r in (content or "").splitlines()
                          if r.strip() and not r.strip().startswith("|")), "")
            if prima and prima not in righe:
                righe.append(prima[:120])
            if len(righe) >= limite:
                break
    return righe


def _testo_pagina(conn, source_id, documento, page, limite_car=2500):
    """Il testo di una pagina: i chunk della pagina, incollati e tagliati."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            """SELECT content FROM chunks
               WHERE source_id = %s AND documento = %s AND page = %s
               ORDER BY id""",
            (source_id, documento, page),
        )
        testo = " ".join(p for (c,) in cur.fetchall() for p in (c or "").split())
    return testo[:limite_car]


def _scrivi(conn, source_id, documento, page, descrizione, vettore):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO indice (source_id, documento, page, descrizione, descrizione_vec)
               VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
            (source_id, documento, page, descrizione, vettore),
        )


def genera_documenti(conn, solo=None):
    """Le descrizioni a livello documento, in `indice` (page NULL).

    Una chiamata al modello per documento. `solo` (sottostringa del nome file)
    restringe ai documenti che la contengono. Non solleva: un documento fallito
    resta senza descrizione e verra' ripreso al giro dopo."""
    pendenti = []
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            """SELECT d.source_id, d.documento FROM documenti d
               WHERE d.stato = 'indicizzato'
                 AND NOT EXISTS (SELECT 1 FROM indice i
                                 WHERE i.source_id = d.source_id
                                   AND i.documento = d.documento
                                   AND i.page IS NULL)
               ORDER BY d.source_id, d.documento""",
        )
        pendenti = cur.fetchall()
    fatti = 0
    for source_id, documento in pendenti:
        if solo and solo not in documento:
            continue
        intestazioni = _intestazioni(conn, source_id, documento)
        if not intestazioni:
            continue
        try:
            descrizione = _chiedi([{"role": "system", "content": ISTRUZIONI},
                                   {"role": "user", "content": "\n".join(intestazioni)}])
        except Exception as e:
            print(f"descrizione non riuscita per {documento}: "
                  f"{type(e).__name__}: {e}", flush=True)
            continue
        if not descrizione:
            continue
        vettore = recupero.embedding(descrizione)
        if vettore is None:
            continue
        _scrivi(conn, source_id, documento, None, descrizione, vettore)
        conn.commit()
        fatti += 1
        print(f"  descritto documento: {documento} -> {descrizione}", flush=True)
    print(f"indice documenti: {fatti} descrizioni scritte su {len(pendenti)}",
          flush=True)


def genera_pagine(conn, solo=None, quanti=None):
    """I riassunti a livello pagina, in `indice` (page N).

    Una chiamata al modello per pagina. `solo` restringe ai documenti il cui
    nome contiene la stringa; `quanti` limita il numero di pagine (per una
    prova). Non solleva: una pagina fallita resta senza riassunto."""
    pendenti = []
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            """SELECT DISTINCT c.source_id, c.documento, c.page
               FROM chunks c
               JOIN documenti d ON d.source_id = c.source_id AND d.documento = c.documento
               WHERE d.stato = 'indicizzato'
                 AND NOT EXISTS (SELECT 1 FROM indice i
                                 WHERE i.source_id = c.source_id
                                   AND i.documento = c.documento
                                   AND i.page = c.page)
               ORDER BY c.source_id, c.documento, c.page""",
        )
        pendenti = cur.fetchall()
    if solo:
        pendenti = [p for p in pendenti if solo in p[1]]
    if quanti:
        pendenti = pendenti[:quanti]
    fatti = 0
    for source_id, documento, page in pendenti:
        testo = _testo_pagina(conn, source_id, documento, page)
        if not testo:
            continue
        try:
            descrizione = _chiedi([{"role": "system", "content": ISTRUZIONI_PAGINA},
                                   {"role": "user", "content": testo}])
        except Exception as e:
            print(f"riassunto non riuscito per {documento} p.{page}: "
                  f"{type(e).__name__}: {e}", flush=True)
            continue
        if not descrizione:
            continue
        vettore = recupero.embedding(descrizione)
        if vettore is None:
            continue
        _scrivi(conn, source_id, documento, page, descrizione, vettore)
        conn.commit()
        fatti += 1
        if fatti % 20 == 0:
            print(f"  ... {fatti} riassunti pagina", flush=True)
    print(f"indice pagine: {fatti} riassunti scritti su {len(pendenti)}",
          flush=True)


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


def _prova():
    print("indice: import ok")
