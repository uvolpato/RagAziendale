-- Migrazione 023 — data dell'ultima descrizione di ogni figura. `immagini` non
-- aveva alcuna colonna temporale: non si poteva sapere QUANDO una figura è stata
-- (ri)descritta, e quindi verificare se i parametri di ingestione sono cambiati
-- da allora. A livello documento c'era solo `documenti.indicizzato_il`.
-- `aggiornato_il` si tocca a ogni lettura della figura (scrittura o riscrittura
-- della riga), cosi' un re-indicizzare con nuovi parametri diventa misurabile.
ALTER TABLE immagini ADD COLUMN aggiornato_il timestamptz NOT NULL DEFAULT now();
-- Backfill: le figure esistenti sono state scritte durante l'ultima
-- indicizzazione del loro documento.
UPDATE immagini i SET aggiornato_il = COALESCE(d.indicizzato_il, i.aggiornato_il)
FROM documenti d
WHERE d.source_id = i.source_id AND d.documento = i.documento;