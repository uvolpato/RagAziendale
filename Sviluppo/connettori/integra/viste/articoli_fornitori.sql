-- Vista Integra -> articoli_fornitori (MODELLO-DATI-GESTIONALE.md §5.7).
-- DA VERIFICARE: la tabella prosoggetti (tipo 'F') del gestionale.
SELECT
    f.id::text                    AS id_origine,
    f.pro_id::text                AS articolo_id,
    f.id_fornitore::text          AS fornitore_id,
    f.codice_fornitore            AS codice_fornitore,
    f.descrizione_fornitore       AS descrizione_fornitore,
    f.prezzo_acquisto             AS prezzo_acquisto,
    f.valuta                      AS valuta,
    NULL                          AS sconti,
    f.quantita_minima             AS quantita_minima,
    NULL                          AS multiplo,        -- psg_liberon1: da confermare
    f.lotto_riordino              AS lotto_riordino,
    f.giorni_consegna             AS giorni_consegna,
    NULL                          AS preferenziale,
    '{}'::jsonb                   AS extra,
    f.data_modifica::timestamptz  AS data_modifica_origine
FROM prosoggetti f
WHERE f.azi_cdazi = %(codice_azienda)s
  AND f.tipo_soggetto = 'F'
  AND (%(dal)s::timestamptz IS NULL OR f.data_modifica::timestamptz > %(dal)s::timestamptz)
