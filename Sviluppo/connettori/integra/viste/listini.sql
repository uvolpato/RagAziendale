-- Vista Integra -> listini (MODELLO-DATI-GESTIONALE.md §5.9).
-- DA VERIFICARE sullo schema reale.
SELECT
    l.codice_listino::text        AS id_origine,
    l.codice_listino::text        AS codice,
    l.descrizione_listino         AS descrizione,
    CASE WHEN l.tipo_listino = 'A' THEN 'acquisto' ELSE 'vendita' END AS ciclo,
    l.listino_con_iva             AS con_iva,
    l.codice_valuta               AS valuta,
    l.data_inizio_validita        AS in_vigore_dal,
    l.data_fine_validita          AS in_vigore_al,
    l.listino_obsoleto            AS obsoleto,
    '{}'::jsonb                   AS extra,
    l.data_modifica::timestamptz  AS data_modifica_origine
FROM b2b_listini_testata l
WHERE l.azi_cdazi = %(codice_azienda)s
  AND (%(dal)s::timestamptz IS NULL OR l.data_modifica::timestamptz > %(dal)s::timestamptz)
