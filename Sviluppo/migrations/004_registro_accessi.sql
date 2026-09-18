-- Migrazione 004 — il registro modifiche accoglie anche utenti, gruppi e
-- profili (decisione 68: gestiti dal pannello, non piu' dalla console di
-- Keycloak). Un registro solo per tutte le modifiche fatte dagli amministratori.
ALTER TABLE registro_modifiche DROP CONSTRAINT registro_modifiche_area_check;
ALTER TABLE registro_modifiche ADD CONSTRAINT registro_modifiche_area_check
    CHECK (area IN ('fonti', 'anomalie', 'aspetto', 'vedi-come', 'accessi'));
