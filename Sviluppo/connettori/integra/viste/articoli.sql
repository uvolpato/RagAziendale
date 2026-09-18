-- Vista Integra -> articoli (MODELLO-DATI-GESTIONALE.md §5.5).
-- Fonte: vista rag_prodotti. Classificazioni e attributi vanno in jsonb: i nomi
-- famiglia/linea/gruppo_merc/gruppo_stat/diametro/altezza vengono dalla vista.
SELECT
    p.pro_id::text                AS id_origine,
    p.pro_cod::text               AS codice,
    p.pro_descr                   AS descrizione,
    NULL                          AS descrizione_estesa,
    p.cod_clv_tipoarticolo        AS tipo_articolo,    -- mappatura da confermare (§5.5)
    NULL                          AS unita_misura,     -- pro_um non esposto da rag_prodotti
    NULL                          AS unita_misura_acquisto,
    NULL                          AS fattore_conversione,
    jsonb_strip_nulls(jsonb_build_object(
        'famiglia', NULLIF(p.cod_famiglia, ''),
        'linea', NULLIF(p.cod_linea, ''),
        'gruppo_merc', NULLIF(p.cod_gruppo_merceologico, ''),
        'gruppo_stat', NULLIF(p.cod_gruppo_statistico, '')
    ))                            AS classificazioni,
    jsonb_strip_nulls(jsonb_build_object(
        'diametro_esterno', NULLIF(p.cod_diametro_esterno, ''),
        'altezza', NULLIF(p.cod_altezza, '')
    ))                            AS attributi,
    NULL                          AS marca,
    NULL                          AS codice_iva,
    NULL                          AS peso_netto,
    NULL                          AS peso_lordo,
    NULL                          AS volume,
    NULL                          AS lunghezza,
    NULL                          AS larghezza,
    NULL                          AS altezza,
    NULL                          AS pezzi_per_confezione,
    NULL                          AS pezzi_per_pallet,
    NULL                          AS gestione_lotti,
    NULL                          AS gestione_matricole,
    NULL                          AS gestito_a_magazzino,
    NULL                          AS scorta_minima,
    NULL                          AS punto_riordino,
    NULL                          AS fornitore_preferenziale_id,
    NULL                          AS costo_standard,
    NULL                          AS costo_ultimo,     -- listino VENCU, decisione 48
    NULL                          AS costo_medio,
    NULLIF(p.ubicazione, '')      AS ubicazione,
    p.incluso_b2b IN ('S', '1', 'true', 'V') AS pubblicato_web,
    p.prodotto_obsoleto IN ('S', '1', 'true', 'V') AS obsoleto,
    jsonb_strip_nulls(jsonb_build_object(
        'codice_alternativo', NULLIF(p.codice_alternativo, ''),
        'codice_esterno', NULLIF(p.codice_esterno, ''),
        'descr_gruppo_merceologico', NULLIF(p.descr_gruppo_merceologico, ''),
        'descr_gruppo_statistico', NULLIF(p.descr_gruppo_statistico, ''),
        'descr_clv_tipoarticolo', NULLIF(p.descr_clv_tipoarticolo, ''),
        'descr_famiglia', NULLIF(p.descr_famiglia, ''),
        'descr_linea', NULLIF(p.descr_linea, ''),
        'descr_diametro_esterno', NULLIF(p.descr_diametro_esterno, ''),
        'descr_altezza', NULLIF(p.descr_altezza, '')
    ))                            AS extra,
    p.data_ultmod::timestamptz    AS data_modifica_origine
FROM rag_prodotti p
WHERE (%(dal)s::timestamptz IS NULL OR p.data_ultmod::timestamptz > %(dal)s::timestamptz)
