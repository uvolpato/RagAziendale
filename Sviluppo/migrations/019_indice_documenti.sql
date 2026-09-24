-- Migrazione 019 — indice dei documenti per la ricerca a due stadi.
--
-- Il problema: su una domanda APERTA («regalo per una ragazza di 30 anni»,
-- «idee per un matrimonio») l'agente riprova con parole che non esistono nel
-- catalogo («zaino», «cappello»), perche' non sa COSA contiene l'archivio.
-- Misurato il 23/09/2026: su 30 domande, le astratte rispondono con rumore
-- mentre l'agente cerca-guarda-riprova resta dentro un dominio che non conosce.
--
-- Qui si salva, per ogni documento, una DESCRIZIONE del suo contenuto («catalogo
-- di sassi decorativi e vasi...») e il suo VETTORE. La ricerca a due stadi
-- funziona cosi':
--   1. primo passaggio: si cerca fra le DESCRIZIONI dei documenti (34 righe,
--      non migliaia di chunk) e si scopre QUALI cataloghi c'entrano;
--   2. secondo passaggio: la ricerca dettagliata resta DENTRO quei documenti.
--
-- La descrizione la scrive il modello una volta (come i sinonimi, migrazione
-- 018), leggendo le intestazioni dei pezzi gia' indicizzati: non si rilegge
-- nessun PDF. Il vettore usa lo stesso spazio (bge-m3, 1024 dimensioni) dei
-- chunk, quindi si confronta con lo stesso operatore <=> senza toccare
-- l'embedding esistente.
--
-- Le ACL restano dove sono sempre state: la descrizione NON si duplica sui
-- pezzi, e la ricerca a due stadi filtra sulle stesse `sources` di sempre.
-- La colonna descrizione e' SOLO un aiuto a scegliere i documenti, non una
-- via per leggere un contenuto che le ACL non consentono.

ALTER TABLE documenti
    ADD COLUMN descrizione     text,
    ADD COLUMN descrizione_vec vector(1024);
