"""Indicizzazione delle cartelle collegate (decisioni 61-64).

    python indicizza.py              # un giro ogni INTERVALLO secondi, per sempre
    python indicizza.py --una-volta  # un giro solo (prove, verifiche)
    python indicizza.py --forza [--solo manuale.pdf]
        # un giro solo che ignora i limiti del giorno: legge anche i file grossi
        # e, se sulla GPU non c'e' posto, scarica il modello di chat da LM Studio.
        # Mentre dura, l'assistente risponde che sta aggiornando l'indice.

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
import signal
import subprocess
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
# Dove si leggono i documenti: docling-serve su una GPU (sviluppo: container
# docling; produzione: server GPU dietro TLS e INFERENCE_TOKEN). Vuoto = qui,
# su CPU, in processi figli (piu' lento, ma senza dipendere da nessuno).
DOCLING_URL = os.environ.get("DOCLING_URL", "").rstrip("/")
PAGINE_PER_BLOCCO = int(os.environ.get("PAGINE_PER_BLOCCO", "30" if DOCLING_URL else "6"))
# GPU: in sviluppo e' la STESSA scheda dove LM Studio tiene il modello di chat
# (16 GB: Qwen 27B ne occupa 13, Docling ne vuole ~3). Regola, decisa con
# l'azienda: di giorno si legge in GPU solo se c'e' posto con LLM ed embedding
# caricati; il modello di chi sta chattando non si tocca MAI da soli. Nella
# finestra notturna (o con --forza) si scarica l'LLM e si legge tutto.
GPU = os.environ.get("GPU", "auto")                         # auto | off
# Quanta VRAM libera serve per leggere in GPU invece che su CPU. 3500 = il
# picco di Docling misurato il 20/09/2026 sul catalogo IPURO (2,1-2,7 GB fra
# layout, tabelle e immagini di pagina) piu' un terzo di margine. Il VLM delle
# descrizioni NON e' in questo conto: sta su LM Studio e la sua VRAM (2,3 GB per
# glm-ocr) risulta gia' occupata quando si guarda quanto e' libero.
VRAM_MINIMA_MB = int(os.environ.get("VRAM_MINIMA_MB", "3500"))
# Scaricare il modello di chat per far posto a Docling: serviva quando i
# modelli non stavano tutti in VRAM. Da quando il server e' llama-swap ci
# stanno (11,1 GB su 16,3, misurato il 22/09/2026), e scaricarlo faceva
# pagare 34 secondi di ricarica a ogni messaggio in chat. `no` lo
# disattiva: la lettura si arrangia con lo spazio che avanza, e se non
# basta un blocco si rifa' sul processore.
SCARICA_CHAT = os.environ.get("SCARICA_CHAT", "si") != "no"
FINESTRA_NOTTE = os.environ.get("FINESTRA_NOTTE", "")       # "22:00-06:00"; vuoto = nessuna finestra
# Fuori finestra i file oltre questa soglia aspettano la notte: leggerli di
# giorno tiene occupata la macchina mentre qualcuno chatta. 0 = nessun limite.
MB_MAX_DI_GIORNO = int(os.environ.get("MB_MAX_DI_GIORNO", "0"))
# Chi tiene il modello di chat, per poterlo scaricare nella finestra:
# sviluppo LM Studio (SDK sulla stessa porta dell'API). In produzione ci sara'
# il server di inferenza: vLLM prealloca la VRAM e si libera con /sleep.
# Il server dei modelli, per SCARICARE quello di chat quando serve la VRAM a
# Docling. Dal 21/09/2026 e' llama-swap (fuori da Docker, sull'host): espone
# POST /api/models/unload/<id>. Prima era LM Studio con il suo SDK Python.
MODELLI_HOST = os.environ.get("MODELLI_HOST", "")           # "host.docker.internal:1235"
MODELLO_CHAT = os.environ.get("MODELLO_CHAT", "")           # l'id da scaricare, es. "qwen3-8b-gsq"
# Chi descrive le immagini dei cataloghi (le rende ricercabili per nome):
#   api  un VLM servito altrove (sviluppo: glm-ocr su LM Studio, 2,3 GB;
#        produzione: il server di inferenza). Se non risponde, Docling registra
#        l'errore e va avanti: il documento entra lo stesso, senza descrizioni.
#   off  niente descrizioni; le immagini si estraggono e si salvano comunque.
# Un VLM dentro questa immagine e' stato provato e scartato: SmolVLM 256M
# pesava 3,3 GB e descriveva appena ("a few bottles").
VLM_DESCRIZIONI = os.environ.get("VLM_DESCRIZIONI", "api")       # api | off
VLM_URL = os.environ.get("VLM_URL", "")                     # "http://host.docker.internal:1234/v1"
VLM_MODELLO = os.environ.get("VLM_MODELLO", "")             # identificatore del modello su quell'host
# Un blocco che non finisce entro questo tempo si chiude: vicino al tetto di
# memoria il processo non muore, si blocca (CPU all'1%, visto il 19/09/2026).
SECONDI_PER_BLOCCO = int(os.environ.get("SECONDI_PER_BLOCCO", "600"))
# Segno messo su un file PRIMA di leggerlo: se il processo muore mentre lo
# legge (memoria), al giro dopo il file risulta non leggibile invece di far
# ripartire il servizio all'infinito sullo stesso file.
IN_LETTURA = "lettura interrotta: il servizio si e' fermato mentre leggeva questo file"
# Il file che stiamo leggendo adesso: serve a distinguere un riavvio VOLUTO
# (deploy, docker stop: arriva SIGTERM) da un guasto vero (memoria esaurita,
# corrente che manca: il processo muore e basta). Nel primo caso il documento
# non ha nessuna colpa e deve tornare "da leggere", non "illeggibile".
IN_CORSO = {}


def _riavvio_voluto(_segnale, _frame):
    """SIGTERM: si toglie il segno di lettura al file in corso e si esce. Il
    crash vero non passa di qui — quello lo marca il riavvio, ed e' giusto:
    un file che fa morire il servizio non deve poterlo rifare all'infinito."""
    if IN_CORSO:
        try:
            with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, connect_timeout=5) as c:
                if IN_CORSO.get("stato"):      # c'era gia' una lettura buona: si rimette com'era
                    c.execute("UPDATE documenti SET in_lettura = NULL, pagine_fatte = 0,"
                              " pagine_totali = NULL, figure_fatte = 0, figure_totali = NULL,"
                              " errore = NULL, stato = %s"
                              " WHERE source_id = %s AND documento = %s",
                              (IN_CORSO["stato"], IN_CORSO["fid"], IN_CORSO["rel"]))
                else:                          # mai letto prima: la riga sparisce, si rilegge dopo
                    c.execute("DELETE FROM documenti WHERE source_id = %s AND documento = %s",
                              (IN_CORSO["fid"], IN_CORSO["rel"]))
            print(f"riavvio richiesto: {IN_CORSO['fid']}/{IN_CORSO['rel']} torna da leggere", flush=True)
        except Exception as e:
            print(f"riavvio richiesto, ma non ho potuto liberare il file ({type(e).__name__}: {e})", flush=True)
    sys.exit(0)


# Colonne che parlano di soldi. Con \b davanti: "costo" si', "incostante" no.
INTESTAZIONE_PREZZO = re.compile(r"\b(prezz\w*|listin\w*|cost[oi]\b|scont[oi]\b|nett[oi]\b|importi?\b|tariff\w*|"
                                 r"eur\b|euro\b|imponibil\w*)|€", re.I)
RIGHE_ESAMINATE = 200
# Prezzi dentro la descrizione di un'immagine. Di regola restano fuori: i
# prezzi vengono dal gestionale (decisione 72), e un listino fotografato
# rientrerebbe dalla finestra — successo davvero, una descrizione conteneva
# "F0305 370 ml 12 EUR 2,15 F0405 500 ml..." (20/09/2026).
# Ma su un catalogo FORNITORE quel prezzo puo' essere l'unico che esiste: nel
# gestionale non c'e'. Percio' e' una scelta, non una regola muta:
#   escludi (predefinito)  la descrizione con prezzi si scarta
#   ammetti                entra, e l'assistente potra' rispondere a domande
#                          come "dieci articoli sotto i 10 euro"
# Vale per fonte (decisione D16): si ammette sui cataloghi fornitore, non sui
# documenti dove il prezzo autorevole sta nel gestionale.
PREZZI_DESCRIZIONI = os.environ.get("PREZZI_DESCRIZIONI", "escludi")   # escludi | ammetti
# Prezzi dentro la descrizione di un'immagine: un listino fotografato rientra
# dalla finestra che la decisione 72 ha chiuso (i prezzi vengono dal
# gestionale). Successo davvero: una descrizione conteneva "F0305 370 ml 12
# € 2,15 F0405 500 ml 12 € 2,85..." (20/09/2026).
PREZZO_IN_DESCRIZIONE = re.compile(r"(€|\beur\b|\bprezz\w*|\bcost[oi]\b|\bprice\b|\bpreis\b)[\s:]*[\d.,]+"
                                   r"|[\d.,]+\s*(€|\beur\b)", re.I)
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


# Riga di una tabella di varianti: «DST2001 rot red», «GRA1040 weiss white».
# Nei cataloghi il prodotto sta in cima alla pagina e le varianti in tabella
# sotto: unendo le righe si perde il soggetto, e il vettore di venti colori
# insieme significa «elenco di codici colore», non «pietra rossa». Misurato il
# 20/09/2026: alla domanda «sassi rossi» il pezzo che conteneva davvero
# «DST2001 rot red» risultava il MENO pertinente di tutta EUROSAND (0,5853),
# dietro a un cesto di muschio (0,4802).
CODICE_VARIANTE = re.compile(r"^[A-Z]{2,5}[-\s]?\d{3,}\b")
MAX_VARIANTE = 80          # una riga di tabella e' corta; oltre e' prosa


MAX_PAROLE_VARIANTE = 6    # codice + colore/misura, non una frase


def _e_variante(testo):
    """Riga di tabella: codice in testa, riga corta, POCHE parole. Il conteggio
    delle parole evita di scambiare per tabella una frase che cita una norma
    ("ISO 27001 richiede un riesame annuale della politica")."""
    testo = testo.strip()
    return (len(testo) <= MAX_VARIANTE and len(testo.split()) <= MAX_PAROLE_VARIANTE
            and bool(CODICE_VARIANTE.match(testo)))


def _contesto_di_pagina(pezzi_pagina):
    """Il testo che dice DI COSA parla la pagina: il primo pezzo di prosa
    abbastanza lungo (il titolo del prodotto, di solito con le misure).

    Il titolo sta in TESTA al pezzo, quindi si taglia invece di scartare: sulla
    pagina vera Docling gli attacca in coda la descrizione della figura e il
    pezzo supera i 200 caratteri. Con un tetto rigido il contesto restava
    vuoto e le righe di tabella finivano nude — «DST2001 rot red» senza una
    parola che dicesse «pietre» (EUROSAND pagina 7, 21/09/2026)."""
    for testo, _pagina in pezzi_pagina:
        pulito = " ".join(testo.split())
        if not _e_variante(pulito) and len(pulito) >= 15:
            return pulito[:200]
    return ""


