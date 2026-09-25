"""Indice dei documenti: descrizione + ricerca a due stadi (D21).

Su una domanda APERTA («regalo per una ragazza di 30 anni») l'agente riprova
con parole che non esistono nel catalogo, perche' non sa COSA contiene
l'archivio. Qui si tiene, per ogni documento, una DESCRIZIONE del suo contenuto
e il suo VETTORE (migrazione 019). La ricerca a due stadi:

  1. primo passaggio: si cerca fra le DESCRIZIONI dei documenti (34 righe, non
     migliaia di chunk) e si scopre QUALI cataloghi c'entrano;
  2. secondo passaggio: la ricerca dettagliata resta DENTRO quei documenti.

La descrizione la scrive il modello UNA volta, leggendo le intestazioni dei
pezzi gia' indicizzati (il titolo di ogni pagina, che e' la prima riga del
chunk): non si rilegge nessun PDF. Stesso principio dei sinonimi (018): il
modello popola, la cache in tabella consulta.

Le ACL restano dove sono sempre state: la descrizione non si duplica sui pezzi,
e `pertinenti` filtra sulle stesse `sources` (gruppi ∩ aziende ∩ attiva). La
descrizione e' SOLO un aiuto a scegliere i documenti, non una via per leggere un
contenuto che le ACL non consentono.
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


def _chiedi(messaggi) -> str:
    return modello.chiedi(messaggi)


def _intestazioni(conn, source_id, documento, limite=120):
    """Le prime righe dei chunk di un documento: sono i titoli delle pagine.

    Un campione, non tutto: bastano a dire di cosa parla il documento, e un
    catalogo ha centinaia di pagine. Si prende la prima riga non vuota di ogni
    chunk, un chunk per pagina, senza doppioni."""
    righe = []
    viste = set()
    with conn.cursor() as cur:
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


def genera(conn):
    """Scrive la descrizione di ogni documento che non ce l'ha ancora.

    Una chiamata al modello per documento, salvata in `documenti.descrizione`
    col suo vettore. Non solleva: un documento fallito resta senza descrizione
    e verra' ripreso al giro dopo (o si riprova con --forza)."""
    from psycopg.rows import dict_row
    pendenti = []
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT source_id, documento FROM documenti
               WHERE stato = 'indicizzato' AND descrizione IS NULL
               ORDER BY source_id, documento""",
        )
        pendenti = cur.fetchall()
    fatti = 0
    for d in pendenti:
        intestazioni = _intestazioni(conn, d["source_id"], d["documento"])
        if not intestazioni:
            continue
        messaggi = [{"role": "system", "content": ISTRUZIONI},
                    {"role": "user", "content": "\n".join(intestazioni)}]
        try:
            descrizione = _chiedi(messaggi)
        except Exception as e:
            print(f"descrizione non riuscita per {d['documento']}: "
                  f"{type(e).__name__}: {e}", flush=True)
            continue
        if not descrizione:
            continue
        vettore = recupero.embedding(descrizione)
        if vettore is None:
            continue
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE documenti SET descrizione = %s, descrizione_vec = %s
                   WHERE source_id = %s AND documento = %s""",
                (descrizione, vettore, d["source_id"], d["documento"]),
            )
        conn.commit()
        fatti += 1
        print(f"  descritto: {d['documento']} -> {descrizione}", flush=True)
    print(f"indice documenti: {fatti} descrizioni scritte su {len(pendenti)}",
          flush=True)


def pertinenti(conn, qvec, gruppi, quanti=4):
    """I documenti piu' pertinenti alla domanda, filtrati per ACL.

    Stesso filtro di `recupero.cerca`: gruppi ∩ aziende ∩ stato='attiva', letto
    da `sources` adesso, mai duplicato. Restituisce (source_id, documento) in
    ordine di vicinanza. Se non c'e' nessuna descrizione (indice non ancora
    generato) torna lista vuota, e il chiamante fa la ricerca su tutto come
    oggi."""
    if qvec is None:
        return []
    from .identita import aziende as aziende_di
    aziende = aziende_di(gruppi)
    if not gruppi or not aziende:
        return []
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            """SELECT d.source_id, d.documento
               FROM documenti d
               JOIN sources s ON s.id = d.source_id
               WHERE s.acl_groups && %(gruppi)s::text[]
                 AND s.aziende && %(aziende)s::text[]
                 AND s.stato = 'attiva'
                 AND d.stato = 'indicizzato'
                 AND d.descrizione_vec IS NOT NULL
               ORDER BY d.descrizione_vec <=> %(qvec)s::vector
               LIMIT %(quanti)s""",
            {"gruppi": gruppi, "aziende": aziende, "qvec": qvec,
             "quanti": quanti},
        )
        # `tuple_row` per NON ereditare il dict_row della connessione del
        # chiamante (main.py lo ha): con dict_row, «for _, d in documenti»
        # estraeva le CHIAVI («source_id», «documento») invece dei valori, e il
        # filtro cercava i nomi-file sbagliati (23/09/2026).
        return [(r[0], r[1]) for r in cur.fetchall()]


def descrizioni_visibili(conn, gruppi):
    """Le descrizioni dei documenti che questa persona puo' vedere, per ACL.

    Servono alla via ASTRATTA dell'agente: generare categorie ANCORATE a cio'
    che il catalogo contiene davvero, invece di inventare «zaini e cappelli»
    da un catalogo di vasi. Stesso filtro di `pertinenti`: le descrizioni non
    sono una via per leggere cio' che le ACL non consentono."""
    from .identita import aziende as aziende_di
    aziende = aziende_di(gruppi)
    if not gruppi or not aziende:
        return []
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            """SELECT d.documento, d.descrizione
               FROM documenti d
               JOIN sources s ON s.id = d.source_id
               WHERE s.acl_groups && %(gruppi)s::text[]
                 AND s.aziende && %(aziende)s::text[]
                 AND s.stato = 'attiva'
                 AND d.stato = 'indicizzato'
                 AND d.descrizione IS NOT NULL
               ORDER BY d.documento""",
            {"gruppi": gruppi, "aziende": aziende},
        )
        # tuple_row: non ereditare il dict_row del chiamante (vedi pertinenti).
        return [(r[0], r[1]) for r in cur.fetchall()]


def _prova():
    print("indice: import ok")