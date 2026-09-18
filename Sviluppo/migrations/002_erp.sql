-- Migrazione 002 — modello canonico dei dati gestionali.
-- Descrizione tabella per tabella: MODELLO-DATI-GESTIONALE.md (radice del
-- progetto). Decisioni 46-54, §15 e §15.9 dell'analisi.
--
-- PRINCIPI
-- 1. Indipendente dal gestionale. Integra e UN connettore: ogni gestionale
--    ha le sue viste di mappatura (connettori/<erp>/) che producono queste
--    colonne. Il modello non cambia quando arriva un gestionale nuovo.
-- 2. Accoglie il massimo, pretende il minimo. Quasi tutto e facoltativo:
--    non tutte le aziende tengono costi, lotti, scadenze. Quello che il
--    modello non prevede finisce in `extra` (jsonb): all'import non si perde
--    niente, e se un campo in extra diventa importante si promuove a colonna.
-- 3. Piu aziende. Ogni riga appartiene a UN'azienda (tabella aziende).
--    L'azienda e un asse di permesso come i gruppi: un utente vede solo le
--    aziende a cui e abilitato (§15.9, decisione 53).
-- 4. Storico (SCD tipo 2). Le tabelle vere stanno in erp_storico: ogni
--    modifica chiude la versione corrente (registrato_al) e ne apre una
--    nuova. Una versione nasce solo se l'impronta (hash_riga) cambia: la
--    sincronizzazione ripetuta non produce copie identiche.
-- 5. L'assistente legge le viste erp.* = solo la versione corrente. Lo
--    storico lo interrogano Cube e gli strumenti di analisi, che sanno
--    gestire il "com'era al ...". Un LLM che dimentica il filtro sulla
--    versione conta tutto due volte: per questo non glielo diamo.
--
-- DUE ASSI DI TEMPO, da non confondere:
--   registrato_dal/_al  quando QUESTA versione era quella vera nel gestionale
--                       (tempo di sistema, lo gestisce la sincronizzazione)
--   in_vigore_dal/_al   quando il dato vale per il business (es. il listino
--                       vale dal 1 gennaio) — viene dal gestionale
--
-- ECCEZIONI allo storico:
--   erp.contatti            dati personali: si sovrascrivono (GDPR,
--                           minimizzazione e diritto alla cancellazione)
--   erp.giacenze_istantanee fotografia periodica, gia storica per natura
--
-- LETTURA IN DIRETTA (decisione 47, invariata): giacenza attuale, fido
-- residuo e prezzo netto per il cliente si chiedono al gestionale nel
-- momento della domanda. Qui ne teniamo lo storico per l'ANALISI, non per
-- rispondere "posso vendere?".
--
-- CHIAVI: id = '<fonte>:<codice azienda nel gestionale>:<id nel gestionale>'
-- es. 'integra:001:20657'. Testo leggibile, unico anche con piu gestionali,
-- nessuna tabella di decodifica da mantenere. I riferimenti fra tabelle
-- usano lo stesso formato; niente FK (si ricostruiscono in qualsiasi ordine).
-- ponytail: chiave testuale; chiave surrogata intera se i join diventano
-- il collo di bottiglia (non prima di decine di milioni di righe).

CREATE EXTENSION IF NOT EXISTS btree_gist;   -- per il vincolo sui periodi
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- ricerca per nome approssimato

CREATE SCHEMA erp_storico;
CREATE SCHEMA erp;
CREATE SCHEMA arricchimenti;

-- ============================================================
-- Aziende — non storicizzata: sono poche righe amministrate a mano.
-- ============================================================
CREATE TABLE aziende (
    codice          text PRIMARY KEY,      -- slug stabile: 'luis', 'decobrands'
    ragione_sociale text NOT NULL,
    partita_iva     text,
    fonte           text NOT NULL,         -- 'integra', 'altro-erp', ...
    codice_origine  text NOT NULL,         -- codice azienda NEL gestionale ('001')
    attiva          boolean NOT NULL DEFAULT true,
    UNIQUE (fonte, codice_origine)
);

