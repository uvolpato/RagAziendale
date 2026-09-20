-- Migrazione 014 — l'immagine appartiene alla SUA fonte.
--
-- `percorso` era unico su tutta la tabella. Con la scrittura che fa
-- ON CONFLICT ... DO UPDATE (serve a non cambiare gli id quando un documento
-- si rilegge: gli URL gia' consegnati in chat devono continuare a funzionare),
-- due fonti che producessero lo stesso percorso si sarebbero rubate la riga a
-- vicenda — e con la riga l'ACL, perche' i permessi si leggono da `source_id`.
--
-- Non e' teoria: nel database di sviluppo quattro fonti condividono il
-- percorso, e due lo condividono fra AZIENDE DIVERSE (erp.listini_righe fra
-- luis e decobrands). Oggi quelle fonti non producono immagini; lo farebbero
-- domani, quando le foto degli articoli arriveranno dal gestionale.
--
-- Chiave naturale: (source_id, percorso). Ogni fonte possiede le sue righe.
ALTER TABLE immagini DROP CONSTRAINT immagini_percorso_key;
ALTER TABLE immagini ADD CONSTRAINT immagini_fonte_percorso_key UNIQUE (source_id, percorso);
