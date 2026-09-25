"""Ricerca ibrida con filtro ACL applicato NELLA query.

Due regole non negoziabili:

1. Il filtro sta nella query, non dopo: gruppi, aziende e stato della fonte.
   Si passa dalla tabella `sources`, che e' l'unica casa del dato di
   sicurezza: nulla e' duplicato sui chunk, quindi non puo' disallinearsi.

2. Fusione RRF di vettoriale e full-text. Il vettoriale sbaglia i codici
   articolo e i part number, il full-text li prende; il full-text sbaglia le
   domande in linguaggio naturale, il vettoriale le prende. La fusione costa
   zero servizi in piu'.

   Il ramo full-text NON usa piu' `plainto_tsquery`, e la ragione e' scritta
   sopra RAMO_LESSICALE. In breve: metteva i termini in AND, quindi «ciottoli
   neri lucidi» pretendeva tutte e tre le parole nello stesso pezzo e in tutto
   l'indice non ce n'era nessuno. Per mesi, su ogni domanda di piu' di una
   parola, il ramo ha restituito l'insieme vuoto e la fusione ha fuso il
   vettoriale con niente: l'ibrida era vettoriale travestita.

   Oggi cerca solo i termini RARI (un pezzo su mille o meno: i codici
   articolo, i nomi propri) e li ordina per rarita'. Su una domanda normale
   nessun termine e' cosi' raro, il ramo tace e la ricerca resta vettoriale —
   che su quelle domande e' la cosa giusta. Misurato: chiedere un articolo per
   codice dentro una frase passa da 19/24 a 21/24, e le risposte alle domande
   d'oro non cambiano.

Se l'host di inferenza non risponde non si puo' calcolare l'embedding della
domanda: si degrada al solo full-text con un avviso, invece di restare muti.
Non e' resilienza di lusso — l'host di inferenza in sviluppo e' LM Studio su
una postazione che va in sospensione.
"""
import os
import re

from psycopg.rows import dict_row

K_RRF = 60          # costante standard della Reciprocal Rank Fusion
CANDIDATI = 30      # quanti per ramo prima della fusione

CANDIDATI_RERANK = int(os.environ.get("CANDIDATI_RERANK", "150"))
RERANK_URL = os.environ.get("RERANK_URL", "")
RERANK_MODELLO = os.environ.get("RERANK_MODELLO", "text-embedding-bge-reranker-v2-m3")
# Quanto testo di ogni pezzo si manda a riordinare. Tutto il pezzo sarebbe
# piu' fedele ma 150 pezzi interi superano il contesto del reranker.
RERANK_CARATTERI = int(os.environ.get("RERANK_CARATTERI", "900"))

# Vincolo MORBIDO (esperimento reversibile): se il must-match del vincolo
# (colore/misura) svuota la pool, si riprova SENZA vincolo invece di tornare
# zero righe. Un colore che il catalogo scrive con un altro termine («tortora»
# dove il catalogo dice «beige») non deve azzerare la ricerca.
VINCOLO_MORBIDO = os.environ.get("VINCOLO_MORBIDO", "") == "1"

# Quanto dev'essere RARA una parola perche' il ramo lessicale la cerchi: al
# massimo un pezzo su MILLE. Con l'archivio di oggi (3112 pezzi) vuol dire al
# massimo 3 pezzi — che e' la definizione di un codice articolo o di un nome
# proprio. Nessuna parola italiana normale ci arriva, quindi su una domanda
# normale il ramo tace e resta la sola ricerca vettoriale: e' quello che
# faceva prima, e su quelle domande faceva bene.
#
# La soglia e' stata scelta misurando, non a occhio (22/09/2026, confronto
# appaiato sugli stessi tre metri):
#
#   soglia    codici (36 prove)   risposte (26 riscontri)
#   nessuna         29                    19
#   1 su 10         31                    17
#   1 su 100        31                    17
#   1 su 1000       31                    19
#
# A 1 su 10 il ramo si sveglia anche per parole come «palline» e cambia la
# composizione del contesto: gli stessi riscontri ci sono, ma il modello ne
# riporta due di meno. A 1 su 1000 il guadagno sui codici resta e la perdita
# sparisce.
#
# ATTENZIONE: misurata su 3112 pezzi. Su un archivio molto piu' grande «un
# pezzo su mille» sono centinaia di pezzi, e la soglia va rimisurata — non
# dedotta. Per questo e' una variabile e non un numero scritto nella query.
PEZZI_SU = int(os.environ.get("LESSEMA_COMUNE_SU", "1000"))

