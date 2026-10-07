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
di calcolo si leggono come qualsiasi altro testo: su un catalogo di fornitore il
prezzo e' contenuto legittimo, e chi vede la pagina vede anche quello.

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
# Quante PAGINE descrivere per giro. Sono 1.901 in archivio e ognuna e'
# una chiamata al modello: un tetto per giro tiene l'indicizzazione
# reattiva e lascia che l'indice si riempia nei giri successivi.
INDICE_PAGINE = int(os.environ.get("INDICE_PAGINE", "200"))
INTERVALLO = int(os.environ.get("INTERVALLO", "300"))
LOTTO_VETTORI = 16
DOCLING = {".pdf", ".docx", ".pptx", ".html", ".htm", ".md", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
TESTO = {".txt"}
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
# Un errore che dipende da FUORI — il modello irraggiungibile, la rete che
# salta, un blocco scaduto — non si risolve aspettando che il file cambi: il
# file e' identico, e' cambiato il mondo. La regola "l'impronta decide", da
# sola, li rendeva DEFINITIVI: il 27/09/2026, 14 errori su 20 erano
# `ConnectError: Name or service not known` e nessuno avrebbe piu' riprovato
# senza un intervento. Si ritentano, ma non a ogni giro: un timeout da 600
# secondi ripreso ogni 5 minuti mangerebbe la GPU senza finire mai.
RITENTO_ORE = int(os.environ.get("RITENTO_ORE", "6"))
# Eccezioni che valgono un nuovo tentativo. Quello che manca e' apposta: un
# difetto nostro (ValueError) o un file non leggibile (Word 97-2003) darebbero
# lo stesso identico errore, e riprovarlo costerebbe la GPU per niente.
ERRORI_DA_RITENTARE = (
    "ConnectError", "ConnectionError", "APIConnectionError", "ReadTimeout",
    "Timeout", "HTTPError", "ServiceUnavailable", "MemoryError",
    "FileNotFoundError",
)
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


def _converti(percorso, blocco, device="cpu", descrivi=True, dentro=None):
    """Converte UN blocco di pagine (o un file intero) con Docling e restituisce
    [(testo, pagina)] e le immagini [(PIL.Image, pagina)]. Gira in un processo a
    parte che muore subito dopo: Docling non restituisce la memoria fra un
    blocco e l'altro (misurato: da 1,3 a oltre 6 GB, swap compreso, su un PDF di
    266 pagine), e un processo che finisce la restituisce tutta. Vale anche per
    la VRAM: il contesto CUDA se ne va con il processo, quindi fra un blocco e
    l'altro la GPU torna libera per chi sta chattando.

    Il documento STRUTTURATO (testo, etichette, tabelle, figure) si salva anche
    come JSON su disco, accanto al markdown: e' tutto quello che Docling ha
    tirato fuori, e rileggerlo non costa una riconversione."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.settings import settings
    from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
    settings.perf.page_batch_size = 1          # una pagina alla volta in memoria
    opzioni = _opzioni_pdf(device, descrivi)
    conv = DocumentConverter(format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=opzioni),
        InputFormat.IMAGE: ImageFormatOption(pipeline_options=opzioni)})
    ris = conv.convert(percorso, page_range=blocco) if blocco else conv.convert(percorso)
    if dentro:
        _salva_docling_json(ris.document, dentro, blocco)
    return _chunk(ris.document), _immagini(ris.document), _markdown_per_pagina(ris.document, blocco)


def _salva_docling_json(documento, dentro, blocco):
    """Il DoclingDocument intero, in JSON, su disco. Un file per blocco di pagine
    (il documento nasce a blocchi per tenere bassa la memoria)."""
    import json as _json
    nome = f"docling-{blocco[0]:04d}-{blocco[1]:04d}.json" if blocco else "docling.json"
    try:
        fuori = RADICE / dentro / nome
        fuori.parent.mkdir(parents=True, exist_ok=True)
        fuori.write_text(_json.dumps(documento.export_to_dict(), ensure_ascii=False),
                         encoding="utf-8")
    except Exception as e:
        print(f"  docling.json non salvato ({type(e).__name__}: {e})", flush=True)


def _markdown_per_pagina(documento, blocco):
    """{pagina: TUTTO il testo che Docling ha estratto}, caption comprese.

    `export_to_markdown` butta le didascalie (label 'caption') trasformandole in
    segnaposto immagine: su IPURO pagina 4 i FORMATI («Room fragrance 240 ml»,
    «Scented candle 270 g», «Refill 240 ml», «Sticks 240 ml») sparivano, e sono
    proprio cio' che un buyer chiede. Qui si prende OGNI item di testo, qualunque
    etichetta, e le tabelle come Markdown: quello che esce dal documento finisce
    nel testo, senza scarti.
    """
    fuori = {}
    per_pagina = {}
    # I formati SENZA pagine (md, docx, html) arrivano da Docling senza `prov`:
    # tutto il loro testo sta sulla pagina 0, «documento senza pagine». Prima
    # si scartava, e un .md o un .docx entrava «vuoto» (visto il 04/10/2026).
    for item in documento.texts:
        pag = item.prov[0].page_no if item.prov else 0
        lab, testo = item.label.value, item.text
        if lab == "section_header":
            per_pagina.setdefault(pag, []).append(f"## {testo}")
        elif lab == "title":
            per_pagina.setdefault(pag, []).append(f"# {testo}")
        elif lab == "list_item":
            per_pagina.setdefault(pag, []).append(f"- {testo}")
        else:
            # caption, text, page_footer, formula...: il testo, punto. La
            # didascalia e' testo come il resto, non un segnaposto da buttare.
            per_pagina.setdefault(pag, []).append(testo)
    for tab in documento.tables:
        pag = tab.prov[0].page_no if tab.prov else 0
        per_pagina.setdefault(pag, []).append(tab.export_to_markdown())
    pagine = range(blocco[0], blocco[1] + 1) if blocco else sorted(
        set(per_pagina) | {p.prov[0].page_no for p in documento.pictures if p.prov} or {1})
    for n in pagine:
        fuori[n] = "\n\n".join(per_pagina.get(n, []))
    return fuori


def _pezzi_da_markdown_docling(markdown, dentro):
    """I pezzi dal Markdown di Docling, pagina per pagina: le tabelle diventano
    una riga per pezzo. Ogni pagina si salva anche su disco (`markdown-docling/`),
    cosi' si puo' guardare cosa ha letto Docling senza rifare la conversione."""
    pezzi = []
    for pagina in sorted(markdown):
        md = markdown[pagina]
        _salva_markdown(dentro, "docling", pagina, md)
        pezzi += _pezzi_da_markdown(md, pagina or None)     # 0 = senza pagine
    return pezzi


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
        out.append((img, pagina, descr, None))
    for tab in documento.tables:
        try:
            img = tab.get_image(documento)
        except Exception:
            continue
        if img is None:
            continue
        pagina = tab.prov[0].page_no if tab.prov else None
        out.append((img, pagina, None, None))
    return out


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


def _converti_remoto(percorso, blocco, dentro=None):
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
    if dentro:
        _salva_docling_json(doc, dentro, blocco)
    return _chunk(doc), _immagini(doc), {}


def _in_processo(percorso, blocco, device, descrivi=True, dentro=None):
    """_converti in un processo figlio, chiuso a forza se non finisce in tempo."""
    import multiprocessing
    pool = multiprocessing.get_context("spawn").Pool(1, maxtasksperchild=1)
    dove = f" (pagine {blocco[0]}-{blocco[1]})" if blocco else ""
    try:
        return pool.apply_async(_converti, (percorso, blocco, device, descrivi, dentro)).get(timeout=SECONDI_PER_BLOCCO)
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

    def pezzi(self, p: pathlib.Path, progresso=None, solo_gpu=False, dentro=None,
              progresso_figure=None):
        """progresso(fatte, totali) se c'e', chiamata ad ogni blocco completato.
        solo_gpu: file che fuori dalla finestra si legge SOLO finche' la GPU e'
        libera; se la perde a meta', si ferma e si riprende dopo (Rimandato).
        dentro: cartella (relativa alla radice) dove scrivere le immagini man
        mano che escono; si torna il METADATO, non l'immagine, cosi' la memoria
        non cresce con le pagine."""
        if p.suffix.lower() in TESTO:
            testo = p.read_text(encoding="utf-8", errors="replace")
            return ([(t, p, None) for t, p in unisci([(x, None) for x in re.split(r"\n\s*\n", testo)])], [], None)
        self._fai_spazio()
        # Le figure le descriviamo NOI anche in questo percorso, non Docling.
        # Il suo prompt e' unico per tutto il documento e non puo' sapere il
        # titolo della pagina da cui viene la figura, e con le icone non parte
        # nemmeno: le descrive solo se le classifica come Picture. Il 27/09/2026
        # su 25 immagini di una presentazione di sicurezza, 23 erano icone e
        # banner ed erano rimaste senza descrizione, quindi senza vettore e non
        # trovabili con una domanda. Il catalogo gia' fa cosi' (descrivi=False):
        # due descrittori diversi darebbero due stili diversi nello stesso
        # indice. Una chiamata al modello per figura, come prima: cambia il
        # prompt, non il conto.
        pezzi, immagini, markdown = self._con_docling(p, progresso, solo_gpu, dentro, descrivi=False)
        titoli = _titoli_da_markdown(markdown)
        immagini = _descrivi_figure(immagini, titoli, dentro, progresso_figure)
        # Le descrizioni delle figure entrano ANCHE nel testo: «sassi rossi» non
        # combacia con nessun codice, ma la figura del prodotto ha il titolo
        # della pagina («DEKOSTEINE pietre decorative») e il colore («red»).
        # Senza questi pezzi il colore dei cataloghi sparisce dall'indice.
        figure = _pezzi_dalle_figure(immagini, titoli)
        return ([(t, p, "docling") for t, p in pezzi] + [(t, p, "docling") for t, p in figure],
                immagini,
                "docling")

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
                g, im, md = _converti_remoto(str(p), blocco, dentro)
                markdown.update(md)
                grezzi += g
                immagini += _scrivi_immagini(dentro, im, len(immagini)) if dentro else im
                if len(blocchi) > 1:
                    print(f"    {p.name}: pagine {blocco[0]}-{blocco[1]} di {blocchi[-1][1]} (GPU)", flush=True)
                if progresso and blocco:
                    progresso(blocco[1], blocchi[-1][1])
            if CHUNK_DOCLING == "markdown":
                return _pezzi_da_markdown_docling(markdown, dentro), immagini, markdown
            return unisci(grezzi), immagini, markdown
        for blocco in blocchi:
            device = dove_leggere()
            if solo_gpu and device != "cuda":
                raise Rimandato(f"GPU occupata dopo {blocco[0] - 1 if blocco else 0} pagine")
            try:
                g, im, md = _in_processo(str(p), blocco, device, descrivi, dentro)
            except Exception as e:
                if not _gpu_piena(e):
                    raise
                print(f"    {p.name}: la GPU non ce l'ha fatta ({type(e).__name__}), questo blocco in CPU",
                      flush=True)
                device = "cpu"
                g, im, md = _in_processo(str(p), blocco, "cpu", descrivi, dentro)
            grezzi += g
            markdown.update(md)
            immagini += _scrivi_immagini(dentro, im, len(immagini)) if dentro else im
            del g, im, md
            if len(blocchi) > 1:
                print(f"    {p.name}: pagine {blocco[0]}-{blocco[1]} di {blocchi[-1][1]}"
                      f"{' (GPU)' if device == 'cuda' else ''}", flush=True)
            if progresso and blocco:
                progresso(blocco[1], blocchi[-1][1])
        if CHUNK_DOCLING == "markdown":
            return _pezzi_da_markdown_docling(markdown, dentro), immagini, markdown
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


def _ritentare(stato, errore, indicizzato_il):
    """Se il documento va riletto ANCHE se il file non e' cambiato.

    Di solito non si rilegge: stesso file, nessun lavoro. Ma un errore
    causato da FUORI non cambia con il file, quindi aspettare che il file
    cambi significa aspettare per sempre: se il modello non rispondeva, il
    giro dopo lo rilegge perche' il mondo e' cambiato, non perche' il file.

    Non si ritenta un errore che sta nel file o nel codice (formato non
    leggibile, difetto nostro): rifarebbe la stessa domanda e riceverebbe la
    stessa risposta, spendendo la GPU. Ritentano solo le eccezioni in
    ERRORI_DA_RITENTARE, e non piu' di una volta ogni RITENTO_ORE: il servizio
    gira ogni 5 minuti e un timeout da 600 secondi ripreso ogni 5 minuti non
    finisce mai.
    """
    if stato != "errore" or not errore or not indicizzato_il:
        return False
    if not any(k in errore for k in ERRORI_DA_RITENTARE):
        return False
    return (datetime.now(timezone.utc) - indicizzato_il).total_seconds() >= RITENTO_ORE * 3600


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


def _butta_sorgenti(percorso_fonte, rel, tieni_lavorato=False):
    """Via il lavorato di un documento. Si chiama prima di rifarlo e quando il
    documento esce dall'indice: i nomi delle immagini dipendono da pagina e
    ordine, quindi un PDF con meno figure di prima lascerebbe file orfani — e
    ora che stanno nelle cartelle di lavoro si vedono.

    `tieni_lavorato`: il documento non e' cambiato, quindi si rifanno le immagini
    ma NON si richiama il VLM. Vale per la carta d'identita' e per le descrizioni
    delle figure (il costo vero: le chiamate al modello visivo), che restano
    finche' il file non cambia."""
    import shutil
    base = RADICE / _cartella_sorgenti(percorso_fonte, rel)
    if tieni_lavorato:
        shutil.rmtree(base / "immagini", ignore_errors=True)
        return
    shutil.rmtree(base, ignore_errors=True)


# ---------------------------------------------------------------- l'indagine
# Che tipo di documento e' e che cosa deve produrre la sua lettura. Non e' un
# passaggio di comodo: `documenti.tipo` e' cio' che l'orchestratore usa per
# impaginare la risposta (schede prodotto o citazioni), quindi sbagliare qui
# cambia il modo in cui l'assistente risponde. Finche' lo decideva la domanda
# "ha delle immagini?", la risposta dipendeva da quale reader era passato:
# `Catalogo Gasper Autunno Natale 2026.pdf` finiva `documento` con 761 pezzi
# e zero figure solo perche' il reader del giorno non le estraeva.
#
# Il tipo e' una SCELTA CHIARA del documento, non una misura: si chiede al VLM
# su un campione di pagine e si tiene il tipo che ha vinto per voti, con i voti
# per pagina accanto, per poter vedere quanto la cosa era convinta. I dettagli
# grossi (voti, misure, dove sta la carta) vanno in `documenti.indagine`; le
# risposte grezze stanno nella carta su disco, e la colonna dice solo dove.

TIPI = ("manuale", "catalogo", "tabella prezzi", "ordine", "fattura", "scheda", "altro")
TESTI = ("leggibile", "non leggibile", "misto")
PRODUCE = ("testo", "testo+immagini", "dati")
PAGINE_MIN, PAGINE_MAX, PERCENTO_INDAGINE = 5, 12, 0.05
# Cambiare questo numero invalida le carte: il campione e' cambiato, quindi i
# voti di ieri non sono piu' quelli che darebbe oggi.
REGOLE_INDAGINE = "2"

ISTRUZIONI_INDAGINE = (
    "Guarda questa pagina di un documento aziendale e rispondi su quattro righe, "
    "nient'altro.\n"
    "TIPO: uno solo fra " + ", ".join(TIPI) + ".\n"
    "COSA: il documento in due parole.\n"
    "TESTO: " + ", ".join(TESTI) + ".\n"
    "PRODUCE: " + ", ".join(PRODUCE) + "."
)

# L'impronta delle istruzioni: se cambiano, la carta vale un'altra cosa e va
# rifatta. Cambiare REGOLE_INDAGINE a mano serve per lo stesso motivo.
IMPRONTA_INDAGINE = hashlib.sha256((ISTRUZIONI_INDAGINE + REGOLE_INDAGINE).encode()).hexdigest()[:8]


def _campione_pagine(n, minimo=PAGINE_MIN, massimo=PAGINE_MAX, percentuale=PERCENTO_INDAGINE):
    """Le pagine da guardare: 5 se sono poche, 12 se sono tante, e nel mezzo un
    5% del documento. Il campione parte dall'inizio e si spande: i cataloghi
    hanno tutte le pagine uguali, e i registri/inventari differiscono soprattutto
    in coda."""
    if n <= 0:
        return []
    quante = min(n, max(minimo, min(massimo, int(n * percentuale))))
    return sorted({min(n - 1, i * n // quante) for i in range(quante)})


def _valore(grezzo, chiave, valori):
    """La riga che il modello ha scritto, se e' una di quelle che sapevamo chiedere.

    Non si controlla solo se c'e' la parola: se il modello inventa un tipo
    ('brochure', 'listino') non lo foriamo in `tipi` e non lasciamo la pagina
    senza voto. Per lo stesso motivo, quando la riga c'e' ma non e' leggibile,
    si tiene il testo cosi' com'e' (una risposta spostata a capo non e' una
    risposta assente).

    Si prova dalla sequenza piu' lunga: nell'elenco c'e' «tabella prezzi», e se si
    guardassero le parole una per una nessuna corrisponderebbe — la pagina
    risponderebbe bene e il suo voto andrebbe perso."""
    riga = next((r for r in grezzo.splitlines() if r.strip().upper().startswith(chiave)), "")
    testo = riga.split(":", 1)[1] if ":" in riga else ""
    parole = testo.split()
    cercati = {v: v for v in valori}
    for n in range(max((len(v.split()) for v in valori), default=1), 0, -1):
        for i in range(len(parole) - n + 1):
            frase = " ".join(parole[i:i + n]).strip(" .:;,*").lower()
            if frase in cercati:
                return cercati[frase]
    return testo.strip()[:60]


def _chiedi_indagine(b64):
    url, testa, modello = _litellm_visione()
    r = httpx.post(f"{url}/v1/chat/completions",
                   json={"model": modello, "temperature": 0, "max_tokens": 120,
                         "messages": [{"role": "user", "content": [
                             {"type": "text", "text": ISTRUZIONI_INDAGINE},
                             {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]},
                   headers=testa, timeout=180)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


def indaga_documento(p, dentro, impronta, noto=None, forza=False):
    """Il tipo di `p` e come va letto, con riuso per impronta.

    `noto` e' l'indagine gia' in banca dati: se riguarda lo stesso file (stessa
    impronta) ed e' stata fatta con le stesse istruzioni, si riusa e non si
    richiama il VLM. Il file che cambia si rilegge da solo; `--forza` rilegge
    anche quando il file e' lo stesso, per quando l'etichetta e' sbagliata.

    La carta su disco e' la prova, `documenti.indagine` e' la copia interrogabile.
    Senza VLM non si butta via quello che c'era: torna quello, e se non c'era
    niente il documento resta col tipo di ripiego."""
    carta = RADICE / dentro / f"identita-{IMPRONTA_INDAGINE}.json"
    if (noto and not forza and carta.exists()
            and noto.get("impronta") == impronta and noto.get("impronta_istruzioni") == IMPRONTA_INDAGINE):
        return noto
    if p.suffix.lower() != ".pdf":
        return None                      # il campione di pagine si sa solo sui PDF
    try:
        import pypdfium2
        pdf = pypdfium2.PdfDocument(str(p))
        quante = len(pdf)
        pagine = _campione_pagine(quante)
        voti, grezzi, misure = [], [], []
        for n in pagine:
            testo = pdf[n].get_textpage().get_text_range()
            b64 = _immagine_pagina(str(p), n + 1)
            grezzo = _chiedi_indagine(b64)
            grezzi.append({"pagina": n + 1, "grezzo": grezzo,
                           "tipo": _valore(grezzo, "TIPO", TIPI), "produce": _valore(grezzo, "PRODUCE", PRODUCE),
                           "cosa": _valore(grezzo, "COSA", ()), "testo": _valore(grezzo, "TESTO", TESTI)})
            voti.append(_valore(grezzo, "TIPO", TIPI))
            misure.append({"pagina": n + 1, "caratteri": len(testo)})
        pdf.close()
    except Exception as e:
        print(f"  indagine non riuscita su {p.name} ({type(e).__name__}: {e})")
        return noto
    validi = [v for v in voti if v in TIPI]
    if not validi:
        # Il modello ha risposto qualcosa che non e' un tipo che avevamo chiesto.
        # Meglio dirlo che lasciare il documento col tipo di ripiego: se e' un
        # difetto delle istruzioni, la riga la si legge qui.
        print(f"  nessun tipo riconosciuto su {p.name}: {voti}")
        return noto
    tipo = max(set(validi), key=validi.count)            # il piu' votato, non il primo
    esito = {"tipo": tipo, "impronta": impronta, "impronta_istruzioni": IMPRONTA_INDAGINE,
             "pagine": quante, "campione": [n + 1 for n in pagine], "voti": voti,
             "produce": [g["produce"] for g in grezzi], "carta": f"{dentro}/{carta.name}"}
    carta.parent.mkdir(parents=True, exist_ok=True)
    carta.write_text(json.dumps(esito | {"grezzi": grezzi, "misure": misure}, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    return esito


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
        for n, (img, pagina, descr, verdetto) in enumerate(immagini, start=da_indice):
            nome = f"{pagina or 0}_{n}.png"
            _riduci(img).save(radice / nome)
            out.append((f"{dentro}/immagini/{nome}", pagina, descr, verdetto))
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
    percorsi = [p for p, _pagina, _descr, _vd in immagini]
    conn.execute("DELETE FROM immagini WHERE source_id = %s AND documento = %s"
                 " AND NOT (percorso = ANY(%s))", (fid, rel, percorsi))
    # La descrizione serve a SCEGLIERE quale figura mostrare, non solo a
    # cercarla nel testo: il suo vettore vive nello stesso spazio dei pezzi
    # (bge-m3), quindi "sassi rossi" puo' incontrare "dark red lava rocks".
    # Se i vettori non si possono fare (host giu'), le immagini entrano lo
    # stesso: si completano al giro dopo, come i pezzi senza vettore.
    descrizioni = [d for _p, _pagina, d, _vd in immagini if d]
    vettori_descr = vettori(descrizioni) if descrizioni else None
    prossimo = iter(vettori_descr) if vettori_descr else None
    for percorso, pagina, descr, verdetto in immagini:
        v = next(prossimo, None) if (descr and prossimo) else None
        conn.execute("""INSERT INTO immagini (source_id, documento, page, percorso, descrizione, embedding, verdetto)
                        VALUES (%s, %s, %s, %s, %s, %s::vector, %s)
                        ON CONFLICT (source_id, percorso) DO UPDATE SET page = EXCLUDED.page,
                          documento = EXCLUDED.documento,
                          descrizione = EXCLUDED.descrizione, embedding = EXCLUDED.embedding,
                          verdetto = EXCLUDED.verdetto""",
                     (fid, rel, pagina, percorso, descr, vettore_sql(v) if v else None, verdetto))
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
        "SELECT documento, impronta, dimensione, modificato_il, stato, errore, indicizzato_il, indagine"
        " FROM documenti WHERE source_id = %s", (fid,))}
    presenti = file_da_leggere(cartella)
    # L'impronta (contenuto) come identita' del documento, per riconoscere gli
    # spostamenti DENTRO la fonte: un file spostato in una sottocartella cambia
    # percorso ma non contenuto, quindi non va riletto — si rinomina il percorso
    # nell'indice e basta.
    per_impronta = {r[1]: rel for rel, r in noti.items() if r[1]}
    spostati = set()
    conteggi = {"fonte": fid, "nuovi": 0, "cambiati": 0, "uguali": 0, "tolti": 0, "spostati": 0, "errori": 0}
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
        riprova = _ritentare(vecchio[4], vecchio[5], vecchio[6]) if vecchio else False
        # Stesso file di prima: non si rilegge (si riprova quando il file
        # cambia). Con --forza si rilegge lo stesso: chi lo chiede vuole
        # proprio quello, di solito su un file andato in errore o rimandato.
        # `riprova` e' l'eccezione: l'errore non era nel file, era FUORI (vedi
        # _ritentare), quindi il file identico non e' una scusa per non riprovare.
        # Un file in stato 'errore' non si salta MAI: se e' nuovo e si e'
        # interrotto (dimensione e data identiche, ma zero pezzi) il salto
        # lo lascerebbe fuori dall'indice per sempre.
        if (not forza and vecchio and not riprova and vecchio[4] != "errore"
                and vecchio[2] == st.st_size and vecchio[3] == quando):
            conteggi["uguali"] += 1
            continue
        impronta = impronta_file(p)
        # Spostato DENTRO la fonte (sottocartella, rinomina): stesso contenuto,
        # percorso diverso. Non si rilegge: si rinomina il percorso nell'indice.
        # Le immagini restano dove sono (il loro `percorso` su disco non cambia),
        # i pezzi pure — cambia solo il `documento` che li etichetta.
        vecchio_rel = per_impronta.get(impronta)
        if vecchio_rel and vecchio_rel != rel:
            with conn.transaction():
                conn.execute("UPDATE documenti SET documento = %s WHERE source_id = %s AND documento = %s",
                             (rel, fid, vecchio_rel))
                conn.execute("UPDATE chunks SET documento = %s WHERE source_id = %s AND documento = %s",
                             (rel, fid, vecchio_rel))
                conn.execute("UPDATE immagini SET documento = %s WHERE source_id = %s AND documento = %s",
                             (rel, fid, vecchio_rel))
            per_impronta[impronta] = rel
            spostati.add(vecchio_rel)
            conteggi["spostati"] += 1
            print(f"  spostato: {fid}/{vecchio_rel} -> {rel}")
            continue
        grosso = rimanda(st.st_size, lettore.finestra, gpu=False)   # grosso e fuori finestra
        if grosso and dove_leggere() != "cuda":
            # Non si segna niente in documenti: il file resta "non ancora letto"
            # e al primo giro utile (GPU libera, o finestra) entra come gli altri.
            conteggi["rimandati"] = conteggi.get("rimandati", 0) + 1
            print(f"  rimandato: {fid}/{rel} ({st.st_size // (1024 * 1024)} MB, GPU occupata)")
            continue
        if (not forza and vecchio and not riprova and vecchio[4] != "errore"
                and vecchio[1] == impronta):
            conn.execute("UPDATE documenti SET modificato_il = %s WHERE source_id = %s AND documento = %s",
                         (quando, fid, rel))
            conteggi["uguali"] += 1
            continue
        chiave = f"illeggibile:{fid}:{rel}"
        escluso = NON_LEGGIBILI.get(p.suffix.lower())
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
        # Se il documento e' lo stesso, si rifanno le immagini ma si tengono la
        # carta d'identita' e le descrizioni delle figure: sono il costo vero
        # (le chiamate al VLM), e il giudizio giusto si deduce dall'impronta,
        # non si chiede a chi lancia il comando.
        stesso_documento = bool(vecchio) and vecchio[1] == impronta
        _butta_sorgenti(percorso, rel, tieni_lavorato=stesso_documento)
        # PRIMA di leggere, e' il tipo che decide. Non e' metadato: e' la
        # risposta che l'assistente da' quando il documento finisce in una
        # risposta, e su un catalogo sbagliarla significa descrivere un listino
        # come se fosse un manuale. Costa fino a 12 pagine di VLM su un file che
        # poi costa decine di minuti a leggere: una domanda ogni venti.
        indagine = indaga_documento(p, _cartella_sorgenti(percorso, rel), impronta,
                                    noto=vecchio[7] if vecchio else None, forza=forza)
        tipo = (indagine or {}).get("tipo")
        IN_CORSO.update(fid=fid, rel=rel, stato=vecchio[4] if vecchio else None)
        try:
            pezzi, immagini, chi_li_ha_letti = lettore.pezzi(p, _progresso, solo_gpu=grosso,
                                                             dentro=_cartella_sorgenti(percorso, rel),
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
                conn.execute("""INSERT INTO documenti (source_id, documento, impronta, dimensione, modificato_il, stato, errore, pezzi, tipo, indagine)
                                VALUES (%s,%s,%s,%s,%s,'errore',%s,0,COALESCE(%s, 'documento'),%s::jsonb)
                                ON CONFLICT (source_id, documento) DO UPDATE SET impronta = EXCLUDED.impronta,
                                  dimensione = EXCLUDED.dimensione, modificato_il = EXCLUDED.modificato_il,
                                  stato = 'errore', errore = EXCLUDED.errore, pezzi = 0,
                                  tipo = COALESCE(EXCLUDED.indagine->>'tipo', documenti.tipo),
                                  indagine = COALESCE(EXCLUDED.indagine, documenti.indagine),
                                  in_lettura = NULL, indicizzato_il = now()""",
                             (fid, rel, impronta, st.st_size, quando, f"{type(e).__name__}: {e}"[:500],
                              tipo, json.dumps(indagine) if indagine else None))
                IN_CORSO.clear()
                anomalia(conn, chiave, "attenzione", f"File non leggibile: {rel}",
                         "Aprire il file: se e' danneggiato o protetto da password, sostituirlo con una copia "
                         "leggibile o spostarlo in _archivio.", azienda, f"fonte:{fid}",
                         {"errore": f"{type(e).__name__}: {e}"[:300]})
            continue

        vett = vettori([t for t, _, _ in pezzi]) if pezzi and stato_vettori["ok"] else None
        if pezzi and vett is None:
            stato_vettori["ok"] = False          # host giu': non si riprova file per file in questo giro
        if vett and not stato_vettori["verificato"]:
            stato_vettori["ok"] = stato_vettori["verificato"] = controlla_modello(conn, vett[0])
            if not stato_vettori["ok"]:
                vett = None
        with conn.transaction():
            conn.execute("DELETE FROM chunks WHERE source_id = %s AND documento = %s", (fid, rel))
            for i, (testo, pagina, chi) in enumerate(pezzi):
                conn.execute("""INSERT INTO chunks (source_id, documento, page, content, content_hash, embedding, lettore)
                                VALUES (%s, %s, %s, %s, %s, %s::vector, %s)
                                ON CONFLICT (source_id, documento, page, content_hash) DO NOTHING""",
                             (fid, rel, pagina, testo, hashlib.sha256(testo.encode()).hexdigest(),
                              vettore_sql(vett[i]) if vett else None, chi))
            _registra_immagini(conn, fid, rel, immagini)
            conn.execute("""INSERT INTO documenti (source_id, documento, impronta, dimensione, modificato_il, stato, errore, pezzi, tipo, indagine)
                            VALUES (%s,%s,%s,%s,%s,%s,NULL,%s,COALESCE(%s, 'documento'),%s::jsonb)
                            ON CONFLICT (source_id, documento) DO UPDATE SET impronta = EXCLUDED.impronta,
                              dimensione = EXCLUDED.dimensione, modificato_il = EXCLUDED.modificato_il,
                              stato = EXCLUDED.stato, errore = NULL, pezzi = EXCLUDED.pezzi,
                              tipo = COALESCE(EXCLUDED.indagine->>'tipo', documenti.tipo),
                              indagine = COALESCE(EXCLUDED.indagine, documenti.indagine),
                              in_lettura = NULL, indicizzato_il = now()""",
                           (fid, rel, impronta, st.st_size, quando, "indicizzato" if pezzi else "vuoto",
                            len(pezzi), tipo, json.dumps(indagine) if indagine else None))
            chiudi(conn, chiave)
        IN_CORSO.clear()        # letto: da qui in poi un riavvio non lo riguarda
        conteggi["cambiati" if vecchio else "nuovi"] += 1
        print(f"  {'aggiornato' if vecchio else 'nuovo'}: {fid}/{rel} ({len(pezzi)} pezzi, {len(immagini)} immagini"
              f"{'' if vett else ', senza vettori'})")

    # Cancellati dalla cartella: via dall'indice. Gli `spostati` no: il loro
    # contenuto e' rimasto, ha solo cambiato percorso (gestito sopra).
    for rel in set(noti) - {r for r, _ in presenti} - spostati:
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


def descrivi_mancanti(conn):
    """Figure gia' indicizzate rimaste SENZA descrizione: il VLM aveva risposto
    il solo verdetto («Verdetto: informazione»). Senza testo non hanno vettore
    ne' pezzo cercabile, e il giro dopo non le recuperava: completa_vettori
    salta le descrizioni vuote. Qui si richiama il VLM per quelle figure e
    basta, con lo stesso prompt e lo stesso titolo di pagina della lettura
    normale; il resto del documento non si rilegge (4 figure Gasper, visto il
    04/10/2026). Il vettore lo mette completa_vettori, subito dopo.

    Se il VLM non risponde ci si ferma alla prima: si riprova al giro dopo.
    Una figura per cui torna di nuovo vuota resta com'e' e si ritenta."""
    if VLM_DESCRIZIONI != "api" or not DESCRIVI_FIGURE:
        return 0
    figure = conn.execute(
        """SELECT i.id, i.source_id, i.documento, i.page, i.percorso, s.percorso
             FROM immagini i JOIN sources s ON s.id = i.source_id
            WHERE coalesce(i.descrizione, '') = '' AND s.provenienza = 'cartella'
            ORDER BY i.id""").fetchall()
    if not figure:
        return 0
    url, testa, logico = _litellm_visione()
    fatte = 0
    for id_, fid, documento, pagina, percorso, percorso_fonte in figure:
        dentro = _cartella_sorgenti(percorso_fonte, documento)
        md = RADICE / dentro / "markdown-docling" / f"{pagina or 0:04d}.md"
        titolo = (_titolo_da_markdown(md.read_text(encoding="utf-8")) if md.is_file() else None) or ""
        try:
            grezza = _descrivi_una(percorso, titolo, url, testa, logico)
        except Exception as e:
            print(f"  figure da descrivere: VLM non risponde ({type(e).__name__}: {e})", flush=True)
            break
        descr, verdetto = _estrae_verdetto(grezza)
        if not descr:
            continue
        with conn.transaction():
            conn.execute("UPDATE immagini SET descrizione = %s, verdetto = %s, embedding = NULL"
                         " WHERE id = %s", (descr, verdetto, id_))
            # Il pezzo di testo della figura, come lo scrive la lettura normale.
            for testo, p in _pezzi_dalle_figure([(percorso, pagina, descr, verdetto)],
                                                {pagina or 0: titolo}):
                conn.execute("""INSERT INTO chunks (source_id, documento, page, content, content_hash, embedding, lettore)
                                VALUES (%s, %s, %s, %s, %s, NULL, 'docling')
                                ON CONFLICT (source_id, documento, page, content_hash) DO NOTHING""",
                             (fid, documento, p, testo, hashlib.sha256(testo.encode()).hexdigest()))
        # Anche la cache su disco: una rilettura non rifa' la chiamata.
        salvate = _descrizioni_salvate(dentro)
        salvate[pathlib.PurePath(percorso).name] = grezza
        _salva_descrizioni(dentro, salvate)
        fatte += 1
    print(f"  figure da descrivere: {fatte} descritte su {len(figure)}", flush=True)
    return fatte


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
        # Prima il vecchio, poi il nuovo: cio' che e' gia' nell'indice si rimette
        # in ordine (figure senza descrizione, pezzi e figure senza vettore)
        # PRIMA di leggere materiale nuovo. Altrimenti un giro lungo di file
        # nuovi rimanda le riparazioni di ore, e l'archivio resta bucato.
        descritte = descrivi_mancanti(conn)
        completati = completa_vettori(conn, stato_vettori) + descritte
        fonti = [f for f in conn.execute(
            """SELECT id, percorso, aziende FROM sources
                WHERE provenienza = 'cartella' AND stato IN ('attiva', 'attesa') ORDER BY id""").fetchall()
                 # Le fonti di esempio hanno percorsi \\server\...: non sono cartelle montate qui.
                 if PERCORSO_VALIDO.match(f[1]) and ".." not in f[1]]
        esiti = [indicizza_fonte(conn, f, lettore, stato_vettori, solo, forza) for f in fonti]
        # Quelli nuovi entrati senza vettore (host giu' a meta' giro).
        completati += completa_vettori(conn, stato_vettori)
        conta_lessemi(conn, esiti)
    del lettore
    gc.collect()
    return esiti, completati


def descrivi_indice(solo=None):
    """Le descrizioni di documenti e pagine, in coda al giro.

    Si chiama QUI e non durante: le scrive il modello di chat, e Docling non
    ci sta in VRAM insieme a lui. A giro finito la GPU e' libera.

    Incrementale per costruzione: le due funzioni saltano quello che c'e'
    gia' (`NOT EXISTS` sulla tabella `indice`), quindi si puo' fermare e
    riprendere, e un giro che non ha cambiato niente non costa niente.

    Non alza mai: una descrizione che manca rende la ricerca a due stadi piu'
    povera, non rompe l'indicizzazione — che e' il lavoro vero di questo
    processo.
    """
    # Import ritardato: `descrizioni` chiama `vettori` di qui, e importarlo
    # in testa chiuderebbe il cerchio.
    import descrizioni
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            return (descrizioni.genera_documenti(conn, solo=solo)
                    + descrizioni.genera_pagine(conn, solo=solo,
                                                quanti=INDICE_PAGINE))
    except Exception as e:
        print(f"indice: non riuscito ({type(e).__name__}: {e})", flush=True)
    return 0


ATTRIBUTI_PER_GIRO = int(os.environ.get("ATTRIBUTI_PER_GIRO", "300"))


def metti_in_colonne(quante=None):
    """Gli attributi dei prodotti, in coda al giro, come le descrizioni.

    Il VLM scrive la didascalia in prosa; questo la mette in colonne, una
    volta, cosi' chi cerca filtra invece di ri-interpretare. Senza, «cuori
    blu» obbliga il critico a capire che «light blue with white hearts» e' un
    nastro azzurro coi cuori bianchi — e sbaglia una volta su tre.

    Col tetto per giro: 12.000 didascalie non si fanno in un giro, e un giro
    che non finisce mai non lascia ripartire Docling. Incrementale, quindi il
    resto si fa al giro dopo.

    Non alza mai, per la stessa ragione delle descrizioni: una riga che manca
    rende la ricerca piu' povera, non rompe l'indicizzazione.
    """
    import attributi
    from concurrent.futures import ThreadPoolExecutor
    from psycopg.rows import dict_row
    try:
        with psycopg.connect(os.environ["DATABASE_URL"],
                             row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(attributi.TABELLA)
            conn.commit()
            righe = attributi.da_fare(conn, quante or ATTRIBUTI_PER_GIRO)
        if not righe:
            return 0
        fatte = 0
        with ThreadPoolExecutor(max_workers=max(1, attributi.PARALLELO)) as pool:
            for (ident, _), (voci, problema) in zip(
                    righe, pool.map(lambda r: attributi.colonne_di(r[1]),
                                    righe)):
                if voci is None:
                    continue
                with psycopg.connect(os.environ["DATABASE_URL"]) as scrittura:
                    attributi.scrivi(scrittura, ident, voci)
                fatte += 1
        return fatte
    except Exception as e:
        print(f"attributi: non riuscito ({type(e).__name__}: {e})", flush=True)
    return 0


def main():
    signal.signal(signal.SIGTERM, _riavvio_voluto)
    una_volta = "--una-volta" in sys.argv or "--forza" in sys.argv
    forza = "--forza" in sys.argv
    solo = sys.argv[sys.argv.index("--solo") + 1] if "--solo" in sys.argv else None
    while True:
        inizio = time.time()
        cambi, completati, descritti = [], 0, 0
        try:
            esiti, completati = giro(aspetta=una_volta, forza=forza, solo=solo)
            if esiti is None:
                print("un altro giro e' in corso: si salta questo", flush=True)
                esiti = []
            cambi = [e for e in esiti if any(e.get(k) for k in ("nuovi", "cambiati", "tolti", "errori", "errore"))]
            # In CODA: Docling ha finito, la GPU e' libera, e il modello di
            # chat puo' scrivere le descrizioni che la ricerca a due stadi
            # legge. Prima di questo la tabella `indice` era vuota e nessuno
            # generava niente (4/10/2026).
            # Ogni giro, non solo quando qualcosa e' cambiato: l'indice ha
            # migliaia di pagine da descrivere e un tetto per giro, quindi il
            # lavoro continua anche quando non arrivano documenti nuovi. A
            # mani vuote costa due SELECT.
            descritti = descrivi_indice(solo)
            # E gli attributi in colonne, per la stessa ragione e nello
            # stesso posto: la GPU e' libera e il modello di chat puo'
            # scrivere. Senza questa riga la tabella `attributi` resterebbe
            # ferma a quello che il passaggio retroattivo aveva fatto, e i
            # documenti nuovi arriverebbero con la sola prosa.
            in_colonne = metti_in_colonne()
            if in_colonne:
                print(f"attributi: {in_colonne} didascalie in colonne",
                      flush=True)
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
        # Se l'indice ha scritto qualcosa, c'e' ancora lavoro: si riparte
        # subito, come dopo un documento nuovo.
        if not (cambi or completati or descritti):
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

# Come si spezzano i pezzi del percorso Docling. "markdown" = _pezzi_da_markdown
# pagina per pagina: le tabelle diventano UNA RIGA per pezzo con il titolo
# davanti, e le didascalie (i formati dei cataloghi) restano nel testo. E' il
# comportamento misurato il 28/09/2026: 13/16 contro i 9/16 del vecchio
# HierarchicalChunker, che perdeva le didascalie. Reversibile: "chunker" torna
# al chunker di Docling.
CHUNK_DOCLING = os.environ.get("CHUNK_DOCLING", "markdown")


DPI_PAGINA = int(os.environ.get("DPI_PAGINA", "150"))

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
    "Then write «Colours: » describing the colour(s) OF the object(s), not a bare list. "
    "If the object has a pattern or motif (hearts, stars, dots, stripes), write the base "
    "colour and the motif colour together, e.g. 'white with red hearts'. If the image "
    "shows MULTIPLE items, describe each item's colour on its own line, keeping each "
    "colour attached to its object. Do NOT collapse into a single flat list. Group "
    "similar shades under one name (e.g. 'light blue' and 'sky blue' are both 'blue').\n"
    "If the image shows a single item with no colour variation, write «Colours: n/a».\n"
    "\n"
    "Only if there is a short PRODUCT CODE or product name printed near the object, "
    "write it as «Code: ». Do NOT transcribe addresses, phone numbers, or long text.\n"
    "Never invent anything that is not visible. Do not repeat. Be COMPLETE: describe "
    "ALL the objects you see, each with its own colour and motif.\n"
    "\n"
    "Finally, answer with ONE word — «informazione» or «corredo» — after the line "
    "«Verdetto: ».\n"
    "- «informazione» if this image shows something the page text does NOT already say "
    "(a product, a code, a colour, a value): the image IS the information.\n"
    "- «corredo» if this image is decorative or repeats what the text already says "
    "(an icon, a logo, a generic illustration): it only accompanies the text.\n"
    "If you are not sure, choose «informazione»."
)
SECONDI_PER_FIGURA = int(os.environ.get("SECONDI_PER_FIGURA", "120"))
# Quante descrizioni di figura si chiedono IN PARALLELO al VLM. Le figure sono
# indipendenti tra loro: descriverle una alla volta era il costo dominante dei
# cataloghi (2617 immagini su INGE = ~45 minuti in serie). llama-swap ha gli
# slot paralleli, quindi si mandano a lotti. Reversibile: 1 = comportamento di
# prima.
PARALLELO_FIGURE = int(os.environ.get("PARALLELO_FIGURE", "10"))
# 0 = le figure entrano nell'indice con la descrizione di Docling (o senza) e il
# VLM non viene chiamato una volta per immagine. Serve per le prove: vedi
# _descrivi_figure.
DESCRIVI_FIGURE = os.environ.get("DESCRIVI_FIGURE", "1") != "0"


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



def _righe_di_tabella(righe, titolo, pagina):
    """Una tabella Markdown -> pezzi. Se sta in un pezzo solo resta intera:
    ┬½quali formati offrite┬╗ vuole vedere tutti i formati insieme. Se e' lunga
    si spezza per RIGA, ognuna con il titolo e l'intestazione davanti ÔÇö
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
    sul codice articolo ÔÇö che funzionava su un catalogo e si rompeva sul
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
    for _percorso, pagina, descr, verdetto in immagini:
        if not descr or verdetto == "corredo":
            continue
        titolo = titoli.get(pagina or 0, "")
        testo = " ".join(descr.split())
        fuori.append((f"{titolo} | Immagine: {testo}" if titolo else f"Immagine: {testo}", pagina))
    return fuori


def _titolo_da_markdown(testo):
    """Il titolo di una pagina: le prime tre righe di testo vero."""
    righe = [r.strip() for r in testo.splitlines()
             if r.strip() and not r.strip().startswith("```")]
    return " ".join(righe[:3])[:160] if righe else None


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


def _estrae_verdetto(descr):
    """Separa la descrizione dal verdetto che il prompt chiede in coda
    («Verdetto: informazione|corredo|tabella»). Torna (descrizione, verdetto).
    Il verdetto decide a che cosa serve la descrizione (pezzo cercabile o solo
    da mostrare), mai se esiste. Se non c'e', verdetto None e la descrizione
    resta cosi' com'e'."""
    verdetto = None
    pulite = []
    for r in (descr or "").splitlines():
        if r.strip().lower().startswith("verdetto"):
            v = r.strip().split(":", 1)[-1].strip().lower()
            if v in ("informazione", "corredo", "tabella"):
                verdetto = v
            continue
        pulite.append(r)
    return "\n".join(pulite).strip(), verdetto


def _descrivi_una(percorso, titolo, url, testa, logico):
    """Una figura -> descrizione grezza dal VLM. Separata per il parallelo."""
    import base64
    import json
    import urllib.request
    b64 = base64.b64encode((RADICE / percorso).read_bytes()).decode()
    corpo = json.dumps({
        "model": logico, "max_tokens": 300, "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": ISTRUZIONI_FIGURA.format(titolo=titolo)},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}],
    }).encode()
    req = urllib.request.Request(f"{url}/v1/chat/completions",
                                 data=corpo, headers=testa, method="POST")
    with urllib.request.urlopen(req, timeout=SECONDI_PER_FIGURA) as r:
        return json.load(r)["choices"][0]["message"]["content"].strip()


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
    from concurrent.futures import ThreadPoolExecutor, as_completed
    if VLM_DESCRIZIONI != "api":
        return immagini
    url, testa, logico = _litellm_visione()
    salvate = _descrizioni_salvate(dentro)
    fuori, falliti, riusate, fatte = [None] * len(immagini), 0, 0, 0

    # Prima passata: le figure gia' in cache si riusano; le altre si raccolgono
    # per descriverle in parallelo. Le figure sono indipendenti: descriverle
    # una alla volta era il costo dominante dei cataloghi (2617 immagini su
    # INGE = ~45 minuti in serie), qui vanno a lotti su llama-swap.
    da_descrivere = []
    for i, (percorso, pagina, vecchia, _vd) in enumerate(immagini):
        nome = pathlib.PurePath(percorso).name
        # Una risposta in cache senza descrizione (solo «Verdetto: ...») non e'
        # un risultato: si rifa'. Riusarla lasciava la figura per sempre senza
        # testo e senza vettore (4 figure Gasper, visto il 04/10/2026).
        if nome in salvate and _estrae_verdetto(salvate[nome])[0]:
            riusate += 1
            fatte += 1
            if progresso:
                progresso(fatte, len(immagini))
            descr, verdetto = _estrae_verdetto(salvate[nome])
            fuori[i] = (percorso, pagina, descr, verdetto)
        else:
            da_descrivere.append((i, percorso, pagina, titoli.get(pagina or 0) or "", vecchia))

    # Seconda passata: il VLM in parallelo, a lotti di PARALLELO_FIGURE.
    with ThreadPoolExecutor(max_workers=PARALLELO_FIGURE) as ex:
        futuri = {ex.submit(_descrivi_una, percorso, titolo, url, testa, logico): (i, percorso, pagina, vecchia)
                  for (i, percorso, pagina, titolo, vecchia) in da_descrivere}
        for fut in as_completed(futuri):
            i, percorso, pagina, vecchia = futuri[fut]
            fatte += 1
            if progresso:
                progresso(fatte, len(immagini))
            nome = pathlib.PurePath(percorso).name
            try:
                descr = fut.result()
                pulita, verdetto = _estrae_verdetto(descr or vecchia)
                if pulita:            # una risposta vuota non si salva: si ritenta
                    salvate[nome] = descr or vecchia
                fuori[i] = (percorso, pagina, pulita, verdetto)
            except Exception as e:
                falliti += 1
                if falliti == 1:      # il primo con il motivo, gli altri solo contati
                    print(f"    figura non descritta ({type(e).__name__}: {e})", flush=True)
                fuori[i] = (percorso, pagina, vecchia, None)

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


def _descrivi_figure(immagini, titoli, dentro, progresso=None):
    """UN posto solo dove le figure vengono descritte.

    Sul percorso 'pagina' (cataloghi) e su quello 'docling' (prosa) si
    descrive con lo stesso prompt, lo stesso modello e la stessa cache:
    descrivere un'immagine e' lo stesso lavoro, e farlo in due modi aveva
    prodotto due stili diversi nello stesso indice. Il caso che lo ha
    smascherato: Docling descrive solo quello che classifica come Picture,
    quindi su 25 immagini di una presentazione di sicurezza 23 icone e banner
    erano rimaste senza descrizione, quindi senza vettore e non trovabili con
    una domanda.

    Il titolo della pagina aiuta — sa COSA sta guardando, e una figura di
    221x149 px senza contesto diventa «dried fruit» — ma non e' un
    prerequisito: senza, l'immagine si manda lo stesso.

    Se il VLM non risponde, _descrivi_col_titolo alza Rimandato e il documento
    viene riletto al giro dopo. Le figure con una descrizione gia' presente non
    si ritocchiano: quelle sono buone e rifarle costerebbe una chiamata per
    nulla.

    `DESCRIVI_FIGURE=0` salta tutto il passaggio: le immagini restano quelle di
    Docling e il documento entra lo stesso. Serve a misurare quanto valgono le
    nostre descrizioni (una chiamata per figura: sono ~8 per pagina di
    catalogo, e cio' che rende un catalogo da 107 pagine un'ora di lavoro)
    senza aspettare un'ora per ogni prova.
    """
    if not DESCRIVI_FIGURE or not dentro or not immagini:
        return immagini
    nuovi = {percorso: (descr, verdetto)
             for percorso, _p, descr, verdetto in
             _descrivi_col_titolo([i for i in immagini if not i[2]], titoli, dentro, progresso)}
    if not nuovi:
        return immagini
    out = []
    for percorso, pagina, vecchia, vecchio_verdetto in immagini:
        descr, verdetto = nuovi.get(percorso, (vecchia, vecchio_verdetto))
        out.append((percorso, pagina, descr, verdetto))
    return out


def _titoli_da_markdown(markdown):
    """{pagina: titolo} dal Markdown che Docling ha gia' prodotto in memoria."""
    titoli = {}
    for n, testo in (markdown or {}).items():
        titolo = _titolo_da_markdown(testo)
        if titolo:
            titoli[n] = titolo
    return titoli


if __name__ == "__main__":
    main()
