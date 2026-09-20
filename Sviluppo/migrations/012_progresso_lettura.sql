-- Migrazione 012 — progresso della lettura per documento.
--
-- Il pannello fonti mostra se l'ingestion sta elaborando una fonte e, per i
-- PDF, con quale avanzamento: si tiene il segno sulla riga del documento.
--  - in_lettura    : istante in cui il file ha iniziato a essere letto (NULL = non sta girando)
--  - pagine_fatte  : ultimo blocco di pagine completato
--  - pagine_totali : pagine del PDF (NULL = tipo senza pagine: niente percentuale)
-- Il valore si azzera a fine lettura, per successo o per errore.
ALTER TABLE documenti
    ADD COLUMN in_lettura    timestamptz,
    ADD COLUMN pagine_fatte  int NOT NULL DEFAULT 0,
    ADD COLUMN pagine_totali int;