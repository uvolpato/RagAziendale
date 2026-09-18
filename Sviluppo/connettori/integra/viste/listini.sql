-- Vista Integra -> listini (MODELLO-DATI-GESTIONALE.md §5.9).
-- Fonte: vista rag_listini_testata. Il ciclo non e' esposto dalla vista: si
-- ipotizza tls_tipo 'A' = acquisto, altrimenti vendita (da confermare).
SELECT
    l.codice_listino::text        AS id_origine,
    l.codice_listino::text        AS codice,
    l.descrizione_listino         AS descrizione,
    CASE WHEN l.tipo_listino = 'A' THEN 'acquisto' ELSE 'vendita' END AS ciclo,
    l.listino_con_iva IN ('S', '1', 'true', 'V') AS con_iva,
    NULLIF(l.codice_valuta, '')   AS valuta,
    NULL                          AS in_vigore_dal,    -- non esposto dalla vista
    NULL                          AS in_vigore_al,
    l.listino_obsoleto IN ('S', '1', 'true', 'V') AS obsoleto,
    jsonb_strip_nulls(jsonb_build_object(
        'tipo_listino', l.tipo_listino,
        'n_decimali', l.n_decimali,
        'prezzi_netto', l.prezzi_netto
    ))                            AS extra,
    l.data_modifica::timestamptz  AS data_modifica_origine
FROM rag_listini_testata l
WHERE (%(dal)s::timestamptz IS NULL OR l.data_modifica::timestamptz > %(dal)s::timestamptz)