# Il ramo LESSICALE, pesato per rarita' (IDF).
#
# Prima ordinava con `ts_rank_cd`, che conta quante volte una parola compare
# DENTRO il pezzo e non si chiede quanto quella parola sia rara nell'archivio.
# Il 22/09/2026, su «quanto costa il DST2040», i primi cinque risultati non
# contenevano il codice: per Postgres «costa» (29 pezzi) valeva quanto
# «DST2040» (2 pezzi su 3112). Con l'IDF il codice torna in posizione 1.
#
# La formula e' quella di sempre: ogni parola vale ln(totale / in quanti pezzi
# compare), e il punteggio di un pezzo e' la somma delle parole che contiene.
# Una parola in 2 pezzi su 3112 vale 7,3; una in 1500 vale 0,7.
#
# I conteggi stanno in `lessemi`, riscritta quando si indicizza (migrazione
# 016). Non e' un elenco di parole da ignorare scritto a mano: nessuno decide
# che «costa» conta poco, lo decide l'archivio contando. Su un archivio di
# ricette «forno» sarebbe comune e «bergamotto» raro, senza toccare niente.
#
# `%(domanda)s` resta un parametro: il testo dell'utente non entra mai
# nell'SQL. I lessemi che finiscono nella tsquery li ha prodotti Postgres da
# quel parametro, e ci tornano passati da quote_literal.
def _ramo_lessicale(ripiego: bool) -> str:
    """Il frammento SQL del ramo lessicale.

    `ripiego`: cosa fare quando la domanda non contiene NESSUN termine raro.
        False (ricerca ibrida) il ramo tace. Il vettoriale c'e' e su quelle
              domande fa meglio da solo: misurato, aggiungere i termini comuni
              faceva riportare due riscontri in meno.
        True  (solo testo, host dei modelli spento) si cercano tutti i
              termini. Qui il lessicale e' l'UNICA ricerca: tacere vorrebbe
              dire non rispondere, e la modalita' degradata esiste per
              rispondere qualcosa. Scoperto dai test T1.15/T1.16, che girano
              senza vettore: con la regola stretta anche per loro, una fonte
              attiva smetteva di rispondere.
    """
    rari = ("SELECT string_agg(quote_literal(t.parola), ' | ')"
            "  FROM termini t, totale WHERE t.pezzi <= totale.n / {soglia}")
    tutti = "SELECT string_agg(quote_literal(t.parola), ' | ') FROM termini t"
    scelti = (f"COALESCE(({rari}), ({tutti}))" if ripiego else f"({rari})")
    return SCHELETRO_LESSICALE.format(soglia=PEZZI_SU, scelti=scelti.format(soglia=PEZZI_SU))


SCHELETRO_LESSICALE = """
termini AS (
    -- Le parole della domanda ridotte a radice da Postgres («ciottoli» e
    -- «ciottolo» sono lo stesso lessema), ognuna col suo conteggio.
    -- Sconosciuta = 1, cioe' rarissima: una parola mai vista e' informativa,
    -- non trascurabile (succede fra un'indicizzazione e la successiva).
    SELECT t.parola, COALESCE(l.pezzi, 1) AS pezzi
      FROM unnest(tsvector_to_array(to_tsvector('italian', %(domanda)s))) AS t(parola)
      LEFT JOIN lessemi l ON l.parola = t.parola
),
totale AS (
    -- Scalare, non una riga di tabella: se `lessemi_stato` fosse vuota — un
    -- impianto nuovo, una migrazione a meta' — la CTE non darebbe nessuna
    -- riga e la ricerca tornerebbe MUTA invece che imprecisa.
    -- GREATEST(...,2) perche' con l'archivio vuoto ln(1/1) sarebbe zero per
    -- tutti e l'ordine diventerebbe casuale invece di essere assente.
    SELECT GREATEST(COALESCE((SELECT pezzi_totali FROM lessemi_stato), 0), 2) AS n
),
scelti AS (
    -- Quali termini cercare: lo decide _ramo_lessicale (vedi la sua
    -- spiegazione). Niente segno di percentuale nei commenti di questo
    -- frammento: psycopg lo legge come un segnaposto e la query non parte.
    SELECT ({scelti})::tsquery AS q
),
punteggiati AS (
    SELECT c.id,
           (SELECT COALESCE(SUM(ln(totale.n::float / t.pezzi)), 0)
              FROM termini t, totale
             WHERE t.parola = ANY(tsvector_to_array(to_tsvector('italian', c.content)))
           ) AS punti
      FROM chunks c
      JOIN consentite s ON s.id = c.source_id
      CROSS JOIN scelti
     WHERE scelti.q IS NOT NULL
       AND to_tsvector('italian', c.content) @@ scelti.q
       %%DOC%%
)
"""


