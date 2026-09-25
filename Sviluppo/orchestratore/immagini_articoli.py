"""Immagini degli ARTICOLI trovati, non della domanda (D23).

Vedi Sviluppo/TOOL-IMMAGINI.md. In breve: oggi le figure si cercano per
somiglianza vettoriale alla DOMANDA, e su «profumatori per auto» escono figure
fuori tema pur dentro la pagina giusta. L'ordine giusto e' inverso: prima si
trovano gli articoli (i chunk gia' recuperati), poi le LORO figure.

Due livelli:
  1. match deterministico per CODICE: l'immagine della stessa pagina la cui
     descrizione contiene il codice dell'articolo. Nessun modello.
  2. verifica LLM, UNA chiamata sola per tutti gli articoli rimasti senza
     immagine: dato l'articolo (descrizione testuale) e le figure candidate
     della stessa pagina, scegli quale lo raffigura, o nessuna.

I permessi non si toccano: le candidate vengono dalle pagine dei chunk gia'
ammessi (quindi ACL gia' filtrate), e il serving ricontrolla come sempre.
"""
import json
import os
import re

from psycopg.rows import dict_row

from orchestratore import egress, modello

LITELLM = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000").rstrip("/")
MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
ROTTA = os.environ.get("LLM_RAGIONAMENTO", "ragionamento")
SECONDI = float(os.environ.get("IMMAGINI_TIMEOUT", "30"))

# I codici articolo visti nei cataloghi hanno forme diverse: FLK2004, FSA1040,
# DST2040 (lettere+numeri), 700 002 156 (tre gruppi di cifre), P1039/K0300
# (lettera+numeri), B41G1 (lettera+cifre+lettera+cifra). Un solo pattern non li
# prende tutti e un pattern troppo largo diventa falsi positivi: il match per
# codice e' un ACCELERATORE, non la garanzia. La verifica LLM copre il resto.
_RE_CODICI = re.compile(
    r"\b(?:[A-Z]{2,}[0-9]{2,}[A-Z0-9]*|[0-9]{3} [0-9]{3} [0-9]{3}|"
    r"[A-Z][0-9]{2,}[A-Z]?[0-9]?)\b"
)

# Quanto del testo dell'articolo e della descrizione dell'immagine si mostra al
# modello nella verifica: basta a riconoscere «questo e' il FLK2004», non serve
# la tabella intera.
CARATTERI = 200

ISTRUZIONI = (
    "Ti do un elenco di articoli e, per ognuno, alcune immagini candidate con "
    "la loro descrizione.\n"
    "Per ogni articolo scegli l'immagine che lo raffigura. Rispondi con una "
    "riga per articolo nel formato «N:LETTERA», dove N e' il numero "
    "dell'articolo e LETTERA e' la lettera dell'immagine scelta. Se nessuna "
    "immagine corrisponde a un articolo, rispondi «N:-».\n"
    "Non inventare corrispondenze: se non sei sicuro, metti «-».\n"
    "Rispondi solo con le righe, niente spiegazioni.\n"
    "/no_think"
)


def _chiedi(messaggi):
    return modello.chiedi(messaggi)


def _codici(testo) -> set:
    return {m.group(0).replace(" ", "") for m in _RE_CODICI.finditer(testo or "")}


