-- Vista Integra -> soggetti (MODELLO-DATI-GESTIONALE.md §5.2).
-- Fonte: vista rag_clienti (DB parallelo "rag"). Il soggetto e' l'anagrafica
-- UNICA: id_origine = id_master (cli_cdcli). Il cliente-per-azienda (cla_cod)
-- sta nel ruolo (§5.3), non qui.
SELECT
    r.id_master::text             AS id_origine,
    r.codice_master               AS codice,
    CASE WHEN r.forma_giuridica IN ('PF', 'DI') THEN 'persona_fisica'
         ELSE 'societa' END       AS tipo_soggetto,
    r.ragione_sociale             AS ragione_sociale,
    r.ragione_sociale_2           AS ragione_sociale_2,
    r.cognome                     AS cognome,
    r.nome                        AS nome,
    r.forma_giuridica             AS forma_giuridica,
    r.partita_iva                 AS partita_iva,
    r.codice_fiscale              AS codice_fiscale,
    NULL                          AS codice_sdi,       -- non esposto da rag_clienti
    r.pec                         AS pec,
    r.email                       AS email,
    r.telefono                    AS telefono,
    r.web                         AS web,
    r.indirizzo                   AS indirizzo,
    r.cap                         AS cap,
    r.citta                       AS citta,
    r.provincia                   AS provincia,
    r.regione                     AS regione,
    r.stato                       AS nazione,          -- cli_stacod
    NULL                          AS settore,
    '{}'::jsonb                   AS classificazioni,  -- zona sta nel ruolo (§5.3)
    NULL                          AS note,
    jsonb_strip_nulls(jsonb_build_object(
        'fax', NULLIF(r.fax, ''),
        'indirizzo_2', NULLIF(r.indirizzo_2, ''),
        'id_cliente', r.id_cliente,
        'codice_cliente', NULLIF(r.codice_cliente, ''),
        'fatturazione_elettronica', r.fatturazione_elettronica
    ))                            AS extra,
    r.data_modifica::timestamptz  AS data_modifica_origine
FROM rag_clienti r
WHERE (%(dal)s::timestamptz IS NULL OR r.data_modifica::timestamptz > %(dal)s::timestamptz)