SQL_IBRIDA = """
WITH consentite AS (
    -- Il filtro: intersezione fra i gruppi del token e quelli della sorgente
    -- (niente livelli numerici, niente gerarchie), E fra le aziende del token
    -- e quelle della sorgente (decisione 53), E solo fonti attive: una fonte
    -- sospesa o in attesa di approvazione non risponde (decisione 67).
    SELECT id, residency FROM sources
     WHERE acl_groups && %(gruppi)s::text[]
       AND aziende && %(aziende)s::text[]
       AND stato = 'attiva'
),
%%VINC_CTE%%""" + _ramo_lessicale(ripiego=False).rstrip() + "," + """
vett AS (
    SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> %(qvec)s::vector) AS r
    FROM chunks c
    WHERE c.source_id IN (SELECT id FROM consentite)
      AND c.embedding IS NOT NULL
      %%VINC_VETT%%
      %%DOC%%
    ORDER BY c.embedding <=> %(qvec)s::vector
    LIMIT %(cand)s
),
testo AS (
    SELECT id, row_number() OVER (ORDER BY punti DESC, id) AS r
      FROM punteggiati WHERE punti > 0
      %%VINC_TEST%%
     ORDER BY punti DESC, id
     LIMIT %(cand)s
),
fusi AS (
    SELECT id, SUM(punti) AS punteggio FROM (
        SELECT id, 1.0 / (%(k)s + r) AS punti FROM vett
        UNION ALL
        SELECT id, 1.0 / (%(k)s + r) AS punti FROM testo
    ) x GROUP BY id
)
SELECT c.id, c.source_id, c.documento, c.page, c.content,
       s.residency, f.punteggio,
       (SELECT array_agg(i.id ORDER BY i.id) FROM immagini i
         WHERE i.source_id = c.source_id AND i.documento = c.documento
           AND i.page IS NOT DISTINCT FROM c.page) AS immagini
FROM fusi f
JOIN chunks c  ON c.id = f.id
JOIN consentite s ON s.id = c.source_id
ORDER BY f.punteggio DESC
LIMIT %(limite)s;
"""

# Variante senza vettoriale: usata quando l'host di inferenza non risponde.
SQL_SOLO_TESTO = """
WITH consentite AS (
    SELECT id, residency FROM sources
     WHERE acl_groups && %(gruppi)s::text[]
       AND aziende && %(aziende)s::text[]
       AND stato = 'attiva'
),
%%VINC_CTE%%""" + _ramo_lessicale(ripiego=True).strip().rstrip(",") + """
SELECT c.id, c.source_id, c.documento, c.page, c.content,
       s.residency, p.punti AS punteggio,
       (SELECT array_agg(i.id ORDER BY i.id) FROM immagini i
         WHERE i.source_id = c.source_id AND i.documento = c.documento
           AND i.page IS NOT DISTINCT FROM c.page) AS immagini
FROM punteggiati p
JOIN chunks c ON c.id = p.id
JOIN consentite s ON s.id = c.source_id
WHERE p.punti > 0
      %%VINC_SOLO%%
ORDER BY p.punti DESC, c.id
LIMIT %(limite)s;
"""


