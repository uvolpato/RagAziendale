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
Le cartelle che iniziano con '_' (_bozze, _archivio) non si leggono. I fogli
di calcolo si leggono, tranne quelli con i prezzi (decisione 72): i prezzi
vengono dal gestionale, un listino letto come testo sbaglia i preventivi.

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
# Fogli di calcolo (decisione 72): si leggono, a meno che contengano prezzi.
FOGLI = {".xlsx", ".xlsm", ".csv"}
# Formati che Docling non legge: compaiono nella scheda Documenti come esclusi,
# con il motivo, invece di sparire senza dire niente.
NON_LEGGIBILI = {".xls": "formato Excel 97-2003: salvarlo come .xlsx", ".ods": "formato OpenDocument: salvarlo come .xlsx",
                 ".doc": "formato Word 97-2003: salvarlo come .docx", ".ppt": "formato PowerPoint 97-2003: salvarlo come .pptx"}
# PDF lunghi a blocchi di pagine: Docling tiene in memoria le immagini delle
# pagine che converte, e un PDF da 20 MB in un colpo solo supera i 4 GB del
# container (successo il 19/09/2026).
PAGINE_PER_BLOCCO = int(os.environ.get("PAGINE_PER_BLOCCO", "6"))
# Un blocco che non finisce entro questo tempo si chiude: vicino al tetto di
# memoria il processo non muore, si blocca (CPU all'1%, visto il 19/09/2026).
SECONDI_PER_BLOCCO = int(os.environ.get("SECONDI_PER_BLOCCO", "600"))
# Segno messo su un file PRIMA di leggerlo: se il processo muore mentre lo
# legge (memoria), al giro dopo il file risulta non leggibile invece di far
# ripartire il servizio all'infinito sullo stesso file.
IN_LETTURA = "lettura interrotta: il servizio si e' fermato mentre leggeva questo file (probabile memoria esaurita)"
# Colonne che parlano di soldi. Con \b davanti: "costo" si', "incostante" no.
INTESTAZIONE_PREZZO = re.compile(r"\b(prezz\w*|listin\w*|cost[oi]\b|scont[oi]\b|nett[oi]\b|importi?\b|tariff\w*|"
                                 r"eur\b|euro\b|imponibil\w*)|€", re.I)
RIGHE_ESAMINATE = 200
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
            if est in DOCLING or est in TESTO or est in FOGLI or est in NON_LEGGIBILI:
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


