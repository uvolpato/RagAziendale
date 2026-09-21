"""Ricerca ibrida con filtro ACL applicato NELLA query.

Due regole non negoziabili:

1. Il filtro sta nella query, non dopo: gruppi, aziende e stato della fonte.
   Si passa dalla tabella `sources`, che e' l'unica casa del dato di
   sicurezza: nulla e' duplicato sui chunk, quindi non puo' disallinearsi.

2. Fusione RRF di vettoriale e full-text. Il vettoriale sbaglia i codici
   articolo e i part number, il full-text li prende; il full-text sbaglia le
   domande in linguaggio naturale, il vettoriale le prende. La fusione costa
   zero servizi in piu'.

Se l'host di inferenza non risponde non si puo' calcolare l'embedding della
domanda: si degrada al solo full-text con un avviso, invece di restare muti.
Non e' resilienza di lusso — l'host di inferenza in sviluppo e' LM Studio su
una postazione che va in sospensione.
"""
import os

from psycopg.rows import dict_row

K_RRF = 60          # costante standard della Reciprocal Rank Fusion
CANDIDATI = 30      # quanti per ramo prima della fusione


SQL_IBRIDA = """
WITH consentite AS (
    -- Il filtro: intersezione fra i gruppi del token e quelli della sorgente
    -- (niente livelli numerici, niente gerarchie), E fra le aziende del token
    -- e quelle della sorgente (decisione 53), E solo fonti attive: una fonte
    -- sospesa o in attesa di approvazione non risponde (decisione 67).
    SELECT id, residency FROM sources
     WHERE acl_groups && %(gruppi)s::text[]
       AND aziende && %(aziende)s::text[]
       AND stato = 'attiva'
),
vett AS (
    SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> %(qvec)s::vector) AS r
    FROM chunks c
    WHERE c.source_id IN (SELECT id FROM consentite)
      AND c.embedding IS NOT NULL
    ORDER BY c.embedding <=> %(qvec)s::vector
    LIMIT %(cand)s
),
testo AS (
    SELECT c.id,
           row_number() OVER (
               ORDER BY ts_rank_cd(to_tsvector('italian', c.content), q) DESC
           ) AS r
    FROM chunks c
    JOIN consentite s ON s.id = c.source_id
    CROSS JOIN plainto_tsquery('italian', %(domanda)s) q
    WHERE to_tsvector('italian', c.content) @@ q
    LIMIT %(cand)s
),
fusi AS (
    SELECT id, SUM(punti) AS punteggio FROM (
        SELECT id, 1.0 / (%(k)s + r) AS punti FROM vett
        UNION ALL
        SELECT id, 1.0 / (%(k)s + r) AS punti FROM testo
    ) x GROUP BY id
)
SELECT c.id, c.source_id, c.documento, c.page, c.content,
       s.residency, f.punteggio,
       (SELECT array_agg(i.id ORDER BY i.id) FROM immagini i
         WHERE i.source_id = c.source_id AND i.documento = c.documento
           AND i.page IS NOT DISTINCT FROM c.page) AS immagini
FROM fusi f
JOIN chunks c  ON c.id = f.id
JOIN consentite s ON s.id = c.source_id
ORDER BY f.punteggio DESC
LIMIT %(limite)s;
"""

# Variante senza vettoriale: usata quando l'host di inferenza non risponde.
SQL_SOLO_TESTO = """
WITH consentite AS (
    SELECT id, residency FROM sources
     WHERE acl_groups && %(gruppi)s::text[]
       AND aziende && %(aziende)s::text[]
       AND stato = 'attiva'
)
SELECT c.id, c.source_id, c.documento, c.page, c.content,
       s.residency,
       ts_rank_cd(to_tsvector('italian', c.content), q) AS punteggio,
       (SELECT array_agg(i.id ORDER BY i.id) FROM immagini i
         WHERE i.source_id = c.source_id AND i.documento = c.documento
           AND i.page IS NOT DISTINCT FROM c.page) AS immagini
-- Attenzione all'ordine: una virgola dopo `chunks c` legherebbe il JOIN
-- successivo a plainto_tsquery invece che a chunks, e Postgres risponde
-- "invalid reference to FROM-clause entry". CROSS JOIN esplicito, sempre.
FROM chunks c
JOIN consentite s ON s.id = c.source_id
CROSS JOIN plainto_tsquery('italian', %(domanda)s) q
WHERE to_tsvector('italian', c.content) @@ q
ORDER BY punteggio DESC
LIMIT %(limite)s;
"""


