-- Migrazione 003 — dati dell'amministrazione (SPECIFICA-INTERFACCE-AMMINISTRAZIONE.md,
-- decisioni 56-60 e 65).
--
--   sources            + tipo, provenienza, stato, aziende
--   anomalie           una riga per PROBLEMA, non per evento (decisione 57)
--   anomalie_eventi    cronologia: occorrenze, prese in carico, commenti
--   registro_modifiche chi ha cambiato cosa; NON modificabile (trigger)
--   aspetto            palette e testi configurabili (una riga sola)

-- ============================================================
-- sources: quello che la schermata Fonti mostra e che mancava.
-- ============================================================
ALTER TABLE sources
    ADD COLUMN tipo        text NOT NULL DEFAULT 'documenti'
                           CHECK (tipo IN ('documenti', 'gestionale')),
    -- Da dove arrivano i contenuti (decisioni 61-62): 'cartella', 'sharepoint',
    -- 'caricamento', 'integra', ... Il percorso resta in `percorso`.
    ADD COLUMN provenienza text NOT NULL DEFAULT 'cartella',
    ADD COLUMN stato       text NOT NULL DEFAULT 'attesa'
                           CHECK (stato IN ('attiva', 'attesa', 'sospesa')),
    -- Asse azienda (decisione 53). Vuoto = NESSUNA azienda, non "tutte":
    -- nessun default permissivo. Una fonte di gruppo elenca tutte le aziende.
    -- ⚠ Oggi la usa l'amministrazione; il filtro nel gate (recupero.py) e' il
    -- passo successivo e va fatto PRIMA di indicizzare dati di piu' aziende.
    ADD COLUMN aziende     text[] NOT NULL DEFAULT '{}';

-- Una fonte si attiva solo con un responsabile e una versione di riferimento
-- (spec §7.2): tre versioni dello stesso listino producono citazioni sbagliate.
ALTER TABLE sources ADD CONSTRAINT attiva_richiede_versione CHECK (
    stato <> 'attiva' OR versione_autoritativa IS NOT NULL
);
CREATE INDEX sources_aziende_idx ON sources USING gin (aziende);

-- ============================================================
-- Anomalie
-- ============================================================
CREATE TABLE anomalie (
    id            bigserial PRIMARY KEY,
    -- Impronta del problema: stessa impronta = stessa anomalia, il contatore
    -- sale. E' la differenza fra una casella di problemi e un log.
    impronta      text        NOT NULL UNIQUE,
    gravita       text        NOT NULL CHECK (gravita IN ('critico', 'errore', 'attenzione', 'info')),
    sistema       text        NOT NULL CHECK (sistema IN ('importazioni', 'documenti', 'modelli',
                                                          'accessi', 'sistemi', 'qualita')),
    titolo        text        NOT NULL,
    cosa_fare     text,       -- "cosa significa e cosa fare", in chiaro
    azienda       text,       -- aziende.codice; NULL = non riguarda un'azienda
    oggetto       text,       -- 'fonte:<id>', 'importazione:<azienda>/<entita>', 'servizio:<nome>'
    dettaglio     jsonb       NOT NULL DEFAULT '{}',   -- dati tecnici, richiudibili nella UI
    occorrenze    integer     NOT NULL DEFAULT 1,
    prima         timestamptz NOT NULL DEFAULT now(),
    ultima        timestamptz NOT NULL DEFAULT now(),
    stato         text        NOT NULL DEFAULT 'aperta'
                  CHECK (stato IN ('aperta', 'presa', 'risolta', 'ignorata')),
    assegnata_a   text,       -- username Keycloak
    risolta_auto  boolean     NOT NULL DEFAULT false,
    ignorata_fino timestamptz,  -- NULL con stato 'ignorata' = per sempre
    CONSTRAINT ignorata_ha_senso CHECK (stato = 'ignorata' OR ignorata_fino IS NULL)
);
CREATE INDEX anomalie_aperte_idx ON anomalie (stato, gravita) WHERE stato IN ('aperta', 'presa');
CREATE INDEX anomalie_azienda_idx ON anomalie (azienda);

CREATE TABLE anomalie_eventi (
    id          bigserial PRIMARY KEY,
    anomalia_id bigint      NOT NULL REFERENCES anomalie(id) ON DELETE CASCADE,
    quando      timestamptz NOT NULL DEFAULT now(),
    chi         text        NOT NULL,     -- username, oppure 'sistema'
    tipo        text        NOT NULL CHECK (tipo IN ('occorrenza', 'presa', 'assegnata', 'commento',
                                                     'risolta', 'ignorata', 'riaperta', 'risolta_auto')),
    testo       text
);
CREATE INDEX anomalie_eventi_idx ON anomalie_eventi (anomalia_id, quando);

