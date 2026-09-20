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


def cerca(conn, domanda: str, gruppi: list[str], qvec=None, limite: int = 5):
    """Restituisce (righe, degradato).

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