def cerca(conn, domanda: str, gruppi: list[str], qvec=None, limite: int = 8):
    """Restituisce (righe, degradato).

    limite 8 e non 5 (dal 20/09/2026): sui cataloghi un solo prodotto occupa
    piu' pezzi fra testo, tabella e descrizione della figura, e con 5 posti una
    domanda larga ("sassi rossi") restava senza il testo del prodotto. Da
    validare con l'eval: piu' pezzi significa anche piu' contesto da leggere per
    il modello, e con 5 utenti in parallelo il contesto costa VRAM.

    `qvec` None significa embedding non disponibile -> solo full-text.
    """
    from .identita import aziende as aziende_di
    aziende = aziende_di(gruppi)
    if not gruppi or not aziende:
        # Nessun gruppo o nessuna azienda, nessun accesso. Esplicito per non
        # dipendere dal comportamento di && con array vuoto.
        return [], False

    degradato = qvec is None
    sql = SQL_SOLO_TESTO if degradato else SQL_IBRIDA
    par = {"gruppi": gruppi, "aziende": aziende, "domanda": domanda, "limite": limite}
    if not degradato:
        par |= {"qvec": qvec, "cand": CANDIDATI, "k": K_RRF}

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, par)
        return cur.fetchall(), degradato


def contiene_interno(righe) -> str | None:
    """La sorgente `interno` che contamina il turno, se c'e'."""
    for r in righe:
        if r["residency"] == "interno":
            return r["source_id"]
    return None


def embedding(domanda: str):
    """Embedding della domanda, chiesto a LiteLLM per nome logico (`embedding`),
    come fa l'ingestion: il modello vero lo decide litellm-config.yaml.

    Restituisce None se LiteLLM o l'host di inferenza non rispondono: il
    chiamante degrada su BM25 invece di propagare l'errore.
    """
    from . import egress

    url = os.environ["LITELLM_BASE_URL"].rstrip("/")
    modello = os.environ.get("LLM_EMBEDDING", "embedding")
    token = os.environ.get("LITELLM_MASTER_KEY") or ""
    testa = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with egress.client(timeout=20.0, verify=False) as c:
            r = c.post(f"{url}/v1/embeddings",
                       json={"model": modello, "input": [domanda]},
                       headers=testa)
            r.raise_for_status()
            return r.json()["data"][0]["embedding"]
    except egress.EgressVietato:
        raise           # non si mascherano le violazioni di egress
    except Exception:
        return None     # host giu', modello scaricato, timeout: si degrada


SQL_IMMAGINI = """
WITH consentite AS (
    -- Stesso filtro della ricerca sul testo: gruppi, aziende, fonte attiva.
    -- Le ACL stanno sulla fonte e non si duplicano sulle immagini.
    SELECT id FROM sources
     WHERE acl_groups && %(gruppi)s::text[]
       AND aziende && %(aziende)s::text[]
       AND stato = 'attiva'
)
SELECT i.id, i.documento, i.page, i.descrizione,
       (i.embedding <=> %(qvec)s::vector) AS distanza
  FROM immagini i
 WHERE i.source_id IN (SELECT id FROM consentite)
   AND i.embedding IS NOT NULL
   AND (%(documenti)s::text[] IS NULL OR i.documento = ANY(%(documenti)s::text[]))
 ORDER BY i.embedding <=> %(qvec)s::vector
 LIMIT %(limite)s;
"""


def immagini_pertinenti(conn, qvec, gruppi, documenti=None, limite: int = 4):
    """Le figure che RISPONDONO alla domanda, non quelle che stanno vicino al
    testo che ha risposto.

    Prima le immagini si prendevano per pagina: in un catalogo una pagina
    contiene dieci prodotti, e uscivano figure che non c'entravano. Ora si
    cerca fra le descrizioni prodotte dal modello visivo, nello stesso spazio
    vettoriale dei pezzi: «sassi rossi» puo' incontrare «dark red lava rocks».

    `documenti`: se valorizzato, si resta nei documenti che hanno risposto —
    la figura deve appartenere alla stessa fonte della risposta, altrimenti si
    mostrano prodotti di un catalogo mentre il testo parla di un altro.
    """
    if qvec is None:
        return []
    from .identita import aziende as aziende_di
    aziende = aziende_di(gruppi)
    if not gruppi or not aziende:
        return []
    par = {"gruppi": gruppi, "aziende": aziende, "qvec": qvec,
           "documenti": documenti or None, "limite": limite}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(SQL_IMMAGINI, par)
        return cur.fetchall()


