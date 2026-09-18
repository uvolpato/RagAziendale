-- Vista Integra -> soggetti_ruoli (MODELLO-DATI-GESTIONALE.md §5.3).
-- Fonte: vista rag_clienti. rag_clienti espone SOLO il tipo 'C' (clienti):
-- fornitori/agenti/vettori non hanno ancora una vista rag_*. Quindi qui il
-- ruolo e' sempre 'cliente'; gli altri ruoli arriveranno quando ci saranno le
-- viste. id_origine = id_master (un cliente = un ruolo oggi; quando arriveranno
-- i fornitori servira' una chiave composta master+ruolo).
SELECT
    r.id_master::text             AS id_origine,
    r.id_master::text             AS soggetto_id,
    'cliente'                     AS ruolo,
    r.codice_cliente              AS codice_ruolo,
    NULLIF(r.codice_agente, '')   AS agente_id,
    NULLIF(r.codice_listino, '')  AS listino,
    NULLIF(r.codice_pagamento, '') AS pagamento,
    NULLIF(r.codice_porto, '')    AS porto,
    NULLIF(r.codice_spedizione, '') AS spedizione,
    NULLIF(r.codice_vettore, '')  AS vettore_id,
    NULLIF(r.codice_valuta, '')   AS valuta,
    NULLIF(r.codice_iva, '')      AS codice_iva,
    NULLIF(r.codice_zona, '')     AS zona,
    NULL                          AS categoria,
    NULL                          AS sconti,           -- rag_clienti non li espone
    r.fido_totale                 AS fido,
    r.fido_scadenze               AS fido_scadenze,
    NULL                          AS bloccato,
    NULL                          AS motivo_blocco,
    NULL                          AS provvigione_pct,
    NULL                          AS attivo_dal,
    NULL                          AS attivo_al,
    jsonb_strip_nulls(jsonb_build_object(
        'tipo_fatturazione', r.tipo_fatturazione,
        'importo_minimo_fattura', r.importo_minimo_fattura,
        'fido_concessione', r.fido_concessione,
        'codice_conto', NULLIF(r.codice_conto, '')
    ))                            AS extra,
    r.data_modifica::timestamptz  AS data_modifica_origine
FROM rag_clienti r
WHERE (%(dal)s::timestamptz IS NULL OR r.data_modifica::timestamptz > %(dal)s::timestamptz)