-- ============================================================
-- Colonne comuni a tutte le tabelle storicizzate. LIKE ... INCLUDING ALL
-- le copia in ogni tabella: una sola definizione, nessuna colonna dimenticata.
-- ============================================================
CREATE TABLE erp_storico._versione (
    versione        bigint GENERATED ALWAYS AS IDENTITY,
    id              text        NOT NULL,   -- vedi CHIAVI sopra
    azienda         text        NOT NULL,   -- aziende.codice
    extra           jsonb       NOT NULL DEFAULT '{}',
    hash_riga       text        NOT NULL,   -- impronta dei campi: cambia -> nuova versione
    data_modifica_origine timestamptz,      -- come la dichiara il gestionale, se la dichiara
    registrato_dal  timestamptz NOT NULL DEFAULT now(),
    registrato_al   timestamptz,            -- NULL = versione corrente
    cancellato      boolean     NOT NULL DEFAULT false,  -- sparito dal gestionale
    CHECK (registrato_al IS NULL OR registrato_al > registrato_dal)
);

-- ============================================================
-- Soggetti: clienti, fornitori, agenti, vettori, prospect. Molti gestionali
-- (Integra compreso) hanno un'anagrafica unica: un fornitore che e anche
-- cliente e UN soggetto con due ruoli.
-- ============================================================
CREATE TABLE erp_storico.soggetti (
    LIKE erp_storico._versione INCLUDING ALL,
    codice          text,
    tipo_soggetto   text,        -- 'societa' | 'persona_fisica' | 'ente'
    ragione_sociale text,
    ragione_sociale_2 text,
    cognome         text,        -- solo ditte individuali/persone fisiche
    nome            text,
    forma_giuridica text,
    partita_iva     text,
    codice_fiscale  text,
    codice_sdi      text,
    pec             text,
    email           text,        -- email AZIENDALE generica; i referenti stanno in contatti
    telefono        text,
    web             text,
    indirizzo       text,
    cap             text,
    citta           text,
    provincia       text,
    regione         text,
    nazione         text,
    settore         text,        -- ATECO o classificazione interna
    classificazioni jsonb NOT NULL DEFAULT '{}',   -- {"zona":"NE","categoria":"GDO"}: codici in erp.codici
    note            text
);

-- Ruolo del soggetto e le sue condizioni commerciali. Storicizzate: se il
-- cliente cambia agente, il fatturato passato resta al vecchio agente.
CREATE TABLE erp_storico.soggetti_ruoli (
    LIKE erp_storico._versione INCLUDING ALL,
    soggetto_id       text NOT NULL,
    ruolo             text NOT NULL,     -- 'cliente' | 'fornitore' | 'agente' | 'vettore' | 'prospect'
    codice_ruolo      text,              -- il codice cliente/fornitore nel gestionale
    agente_id         text,
    listino           text,
    pagamento         text,
    porto             text,
    spedizione        text,
    vettore_id        text,
    valuta            text,
    codice_iva        text,
    zona              text,
    categoria         text,
    sconti            numeric[],         -- sconti di testata in cascata, quanti che siano
    fido              numeric,           -- per ANALISI; la risposta "posso vendere" legge in diretta
    fido_scadenze     numeric,
    bloccato          boolean,
    motivo_blocco     text,
    provvigione_pct   numeric,           -- per il ruolo agente
    attivo_dal        date,
    attivo_al         date
);

CREATE TABLE erp_storico.indirizzi (
    LIKE erp_storico._versione INCLUDING ALL,
    soggetto_id     text NOT NULL,
    tipo            text,        -- 'sede' | 'consegna' | 'fatturazione' | 'magazzino'
    ragione_sociale text,
    indirizzo       text,
    indirizzo_2     text,
    cap             text,
    citta           text,
    provincia       text,
    regione         text,
    nazione         text,
    latitudine      numeric,
    longitudine     numeric,
    abituale        boolean,
    zona            text,
    agente_id       text,
    vettore_id      text,        -- se valorizzati sovrascrivono quelli del soggetto
    porto           text,
    giorni_preparazione integer,
    orari_consegna  text
);

