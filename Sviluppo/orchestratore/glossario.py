"""Glossario multilingue di dominio (ex «sinonimi», D20).

I cataloghi sono trilingue (it/de/en) e il vettore, pur multilingue, NON
collega i nomi di dominio fra lingue: misurato il 23/09/2026, coseno(«sassi
rossi», «palline natalizie rosse») = 0.5065 mentre coseno(«sassi rossi»,
«river pebbles dunkelrot») = 0.4415. I colori il vettore li collega da solo; i
NOMI delle cose no.

Qui sta il glossario: voce -> termini nelle lingue dei cataloghi, in una
tabella Postgres (migrazione 020). Lo consulta la ricerca per estendere la
domanda: «sassi» aggiunge «pietre, ciottoli, pebbles, kiesel», e «pietre» (dal
corpus) aggiunge «rocks, pierres, dekosteine» — i nomi che il modello NON sa
ma che stanno scritti nei cataloghi.

Due sorgenti, una tabella:
- il MODELLO, al primo incontro di una parola che non c'e' (glossario_di): la
  sua memoria copre «sassi» -> «pietre, ciottoli, pebbles» ma non i nomi di
  dominio rari («rocks», «dekosteine»).
- il CORPUS, con estrai_glossario(): il modello LEGGE i nomi trilingue dei
  cataloghi («pebbles | galets | ciottoli») e ne estrae voce -> termini. E'
  lui a capire cos'e' l'oggetto e cosa e' un aggettivo o un colore, non una
  lista di parole hardcoded.

Una voce con array vuoto significa «gia' guardata, nessun termine»: cosi' il
modello non viene ri-chiamato per «bisogno», «quanto», «costa» a ogni turno.
"""
import json
import os
import threading

from psycopg.rows import tuple_row

from orchestratore import modello

ISTRUZIONI = (
    "Dammi, nelle lingue italiano, tedesco e inglese, le parole DIVERSE con cui "
    "un catalogo di decorazioni e giardinaggio chiamerebbe la cosa indicata.\n"
    "NON ripetere la parola stessa: voglio traduzioni e termini equivalenti, non "
    "declinazioni.\n"
    "Massimo 6 parole, le piu' utili per trovare il prodotto in un catalogo.\n"
    "Esempio: «sassi» -> «pietre, ciottoli, pebbles, river pebbles, kiesel».\n"
    "Se la parola non e' un oggetto o materiale da catalogo (per esempio "
    "«bisogno», «quanto», «costa», «ho», «di», o un COLORE come «rosso», "
    "«blu», «verde»), rispondi SOLO con VUOTO.\n"
    "Rispondi solo con parole separate da virgola.\n"
    "/no_think"
)

# Cache in RAM: voce -> lista termini (anche vuota, per le voci gia' guardate).
# Caricata dalla tabella al primo uso, aggiornata in scrittura.
_cache: dict[str, list] = {}
_caricate: set[str] = set()
_lock = threading.Lock()


def _chiedi(messaggi) -> str:
    return modello.chiedi(messaggi)


def _genera(parola: str) -> list:
    """Chiede al modello i termini multilingue di `parola`. Mai solleva."""
    messaggi = [{"role": "system", "content": ISTRUZIONI},
                {"role": "user", "content": parola}]
    try:
        testo = _chiedi(messaggi)
    except Exception:
        return []
    testo = testo.split("```")[-2] if "```" in testo else testo
    testo = testo.strip().strip("`").strip()
    if not testo or testo.lower() == "vuoto":
        return []
    return _pulisci(testo, parola)


def _carica(conn):
    """Carica la tabella nella cache RAM, una volta sola."""
    if "ok" in _caricate:
        return
    with _lock:
        if "ok" in _caricate:
            return
        # tuple_row: non ereditare il dict_row del chiamante (main.py ce l'ha).
        with conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SELECT voce, termini FROM glossario")
            for voce, termini in cur.fetchall():
                _cache[voce] = list(termini)
        _caricate.add("ok")


