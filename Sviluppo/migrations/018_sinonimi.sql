-- Migrazione 018 — sinonimi multilingue per il ramo di ricerca.
--
-- Il problema: i cataloghi sono trilingue (it/de/en) e il vettore, pur
-- essendo multilingue, NON collega i nomi di dominio fra lingue: misurato il
-- 23/09/2026, coseno(«sassi rossi», «palline natalizie rosse») = 0.5065 mentre
-- coseno(«sassi rossi», «river pebbles dunkelrot») = 0.4415. I colori il
-- vettore li collega da solo; i NOMI delle cose no («sassi» non e' vicino a
-- «pebbles» o «kiesel»).
--
-- Qui sta il dizionario: parola -> sinonimi nelle lingue dei cataloghi. Lo
-- consulta il ramo di ricerca per estendere la query («sassi» aggiunge
-- «pietre, ciottoli, pebbles, kiesel»). Lo POPOLA il modello (8B) al primo
-- incontro di una parola che non c'e', e la cache in RAM lo rende istantaneo:
-- il modello non si interroga a ogni domanda, si interroga UNA volta per
-- parola e il risultato resta. E' lo stesso meccanismo di RAGFlow
-- (rag/nlp/synonym.py), ma senza Redis e senza WordNet: solo il dizionario di
-- dominio, che e' cio' che un catalogo di decorazioni richiede.
--
-- Una parola con array VUOTO significa «gia' guardata, nessun sinonimo»: la
-- cache non la richiede piu' al modello (evita di ri-chiamare l'8B per
-- «bisogno», «quanto», «costa» a ogni turno).

CREATE TABLE IF NOT EXISTS sinonimi (
    parola        text        PRIMARY KEY,
    sinonimi      text[]      NOT NULL DEFAULT '{}',
    aggiornato_il timestamptz NOT NULL DEFAULT now()
);
