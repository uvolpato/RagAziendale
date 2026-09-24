-- Migrazione 017 — progresso delle descrizioni delle figure per documento.
--
-- Dopo la lettura delle pagine (pagine_fatte/pagine_totali, migrazione 012)
-- Docling estrae le immagini e poi le descrizioni girano UNA PER UNA col modello
-- visivo: su un catalogo sono centinaia di chiamate da qualche decimo di
-- secondo, e la barra delle pagine era gia' al 100% — il pannello sembrava
-- bloccato per minuti (visto il 22/09/2026).
--
-- La seconda coppia di colonne mostra un secondo avanzamento, da 0 a
-- len(immagini), senza toccare la percentuale delle pagine.
--  - figure_fatte  : descrizioni completate (nuove o riusate dalla cache)
--  - figure_totali : figure da descrivere del documento (NULL = niente barra)
ALTER TABLE documenti
    ADD COLUMN figure_fatte  int NOT NULL DEFAULT 0,
    ADD COLUMN figure_totali int;