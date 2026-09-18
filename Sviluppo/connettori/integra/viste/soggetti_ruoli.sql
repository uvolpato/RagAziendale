-- Vista Integra -> soggetti_ruoli (MODELLO-DATI-GESTIONALE.md §5.3).
-- DA VERIFICARE sullo schema reale, come soggetti.sql.
SELECT
    r.id_cliente::text           AS id_origine,          -- composita? da confermare
    r.id_cliente::text           AS soggetto_id,
    CASE r.tipo_anagrafica WHEN 'C' THEN 'cliente' WHEN 'F' THEN 'fornitore' END AS ruolo,
    r.codice_cliente             AS codice_ruolo,
    r.codice_agente              AS agente_id,
    r.codice_listino             AS listino,
    r.codice_pagamento           AS pagamento,
    r.codice_porto               AS porto,
    r.codice_spedizione          AS spedizione,
    r.codice_vettore             AS vettore_id,
    r.codice_valuta              AS valuta,
    r.codice_iva                 AS codice_iva,
    r.codice_zona                AS zona,
    r.codice_categoria           AS categoria,
    r.sconti                     AS sconti,              -- numeric[] da sconto_1..n
    r.fido_totale                AS fido,
    r.fido_scadenze              AS fido_scadenze,
    r.bloccato                   AS bloccato,
    r.motivo_blocco              AS motivo_blocco,
    r.provvigione_pct            AS provvigione_pct,
    '{}'::jsonb                  AS extra,
    r.data_modifica::timestamptz AS data_modifica_origine
FROM b2b_clienti r
WHERE r.azi_cdazi = %(codice_azienda)s
  AND (%(dal)s::timestamptz IS NULL OR r.data_modifica::timestamptz > %(dal)s::timestamptz)
