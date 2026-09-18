-- Vista Integra -> soggetti (modello canonico, MODELLO-DATI-GESTIONALE.md §5.2).
--
-- CONVENZIONI (valgono per tutte le viste in questa cartella):
--   * il risultato DEVE avere una colonna `id_origine` (l'id nel gestionale):
--     l'id canonico `<azienda>:<id_origine>` lo compone il motore (base.py);
--   * `extra` raccoglie i campi del gestionale che il modello non prevede:
--     qui jsonb vuoto per ora, da riempire con i campi rimasti fuori;
--   * `data_modifica_origine` abilita la lettura incrementale; se il gestionale
--     non la tiene, lasciare NULL e la strategia dell'entita' diventa completa;
--   * placeholder `%(codice_azienda)s` e `%(dal)s` iniettati dal connettore.
--
-- DA VERIFICARE sullo schema reale: i nomi delle colonne vengono dal contratto
-- B2B (DB Integra - Luis/b2b_*.sql) e da MODELLO-DATI-GESTIONALE.md §5.2, non
-- dallo schema vero, che in sviluppo non e' raggiungibile.
SELECT
    c.id_cliente::text           AS id_origine,
    c.codice_cliente::text       AS codice,
    CASE WHEN c.forma_giuridica IN ('PF', 'DI') THEN 'persona_fisica'
         ELSE 'societa' END      AS tipo_soggetto,      -- da verificare
    c.ragione_sociale            AS ragione_sociale,
    c.ragione_sociale2           AS ragione_sociale_2,
    c.cognome                    AS cognome,
    c.nome                       AS nome,
    c.forma_giuridica            AS forma_giuridica,
    c.partita_iva                AS partita_iva,
    c.codice_fiscale             AS codice_fiscale,
    c.pec                        AS pec,
    c.email                      AS email,
    c.telefono                   AS telefono,
    c.web                        AS web,
    c.indirizzo                  AS indirizzo,
    c.cap                        AS cap,
    c.citta                      AS citta,
    c.provincia                  AS provincia,
    c.regione                    AS regione,
    c.stato                      AS nazione,
    '{}'::jsonb                  AS extra,
    c.data_modifica::timestamptz AS data_modifica_origine   -- da verificare
FROM b2b_clienti c
WHERE c.azi_cdazi = %(codice_azienda)s
  AND (%(dal)s::timestamptz IS NULL OR c.data_modifica::timestamptz > %(dal)s::timestamptz)