def unisci(pezzi):
    """[(testo, pagina)] -> pezzi di dimensione utile per la ricerca.

    Docling spezza per elemento (un titolo, una riga di tabella): da soli non
    rispondono a nulla. Si uniscono i consecutivi della stessa pagina fino a
    MAX_PEZZO; un pezzo troppo lungo si taglia ai capoversi.

    ECCEZIONE: le righe di una tabella di varianti restano pezzi a se', ognuna
    con l'intestazione della pagina davanti. Cosi' «DEKOSTEINE pietre
    decorative 9-13 mm | DST2001 rot red» vale «pietra decorativa rossa» e una
    domanda per colore, misura o codice la trova — in tutte le lingue del
    catalogo, perche' il vettore ci arriva anche da «sassi rossi».
    """
    out = []
    for pagina in _pagine(pezzi):
        contesto = _contesto_di_pagina(pagina)
        # Il pezzo appena messo in coda era una riga di tabella? Non basta
        # riguardare out[-1]: li' dentro c'e' gia' il titolo davanti alla riga,
        # e un testo lungo non sembra piu' una riga di tabella. Cosi' il pezzo
        # successivo ci si attaccava dentro, e in EUROSAND pagina 7 il chunk
        # finiva «... | DST2001 rot red ai. p , oa mel i» — sporcizia dell'OCR
        # dentro il pezzo che deve dire «pietra decorativa rossa». Misurato il
        # 21/09/2026: con la coda 0,5030 dalla domanda, senza 0,4651 (e il
        # primo classificato di quel giorno stava a 0,4758).
        chiuso = False
        for testo, pag in pagina:
            testo = " ".join(testo.split())
            if not testo:
                continue
            if _e_variante(testo):
                out.append((f"{contesto} | {testo}" if contesto else testo, pag))
                chiuso = True
                continue
            if (out and out[-1][1] == pag and not chiuso
                    and len(out[-1][0]) < MIN_PEZZO and len(out[-1][0]) + len(testo) <= MAX_PEZZO):
                out[-1] = (out[-1][0] + "\n" + testo, pag)
            else:
                out.append((testo, pag))
            chiuso = False
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


def _pagine(pezzi):
    """I pezzi raggruppati per pagina, nell'ordine in cui arrivano."""
    gruppo, pagina_corrente = [], object()
    for testo, pagina in pezzi:
        if pagina != pagina_corrente and gruppo:
            yield gruppo
            gruppo = []
        pagina_corrente = pagina
        gruppo.append((testo, pagina))
    if gruppo:
        yield gruppo


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


# ------------------------------------------------- GPU, finestra, modelli
def in_finestra(finestra=None, adesso=None):
    """True dentro FINESTRA_NOTTE ("22:00-06:00", anche a cavallo di mezzanotte).
    L'ora e' quella LOCALE del container: senza TZ sarebbe UTC e d'estate la
    finestra si aprirebbe due ore prima."""
    finestra = FINESTRA_NOTTE if finestra is None else finestra
    if not finestra:
        return False
    try:
        a, b = [datetime.strptime(x.strip(), "%H:%M").time() for x in finestra.split("-")]
    except ValueError:
        print(f"FINESTRA_NOTTE non valida ({finestra!r}): serve 'HH:MM-HH:MM'", flush=True)
        return False
    ora = (adesso or datetime.now()).time()
    return a <= ora < b if a <= b else (ora >= a or ora < b)


def rimanda(dimensione, finestra, gpu=False):
    """Un file grosso aspetta solo se andrebbe letto sul PROCESSORE fuori dalla
    finestra: li' occupa la macchina per ore mentre qualcuno chatta. Se sulla
    GPU c'e' posto si legge subito, a qualsiasi ora e di qualsiasi dimensione:
    l'indicizzazione deve stare al passo con chi deposita i documenti, e in GPU
    non disturba nessuno."""
    if gpu or finestra or MB_MAX_DI_GIORNO <= 0:
        return False
    return dimensione > MB_MAX_DI_GIORNO * 1024 * 1024


def vram_libera_mb():
    """MB liberi sulla GPU, None se qui GPU non ce n'e'. Si chiede a nvidia-smi
    (lo inietta il runtime NVIDIA) e non a torch: torch aprirebbe un contesto
    CUDA in QUESTO processo, che poi tiene VRAM per tutto il giro senza usarla."""
    if GPU == "off":
        return None
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15)
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return None


def dove_leggere():
    """'cuda' se in questo momento c'e' posto, 'cpu' altrimenti. Si guarda a
    ogni blocco di pagine: LM Studio carica il modello quando gli arriva una
    domanda (justInTimeModelLoading), anche a meta' file."""
    libera = vram_libera_mb()
    return "cuda" if libera is not None and libera >= VRAM_MINIMA_MB else "cpu"


def _gpu_piena(e):
    """L'errore viene dalla GPU: quel blocco si rifa' in CPU invece di perdere
    il file. Qualunque errore CUDA, non solo la memoria finita: la lettura non
    deve mai fallire per colpa dell'acceleratore."""
    return "cuda" in f"{type(e).__name__}: {e}".lower()


def scarica_llm():
    """Scarica il modello di CHAT per fare spazio a Docling, e lascia stare
    l'embedding e il VLM: quelli servono a QUESTO giro. True se ha scaricato
    qualcosa. Non alza mai: se il server dei modelli non risponde si legge lo
    stesso, in CPU.

    Dal 21/09/2026 il server e' llama-swap (`POST /api/models/unload/<id>`) e
    non piu' LM Studio con il suo SDK. Ricaricarlo non tocca a noi: llama-swap
    lo riavvia alla prima richiesta, con i parametri del suo YAML invece che
    con parametri indovinati da qui — stessa proprieta' che aveva il JIT di LM
    Studio, ed e' il motivo per cui questa funzione scarica e basta.

    ponytail: in produzione il modello sta su vLLM, che prealloca la VRAM e si
    libera con /sleep; quando il server GPU esistera' e' un ramo in piu' qui."""
    return _scarica_modello(MODELLO_CHAT, "di chat") if MODELLO_CHAT else False


def _scarica_modello(modello: str, come_si_chiama: str = "") -> bool:
    """Scarica UN modello dal server. True se c'era ed e' uscito.

    Non alza mai: se il server non risponde si va avanti con quel che c'e'.
    llama-swap lo ricarica alla prima richiesta successiva."""
    if not MODELLI_HOST or not modello:
        return False
    try:
        import urllib.parse
        import urllib.request
        base = MODELLI_HOST if "://" in MODELLI_HOST else f"http://{MODELLI_HOST}"
        # L'id puo' contenere una barra ("qwen/qwen3-vl-4b"): va protetta, o
        # diventa un altro pezzo di percorso e il server risponde 404.
        via = urllib.parse.quote(modello, safe="")
        req = urllib.request.Request(f"{base.rstrip('/')}/api/models/unload/{via}", method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            # 200 = scaricato; 404 = non era caricato, e va benissimo.
            return r.status == 200
    except Exception as e:
        print(f"il modello {come_si_chiama or modello} non e' stato scaricato "
              f"({type(e).__name__}: {e}): si legge con quel che c'e'", flush=True)
        return False


def _opzioni_pdf(device="cpu", descrivi=True):
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
    # OCR con Tesseract (pacchetto Debian, italiano e inglese): funziona senza
    # rete, gli altri motori scaricano modelli da server esterni.
    opzioni = PdfPipelineOptions(
        do_ocr=True, do_table_structure=True,
        ocr_options=TesseractCliOcrOptions(lang=["ita", "eng"]),
        # L'OCR resta a Tesseract (CPU) in ogni caso: in GPU vanno layout e
        # tabelle, che sono il grosso del tempo.
        accelerator_options=AcceleratorOptions(num_threads=2, device=device),
        artifacts_path=os.environ.get("DOCLING_MODELLI") or None)
    # Senza queste tre righe Docling NON conserva le immagini: get_image()
    # torna vuoto, la tabella immagini resta a zero e la descrizione non parte
    # mai (verificato il 20/09/2026 sul catalogo IPURO: 0 immagini su 41 pagine).
    # generate_page_images serve anche alle tabelle: TableItem.get_image()
    # ritaglia dalla pagina (generate_table_images e' deprecato).
    opzioni.generate_picture_images = True
    opzioni.generate_page_images = True
    # 3.0 = 216 dpi (un PDF nasce a 72): figure estratte piu' nitide ed
    # etichette leggibili da chi le descrive. Il prezzo sono 2,25 volte i pixel
    # da disegnare e da tenere in memoria per blocco: se il picco si avvicina al
    # tetto del container, si abbassa PAGINE_PER_BLOCCO.
    opzioni.images_scale = 3.0
    # Senza un VLM configurato non si chiede niente a nessuno: le immagini
    # entrano lo stesso, trovabili dal testo della loro pagina.
    # `descrivi=False`: le figure le descriviamo NOI, dopo, passando al modello
    # il titolo della pagina da cui vengono (vedi _descrivi_col_titolo). Docling
    # usa un prompt unico per tutto il documento e non puo' saperlo.
    if not descrivi or VLM_DESCRIZIONI != "api" or not VLM_MODELLO:
        opzioni.do_picture_description = False
        return opzioni
    # Il VLM sta altrove (sviluppo: glm-ocr su LM Studio; produzione: il server
    # di inferenza): un modello dentro questo servizio si contenderebbe la VRAM
    # con Docling, e quello piccolo abbastanza da starci descrive troppo male.
    # enable_remote_services riguarda host INTERNI dichiarati (egress), non
    # internet. Se l'host non risponde, Docling lo registra e prosegue senza
    # descrizioni (provato staccando la porta): il documento entra lo stesso.
    from docling.datamodel.pipeline_options import PictureDescriptionApiOptions
    chiave = os.environ.get("INFERENCE_TOKEN") or ""
    opzioni.do_picture_description = True
    opzioni.enable_remote_services = True
    opzioni.picture_description_options = PictureDescriptionApiOptions(
        url=f"{VLM_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {chiave}"} if chiave else {},
        params={"model": VLM_MODELLO},
        # scale: il modello vede l'immagine ingrandita 3 volte e legge anche le
        # etichette piccole.
        #
        # picture_area_threshold=0: si descrivono TUTTE le figure. La soglia
        # c'era (0,08 = almeno l'8% della pagina) e il 22/09/2026 e' stata
        # tolta, perche' selezionava esattamente le figure sbagliate.
        #
        # In una griglia di varianti — 26 campioni di colore su una pagina —
        # ogni campione vale meno dell'1% per costruzione: nessuna griglia di
        # nessun catalogo passera' mai l'8%. Restavano descritte le foto
        # d'ambiente (il cesto sul tavolo con le candele) e sparivano le foto
        # del PRODOTTO, che sono quelle che serve vedere per scegliere un
        # articolo. Su EUROSAND: 115 immagini cercabili su 894, il 13%.
        #
        # Il costo che avevo temuto non c'era: misurato 0,4-0,9 s per figura
        # piccola, cioe' ~9 minuti in piu' per catalogo, una volta sola per
        # versione del documento. E le descrizioni delle piccole sono le piu'
        # utili: «numerous bright red, irregularly shaped, rough-textured»
        # contro «DEKOSTEINE ... a basket with candles» della grande.
        #
        # Il rumore (loghi, icone, cornici) entra nell'indice e non da'
        # fastidio: «a blue rectangular icon with diagonal stripes» non vince
        # nessuna ricerca di prodotto. Provato anche a farlo CLASSIFICARE al
        # modello (PRODOTTO/GRAFICA/AMBIENTE), in due forme di prompt: risponde
        # sempre PRODOTTO, anche su un'icona che lui stesso descrive come
        # «icon». Non filtrare, ordinare: e' la stessa regola per cui la
        # descrizione non e' un biglietto d'ingresso.
        scale=3.0, picture_area_threshold=0,
        # Trascrizione E descrizione, in inglese. Chiedere la descrizione in
        # italiano faceva inventare a glm-ocr ("profumo di arsenico"); chiedere
        # SOLO la trascrizione lasciava fuori dall'indice gli attributi visivi,
        # ed e' il difetto che ha fatto fallire "sto cercando dei sassi rossi":
        # una foto di pietre rosse con l'etichetta "LAVA ROCKS 20-40 mm" entrava
        # senza la parola "rosso" (provato il 20/09/2026). Colore e materiale
        # sono spesso l'unica cosa che l'utente ricorda di un prodotto.
        # L'embedding e' multilingue: una domanda in italiano trova lo stesso
        # una descrizione in inglese.
        prompt=("Transcribe all text visible in this image. Then add one short sentence describing "
                "what is shown: objects, colours, materials, shapes. Do not invent details that are "
                "not visible."),
        timeout=120, concurrency=1)
    return opzioni


