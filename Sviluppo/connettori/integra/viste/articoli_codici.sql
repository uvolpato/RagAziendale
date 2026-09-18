-- Vista Integra -> articoli_codici (MODELLO-DATI-GESTIONALE.md §5.6).
-- Codici alternativi: EAN, codice fornitore, codice cliente. DA VERIFICARE:
-- le colonne (codice_alternativo, codice_esterno, psg_cod1..3) dipendono dallo
-- schema reale. Qui si ipotizza una vista b2b_codici_alternativi del B2B.
SELECT
    c.codice_alternativo::text    AS id_origine,
    c.pro_id::text                AS articolo_id,
    c.tipo_codice::text           AS tipo,        -- 'ean' | 'fornitore' | 'cliente' | ...
    c.codice::text                AS codice,
    c.soggetto_id::text           AS soggetto_id,
    '{}'::jsonb                   AS extra,
    NULL                          AS data_modifica_origine
FROM b2b_codici_alternativi c
WHERE c.azi_cdazi = %(codice_azienda)s