-- Il punto d'ingresso per TUTTI i componenti (sincronizzazioni, indicizzazione,
-- orchestratore, controlli): una funzione SQL, cosi nessuno deve conoscere le
-- regole di deduplica. Restituisce l'id dell'anomalia.
--   - impronta nuova             -> nuova anomalia aperta
--   - anomalia aperta/presa      -> contatore +1, ultima = adesso
--   - risolta                    -> riaperta (con la cronologia intatta)
--   - ignorata e scaduta         -> riaperta
--   - ignorata e non scaduta     -> contatore +1, resta ignorata
CREATE FUNCTION segnala_anomalia(
    p_impronta text, p_gravita text, p_sistema text, p_titolo text,
    p_cosa_fare text DEFAULT NULL, p_azienda text DEFAULT NULL,
    p_oggetto text DEFAULT NULL, p_dettaglio jsonb DEFAULT '{}'
) RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
    a anomalie%ROWTYPE;
BEGIN
    SELECT * INTO a FROM anomalie WHERE impronta = p_impronta FOR UPDATE;
    IF NOT FOUND THEN
        INSERT INTO anomalie (impronta, gravita, sistema, titolo, cosa_fare, azienda, oggetto, dettaglio)
        VALUES (p_impronta, p_gravita, p_sistema, p_titolo, p_cosa_fare, p_azienda, p_oggetto, p_dettaglio)
        RETURNING * INTO a;
        INSERT INTO anomalie_eventi (anomalia_id, chi, tipo, testo) VALUES (a.id, 'sistema', 'occorrenza', NULL);
        RETURN a.id;
    END IF;

    UPDATE anomalie SET occorrenze = occorrenze + 1, ultima = now(),
                        gravita = p_gravita, titolo = p_titolo, dettaglio = p_dettaglio
     WHERE id = a.id;
    INSERT INTO anomalie_eventi (anomalia_id, chi, tipo) VALUES (a.id, 'sistema', 'occorrenza');

    IF a.stato = 'risolta'
       OR (a.stato = 'ignorata' AND a.ignorata_fino IS NOT NULL AND a.ignorata_fino < now()) THEN
        UPDATE anomalie SET stato = 'aperta', risolta_auto = false, ignorata_fino = NULL, assegnata_a = NULL
         WHERE id = a.id;
        INSERT INTO anomalie_eventi (anomalia_id, chi, tipo) VALUES (a.id, 'sistema', 'riaperta');
    END IF;
    RETURN a.id;
END $$;

-- Chiusura automatica (spec §9.3): il controllo e' tornato a passare.
CREATE FUNCTION chiudi_anomalia(p_impronta text) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    v_id bigint;
BEGIN
    UPDATE anomalie SET stato = 'risolta', risolta_auto = true
     WHERE impronta = p_impronta AND stato IN ('aperta', 'presa')
    RETURNING id INTO v_id;
    IF v_id IS NOT NULL THEN
        INSERT INTO anomalie_eventi (anomalia_id, chi, tipo) VALUES (v_id, 'sistema', 'risolta_auto');
    END IF;
END $$;

-- ============================================================
-- Registro modifiche — in sola lettura per tutti, senza eccezioni (spec §10).
-- Non e' una convenzione dell'applicazione: il database rifiuta UPDATE e
-- DELETE, anche da chi si collega con psql.
-- ============================================================
CREATE TABLE registro_modifiche (
    id        bigserial PRIMARY KEY,
    quando    timestamptz NOT NULL DEFAULT now(),
    chi       text        NOT NULL,       -- username Keycloak
    chi_nome  text,                       -- nome leggibile al momento della modifica
    area      text        NOT NULL CHECK (area IN ('fonti', 'anomalie', 'aspetto', 'vedi-come')),
    azione    text        NOT NULL,       -- frase leggibile
    oggetto   text,
    azienda   text,
    motivo    text,
    prima     jsonb,
    dopo      jsonb
);
CREATE INDEX registro_quando_idx ON registro_modifiche (quando DESC);

CREATE FUNCTION registro_immutabile() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'il registro modifiche non si modifica ne si cancella';
END $$;
CREATE TRIGGER registro_immutabile BEFORE UPDATE OR DELETE ON registro_modifiche
    FOR EACH ROW EXECUTE FUNCTION registro_immutabile();
CREATE TRIGGER registro_immutabile_truncate BEFORE TRUNCATE ON registro_modifiche
    FOR EACH STATEMENT EXECUTE FUNCTION registro_immutabile();

-- ============================================================
-- Aspetto: i 5 token configurabili (spec §2.1.6). Una riga sola.
-- ============================================================
CREATE TABLE aspetto (
    id              int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    palette         jsonb       NOT NULL,     -- {"marchio":"#0d6efd", ...}
    aggiornato_da   text,
    aggiornato_il   timestamptz NOT NULL DEFAULT now()
);
INSERT INTO aspetto (palette) VALUES ('{
    "marchio": "#0d6efd", "marchio-secondario": "#475a6b", "marchio-testo": "#ffffff",
    "sfondo-pagina": "#f4f6f8", "superficie": "#ffffff"}');