def _converti(percorso, blocco, device="cpu", descrivi=True):
    """Converte UN blocco di pagine (o un file intero) con Docling e restituisce
    [(testo, pagina)] e le immagini [(PIL.Image, pagina)]. Gira in un processo a
    parte che muore subito dopo: Docling non restituisce la memoria fra un
    blocco e l'altro (misurato: da 1,3 a oltre 6 GB, swap compreso, su un PDF di
    266 pagine), e un processo che finisce la restituisce tutta. Vale anche per
    la VRAM: il contesto CUDA se ne va con il processo, quindi fra un blocco e
    l'altro la GPU torna libera per chi sta chattando."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.settings import settings
    from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
    settings.perf.page_batch_size = 1          # una pagina alla volta in memoria
    opzioni = _opzioni_pdf(device, descrivi)
    conv = DocumentConverter(format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=opzioni),
        InputFormat.IMAGE: ImageFormatOption(pipeline_options=opzioni)})
    ris = conv.convert(percorso, page_range=blocco) if blocco else conv.convert(percorso)
    tolte = togli_prezzi(ris.document)
    if tolte:
        print(f"    {tolte} descrizioni con prezzi scartate (decisione 72)", flush=True)
    return _chunk(ris.document), _immagini(ris.document), _markdown_per_pagina(ris.document, blocco)


def _markdown_per_pagina(documento, blocco):
    """{pagina: markdown} con i SEGNAPOSTO delle figure al loro posto.

    `export_to_markdown(page_no=N)` estrae una pagina sola da un documento gia'
    convertito: i segnaposto per pagina si ottengono pagando UNA conversione
    ogni sei pagine, non una per pagina. Misurato il 22/09/2026: un blocco di
    sei pagine costa 61 s, una pagina sola 56 — quasi tutto avvio. Una pagina
    per blocco sarebbe costata 80 minuti in piu' per catalogo.

    Il conto torna: sulle pagine 7-12 di EUROSAND i segnaposto sono 28, 11, 5,
    4, 6, 8 — esattamente le figure di quelle pagine.
    """
    fuori = {}
    pagine = range(blocco[0], blocco[1] + 1) if blocco else sorted(
        {p.prov[0].page_no for p in documento.pictures if p.prov} or {1})
    for n in pagine:
        try:
            fuori[n] = documento.export_to_markdown(page_no=n)
        except Exception as e:
            print(f"    markdown della pagina {n} non estratto ({type(e).__name__}: {e})", flush=True)
    return fuori


def _immagini(documento):
    """Le immagini (PictureItem) e le tabelle (TableItem, rese come immagine)
    del documento, con la loro pagina e la descrizione generata dal VLM."""
    out = []
    for pic in documento.pictures:
        try:
            img = pic.get_image(documento)
        except Exception:
            continue
        if img is None:
            continue
        pagina = pic.prov[0].page_no if pic.prov else None
        descr = (pic.meta.description.text if pic.meta and pic.meta.description else None)
        out.append((img, pagina, descr))
    for tab in documento.tables:
        try:
            img = tab.get_image(documento)
        except Exception:
            continue
        if img is None:
            continue
        pagina = tab.prov[0].page_no if tab.prov else None
        out.append((img, pagina, None))
    return out


def togli_prezzi(documento):
    """Cancella le descrizioni delle immagini che contengono prezzi, PRIMA che
    il chunker le metta nel testo. I prezzi vengono dal gestionale (decisione
    72): un listino trascritto da una foto del catalogo li farebbe rientrare
    nell'indice senza che nessuno se ne accorga. Si scarta tutta la descrizione,
    non solo il numero: meglio perdere una didascalia che indicizzare un prezzo
    sbagliato. L'immagine resta, e resta mostrabile in chat."""
    if PREZZI_DESCRIZIONI == "ammetti":
        return 0
    tolte = 0
    for pic in getattr(documento, "pictures", []):
        descrizione = getattr(getattr(pic, "meta", None), "description", None)
        testo = getattr(descrizione, "text", None)
        if testo and PREZZO_IN_DESCRIZIONE.search(testo):
            pic.meta.description = None
            tolte += 1
    return tolte


def _chunk(documento):
    """DoclingDocument -> [(testo con i titoli, pagina)].

    Le descrizioni delle immagini sono GIA' qui dentro: il chunker di Docling
    include le annotazioni delle figure nel testo del pezzo, con il percorso dei
    titoli attorno. Aggiungerle una seconda volta (come si faceva fino al
    20/09/2026 con una funzione a parte) raddoppiava lo stesso contenuto
    nell'indice: gli stessi pezzi venivano recuperati due volte e occupavano i
    posti del testo vero — su "sto cercando dei sassi rossi" tutti e cinque i
    risultati erano descrizioni di immagini."""
    from docling_core.transforms.chunker.hierarchical_chunker import HierarchicalChunker
    out = []
    for ch in HierarchicalChunker().chunk(documento):
        pagina = None
        for item in ch.meta.doc_items:
            if item.prov:
                pagina = item.prov[0].page_no
                break
        titoli = " > ".join(ch.meta.headings or [])
        out.append(((titoli + "\n" if titoli else "") + ch.text, pagina))
    return out


def _converti_remoto(percorso, blocco):
    """Un blocco di pagine (o un file intero) a docling-serve: layout, tabelle
    e OCR sulla GPU. Torna il documento strutturato; i pezzi si fanno qui."""
    from docling_core.types.doc import DoclingDocument
    token = os.environ.get("INFERENCE_TOKEN") or ""
    campi = {"to_formats": ["json"], "do_ocr": "true", "ocr_lang": ["it", "en"],
             "table_mode": "accurate", "image_export_mode": "embedded", "abort_on_error": "false"}
    if blocco:
        campi["page_range"] = [str(blocco[0]), str(blocco[1])]
    with open(percorso, "rb") as f:
        r = httpx.post(f"{DOCLING_URL}/v1/convert/file", data=campi,
                       files={"files": (pathlib.Path(percorso).name, f, "application/octet-stream")},
                       headers={"Authorization": f"Bearer {token}"} if token else {},
                       timeout=SECONDI_PER_BLOCCO, verify=os.environ.get("DOCLING_CA") or True)
    if r.status_code >= 400:
        raise RuntimeError(f"docling-serve {r.status_code}: {r.text[:300]}")
    ris = r.json()
    if ris.get("status") not in ("success", "partial_success"):
        raise RuntimeError(f"docling-serve: {ris.get('status')} {str(ris.get('errors'))[:300]}")
    doc = DoclingDocument.model_validate(ris["document"]["json_content"])
    return _chunk(doc), _immagini(doc), {}


def _in_processo(percorso, blocco, device, descrivi=True):
    """_converti in un processo figlio, chiuso a forza se non finisce in tempo."""
    import multiprocessing
    pool = multiprocessing.get_context("spawn").Pool(1, maxtasksperchild=1)
    dove = f" (pagine {blocco[0]}-{blocco[1]})" if blocco else ""
    try:
        return pool.apply_async(_converti, (percorso, blocco, device, descrivi)).get(timeout=SECONDI_PER_BLOCCO)
    except multiprocessing.TimeoutError:
        # Il messaggio dice COSA e' successo, non perche'. «Probabile memoria
        # esaurita» e' un'ipotesi, e in due occasioni ha mandato a cercare la
        # RAM mentre il blocco era solo lento (OCR su pagine fitte).
        raise MemoryError(f"blocco non finito{dove}: fermato dopo "
                          f"{SECONDI_PER_BLOCCO} s (SECONDI_PER_BLOCCO)") from None
    finally:
        pool.terminate()
        pool.join()


class Rimandato(Exception):
    """La GPU se l'e' presa qualcun altro mentre leggevamo un file grosso (fuori
    finestra LM Studio carica il modello di chat appena arriva una domanda).
    Non e' un errore del file: si lascia com'era e si riprende al giro utile."""


