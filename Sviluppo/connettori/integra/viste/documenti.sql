-- Vista Integra -> documenti, ciclo VENDITA (MODELLO-DATI-GESTIONALE.md §5.10).
-- Fonte: vista rag_ordini_clienti = SOLO ordini di vendita (mvt_natmov = 'ORD').
-- DDT, fatture e documenti di acquisto non hanno ancora una vista rag_*.
SELECT
    o.id_ordine::text             AS id_origine,
    'vendita'                     AS ciclo,
    'ordine'                      AS tipo,
    'ORD'                         AS tipo_origine,
    o.numero_ordine::text         AS numero,
    o.serie::text                 AS serie,
    o.anno_ordine::smallint       AS anno,
    o.data_ordine                 AS data_documento,
    o.data_registrazione          AS data_registrazione,
    o.data_trasporto              AS data_consegna_prevista,
    NULL                          AS data_consegna,
    o.id_cliente::text            AS soggetto_id,
    o.id_destinazione_merce::text AS indirizzo_consegna_id,
    o.id_destinazione_fattura::text AS indirizzo_fatturazione_id,
    NULL                          AS agente_id,        -- non esposto da rag_ordini_clienti
    NULL                          AS listino,
    NULLIF(o.codice_pagamento, '') AS pagamento,
    NULLIF(o.codice_porto, '')    AS porto,
    NULLIF(o.codice_spedizione, '') AS spedizione,
    NULLIF(o.codice_vettore, '')  AS vettore_id,
    NULLIF(o.codice_valuta, '')   AS valuta,
    NULL                          AS cambio,
    (SELECT array_agg(x ORDER BY o) FROM (VALUES
        (o.sconto_1, 1), (o.sconto_2, 2), (o.sconto_3, 3), (o.sconto_4, 4)
    ) AS s(x, o) WHERE x IS NOT NULL) AS sconti,
    o.importo_imponibile          AS imponibile,
    o.importo_iva                 AS iva,
    NULL                          AS totale,
    NULL                          AS spese_trasporto,
    NULL                          AS spese_incasso,
    NULL                          AS peso_totale,
    NULL                          AS colli,
    NULLIF(o.riferimento_ordine_cliente, '') AS riferimento_soggetto,
    o.data_riferimento_ordine     AS data_riferimento_soggetto,
    CASE WHEN o.flag_fatturato IN ('S', '1', 'true', 'V') THEN 'evaso'
         WHEN o.stato_saldo IN ('S', '1', 'true', 'V') THEN 'aperto'
         ELSE 'aperto' END        AS stato,
    o.stato_saldo::text           AS stato_origine,
    CASE WHEN NULLIF(o.riferimento_b2b, '') IS NOT NULL THEN 'b2b' ELSE NULL END AS canale,
    o.utente_inserimento::text    AS utente_origine,
    NULLIF(o.note_ordine, '')     AS note,
    jsonb_strip_nulls(jsonb_build_object(
        'numero_progressivo', o.numero_progressivo,
        'data_valuta', o.data_valuta,
        'data_competenza', o.data_competenza,
        'base_imponibile', o.base_imponibile,
        'sconto_finale', o.sconto_finale,
        'riferimento_b2b', NULLIF(o.riferimento_b2b, ''),
        'data_riferimento_b2b', o.data_riferimento_b2b,
        'id_destinazione_committente', o.id_destinazione_committente,
        'flag_contabilizzato', o.flag_contabilizzato,
        'stato_verifica', o.stato_verifica
    ))                            AS extra,
    o.data_modifica::timestamptz  AS data_modifica_origine
FROM rag_ordini_clienti o
WHERE (%(dal)s::timestamptz IS NULL OR o.data_modifica::timestamptz > %(dal)s::timestamptz)
