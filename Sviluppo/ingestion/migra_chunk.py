"""Migrazione §14: ri-chunk dei pezzi di catalogo.

Un codice per pezzo, col titolo del prodotto davanti. NIENTE Docling, NIENTE
VLM: solo spezzamento del testo gia' estratto + ri-embedding.

    docker compose run --rm -T -v "${PWD}:/app" ingestion python migra_chunk.py [--applica]

Senza --applica fa solo il conteggio (dry-run).
"""
import hashlib
import os
import re
import sys

import psycopg

sys.path.insert(0, "/app")
import indicizza as ix

CODICE = re.compile(r"\b[A-Z]{2,5}\d{3,}\b")
TITOLO = re.compile(r"deco rocks|pierres d.coratives|pietre decorative|dekosteine|steine", re.I)


def titolo_di(chunks):
    """Il titolo del prodotto in questa pagina, o None."""
    for _cid, content in chunks:
        if TITOLO.search(content):
            t = " ".join(content.split())
            # taglia ai dettagli di packaging ("NACHHALTIGE"): resta solo il nome
            t = re.split(r"\s+NACHHALTIGE", t, flags=re.I)[0]
            return t
    return None


def main():
    applica = "--applica" in sys.argv
    conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    docs = conn.execute("SELECT source_id, documento FROM documenti WHERE tipo='catalogo'").fetchall()
    tot_split = tot_nuovi = 0
    for source_id, documento in docs:
        righe = conn.execute(
            "SELECT id, page, content FROM chunks WHERE source_id=%s AND documento=%s",
            (source_id, documento)).fetchall()
        per_pagina = {}
        for cid, page, content in righe:
            per_pagina.setdefault(page, []).append((cid, content))
        for page, chunks in per_pagina.items():
            titolo = titolo_di(chunks)
            if not titolo:
                continue
            for cid, content in chunks:
                codici = CODICE.findall(content)
                if len(codici) < 3:
                    continue
                pezzi = re.split(r"(?=\b[A-Z]{2,5}\d{3,}\b)", content)
                nuovi = []
                for p in pezzi:
                    p = " ".join(p.split())
                    if p and CODICE.search(p):
                        nuovi.append(f"{titolo} | {p}")
                if not nuovi:
                    continue
                tot_split += 1
                tot_nuovi += len(nuovi)
                if applica:
                    conn.execute("DELETE FROM chunks WHERE id=%s", (cid,))
                    vett = ix.vettori(nuovi) if nuovi else None
                    for i, t in enumerate(nuovi):
                        v = ix.vettore_sql(vett[i]) if vett else None
                        conn.execute(
                            "INSERT INTO chunks (source_id, documento, page, content, content_hash, embedding, lettore)"
                            " VALUES (%s,%s,%s,%s,%s,%s::vector,%s)"
                            " ON CONFLICT (source_id, documento, page, content_hash) DO NOTHING",
                            (source_id, documento, page, t,
                             hashlib.sha256(t.encode()).hexdigest(), v, "docling"))
        print(f"  {documento}: {'applicato' if applica else 'dry-run'}")
    print(f"totale: {tot_split} chunk spezzati in {tot_nuovi} pezzi {'(applicato)' if applica else '(dry-run)'}")
    conn.close()


if __name__ == "__main__":
    main()