class Lettore:
    """Legge un file e lo divide in pezzi. Docling gira in processi figli, uno
    per blocco di pagine: la memoria torna libera a ogni blocco, e se un file
    la esaurisce (o il blocco si pianta) si chiude il figlio e il file risulta
    non leggibile; il servizio va avanti. ponytail: i modelli si ricaricano a ogni blocco (qualche secondo);
    pochi secondi contro un servizio che non si pianta."""

    def __init__(self, conn=None, finestra=False):
        self.conn, self.finestra, self.spazio_fatto = conn, finestra, False

    def _fai_spazio(self):
        """Nella finestra notturna (o con --forza) si scarica il modello di chat,
        ma solo quando c'e' davvero un file da leggere, e una volta sola per giro.
        Finche' il lock BLOCCO_LLM e' preso, l'orchestratore risponde che sta
        aggiornando l'indice invece di far leggere il modello a chi chatta.

        Con SCARICA_CHAT=no non si tocca niente: chi chatta continua a
        ricevere risposte mentre si legge. Ha senso quando i modelli stanno
        tutti in VRAM insieme — e da quando il server e' llama-swap ci stanno
        (11,1 GB su 16,3, misurato il 22/09/2026). Il prezzo lo paga la
        lettura, non le persone: se a Docling non basta lo spazio rimasto,
        quel blocco si rifa' sul processore (vedi _gpu_piena), piu' lento ma
        senza fallire.

        Scaricarlo costava 34 secondi di ricarica a OGNI messaggio, perche'
        l'indicizzazione lo rifaceva a ogni file."""
        if self.spazio_fatto or not self.finestra or not SCARICA_CHAT:
            return
        self.spazio_fatto = True
        libera = vram_libera_mb()
        if libera is None or libera >= VRAM_MINIMA_MB:
            return                       # c'e' gia' posto: non si disturba nessuno
        if scarica_llm():
            if self.conn is not None:
                self.conn.execute("SELECT pg_advisory_lock(%s)", (BLOCCO_LLM,))
            print(f"modello di chat scaricato per fare spazio: {libera} MB liberi, "
                  f"ne servono {VRAM_MINIMA_MB}", flush=True)

    def pezzi(self, p: pathlib.Path, progresso=None, solo_gpu=False, dentro=None, lettura=None,
              progresso_figure=None):
        """progresso(fatte, totali) se c'e', chiamata ad ogni blocco completato.
        solo_gpu: file che fuori dalla finestra si legge SOLO finche' la GPU e'
        libera; se la perde a meta', si ferma e si riprende dopo (Rimandato).
        dentro: cartella (relativa alla radice) dove scrivere le immagini man
        mano che escono; si torna il METADATO, non l'immagine, cosi' la memoria
        non cresce con le pagine.
        lettura: `docling` o `pagina`; None = quello che dice la configurazione.
        Lo decide la FONTE, non il file: vedi come_leggere()."""
        if p.suffix.lower() in TESTO:
            testo = p.read_text(encoding="utf-8", errors="replace")
            return unisci([(x, None) for x in re.split(r"\n\s*\n", testo)]), []
        self._fai_spazio()
        if (lettura or LETTURA) == "pagina" and p.suffix.lower() == ".pdf":
            # Il TESTO lo legge il VLM guardando la pagina; le IMMAGINI continua
            # a estrarle Docling, che su quelle e' affidabile e serve per
            # mostrarle in chat. Due letture della stessa pagina, ognuna per
            # quello che sa fare.
            # I DUE testi, non uno al posto dell'altro. Misurato il 21/09/2026
            # sulle 24 domande vere: il solo VLM fa 5/20 contro i 10/20 di
            # Docling, ma recupera le domande su formati e assortimenti che
            # Docling sbagliava tutte (categoria «Logistica» 0 su 5). Sono due
            # sguardi sulla stessa pagina e trovano cose diverse:
            #   Docling   frammenti CON le descrizioni delle figure — prosa,
            #             che risponde alle domande descrittive
            #   VLM       tabelle pulite con codici, misure e prezzi — che
            #             rispondono alle domande strutturate
            # Con il solo VLM i pezzi erano tabelle al 98% e ZERO contenevano
            # una descrizione: il vettore non aveva piu' niente da mordere.
            # Il testo di Docling si paga comunque, perche' e' la stessa
            # chiamata che estrae le immagini: buttarlo era spreco.
            # La barra si divide fra le due passate: il VLM la porta a meta',
            # Docling dalla meta' alla fine. Senza, il pannello segna «finito»
            # mentre c'e' ancora la seconda passata da fare — e chi guarda
            # pensa che sia bloccato (visto il 22/09/2026).
            def meta(offset):
                if not progresso:
                    return None
                return lambda fatte, totali: progresso(round(offset * totali + fatte / 2), totali)

            testi = self._pagine_col_vlm(p, meta(0), dentro)
            # Le figure le descriviamo NOI, con davanti il titolo della pagina
            # da cui vengono: Docling usa un prompt unico per tutto il
            # documento e non puo' saperlo. Senza contesto un ritaglio di
            # 221x149 px di sassi rossi diventa «possibly dried fruit or
            # processed food» (misurato il 22/09/2026).
            #
            # E le nostre descrizioni entrano anche nel TESTO, prendendo il
            # posto di quelle di Docling: il suo chunker le includeva nei pezzi
            # — 159 su EUROSAND — quindi l'indice conteneva quelle sbagliate.
            # Lasciarle avrebbe voluto dire cercare fra descrizioni che
            # sappiamo false. Nessun doppione, perche' Docling qui non ne
            # produce piu' (descrivi=False), e una chiamata invece di due.
            titoli = _titoli_di_pagina(dentro)
            # Il VLM ha finito: le pagine sono lette. Ora tocca a Docling, e i
            # due non lavorano MAI insieme — quindi si libera la VRAM che il
            # VLM tiene (4,3 GB) invece di quella del modello di chat.
            #
            # Misurato il 22/09/2026: con tutti e quattro i modelli caricati
            # restavano 1,9 GB liberi e Docling, che ne chiede 6,1, superava i
            # 600 secondi per blocco. Togliendo il VLM restano 8,5 GB e ci sta
            # comodo — mentre chi chatta continua ad avere risposta, che e' il
            # contrario di quello che faceva la vecchia finestra notturna.
            #
            # llama-swap lo ricarica da solo alla prima pagina del documento
            # dopo. Le descrizioni delle figure arrivano DOPO questa riga e lo
            # riaccendono: e' voluto, a quel punto Docling ha finito.
            if _scarica_modello(VLM_MODELLO, "che legge le pagine"):
                print("    VLM scaricato: la VRAM va a Docling", flush=True)
            _, immagini, markdown = self._con_docling(p, meta(0.5), solo_gpu, dentro,
                                                      descrivi=False)
            immagini = _descrivi_col_titolo(immagini, titoli, dentro, progresso_figure)
            # Le descrizioni vanno NEI SEGNAPOSTO del Markdown di Docling, che
            # li mette dove stanno le figure: cosi' ogni descrizione resta
            # accanto al suo codice articolo invece di galleggiare nella
            # pagina. Poi si indicizza quel Markdown, ed e' lo stesso file che
            # si salva su disco: quello che leggi e' quello che viene cercato.
            # Senza il testo di Docling restano le sole descrizioni, staccate:
            # e' la forma di prima, e serve a misurare se quel testo aiuti.
            if TESTO_DOCLING:
                figure, immagini = _pezzi_dal_markdown_figure(dentro, markdown,
                                                              immagini, titoli)
            else:
                figure = _pezzi_dalle_figure(immagini, titoli)
            return testi + figure, immagini
        return self._con_docling(p, progresso, solo_gpu, dentro)

    def _pagine_col_vlm(self, p, progresso=None, dentro=None):
        """[(testo, pagina)] dal VLM che LEGGE la pagina, una alla volta.

        Una pagina per volta e non un blocco: il Markdown di ognuna si salva
        appena pronto, quindi un giro interrotto riprende da dove era invece di
        ricominciare (30 minuti a catalogo)."""
        import pypdfium2
        pdf = pypdfium2.PdfDocument(str(p))
        n = len(pdf)
        pdf.close()
        if progresso:
            progresso(0, n)
        fuori = []
        errori = 0
        for pagina in range(1, n + 1):
            try:
                md = _markdown_pagina(p, pagina, dentro)
            except Exception as e:
                # Una pagina illeggibile non fa fallire il documento: entra
                # senza quella, con l'avviso nel registro.
                print(f"    {p.name}: pagina {pagina} non letta ({type(e).__name__}: {e})", flush=True)
                md = ""
                errori += 1
            fuori += _pezzi_da_markdown(md, pagina)
            if progresso:
                progresso(pagina, n)
            if pagina % 6 == 0 or pagina == n:
                print(f"    {p.name}: pagina {pagina} di {n} (VLM)", flush=True)
        # Tutte le pagine fallite non sono un catalogo illeggibile: sono il
        # VLM che non risponde (o che non e' mai stato raggiunto). Se il
        # documento entrasse lo stesso, l'impronta lo marchierebbe «fatto» e il
        # giro dopo lo saltarrebbe senza testo — come successo il 22/09/2026.
        # Un file davvero rovinato si rivede al ritorno del VLM e va in errore.
        if errori == n:
            raise Rimandato("VLM non raggiungibile: nessuna pagina letta")
        return fuori

    def _con_docling(self, p, progresso=None, solo_gpu=False, dentro=None, descrivi=True):
        """La lettura storica: Docling a blocchi di pagine, in un processo
        figlio che muore subito dopo. Resta il percorso predefinito e l'unico
        per i documenti di PROSA, dove funziona bene (nelle 24 domande vere le
        categorie «Specifiche tecniche» e «Certificazioni» fanno 4/4 e 2/3)."""
        blocchi = [None]
        if p.suffix.lower() == ".pdf":
            import pypdfium2
            pdf = pypdfium2.PdfDocument(str(p))
            n = len(pdf)
            pdf.close()
            blocchi = [(a, min(a + PAGINE_PER_BLOCCO - 1, n)) for a in range(1, n + 1, PAGINE_PER_BLOCCO)] or [None]
            if progresso:
                progresso(0, n)
        grezzi, immagini, markdown = [], [], {}
        if DOCLING_URL:
            for blocco in blocchi:
                g, im, md = _converti_remoto(str(p), blocco)
                markdown.update(md)
                grezzi += g
                immagini += _scrivi_immagini(dentro, im, len(immagini)) if dentro else im
                if len(blocchi) > 1:
                    print(f"    {p.name}: pagine {blocco[0]}-{blocco[1]} di {blocchi[-1][1]} (GPU)", flush=True)
                if progresso and blocco:
                    progresso(blocco[1], blocchi[-1][1])
            return unisci(grezzi), immagini, markdown
        for blocco in blocchi:
            device = dove_leggere()
            if solo_gpu and device != "cuda":
                raise Rimandato(f"GPU occupata dopo {blocco[0] - 1 if blocco else 0} pagine")
            try:
                g, im, md = _in_processo(str(p), blocco, device, descrivi)
            except Exception as e:
                if not _gpu_piena(e):
                    raise
                print(f"    {p.name}: la GPU non ce l'ha fatta ({type(e).__name__}), questo blocco in CPU",
                      flush=True)
                device = "cpu"
                g, im, md = _in_processo(str(p), blocco, "cpu", descrivi)
            grezzi += g
            markdown.update(md)
            immagini += _scrivi_immagini(dentro, im, len(immagini)) if dentro else im
            del g, im, md
            if len(blocchi) > 1:
                print(f"    {p.name}: pagine {blocco[0]}-{blocco[1]} di {blocchi[-1][1]}"
                      f"{' (GPU)' if device == 'cuda' else ''}", flush=True)
            if progresso and blocco:
                progresso(blocco[1], blocchi[-1][1])
        return unisci(grezzi), immagini, markdown


# ------------------------------------------------------------------ vettori
def _litellm():
    """I modelli locali via llama-swap DIRETTO (litellm tolto il 24/09/2026).
    Il nome e' quello del modello embedding su llama-swap."""
    host = os.environ.get("MODELLI_HOST", "host.docker.internal:1235")
    return (f"http://{host}", {},
            os.environ.get("EMBEDDING_MODELLO", "text-embedding-bge-m3-embeddings"))


def _litellm_visione():
    """Il VLM via llama-swap diretto: `VLM_MODELLO` (es. qwen/qwen3-vl-4b)."""
    host = os.environ.get("MODELLI_HOST", "host.docker.internal:1235")
    return (f"http://{host}",
            {"Content-Type": "application/json"},
            os.environ.get("VLM_MODELLO", "qwen/qwen3-vl-4b"))


def modello_vero():
    """Il nome del modello embedding locale (e' quello che l'indice registra)."""
    return os.environ.get("EMBEDDING_MODELLO", "text-embedding-bge-m3-embeddings")


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


MAX_LATO = 1600          # le immagini di catalogo sono enormi: si riducono una volta per tutte


def _riduci(img):
    """L'immagine ridotta al massimo a MAX_LATO px sul lato lungo, in proporzione.

    Serve a tenere i file leggeri (serving) e i data URL piccoli (vision): una
    foto da 4000 px in base64 sfonderebbe il contesto del modello senza aggiungere
    informazione utile a una domanda.""" 
    img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
    if max(img.size) > MAX_LATO:
        img.thumbnail((MAX_LATO, MAX_LATO))
    return img


def _cartella_sorgenti(percorso_fonte, rel):
    """Dove sta il LAVORATO di UN documento, relativo alla radice delle
    cartelle: `<fonte>/_sorgenti/<hash del documento>`, con dentro

        immagini/    le figure estratte, che l'orchestratore serve in chat
        markdown/    una pagina per file, come l'ha letta il VLM

    Un solo posto che lo decide, perche' lo usano sia chi scrive sia chi
    ripulisce. Il prefisso `_` tiene la cartella fuori dall'indicizzazione.
    Prima del 21/09/2026 era `_immagini/<hash>` con i PNG dentro: i percorsi
    in banca dati cambiano, e si sistemano rileggendo."""
    return f"{percorso_fonte}/_sorgenti/{hashlib.sha256(rel.encode()).hexdigest()[:16]}"


