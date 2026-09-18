-- Utente di SOLA LETTURA per il connettore Integra (SPECIFICA-CONNETTORI.md §4.3).
-- Da eseguire come superutente sul DB parallelo "rag", quello che espone le
-- viste rag_* e legge il gestionale con postgres_fdw. Il connettore punta a
-- QUESTO database, non al gestionale: qui basta la SELECT sulle viste.

-- Sostituire la password prima di eseguire.
CREATE ROLE assistente_ro LOGIN PASSWORD 'CAMBIAMI';
GRANT CONNECT ON DATABASE rag TO assistente_ro;
GRANT USAGE ON SCHEMA public TO assistente_ro;

-- Solo le viste del portale B2B che servono, MAI la scrittura. Se le viste
-- rag_* leggono tabelle FDW (schema integra), serve anche USAGE su quello
-- schema; la scrittura non si concede mai in nessun caso.
-- rag_pagamenti_clienti e' esclusa: espone IBAN/ABI/CAB/mandato (minimizzazione §7).
GRANT SELECT ON rag_prodotti, rag_clienti, rag_indirizzi_clienti,
  rag_ordini_clienti, rag_righe_ordini,
  rag_listini_testata, rag_listini_righe, rag_tabpag, rag_tabpor, rag_tabspe
  TO assistente_ro;

-- Il connettore rifiuta un utente con permessi di scrittura: qui NON si
-- concedono INSERT/UPDATE/DELETE ne' ruoli superutente.
