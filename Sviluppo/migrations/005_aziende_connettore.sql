-- Migrazione 005 — ogni azienda ha il SUO connettore al gestionale di
-- riferimento (decisione 69). Sostituisce l'idea di "un gestionale condiviso
-- identificato da fonte + codice".
--
-- CHIAVI DEI DATI IMPORTATI (rivede MODELLO-DATI-GESTIONALE.md §3):
--     id = '<codice azienda nostra>:<id nel gestionale>'      es. 'luis:20657'
-- Lo spazio dei nomi e' la NOSTRA azienda, non il gestionale: due aziende
-- sullo stesso tipo di gestionale, anche con lo stesso codice interno (001 e'
-- frequente), non si pestano mai i piedi. Le tabelle erp_storico sono vuote:
-- nessun dato da convertire. Il commento della 002 descrive il formato vecchio
-- e non si tocca (migrazione gia' applicata): vale questo.

ALTER TABLE aziende DROP CONSTRAINT aziende_fonte_codice_origine_key;
ALTER TABLE aziende RENAME COLUMN fonte TO connettore;         -- tipo: 'integra', ...
ALTER TABLE aziende ALTER COLUMN connettore DROP NOT NULL;     -- NULL = non ancora collegata
ALTER TABLE aziende ALTER COLUMN codice_origine DROP NOT NULL; -- serve solo se il gestionale e' multi-azienda
ALTER TABLE aziende
    ADD COLUMN creata_il timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN note      text,
    -- Il codice finisce nel gruppo Keycloak 'azienda-<codice>' e nelle chiavi
    -- di ogni record importato: corto, stabile, senza sorprese negli URL.
    ADD CONSTRAINT codice_valido CHECK (codice ~ '^[a-z0-9][a-z0-9-]{1,30}$');

COMMENT ON COLUMN aziende.connettore IS 'Tipo di connettore al gestionale; credenziali in .env, mai qui';
COMMENT ON COLUMN aziende.codice_origine IS 'Codice dell''azienda DENTRO il gestionale (es. azi_cdazi di Integra), se multi-azienda';
