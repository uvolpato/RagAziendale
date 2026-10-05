"""Le descrizioni di documenti e pagine: QUESTO SCRIVE l'indice (D21).

Sta qui, in `ingestion`, e non nell'orchestratore, perche' scrive dati.
L'orchestratore e' l'agente che risponde alle domande: `orchestratore/indice.py`
LEGGE questa tabella (`pertinenti`, `pagine_pertinenti`,
`descrizioni_visibili`) e non la scrive. Uno legge, l'altro scrive, e non si
toccano.

Il 4/10/2026 queste due funzioni stavano nell'orchestratore e non le chiamava
nessuno: `indice` aveva 0 righe, i lettori giravano a vuoto, e la ricerca a
due stadi — prima QUALI documenti c'entrano, poi il dettaglio dentro quelli —
non era mai partita. Montare il pacchetto dell'orchestratore qui dentro per
chiamarle e' durato cinque minuti: si portava appresso la sua politica di
rete (`EgressNonDichiarato` su `host.docker.internal`), che e' il sintomo di
codice nel servizio sbagliato.

Niente PDF da rileggere: le descrizioni si scrivono sui PEZZI che Docling ha
gia' estratto. Per questo girano in CODA al giro, quando Docling ha finito e
la GPU e' libera: il modello di chat non ci sta in VRAM insieme a lui.
"""
import os
from concurrent.futures import ThreadPoolExecutor

from psycopg.rows import tuple_row

# Quante pagine descrivere INSIEME. Gli slot del modello di chat sono due
# (`--parallel 2`), e sono gli stessi che serve la chat: con entrambi occupati
# dall'indice un turno di chat aspetta. 1 lascia respiro, 2 va il doppio.
PARALLELO = int(os.environ.get("INDICE_PARALLELO", "2"))

ISTRUZIONI = (
    "Ti do le intestazioni di un documento aziendale. Scrivi in una frase "
    "breve (max 30 parole) di cosa parla.\n"
    "QUESTO ARCHIVIO NON E' FATTO SOLO DI CATALOGHI: ci sono cataloghi di "
    "prodotti, e ci sono procedure, manuali, relazioni, note di progetto, "
    "specifiche tecniche, regole di lavoro. Guarda le intestazioni e di' che "
    "cosa e' QUESTO documento: se e' un catalogo elenca le categorie di "
    "prodotti, se e' un testo di' il suo argomento.\n"
    "Non immaginare niente che le intestazioni non mostrino, nemmeno il "
    "GENERE del documento. Se sono poche e non bastano, dillo e riportale — "
    "«le intestazioni non dicono di cosa parla: x, y» — invece di "
    "immaginarlo: una descrizione inventata finisce nell'indice e manda a "
    "cercare nel documento sbagliato. Misurato: da una sola intestazione, "
    "«Sii sintetico nelle risposte», e' uscito «un catalogo di prodotti "
    "tecnologici» per un file di regole di progetto.\n"
    "Scrivi in italiano, solo la frase, niente introduzioni.\n"
    "/NO_THINK"
)

ISTRUZIONI_PAGINA = (
    "Ti do il testo di una pagina di un documento aziendale (catalogo, manuale, "
    "scheda tecnica). Scrivi in una frase breve (max 20 parole) di cosa parla "
    "questa pagina: quali prodotti, formati, argomenti o istruzioni contiene.\n"
    "Se la pagina elenca solo prodotti simili, riassumili in una categoria. Se "
    "e' una tabella di formati o prezzi, dillo. Non inventare dettagli che il "
    "testo non mostra. Scrivi in italiano, solo la frase, niente introduzioni.\n"
    "/NO_THINK"
)