def cerca(conn, domanda: str, gruppi: list[str], qvec=None, limite: int = 8,
          vincolo: str = "", documenti: list[str] | None = None):
    """Restituisce (righe, degradato).

    limite 8 e non 5 (dal 20/09/2026): sui cataloghi un solo prodotto occupa
    piu' pezzi fra testo, tabella e descrizione della figura, e con 5 posti una
    domanda larga ("sassi rossi") restava senza il testo del prodotto. Da
    validare con l'eval: piu' pezzi significa anche piu' contesto da leggere per
    il modello, e con 5 utenti in parallelo il contesto costa VRAM.

    `vincolo` (D19): espressione regolare dei termini di un attributo ENUMERABILE
    (colore, misura, formato, prezzo) nelle lingue dei cataloghi. Se presente,
    la ricerca resta SOLO dentro i pezzi che la contengono: e' la POOL definita
    dal vincolo, sopra la quale vettoriale e full-text ordinano. Se la pool e'
    vuota non tornano righe, e il chiamante risponde onestamente senza chiamare
    il modello. Prima di D19 un vero natalizio viola stava a posizione 753 su
    1434 per coseno: il vincolo non si cerca col vettore, si esige nel testo.

    `documenti` (D21): se presente, la ricerca resta DENTRO quei documenti. E'
    il secondo passaggio dell'indice (indice.py): il primo ha scelto quali
    cataloghi c'entrano, questo cerca solo li'. E' un restringimento del
    recupero, non dei permessi: le ACL restano nella WHERE come sempre.

    `qvec` None significa embedding non disponibile -> solo full-text.
    """
    from .identita import aziende as aziende_di
    aziende = aziende_di(gruppi)
    if not gruppi or not aziende:
        # Nessun gruppo o nessuna azienda, nessun accesso. Esplicito per non
        # dipendere dal comportamento di && con array vuoto.
        return [], False

    degradato = qvec is None
    # D19: la pool del vincolo, dentro "consentite". Nei template SQL i
    # marcatori %%VINC_*%% vengono sostituiti col frammento giusto se c'e' un
    # vincolo, o con niente se non c'e' (i rami tornano quelli di prima).
    # Il vincolo viaggia come parametro, mai nel testo SQL.
    cte = ("vinc AS (\n"
           "    SELECT c.id\n"
           "      FROM chunks c\n"
           "      JOIN consentite s ON s.id = c.source_id\n"
           "     WHERE c.content ~* %(vincolo)s\n"
           "),\n" if vincolo else "")
    filtro_vett = "AND c.id IN (SELECT id FROM vinc)\n" if vincolo else ""
    filtro_testo = "AND id IN (SELECT id FROM vinc)\n" if vincolo else ""
    filtro_solo = "AND p.id IN (SELECT id FROM vinc)\n" if vincolo else ""
    # D21: restrizione ai documenti scelti dall'indice. Parametro, mai testo.
    filtro_doc = "AND c.documento = ANY(%(documenti)s::text[])\n" if documenti else ""
    if degradato:
        sql = (SQL_SOLO_TESTO.replace("%%VINC_CTE%%", cte)
                              .replace("%%VINC_SOLO%%", filtro_solo)
                              .replace("%%DOC%%", filtro_doc))
    else:
        sql = (SQL_IBRIDA.replace("%%VINC_CTE%%", cte)
                         .replace("%%VINC_VETT%%", filtro_vett)
                         .replace("%%VINC_TEST%%", filtro_testo)
                         .replace("%%DOC%%", filtro_doc))
    # Con il rerank si pesca largo e si sceglie dopo: la fusione decide un
    # ordine, il cross-encoder lo corregge guardando domanda e pezzo insieme.
    # Senza rerank configurato si prendono gli stessi di prima, altrimenti si
    # manderebbero al modello 150 pezzi invece di 8.
    largo = bool(RERANK_URL) and not degradato
    par = {"gruppi": gruppi, "aziende": aziende, "domanda": domanda,
           "limite": max(limite, CANDIDATI_RERANK) if largo else limite}
    if not degradato:
        par |= {"qvec": qvec, "cand": max(CANDIDATI, CANDIDATI_RERANK) if largo else CANDIDATI,
                "k": K_RRF}
    if vincolo:
        par["vincolo"] = vincolo
    if documenti:
        par["documenti"] = documenti

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, par)
        righe = cur.fetchall()
    righe = riordina(domanda, righe, limite) if largo else righe
    # Vincolo morbido: la pool del must-match e' vuota -> si riprova senza.
    if vincolo and VINCOLO_MORBIDO and not righe:
        return cerca(conn, domanda, gruppi, qvec=qvec, limite=limite,
                     vincolo="", documenti=documenti)
    return righe, degradato


def contiene_interno(righe) -> str | None:
    """La sorgente `interno` che contamina il turno, se c'e'."""
    for r in righe:
        if r["residency"] == "interno":
            return r["source_id"]
    return None


def embedding(domanda: str):
    """Embedding della domanda, chiesto DIRETTAMENTE a llama-swap (il modello
    bge-m3 locale). Senza litellm (tolto il 24/09/2026).

    Restituisce None se l'host di inferenza non risponde: il chiamante degrada
    su BM25 invece di propagare l'errore.
    """
    from . import egress

    host = os.environ.get("MODELLI_HOST", "host.docker.internal:1235")
    modello = os.environ.get("EMBEDDING_MODELLO", "text-embedding-bge-m3-embeddings")
    try:
        with egress.client(timeout=20.0, verify=False) as c:
            r = c.post(f"http://{host}/v1/embeddings",
                       json={"model": modello, "input": [domanda]})
            r.raise_for_status()
            return r.json()["data"][0]["embedding"]
    except egress.EgressVietato:
        raise           # non si mascherano le violazioni di egress
    except Exception:
        return None     # host giu', modello scaricato, timeout: si degrada