def _immagine_per_codice(conn, riga, codici):
    """L'immagine della stessa pagina la cui descrizione contiene uno dei
    codici dell'articolo. La prima, se ce n'e' piu' d'una (il codice dovrebbe
    comparire in una sola descrizione: la sua)."""
    if not codici or not riga.get("page"):
        return None
    pattern = "|".join(re.escape(c) for c in codici)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT i.id, i.source_id, i.documento, i.page, i.descrizione
               FROM immagini i
               WHERE i.source_id = %s AND i.documento = %s
                 AND i.page IS NOT DISTINCT FROM %s
                 AND i.descrizione ~ %s
               ORDER BY i.id LIMIT 1""",
            (riga["source_id"], riga["documento"], riga["page"], pattern),
        )
        r = cur.fetchone()
        return r if r else None


def _candidate(conn, riga):
    """Le immagini della stessa pagina dell'articolo, per la verifica LLM."""
    if not riga.get("page"):
        return []
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT i.id, i.descrizione
               FROM immagini i
               WHERE i.source_id = %s AND i.documento = %s
                 AND i.page IS NOT DISTINCT FROM %s
                 AND i.descrizione IS NOT NULL
               ORDER BY i.id""",
            (riga["source_id"], riga["documento"], riga["page"]),
        )
        return cur.fetchall()


def _verifica(conn, senza):
    """Una chiamata sola: assegna a ogni articolo senza immagine la figura che
    lo raffigura, fra le candidate della sua pagina. Torna la lista di
    (id_immagine, source_id, documento, page, descrizione) assegnati."""
    articoli, mappa = [], {}
    for riga, candidate in senza:
        articoli.append((riga, candidate))
    testo = []
    for n, (riga, candidate) in enumerate(articoli, 1):
        art = " ".join((riga.get("content") or "").split())[:CARATTERI]
        testo.append(f"Articolo {n}: {art}")
        if not candidate:
            testo.append(f"  (nessuna immagine)")
            continue
        for l, cand in enumerate(candidate, 1):
            lettera = chr(64 + l)  # A, B, C...
            d = " ".join((cand.get("descrizione") or "").split())[:CARATTERI]
            testo.append(f"  {lettera}: {d}")
            mappa[(n, lettera)] = (cand["id"], riga)
    try:
        risposta = _chiedi([{"role": "system", "content": ISTRUZIONI},
                            {"role": "user", "content": "\n".join(testo)}])
    except Exception:
        return []
    assegnati = []
    for riga in risposta.splitlines():
        m = re.match(r"\s*(\d+)\s*[:：]\s*([A-Za-z-])\s*", riga.strip())
        if not m:
            continue
        n = int(m.group(1))
        lettera = m.group(2).upper()
        if lettera == "-" or (n, lettera) not in mappa:
            continue
        iid, riga = mappa[(n, lettera)]
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT i.id, i.source_id, i.documento, i.page, i.descrizione
                   FROM immagini i WHERE i.id = %s""", (iid,))
            r = cur.fetchone()
        if r:
            assegnati.append(r)
    return assegnati


def per_articoli(conn, righe, gruppi, limite):
    """Le immagini degli articoli trovati: (id, descrizione, riga) per ognuna.

    `righe` sono i chunk gia' recuperati (gli articoli), in ordine di pertinenza.
    `gruppi` e' inutilizzato qui ma resta nella firma per il contratto: le ACL
    sono gia' filtrate a monte (le righe arrivano ammesse) e si ricontrollano al
    serving. Ritorna al massimo `limite` figure, una per articolo, senza
    doppioni di articolo.
    """
    risultato, visti_articolo = [], set()
    senza = []
    for r in righe:
        key = (r.get("documento"), r.get("page"))
        img = _immagine_per_codice(conn, r, _codici(r.get("content")))
        if img:
            if key not in visti_articolo:
                risultato.append(img)
                visti_articolo.add(key)
        else:
            senza.append((r, _candidate(conn, r)))

    if senza and len(risultato) < limite:
        for img in _verifica(conn, senza):
            key = (img["documento"], img["page"])
            if key not in visti_articolo and len(risultato) < limite:
                risultato.append(img)
                visti_articolo.add(key)

    return risultato[:limite]


def _prova():
    # Il parsing dei codici, verificabile senza modello ne' DB.
    assert "FLK2004" in _codici("FLUSS KIESSEL FLK2004 dunkelrot dark red")
    assert "700002156" in _codici("etichette 700 002 156 nero black")
    assert "P1039" in _codici("diffusore P1039 500 ml")
    assert "B41G1" in _codici("decorazione B41G1 500 g")
    assert _codici("un vaso di terracotta") == set()
    print("immagini_articoli: regole locali ok")


if __name__ == "__main__":
    _prova()
