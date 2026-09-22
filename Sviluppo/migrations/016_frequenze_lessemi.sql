-- Migrazione 016 — quanto e' RARA ogni parola dell'archivio.
--
-- Il ramo lessicale della ricerca ibrida ordinava i risultati contando quante
-- volte una parola compare DENTRO il pezzo, senza mai chiedersi quanto quella
-- parola sia rara nell'archivio. Misurato il 22/09/2026 su «quanto costa il
-- DST2040»: i primi cinque risultati non contenevano il codice, perche'
-- «costa» valeva quanto «DST2040» — che compare in 2 pezzi su 3112.
--
-- Qui si tiene il conto, una riga per lessema (la parola gia' ridotta alla
-- radice da Postgres: «ciottoli» e «ciottolo» sono lo stesso lessema). Si
-- riscrive quando si indicizza, che e' l'unico momento in cui l'archivio
-- cambia: contarlo a ogni ricerca vorrebbe dire rileggere tutto l'indice ogni
-- volta.
--
-- Non e' un elenco di parole da ignorare scritto a mano. Nessuno decide che
-- «costa» conta poco: lo decide l'archivio, contando. Su un archivio di
-- ricette «forno» sarebbe comune e «bergamotto» raro, senza toccare niente.

CREATE TABLE IF NOT EXISTS lessemi (
    parola text    PRIMARY KEY,
    pezzi  integer NOT NULL CHECK (pezzi > 0)   -- in quanti pezzi compare
);

-- Il totale serve dentro la formula (ln(totale / pezzi)), e serve esatto nel
-- momento in cui si e' contato: prenderlo da `count(*)` a ogni ricerca darebbe
-- un totale piu' nuovo dei conteggi, cioe' due misure di archivi diversi.
CREATE TABLE IF NOT EXISTS lessemi_stato (
    id            integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    pezzi_totali  integer NOT NULL DEFAULT 0,
    aggiornato_il timestamptz NOT NULL DEFAULT now()
);

INSERT INTO lessemi_stato (id, pezzi_totali) VALUES (1, 0)
ON CONFLICT (id) DO NOTHING;
