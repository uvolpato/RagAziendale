-- Migrazione 010 — un documento puo' essere visto ma lasciato fuori, con il
-- motivo in `errore` (decisione 72): fogli di calcolo con i prezzi, formati
-- che non si leggono (.xls, .ods). Compaiono nella scheda Documenti invece di
-- sparire senza dire niente.
ALTER TABLE documenti DROP CONSTRAINT documenti_stato_check;
ALTER TABLE documenti ADD CONSTRAINT documenti_stato_check
    CHECK (stato IN ('indicizzato', 'errore', 'vuoto', 'escluso'));
