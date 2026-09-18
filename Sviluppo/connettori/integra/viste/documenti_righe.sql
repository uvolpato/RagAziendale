-- Vista Integra -> documenti_righe, ciclo VENDITA (MODELLO-DATI-GESTIONALE.md §5.11).
-- DA VERIFICARE sullo schema reale.
SELECT
    r.id_riga::text               AS id_origine,
    r.id_ordine::text             AS documento_id,
    r.ordine_riga::integer        AS numero_riga,
    'articolo'                    AS tipo_riga,
    r.id_prodotto::text           AS articolo_id,
    r.codice_prodotto::text       AS codice_articolo,
    r.descrizione_riga            AS descrizione,
    r.quantita                    AS quantita,
    NULL                          AS unita_misura,
    r.prezzo_listino              AS prezzo_listino,
    NULL                          AS sconti,         -- sconto_1..4
    NULL                          AS prezzo_netto,
    r.importo_riga                AS importo,
    NULL                          AS codice_iva,
    NULL                          AS costo_unitario,
    NULL                          AS provvigione_pct,
    NULL                          AS magazzino,
    NULL                          AS lotto,
    r.data_consegna               AS data_consegna_prevista,
    NULL                          AS quantita_evasa,
    r.quantita_fatturata          AS quantita_fatturata,
    NULL                          AS riga_origine_id,
    r.note_riga                   AS note,
    '{}'::jsonb                   AS extra,
    r.data_modifica::timestamptz  AS data_modifica_origine
FROM b2b_righe_ordini r
WHERE r.azi_cdazi = %(codice_azienda)s
  AND (%(dal)s::timestamptz IS NULL OR r.data_modifica::timestamptz > %(dal)s::timestamptz)