SQL_IMMAGINI = """
WITH consentite AS (
    -- Stesso filtro della ricerca sul testo: gruppi, aziende, fonte attiva.
    -- Le ACL stanno sulla fonte e non si duplicano sulle immagini.
    SELECT id FROM sources
     WHERE acl_groups && %(gruppi)s::text[]
       AND aziende && %(aziende)s::text[]
       AND stato = 'attiva'
)
SELECT i.id, i.source_id, i.documento, i.page, i.descrizione,
       (i.embedding <=> %(qvec)s::vector) AS distanza,
       -- Lo stesso punteggio di rarita' che ordina (vedi ORDER BY): serve
       -- FUORI da qui per decidere QUANTE figure mostrare. Se qualcuna
       -- contiene i termini rari della domanda — un codice articolo — quelle
       -- sono le figure chieste, e le altre non vanno mostrate per riempire.
       (SELECT COALESCE(SUM(ln(GREATEST(COALESCE(
                   (SELECT pezzi_totali FROM lessemi_stato), 0), 2)::float
                 / GREATEST(COALESCE(l.pezzi, 1), 1))), 0)
          FROM unnest(tsvector_to_array(to_tsvector('italian', %(domanda)s))) AS t(parola)
          LEFT JOIN lessemi l ON l.parola = t.parola
         WHERE l.pezzi IS NULL OR l.pezzi <= GREATEST(COALESCE(
                   (SELECT pezzi_totali FROM lessemi_stato), 0), 2) / """ + str(PEZZI_SU) + """
           AND t.parola = ANY(tsvector_to_array(to_tsvector('italian', i.descrizione)))
       ) AS rarita
  FROM immagini i
 WHERE i.source_id IN (SELECT id FROM consentite)
   -- La descrizione e' un criterio di ORDINAMENTO, non un biglietto
   -- d'ingresso. Su EUROSAND solo 115 immagini su 894 ne hanno una (la soglia
   -- descrive le figure grandi, e i campioni di colore di un catalogo sono
   -- piccoli): pretenderla rendeva INVISIBILI proprio le foto dei prodotti.
   -- Il 22/09/2026 alla domanda sui sassi rossi mancavano 7_67 e 7_69, i
   -- campioni della pagina 7, perche' non descritti.
   -- Senza pagine dichiarate invece si resta prudenti: pescare da tutto un
   -- catalogo senza sapere cosa raffigurano darebbe figure a caso.
   AND (i.embedding IS NOT NULL OR %(pagine)s::int[] IS NOT NULL)
   AND (%(documenti)s::text[] IS NULL OR i.documento = ANY(%(documenti)s::text[]))
   -- Solo le PAGINE che hanno risposto, non tutto il documento. Restringere
   -- al documento non restringe niente quando la risposta viene da un
   -- catalogo solo: il 22/09/2026 alla domanda «sassi rossi» sono uscite
   -- sfere di acciaio (pagina 31), ciottoli di fiume (63) e stelline
   -- natalizie (92), mentre il testo citava le pagine 4, 6 e 7 — e la frase
   -- sopra le immagini diceva «le figure delle pagine citate».
   AND (%(pagine)s::int[] IS NULL OR i.page = ANY(%(pagine)s::int[]))
 ORDER BY
          -- Prima chi contiene i termini RARI della domanda, pesati per
          -- rarita' come nel ramo lessicale del testo (vedi RAMO_LESSICALE).
          --
          -- Il vettoriale sui codici non puo' funzionare: GRA1040 e GRA1041
          -- sono due prodotti diversi e due punti quasi coincidenti. Misurato
          -- il 22/09/2026: chiedendo la figura di GRA1041 usciva GRA1040, di
          -- FSA1043 usciva FSA1081, di EKM6099 usciva EKM2099 — sempre il
          -- vicino di casa. Mettere il codice nella descrizione non bastava:
          -- il dato c'era e la ricerca non sapeva vederlo. Con questo, da
          -- 7 su 12 a 11 su 12, tutte in prima posizione.
          --
          -- Se la domanda non ha termini rari il punteggio e' zero per tutte
          -- e l'ordine torna quello di prima, il vettoriale: e' il caso di
          -- «sassi rossi», dove il codice non c'e' e non deve contare nulla.
          (SELECT COALESCE(SUM(ln(GREATEST(COALESCE(
                      (SELECT pezzi_totali FROM lessemi_stato), 0), 2)::float
                    / GREATEST(COALESCE(l.pezzi, 1), 1))), 0)
             FROM unnest(tsvector_to_array(to_tsvector('italian', %(domanda)s))) AS t(parola)
             LEFT JOIN lessemi l ON l.parola = t.parola
            WHERE l.pezzi IS NULL OR l.pezzi <= GREATEST(COALESCE(
                      (SELECT pezzi_totali FROM lessemi_stato), 0), 2) / """ + str(PEZZI_SU) + """
              AND t.parola = ANY(tsvector_to_array(to_tsvector('italian', i.descrizione)))
          ) DESC,
          (i.embedding IS NULL),          -- poi quelle che sappiamo leggere
          i.embedding <=> %(qvec)s::vector,
          i.page, i.id                    -- e infine nell'ordine della pagina
 LIMIT %(limite)s;
"""


