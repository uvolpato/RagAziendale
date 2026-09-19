"""Indicizzazione delle cartelle collegate (decisioni 61-64).

    python indicizza.py              # un giro ogni INTERVALLO secondi, per sempre
    python indicizza.py --una-volta  # un giro solo (prove, verifiche)

Per ogni fonte con provenienza 'cartella' (attiva o in attesa: si indicizza
prima di attivare, cosi' chi approva vede cosa entra) legge la cartella
/cartelle/<percorso> e allinea l'indice:
  - file nuovo o cambiato   -> Docling, pezzi, vettori, in chunks
  - file uguale             -> niente (dimensione e data, poi impronta)
  - file cancellato         -> via dall'indice
  - file illeggibile        -> anomalia sul pannello, gli altri proseguono
  - stesso file due volte   -> anomalia "doppione": due copie = due citazioni
Le cartelle che iniziano con '_' (_bozze, _archivio) non si leggono, e
nemmeno i fogli di calcolo: i prezzi vengono dal gestionale (decisione 64).

I vettori si chiedono a LiteLLM per nome logico (`embedding`), come fa
l'orchestratore. Se non risponde i pezzi entrano senza vettore: la ricerca
testuale li trova gia', i vettori si aggiungono al giro successivo.

Le ACL NON stanno qui: stanno in sources, e la ricerca le applica nella query
(orchestratore/recupero.py). Questo servizio non decide chi vede cosa.

ponytail: un giro a intervallo fisso, non un file-watcher ne' Dagster; basta
per cartelle che cambiano poche volte al giorno. Dagster quando servira'
l'"indicizza ora" dal pannello.
"""
import gc
import hashlib
import json
import os
import pathlib
import re
import sys
import time
from datetime import datetime, timezone

import httpx
import psycopg

RADICE = pathlib.Path(os.environ.get("CARTELLE", "/cartelle"))
INTERVALLO = int(os.environ.get("INTERVALLO", "300"))
LOTTO_VETTORI = 16
DOCLING = {".pdf", ".docx", ".pptx", ".html", ".htm", ".md", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
TESTO = {".txt"}
# Decisione 64: i prezzi vengono dal gestionale, un foglio indicizzato come
# testo e' il modo piu' rapido per sbagliare un preventivo.
ESCLUSI = {".xlsx", ".xls", ".xlsm", ".csv", ".ods"}
PERCORSO_VALIDO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/-]*$")   # relativo: niente UNC, lettere di unita', '..'
MIN_PEZZO, MAX_PEZZO = 300, 1800


# ------------------------------------------------------------- file e pezzi
def file_da_leggere(cartella: pathlib.Path):
    """(percorso relativo, Path) dei file da indicizzare, in ordine stabile.
    Salta cartelle e file nascosti o con '_' iniziale, i temporanei di Office
    ('~$') e le estensioni non supportate o escluse."""
    out = []
    for dirpath, dirnames, filenames in os.walk(cartella):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(("_", ".")))
        for f in sorted(filenames):
            if f.startswith(("_", ".", "~$")):
                continue
            est = pathlib.Path(f).suffix.lower()
            if est in DOCLING or est in TESTO:
                p = pathlib.Path(dirpath) / f
                out.append((p.relative_to(cartella).as_posix(), p))
    return out


def unisci(pezzi):
    """[(testo, pagina)] -> pezzi di dimensione utile per la ricerca.
    Docling spezza per elemento (un titolo, una riga di tabella): da soli non
    rispondono a nulla. Si uniscono i consecutivi della stessa pagina fino a
    MAX_PEZZO; un pezzo troppo lungo si taglia ai capoversi."""
    out = []
    for testo, pagina in pezzi:
        testo = testo.strip()
        if not testo:
            continue
        if out and out[-1][1] == pagina and len(out[-1][0]) < MIN_PEZZO and len(out[-1][0]) + len(testo) <= MAX_PEZZO:
            out[-1] = (out[-1][0] + "\n" + testo, pagina)
        else:
            out.append((testo, pagina))
    finali = []
    for testo, pagina in out:
        while len(testo) > MAX_PEZZO:
            taglio = testo.rfind("\n", 0, MAX_PEZZO)
            taglio = taglio if taglio > MIN_PEZZO else MAX_PEZZO
            finali.append((testo[:taglio].strip(), pagina))
            testo = testo[taglio:]
        if testo.strip():
            finali.append((testo.strip(), pagina))
    return finali


