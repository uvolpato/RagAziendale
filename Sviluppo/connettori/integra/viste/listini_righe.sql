-- Vista Integra -> listini_righe (MODELLO-DATI-GESTIONALE.md §5.9).
-- Fonte: vista rag_listini_righe. sconti = array dei 4 sconti non nulli.
-- soggetto_id valorizzato solo se la riga e' riservata a un cliente (tipo_cliente = 'C').
SELECT
    r.id_riga_listino::text       AS id_origine,
    r.codice_listino::text        AS listino_id,
    r.id_prodotto::text           AS articolo_id,
    CASE WHEN r.tipo_cliente = 'C' THEN r.id_cliente::text END AS soggetto_id,
    r.prezzo_listino              AS prezzo,
    (SELECT array_agg(x ORDER BY o) FROM (VALUES
        (r.sconto_1, 1), (r.sconto_2, 2), (r.sconto_3, 3), (r.sconto_4, 4)
    ) AS s(x, o) WHERE x IS NOT NULL) AS sconti,
    r.quantita_da                 AS quantita_da,
    r.quantita_a                  AS quantita_a,
    r.data_inizio_validita        AS in_vigore_dal,
    r.data_fine_validita          AS in_vigore_al,
    jsonb_strip_nulls(jsonb_build_object(
        'codice_prodotto', NULLIF(r.codice_prodotto, ''),
        'descrizione_prodotto', NULLIF(r.descrizione_prodotto, ''),
        'id_variante', r.id_variante,
        'codice_iva', NULLIF(r.codice_iva, ''),
        'scala', r.scala,
        'tipo_cliente', r.tipo_cliente
    ))                            AS extra,
    r.data_modifica::timestamptz  AS data_modifica_origine
FROM rag_listini_righe r
WHERE (%(dal)s::timestamptz IS NULL OR r.data_modifica::timestamptz > %(dal)s::timestamptz)
