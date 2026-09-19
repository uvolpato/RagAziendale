-- Migrazione 009 — un file per riga: cosa l'indicizzazione ha visto nelle
-- cartelle (decisioni 61-64).
--
-- Serve a tre cose:
--   - non rileggere un file che non e' cambiato (impronta del contenuto);
--   - togliere dall'indice un file cancellato dalla cartella;
--   - mostrare a chi gestisce la cartella quanti documenti ci sono e quali
--     non si leggono, senza contare i chunk.
-- I chunk restano l'unica cosa che la ricerca legge; le ACL restano solo
-- in sources (001).
CREATE TABLE documenti (
    source_id      text        NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    documento      text        NOT NULL,   -- percorso relativo alla cartella della fonte, come chunks.documento
    impronta       text        NOT NULL,   -- sha256 del contenuto del file
    dimensione     bigint      NOT NULL,
    modificato_il  timestamptz NOT NULL,   -- data del file
    stato          text        NOT NULL CHECK (stato IN ('indicizzato', 'errore', 'vuoto')),
    errore         text,
    pezzi          int         NOT NULL DEFAULT 0,
    indicizzato_il timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, documento)
);
CREATE INDEX documenti_impronta_idx ON documenti (source_id, impronta);