def _butta_sorgenti(percorso_fonte, rel, tieni_markdown=False):
    """Via il lavorato di un documento. Si chiama prima di rifarlo e quando il
    documento esce dall'indice: i nomi delle immagini dipendono da pagina e
    ordine, quindi un PDF con meno figure di prima lascerebbe file orfani — e
    ora che stanno nelle cartelle di lavoro si vedono.

    `tieni_markdown`: si rifanno i pezzi SENZA richiamare il VLM. Trenta minuti
    di lettura per catalogo, contro pochi secondi per rispezzare quello che
    c'e' gia'."""
    import shutil
    base = RADICE / _cartella_sorgenti(percorso_fonte, rel)
    if tieni_markdown:
        shutil.rmtree(base / "immagini", ignore_errors=True)
        return
    shutil.rmtree(base, ignore_errors=True)


def _scrivi_immagini(dentro, immagini, da_indice):
    """Scrive su disco le immagini di UN blocco di pagine e torna i metadati
    [(percorso, pagina, descrizione)]; il percorso e' relativo alla radice delle
    cartelle, lo stesso valore che serve all'orchestratore.

    Blocco per blocco, non a fine file: tenere le immagini in memoria fino
    all'ultima pagina costava 3,3 GB dei 4 del container a meta' di un catalogo
    da 107 pagine (misurato il 20/09/2026), e su un catalogo da 227 MB avrebbe
    fatto morire il servizio.

    Se la condivisione e' in sola lettura (in produzione puo' esserlo) non si
    fallisce il documento: entra senza immagini, con un avviso nel registro."""
    if not immagini:
        return []
    radice = RADICE / dentro / "immagini"
    out = []
    try:
        radice.mkdir(parents=True, exist_ok=True)
        for n, (img, pagina, descr) in enumerate(immagini, start=da_indice):
            nome = f"{pagina or 0}_{n}.png"
            _riduci(img).save(radice / nome)
            out.append((f"{dentro}/immagini/{nome}", pagina, descr))
    except OSError as e:
        print(f"  immagini non salvate in {dentro} ({type(e).__name__}: {e}): "
              f"la cartella e' scrivibile?", flush=True)
        return []
    return out


def _registra_immagini(conn, fid, rel, immagini):
    """Collega nel DB le immagini gia' scritte su disco.

    L'id di un'immagine NON cambia quando il documento si rilegge: le figure
    citate in una conversazione passata restano raggiungibili. Prima si
    cancellava tutto e si reinseriva, e ogni rilettura faceva morire gli URL
    gia' consegnati agli utenti: in chat comparivano i riquadri vuoti
    «immagine 1, immagine 2» e nei log una fila di 404 (visto il 20/09/2026).
    Il percorso e' deterministico (`<pagina>_<n>.png`), quindi serve da chiave:
    l'immagine che torna uguale tiene la sua riga, quella sparita esce."""
    percorsi = [p for p, _pagina, _descr in immagini]
    conn.execute("DELETE FROM immagini WHERE source_id = %s AND documento = %s"
                 " AND NOT (percorso = ANY(%s))", (fid, rel, percorsi))
    # La descrizione serve a SCEGLIERE quale figura mostrare, non solo a
    # cercarla nel testo: il suo vettore vive nello stesso spazio dei pezzi
    # (bge-m3), quindi "sassi rossi" puo' incontrare "dark red lava rocks".
    # Se i vettori non si possono fare (host giu'), le immagini entrano lo
    # stesso: si completano al giro dopo, come i pezzi senza vettore.
    descrizioni = [d for _p, _pagina, d in immagini if d]
    vettori_descr = vettori(descrizioni) if descrizioni else None
    prossimo = iter(vettori_descr) if vettori_descr else None
    for percorso, pagina, descr in immagini:
        v = next(prossimo, None) if (descr and prossimo) else None
        conn.execute("""INSERT INTO immagini (source_id, documento, page, percorso, descrizione, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s::vector)
                        ON CONFLICT (source_id, percorso) DO UPDATE SET page = EXCLUDED.page,
                          documento = EXCLUDED.documento,
                          descrizione = EXCLUDED.descrizione, embedding = EXCLUDED.embedding""",
                     (fid, rel, pagina, percorso, descr, vettore_sql(v) if v else None))
    return len(immagini)


def indicizza_fonte(conn, fonte, lettore, stato_vettori, solo=None, forza=False):
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
    # Cartella che si presenta VUOTA dove prima c'erano documenti: quasi sempre
    # e' la condivisione di rete montata male (il percorso esiste, il contenuto
    # no). Cancellare sarebbe corretto per la regola "il file sparito esce
    # dall'indice", ma svuoterebbe la fonte e l'assistente direbbe a tutti "non
    # trovo documenti". Meglio fermarsi e dirlo: se i file sono stati tolti
    # davvero, si risolve l'anomalia e al giro dopo si allinea.
    if noti and not presenti and not forza:
        anomalia(conn, f"cartella-vuota:{fid}", "errore", f"Cartella vuota ma l'indice ha {len(noti)} documenti: {percorso}",
                 "Controllare che la condivisione sia montata e leggibile. Se i documenti sono stati "
                 "tolti davvero, svuotare l'indice della fonte con: "
                 "docker compose run --rm ingestion python indicizza.py --forza",
                 azienda, f"fonte:{fid}")
        return {"fonte": fid, "errore": f"cartella vuota, {len(noti)} documenti non toccati"}
    chiudi(conn, f"cartella-vuota:{fid}")

    for rel, p in presenti:
        if solo and solo.lower() not in rel.lower():   # --solo non distingue maiuscole
            continue
        st = p.stat()
        quando = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).replace(microsecond=0)
        vecchio = noti.get(rel)
        # Stesso file di prima: non si rilegge, nemmeno se era illeggibile (si
        # riprova quando cambia: riprovarlo a ogni giro non lo aggiusta).
        # Con --forza si rilegge lo stesso: chi lo chiede vuole proprio quello,
        # di solito su un file che era andato in errore o rimandato.
        if not forza and vecchio and vecchio[2] == st.st_size and vecchio[3] == quando:
            conteggi["uguali"] += 1
            continue
        grosso = rimanda(st.st_size, lettore.finestra, gpu=False)   # grosso e fuori finestra
        if grosso and dove_leggere() != "cuda":
            # Non si segna niente in documenti: il file resta "non ancora letto"
            # e al primo giro utile (GPU libera, o finestra) entra come gli altri.
            conteggi["rimandati"] = conteggi.get("rimandati", 0) + 1
            print(f"  rimandato: {fid}/{rel} ({st.st_size // (1024 * 1024)} MB, GPU occupata)")
            continue
        impronta = impronta_file(p)
        if not forza and vecchio and vecchio[1] == impronta:
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
                conn.execute("DELETE FROM immagini WHERE source_id = %s AND documento = %s", (fid, rel))
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
        conn.execute("""INSERT INTO documenti (source_id, documento, impronta, dimensione, modificato_il, stato, errore, pezzi, in_lettura)
                        VALUES (%s,%s,%s,%s,%s,'errore',%s,0,now())
                        ON CONFLICT (source_id, documento) DO UPDATE SET impronta = EXCLUDED.impronta,
                          dimensione = EXCLUDED.dimensione, modificato_il = EXCLUDED.modificato_il,
                          stato = 'errore', errore = EXCLUDED.errore, in_lettura = now(), indicizzato_il = now()""",
                     (fid, rel, impronta, st.st_size, quando, IN_LETTURA))
        def _progresso(fatte, totali):
            """Il pannello fonti legge pagine_fatte/pagine_totali mentre il file gira."""
            conn.execute("UPDATE documenti SET pagine_fatte = %s, pagine_totali = %s"
                         " WHERE source_id = %s AND documento = %s", (fatte, totali, fid, rel))
        def _progresso_figure(fatte, totali):
            """Le descrizioni delle figure corrono a barra pagine gia' al 100%:
            si tengono su colonne proprie, cosi' il pannello le vede muovere."""
            conn.execute("UPDATE documenti SET figure_fatte = %s, figure_totali = %s"
                         " WHERE source_id = %s AND documento = %s", (fatte, totali, fid, rel))
        # Grosso e fuori finestra: si legge finche' la GPU e' libera. Se la
        # perde a meta' non si insiste in CPU (ore di macchina occupata mentre
        # qualcuno chatta): si rimette il file come stava e si riprende dopo.
        # Si riparte pulito: le immagini si scrivono blocco per blocco, quindi
        # i residui della lettura precedente (o di una interrotta) vanno tolti
        # PRIMA, non a fine file.
        # Il Markdown del VLM si butta quando cambia il DOCUMENTO, non quando
        # si rilegge: e' il risultato costoso (~20 minuti a catalogo) e se il
        # PDF e' lo stesso vale ancora. Le immagini invece si rifanno sempre,
        # che costano poco e hanno nomi dipendenti dall'ordine.
        #
        # Prima dipendeva da TIENI_MARKDOWN, cioe' da una variabile da
        # ricordarsi: il 22/09/2026 un --forza ha ributtato tutte e 105 le
        # pagine gia' lette e le ha richieste al modello da capo. Il
        # comportamento giusto non si chiede a chi lancia il comando, si deduce
        # dall'impronta.
        stesso_documento = bool(vecchio) and vecchio[1] == impronta
        _butta_sorgenti(percorso, rel, tieni_markdown=stesso_documento and not TIENI_MARKDOWN_NO)
        IN_CORSO.update(fid=fid, rel=rel, stato=vecchio[4] if vecchio else None)
        try:
            pezzi, immagini = lettore.pezzi(p, _progresso, solo_gpu=grosso,
                                            dentro=_cartella_sorgenti(percorso, rel),
                                            lettura=come_leggere(fid),
                                            progresso_figure=_progresso_figure)
        except Rimandato as e:
            if vecchio:
                conn.execute("UPDATE documenti SET impronta = %s, dimensione = %s, modificato_il = %s,"
                             " stato = %s, errore = NULL, in_lettura = NULL, pagine_fatte = 0,"
                             " pagine_totali = NULL, figure_fatte = 0, figure_totali = NULL"
                             " WHERE source_id = %s AND documento = %s",
                             (vecchio[1], vecchio[2], vecchio[3], vecchio[4], fid, rel))
            else:
                conn.execute("DELETE FROM documenti WHERE source_id = %s AND documento = %s", (fid, rel))
            conteggi["rimandati"] = conteggi.get("rimandati", 0) + 1
            print(f"  rimandato: {fid}/{rel} ({e})")
            IN_CORSO.clear()
            continue
        except Exception as e:
            conteggi["errori"] += 1
            print(f"  ERRORE {fid}/{rel}: {type(e).__name__}: {e}")
            with conn.transaction():
                conn.execute("DELETE FROM chunks WHERE source_id = %s AND documento = %s", (fid, rel))
                conn.execute("""INSERT INTO documenti (source_id, documento, impronta, dimensione, modificato_il, stato, errore, pezzi)
                                VALUES (%s,%s,%s,%s,%s,'errore',%s,0)
                                ON CONFLICT (source_id, documento) DO UPDATE SET impronta = EXCLUDED.impronta,
                                  dimensione = EXCLUDED.dimensione, modificato_il = EXCLUDED.modificato_il,
                                  stato = 'errore', errore = EXCLUDED.errore, pezzi = 0,
                                  in_lettura = NULL, indicizzato_il = now()""",
                             (fid, rel, impronta, st.st_size, quando, f"{type(e).__name__}: {e}"[:500]))
                IN_CORSO.clear()
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
            _registra_immagini(conn, fid, rel, immagini)
            conn.execute("""INSERT INTO documenti (source_id, documento, impronta, dimensione, modificato_il, stato, errore, pezzi)
                            VALUES (%s,%s,%s,%s,%s,%s,NULL,%s)
                            ON CONFLICT (source_id, documento) DO UPDATE SET impronta = EXCLUDED.impronta,
                              dimensione = EXCLUDED.dimensione, modificato_il = EXCLUDED.modificato_il,
                              stato = EXCLUDED.stato, errore = NULL, pezzi = EXCLUDED.pezzi,
                              in_lettura = NULL, indicizzato_il = now()""",
                         (fid, rel, impronta, st.st_size, quando, "indicizzato" if pezzi else "vuoto", len(pezzi)))
            chiudi(conn, chiave)
        IN_CORSO.clear()        # letto: da qui in poi un riavvio non lo riguarda
        conteggi["cambiati" if vecchio else "nuovi"] += 1
        print(f"  {'aggiornato' if vecchio else 'nuovo'}: {fid}/{rel} ({len(pezzi)} pezzi, {len(immagini)} immagini"
              f"{'' if vett else ', senza vettori'})")

    # Cancellati dalla cartella: via dall'indice.
    for rel in set(noti) - {r for r, _ in presenti}:
        with conn.transaction():
            conn.execute("DELETE FROM chunks WHERE source_id = %s AND documento = %s", (fid, rel))
            conn.execute("DELETE FROM immagini WHERE source_id = %s AND documento = %s", (fid, rel))
            conn.execute("DELETE FROM documenti WHERE source_id = %s AND documento = %s", (fid, rel))
            chiudi(conn, f"illeggibile:{fid}:{rel}")
        _butta_sorgenti(percorso, rel)        # tutto: il documento non c'e' piu'
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
    """Pezzi e immagini entrati senza vettore (host giu' al giro prima)."""
    if not stato_vettori["ok"]:
        return 0
    fatti = 0
    while True:
        righe = conn.execute("""SELECT c.id, c.content FROM chunks c JOIN sources s ON s.id = c.source_id
                                 WHERE c.embedding IS NULL AND s.provenienza = 'cartella'
                                 ORDER BY c.id LIMIT 64""").fetchall()
        tabella = "chunks"
        if not righe:
            righe = conn.execute("""SELECT i.id, i.descrizione FROM immagini i JOIN sources s ON s.id = i.source_id
                                     WHERE i.embedding IS NULL AND i.descrizione <> ''
                                       AND s.provenienza = 'cartella'
                                     ORDER BY i.id LIMIT 64""").fetchall()
            tabella = "immagini"
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
            for (rid, _), v in zip(righe, vett):
                if tabella == "chunks":
                    conn.execute("UPDATE chunks SET embedding = %s::vector, updated_at = now() WHERE id = %s",
                                 (vettore_sql(v), rid))
                else:
                    conn.execute("UPDATE immagini SET embedding = %s::vector WHERE id = %s",
                                 (vettore_sql(v), rid))
        fatti += len(righe)