class Lettore:
    """Docling, caricato solo se c'e' davvero un file da leggere: i modelli
    occupano memoria, e quasi tutti i giri non trovano niente di nuovo."""

    def __init__(self):
        self._conv = self._chunker = None

    def _carica(self):
        from docling.chunking import HierarchicalChunker
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
        from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
        # OCR con Tesseract (pacchetto Debian, italiano e inglese): funziona
        # senza rete, gli altri motori scaricano modelli da server esterni.
        opzioni = PdfPipelineOptions(do_ocr=True, do_table_structure=True,
                                     ocr_options=TesseractCliOcrOptions(lang=["ita", "eng"]),
                                     artifacts_path=os.environ.get("DOCLING_MODELLI") or None)
        self._conv = DocumentConverter(format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=opzioni),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=opzioni)})
        self._chunker = HierarchicalChunker()

    def pezzi(self, p: pathlib.Path):
        if p.suffix.lower() in TESTO:
            testo = p.read_text(encoding="utf-8", errors="replace")
            return unisci([(x, None) for x in re.split(r"\n\s*\n", testo)])
        if self._conv is None:
            self._carica()
        doc = self._conv.convert(str(p)).document
        grezzi = []
        for ch in self._chunker.chunk(doc):
            pagina = None
            for item in ch.meta.doc_items:
                if item.prov:
                    pagina = item.prov[0].page_no
                    break
            titoli = " > ".join(ch.meta.headings or [])
            grezzi.append(((titoli + "\n" if titoli else "") + ch.text, pagina))
        return unisci(grezzi)


# ------------------------------------------------------------------ vettori
def _litellm():
    """LiteLLM, come l'orchestratore: si chiede il nome LOGICO (`embedding`),
    il modello vero lo decide litellm-config.yaml. Un posto solo da cambiare."""
    return (os.environ.get("LITELLM_BASE_URL", "http://litellm:4000").rstrip("/"),
            {"Authorization": "Bearer " + os.environ.get("LITELLM_MASTER_KEY", "")},
            os.environ.get("LLM_EMBEDDING", "embedding"))


def modello_vero():
    """Il modello dietro il nome logico, da /model/info di LiteLLM (es.
    'text-embedding-bge-m3-embeddings'): e' questo che l'indice registra, non
    il nome logico, che resta uguale anche quando il modello cambia."""
    url, testa, logico = _litellm()
    r = httpx.get(f"{url}/model/info", headers=testa, timeout=30)
    r.raise_for_status()
    for m in r.json()["data"]:
        if m["model_name"] == logico:
            return m["litellm_params"]["model"].split("/", 1)[-1]
    raise RuntimeError(f"LiteLLM non ha il modello logico {logico!r}")


def vettori(testi):
    """Vettori via LiteLLM, o None se non risponde (LM Studio spento, ecc.)."""
    url, testa, logico = _litellm()
    try:
        out = []
        for i in range(0, len(testi), LOTTO_VETTORI):
            r = httpx.post(f"{url}/v1/embeddings", json={"model": logico, "input": testi[i:i + LOTTO_VETTORI]},
                           headers=testa, timeout=120)
            r.raise_for_status()
            out += [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]
        return out
    except Exception as e:
        print(f"  vettori non disponibili ({type(e).__name__}): i pezzi entrano senza, si riprova al giro dopo")
        return None


def vettore_sql(v):
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


def controlla_modello(conn, esempio):
    """Un indice, un modello: vettori di modelli diversi non si confrontano, e
    il recall cala senza nessun errore (001, index_meta). Se il modello e'
    cambiato si smette di scrivere vettori e lo si dice."""
    try:
        modello = modello_vero()
    except Exception as e:
        print(f"  modello dei vettori non verificabile ({type(e).__name__}): niente vettori in questo giro")
        return False
    meta = conn.execute("SELECT embedding_model, dimensioni FROM index_meta WHERE id = 1").fetchone()
    if meta is None:
        canarino = vettori(["Il canarino nella miniera di carbone."])
        if not canarino:
            return False
        impronta = hashlib.sha256(json.dumps([round(x, 3) for x in canarino[0]]).encode()).hexdigest()
        conn.execute("INSERT INTO index_meta (embedding_model, dimensioni, canary_hash) VALUES (%s, %s, %s)",
                     (modello, len(esempio), impronta))
        return True
    if meta[0] != modello or meta[1] != len(esempio):
        anomalia(conn, "indice:modello-cambiato", "critico", "Il modello dei vettori e' cambiato",
                 f"L'indice e' stato costruito con {meta[0]} ({meta[1]} dimensioni), ora e' configurato {modello} "
                 f"({len(esempio)}). Nessun vettore nuovo finche' non si ricostruisce l'indice.", None, "indice")
        return False
    return True


# ------------------------------------------------------------------ anomalie
def anomalia(conn, impronta, gravita, titolo, cosa_fare, azienda, oggetto, dettaglio=None):
    conn.execute("SELECT segnala_anomalia(%s, %s, 'documenti', %s, %s, %s, %s, %s::jsonb)",
                 (impronta, gravita, titolo, cosa_fare, azienda, oggetto, json.dumps(dettaglio or {})))


