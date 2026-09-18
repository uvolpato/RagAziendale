-- Vista Integra -> documenti_righe, ciclo VENDITA (MODELLO-DATI-GESTIONALE.md §5.11).
-- Fonte: vista rag_righe_ordini. La vista NON espone la data di modifica della
-- riga (solo quella della testata via rag_ordini_clienti): qui data_modifica_origine
-- resta NULL e le righe si rileggono per intero a ogni importazione (idempotente
-- via SCD2). Se servira' l'incrementale sulle righe, andra' aggiunta una colonna
-- alla vista rag_righe_ordini.
SELECT
    r.id_riga::text               AS id_origine,
    r.id_ordine::text             AS documento_id,
    r.ordine_riga::integer        AS numero_riga,
    'articolo'                    AS tipo_riga,
    r.id_prodotto::text           AS articolo_id,
    r.codice_prodotto::text       AS codice_articolo,
    r.descrizione_riga            AS descrizione,
    r.quantita                    AS quantita,
    r.unita_misura                AS unita_misura,
    r.prezzo_listino              AS prezzo_listino,
    (SELECT array_agg(x ORDER BY o) FROM (VALUES
        (r.sconto_1, 1), (r.sconto_2, 2), (r.sconto_3, 3), (r.sconto_4, 4)
    ) AS s(x, o) WHERE x IS NOT NULL) AS sconti,
    r.prezzo_netto                AS prezzo_netto,
    r.importo                     AS importo,
    NULL                          AS codice_iva,        -- non esposto da rag_righe_ordini
    NULL                          AS costo_unitario,
    NULL                          AS provvigione_pct,
    NULL                          AS magazzino,
    NULL                          AS lotto,
    NULL                          AS data_consegna_prevista,
    NULL                          AS quantita_evasa,
    r.quantita_fatturata          AS quantita_fatturata,
    NULL                          AS riga_origine_id,
    NULLIF(r.note_riga, '')       AS note,
    jsonb_strip_nulls(jsonb_build_object(
        'numero_ordine', r.numero_ordine,
        'data_ordine', r.data_ordine,
        'id_cliente', r.id_cliente,
        'descrizione_prodotto', NULLIF(r.descrizione_prodotto, ''),
        'prezzo_ivato', r.prezzo_ivato,
        'valore_sconto', r.valore_sconto,
        'stato_saldo', r.stato_saldo,
        'stato_fatturazione', r.stato_fatturazione,
        'importo_fatturato', r.importo_fatturato
    ))                            AS extra,
    NULL                          AS data_modifica_origine
FROM rag_righe_ordini r