BLOCCO = 7_310_061      # pg_advisory_lock: un giro alla volta, in tutto il database
# Preso SOLO finche' il modello di chat e' scaricato per fare spazio a Docling.
# L'orchestratore lo legge in pg_locks e risponde "sto aggiornando l'indice":
# senza, la prima domanda farebbe ricaricare i 13 GB a meta' lettura. Il lock e'
# della connessione, quindi se questo servizio muore si libera da solo e
# l'assistente riparte: nessun flag appeso in una tabella.
BLOCCO_LLM = 7_310_062


def conta_lessemi(conn, esiti=None):
    """Riscrive `lessemi`: in quanti pezzi compare ogni parola dell'archivio.

    Serve al ramo lessicale della ricerca, che pesa i termini per quanto sono
    rari (recupero.py). Senza questo conteggio una parola nuova risulta
    sconosciuta e viene trattata come rarissima: non e' un guasto — e' il
    valore prudente — ma le parole diventate comuni resterebbero sopravvalutate.

    Si fa QUI e non a ogni ricerca perche' l'archivio cambia solo qui, e
    contarlo vuol dire rileggere tutto l'indice: un secondo su 3112 pezzi,
    inaccettabile moltiplicato per ogni domanda.

    Si salta se il giro non ha cambiato niente: su quattro cartelle ferme sono
    288 riletture inutili dell'indice al giorno.
    """
    if esiti is not None and not any(e.get("nuovi") or e.get("cambiati") or e.get("tolti")
                                     for e in esiti):
        return
    try:
        with conn.transaction():
            conn.execute("TRUNCATE lessemi")
            conn.execute(
                "INSERT INTO lessemi (parola, pezzi) "
                "SELECT word, ndoc FROM ts_stat($$SELECT to_tsvector('italian', content) "
                "FROM chunks$$) ON CONFLICT (parola) DO UPDATE SET pezzi = EXCLUDED.pezzi")
            conn.execute("UPDATE lessemi_stato SET pezzi_totali = (SELECT count(*) FROM chunks),"
                         " aggiornato_il = now()")
        n = conn.execute("SELECT count(*) FROM lessemi").fetchone()[0]
        print(f"  frequenze delle parole: {n} lessemi", flush=True)
    except psycopg.Error as e:
        # Un conteggio vecchio fa cercare un po' peggio; fallire il giro
        # perderebbe tutto il lavoro di lettura che c'e' appena stato.
        print(f"  frequenze delle parole non aggiornate ({type(e).__name__}: {e})", flush=True)


def giro(aspetta=False, forza=False, solo=None):
    """Un giro su tutte le cartelle. Uno solo alla volta (servizio e giri a
    mano insieme leggerebbero due volte gli stessi file): il servizio salta il
    giro se un altro e' in corso, il giro a mano (aspetta=True) lo attende.
    Il blocco si libera da solo se il processo muore (e' della connessione).

    forza=True (--forza): si comporta come se fosse dentro la finestra notturna
    anche alle tre del pomeriggio — legge i file grossi e, se serve, scarica il
    modello di chat. Lo chiede una persona, non lo decide il servizio.
    solo: legge i file il cui percorso contiene questa stringa."""
    stato_vettori = {"ok": True, "verificato": False}
    finestra = forza or in_finestra()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        lettore = Lettore(conn, finestra)
        if aspetta:
            conn.execute("SELECT pg_advisory_lock(%s)", (BLOCCO,))
        elif not conn.execute("SELECT pg_try_advisory_lock(%s)", (BLOCCO,)).fetchone()[0]:
            return None, 0
        # Residui di un giro morto a meta': un file segnato "in lettura" che non
        # e' piu' in lettura. Uno solo girerebbe senza azzerarli (il processo
        # che li ha scritti se n'e' andato): alla prossima lettura valida.
        conn.execute("UPDATE documenti SET in_lettura = NULL, pagine_fatte = 0, pagine_totali = NULL,"
                     " figure_fatte = 0, figure_totali = NULL"
                     " WHERE in_lettura IS NOT NULL")
        fonti = [f for f in conn.execute(
            """SELECT id, percorso, aziende FROM sources
                WHERE provenienza = 'cartella' AND stato IN ('attiva', 'attesa') ORDER BY id""").fetchall()
                 # Le fonti di esempio hanno percorsi \\server\...: non sono cartelle montate qui.
                 if PERCORSO_VALIDO.match(f[1]) and ".." not in f[1]]
        esiti = [indicizza_fonte(conn, f, lettore, stato_vettori, solo, forza) for f in fonti]
        completati = completa_vettori(conn, stato_vettori)
        conta_lessemi(conn, esiti)
    del lettore
    gc.collect()
    return esiti, completati


def main():
    signal.signal(signal.SIGTERM, _riavvio_voluto)
    una_volta = "--una-volta" in sys.argv or "--forza" in sys.argv
    forza = "--forza" in sys.argv
    solo = sys.argv[sys.argv.index("--solo") + 1] if "--solo" in sys.argv else None
    while True:
        inizio = time.time()
        cambi, completati = [], 0
        try:
            esiti, completati = giro(aspetta=una_volta, forza=forza, solo=solo)
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
        # Se il giro ha fatto qualcosa si riparte SUBITO: con la GPU libera
        # l'indicizzazione deve stare al passo di chi deposita i documenti, non
        # leggere un file e poi dormire cinque minuti. Si aspetta solo quando
        # non c'e' niente da fare — compresi i giri in cui l'unica cosa
        # successa e' aver rimandato file grossi (la GPU e' occupata: insistere
        # ogni secondo non la libera).
        if not (cambi or completati):
            time.sleep(INTERVALLO)


# ====================================================================== pagina intera
#
# Percorso alternativo alla lettura di Docling, per i documenti a GRIGLIA
# (cataloghi, listini). Misurato il 21/09/2026 su EUROSAND, pagine 7 e 76:
# Docling trova ZERO tabelle e i titoli o mancano (pagina 7) o sono invertiti
# — il codice articolo diventa titolo e il valore contenuto (pagina 76). La
# griglia di un catalogo e' visiva, non una tabella con le righe disegnate, e
# il modello di layout non la vede.
#
# Qui la pagina si rende a immagine e la legge il VLM, che restituisce Markdown
# con i titoli veri e gli articoli uno per riga. Nessuna euristica sul testo:
# la struttura la dichiara il modello guardando la pagina, come farebbe una
# persona. Sulla pagina 7 escono nello stesso colpo il titolo «DEKOSTEINE
# 9-13 mm», i formati «E5500 5,5 l € 13,80» e i colori «DST2001 rot» — le tre
# cose che mancavano alle domande vere.

LETTURA = os.environ.get("LETTURA", "docling")     # docling | pagina
# Le fonti che si leggono a PAGINA invece che con Docling, separate da virgola
# (id della fonte, come in `sources`). Non e' globale di proposito: misurato il
# 22/09/2026, sui cataloghi il percorso a due sguardi porta il recupero da
# 10/20 a 17/20, ma sui documenti di PROSA — policy, procedure — Docling da
# solo fa gia' 4/4, e il prompt «catalogo» li' imporrebbe una griglia che non
# c'e' (sulle pagine non-prodotto di EUROSAND produceva tabelle vuote).
# Questa variabile e' la D16 in piccolo: quando ci sara' l'impostazione per
# fonte nel pannello, sparisce e il valore arriva da `sources`.
FONTI_A_PAGINA = {x.strip() for x in os.environ.get("LETTURA_PAGINA", "").split(",") if x.strip()}
# Nel percorso a pagina, il TESTO di Docling serve ancora? Le sue descrizioni
# delle figure ora le scriviamo noi, per pagina e con il titolo del prodotto;
# quello che resta del suo contributo e' l'OCR e i frammenti che il VLM
# potrebbe aver saltato. Con `no` Docling fa SOLO l'estrazione delle immagini.
# Da decidere con le 24 domande vere, non a naso: il riferimento e' 17/20.
TESTO_DOCLING = os.environ.get("TESTO_DOCLING", "si") != "no"


def come_leggere(fonte: str) -> str:
    """`pagina` per le fonti dichiarate a griglia, altrimenti quello che dice
    LETTURA. Un solo posto che lo decide."""
    return "pagina" if fonte in FONTI_A_PAGINA else LETTURA
DPI_PAGINA = int(os.environ.get("DPI_PAGINA", "150"))
# Il Markdown del VLM si tiene finche' il documento e' lo stesso: e' il
# risultato costoso (~20 minuti a catalogo) e non dipende da come spezziamo i
# pezzi. Si butta da solo quando cambia il PDF (impronta diversa) o quando
# cambiano le istruzioni al modello (l'impronta del prompt e' nel nome della
# cartella). Questa variabile serve solo a forzarne la rilettura a mano, per
# esempio se si sospetta che il modello abbia letto male.
TIENI_MARKDOWN_NO = os.environ.get("RILEGGI_MARKDOWN", "") == "1"

