-- Vista Integra -> codici (MODELLO-DATI-GESTIONALE.md §5.15).
-- Tutte le tabelle codice->descrizione in una sola: tipo + codice + descrizione.
-- id_origine = chiave naturale '<tipo>:<codice>'. Le viste rag_* coprono
-- pagamenti, porti e spedizioni; vettori, famiglie e linee non hanno ancora una
-- vista rag_* (v. README).
SELECT ('pagamento:' || t.codice_pagamento)::text AS id_origine,
       'pagamento' AS tipo,
       t.codice_pagamento::text AS codice,
       t.descrizione_pagamento AS descrizione,
       NULL AS padre,
       jsonb_strip_nulls(jsonb_build_object(
           'tipo_scadenza', t.tipo_scadenza,
           'tipo_iva', t.tipo_iva,
           'sconto_cassa', t.sconto_cassa,
           'gg_fine_mese', t.gg_fine_mese,
           'gg_esclusi', t.gg_esclusi,
           'obsoleto', t.obsoleto
       )) AS dettagli,
       '{}'::jsonb AS extra,
       t.data_modifica::timestamptz AS data_modifica_origine
FROM rag_tabpag t
WHERE (%(dal)s::timestamptz IS NULL OR t.data_modifica::timestamptz > %(dal)s::timestamptz)
UNION ALL
SELECT ('porto:' || t.codice_porto)::text, 'porto',
       t.codice_porto::text, t.descrizione_porto, NULL,
       jsonb_strip_nulls(jsonb_build_object('obsoleto', t.obsoleto)),
       '{}'::jsonb, t.data_modifica::timestamptz
FROM rag_tabpor t
WHERE (%(dal)s::timestamptz IS NULL OR t.data_modifica::timestamptz > %(dal)s::timestamptz)
UNION ALL
SELECT ('spedizione:' || t.codice_spedizione)::text, 'spedizione',
       t.codice_spedizione::text, t.descrizione_spedizione, NULL,
       jsonb_strip_nulls(jsonb_build_object('obsoleto', t.obsoleto)),
       '{}'::jsonb, t.data_modifica::timestamptz
FROM rag_tabspe t
WHERE (%(dal)s::timestamptz IS NULL OR t.data_modifica::timestamptz > %(dal)s::timestamptz)
