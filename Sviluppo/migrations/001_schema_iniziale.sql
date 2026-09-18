-- Migrazione 001 — schema iniziale della fase 1.
-- Applicata da migrate.py. Non modificare: per cambiare lo schema si
-- aggiunge una migrazione nuova, cosi i DB con dati dentro si evolvono.

-- ============================================================
-- sources — il registro delle sorgenti.
-- E' la matrice ACL e la matrice di residenza come DATO, non come foglio
-- Excel: auditabile, versionata, con il nome di chi ha firmato.
-- Venti-quaranta righe in tutto.
-- ============================================================
CREATE TABLE sources (
    id            text PRIMARY KEY,        -- slug stabile, es. 'manuali-tecnici'
    descrizione   text        NOT NULL,
    percorso      text        NOT NULL,    -- share, libreria SharePoint, tabella ERP

    -- Asse 1: chi puo vedere. Intersezione di insiemi con i gruppi del token,
    -- non gerarchia numerica: un livello numerico si rompe alla prima
    -- eccezione organizzativa.
    acl_groups    text[]      NOT NULL CHECK (cardinality(acl_groups) > 0),

    -- Asse 2: se puo uscire dal perimetro. ORTOGONALE all'asse 1: il catalogo
    -- con i listini lo leggono tutti i commerciali ma non deve uscire.
    -- Default 'interno': si approva l'uscita, non la si presume.
    residency     text        NOT NULL DEFAULT 'interno'
                  CHECK (residency IN ('interno', 'cloud_ok')),

    -- Chi risponde del contenuto, e quale versione vale. Se nessuno sa
    -- rispondere, la sorgente non entra: tre versioni dello stesso listino
    -- indicizzate producono una citazione sicura e sbagliata.
    owner                  text NOT NULL,
    versione_autoritativa  text,

    approvato_da  text,
    approvato_il  timestamptz,
    creato_il     timestamptz NOT NULL DEFAULT now(),

    -- Il vincolo che rende la firma non aggirabile: nessuno puo marcare una
    -- sorgente 'cloud_ok' senza registrare chi l'ha approvata e quando.
    -- La policy diventa un vincolo del database, non una convenzione.
    CONSTRAINT cloud_ok_richiede_firma CHECK (
        residency <> 'cloud_ok'
        OR (approvato_da IS NOT NULL AND approvato_il IS NOT NULL)
    )
);

-- ============================================================
-- chunks — l'archivio ricercabile.
-- Le ACL NON sono duplicate qui: stanno solo in sources, e il filtro passa
-- da una sottoquery su una tabella da 40 righe. Un'unica casa per il dato di
-- sicurezza, nessun rischio di disallineamento.
-- ============================================================
CREATE TABLE chunks (
    id           bigserial PRIMARY KEY,
    source_id    text        NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    documento    text        NOT NULL,   -- file specifico dentro la sorgente
    page         int,
    content      text        NOT NULL,
    content_hash text        NOT NULL,   -- sha256: rende l'ingestion idempotente
    embedding    vector(1024),
    updated_at   timestamptz NOT NULL DEFAULT now(),

    -- Rilanciare l'ingestion non duplica. Serve davvero: la macchina di
    -- sviluppo va in sospensione e l'indicizzazione riprende a meta.
    UNIQUE (source_id, documento, page, content_hash)
);

CREATE INDEX chunks_source_idx ON chunks (source_id);
CREATE INDEX chunks_vec_idx    ON chunks USING hnsw (embedding vector_cosine_ops);
-- Ricerca testuale per codici articolo e part number, che il vettoriale
-- sbaglia. Meta della fusione RRF, e il fallback quando l'host di inferenza
-- non risponde.
CREATE INDEX chunks_fts_idx    ON chunks USING gin (to_tsvector('italian', content));

CREATE INDEX sources_acl_idx   ON sources USING gin (acl_groups);

-- ============================================================
-- conversation_taint — high-water-mark della conversazione.
-- La presenza di una riga significa contaminata. Una volta che un dato
-- 'interno' entra, la conversazione resta interna fino alla fine: lo storico
-- torna al modello a ogni turno, quindi ripulirla sarebbe illusorio.
-- ============================================================
CREATE TABLE conversation_taint (
    conversation_id text PRIMARY KEY,
    source_id       text        REFERENCES sources(id),  -- chi l'ha contaminata
    da_quando       timestamptz NOT NULL DEFAULT now()
);

-- ============================================================
-- index_meta — coerenza fra indice e modello di embedding.
-- Qualcuno cambia modello o quantizzazione, gli embedding si spostano e il
-- recall cala SENZA alcun errore. Il canary lo intercetta: l'embedding di una
-- frase fissa, ricalcolato a ogni avvio e confrontato. Se differisce,
-- l'orchestratore RIFIUTA di partire.
-- ============================================================
CREATE TABLE index_meta (
    id              int  PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    embedding_model text        NOT NULL,   -- come lo riporta GET /v1/models
    dimensioni      int         NOT NULL,
    canary_frase    text        NOT NULL DEFAULT 'Il canarino nella miniera di carbone.',
    canary_hash     text        NOT NULL,
    costruito_il    timestamptz NOT NULL DEFAULT now()
);

-- ============================================================
-- traces — tracciare non e' Langfuse. Il cruscotto arriva in fase 2, questi
-- campi servono dalla fase 1: senza di essi un pilota fallito da' un fatto
-- binario e nessuna diagnosi.
-- ============================================================
CREATE TABLE traces (
    id               bigserial PRIMARY KEY,
    conversation_id  text,
    utente           text,
    domanda          text,
    chunk_ids        bigint[],
    -- I tre campi della diagnosi: niente recuperato = buco nel corpus;
    -- recuperato ma riformulato = chunking o ranking; feedback negativo con
    -- retrieval giusto = prompt o modello.
    retrieval_vuoto  boolean   NOT NULL,
    riformulazione   boolean   NOT NULL DEFAULT false,
    feedback         smallint  CHECK (feedback IN (-1, 0, 1)),
    taint            text,
    modello          text,                  -- quale ha risposto: il modello e' una variabile
    token_in         int,
    token_out        int,
    latenza_ms       int,
    creato_il        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX traces_conv_idx ON traces (conversation_id);
CREATE INDEX traces_data_idx ON traces (creato_il DESC);

-- ============================================================
-- pending_actions — azioni proposte in attesa di conferma umana.
-- Usata dalla fase 3; in fase 1 resta vuota. Dalla fase 3 la sostituisce
-- interrupt() di LangGraph con il checkpointer.
-- ponytail: scadenza 10 minuti, una sola azione pendente per conversazione.
-- ============================================================
CREATE TABLE pending_actions (
    conversation_id text PRIMARY KEY,
    tool            text        NOT NULL,
    args_json       jsonb       NOT NULL,
    expires_at      timestamptz NOT NULL
);
