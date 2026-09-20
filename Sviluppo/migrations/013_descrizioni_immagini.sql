-- Migrazione 013 — cosa mostra ogni immagine, e il suo vettore.
--
-- Fino a oggi la descrizione prodotta dal modello visivo finiva SOLO dentro il
-- testo dei pezzi: cercabile, ma inutilizzabile per SCEGLIERE quale figura
-- mostrare. Le immagini si sceglievano per pagina, e in un catalogo una pagina
-- contiene dieci prodotti diversi: alla domanda sui profumatori uscivano
-- quattro figure che non c'entravano (20/09/2026).
--
--  - descrizione : la frase del modello visivo (trascrizione + colori e materiali)
--  - embedding   : il suo vettore, per cercare direttamente FRA le immagini.
--                  Stesso spazio dei pezzi (bge-m3, 1024 dimensioni): una
--                  domanda in italiano trova una descrizione in inglese, che e'
--                  il caso normale sui cataloghi.
--
-- Le ACL non si duplicano: restano sulla fonte, e la ricerca fra le immagini
-- usa lo stesso filtro della ricerca sul testo (source_id -> sources).
ALTER TABLE immagini
    ADD COLUMN descrizione text,
    ADD COLUMN embedding   vector(1024);

-- Le immagini descritte sono poche rispetto al totale (solo le figure sopra la
-- soglia d'area): un indice parziale basta e resta piccolo.
CREATE INDEX immagini_embedding_idx ON immagini
    USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL;