-- ============================================================
-- Articoli
-- ============================================================
CREATE TABLE erp_storico.articoli (
    LIKE erp_storico._versione INCLUDING ALL,
    codice          text NOT NULL,
    descrizione     text,
    descrizione_estesa text,
    tipo_articolo   text,        -- 'merce' | 'servizio' | 'semilavorato' | 'materia_prima' | 'kit'
    unita_misura    text,
    unita_misura_acquisto text,
    fattore_conversione numeric,
    classificazioni jsonb NOT NULL DEFAULT '{}',   -- {"famiglia":"VAS","linea":"L1","gruppo_merc":"04"}
    attributi       jsonb NOT NULL DEFAULT '{}',   -- {"diametro_cm":30,"materiale":"ceramica"}
    marca           text,
    codice_iva      text,
    peso_netto      numeric,
    peso_lordo      numeric,
    volume          numeric,
    lunghezza       numeric,
    larghezza       numeric,
    altezza         numeric,
    pezzi_per_confezione numeric,
    pezzi_per_pallet numeric,
    gestione_lotti  boolean,
    gestione_matricole boolean,
    gestito_a_magazzino boolean,
    scorta_minima   numeric,
    punto_riordino  numeric,
    fornitore_preferenziale_id text,
    -- Costi: storicizzati con l'articolo. Sono loro a dare il margine nel
    -- tempo quando le righe documento non portano il costo.
    costo_standard  numeric,
    costo_ultimo    numeric,
    costo_medio     numeric,
    ubicazione      text,
    pubblicato_web  boolean,
    obsoleto        boolean
);

-- Codici alternativi: EAN, codice del fornitore, codice del cliente.
-- Servono alla domanda "cos'e l'articolo XYZ-123?" quando il codice e quello
-- del fornitore.
CREATE TABLE erp_storico.articoli_codici (
    LIKE erp_storico._versione INCLUDING ALL,
    articolo_id     text NOT NULL,
    tipo            text NOT NULL,   -- 'ean' | 'fornitore' | 'cliente' | 'alternativo' | 'produttore'
    codice          text NOT NULL,
    soggetto_id     text             -- il fornitore/cliente a cui appartiene il codice
);

-- Condizioni di acquisto per fornitore. Integra le tiene in `prosoggetti`.
CREATE TABLE erp_storico.articoli_fornitori (
    LIKE erp_storico._versione INCLUDING ALL,
    articolo_id     text NOT NULL,
    fornitore_id    text NOT NULL,
    codice_fornitore text,
    descrizione_fornitore text,
    prezzo_acquisto numeric,
    valuta          text,
    sconti          numeric[],
    quantita_minima numeric,
    multiplo        numeric,
    lotto_riordino  numeric,
    giorni_consegna integer,
    preferenziale   boolean
);

-- Distinta base: di cosa e fatto un articolo.
CREATE TABLE erp_storico.articoli_componenti (
    LIKE erp_storico._versione INCLUDING ALL,
    articolo_id     text NOT NULL,
    componente_id   text NOT NULL,
    quantita        numeric,
    unita_misura    text,
    scarto_pct      numeric,
    ordinamento     integer
);

-- ============================================================
-- Listini (vendita e acquisto). Sono i listini BASE: il prezzo netto per il
-- cliente lo calcola il gestionale (decisione 48).
-- ============================================================
CREATE TABLE erp_storico.listini (
    LIKE erp_storico._versione INCLUDING ALL,
    codice          text NOT NULL,
    descrizione     text,
    ciclo           text,        -- 'vendita' | 'acquisto'
    con_iva         boolean,
    valuta          text,
    in_vigore_dal   date,
    in_vigore_al    date,
    obsoleto        boolean
);