# Il TEMPO DI CARICAMENTO, non il tempo di una risposta. Questo giro
# scarica il modello di chat per far posto a Docling (`scarica_llm`), e
# qui in coda llama-swap deve ricaricarne 9 GB: la prima chiamata paga
# il caricamento e a 60 secondi sforava — «TimeoutError: timed out», e
# la descrizione saltava (4/10/2026). Le successive sono veloci, perche'
# il modello resta caricato (ttl 0 nel YAML di llama-swap).
SECONDI = float(os.environ.get("INDICE_TIMEOUT", "300"))


def _chiedi(messaggi) -> str:
    """Una frase dal modello di chat, col canale che usa gia' l'ingestione.

    Non si importa il client dell'orchestratore: quello e' il client di un
    altro servizio, con le sue regole di rete. Qui si chiama il server dei
    modelli come lo chiama tutto il resto di questo file."""
    import json
    import urllib.request
    host = os.environ.get("MODELLI_HOST", "host.docker.internal:1235")
    url = host if "://" in host else f"http://{host}"
    corpo = json.dumps({
        "model": os.environ.get("MODELLO_CHAT", "qwen3-14b"),
        "max_tokens": 200, "temperature": 0.0,
        "reasoning_effort": "none",
        "messages": messaggi,
    }).encode()
    req = urllib.request.Request(f"{url.rstrip('/')}/v1/chat/completions",
                                 data=corpo,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=SECONDI) as r:
        return (json.load(r)["choices"][0]["message"].get("content") or "").strip()


def _intestazioni(conn, source_id, documento, limite=120):
    """Le prime righe dei pezzi di un documento: sono i titoli delle pagine.

    Un campione, non tutto: bastano a dire di cosa parla il documento, e un
    catalogo ha centinaia di pagine. La prima riga non vuota di ogni pezzo, un
    pezzo per pagina, senza doppioni."""
    righe = []
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            """SELECT page, content FROM chunks
               WHERE source_id = %s AND documento = %s
               ORDER BY page, id LIMIT %s""",
            (source_id, documento, limite * 3))
        for page, content in cur.fetchall():
            # Una riga per PEZZO, non una per pagina. Il campione per pagina
            # e' tarato sui PDF, dove un pezzo E' una pagina; un .md non ha
            # pagine — tutto il suo testo sta sulla pagina 0 — e «una per
            # pagina» diventava UNA IN TUTTO. I doppioni li toglie il
            # controllo qui sotto, che guarda il TESTO: e' quello che conta.
            prima = next((r.strip() for r in (content or "").splitlines()
                          if r.strip() and not r.strip().startswith("|")), "")
            if prima and prima not in righe:
                righe.append(prima[:120])
            if len(righe) >= limite:
                break
    return righe