def _numero(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return True
    t = str(v or "").strip().replace("€", "").replace(".", "").replace(",", ".").strip()
    try:
        float(t)
        return bool(t)
    except ValueError:
        return False


def _prezzi_in_righe(righe):
    """righe: liste di (valore, formato). Prezzi = una cella in formato valuta,
    oppure un'intestazione da soldi con numeri sotto nella stessa colonna.
    L'intestazione da sola non basta: un registro dei rischi puo' avere una
    colonna "costo stimato" vuota o descrittiva."""
    righe = list(righe)
    for r, riga in enumerate(righe):
        for c, (valore, formato) in enumerate(riga):
            if formato and ("€" in formato or "EUR" in formato.upper()) and _numero(valore):
                return f"celle in formato valuta (riga {r + 1})"
            if isinstance(valore, str) and len(valore) <= 60 and INTESTAZIONE_PREZZO.search(valore):
                sotto = [x[c][0] for x in righe[r + 1:r + 51] if len(x) > c and x[c][0] not in (None, "")]
                if sotto and sum(_numero(x) for x in sotto) >= max(1, len(sotto) // 2):
                    return f"colonna «{valore.strip()}» con valori numerici (riga {r + 1})"
    return None


def motivo_prezzi(p: pathlib.Path):
    """Il motivo per cui un foglio di calcolo ha i prezzi, o None."""
    if p.suffix.lower() == ".csv":
        import csv
        with open(p, encoding="utf-8", errors="replace", newline="") as f:
            campione = f.read(64 * 1024)
        try:
            dialetto = csv.Sniffer().sniff(campione.split("\n", 1)[0] or ",", delimiters=";,\t|")
        except csv.Error:
            dialetto = csv.excel
        righe = list(csv.reader(campione.splitlines()[:RIGHE_ESAMINATE], dialetto))
        if any("€" in cella and _numero(cella) for riga in righe for cella in riga):
            return "importi in euro nel file"
        return _prezzi_in_righe([[(x, None) for x in riga] for riga in righe])
    import openpyxl
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            righe = []
            for riga in ws.iter_rows(max_row=RIGHE_ESAMINATE):
                righe.append([(getattr(x, "value", None), getattr(x, "number_format", None)) for x in riga])
            motivo = _prezzi_in_righe(righe)
            if motivo:
                return f"foglio «{ws.title}»: {motivo}"
    finally:
        wb.close()
    return None


def _converti(percorso, blocco):
    """Converte UN blocco di pagine (o un file intero) con Docling e restituisce
    [(testo, pagina)]. Gira in un processo a parte che muore subito dopo:
    Docling non restituisce la memoria fra un blocco e l'altro (misurato:
    da 1,3 a oltre 6 GB, swap compreso, su un PDF di 266 pagine), e un
    processo che finisce la restituisce tutta."""
    from docling.chunking import HierarchicalChunker
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
    from docling.datamodel.settings import settings
    from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
    settings.perf.page_batch_size = 1          # una pagina alla volta in memoria
    # OCR con Tesseract (pacchetto Debian, italiano e inglese): funziona senza
    # rete, gli altri motori scaricano modelli da server esterni.
    opzioni = PdfPipelineOptions(do_ocr=True, do_table_structure=True,
                                 ocr_options=TesseractCliOcrOptions(lang=["ita", "eng"]),
                                 accelerator_options=AcceleratorOptions(num_threads=2),
                                 artifacts_path=os.environ.get("DOCLING_MODELLI") or None)
    conv = DocumentConverter(format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=opzioni),
        InputFormat.IMAGE: ImageFormatOption(pipeline_options=opzioni)})
    ris = conv.convert(percorso, page_range=blocco) if blocco else conv.convert(percorso)
    out = []
    for ch in HierarchicalChunker().chunk(ris.document):
        pagina = None
        for item in ch.meta.doc_items:
            if item.prov:
                pagina = item.prov[0].page_no
                break
        titoli = " > ".join(ch.meta.headings or [])
        out.append(((titoli + "\n" if titoli else "") + ch.text, pagina))
    return out


