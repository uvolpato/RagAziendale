"""Chiedere un ARTICOLO PER CODICE: la catena lo trova?

    D=/tmp/val-$(date +%s)
    docker compose cp valutazione orchestratore:$D
    docker compose exec -T orchestratore python $D/codici.py

Le 24 domande d'oro non contengono nessun codice articolo, ed e' giusto: chi
compra non li sa a memoria. Ma chi li sa — l'ufficio acquisti con l'ordine
aperto davanti — li scrive, e quel caso non era misurato da niente.

E' anche l'unico caso per cui esiste il ramo lessicale della ricerca: il
vettoriale sui codici non puo' funzionare, «DST2040» e «DST2043» sono due
prodotti diversi e due punti vicinissimi.

Le domande non le scrivo io: si PESCANO dall'indice. Si cerca nel testo
qualcosa che somigli a un codice articolo, si tengono quelli che compaiono in
pochissimi pezzi (un codice e' raro per definizione: se sta in cento pezzi non
e' un codice, e' una parola) e si costruisce la domanda intorno. Cosi' il
metro non e' tarato su un catalogo: cambia da solo quando cambia l'archivio.

Tre forme, perche' le tre si comportano in modo diverso:

    nudo      «DST2040»
    frase     «quanto costa il DST2040»
    naturale  «avete ancora disponibile il DST2040?»

La prima funzionava gia'. Le altre due sono quelle che l'AND di
`plainto_tsquery` mandava a vuoto: pretendendo TUTTE le parole nello stesso
pezzo, «quanto costa il DST2040» non trovava niente.
"""
import os
import re
import sys

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, "/app")
from orchestratore import recupero      # noqa: E402

K = 8
QUANTI = 12
GRUPPI = ["acquisti", "azienda-decobrands"]
# Un codice articolo: lettere e cifre attaccate, almeno una cifra, non una
# parola. Non e' il formato di un catalogo particolare — e' la forma che hanno
# quasi tutti, e quello che non lo e' viene scartato dal filtro sulla rarita'.
CODICE = re.compile(r"\b(?=[A-Z0-9-]*[0-9])(?=[A-Z0-9-]*[A-Z])[A-Z][A-Z0-9-]{4,11}\b")

FORME = [
    ("nudo", "{c}"),
    ("frase", "quanto costa il {c}"),
    ("naturale", "avete ancora disponibile il {c}?"),
]


def codici(conn):
    """Codici pescati dall'indice, dal piu' raro in su, uno per documento."""
    # Solo i documenti che i GRUPPI di questa misura possono vedere: pescare
    # un codice da una fonte non consentita misurerebbe l ACL, non la ricerca
    # (successo il 22/09/2026: quattro no su dodici erano documenti di
    # un altra fonte, e sembravano fallimenti del recupero).
    visibili = {d["documento"] for d in recupero.documenti_visibili(conn, GRUPPI)}
    righe = [r for r in conn.execute(
        """SELECT documento, content FROM chunks
            WHERE documento IS NOT NULL ORDER BY id""").fetchall()
             if r["documento"] in visibili]
    dove = {}
    for r in righe:
        for c in set(CODICE.findall(r["content"])):
            dove.setdefault(c, set()).add(r["documento"])
    # Raro = codice. Se compare in molti pezzi e' un'intestazione o una sigla
    # di colonna, non un articolo.
    conteggio = {}
    for r in righe:
        for c in set(CODICE.findall(r["content"])):
            conteggio[c] = conteggio.get(c, 0) + 1
    buoni = [c for c, n in conteggio.items() if 1 <= n <= 3]
    buoni.sort(key=lambda c: (conteggio[c], c))
    # Uno per documento finche' si puo': un metro tutto su un catalogo solo
    # misurerebbe quel catalogo.
    visti, fuori = set(), []
    for c in buoni:
        d = sorted(dove[c])[0]
        if d in visti and len(visti) < len(set(dove[c] | visti)):
            continue
        visti.add(d)
        fuori.append((c, d))
        if len(fuori) >= QUANTI:
            break
    return fuori


def trovato(conn, testo, codice):
    """Posizione del primo pezzo che contiene il codice, fra i primi K."""
    righe, _ = recupero.cerca(conn, testo, GRUPPI, recupero.embedding(testo), limite=K)
    for i, r in enumerate(righe, 1):
        if codice.lower() in (r.get("content") or "").lower():
            return i
    return None


def main():
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    elenco = codici(conn)
    if not elenco:
        print("nessun codice pescato dall'indice: il metro non si applica qui")
        return
    print(f"{len(elenco)} codici pescati dall'indice · primi {K} pezzi\n")
    print(f"{'codice':>12}  " + "  ".join(f"{n:>8}" for n, _ in FORME) + "   documento")
    print("-" * 78)
    conta = {n: 0 for n, _ in FORME}
    for c, doc in elenco:
        pos = {}
        for nome, forma in FORME:
            pos[nome] = trovato(conn, forma.format(c=c), c)
            conta[nome] += 1 if pos[nome] else 0
        segno = lambda v: (f"#{v}" if v else "no")      # noqa: E731
        print(f"{c:>12}  " + "  ".join(f"{segno(pos[n]):>8}" for n, _ in FORME)
              + f"   {doc[:28]}")
    print("-" * 78)
    n = len(elenco)
    for nome, _ in FORME:
        print(f"  {nome:9} {conta[nome]}/{n} ({conta[nome]/n:.0%})")


if __name__ == "__main__":
    main()
