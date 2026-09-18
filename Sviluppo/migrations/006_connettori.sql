-- Migrazione 006 — collegamenti ai gestionali (SPECIFICA-CONNETTORI.md §4.2,
-- decisione 70). Un "collegamento" e' un'istanza configurata di un connettore
-- del catalogo: questo server, questo database, queste credenziali.
--
-- Il Superutente li crea dal pannello. Le credenziali sono CIFRATE qui
-- (Fernet): il segreto non compare mai in chiaro nel database. La chiave sta
-- in .env (CHIAVE_CREDENZIALI) e la possiede solo il servizio `connettori`,
-- che cifra e decifra in memoria; il pannello di amministrazione non la vede.
-- Chi ruba solo il database (backup, dump) non ha i segreti.

CREATE TABLE collegamenti (
    id            text PRIMARY KEY,             -- slug: 'integra-sede'
    nome          text NOT NULL,
    tipo          text NOT NULL,                -- dal catalogo (connettori/<tipo>/)
    parametri     jsonb NOT NULL DEFAULT '{}',  -- SOLO i campi NON segreti
    segreti       bytea,                        -- JSON dei segreti, cifrato (Fernet)
    versione_chiave smallint NOT NULL DEFAULT 1,-- con quale chiave sono cifrati (rotazione)
    stato         text NOT NULL DEFAULT 'da_provare'
                  CHECK (stato IN ('da_provare', 'funzionante', 'non_raggiungibile',
                                   'credenziali_non_valide', 'troppi_permessi', 'disattivato')),
    ultima_prova  jsonb,                        -- esito, versione, aziende trovate
    segreti_cambiati_da text,                   -- chi ha cambiato la password, mai il valore
    segreti_cambiati_il  timestamptz,
    attivo        boolean NOT NULL DEFAULT true,
    creato_il     timestamptz NOT NULL DEFAULT now()
);

-- Ogni azienda legge da un collegamento (e, se il gestionale e' multi-azienda,
-- dal suo codice_origine DENTRO quel gestionale). NULL = non ancora collegata.
ALTER TABLE aziende ADD COLUMN collegamento text REFERENCES collegamenti(id);

-- La stessa azienda del gestionale (coppia collegamento + codice_origine)
-- abbinata a UNA sola nostra azienda: due aziende non leggono due volte lo
-- stesso spazio di chiavi. NULL (aziende non ancora collegate) restano libere.
ALTER TABLE aziende ADD CONSTRAINT un_abbinamento_per_codice
    UNIQUE (collegamento, codice_origine);

COMMENT ON COLUMN collegamenti.parametri IS 'Campi NON segreti (host, porta, database, utente, ssl): il resto nel segreto cifrato';
COMMENT ON COLUMN collegamenti.segreti IS 'JSON dei segreti cifrato con Fernet; mai riletto in chiaro dal pannello';
COMMENT ON COLUMN aziende.collegamento IS 'Collegamento configurato dal Superutente; NULL = azienda non collegata';

-- Le modifiche a collegamenti e abbinamenti finiscono nel registro modifiche
-- (SPECIFICA-CONNETTORI.md §3.3): una nuova area, mai i valori dei segreti.
ALTER TABLE registro_modifiche DROP CONSTRAINT registro_modifiche_area_check;
ALTER TABLE registro_modifiche ADD CONSTRAINT registro_modifiche_area_check
    CHECK (area IN ('fonti', 'anomalie', 'aspetto', 'vedi-come', 'accessi', 'gestionali'));