CREATE TABLE erp_storico.listini_righe (
    LIKE erp_storico._versione INCLUDING ALL,
    listino_id      text NOT NULL,
    articolo_id     text NOT NULL,
    soggetto_id     text,        -- valorizzato = prezzo riservato a quel cliente/fornitore
    prezzo          numeric,
    sconti          numeric[],
    quantita_da     numeric,     -- scaglioni
    quantita_a      numeric,
    in_vigore_dal   date,
    in_vigore_al    date
);

-- ============================================================
-- Documenti: preventivi, ordini, DDT, fatture, note di credito, resi — di
-- vendita e di acquisto. I documenti di ACQUISTO danno subito lo storico dei
-- prezzi fornitore, anche degli anni passati.
-- ============================================================
CREATE TABLE erp_storico.documenti (
    LIKE erp_storico._versione INCLUDING ALL,
    ciclo           text NOT NULL,   -- 'vendita' | 'acquisto'
    tipo            text NOT NULL,   -- 'preventivo' | 'ordine' | 'ddt' | 'fattura' | 'nota_credito' | 'reso' | 'altro'
    tipo_origine    text,            -- codice del gestionale ('ORD', 'FAT'...): non si perde la sfumatura
    numero          text,
    serie           text,
    anno            smallint,
    data_documento  date,
    data_registrazione date,
    data_consegna_prevista date,
    data_consegna   date,
    soggetto_id     text,
    indirizzo_consegna_id text,
    indirizzo_fatturazione_id text,
    agente_id       text,
    listino         text,
    pagamento       text,
    porto           text,
    spedizione      text,
    vettore_id      text,
    valuta          text,
    cambio          numeric,
    sconti          numeric[],
    imponibile      numeric,
    iva             numeric,
    totale          numeric,
    spese_trasporto numeric,
    spese_incasso   numeric,
    peso_totale     numeric,
    colli           integer,
    riferimento_soggetto text,       -- il "vs. ordine n." del cliente
    data_riferimento_soggetto date,
    stato           text,            -- 'aperto' | 'parziale' | 'evaso' | 'annullato' ...
    stato_origine   text,
    canale          text,            -- 'b2b' | 'agente' | 'telefono' | 'ecommerce' ...
    utente_origine  text,            -- chi l'ha inserito nel gestionale
    note            text
);

CREATE TABLE erp_storico.documenti_righe (
    LIKE erp_storico._versione INCLUDING ALL,
    documento_id    text NOT NULL,
    numero_riga     integer,
    tipo_riga       text,            -- 'articolo' | 'descrittiva' | 'spesa' | 'omaggio' | 'sconto'
    articolo_id     text,
    codice_articolo text,            -- come scritto sul documento, anche se l'articolo sparisce
    descrizione     text,
    quantita        numeric,
    unita_misura    text,
    prezzo_listino  numeric,
    sconti          numeric[],
    prezzo_netto    numeric,         -- quello APPLICATO allora
    importo         numeric,
    codice_iva      text,
    costo_unitario  numeric,         -- se il gestionale lo registra: margine esatto e retroattivo
    provvigione_pct numeric,
    magazzino       text,
    lotto           text,
    data_consegna_prevista date,
    quantita_evasa  numeric,
    quantita_fatturata numeric,
    riga_origine_id text,            -- la riga d'ordine da cui nasce questa riga di DDT/fattura
    note            text
);

-- Legami fra documenti: ordine -> DDT -> fattura.
CREATE TABLE erp_storico.documenti_collegamenti (
    LIKE erp_storico._versione INCLUDING ALL,
    documento_id        text NOT NULL,
    documento_origine_id text NOT NULL
);

-- Scadenze e incassi: "chi e in ritardo coi pagamenti".
CREATE TABLE erp_storico.scadenze (
    LIKE erp_storico._versione INCLUDING ALL,
    soggetto_id     text NOT NULL,
    documento_id    text,
    ciclo           text,            -- 'attiva' (da incassare) | 'passiva' (da pagare)
    data_scadenza   date,
    importo         numeric,
    importo_pagato  numeric,
    data_pagamento  date,
    pagamento       text,
    insoluto        boolean
);

