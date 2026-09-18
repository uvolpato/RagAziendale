-- Migrazione 007 — pianificazione delle importazioni (SPECIFICA-CONNETTORI.md §7.5).
--
-- Una riga per (azienda, entita) per MODIFICARE la frequenza predefinita
-- (definita nel codice, base.scheda.FREQUENZE). Se non c'e' una riga, vale il
-- predefinito. La riga puo' disattivare una singola importazione (attiva=false).
-- Il pannello la mostrera' in "Gestione importazioni" (Dagster, decisione 60);
-- il servizio `connettori` la legge per lo scheduler ponte (prima di Dagster).

CREATE TABLE pianificazioni (
    azienda           text NOT NULL,
    entita            text NOT NULL,
    frequenza_secondi integer NOT NULL CHECK (frequenza_secondi >= 60),
    attiva            boolean NOT NULL DEFAULT true,
    PRIMARY KEY (azienda, entita)
);

COMMENT ON TABLE pianificazioni IS 'Frequenze di importazione per azienda x entita: assenza = predefinito (base.scheda)';