def glossario_di(conn, parola: str) -> list:
    """I termini di una parola, dalla cache o dal modello (una volta sola)."""
    parola = parola.strip().lower()
    if not parola:
        return []
    _carica(conn)
    with _lock:
        if parola in _cache:
            return _cache[parola]
    termini = _genera(parola)
    with _lock:
        _cache[parola] = termini
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO glossario (voce, termini) VALUES (%s, %s)
               ON CONFLICT (voce) DO UPDATE SET termini = EXCLUDED.termini,
                 aggiornato_il = now()""",
            (parola, termini),
        )
    conn.commit()
    return termini


def glossario_noti(conn, parola: str) -> list:
    """I termini GIA' noti di una parola (tabella/cache), senza chiamare il
    modello. Serve all'espansione col corpus: se «sassi» porta «pietre» e il
    corpus ha gia' collegato «pietre»->«rocks, dekosteine», li aggiungiamo senza
    interrogare il modello una seconda volta."""
    parola = parola.strip().lower()
    if not parola:
        return []
    _carica(conn)
    with _lock:
        return list(_cache.get(parola, []))


def espandi(conn, termini: list) -> list:
    """`termini` piu' i termini del glossario (corpus) collegati a ognuno, un
    livello. I termini gia' arrivano dal modello (vincoli.estrae); qui si
    aggiungono SOLO quelli del corpus, che il modello non conosce: «pietre» ->
    «rocks, dekosteine». Niente chiamate al modello: pura lettura della tabella."""
    out = list(termini)
    for t in termini:
        for s in glossario_noti(conn, t):
            if s not in out:
                out.append(s)
    return out


def arricchisci(conn, domanda: str) -> str:
    """La domanda estesa coi termini multilingue delle parole di contenuto.

    Le parole si prendono cosi' come sono (minuscole, senza punteggiatura), e
    per ognuna si cercano i termini. La domanda originale resta in testa: i
    termini CHIUDONO il collegamento linguistico, non lo sostituiscono.

    Espansione di UN livello col corpus: «sassi» -> (modello) «pietre» ->
    (corpus) «rocks, dekosteine». Se il modello e' giu' o non risponde, torna la
    domanda com'era: la ricerca non peggiora mai rispetto a oggi.
    """
    if not domanda:
        return domanda
    import re
    parole = re.findall(r"[a-zàèéìòù]+", domanda.lower())
    aggiunte = []
    for p in parole:
        for s in glossario_di(conn, p):
            if s not in aggiunte and s not in parole:
                aggiunte.append(s)
            for s2 in glossario_noti(conn, s):
                if s2 not in aggiunte and s2 not in parole:
                    aggiunte.append(s2)
    if not aggiunte:
        return domanda
    return domanda + " " + " ".join(aggiunte[:MAX_GLOSSARIO * 2])


# Quanti termini AL MASSIMO per parola entrano nella query. Il prompt chiede
# «massimo 6», ma il 23/09/2026 il modello ha risposto con 30 parole su
# «candele» (wicks, wax, votives, beeswax, melt, bath salts...) e la query gonfia
# diluiva l'embedding e inquinava la ricerca. Il limite sta QUI, in codice:
# fidarsi del modello su un prompt e' il bug, non la richiesta.
MAX_GLOSSARIO = int(os.environ.get("GLOSSARIO_MAX", "6"))


def _pulisci(testo: str, parola: str) -> list:
    """Estrae i termini dalla risposta del modello (virgole, punti, minuscole,
    senza la parola stessa, senza doppioni, al massimo MAX_GLOSSARIO). Separata
    perche' e' l'unica regola verificabile senza modello ne' DB."""
    out = []
    for t in testo.replace(";", ",").split(","):
        t = t.strip().strip(".").lower()
        if t and t != parola and t not in out:
            out.append(t)
    return out[:MAX_GLOSSARIO]


# ---- Estrazione dal corpus --------------------------------------------------

# Il modello legge i nomi trilingue dei cataloghi e ne estrae voce -> termini.
# E' lui a distinguere l'oggetto dall'aggettivo e dal colore: qui non c'e'
# nessuna lista di parole da tenere o scartare.
ISTRUZIONI_ESTRAI = (
    "Sei un esperto di cataloghi di decorazioni e giardinaggio. Ti passo i nomi "
    "dei prodotti nelle lingue italiano/tedesco/inglese, separati da «|» (ogni "
    "riga e' lo stesso prodotto nelle tre lingue).\n"
    "Estrai il glossario dei termini multilingue. Per ogni OGGETTO o MATERIALE "
    "scrivi una riga con la PAROLA SEMPLICE che lo indica (il nome della cosa, "
    "non l'aggettivo che la descrive: «rocks», non «deco rocks»; «pebbles», non "
    "«river pebbles»), seguita dai suoi termini/traduzioni nelle altre lingue, "
    "anch'essi parole semplici.\n"
    "NON includere i colori, e NON gli aggettivi o i qualificatori che "
    "descrivono stile o materiale dell'oggetto: tieni solo il NOME della cosa.\n"
    "Formato: oggetto: termine1, termine2, ...\n"
    "Esempio: rocks: pietre, pierres, dekosteine\n"
    "Niente numeri, niente nomi di prodotto completi, niente spiegazioni.\n"
    "/no_think"
)


def _righe_corpus(conn) -> list:
    """Le righe «|» dei chunk: i nomi trilingue dei prodotti. Uniche e ordinate."""
    righe = set()
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT content FROM chunks")
        for (t,) in cur:
            for x in (t or "").splitlines():
                x = x.strip()
                if 10 <= len(x) <= 120 and x.count("|") >= 1:
                    righe.add(x)
    return sorted(righe)


def _lotti(righe: list, soglia: int = 8000) -> list:
    lotti, corrente, n = [], [], 0
    for r in righe:
        corrente.append(r)
        n += len(r)
        if n >= soglia:
            lotti.append(corrente)
            corrente, n = [], 0
    if corrente:
        lotti.append(corrente)
    return lotti


def _parsa_estrazione(testo: str) -> dict:
    out = {}
    for riga in testo.splitlines():
        riga = riga.strip().strip("`").strip()
        if not riga or riga.startswith(("#", "```")):
            continue
        sep = next((s for s in (": ", ":", "=", " -> ") if s in riga), None)
        if not sep:
            continue
        voce, _, valori = riga.partition(sep)
        voce = voce.strip().lower()
        if not voce:
            continue
        vals = {v.strip().lower() for v in valori.split(",") if v.strip()}
        if vals:
            out.setdefault(voce, set()).update(vals)
    return out


def _persisti(conn, glossario: dict):
    """Scrive il glossario in tabella, bidirezionale (ogni voce porta le altre)
    e in UNIONE coi termini gia' presenti: non sovrascrive la memoria del modello
    («sassi» resta), aggiunge quella del corpus («pietre» -> «rocks»)."""
    gruppi = {}
    for voce, termini in glossario.items():
        tutte = {voce} | set(termini)
        for v in tutte:
            gruppi.setdefault(v, set()).update(tutte - {v})
    with conn.cursor(row_factory=tuple_row) as cur:
        for voce, termini in gruppi.items():
            cur.execute("SELECT termini FROM glossario WHERE voce = %s", (voce,))
            riga = cur.fetchone()
            esistenti = set(riga[0]) if riga else set()
            tutti = list(termini | esistenti)[:MAX_GLOSSARIO]
            cur.execute(
                """INSERT INTO glossario (voce, termini) VALUES (%s, %s)
                   ON CONFLICT (voce) DO UPDATE SET termini = EXCLUDED.termini,
                     aggiornato_il = now()""",
                (voce, tutti),
            )
    conn.commit()


def estrai_glossario(conn, soglia: int = 8000) -> int:
    """Costruisce il glossario dal CORPUS con il modello. I nomi trilingue dei
    cataloghi («pebbles | galets | ciottoli») passano al modello a lotti; lui ne
    estrae voce -> termini capendo da se' cos'e' oggetto e cos'e' aggettivo. Il
    codice si limita a passargli i dati e a persistere il risultato. Idempotente:
    si puo' rilanciare a ogni cambio del corpus."""
    righe = _righe_corpus(conn)
    if not righe:
        return 0
    glossario = {}
    for lotto in _lotti(righe, soglia):
        try:
            testo = _chiedi([
                {"role": "system", "content": ISTRUZIONI_ESTRAI},
                {"role": "user", "content": "\n".join(lotto)},
            ])
        except Exception:
            continue
        for voce, vals in _parsa_estrazione(testo).items():
            glossario.setdefault(voce, set()).update(vals)
    _persisti(conn, glossario)
    return len(glossario)


def _prova():
    assert _pulisci("pietre, ciottoli, Pebbles., pietre", "sassi") == \
        ["pietre", "ciottoli", "pebbles"]
    # «VUOTO» non passa da _pulisci: lo intercetta _genera prima.
    assert _pulisci("", "quanto") == []
    assert _pulisci("sassi, sassi", "sassi") == []
    # Il limite duro: il modello non puo' gonfiare la query oltre MAX_GLOSSARIO.
    lunghi = ",".join(f"s{i}" for i in range(40))
    assert len(_pulisci(lunghi, "x")) == MAX_GLOSSARIO
    # Parsing dell'estrazione: una riga valida, una riga da scartare.
    assert _parsa_estrazione("rocks: pietre, pierres\nciao mondo\n") == \
        {"rocks": {"pietre", "pierres"}}
    print("glossario: regole locali ok")


if __name__ == "__main__":
    _prova()
