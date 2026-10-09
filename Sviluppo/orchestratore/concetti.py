"""Il grafo dei concetti, LETTO. Dai gruppi della persona ai suoi dataset.

Questo modulo non sa niente di Cognee: fa una GET al servizio `cognee-lettore`
(che e' l'unico che parla con il grafo) e gli dice su quali dataset cercare.
Il pacchetto di Cognee pesa 1,3 GB e si porta dentro litellm: non sta qui, e
qui non si scrive niente nel grafo — chi scrive e' l'ingestione.

PERCHE' I DATASET LI SCEGLIAMO NOI. Cognee applica le sue ACL (isolamento,
ereditarieta' dal ruolo e revoca, dimostrati il 5/10/2026), ma i gruppi della
persona li abbiamo noi, nel token: tradurli qui vuol dire che il filtro parte
da dove la verita' sta, e che un dataset non chiesto non viene nemmeno
nominato. La ricerca implicita di Cognee — «cerca in tutto quello a cui ho
diritto» — torna vuota a chi non e' il proprietario, quindi il nome va dato.

UN DATASET PER CARTELLA, oggi: `sources.percorso` finisce col nome della
cartella, e quel nome e' il dataset. Un domani un dataset che attraversa piu'
cartelle non rompe niente: qui si restituirebbe piu' di un nome.
"""
import os

from orchestratore import egress

LETTORE = os.environ.get("COGNEE_LETTORE", "http://cognee-lettore:8800")
SECONDI = float(os.environ.get("COGNEE_TIMEOUT", "20"))
# Quanto contesto chiedere. `only_context` torna un prompt confezionato, e
# piu' pezzi vuol dire piu' roba dentro il nostro.
QUANTI = int(os.environ.get("COGNEE_TOP_K", "10"))

# L'INVOLUCRO INGLESE che `only_context` mette attorno al contenuto: «The
# question is: ... and here is the context provided with a set of
# relationships from a knowledge graph, separate da una riga
# di tre trattini, ognuna come nodo1 -- relazione -- nodo2:
# as node1 -- relation -- node2 triplet:». Sono le istruzioni che Cognee
# darebbe al SUO modello; al nostro redattore va il contenuto, che comincia
# dove quelle finiscono. Si taglia all'ultimo marcatore che si trova.
INVOLUCRI = ("Nodes:", "triplet:",
             "and here is the context provided with a set of relationships")


def dataset_per(conn, gruppi: list) -> list:
    """I dataset su cui questa persona puo' cercare, dai suoi gruppi.

    Stessa `WHERE` del resto del progetto — gruppi, aziende, sorgente attiva —
    letta ADESSO, cosi' sospendere una fonte ha effetto nello stesso istante."""
    from orchestratore import identita
    aziende = identita.aziende(gruppi)
    if not gruppi or not aziende:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT percorso FROM sources
               WHERE acl_groups && %(gruppi)s::text[]
                 AND aziende && %(aziende)s::text[]
                 AND stato = 'attiva'""",
            {"gruppi": gruppi, "aziende": aziende})
        righe = cur.fetchall()
    nomi = []
    for r in righe:
        percorso = (r[0] if isinstance(r, (tuple, list)) else r["percorso"]) or ""
        nome = percorso.replace("\\", "/").rstrip("/").split("/")[-1]
        if nome and nome not in nomi:
            nomi.append(nome)
    return nomi


def _pulisci(testo: str) -> str:
    """Via l'involucro di Cognee, resta il contenuto."""
    t = " ".join((testo or "").split())
    for marcatore in INVOLUCRI:
        i = t.find(marcatore)
        if i >= 0:
            # `Nodes:` fa parte del contenuto: si taglia PRIMA, non dopo.
            taglio = i if marcatore == "Nodes:" else i + len(marcatore)
            return t[taglio:].lstrip(" :.,`")
    return t


def contesto(conn, cerca: str, gruppi: list) -> list:
    """[{dataset, contesto}] dal grafo, o [] se non c'e' niente o e' giu'.

    Non solleva: un grafo che non risponde non deve rompere un turno. Chi
    chiama tratta la lista vuota come «il grafo non ha niente da dire»."""
    nomi = dataset_per(conn, gruppi)
    if not nomi or not (cerca or "").strip():
        return []
    try:
        with egress.client(timeout=SECONDI) as c:
            r = c.get(LETTORE + "/contesto",
                      params=[("cerca", cerca), ("quanti", QUANTI)]
                             + [("dataset", n) for n in nomi])
            r.raise_for_status()
            pezzi = r.json().get("pezzi") or []
    except Exception as e:
        print("concetti: il grafo non risponde (%s: %s)"
              % (type(e).__name__, e), flush=True)
        return []
    fuori = []
    for p in pezzi:
        testo = _pulisci(p.get("contesto"))
        if testo:
            fuori.append({"dataset": p.get("dataset") or "?", "contesto": testo})
    return fuori


def _prova():
    vero = ("The question is: `x` and here is the context provided with a set "
            "of relationships from a knowledge graph separated by each "
            "represented as node1 -- relation -- node2 triplet: `Nodes: Node: "
            "punto di partenza")
    pulito = _pulisci(vero)
    assert pulito.startswith("Nodes:"), pulito[:60]
    assert "knowledge graph" not in pulito, pulito[:60]
    assert _pulisci("niente involucro") == "niente involucro"
    assert _pulisci(None) == ""
    print("concetti: ok")


if __name__ == "__main__":
    _prova()