ISTRUZIONI_PAGINA = (
    "Read this page and report its content faithfully, in Markdown.\n"
    "\n"
    "RULES:\n"
    "- Transcribe the text you see, in every language present. Do NOT translate, "
    "do NOT summarise, do NOT reinterpret.\n"
    "- If the page contains a real TABLE, write it as a Markdown table with its "
    "own columns and rows. If there is no table, do NOT invent one.\n"
    "- If the page is a photo, a scan, or an image with no readable text, write "
    "«Immagine» on one line and then ONE short description of what is visible "
    "(objects, colours, material). Do NOT invent text, codes, numbers, prices.\n"
    "- If a text repeats many times (for example a repeated greeting or slogan), "
    "write it ONCE. Never repeat the same line.\n"
    "- Never invent anything that is not visible: no codes, no prices, no rows, "
    "no numbers.\n"
    "- Keep the answer as long as the page requires, no longer. Stop when you "
    "have reported everything once.\n"
)
# Il Markdown salvato dipende da QUESTE istruzioni: se cambiano, i file
# vecchi sono di un'altra forma e rileggerli darebbe pezzi incoerenti con i
# nuovi. L'impronta entra nel nome della cartella, cosi' un prompt diverso
# rilegge da solo e i file vecchi restano li' per il confronto.
IMPRONTA_PROMPT = hashlib.sha256(ISTRUZIONI_PAGINA.encode()).hexdigest()[:8]


# Le istruzioni per descrivere UNA figura, con il titolo della pagina da cui
# viene. Il titolo serve a capire COSA sono gli oggetti: senza, un ritaglio di
# 221x149 px di sassi rossi diventa «possibly dried fruit or processed food»
# (misurato il 22/09/2026).
ISTRUZIONI_FIGURA = (
    "This image is a detail from a product catalogue page titled: «{titolo}».\n"
    "\n"
    "Describe the product using ONLY what you can actually see, in short lines:\n"
    "- object: what it is\n"
    "- material: what it is made of (only if visible or readable)\n"
    "- shape/size: width, height, shape, texture, edges, any visible detail\n"
    "\n"
    "Then write «Colours: » followed by the DISTINCT colours visible, comma-separated. "
    "Group similar shades under one name (for example 'light blue' and 'sky blue' are "
    "both 'blue'). List each colour exactly once.\n"
    "If the image shows a single item with no colour variation, write «Colours: n/a».\n"
    "\n"
    "Only if there is a short PRODUCT CODE or product name printed near the object, "
    "write it as «Code: ». Do NOT transcribe addresses, phone numbers, or long text.\n"
    "Never invent anything that is not visible. Do not repeat. Keep the answer short."
)
SECONDI_PER_FIGURA = int(os.environ.get("SECONDI_PER_FIGURA", "120"))


def rispetta_la_forma(md: str) -> bool:
    """Il modello ha seguito le istruzioni? Non si giudica il CONTENUTO — non
    sapremmo — si controlla il CONTRATTO, che e' oggettivo: `<br>` dentro la
    risposta e' vietato esplicitamente, perche' significa piu' valori schiacciati
    in una casella invece che in colonne separate.

    Serve perche' il modello non e' coerente: sulla stessa pagina, a temperatura
    zero, il 21/09/2026 ha prodotto una tabella in una chiamata e righe nude con
    `<br>` nella successiva."""
    return "<br>" not in (md or "")


def _immagine_pagina(percorso, n):
    """La pagina n come PNG in base64. 150 dpi: sotto, i codici articolo
    piccoli si perdono; sopra, l'immagine supera il contesto del VLM."""
    import base64
    import io
    import pypdfium2
    pdf = pypdfium2.PdfDocument(str(percorso))
    try:
        img = pdf[n - 1].render(scale=DPI_PAGINA / 72).to_pil()
    finally:
        pdf.close()
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _markdown_pagina(percorso, n, dentro):
    """Il Markdown della pagina n, dal VLM. Si SALVA su disco
    (`<sorgenti>/markdown/NNN.md`) e al giro dopo si rilegge da li'.

    Salvarlo non e' cautela: e' il risultato costoso. Trenta minuti di VLM per
    catalogo. Se cambia il modo di spezzare i pezzi — ed e' cambiato tre volte
    il 21/09/2026 — si riscrive l'indice senza rileggere le pagine. Ed e'
    leggibile da una persona: quando un prezzo sara' sbagliato si apre il file
    invece di dedurlo."""
    import json
    import urllib.request
    fuori = RADICE / dentro / f"markdown-{IMPRONTA_PROMPT}" / f"{n:04d}.md"
    if fuori.is_file():
        return fuori.read_text(encoding="utf-8")
    b64 = _immagine_pagina(percorso, n)

    def chiedi(istruzioni):
        corpo = json.dumps({
            "model": VLM_MODELLO, "max_tokens": 3000, "temperature": 0,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": istruzioni},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}],
        }).encode()
        req = urllib.request.Request(f"{VLM_URL.rstrip('/')}/chat/completions", data=corpo,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=SECONDI_PER_BLOCCO) as r:
            return json.load(r)["choices"][0]["message"]["content"]

    md = chiedi(ISTRUZIONI_PAGINA)
    if not rispetta_la_forma(md):
        # Il modello non e' coerente fra una chiamata e l'altra: sulla STESSA
        # pagina 7 ha prodotto una volta una tabella e una volta righe nude con
        # `<br>` dentro (21/09/2026). Righe nude vuol dire ventisei articoli in
        # un pezzo solo, cioe' il difetto da cui siamo partiti.
        # Non si indovina cosa intendeva: si richiede la forma. La violazione e'
        # oggettiva — `<br>` e' vietato dalle istruzioni — quindi non serve
        # nessuna soglia ne' ipotesi sul contenuto.
        print(f"    pagina {n}: forma non rispettata, richiedo", flush=True)
        md = chiedi(ISTRUZIONI_PAGINA + "\nATTENZIONE: la risposta precedente conteneva `<br>`. "
                                        "Ogni valore in una COLONNA sua, un articolo per riga.")
    try:
        fuori.parent.mkdir(parents=True, exist_ok=True)
        fuori.write_text(md, encoding="utf-8")
    except OSError as e:      # sola lettura: si legge lo stesso, senza cache
        print(f"  markdown non salvato ({type(e).__name__}: {e})", flush=True)
    return md


def _righe_di_tabella(righe, titolo, pagina):
    """Una tabella Markdown -> pezzi. Se sta in un pezzo solo resta intera:
    «quali formati offrite» vuole vedere tutti i formati insieme. Se e' lunga
    si spezza per RIGA, ognuna con il titolo e l'intestazione davanti —
    altrimenti il vettore di venti articoli non significa nessun articolo."""
    intero = "\n".join(righe)
    if len(intero) <= MAX_PEZZO:
        return [(f"{titolo}\n{intero}" if titolo else intero, pagina)]
    intestazione = righe[0] if righe else ""
    out = []
    for r in righe[1:]:
        if set(r.replace("|", "").strip()) <= set("-: "):     # riga di separazione
            continue
        davanti = " | ".join(x for x in (titolo, intestazione) if x)
        out.append((f"{davanti} | {r}" if davanti else r, pagina))
    return out


def _pezzi_da_markdown(md, pagina):
    """Markdown -> [(testo, pagina)], seguendo la STRUTTURA invece di
    indovinarla: le intestazioni fanno da contesto, le voci di elenco e le
    righe di tabella diventano pezzi distinti con quel contesto davanti.

    E' la versione generica di quello che prima faceva un'espressione regolare
    sul codice articolo — che funzionava su un catalogo e si rompeva sul
    successivo (21/09/2026: i codici a una lettera dei formati non li vedeva)."""
    fuori, blocco, titolo = [], [], ""

    def chiudi():
        testo = "\n".join(blocco).strip()
        blocco.clear()
        if testo:
            fuori.append((f"{titolo}\n{testo}" if titolo else testo, pagina))

    righe = md.splitlines()
    i = 0
    while i < len(righe):
        r = righe[i].rstrip()
        nuda = r.strip()
        if nuda.startswith("```") or nuda in ("---", "***", "___"):
            i += 1
            continue
        if nuda.startswith("#"):
            chiudi()
            titolo = nuda.lstrip("#").strip()
            i += 1
            # Le righe attaccate sotto il titolo ne fanno parte: le traduzioni
            # del nome e le misure ("deco rocks | pierres decoratives", "9-13 mm").
            while i < len(righe) and righe[i].strip() and not righe[i].lstrip().startswith(("#", "|", "-", "*")):
                titolo += " " + righe[i].strip()
                i += 1
            continue
        if nuda.startswith("|"):
            chiudi()
            tabella = []
            while i < len(righe) and righe[i].lstrip().startswith("|"):
                tabella.append(righe[i].strip())
                i += 1
            fuori.extend(_righe_di_tabella(tabella, titolo, pagina))
            continue
        if nuda.startswith(("- ", "* ")):
            chiudi()
            voce = nuda[2:].strip()
            if voce:
                fuori.append((f"{titolo} | {voce}" if titolo else voce, pagina))
            i += 1
            continue
        blocco.append(r)
        i += 1
    chiudi()
    return fuori


SEGNAPOSTO = "<!-- image -->"
# Quanto testo PRIMA del segnaposto si tiene.
INTORNO = 120


def attorno_ai_segnaposti(markdown: str) -> list:
    """Il testo che PRECEDE ogni segnaposto, uno per segnaposto, in ordine.

    Serve a dare un'identita' alla figura. Il modello visivo descrive un
    RITAGLIO, e nel ritaglio il codice articolo c'e' solo se il layout della
    pagina ce l'ha messo dentro: misurato il 22/09/2026, le descrizioni di
    EUROSAND contengono FSA1043 e FSA1041 ma non FSA1001, DST2001, RAD1001.
    Chiedendo la figura di FSA1001 uscivano quattro prodotti diversi della
    stessa pagina — la ricerca non sbagliava a cercare, mancava proprio il
    dato.

    Nel Markdown della pagina il codice c'e' sempre, e sta PRIMA della sua
    figura. Su EUROSAND pagina 7:

        Immagine: ...pietre bianche...      <- il ritaglio ha il suo codice
        DST2043 creme cream                 <- codice
        Immagine: ...pietre beige...        <- beige = creme = DST2043
        DST2012 hellgrau light grey         <- codice
        Immagine: ...pietre grigio chiaro...

    Fra un segnaposto e l'altro c'e' esattamente UN codice, quindi la finestra
    va delimitata dal segnaposto precedente: non e' una scelta di stile, e' la
    differenza fra un'etichetta e un'ambiguita'.

    La prima versione prendeva 120 caratteri da ENTRAMBI i lati e si portava
    dietro anche il prodotto successivo. Misurato sulle 890 figure di EUROSAND
    il 22/09/2026:

                              un codice solo   nessuno   piu' di uno
        entrambi i lati              171         520         199
        solo quello che precede      194         643           0

    Piu' figure identificate e zero ambigue: non e' un compromesso, e' un
    difetto che se ne va. Le 199 ambigue sono il motivo per cui, chiedendo
    «sassi rossi», uscivano due figure BLU che si portavano dietro «DST2001
    rot red» dalla riga del prodotto accanto.

    Torna una COPPIA per figura: (prima, dopo). I due lati servono a due cose
    diverse, e per un pezzo della serata ho provato a farli fare dallo stesso
    testo — sbagliando.

        prima   l'ETICHETTA. Delimitata dal segnaposto precedente, quindi
                porta al massimo il codice di UN prodotto. E' quella che si
                mostra sotto la miniatura in chat, dove una parola di troppo
                e' un'affermazione falsa.
        dopo    contesto in piu' per la RICERCA. Su 891 figure di EUROSAND,
                123 hanno il codice solo da questo lato: tagliarlo faceva
                scendere le figure trovate da 11/12 a 10/12 e le pertinenti
                dal 79 al 58 per cento (misurato il 22/09/2026).

    La ricerca vuole recall, la didascalia vuole precisione. Non e' un
    compromesso da trovare: sono due campi, e si tengono separati.
    """
    pezzi = (markdown or "").split(SEGNAPOSTO)
    return [(" ".join(pezzi[i].split())[-INTORNO:],
             " ".join(pezzi[i + 1].split())[:INTORNO])
            for i in range(len(pezzi) - 1)]