def chiudi(conn, impronta):
    conn.execute("SELECT chiudi_anomalia(%s)", (impronta,))


# ------------------------------------------------------------------ un giro
def impronta_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for blocco in iter(lambda: f.read(1 << 20), b""):
            h.update(blocco)
    return h.hexdigest()


def indicizza_fonte(conn, fonte, lettore, stato_vettori):
    fid, percorso, aziende = fonte
    azienda = aziende[0] if len(aziende) == 1 else None
    cartella = RADICE / percorso
    if not cartella.is_dir():
        anomalia(conn, f"cartella-mancante:{fid}", "errore", f"Cartella non trovata: {percorso}",
                 "Controllare che la cartella esista e che la condivisione sia montata; "
                 "oppure correggere il percorso della fonte.", azienda, f"fonte:{fid}")
        return {"fonte": fid, "errore": "cartella non trovata"}
    chiudi(conn, f"cartella-mancante:{fid}")

    noti = {r[0]: r for r in conn.execute(
        "SELECT documento, impronta, dimensione, modificato_il, stato FROM documenti WHERE source_id = %s", (fid,))}
    presenti = file_da_leggere(cartella)
    conteggi = {"fonte": fid, "nuovi": 0, "cambiati": 0, "uguali": 0, "tolti": 0, "errori": 0}

    for rel, p in presenti:
        st = p.stat()
        quando = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).replace(microsecond=0)
        vecchio = noti.get(rel)
        if vecchio and vecchio[2] == st.st_size and vecchio[3] == quando and vecchio[4] != "errore":
            conteggi["uguali"] += 1
            continue
        impronta = impronta_file(p)
        if vecchio and vecchio[1] == impronta and vecchio[4] != "errore":
            conn.execute("UPDATE documenti SET modificato_il = %s WHERE source_id = %s AND documento = %s",
                         (quando, fid, rel))
            conteggi["uguali"] += 1
            continue
        chiave = f"illeggibile:{fid}:{rel}"
        try:
            pezzi = lettore.pezzi(p)
        except Exception as e:
            conteggi["errori"] += 1
            print(f"  ERRORE {fid}/{rel}: {type(e).__name__}: {e}")
            with conn.transaction():
                conn.execute("DELETE FROM chunks WHERE source_id = %s AND documento = %s", (fid, rel))
                conn.execute("""INSERT INTO documenti (source_id, documento, impronta, dimensione, modificato_il, stato, errore, pezzi)
                                VALUES (%s,%s,%s,%s,%s,'errore',%s,0)
                                ON CONFLICT (source_id, documento) DO UPDATE SET impronta = EXCLUDED.impronta,
                                  dimensione = EXCLUDED.dimensione, modificato_il = EXCLUDED.modificato_il,
                                  stato = 'errore', errore = EXCLUDED.errore, pezzi = 0, indicizzato_il = now()""",
                             (fid, rel, impronta, st.st_size, quando, f"{type(e).__name__}: {e}"[:500]))
                anomalia(conn, chiave, "attenzione", f"File non leggibile: {rel}",
                         "Aprire il file: se e' danneggiato o protetto da password, sostituirlo con una copia "
                         "leggibile o spostarlo in _archivio.", azienda, f"fonte:{fid}",
                         {"errore": f"{type(e).__name__}: {e}"[:300]})
            continue

        vett = vettori([t for t, _ in pezzi]) if pezzi and stato_vettori["ok"] else None
        if pezzi and vett is None:
            stato_vettori["ok"] = False          # host giu': non si riprova file per file in questo giro
        if vett and not stato_vettori["verificato"]:
            stato_vettori["ok"] = stato_vettori["verificato"] = controlla_modello(conn, vett[0])
            if not stato_vettori["ok"]:
                vett = None
        with conn.transaction():
            conn.execute("DELETE FROM chunks WHERE source_id = %s AND documento = %s", (fid, rel))
            for i, (testo, pagina) in enumerate(pezzi):
                conn.execute("""INSERT INTO chunks (source_id, documento, page, content, content_hash, embedding)
                                VALUES (%s, %s, %s, %s, %s, %s::vector)
                                ON CONFLICT (source_id, documento, page, content_hash) DO NOTHING""",
                             (fid, rel, pagina, testo, hashlib.sha256(testo.encode()).hexdigest(),
                              vettore_sql(vett[i]) if vett else None))
            conn.execute("""INSERT INTO documenti (source_id, documento, impronta, dimensione, modificato_il, stato, errore, pezzi)
                            VALUES (%s,%s,%s,%s,%s,%s,NULL,%s)
                            ON CONFLICT (source_id, documento) DO UPDATE SET impronta = EXCLUDED.impronta,
                              dimensione = EXCLUDED.dimensione, modificato_il = EXCLUDED.modificato_il,
                              stato = EXCLUDED.stato, errore = NULL, pezzi = EXCLUDED.pezzi, indicizzato_il = now()""",
                         (fid, rel, impronta, st.st_size, quando, "indicizzato" if pezzi else "vuoto", len(pezzi)))
            chiudi(conn, chiave)
        conteggi["cambiati" if vecchio else "nuovi"] += 1
        print(f"  {'aggiornato' if vecchio else 'nuovo'}: {fid}/{rel} ({len(pezzi)} pezzi{'' if vett else ', senza vettori'})")

    # Cancellati dalla cartella: via dall'indice.
    for rel in set(noti) - {r for r, _ in presenti}:
        with conn.transaction():
            conn.execute("DELETE FROM chunks WHERE source_id = %s AND documento = %s", (fid, rel))
            conn.execute("DELETE FROM documenti WHERE source_id = %s AND documento = %s", (fid, rel))
            chiudi(conn, f"illeggibile:{fid}:{rel}")
        conteggi["tolti"] += 1
        print(f"  tolto: {fid}/{rel}")

    # Doppioni: lo stesso contenuto in due file della stessa cartella.
    doppi = conn.execute("""SELECT impronta, array_agg(documento ORDER BY documento) FROM documenti
                             WHERE source_id = %s GROUP BY impronta HAVING count(*) > 1""", (fid,)).fetchall()
    attuali = []
    for impronta, documenti in doppi:
        chiave = f"doppione:{fid}:{impronta[:16]}"
        attuali.append(chiave)
        anomalia(conn, chiave, "attenzione", f"Stesso documento in {len(documenti)} copie: {documenti[0]}",
                 "Tenere una copia sola: le altre vanno cancellate o spostate in _archivio, "
                 "altrimenti l'assistente cita lo stesso testo due volte.", azienda, f"fonte:{fid}",
                 {"file": documenti})
    for (chiave,) in conn.execute("""SELECT impronta FROM anomalie WHERE impronta LIKE %s
                                      AND stato IN ('aperta', 'presa')""", (f"doppione:{fid}:%",)).fetchall():
        if chiave not in attuali:
            chiudi(conn, chiave)
    return conteggi


