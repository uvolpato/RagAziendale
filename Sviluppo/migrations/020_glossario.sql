-- Migrazione 020 — «sinonimi» diventa «glossario».
--
-- Il nome «sinonimi» diceva la meta' della cosa: la tabella non porta solo i
-- sinonimi di una parola, porta il vocabolario multilingue di dominio estratto
-- dai cataloghi («pietre» -> «rocks, pierres, dekosteine»). «Glossario» e' il
-- nome giusto per una memoria di termini di dominio, e allinea il nome alla
-- sostanza: e' memoria estratta dal corpus, non una lista di sinonimi generici.
--
-- Stesso contenuto, nuovi nomi: tabella `glossario`, colonna `voce` (la parola)
-- e `termini` (le sue traduzioni/sinonimi). I dati restano; li ripopolera' il
-- modello leggendo il corpus (estrai_glossario), non piu' la co-occorrenza.

ALTER TABLE sinonimi RENAME TO glossario;
ALTER TABLE glossario RENAME COLUMN parola TO voce;
ALTER TABLE glossario RENAME COLUMN sinonimi TO termini;
