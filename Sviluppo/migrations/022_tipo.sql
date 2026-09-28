-- Migrazione 022 — tipo del documento: 'catalogo' o 'documento', deciso
-- dall'ingestione a fine lettura (un file con figure e' un catalogo). Sostituisce
-- la proxy «ha figure = catalogo» che il chiamante usava a ogni risposta.
ALTER TABLE documenti ADD COLUMN tipo text NOT NULL DEFAULT 'documento';
-- Backfill: i documenti GIA' indicizzati che hanno figure sono cataloghi. Da
-- dopo l'ingestion (migrazione 022 in poi) lo scrive direttamente indicizza.py.
UPDATE documenti d SET tipo = 'catalogo'
WHERE EXISTS (SELECT 1 FROM immagini i
              WHERE i.source_id = d.source_id AND i.documento = d.documento);
CREATE INDEX documenti_tipo_idx ON documenti (source_id, tipo);