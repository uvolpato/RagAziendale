-- Vista Integra -> listini_righe (MODELLO-DATI-GESTIONALE.md §5.9).
-- DA VERIFICARE sullo schema reale.
SELECT
    r.id_riga::text               AS id_origine,
    r.codice_listino::text        AS listino_id,
    r.id_prodotto::text           AS articolo_id,
    r.id_cliente::text            AS soggetto_id,     -- solo se tipo_cliente = 'C'
    r.prezzo_listino              AS prezzo,
    NULL                          AS sconti,          -- sconto_1..4 in array
    r.quantita_da                 AS quantita_da,
    r.quantita_a                  AS quantita_a,
    r.data_inizio_validita        AS in_vigore_dal,
    r.data_fine_validita          AS in_vigore_al,
    '{}'::jsonb                   AS extra,
    r.data_modifica::timestamptz  AS data_modifica_origine
FROM b2b_listini_righe r
WHERE r.azi_cdazi = %(codice_azienda)s
  AND (%(dal)s::timestamptz IS NULL OR r.data_modifica::timestamptz > %(dal)s::timestamptz)
