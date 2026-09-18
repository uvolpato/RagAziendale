-- Vista Integra -> codici (MODELLO-DATI-GESTIONALE.md §5.15).
-- Tutte le tabelle codice->descrizione in una sola: tipo + codice + descrizione.
-- id_origine = chiave naturale '<tipo>:<codice>' (MODELLO §3). DA VERIFICARE.
SELECT ('pagamento:' || c.codice)::text AS id_origine, 'pagamento' AS tipo,
       c.codice::text AS codice, c.descrizione AS descrizione, NULL AS padre,
       '{}'::jsonb AS extra, NULL AS data_modifica_origine
FROM b2b_tabpag c WHERE c.azi_cdazi = %(codice_azienda)s
UNION ALL
SELECT ('porto:' || c.codice)::text, 'porto', c.codice::text, c.descrizione, NULL, '{}'::jsonb, NULL
FROM b2b_tabpor c WHERE c.azi_cdazi = %(codice_azienda)s
UNION ALL
SELECT ('spedizione:' || c.codice)::text, 'spedizione', c.codice::text, c.descrizione, NULL, '{}'::jsonb, NULL
FROM b2b_tabspe c WHERE c.azi_cdazi = %(codice_azienda)s
UNION ALL
SELECT ('vettore:' || c.codice)::text, 'vettore', c.codice::text, c.descrizione, NULL, '{}'::jsonb, NULL
FROM b2b_vettori c WHERE c.azi_cdazi = %(codice_azienda)s