def immagini_pertinenti(conn, qvec, gruppi, documenti=None, limite: int = 4,
                        pagine=None, domanda: str = ""):
    """Le figure che RISPONDONO alla domanda, non quelle che stanno vicino al
    testo che ha risposto.

    Prima le immagini si prendevano per pagina: in un catalogo una pagina
    contiene dieci prodotti, e uscivano figure che non c'entravano. Ora si
    cerca fra le descrizioni prodotte dal modello visivo, nello stesso spazio
    vettoriale dei pezzi: «sassi rossi» puo' incontrare «dark red lava rocks».

    `documenti` e `pagine`: si resta dove il testo ha risposto. Il documento da
    solo non basta — con una risposta che viene da un catalogo solo, «stesso
    documento» lascia candidate tutte le sue pagine, e il 22/09/2026 alla
    domanda «sassi rossi» sono uscite sfere di acciaio dalla pagina 31 mentre
    la risposta citava le pagine 4, 6 e 7.
    """
    if qvec is None:
        return []
    from .identita import aziende as aziende_di
    aziende = aziende_di(gruppi)
    if not gruppi or not aziende:
        return []
    par = {"gruppi": gruppi, "aziende": aziende, "qvec": qvec, "domanda": domanda or "",
           "documenti": documenti or None, "pagine": pagine or None, "limite": limite}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(SQL_IMMAGINI, par)
        return cur.fetchall()


# ------------------------------------------------------------------ altri modi di guardare
#
# La ricerca a somiglianza risponde a UNA domanda: «dammi i pezzi che
# assomigliano a questa frase». Funziona quando l'utente DESCRIVE una cosa, e
# sbanda quando la NOMINA: un codice o un nome di linea, dentro un pezzo di
# duecento caratteri, si diluisce. Misurato il 21/09/2026 sulle domande vere:
# «le sfere Marrakesch» aveva la risposta in posizione 26 su 4571, fuori dalle
# prime 8, mentre la parola MARRAKESCH nel catalogo compare in una pagina sola.
#
# Qui sotto le altre due domande che servono — «dove compare questa parola» e
# «cosa c'e' intorno» — piu' «cosa c'e' in generale». Sono le mosse che hanno
# risolto a mano il caso «sassi rossi» (VALUTAZIONE-ORCHESTRATORE-AGENTE.md §9).
#
# Il filtro dei permessi e' lo STESSO di cerca(), nella query: gruppi ∩
# acl_groups, aziende ∩ aziende, stato = 'attiva'. Non e' copiato per pigrizia
# ma perche' deve valere qui identico: uno strumento che dimentica le ACL e' una
# fuga di dati, e questi strumenti li chiamera' un agente.

_CONSENTITE = """
    SELECT id, residency FROM sources
     WHERE acl_groups && %(gruppi)s::text[]
       AND aziende && %(aziende)s::text[]
       AND stato = 'attiva'
"""

SQL_ESATTA = f"""
WITH consentite AS ({_CONSENTITE})
SELECT c.id, c.source_id, c.documento, c.page, c.content, s.residency
FROM chunks c
JOIN consentite s ON s.id = c.source_id
WHERE c.content ILIKE %(motivo)s
ORDER BY length(c.content), c.documento, c.page
LIMIT %(limite)s;
"""

SQL_PAGINA = f"""
WITH consentite AS ({_CONSENTITE})
SELECT c.id, c.source_id, c.documento, c.page, c.content, s.residency
FROM chunks c
JOIN consentite s ON s.id = c.source_id
WHERE c.documento = %(documento)s AND c.page IS NOT DISTINCT FROM %(page)s
ORDER BY c.id;
"""

