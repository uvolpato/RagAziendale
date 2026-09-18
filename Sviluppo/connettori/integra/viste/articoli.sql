-- Vista Integra -> articoli (MODELLO-DATI-GESTIONALE.md §5.5).
-- DA VERIFICARE sullo schema reale (colonne dal contratto B2B, non verificate).
SELECT
    p.pro_id::text                AS id_origine,
    p.pro_cod::text               AS codice,
    p.pro_descr                   AS descrizione,
    p.pro_descr_estesa            AS descrizione_estesa,      -- se presente
    NULL                          AS tipo_articolo,           -- da mappare da cod_clv_tipoarticolo
    p.pro_um                      AS unita_misura,
    NULL                          AS classificazioni,         -- cod_famiglia, cod_linea, ...
    NULL                          AS attributi,               -- cod_diametro_esterno, cod_altezza
    p.pro_marca                   AS marca,
    NULL                          AS codice_iva,
    NULL                          AS peso_netto,
    NULL                          AS peso_lordo,
    NULL                          AS volume,
    NULL                          AS costo_ultimo,            -- listino VENCU
    NULL                          AS costo_medio,
    p.ubicazione                  AS ubicazione,
    p.incluso_b2b                 AS pubblicato_web,
    p.prodotto_obsoleto           AS obsoleto,
    '{}'::jsonb                   AS extra,
    p.data_modifica::timestamptz  AS data_modifica_origine
FROM b2b_prodotti p
WHERE p.azi_cdazi = %(codice_azienda)s
  AND (%(dal)s::timestamptz IS NULL OR p.data_modifica::timestamptz > %(dal)s::timestamptz)
