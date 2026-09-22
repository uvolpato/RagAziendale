"""Quanto DISCRIMINA la distanza, con la domanda lunga e con quella corta?

    D=/tmp/val-$(date +%s)
    docker compose cp valutazione orchestratore:$D
    docker compose exec -T orchestratore python $D/banda.py

Il 22/09/2026 il recupero passa da 18/20 a 14/20 quando la domanda e' quella
che una persona scrive davvero. Il sospetto: i pezzi sono lunghi e mescolati
(titolo + tabella + descrizioni delle figure), e contro una domanda di tre
parole si somigliano tutti. Se e' cosi', la distanza fra il primo pezzo e il
cinquantesimo si STRINGE quando la domanda si accorcia: l'ordine smette di
dire qualcosa e diventa rumore.

Non serve a decidere niente da solo. Serve a sapere se il problema e' la
GRANULARITA' dei pezzi o qualcos'altro, prima di rifare 36 minuti di indice.
"""
import json
import os
import pathlib
import sys

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, "/app")
from orchestratore import recupero      # noqa: E402

QUI = pathlib.Path(__file__).parent


def banda(conn, testo):
    """(distanza del 1o, del 10o, del 50o, parole del 1o)."""
    q = recupero.embedding(testo)
    r = conn.execute(
        """SELECT embedding <=> %s::vector AS d, content FROM chunks
           WHERE embedding IS NOT NULL ORDER BY d LIMIT 50""", (q,)).fetchall()
    return r[0]["d"], r[9]["d"], r[-1]["d"], len(r[0]["content"].split())


def main():
    dati = json.loads((QUI / "domande-eurosand.json").read_text(encoding="utf-8"))
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    print(f"{'id':>3}  {'parole':>6}  {'1o':>6} {'10o':>6} {'50o':>6}  {'banda':>6}  domanda")
    print("-" * 78)
    tot = {"lunga": [], "corta": []}
    for d in dati["domande"]:
        if not d.get("corta"):
            continue
        for quale, testo in (("lunga", d["domanda"]), ("corta", d["corta"])):
            p, dieci, c, parole_pezzo = banda(conn, testo)
            tot[quale].append(c - p)
            print(f"{d['id']:>3}  {len(testo.split()):>6}  {p:.4f} {dieci:.4f} {c:.4f}  "
                  f"{c - p:.4f}  {quale}")
    print("-" * 78)
    for quale, v in tot.items():
        print(f"banda media (50o - 1o), domanda {quale}: {sum(v)/len(v):.4f}")
    print("\nPiu' la banda e' STRETTA, meno l'ordine dei primi 50 significa qualcosa.")


if __name__ == "__main__":
    main()