# ------------------------------------------------------------------ altri modi di guardare
#
# La ricerca a somiglianza risponde a UNA domanda: «dammi i pezzi che
# assomigliano a questa frase». Funziona quando l'utente DESCRIVE una cosa, e
# sbanda quando la NOMINA: un codice o un nome di linea, dentro un pezzo di
# duecento caratteri, si diluisce. Misurato il 21/09/2026 sulle domande vere:
# «le sfere Marrakesch» aveva la risposta in posizione 26 su 4571, fuori dalle
# prime 8, mentre la parola MARRAKESCH nel catalogo compare in una pagina sola.
#
# Qui sotto le altre due domande che servono — «dove compare questa parola» e
# «cosa c'e' intorno» — piu' «cosa c'e' in generale». Sono le mosse che hanno
# risolto a mano il caso «sassi rossi» (VALUTAZIONE-ORCHESTRATORE-AGENTE.md §9).
#
# Il filtro dei permessi e' lo STESSO di cerca(), nella query: gruppi ∩
# acl_groups, aziende ∩ aziende, stato = 'attiva'. Non e' copiato per pigrizia
# ma perche' deve valere qui identico: uno strumento che dimentica le ACL e' una
# fuga di dati, e questi strumenti li chiamera' un agente.

_CONSENTITE = """
    SELECT id, residency FROM sources
     WHERE acl_groups && %(gruppi)s::text[]
       AND aziende && %(aziende)s::text[]
       AND stato = 'attiva'
"""

SQL_ESATTA = f"""
WITH consentite AS ({_CONSENTITE})
SELECT c.id, c.source_id, c.documento, c.page, c.content, s.residency
FROM chunks c
JOIN consentite s ON s.id = c.source_id
WHERE c.content ILIKE %(motivo)s
ORDER BY length(c.content), c.documento, c.page
LIMIT %(limite)s;
"""

SQL_PAGINA = f"""
WITH consentite AS ({_CONSENTITE})
SELECT c.id, c.source_id, c.documento, c.page, c.content, s.residency
FROM chunks c
JOIN consentite s ON s.id = c.source_id
WHERE c.documento = %(documento)s AND c.page IS NOT DISTINCT FROM %(page)s
ORDER BY c.id;
"""

SQL_DOCUMENTI = f"""
WITH consentite AS ({_CONSENTITE})
SELECT c.source_id, c.documento, count(*) AS pezzi,
       min(c.page) AS prima, max(c.page) AS ultima
FROM chunks c
JOIN consentite s ON s.id = c.source_id
GROUP BY c.source_id, c.documento
ORDER BY c.documento;
"""


def _permessi(gruppi):
    """(gruppi, aziende) o None se questa persona non puo' vedere niente.
    Stessa regola di cerca(): nessun gruppo o nessuna azienda = nessun dato."""
    from .identita import aziende as aziende_di
    aziende = aziende_di(gruppi)
    if not gruppi or not aziende:
        return None
    return {"gruppi": gruppi, "aziende": aziende}


def cerca_esatta(conn, termine: str, gruppi: list[str], limite: int = 8):
    """I pezzi che contengono ALLA LETTERA `termine`: un codice, un nome di
    linea, una sigla. Maiuscole ignorate.

    Ordinati dal pezzo piu' CORTO: se «MARRAKESCH» compare nel titolo di una
    pagina e dentro un indice lungo, il titolo e' quasi sempre la risposta e
    l'indice quasi mai. Niente punteggi: o la parola c'e' o non c'e'.

    `termine` e' trattato come testo, non come motivo di ricerca: i caratteri
    speciali di LIKE si neutralizzano, cosi' un termine con `%` dentro non
    diventa «qualsiasi cosa»."""
    par = _permessi(gruppi)
    if par is None or not (termine or "").strip():
        return []
    pulito = termine.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    par |= {"motivo": f"%{pulito}%", "limite": limite}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(SQL_ESATTA, par)
        return cur.fetchall()


def pagina(conn, documento: str, page, gruppi: list[str]):
    """Tutti i pezzi di UNA pagina, nell'ordine in cui stanno sul foglio.

    «DST2001 rot red» da solo non vuol dire niente; la pagina dice che sono
    pietre decorative da 9-13 mm e quali colori esistono. Senza questo il
    modello vede il frammento e non ha modo di chiedere cosa c'e' intorno."""
    par = _permessi(gruppi)
    if par is None:
        return []
    par |= {"documento": documento, "page": page}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(SQL_PAGINA, par)
        return cur.fetchall()


def documenti_visibili(conn, gruppi: list[str]):
    """I documenti che questa persona puo' vedere, con quanti pezzi e che
    pagine. Serve a due cose: rispondere a «quali cataloghi avete?», che oggi
    il sistema non sa fare perche' sa solo cercare DENTRO i documenti; e
    permettere di dire «ho guardato nei quattro cataloghi a cui hai accesso e
    non c'e'» invece di un «non trovato» che non si sa quanto valga."""
    par = _permessi(gruppi)
    if par is None:
        return []
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(SQL_DOCUMENTI, par)
        return cur.fetchall()
