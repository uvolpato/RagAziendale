-- Vista Integra -> indirizzi (MODELLO-DATI-GESTIONALE.md §5.4).
-- DA VERIFICARE sullo schema reale, come soggetti.sql.
SELECT
    d.id_destinazione::text      AS id_origine,
    d.id_cliente::text           AS soggetto_id,
    d.codice_tipo_destinazione   AS tipo,
    d.ragione_sociale            AS ragione_sociale,
    d.indirizzo                  AS indirizzo,
    d.indirizzo_2                AS indirizzo_2,
    d.cap                        AS cap,
    d.citta                      AS citta,
    d.provincia                  AS provincia,
    d.regione                    AS regione,
    d.nazione                    AS nazione,
    d.latitudine                 AS latitudine,
    d.longitudine                AS longitudine,
    d.flag_abituale              AS abituale,
    d.codice_zona                AS zona,
    d.codice_agente              AS agente_id,
    d.codice_vettore             AS vettore_id,
    d.codice_porto               AS porto,
    d.giorni_preparazione        AS giorni_preparazione,
    '{}'::jsonb                  AS extra,
    d.data_modifica::timestamptz AS data_modifica_origine
FROM b2b_destinazioni_clienti d
WHERE d.azi_cdazi = %(codice_azienda)s
  AND (%(dal)s::timestamptz IS NULL OR d.data_modifica::timestamptz > %(dal)s::timestamptz)