def nei_segnaposti(markdown: str, descrizioni: list) -> str:
    """Le descrizioni al posto dei segnaposto, nell'ordine in cui compaiono.

    Cosi' la descrizione di una figura finisce DOVE sta la figura: accanto al
    suo codice articolo, non genericamente dentro la pagina. Sulla pagina 7 di
    EUROSAND il segnaposto e' seguito da «DST2043 creme cream», quindi il
    pezzo che ne esce lega la descrizione al codice giusto.

    Se i conti non tornano — piu' segnaposto che descrizioni o viceversa — si
    sostituisce solo quello che combacia e il resto dei segnaposto sparisce:
    meglio una pagina con qualche descrizione in meno che una con le
    descrizioni attaccate all'articolo sbagliato.
    """
    fuori, resto = [], list(descrizioni)
    for pezzo in (markdown or "").split(SEGNAPOSTO):
        fuori.append(pezzo)
        if resto:
            d = " ".join((resto.pop(0) or "").split())
            fuori.append(f"\n\nImmagine: {d}\n\n" if d else "")
    return "".join(fuori).strip()


def _pezzi_dal_markdown_figure(dentro, markdown, immagini, titoli):
    """Il Markdown di Docling con le descrizioni al posto dei segnaposto,
    salvato per pagina e spezzato dallo stesso chunker della lettura a pagina.

    Un artefatto solo per pagina, leggibile da una persona e identico a quello
    che finisce nell'indice: quando una risposta sara' sbagliata si apre quel
    file invece di dedurre.

    Torna anche le immagini con la descrizione ARRICCHITA del testo che
    circonda il loro segnaposto — il codice articolo, quando nel ritaglio
    non c'e' (vedi attorno_ai_segnaposti).
    """
    per_pagina = {}
    for i, (_percorso, pagina, descr) in enumerate(immagini):
        per_pagina.setdefault(pagina, []).append((i, descr or ""))
    arricchite = list(immagini)
    fuori = []
    for pagina, md in sorted(markdown.items()):
        qui = per_pagina.get(pagina, [])
        completo = nei_segnaposti(md, [d for _, d in qui])
        if not completo:
            continue
        # Il testo attorno al segnaposto si mette DAVANTI alla descrizione:
        # e' quello che identifica la figura, e quel che viene dal modello
        # visivo resta a descriverla. Va nell'indice delle IMMAGINI, non nel
        # Markdown, dove sarebbe la stessa riga scritta due volte.
        for (i, descr), (prima, dopo) in zip(qui, attorno_ai_segnaposti(md)):
            percorso, pag, _ = immagini[i]
            # Tre parti, separatore SEMPRE presente: l'etichetta (che puo'
            # essere vuota), il contesto dopo, e la descrizione del modello.
            # Il separatore e' quello che distingue «non ho trovato
            # un'etichetta» da «l'etichetta e' questa», e la didascalia in chat
            # si regge su quella distinzione.
            # NIENTE strip a sinistra: con l'etichetta vuota il separatore
            # iniziale e' il segno che l'etichetta non c'e'. Toglierlo faceva
            # scambiare il contesto DOPO per l'etichetta.
            unita = f"{prima} — {dopo} — {descr}".rstrip(" ")
            arricchite[i] = (percorso, pag, unita)
        titolo = titoli.get(pagina, "")
        if titolo:
            completo = titolo + '\n\n' + completo
        _salva_markdown(dentro, "figure", pagina, completo)
        fuori += _pezzi_da_markdown(completo, pagina)
    return fuori, arricchite


def _salva_markdown(dentro, quale, pagina, testo):
    """Un file per pagina, accanto a quello del VLM. Non e' un registro: e' il
    materiale che si indicizza, e averlo su disco e' l'unico modo di guardare
    cosa e' stato letto senza rifare la lettura."""
    if not dentro:
        return
    try:
        fuori = RADICE / dentro / f"markdown-{quale}"
        fuori.mkdir(parents=True, exist_ok=True)
        (fuori / f"{pagina:04d}.md").write_text(testo, encoding="utf-8")
    except OSError as e:
        print(f"  markdown {quale} non salvato ({type(e).__name__}: {e})", flush=True)


def _pezzi_dalle_figure(immagini, titoli):
    """Le descrizioni delle figure come pezzi di testo, una per figura.

    Finora ce le metteva il chunker di Docling; da quando le scriviamo noi
    (con il titolo della pagina davanti) le mettiamo noi, altrimenti la prosa
    che descrive le figure sparirebbe dall'indice del testo — ed e' quella che
    fa funzionare le domande descrittive: «ciottoli neri con effetto specchio»
    non combacia con nessun codice articolo.

    Il titolo davanti serve come a ogni altro pezzo: «Immagine:» da sola non
    dice di che prodotto si parla.
    """
    fuori = []
    for _percorso, pagina, descr in immagini:
        if not descr:
            continue
        titolo = titoli.get(pagina or 0, "")
        testo = " ".join(descr.split())
        fuori.append((f"{titolo} | Immagine: {testo}" if titolo else f"Immagine: {testo}", pagina))
    return fuori


def _titoli_di_pagina(dentro):
    """{pagina: titolo} dal Markdown che il VLM ha gia' prodotto per ogni
    pagina. Non costa niente: i file sono gia' sul disco."""
    titoli = {}
    base = RADICE / dentro / f"markdown-{IMPRONTA_PROMPT}"
    if not base.is_dir():
        return titoli
    for f in base.glob("*.md"):
        try:
            n = int(f.stem)
        except ValueError:
            continue
        righe = [r.strip() for r in f.read_text(encoding="utf-8").splitlines()
                 if r.strip() and not r.strip().startswith("```")]
        if righe:
            titoli[n] = " ".join(righe[:3])[:160]
    return titoli


IMPRONTA_FIGURA = hashlib.sha256(ISTRUZIONI_FIGURA.encode()).hexdigest()[:8]


def _descrizioni_salvate(dentro):
    """{nome del file immagine: descrizione} dal giro precedente.

    Le descrizioni sono il COSTO di un'indicizzazione: 891 chiamate al modello
    visivo su EUROSAND, mezz'ora. Il Markdown delle pagine era gia' in cache
    per la stessa ragione; le descrizioni no, e il 22/09/2026 le ho ripagate
    TRE volte per tre modifiche che non le toccavano — l'ultima solo per
    cambiare il testo che si mette davanti.

    La chiave e' il nome del file (`0007_12.png`), che dipende da pagina e
    ordine: finche' il PDF e' lo stesso, la stessa figura ha lo stesso nome.
    L'impronta nel nome del file e' quella del PROMPT: cambiarlo invalida
    tutto, come per il Markdown.
    """
    if not dentro:
        return {}
    f = RADICE / dentro / f"descrizioni-{IMPRONTA_FIGURA}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except (OSError, ValueError) as e:
        print(f"    descrizioni salvate non rilette ({type(e).__name__}: {e})", flush=True)
        return {}


def _salva_descrizioni(dentro, mappa):
    if not dentro or not mappa:
        return
    try:
        f = RADICE / dentro / f"descrizioni-{IMPRONTA_FIGURA}.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(mappa, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        print(f"    descrizioni non salvate ({type(e).__name__}: {e})", flush=True)


def _descrivi_col_titolo(immagini, titoli, dentro=None, progresso=None):
    """Le figure descritte da NOI, dicendo al modello da che pagina vengono.

    Un ritaglio di 221x149 px senza contesto inganna: il 22/09/2026 un primo
    piano di sassi rossi e' stato descritto come «possibly dried fruit or
    processed food». Con il titolo della pagina davanti — «DEKOSTEINE deco
    rocks | pietre decorative 9 - 13 mm» — lo stesso ritaglio diventa
    «reddish-brown decorative stones, approximately 9-13 mm». Stesso modello,
    stessa immagine, stesso numero di chiamate: cambia solo che sa cosa sta
    guardando.

    Il titolo serve a capire COSA sono gli oggetti, non a inventare quello che
    non si vede: e' scritto nelle istruzioni. Se il modello non risponde, la
    figura resta senza descrizione e il documento entra lo stesso.
    """
    import base64
    import json
    import urllib.request
    if VLM_DESCRIZIONI != "api":
        return immagini
    # La descrizione passa da LiteLLM (rotta `visione`), non da VLM_URL/llama-swap.
    url, testa, logico = _litellm_visione()
    salvate = _descrizioni_salvate(dentro)
    fuori, falliti, riusate, fatte = [], 0, 0, 0
    for percorso, pagina, vecchia in immagini:
        fatte += 1
        if progresso:
            progresso(fatte, len(immagini))
        nome = pathlib.PurePath(percorso).name
        if nome in salvate:
            riusate += 1
            fuori.append((percorso, pagina, salvate[nome]))
            continue
        # ATTENZIONE: qui le figure sono gia' SU DISCO. _scrivi_immagini le ha
        # salvate blocco per blocco e ha sostituito l'immagine con il suo
        # percorso — tenerle in memoria fino a fine documento costava 3,3 GB su
        # un catalogo da 107 pagine. Trattarle come immagini PIL fallirebbe
        # dentro la `except` qui sotto, e sembrerebbe che non risponda il
        # modello (quasi successo il 22/09/2026).
        titolo = titoli.get(pagina or 0)
        if not titolo:
            fuori.append((percorso, pagina, vecchia))
            continue
        try:
            b64 = base64.b64encode((RADICE / percorso).read_bytes()).decode()
            corpo = json.dumps({
                "model": logico, "max_tokens": 160, "temperature": 0,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": ISTRUZIONI_FIGURA.format(titolo=titolo)},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}],
            }).encode()
            req = urllib.request.Request(f"{url}/v1/chat/completions",
                                         data=corpo, headers=testa, method="POST")
            with urllib.request.urlopen(req, timeout=SECONDI_PER_FIGURA) as r:
                descr = json.load(r)["choices"][0]["message"]["content"].strip()
            salvate[nome] = descr or vecchia
            fuori.append((percorso, pagina, descr or vecchia))
        except Exception as e:
            falliti += 1
            if falliti == 1:      # il primo con il motivo, gli altri solo contati
                print(f"    figura non descritta ({type(e).__name__}: {e})", flush=True)
            fuori.append((percorso, pagina, vecchia))
    if riusate:
        print(f"    {riusate} descrizioni riprese da disco (niente VLM)", flush=True)
    if falliti:
        print(f"    {falliti} figure senza descrizione (il modello non ha risposto)", flush=True)
    # Tutte fallite (e ce n'erano) = VLM giu', non figure difficili: senza
    # queste descrizioni il documento entrerebbe dimezzato e marcato «fatto».
    # Come per le pagine, meglio rimandare: al giro con la VLM su si rivede.
    if immagini and falliti == len(immagini):
        raise Rimandato(f"VLM non raggiungibile: {falliti} figure senza descrizione")
    _salva_descrizioni(dentro, salvate)
    return fuori


if __name__ == "__main__":
    main()