SQL_DOCUMENTI = f"""
WITH consentite AS ({_CONSENTITE})
SELECT c.source_id, c.documento, count(*) AS pezzi,
       min(c.page) AS prima, max(c.page) AS ultima
FROM chunks c
JOIN consentite s ON s.id = c.source_id
GROUP BY c.source_id, c.documento
ORDER BY c.documento;
"""


def _permessi(gruppi):
    """(gruppi, aziende) o None se questa persona non puo' vedere niente.
    Stessa regola di cerca(): nessun gruppo o nessuna azienda = nessun dato."""
    from .identita import aziende as aziende_di
    aziende = aziende_di(gruppi)
    if not gruppi or not aziende:
        return None
    return {"gruppi": gruppi, "aziende": aziende}


def cerca_esatta(conn, termine: str, gruppi: list[str], limite: int = 8):
    """I pezzi che contengono ALLA LETTERA `termine`: un codice, un nome di
    linea, una sigla. Maiuscole ignorate.

    Ordinati dal pezzo piu' CORTO: se «MARRAKESCH» compare nel titolo di una
    pagina e dentro un indice lungo, il titolo e' quasi sempre la risposta e
    l'indice quasi mai. Niente punteggi: o la parola c'e' o non c'e'.

    `termine` e' trattato come testo, non come motivo di ricerca: i caratteri
    speciali di LIKE si neutralizzano, cosi' un termine con `%` dentro non
    diventa «qualsiasi cosa»."""
    par = _permessi(gruppi)
    if par is None or not (termine or "").strip():
        return []
    pulito = termine.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    par |= {"motivo": f"%{pulito}%", "limite": limite}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(SQL_ESATTA, par)
        return cur.fetchall()


SQL_GREP = f"""
WITH consentite AS ({_CONSENTITE})
SELECT c.documento, count(*) AS pezzi, min(c.page) AS prima, max(c.page) AS ultima
FROM chunks c
JOIN consentite s ON s.id = c.source_id
WHERE c.content ILIKE %(motivo)s
GROUP BY c.documento
ORDER BY pezzi DESC;
"""


def grep(conn, termine: str, gruppi: list[str]):
    """Quanti pezzi (e in quali documenti) contengono `termine`.

    E' il passo di ORIENTAMENTO che a mano ha scoperto il difetto «nastri blu»:
    la ricerca diceva «non c'e'» mentre il termine compariva in 240 pezzi. Il
    modello, prima di fidarsi di un «non trovato», puo' chiedere «ma 'blu'
    esiste davvero? dove?». Niente punteggi: solo dove e quanto."""
    par = _permessi(gruppi)
    if par is None or not (termine or "").strip():
        return []
    pulito = termine.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    par |= {"motivo": f"%{pulito}%"}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(SQL_GREP, par)
        return cur.fetchall()


# Le figure di INTRO/MARCHIO che non sono prodotti: loghi, branding, pagine
# «customization», copertine («## » e' il titolo della pagina) e il footer
# (Susteren, il paese nell'indirizzo). Il VLM le descrive come ogni altra
# figura, ma non sono cio' che si cerca in un catalogo: le si scarta dal
# `cerca_figure` (24/09/2026, «nastri blu» tornava copertine e footer).
SCARTA_FIGURE = r"logo|branding|customization|digital graphic|Susteren|##"


SQL_FIGURE = f"""
WITH consentite AS ({_CONSENTITE})
SELECT i.id, i.source_id, i.documento, i.page, i.descrizione, i.percorso
FROM immagini i
JOIN consentite s ON s.id = i.source_id
WHERE i.descrizione ~* %(oggetto)s
  AND i.descrizione !~* %(scarta)s
%%DOC%%
%%VINC%%
ORDER BY i.page, i.id
LIMIT %(limite)s;
"""