def _testo_pagina(conn, source_id, documento, page, limite_car=2500):
    """Il testo di una pagina: i pezzi della pagina, incollati e tagliati."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            """SELECT content FROM chunks
               WHERE source_id = %s AND documento = %s AND page = %s
               ORDER BY id""",
            (source_id, documento, page))
        testo = " ".join(p for (c,) in cur.fetchall() for p in (c or "").split())
    return testo[:limite_car]


def _scrivi(conn, source_id, documento, page, descrizione, vettore):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO indice (source_id, documento, page, descrizione,
                                   descrizione_vec)
               VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
            (source_id, documento, page, descrizione, vettore))


def _vettore(testo):
    """Il vettore della descrizione, con l'embedding dell'ingestione.

    Import ritardato: `indicizza` importa questo modulo quando serve, e
    importarlo in testa farebbe un cerchio."""
    from indicizza import vettori, vettore_sql
    v = vettori([testo])
    return vettore_sql(v[0]) if v else None


def genera_documenti(conn, solo=None) -> int:
    """Le descrizioni a livello documento, in `indice` (page NULL).

    Una chiamata al modello per documento. `solo` (sottostringa del nome file)
    restringe ai documenti che la contengono. Non solleva: un documento
    fallito resta senza descrizione e si riprende al giro dopo."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            """SELECT d.source_id, d.documento FROM documenti d
               WHERE d.stato = 'indicizzato'
                 AND NOT EXISTS (SELECT 1 FROM indice i
                                 WHERE i.source_id = d.source_id
                                   AND i.documento = d.documento
                                   AND i.page IS NULL)
               ORDER BY d.source_id, d.documento""")
        pendenti = cur.fetchall()
    if solo:
        pendenti = [p for p in pendenti if solo in p[1]]
    fatti = 0
    for source_id, documento in pendenti:
        intestazioni = _intestazioni(conn, source_id, documento)
        if not intestazioni:
            continue
        try:
            descrizione = _chiedi([{"role": "system", "content": ISTRUZIONI},
                                   {"role": "user",
                                    "content": "\n".join(intestazioni)}])
        except Exception as e:
            print(f"  descrizione non riuscita per {documento}: "
                  f"{type(e).__name__}: {e}", flush=True)
            continue
        vettore = _vettore(descrizione) if descrizione else None
        if not vettore:
            continue
        _scrivi(conn, source_id, documento, None, descrizione, vettore)
        conn.commit()
        fatti += 1
        print(f"  descritto: {documento} -> {descrizione}", flush=True)
    if pendenti:
        print(f"indice documenti: {fatti} su {len(pendenti)}", flush=True)
    return fatti


def genera_pagine(conn, solo=None, quanti=None) -> int:
    """I riassunti a livello pagina, in `indice` (page N).

    Una chiamata al modello per pagina, e le pagine sono migliaia: `quanti`
    mette un tetto per giro, cosi' l'indicizzazione resta reattiva e l'indice
    si riempie nei giri successivi. Incrementale per costruzione — il
    `NOT EXISTS` salta quello che c'e' gia'."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            """SELECT DISTINCT c.source_id, c.documento, c.page
               FROM chunks c
               JOIN documenti d
                 ON d.source_id = c.source_id AND d.documento = c.documento
               WHERE d.stato = 'indicizzato' AND c.page IS NOT NULL
                 AND NOT EXISTS (SELECT 1 FROM indice i
                                 WHERE i.source_id = c.source_id
                                   AND i.documento = c.documento
                                   AND i.page = c.page)
               ORDER BY c.source_id, c.documento, c.page""")
        pendenti = cur.fetchall()
    if solo:
        pendenti = [p for p in pendenti if solo in p[1]]
    resta = len(pendenti)
    if quanti:
        pendenti = pendenti[:quanti]

    # Il TESTO si legge qui, prima dei thread: la connessione e' una e psycopg
    # non la vuole condivisa. Sono letture locali, costano niente.
    lavoro = []
    for source_id, documento, page in pendenti:
        testo = _testo_pagina(conn, source_id, documento, page)
        if testo:
            lavoro.append((source_id, documento, page, testo))

    def descrivi(una):
        """Solo modello ed embedding: niente database dentro il thread."""
        source_id, documento, page, testo = una
        try:
            descrizione = _chiedi([{"role": "system", "content": ISTRUZIONI_PAGINA},
                                   {"role": "user", "content": testo}])
        except Exception as e:
            print(f"  riassunto non riuscito per {documento} p.{page}: "
                  f"{type(e).__name__}: {e}", flush=True)
            return None
        if not descrizione:
            return None
        vettore = _vettore(descrizione)
        if not vettore:
            return None
        return (source_id, documento, page, descrizione, vettore)

    fatti = 0
    with ThreadPoolExecutor(max_workers=max(1, PARALLELO)) as pool:
        for esito in pool.map(descrivi, lavoro):
            if not esito:
                continue
            _scrivi(conn, *esito)
            conn.commit()
            fatti += 1
            if fatti % 20 == 0:
                print(f"  ... {fatti} riassunti pagina", flush=True)
    if pendenti:
        print(f"indice pagine: {fatti} scritti, {resta - fatti} da fare",
              flush=True)
    return fatti
