-- Migrazione 008 — il registro modifiche accoglie anche le azioni sui sistemi
-- (riavvio/stop dei container, dal pannello). Area nuova: 'sistemi'.
ALTER TABLE registro_modifiche DROP CONSTRAINT registro_modifiche_area_check;
ALTER TABLE registro_modifiche ADD CONSTRAINT registro_modifiche_area_check
    CHECK (area IN ('fonti', 'anomalie', 'aspetto', 'vedi-come', 'accessi', 'gestionali', 'sistemi'));
