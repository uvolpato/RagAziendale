-- Vista Integra -> indirizzi (MODELLO-DATI-GESTIONALE.md §5.4).
-- Fonte: vista rag_indirizzi_clienti. Il "soggetto" a cui l'indirizzo appartiene
-- e' id_cliente (qui = cli_cdcli = id_master, coerente con soggetti.sql).
-- Il tipo si ricava da flag_spedizione + codice_tipo_destinazione (mappatura
-- da confermare sullo schema reale: i codici dst_dsfcod vanno decodificati).
SELECT
    d.id_destinazione::text       AS id_origine,
    d.id_cliente::text            AS soggetto_id,
    CASE WHEN d.flag_spedizione IN ('S', '1', 'true', 'V') THEN 'consegna'
         ELSE 'sede' END          AS tipo,
    d.ragione_sociale             AS ragione_sociale,
    d.indirizzo                   AS indirizzo,
    d.indirizzo_2                 AS indirizzo_2,
    d.cap                         AS cap,
    d.citta                       AS citta,
    d.provincia                   AS provincia,
    NULL                          AS regione,          -- non esposta da rag_indirizzi_clienti
    NULL                          AS nazione,
    NULL                          AS latitudine,
    NULL                          AS longitudine,
    d.flag_abituale IN ('S', '1', 'true', 'V') AS abituale,
    NULLIF(d.codice_zona, '')     AS zona,
    NULLIF(d.codice_agente, '')   AS agente_id,
    NULLIF(d.codice_vettore, '')  AS vettore_id,
    NULLIF(d.codice_porto, '')    AS porto,
    d.giorni_preparazione         AS giorni_preparazione,
    NULL                          AS orari_consegna,
    jsonb_strip_nulls(jsonb_build_object(
        'codice_tipo_destinazione', NULLIF(d.codice_tipo_destinazione, ''),
        'flag_spedizione', d.flag_spedizione,
        'km', d.km,
        'ordinamento', d.ordinamento,
        'layout_linea', d.layout_linea,
        'stato_destinazione', d.stato_destinazione,
        'referente', NULLIF(d.referente, ''),
        'telefono_referente', NULLIF(d.telefono_referente, '')
    ))                            AS extra,
    d.data_modifica::timestamptz  AS data_modifica_origine
FROM rag_indirizzi_clienti d
WHERE (%(dal)s::timestamptz IS NULL OR d.data_modifica::timestamptz > %(dal)s::timestamptz)
