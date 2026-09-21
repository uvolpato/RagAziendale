-- Migrazione 015 — ultimi gruppi conosciuti di ogni utente.
--
-- Serve a rispondere a una domanda che prima non si poteva fare: «questa
-- persona puo' vedere QUESTA immagine, adesso?».
--
-- Un tag <img> nel browser non manda nessuna identita': solo i cookie del
-- dominio. Finora bastava la firma dell'URL, emessa quando il gate aveva
-- approvato il pezzo — ma una firma vale finche' non scade, anche se nel
-- frattempo la fonte e' stata sospesa o la persona e' uscita dal gruppo.
--
-- Qui si tiene l'ultimo elenco di gruppi visto nel token di ogni utente
-- (scritto a ogni turno di chat). Servendo l'immagine si rilegge da `sources`
-- lo stato ATTUALE della fonte e lo si incrocia con questi gruppi: la
-- sospensione di una fonte ha effetto subito, un cambio di gruppi al primo
-- messaggio successivo della persona.
--
-- Non e' un archivio di identita': niente nomi, niente email. Solo il
-- soggetto del token (`sub`) e i gruppi, che stanno gia' nel token stesso.
CREATE TABLE gruppi_utente (
    utente        text PRIMARY KEY,
    gruppi        text[] NOT NULL,
    aggiornato_il timestamptz NOT NULL DEFAULT now()
);
