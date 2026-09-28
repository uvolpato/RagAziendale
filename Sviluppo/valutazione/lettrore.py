"""Il lettore delle pagine al VLM serve, o Docling basta?

    docker compose cp valutazione orchestratore:/tmp/val
    docker compose exec -T orchestratore python /tmp/val/lettrore.py acquisti-decobrands
    docker compose exec -T orchestratore python /tmp/val/lettrore.py test-decobrands

Lo STESSO documento (EUROSAND 2024, 107 pagine) indicizzato due volte: una dalla
fonte che e' in LETTURA_PAGINA e lo legge il VLM pagina per pagina, una dalla
fonte di test che lo legge solo con Docling. Sulle stesse 24 domande vere si
confronta la posizione del pezzo giusto.

La fonte si isola cambiandole le ACL, non il codice: `cerca()` filtra per
`acl_groups`/`aziende`, quindi la catena che gira e' letteralmente quella di
produzione, con una riga di permessi in meno. Il resto (RRF, cross-encoder,
soglia di punteggio) non lo si tocca.

Se Docling da solo arriva dove arriva tutto, il passaggio al VLM si toglie: sono
107 chiamate in più per ogni catalogo.
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
GRUPPI = ["magazzino"]


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


def main():
    fonte = sys.argv[1]
    dati = json.loads((QUI / "domande-eurosand.json").read_text(encoding="utf-8"))
    gruppo, documento = dati["gruppi"], dati["documento"]
    indice = dati.get("pagine_indice", [])
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    pezzi = conn.execute("""SELECT count(*) AS n, count(embedding) AS vettori
                            FROM chunks WHERE source_id = %s AND documento = %s""",
                         (fonte, documento)).fetchone()
    if not pezzi["n"]:
        raise SystemExit(f"{fonte}: nessun pezzo per {documento}")

    print(f"{fonte}: {pezzi['n']} pezzi ({pezzi['vettori']} con vettore)\n")
    print(f"{'id':>3}  {'pos':>4}  {'pagine':>6}   domanda corta")
    print("-" * 70)
    trovati = 0
    for d in dati["domande"]:
        if not d.get("corta"):
            continue
        p, _ = recupero.cerca(conn, d["corta"], gruppo, recupero.embedding(d["corta"]), limite=K)
        pos = colpita(p, d["riscontro"], documento, indice)
        trovati += 1 if pos else 0
        segno = f"#{pos}" if pos else "no"
        print(f"{d['id']:>3}  {segno:>4}  {len(p):>6}   {d['corta'][:38]}")
    print("-" * 70)
    n = sum(1 for d in dati["domande"] if d.get("corta"))
    print(f"  pezzo giusto nei primi {K}: {trovati}/{n}")


if __name__ == "__main__":
    main()
