"""Chi butta fuori il pezzo giusto quando la domanda e' corta?

    D=/tmp/val-$(date +%s)
    docker compose cp valutazione orchestratore:$D
    docker compose exec -T orchestratore python $D/ablazione.py

Il 22/09/2026 il recupero passa da 18/20 (domanda lunga) a 14/20 (domanda
corta come la scrive una persona). La diagnosi mostrava un indizio: per la
domanda 1 il pezzo giusto sta in posizione 5 col SOLO vettore, cioe' dentro
gli otto — eppure la catena completa lo perde. Allora non e' la
rappresentazione: e' qualcosa che viene DOPO.

Tre colonne, ognuna toglie un pezzo di catena:

    vettore   solo la ricerca vettoriale, primi 8
    fusione   vettore + testo esatto (RRF), primi 8, senza rerank
    completa  come oggi, col cross-encoder sui primi 150

Se «vettore» batte «completa» sulle corte, il colpevole ha un nome.
"""
import json
import os
import pathlib
import sys
import unicodedata

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, "/app")
from orchestratore import recupero      # noqa: E402

QUI = pathlib.Path(__file__).parent
K = 8


def piatto(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def colpita(pezzi, riscontri, documento, indice):
    attesi = [piatto(x) for x in riscontri]
    for i, p in enumerate(pezzi, 1):
        if p.get("documento") != documento or p.get("page") in indice:
            continue
        if any(a in piatto(p.get("content", "")) for a in attesi):
            return i
    return None


def solo_vettore(conn, testo, documento, indice):
    q = recupero.embedding(testo)
    return [dict(r) for r in conn.execute(
        """SELECT documento, page, content FROM chunks
           WHERE embedding IS NOT NULL ORDER BY embedding <=> %s::vector LIMIT %s""",
        (q, K)).fetchall()]


def main():
    dati = json.loads((QUI / "domande-eurosand.json").read_text(encoding="utf-8"))
    gruppi, documento = dati["gruppi"], dati["documento"]
    indice = dati.get("pagine_indice", [])
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    vero = recupero.RERANK_URL

    print(f"{'id':>3}  {'vett':>5} {'fus':>5} {'compl':>5}   domanda corta")
    print("-" * 66)
    conta = {"vettore": 0, "fusione": 0, "completa": 0}
    for d in dati["domande"]:
        if not d.get("corta"):
            continue
        testo, ris = d["corta"], d["riscontro"]
        pos = {}
        pos["vettore"] = colpita(solo_vettore(conn, testo, documento, indice), ris, documento, indice)
        recupero.RERANK_URL = ""
        p, _ = recupero.cerca(conn, testo, gruppi, recupero.embedding(testo), limite=K)
        pos["fusione"] = colpita(p, ris, documento, indice)
        recupero.RERANK_URL = vero
        p, _ = recupero.cerca(conn, testo, gruppi, recupero.embedding(testo), limite=K)
        pos["completa"] = colpita(p, ris, documento, indice)
        for k, v in pos.items():
            conta[k] += 1 if v else 0
        segno = lambda v: (f"#{v}" if v else "no")      # noqa: E731
        print(f"{d['id']:>3}  {segno(pos['vettore']):>5} {segno(pos['fusione']):>5} "
              f"{segno(pos['completa']):>5}   {testo[:38]}")
    print("-" * 66)
    n = sum(1 for d in dati["domande"] if d.get("corta"))
    for k, v in conta.items():
        print(f"  {k:9} {v}/{n}")


if __name__ == "__main__":
    main()
