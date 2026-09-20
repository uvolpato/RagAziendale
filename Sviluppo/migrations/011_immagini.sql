-- Migrazione 011 — immagini estratte dai documenti (cataloghi, schede).
--
-- Docling estrae le immagini dai PDF durante l'indicizzazione: si salvano su
-- un volume condiviso (ingestion scrive, orchestratore serve) e qui si tiene
-- il collegamento (source, documento, pagina) -> percorso. Il contenuto NON
-- sta nel database: sono file, e il serving li legge con un URL firmato
-- (orchestratore) dopo che il gate ha ammesso il chunk. Le ACL stanno sulla
-- fonte, come per il testo: niente duplicazione del dato di sicurezza.
CREATE TABLE immagini (
    id          bigserial PRIMARY KEY,
    source_id   text NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    documento   text NOT NULL,        -- file specifico dentro la sorgente
    page        int,                  -- pagina da cui viene l'immagine
    percorso    text NOT NULL UNIQUE  -- relativo alla radice del volume immagini
);
CREATE INDEX immagini_chunk_idx ON immagini (source_id, documento, page);
