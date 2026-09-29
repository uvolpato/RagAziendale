"""Migrazione: riporta le descrizioni delle figure nel TESTO (titolo + descrizione).

«sassi rossi» non combacia con nessun codice, ma la figura del prodotto ha il
titolo della pagina («DEKOSTEINE pietre decorative») e il colore («red»). Senza
questi pezzi il colore dei cataloghi sparisce dall'indice. NIENTE Docling/VLM:
legge il markdown gia' su disco e le descrizioni gia' in DB.

    docker compose run --rm -T -v "${PWD}:/app" ingestion python migra_figure.py [--applica]
"""
import hashlib
import os
import sys

import psycopg

sys.path.insert(0, "/app")
import indicizza as ix


def main():
    applica = "--applica" in sys.argv
    conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    docs = conn.execute(
        "SELECT s.percorso, d.source_id, d.documento FROM documenti d"
        " JOIN sources s ON s.id=d.source_id WHERE d.tipo='catalogo'").fetchall()
    totale = 0
    for percorso, source_id, documento in docs:
        dentro = ix._cartella_sorgenti(percorso, documento)
        titoli = {}
        md_dir = ix.RADICE / dentro / "markdown-docling"
        if md_dir.is_dir():
            for f in md_dir.glob("*.md"):
                try:
                    n = int(f.stem)
                except ValueError:
                    continue
                t = ix._titolo_da_markdown(f.read_text(encoding="utf-8"))
                if t:
                    titoli[n] = t
        figure = conn.execute(
            "SELECT page, descrizione FROM immagini WHERE source_id=%s AND documento=%s"
            " AND descrizione IS NOT NULL AND (verdetto IS NULL OR verdetto <> 'corredo')",
            (source_id, documento)).fetchall()
        nuovi = []
        for page, descr in figure:
            titolo = titoli.get(page, "")
            testo = " ".join((descr or "").split())
            chunk = (f"{titolo} | Immagine: {testo}" if titolo else f"Immagine: {testo}")
            nuovi.append((page, chunk))
        totale += len(nuovi)
        if applica and nuovi:
            vett = ix.vettori([c for _, c in nuovi])
            for i, (page, chunk) in enumerate(nuovi):
                v = ix.vettore_sql(vett[i]) if vett else None
                conn.execute(
                    "INSERT INTO chunks (source_id, documento, page, content, content_hash, embedding, lettore)"
                    " VALUES (%s,%s,%s,%s,%s,%s::vector,%s)"
                    " ON CONFLICT (source_id, documento, page, content_hash) DO NOTHING",
                    (source_id, documento, page, chunk,
                     hashlib.sha256(chunk.encode()).hexdigest(), v, "docling"))
        print(f"  {documento}: {len(nuovi)} figure {'(applicato)' if applica else '(dry-run)'}")
    print(f"totale: {totale} chunk figura {'(applicato)' if applica else '(dry-run)'}")
    conn.close()


if __name__ == "__main__":
    main()
