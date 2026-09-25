-- Migrazione 021 — indice a due livelli: documento + pagina.
--
-- La descrizione a livello documento (019) non basta: un catalogo e' anche
-- pagine di PROSA — schede tecniche, istruzioni, tabelle di formati e prezzi —
-- e il riassunto di quelle pagine serve a trovarle (misurato: le domande di
-- «Logistica, Volumi e Packaging» falliscono perche' la risposta sta in pagine
-- di soli codici senza prosa).
--
-- Una sola tabella per entrambi i livelli: `page` NULL = documento intero,
-- `page` N = riassunto della pagina N. Stesso vettore bge-m3 (1024 dim) dei
-- chunk, quindi stesso operatore <=>.
--
-- Le descrizioni a livello documento esistono gia' su `documenti`: si spostano
-- qui (page NULL) e le colonne vecchie si tolgono. Una fonte unica.

CREATE TABLE indice (
    source_id       text        NOT NULL,
    documento       text        NOT NULL,
    page            int,                    -- NULL = documento intero
    descrizione     text        NOT NULL,
    descrizione_vec vector(1024),
    aggiornato_il   timestamptz NOT NULL DEFAULT now()
);

-- Un solo elemento per documento (page NULL) e uno per pagina (page N).
CREATE UNIQUE INDEX indice_documento_unico ON indice (source_id, documento)
    WHERE page IS NULL;
CREATE UNIQUE INDEX indice_pagina_unico ON indice (source_id, documento, page)
    WHERE page IS NOT NULL;

-- Le descrizioni a livello documento esistenti si spostano (page NULL).
INSERT INTO indice (source_id, documento, page, descrizione, descrizione_vec)
SELECT source_id, documento, NULL, descrizione, descrizione_vec
FROM documenti
WHERE descrizione IS NOT NULL;

ALTER TABLE documenti DROP COLUMN descrizione, DROP COLUMN descrizione_vec;
