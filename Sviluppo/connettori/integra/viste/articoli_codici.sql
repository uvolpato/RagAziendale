-- Vista Integra -> articoli_codici (MODELLO-DATI-GESTIONALE.md §5.6).
-- Fonte: vista rag_prodotti, colonne codice_alternativo (pro_cod1) e
-- codice_esterno (pro_cod2). Due righe per articolo quando entrambi valorizzati.
SELECT p.pro_id::text || ':alt'  AS id_origine,
       p.pro_id::text            AS articolo_id,
       'alternativo'             AS tipo,
       p.codice_alternativo::text AS codice,
       NULL                      AS soggetto_id,
       '{}'::jsonb               AS extra,
       p.data_ultmod::timestamptz AS data_modifica_origine
FROM rag_prodotti p
WHERE NULLIF(p.codice_alternativo, '') IS NOT NULL
  AND (%(dal)s::timestamptz IS NULL OR p.data_ultmod::timestamptz > %(dal)s::timestamptz)
UNION ALL
SELECT p.pro_id::text || ':est'  AS id_origine,
       p.pro_id::text            AS articolo_id,
       'esterno'                 AS tipo,
       p.codice_esterno::text    AS codice,
       NULL                      AS soggetto_id,
       '{}'::jsonb               AS extra,
       p.data_ultmod::timestamptz AS data_modifica_origine
FROM rag_prodotti p
WHERE NULLIF(p.codice_esterno, '') IS NOT NULL
  AND (%(dal)s::timestamptz IS NULL OR p.data_ultmod::timestamptz > %(dal)s::timestamptz)