CREATE TABLE erp_storico.movimenti_magazzino (
    LIKE erp_storico._versione INCLUDING ALL,
    data_movimento  date,
    articolo_id     text NOT NULL,
    magazzino       text,
    causale         text,
    segno           smallint,        -- +1 carico, -1 scarico
    quantita        numeric,
    valore          numeric,
    lotto           text,
    documento_id    text,
    soggetto_id     text
);

-- Tabelle codici del gestionale: pagamenti, porti, vettori, zone, famiglie,
-- causali, magazzini... tutte codice -> descrizione.
CREATE TABLE erp_storico.codici (
    LIKE erp_storico._versione INCLUDING ALL,
    tipo            text NOT NULL,
    codice          text NOT NULL,
    descrizione     text,
    padre           text,            -- gerarchie: famiglia -> linea
    dettagli        jsonb NOT NULL DEFAULT '{}'
);

-- ============================================================
-- Per ogni tabella storicizzata, in un colpo solo:
--   PK sulla versione
--   vincolo: per lo stesso id i periodi NON si sovrappongono (il database
--     rifiuta due versioni "correnti" insieme: niente doppi conteggi)
--   indice sulla versione corrente
--   vista erp.<tabella> = solo versione corrente, non cancellata
-- ============================================================
DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables
           WHERE schemaname = 'erp_storico' AND tablename <> '_versione'
  LOOP
    EXECUTE format('ALTER TABLE erp_storico.%I ADD PRIMARY KEY (versione)', t);
    EXECUTE format(
      'ALTER TABLE erp_storico.%I ADD CONSTRAINT %I EXCLUDE USING gist '
      '(id WITH =, tstzrange(registrato_dal, registrato_al) WITH &&)',
      t, t || '_periodi_disgiunti');
    EXECUTE format(
      'CREATE UNIQUE INDEX %I ON erp_storico.%I (id) WHERE registrato_al IS NULL',
      t || '_corrente', t);
    EXECUTE format('CREATE INDEX %I ON erp_storico.%I (azienda)', t || '_azienda', t);
    EXECUTE format(
      'CREATE VIEW erp.%I AS SELECT * FROM erp_storico.%I '
      'WHERE registrato_al IS NULL AND NOT cancellato', t, t);
  END LOOP;
END $$;
DROP TABLE erp_storico._versione;

-- Indici di ricerca che l'assistente usa di continuo.
CREATE INDEX soggetti_piva_idx      ON erp_storico.soggetti (partita_iva) WHERE registrato_al IS NULL;
CREATE INDEX soggetti_rgsoc_trgm    ON erp_storico.soggetti USING gin (ragione_sociale gin_trgm_ops) WHERE registrato_al IS NULL;
CREATE INDEX articoli_codice_idx    ON erp_storico.articoli (codice) WHERE registrato_al IS NULL;
CREATE INDEX articoli_descr_trgm    ON erp_storico.articoli USING gin (descrizione gin_trgm_ops) WHERE registrato_al IS NULL;
CREATE INDEX articoli_codici_idx    ON erp_storico.articoli_codici (codice) WHERE registrato_al IS NULL;
CREATE INDEX documenti_sogg_idx     ON erp_storico.documenti (soggetto_id, data_documento);
CREATE INDEX documenti_tipo_idx     ON erp_storico.documenti (azienda, ciclo, tipo, data_documento);
CREATE INDEX righe_doc_idx          ON erp_storico.documenti_righe (documento_id);
CREATE INDEX righe_articolo_idx     ON erp_storico.documenti_righe (articolo_id);
CREATE INDEX listini_righe_art_idx  ON erp_storico.listini_righe (listino_id, articolo_id);

-- ============================================================
-- Eccezioni allo storico
-- ============================================================

