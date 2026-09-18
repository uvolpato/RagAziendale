-- Vista Integra -> documenti, ciclo VENDITA (MODELLO-DATI-GESTIONALE.md §5.10).
-- ⚠ Il contratto B2B espone solo gli ORDINI di vendita (b2b_ordini_clienti).
-- DDT, fatture e documenti di acquisto vanno aggiunti al connettore (e al B2B)
-- per avere il fatturato. DA VERIFICARE sullo schema reale.
SELECT
    o.id_ordine::text             AS id_origine,
    'vendita'                     AS ciclo,
    'ordine'                      AS tipo,           -- da mvt_natmov
    o.mvt_natmov::text            AS tipo_origine,
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
    o.codice_agente::text         AS agente_id,
    o.codice_listino::text        AS listino,
    o.codice_pagamento::text      AS pagamento,
    o.codice_porto::text          AS porto,
    o.codice_spedizione::text     AS spedizione,
    o.codice_vettore::text        AS vettore_id,
    o.codice_valuta::text         AS valuta,
    NULL                          AS cambio,
    NULL                          AS sconti,         -- sconto_1..4 + sconto_finale
    o.importo_imponibile          AS imponibile,
    o.importo_iva                 AS iva,
    NULL                          AS totale,
    o.riferimento_ordine_cliente  AS riferimento_soggetto,
    o.data_riferimento_ordine     AS data_riferimento_soggetto,
    o.stato_saldo::text           AS stato,
    NULL                          AS stato_origine,
    'b2b'                         AS canale,         -- mvt_liberoc5 = 'B2B'
    o.utente_inserimento::text    AS utente_origine,
    o.note_ordine                 AS note,
    '{}'::jsonb                   AS extra,
    o.data_modifica::timestamptz  AS data_modifica_origine
FROM b2b_ordini_clienti o
WHERE o.azi_cdazi = %(codice_azienda)s
  AND (%(dal)s::timestamptz IS NULL OR o.data_modifica::timestamptz > %(dal)s::timestamptz)