def cerca_figure(conn, termini, vincolo="", gruppi=None, limite=12, documenti=None):
    """Le figure la cui descrizione contiene uno dei `termini` (l'oggetto) e,
    se c'e', il `vincolo` (l'attributo). `documenti` restringe a quei documenti
    (pre-selezione dell'indice).

    E' il modo in cui un catalogo va cercato: il prodotto sta nella FIGURA,
    non nel testo. Qui si trova la pagina da indicare alla persona — «i nastri
    blu sono qui, qui e qui» — senza estrarre codici."""
    par = _permessi(gruppi)
    if par is None or not termini:
        return []
    puliti = [t for t in (t.strip() for t in termini) if t]
    if not puliti:
        return []
    par |= {"oggetto": "|".join(re.escape(t) for t in puliti),
            "scarta": SCARTA_FIGURE, "limite": limite}
    vinc = "AND i.descrizione ~* %(vincolo)s" if vincolo else ""
    if vincolo:
        par["vincolo"] = vincolo
    doc = "AND i.documento = ANY(%(documenti)s::text[])" if documenti else ""
    if documenti:
        par["documenti"] = documenti
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(SQL_FIGURE.replace("%%VINC%%", vinc).replace("%%DOC%%", doc), par)
        righe = cur.fetchall()
    # Vincolo morbido: la pool del must-match e' vuota -> si riprova senza.
    if vincolo and VINCOLO_MORBIDO and not righe:
        return cerca_figure(conn, termini, "", gruppi, limite, documenti)
    return righe


def pagina(conn, documento: str, page, gruppi: list[str]):
    """Tutti i pezzi di UNA pagina, nell'ordine in cui stanno sul foglio.

    «DST2001 rot red» da solo non vuol dire niente; la pagina dice che sono
    pietre decorative da 9-13 mm e quali colori esistono. Senza questo il
    modello vede il frammento e non ha modo di chiedere cosa c'e' intorno."""
    par = _permessi(gruppi)
    if par is None:
        return []
    par |= {"documento": documento, "page": page}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(SQL_PAGINA, par)
        return cur.fetchall()


def documenti_visibili(conn, gruppi: list[str]):
    """I documenti che questa persona puo' vedere, con quanti pezzi e che
    pagine. Serve a due cose: rispondere a «quali cataloghi avete?», che oggi
    il sistema non sa fare perche' sa solo cercare DENTRO i documenti; e
    permettere di dire «ho guardato nei quattro cataloghi a cui hai accesso e
    non c'e'» invece di un «non trovato» che non si sa quanto valga."""
    par = _permessi(gruppi)
    if par is None:
        return []
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(SQL_DOCUMENTI, par)
        return cur.fetchall()


# ------------------------------------------------------------------ rerank
#
# La ricerca a somiglianza confronta domanda e documento SEPARATAMENTE: ognuno
# diventa 1024 numeri, e il confronto avviene dopo che il contesto e' stato
# buttato via. Un cross-encoder li guarda INSIEME, e discrimina molto meglio:
# misurato il 21/09/2026, +5,54 per una corrispondenza vera contro -11,04 per
# una frase fuori tema, 2,6 s su 40 brani.
#
# Serve perche' il 22/09/2026, con i due sguardi sulla stessa pagina, i pezzi
# sono passati da 361 a 786 mentre il budget restava 8: le risposte delle
# domande 1, 5 e 6 stavano in posizione 21, 27 e 45 — dentro l'indice, fuori
# dalla finestra. Allargare i candidati senza riordinarli non basta, perche' e'
# la FUSIONE a scegliere gli 8 finali.
#
# Se il reranker non risponde non si fallisce: si tiene l'ordine della fusione,
# cioe' il comportamento di prima.

def riordina(domanda: str, righe: list, limite: int) -> list:
    """Le righe riordinate dal cross-encoder, le prime `limite`.

    Non tocca i PERMESSI: arrivano gia' filtrate dalla query SQL, e qui si
    cambia solo l'ordine. Un pezzo che non era consentito non puo' comparire,
    qualunque cosa dica il modello."""
    if not RERANK_URL or len(righe) <= limite:
        return righe[:limite]
    import httpx
    documenti = [(r.get("content") or "")[:RERANK_CARATTERI] for r in righe]
    try:
        r = httpx.post(f"{RERANK_URL.rstrip('/')}/rerank",
                       json={"model": RERANK_MODELLO, "query": domanda, "documents": documenti},
                       timeout=float(os.environ.get("RERANK_TIMEOUT", "60")))
        r.raise_for_status()
        ordine = [x["index"] for x in r.json()["results"]]
    except Exception as e:
        print(f"rerank non disponibile ({type(e).__name__}: {e}): si tiene l'ordine della fusione",
              flush=True)
        return righe[:limite]
    # Indici fuori intervallo o mancanti: si ignorano invece di far saltare il
    # turno. Poi si completa con l'ordine della fusione, se il modello ne ha
    # restituiti meno del dovuto.
    scelti = [righe[i] for i in ordine if 0 <= i < len(righe)][:limite]
    visti = {id(x) for x in scelti}
    for x in righe:
        if len(scelti) >= limite:
            break
        if id(x) not in visti:
            scelti.append(x)
    return scelti