-- Referenti: dati PERSONALI. Si sovrascrivono, non si storicizzano.
CREATE TABLE erp.contatti (
    id              text PRIMARY KEY,
    azienda         text NOT NULL,
    soggetto_id     text NOT NULL,
    nome            text,
    cognome         text,
    ruolo           text,        -- 'acquisti', 'amministrazione', 'titolare'
    email           text,
    telefono        text,
    cellulare       text,
    extra           jsonb NOT NULL DEFAULT '{}',
    aggiornato_il   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX contatti_soggetto_idx ON erp.contatti (soggetto_id);

-- Fotografia periodica delle giacenze (tipicamente giornaliera): da qui
-- rotazione e andamento delle scorte. La giacenza DI ADESSO si legge in
-- diretta dal gestionale.
CREATE TABLE erp.giacenze_istantanee (
    data            date NOT NULL,
    azienda         text NOT NULL,
    articolo_id     text NOT NULL,
    magazzino       text NOT NULL DEFAULT '',
    esistenza       numeric,
    impegnato       numeric,     -- in ordini clienti
    ordinato        numeric,     -- in ordini fornitori
    disponibile     numeric,
    valore          numeric,
    PRIMARY KEY (data, azienda, articolo_id, magazzino)
);

-- ============================================================
-- Stato della sincronizzazione: una riga per (azienda, entita).
-- L'assistente la cita: "dati aggiornati al ...".
-- ============================================================
CREATE TABLE erp.sincronizzazioni (
    azienda         text NOT NULL,
    entita          text NOT NULL,
    cursore         timestamptz,     -- ultima data_modifica_origine letta
    iniziata_il     timestamptz,
    completata_il   timestamptz,
    esito           text CHECK (esito IN ('in_corso', 'ok', 'errore')),
    righe_lette     integer,
    versioni_nuove  integer,
    errore          text,
    PRIMARY KEY (azienda, entita)
);

-- ============================================================
-- Arricchimenti AI — non sovrascrivono mai il gestionale.
-- ============================================================
CREATE TABLE arricchimenti.fatti (
    id            bigserial PRIMARY KEY,
    azienda       text NOT NULL,
    entita        text NOT NULL,     -- 'soggetto' | 'articolo' | ...
    chiave        text NOT NULL,     -- id dell'entita (formato CHIAVI)
    attributo     text NOT NULL,
    valore        jsonb NOT NULL,
    fonte         text NOT NULL,     -- 'chunk:123' | url | 'regola:<nome>'
    metodo        text NOT NULL,     -- modello o regola che l'ha prodotto
    fiducia       numeric CHECK (fiducia BETWEEN 0 AND 1),
    stato         text NOT NULL DEFAULT 'proposto'
                  CHECK (stato IN ('proposto', 'confermato', 'scartato')),
    rivisto_da    text,
    rivisto_il    timestamptz,
    creato_il     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT confermato_richiede_revisore CHECK (
        stato <> 'confermato' OR (rivisto_da IS NOT NULL AND rivisto_il IS NOT NULL)
    )
);
CREATE INDEX fatti_entita_idx ON arricchimenti.fatti (entita, chiave);
CREATE UNIQUE INDEX fatti_unico_idx ON arricchimenti.fatti (entita, chiave, attributo, fonte)
    WHERE stato <> 'scartato';

CREATE TABLE arricchimenti.collegamenti (
    chunk_id      bigint NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    entita        text   NOT NULL,
    chiave        text   NOT NULL,
    metodo        text   NOT NULL,
    fiducia       numeric CHECK (fiducia BETWEEN 0 AND 1),
    creato_il     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chunk_id, entita, chiave)
);
CREATE INDEX collegamenti_entita_idx ON arricchimenti.collegamenti (entita, chiave);

-- NON QUI, e perche:
--   registro sorgenti per le tabelle ERP e colonna sources.aziende: vanno
--     insieme al filtro per azienda nel gate (§15.9) — una colonna di
--     permesso che il codice non applica e peggio di nessuna colonna
--   contabilita generale, produzione, HR/paghe, CRM, cespiti: moduli
--     successivi, stesso schema (_versione + vista corrente)
