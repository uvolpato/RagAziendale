-- Migrazione 024 — cosa ha CHIAMATO l'agente e cosa ha RISPOSTO. La traccia
-- sapeva la domanda e i pezzi tornati, non il percorso: per capire perche' una
-- risposta e' sbagliata bisognava ricostruirlo (26/09/2026: «perche' il bouquet
-- era in prima posizione?» ha richiesto di rileggere la risposta dal Mongo di
-- LibreChat, rilanciare l'estrazione dei vincoli — non deterministica — e
-- rifare la ricerca con il codice di oggi, non quello del turno: se il
-- retrieval e' stato corretto proprio dopo, la ricostruzione mente).
--
-- `strumenti`: le chiamate agli strumenti del ciclo, ordinate, con argomenti,
-- numero di righe tornate e millisecondi. Include la ricerca forzata dal
-- guardrail, che e' quella che ha deciso la pool.
-- `ricerca`: cosa e' stato cercato davvero (intent, termini multilingue,
-- contesto, regex del vincolo, pezzi tenuti) in una riga leggibile.
-- `risposta`: il testo finale come mandato, fonti comprese. Consente di
-- misurare da solo quante risposte citano pagine che il recupero non aveva
-- portato, senza rileggerle a mano.
--
-- La rotta di riserva ("ragionamento", quando l'agente non risponde) scrive
-- solo `strumenti` e `ricerca`: la risposta li passa a stream e non esiste
-- come variabile. NULL la distingue, e `rotta` la dice comunque.
ALTER TABLE traces ADD COLUMN strumenti jsonb;
ALTER TABLE traces ADD COLUMN ricerca text;
ALTER TABLE traces ADD COLUMN risposta text;