class Lettore:
    """Legge un file e lo divide in pezzi. Docling gira in processi figli, uno
    per blocco di pagine: la memoria torna libera a ogni blocco, e se un file
    la esaurisce (o il blocco si pianta) si chiude il figlio e il file risulta
    non leggibile; il servizio va avanti. ponytail: i modelli si ricaricano a ogni blocco (qualche secondo);
    pochi secondi contro un servizio che non si pianta."""

    def pezzi(self, p: pathlib.Path):
        if p.suffix.lower() in TESTO:
            testo = p.read_text(encoding="utf-8", errors="replace")
            return unisci([(x, None) for x in re.split(r"\n\s*\n", testo)])
        blocchi = [None]
        if p.suffix.lower() == ".pdf":
            import pypdfium2
            pdf = pypdfium2.PdfDocument(str(p))
            n = len(pdf)
            pdf.close()
            blocchi = [(a, min(a + PAGINE_PER_BLOCCO - 1, n)) for a in range(1, n + 1, PAGINE_PER_BLOCCO)] or [None]
        import multiprocessing
        grezzi = []
        for blocco in blocchi:
            # Un processo per blocco, chiuso a forza se non finisce in tempo.
            pool = multiprocessing.get_context("spawn").Pool(1, maxtasksperchild=1)
            dove = f" (pagine {blocco[0]}-{blocco[1]})" if blocco else ""
            try:
                grezzi += pool.apply_async(_converti, (str(p), blocco)).get(timeout=SECONDI_PER_BLOCCO)
            except multiprocessing.TimeoutError:
                raise MemoryError(f"lettura troppo lenta{dove}: oltre {SECONDI_PER_BLOCCO} s, "
                                  f"probabile memoria esaurita") from None
            finally:
                pool.terminate()
                pool.join()
            if len(blocchi) > 1:
                print(f"    {p.name}: pagine {blocco[0]}-{blocco[1]} di {blocchi[-1][1]}", flush=True)
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

    for (rel,) in conn.execute("SELECT documento FROM documenti WHERE source_id = %s AND errore = %s",
                               (fid, IN_LETTURA)).fetchall():
        anomalia(conn, f"illeggibile:{fid}:{rel}", "errore", f"File troppo pesante da leggere: {rel}",
                 "Il servizio si e' fermato mentre leggeva questo file, quasi certamente per memoria. Dividerlo in "
                 "file piu' piccoli, ridurre le immagini, oppure spostarlo in _archivio. Si riprova da solo quando "
                 "il file cambia.", azienda, f"fonte:{fid}")
        conn.execute("UPDATE documenti SET errore = %s WHERE source_id = %s AND documento = %s",
                     (IN_LETTURA.replace("lettura interrotta", "non letto"), fid, rel))
    noti = {r[0]: r for r in conn.execute(
        "SELECT documento, impronta, dimensione, modificato_il, stato FROM documenti WHERE source_id = %s", (fid,))}
    presenti = file_da_leggere(cartella)
    conteggi = {"fonte": fid, "nuovi": 0, "cambiati": 0, "uguali": 0, "tolti": 0, "errori": 0}

    for rel, p in presenti:
        st = p.stat()
        quando = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).replace(microsecond=0)
        vecchio = noti.get(rel)
        # Stesso file di prima: non si rilegge, nemmeno se era illeggibile (si
        # riprova quando cambia: riprovarlo a ogni giro non lo aggiusta).
        if vecchio and vecchio[2] == st.st_size and vecchio[3] == quando:
            conteggi["uguali"] += 1
            continue
        impronta = impronta_file(p)
        if vecchio and vecchio[1] == impronta:
            conn.execute("UPDATE documenti SET modificato_il = %s WHERE source_id = %s AND documento = %s",
                         (quando, fid, rel))
            conteggi["uguali"] += 1
            continue
        chiave = f"illeggibile:{fid}:{rel}"
        escluso = NON_LEGGIBILI.get(p.suffix.lower())
        if not escluso and p.suffix.lower() in FOGLI:
            try:
                escluso = motivo_prezzi(p)
                escluso = f"contiene prezzi: {escluso}" if escluso else None
            except Exception as e:
                escluso = f"non si riesce a controllare se contiene prezzi ({type(e).__name__}): per prudenza resta fuori"
        if escluso:
            with conn.transaction():
                conn.execute("DELETE FROM chunks WHERE source_id = %s AND documento = %s", (fid, rel))
                conn.execute("""INSERT INTO documenti (source_id, documento, impronta, dimensione, modificato_il, stato, errore, pezzi)
                                VALUES (%s,%s,%s,%s,%s,'escluso',%s,0)
                                ON CONFLICT (source_id, documento) DO UPDATE SET impronta = EXCLUDED.impronta,
                                  dimensione = EXCLUDED.dimensione, modificato_il = EXCLUDED.modificato_il,
                                  stato = 'escluso', errore = EXCLUDED.errore, pezzi = 0, indicizzato_il = now()""",
                             (fid, rel, impronta, st.st_size, quando, escluso[:500]))
                chiudi(conn, chiave)
            conteggi["esclusi"] = conteggi.get("esclusi", 0) + 1
            print(f"  escluso: {fid}/{rel} ({escluso})")
            continue
        conn.execute("""INSERT INTO documenti (source_id, documento, impronta, dimensione, modificato_il, stato, errore, pezzi)
                        VALUES (%s,%s,%s,%s,%s,'errore',%s,0)
                        ON CONFLICT (source_id, documento) DO UPDATE SET impronta = EXCLUDED.impronta,
                          dimensione = EXCLUDED.dimensione, modificato_il = EXCLUDED.modificato_il,
                          stato = 'errore', errore = EXCLUDED.errore, indicizzato_il = now()""",
                     (fid, rel, impronta, st.st_size, quando, IN_LETTURA))
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


BLOCCO = 7_310_061      # pg_advisory_lock: un giro alla volta, in tutto il database


def giro(aspetta=False):
    """Un giro su tutte le cartelle. Uno solo alla volta (servizio e giri a
    mano insieme leggerebbero due volte gli stessi file): il servizio salta il
    giro se un altro e' in corso, il giro a mano (aspetta=True) lo attende.
    Il blocco si libera da solo se il processo muore (e' della connessione)."""
    lettore = Lettore()
    stato_vettori = {"ok": True, "verificato": False}
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        if aspetta:
            conn.execute("SELECT pg_advisory_lock(%s)", (BLOCCO,))
        elif not conn.execute("SELECT pg_try_advisory_lock(%s)", (BLOCCO,)).fetchone()[0]:
            return None, 0
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
            esiti, completati = giro(aspetta=una_volta)
            if esiti is None:
                print("un altro giro e' in corso: si salta questo", flush=True)
                esiti = []
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
