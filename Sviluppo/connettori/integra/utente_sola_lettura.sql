-- Utente di SOLA LETTURA per il connettore Integra (SPECIFICA-CONNETTORI.md §4.3).
-- Da eseguire come superutente sul server del gestionale Integra.
-- Oggi il portale B2B legge con l'amministratore `postgres`: questo utente
-- dedicato e' il prerequisito per importare i dati con l'assistente.

-- Sostituire la password prima di eseguire.
CREATE ROLE assistente_ro LOGIN PASSWORD 'CAMBIAMI';
GRANT CONNECT ON DATABASE integra TO assistente_ro;
GRANT USAGE ON SCHEMA public TO assistente_ro;

-- Solo le viste/tabelle del contratto B2B che servono, MAI la scrittura.
-- Adattare l'elenco alle viste reali del B2B.
GRANT SELECT ON b2b_clienti, b2b_destinazioni_clienti, b2b_prodotti,
  b2b_listini_testata, b2b_listini_righe, b2b_ordini_clienti, b2b_righe_ordini,
  b2b_tabpag, b2b_tabpor, b2b_tabspe, b2b_vettori
  TO assistente_ro;

-- Il connettore rifiuta un utente con permessi di scrittura: qui NON si
-- concedono INSERT/UPDATE/DELETE ne' ruoli superutente.