def completa_vettori(conn, stato_vettori):
    """Pezzi entrati senza vettore (host giu' al giro prima)."""
    if not stato_vettori["ok"]:
        return 0
    fatti = 0
    while True:
        righe = conn.execute("""SELECT c.id, c.content FROM chunks c JOIN sources s ON s.id = c.source_id
                                 WHERE c.embedding IS NULL AND s.provenienza = 'cartella'
                                 ORDER BY c.id LIMIT 64""").fetchall()
        if not righe:
            return fatti
        vett = vettori([r[1] for r in righe])
        if not vett:
            return fatti
        if not stato_vettori["verificato"]:
            stato_vettori["ok"] = stato_vettori["verificato"] = controlla_modello(conn, vett[0])
            if not stato_vettori["ok"]:
                return fatti
        with conn.transaction():
            for (cid, _), v in zip(righe, vett):
                conn.execute("UPDATE chunks SET embedding = %s::vector, updated_at = now() WHERE id = %s",
                             (vettore_sql(v), cid))
        fatti += len(righe)


def giro():
    lettore = Lettore()
    stato_vettori = {"ok": True, "verificato": False}
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        fonti = [f for f in conn.execute(
            """SELECT id, percorso, aziende FROM sources
                WHERE provenienza = 'cartella' AND stato IN ('attiva', 'attesa') ORDER BY id""").fetchall()
                 # Le fonti di esempio hanno percorsi \\server\...: non sono cartelle montate qui.
                 if PERCORSO_VALIDO.match(f[1]) and ".." not in f[1]]
        esiti = [indicizza_fonte(conn, f, lettore, stato_vettori) for f in fonti]
        completati = completa_vettori(conn, stato_vettori)
    del lettore
    gc.collect()
    return esiti, completati


def main():
    una_volta = "--una-volta" in sys.argv
    while True:
        inizio = time.time()
        try:
            esiti, completati = giro()
            cambi = [e for e in esiti if any(e.get(k) for k in ("nuovi", "cambiati", "tolti", "errori", "errore"))]
            if cambi or completati or una_volta:
                print(f"giro in {time.time() - inizio:.0f}s: {json.dumps(esiti, ensure_ascii=False)}"
                      + (f"; vettori aggiunti a {completati} pezzi" if completati else ""), flush=True)
        except Exception as e:
            # Il database giu' non deve far morire il servizio: si riprova.
            print(f"giro non riuscito: {type(e).__name__}: {e}", flush=True)
            if una_volta:
                raise
        if una_volta:
            return
        time.sleep(INTERVALLO)


if __name__ == "__main__":
    main()
