"""Server HTTP dell'orchestratore: l'endpoint OpenAI-compatible che LibreChat
chiama, e il nodo che cuce la catena RAG.

    uvicorn orchestratore.main:app

Flusso di un turno (SPECIFICA-CONNETTORI? no, questa e' la chat):

    1. verifica del token (identita.py) -> gruppi e aziende
    2. embedding della domanda (recupero.embedding) -> vettore o None
    3. ricerca ibrida con ACL nella query (recupero.cerca)
    4. gate: contaminazione e scelta della rotta (gate.applica)
    5. prompt (prompt.py) + chiamata a LiteLLM in streaming
    6. traccia su `traces` per la diagnosi

Il modello e' una variabile di LiteLLM: qui si conosce solo il nome logico
della rotta decisa dal gate. L'endpoint espone UN SOLO modello ("assistente-v1"):
LibreChat non fa scegliere nulla all'utente (librechat.yaml.tmpl).
"""
import json
import os
import queue
import re
import threading
import time

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from orchestratore import documento as documento_mod
from orchestratore import agente, egress, gate, grafo, identita, immagini, immagini_articoli, indice, memoria, modello, prompt, recupero, ricerca_agente, riformula, vincoli

app = FastAPI()

MODEL_NAME = "assistente-v1"
LITELLM = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000").rstrip("/")
MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
APP_HOST = os.environ.get("APP_HOST", "assistente.localhost")
# Temperatura bassa ma non zero: a zero anche la decodifica avida puo' entrare
# in loop su un contesto pieno di righe di tabella quasi uguali.
TEMPERATURA = float(os.environ.get("TEMPERATURA", "0.2"))
# Budget di token della risposta. Con il 27B "rco" (reasoning content output)
# il modello ragiona ad alta voce PRIMA di rispondere: senza un budget
# sufficiente il ragionamento si mangia i token e il `content` resta vuoto
# (verificato il 24/09/2026). 4096 copre ragionamento + risposta con citazioni.
MAX_TOKEN = int(os.environ.get("MAX_TOKEN_RISPOSTA", "4096"))
# NIENTE frequency_penalty da qui. Provata il 22/09/2026 e scartata subito:
# punisce i token gia' usati, e una citazione come «[2]» si ripete
# legittimamente dieci volte in una risposta. Il modello ha smesso di citare i
# numeri e ha scritto «il natur [n], il creme [n], il rosa [n]».
# Contro la degenerazione agisce `--repeat-penalty 1.1` sul server
# (modelli/llama-swap.yaml): finestra corta, non tocca le citazioni.

# Quante figure si mostrano: NON un numero fisso. Il tetto esiste per non
# allagare la chat, ma quante mostrarne lo decide la domanda (vedi _scelte).
# Fino al 22/09/2026 erano sempre quattro, riempite con le piu' vicine che
# c'erano: nel testo non si vede, perche' il modello scarta cio' che non
# serve, ma una figura mostrata e' un'affermazione — «questa c'entra» — e
# riempire significa dirne tre false.
MAX_IMMAGINI = 12
# Quanto puo' essere peggiore di quella buona una figura perche' valga la pena
# mostrarla lo stesso, quando la domanda NON nomina niente di preciso: il 25
# per cento di distanza in piu'. Serve solo per le domande descrittive, dove
# non c'e' un criterio esatto e il numero giusto non lo sa nessuno.
QUOTA_PEGGIO = float(os.environ.get("IMMAGINI_QUOTA_PEGGIO", "0.25"))
# Quante se ne mostrano quando la domanda non permette di sceglierle con
# esattezza: un campione, non un catalogo.
CAMPIONE = int(os.environ.get("IMMAGINI_CAMPIONE", "4"))
# Coda del prompt di sistema, per le stranezze del modello del momento: sta in
# configurazione perche' cambia col modello, e cambiare modello non deve voler
# dire toccare il codice. Oggi serve per Qwen3, che ragiona a voce alta: senza
# "/no_think" LM Studio manda il ragionamento in reasoning_content e LibreChat
# riceve una risposta VUOTA (provato il 20/09/2026). Per il RAG il ragionamento
# non serve: la risposta deve stare nei documenti recuperati.
SUFFISSO_SISTEMA = os.environ.get("SUFFISSO_SISTEMA", "")
# Prova A/B: salta riformulazione e estrazione vincoli (i due passi LLM che
# iniettano errori). Si cerca la domanda dell'utente TALE E QUALE, senza
# riscrittura ne' pool must-match: e' il comportamento di RAGFlow/Onyx.
# Variabile d'ambiente, non codice: per tornare indietro basta riavviare senza.
SENZA_ESTRAZIONE = os.environ.get("SENZA_ESTRAZIONE", "") == "1"


@app.on_event("startup")
def _autocontrollo():
    # Fallimento rumoroso al boot, non silenzioso in esercizio.
    identita.autocontrollo()


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


# Lock preso dall'indicizzazione mentre il modello di chat e' scaricato per
# fare spazio a Docling sulla GPU (ingestion/indicizza.py, BLOCCO_LLM).
BLOCCO_LLM = 7_310_062
# Messaggio GENERICO di proposito: lo legge chiunque, e i nomi dei file o il
# loro numero direbbero cosa sta entrando nelle aree di altri.
INDICE_IN_AGGIORNAMENTO = ("Sto aggiornando l'indice dei documenti e in questo momento non posso rispondere. "
                           "Riprova fra qualche minuto.")


def _indice_in_aggiornamento(conn) -> bool:
    """True se l'indicizzazione ha scaricato il modello. Senza questo controllo
    la prima domanda lo farebbe ricaricare a meta' lettura (LM Studio carica su
    richiesta), e i due si toglierebbero la VRAM a vicenda. Il lock e' della
    connessione dell'indicizzazione: se quel servizio muore, sparisce."""
    with conn.cursor() as cur:
        cur.execute("""SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory'
                                       AND classid = 0 AND objid = %s AND objsubid = 1 AND granted) AS bloccato""",
                    (BLOCCO_LLM,))
        return bool(cur.fetchone()["bloccato"])


def _ricorda_gruppi(conn, utente: str, gruppi: list) -> None:
    """Ultimo elenco di gruppi visto nel token di questa persona (migrazione
    015). Non e' un archivio di identita': solo il `sub` e i gruppi, che stanno
    gia' nel token."""
    if not utente:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO gruppi_utente (utente, gruppi, aggiornato_il)
                           VALUES (%s, %s, now())
                           ON CONFLICT (utente) DO UPDATE SET gruppi = EXCLUDED.gruppi,
                             aggiornato_il = now()""", (utente, gruppi))
        conn.commit()
    except Exception as e:      # non si rompe un turno di chat per questo
        print(f"gruppi non registrati per {utente}: {type(e).__name__}: {e}", flush=True)


def _gruppi_della_richiesta(request, conn, utente: str) -> list:
    """I gruppi di chi sta chiedendo l'immagine, nell'ordine di affidabilita'.

    1. `X-Forwarded-Groups`, messo da oauth2-proxy dopo aver autenticato la
       persona con Keycloak: e' la SESSIONE, quindi sono i gruppi di adesso.
    2. Altrimenti l'ultimo elenco visto nel token di quella persona
       (migrazione 015): serve finche' la sessione non c'e' — per esempio
       quando l'orchestratore viene raggiunto dalla rete interna.

    In entrambi i casi il permesso si ricontrolla su `sources`: la sessione
    dice CHI sei, non COSA puoi vedere.
    """
    intestazione = (request.headers.get("x-forwarded-groups") or "") if request is not None else ""
    if intestazione:
        # oauth2-proxy le separa con virgola; Keycloak le scrive con lo slash
        # davanti perche' i gruppi sono un albero (full.path): "/vendite".
        return [g.strip().lstrip("/") for g in intestazione.split(",") if g.strip()]
    return _gruppi_noti(conn, utente)


def _gruppi_noti(conn, utente: str) -> list:
    """I gruppi dell'ultimo token di quella persona, o [] se non li abbiamo."""
    if not utente:
        return []
    with conn.cursor() as cur:
        cur.execute("SELECT gruppi FROM gruppi_utente WHERE utente = %s", (utente,))
        riga = cur.fetchone()
    return (riga["gruppi"] if isinstance(riga, dict) else riga[0]) if riga else []


def _risposta_unica(testo: str):
    """Una risposta finta ma ben formata: LibreChat si aspetta lo stream SSE."""
    def gen():
        yield f"data: {json.dumps({'choices': [{'delta': {'role': 'assistant', 'content': testo}, 'index': 0}], 'model': MODEL_NAME})}\n\n"
        yield f"data: {json.dumps({'choices': [{'delta': {}, 'index': 0, 'finish_reason': 'stop'}], 'model': MODEL_NAME})}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


def _senza_aggiunte(messaggio: dict) -> dict:
    """Toglie dalla cronologia TUTTE le righe che ha scritto il SISTEMA, non il
    modello: l'elenco delle fonti, l'offerta delle immagini, i collegamenti
    alle figure, e il filetto che le separa.

    Rimandargliele indietro lo porta a imitarle. Il 20/09/2026 era l'offerta a
    comparire due volte; il 21/09/2026, sulla stessa conversazione, erano le
    FONTI: il modello ricopiava di sana pianta l'elenco del turno prima e il
    sistema gli accodava quello vero, con numeri diversi. Due blocchi «Fonti»
    di seguito, e i riferimenti [n] del primo puntavano ai pezzi di un altro
    turno — cioe' esattamente il contrario di quello che l'elenco serve a
    fare. Al modello interessa cosa ha detto, non come il sistema ha decorato
    la risposta."""
    if messaggio.get("role") != "assistant":
        return messaggio
    righe = []
    dopo_immagine = False
    for r in messaggio["content"].splitlines():
        if MARCA_OFFERTA in r or MARCA_FONTI in r:
            continue
        if "![immagine" in r:
            # Con le figure se ne vanno intestazione e separatore della loro
            # tabella, che altrimenti restano orfani. Si tolgono solo QUI,
            # attaccati a una riga di figure: «|---|---|» da solo e' anche il
            # separatore di una tabella scritta dal modello, e quella resta.
            while righe and IMPALCATURA_TABELLA.match(righe[-1]):
                righe.pop()
            # La riga delle didascalie viene SUBITO dopo quella delle figure:
            # la riconosce la posizione, non un marcatore — e' testo piano in
            # una cella di tabella, e dal 24/09/2026 niente <sub> la segna.
            dopo_immagine = True
            continue
        if dopo_immagine and r.lstrip().startswith("|"):
            dopo_immagine = False
            continue
        dopo_immagine = False
        righe.append(r)
    # Il filetto restava orfano dell'elenco che introduceva.
    while righe and righe[-1].strip() in ("---", ""):
        righe.pop()
    testo = "\n".join(righe).strip()
    # I collegamenti [n](url) e il numero di fonte in piccolo che il SISTEMA
    # mette nel corpo: in cronologia il modello li ricopia parola per parola e
    # produce link annidati («[Pagina [5](url)¹](url)») e URL per esteso
    # (misurato il 30/09/2026 su un follow-up di «nastri bianchi con cuori
    # rossi»). In cronologia serve solo la prosa del modello: «pagina 5» gia' c'e'.
    testo = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", testo)
    testo = re.sub(r"[¹²³⁴⁵⁶⁷⁸⁹]", "", testo)
    return {**messaggio, "content": testo}


def _domanda(messages) -> str:
    """L'ultimo messaggio dell'utente: e' la domanda di questo turno."""
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"].strip()
    return ""


def _sse(testo: str, primo: bool = False) -> str:
    """Un pezzo di risposta nel formato che LibreChat si aspetta."""
    corpo = {"choices": [{"delta": {"role": "assistant", "content": testo},
                          "index": 0}]}
    if primo:
        corpo["model"] = MODEL_NAME
    return f"data: {json.dumps(corpo)}\n\n"


def _grafo_in_streaming(conn, domanda, gruppi, storico, utente,
                        conversation_id, inizio):
    """La risposta del grafo mandata mentre nasce.

    Il grafo gira in un thread e il redattore spinge i suoi pezzi in una
    coda; qui si svuota la coda verso il browser. L'ordine e' garantito e
    `conn` non e' mai usata da due parti insieme: il thread finisce prima
    che il generatore esca dal ciclo.

    Le citazioni sono gia' collegate quando arrivano (`grafo` le risolve nel
    flusso) e le fonti sono l'ultimo pezzo: qui non si trasforma niente.
    """
    pezzi = queue.Queue()
    esito = {}

    def lavora():
        try:
            esito["out"] = grafo.cerca(conn, domanda, gruppi,
                                       storia=_storia(storico),
                                       su_pezzo=pezzi.put,
                                       base=f"https://{APP_HOST}", utente=utente)
        except Exception as e:          # il thread non deve morire in silenzio
            esito["errore"] = e
        finally:
            pezzi.put(None)

    threading.Thread(target=lavora, daemon=True).start()

    def gen():
        mandato, primo = [], True
        try:
            while True:
                pezzo = pezzi.get()
                if pezzo is None:
                    break
                mandato.append(pezzo)
                yield _sse(pezzo, primo)
                primo = False
            righe, risposta, traccia = esito.get("out") or ([], "", {})
            if esito.get("errore"):
                e = esito["errore"]
                print(f"grafo: {type(e).__name__}: {e}", flush=True)
                if not mandato:
                    testo = "Non sono riuscito a rispondere a questa domanda."
                    mandato.append(testo)
                    yield _sse(testo, primo)
            elif not mandato and risposta:
                # Il redattore non ha streammato (puo' succedere se il turno
                # finisce senza passare di li'): si manda il testo reso.
                testo = (risposta if traccia.get("resa") else
                         grafo.rendi(conn, risposta, righe, f"https://{APP_HOST}",
                                     utente, gruppi, traccia.get("etichette")))
                mandato.append(testo)
                yield _sse(testo, primo)
            yield "data: [DONE]\n\n"
        finally:
            righe, _, traccia = esito.get("out") or ([], "", {})
            _registra_traccia(conn, conversation_id, utente, domanda, righe,
                              {"rotta": "grafo"}, None, None,
                              int((time.monotonic() - inizio) * 1000),
                              traccia=traccia, risposta="".join(mandato))
            conn.close()

    return StreamingResponse(gen(), media_type="text/event-stream")


def _storia(messaggi) -> list:
    """La cronologia da dare all'agente: i soli turni di utente e assistente,
    privi delle decorazioni del sistema (link, fonti, offerte delle immagini).
    Il modello che rivede i collegamenti del turno prima li ricopia e produce
    link annidati e URL per esteso (misurato il 30/09/2026)."""
    return memoria.comprimi([
        _senza_aggiunte(m) for m in messaggi
        if isinstance(m.get("content"), str) and m.get("role") in ("user", "assistant")
    ])


# Il prompt che LibreChat manda per chiedere il TITOLO automatico della
# conversazione. Non e' una domanda sui documenti: e' una richiesta di servizio
# del frontend. Se passa dal flusso di ricerca, intasa il modello e fa scattare
# i timeout (misurato il 23/09/2026).
TITOLO_LIBRECHAT = re.compile(
    r"provide a concise,?\s+5-word-or-less\s+title", re.I
)


def _e_titolo_librechat(domanda) -> bool:
    return bool(domanda) and bool(TITOLO_LIBRECHAT.search(domanda))


def _titolo_librechat(domanda):
    """Genera il titolo della conversazione, senza ricerca ne' gate.

    LibreChat manda gia' l'intera conversazione dentro la domanda; qui la si
    rimanda al modello e si restituisce la risposta. Nessun filtro ACL: non si
    leggono documenti, si riassume solo cio' che l'utente ha gia' scritto e
    visto nella sua stessa chat."""
    messaggi = [{"role": "system", "content": SUFFISSO_SISTEMA},
                {"role": "user", "content": domanda}]

    def gen():
        try:
            for pezzo in _stream_litellm(messaggi, os.environ.get("LLM_RAGIONAMENTO", "ragionamento"), {}):
                yield pezzo
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'upstream_error'}})}\n\n"
            yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


def _e_chiacchiera(domanda) -> bool:
    """Saluto o chiacchiera (non una ricerca)? Una risposta secca del modello,
    senza ragionamento: costa ~1,5 s e salva l'intera ricerca sui saluti."""
    if not domanda or len(domanda.strip()) > 120:
        return False
    try:
        testo = modello.chiedi([{
            "role": "system",
            "content": ("Rispondi SOLO «si» o «no».\n"
                        "«si» = e' un saluto, un ringraziamento o una chiacchiera "
                        "che NON chiede di cercare nei documenti (es. «ciao», "
                        "«grazie», «come stai», «ok»).\n"
                        "«no» = chiede qualcosa da cercare nei documenti."),
        }, {"role": "user", "content": domanda}], max_tokens=8)
        return testo.strip().lower().startswith("si")
    except Exception:
        return False


def _chiacchiera(domanda):
    """Risposta diretta a un saluto: niente ricerca, niente ragionamento."""
    try:
        testo = modello.chiedi([{"role": "system", "content": SUFFISSO_SISTEMA},
                                {"role": "user", "content": domanda}])
    except Exception:
        testo = "Ciao! Come posso aiutarti?"
    return _risposta_unica(testo)


# Le immagini non si attaccano piu' a ogni risposta: si OFFRONO, e si mostrano
# a chi le chiede. Vederle serve di rado (un catalogo, uno schema), e quattro
# figure in fondo a ogni risposta sono rumore che nasconde il testo.
# IMMAGINI_SU_RICHIESTA=0 torna al comportamento di prima.
SU_RICHIESTA = os.environ.get("IMMAGINI_SU_RICHIESTA", "1") != "0"

# 1 = ricerca multiagentica (grafo.py, D23): cinque agenti invece di uno, con
# il critico che verifica riga per riga e le citazioni [[n]] al posto del
# «pagina N» da reinterpretare. 0/assente = il flusso di oggi, identico.
GRAFO = os.environ.get("GRAFO", "") == "1"
# Frase dell'offerta. Contiene MARCA: al turno dopo si guarda se l'assistente
# aveva davvero offerto qualcosa, prima di interpretare un "si" come consenso.
MARCA_OFFERTA = "immagini collegate a questa risposta"
# Offerta di mostrare il resto dell'elenco: al turno dopo si riconosce il "si'".
MARCA_ALTRO = "Vuoi che te le elenchi tutte?"
# Le due marche servono a due cose insieme: scrivere la riga (_fonti_citate,
# _blocco_offerta) e RICONOSCERLA nella cronologia per toglierla
# (_senza_aggiunte). Una sola costante, cosi' non possono divergere.
MARCA_FONTI = "_Fonti: "
# I numeri di fonte in piccolo (¹²³…) con cui si marca ogni affermazione; il
# decimo in poi non ha un glifo Unicode e si scrive fra parentesi quadre.
APICI = "¹²³⁴⁵⁶⁷⁸⁹"


def _apice(n: int) -> str:
    return APICI[n - 1] if 1 <= n <= len(APICI) else f"[{n}]"
# Intestazione e riga di separazione della tabella delle figure: celle VUOTE,
# solo barre, trattini e spazi. Tolta la riga con le immagini resterebbero
# orfane, e il modello se le rivedrebbe in cronologia. Una tabella vera del
# modello ha del testo nelle celle, quindi non combacia.
IMPALCATURA_TABELLA = re.compile(r"^\s*\|[\s|:-]*\|\s*$")
# "Si", "mostra", "fammi vedere": un consenso, non una domanda. Deve essere un
# messaggio breve, altrimenti "quali immagini ci sono nel catalogo?" verrebbe
# scambiato per un si'.
CONSENSO = re.compile(r"^(si|sì|certo|ok|va bene|volentieri|vedi|vediamo|mostra\w*|"
                      r"fammi vedere|fammele vedere|le voglio vedere|immagini|foto|figure)\b", re.I)


# "Fammi vedere le foto dei diffusori" e' una domanda E una richiesta di
# immagini: si risponde CON le figure, non offrendole. Si cercano i nomi delle
# figure (foto, immagine, figura, illustrazione) e non verbi generici come
# "vedere", che compaiono anche in "vorrei vedere se avete profumatori".
CHIEDE_IMMAGINI = re.compile(r"\b(foto|fotografi\w*|immagin\w*|figur\w*|illustrazion\w*)\b", re.I)


def chiede_le_immagini(domanda: str) -> bool:
    """L'utente ha chiesto lui stesso di vedere le figure."""
    return bool(CHIEDE_IMMAGINI.search(domanda or ""))


def vuole_le_immagini(domanda: str, ultima_risposta: str) -> bool:
    """True se l'utente sta dicendo di si' a un'offerta di immagini appena
    fatta. Servono ENTRAMBE le condizioni: l'offerta nel turno precedente e una
    risposta breve e affermativa."""
    if not (ultima_risposta and MARCA_OFFERTA in ultima_risposta):
        return False
    d = domanda.strip()
    return len(d) <= 60 and bool(CONSENSO.match(d))


def vuole_il_resto(domanda: str, ultima_risposta: str) -> bool:
    """True se l'utente dice di si' all'offerta «vuoi che te le elenchi tutte?».
    Stesso meccanismo di vuole_le_immagini: l'offerta prima, il consenso dopo."""
    if not (ultima_risposta and MARCA_ALTRO in ultima_risposta):
        return False
    d = domanda.strip()
    return len(d) <= 60 and bool(CONSENSO.match(d))


def _ultima_risposta(messages) -> str:
    """L'ultimo messaggio dell'assistente: serve a sapere se l'offerta c'e' stata."""
    for m in reversed(messages):
        if m.get("role") == "assistant" and isinstance(m.get("content"), str):
            return m["content"]
    return ""


def _domanda_precedente(messages) -> str:
    """La domanda prima di questa: e' quella a cui le immagini si riferiscono."""
    trovate = [m["content"].strip() for m in messages
               if m.get("role") == "user" and isinstance(m.get("content"), str)]
    return trovate[-2] if len(trovate) > 1 else ""


# "Quali fragranze ci sono?", "elenca i formati", "che colori avete": la
# risposta sta in DIECI pezzi diversi, uno per variante, non negli otto che
# bastano a una domanda puntuale. Su un catalogo otto pezzi danno un elenco di
# due voci e sembra che il resto non esista (visto il 20/09/2026).
ELENCO = re.compile(r"\b(quali|quante|elenca|elencare|lista|tutt[ei]|che\s+\w+\s+ci\s+sono|"
                    r"che\s+\w+\s+(avete|ci sono|esistono)|assortimento|gamma|catalogo completo)\b", re.I)
PEZZI_ELENCO = 120


def pezzi_da_recuperare(domanda: str) -> int:
    """Quanti pezzi mettere nel contesto: un pool AMPIO per i cataloghi, dove
    una sola domanda copre tante pagine (»un sacco di risultati», 25/09/2026);
    per l'elenco completo ancora di piu'. Lo strozzo a 8 aveva fatto sparire
    interi gruppi di articoli dalla risposta."""
    return PEZZI_ELENCO if ELENCO.search(domanda or "") else 60


def _fonti_citate(risposta, righe, base: str = "", utente: str = "") -> str:
    """Documento e pagina di ogni pezzo CITATO, in coda alla risposta.

    Il modello cita «[7]» e basta: chi legge non ha modo di sapere che [7] e'
    «CATALOGO IPURO 2025.pdf, pagina 8», quindi per verificare deve sfogliare a
    mano — ed e' successo davvero il 20/09/2026, con un utente che ha concluso
    «non c'e'» su un prodotto che stava a pagina 8. Il disclaimer chiede di
    verificare sempre la fonte citata: senza questo elenco non e' possibile.
    I numeri corrispondono all'ordine in cui i pezzi entrano nel CONTESTO
    (prompt.contesto), quindi [n] qui e [n] nella risposta sono lo stesso pezzo.

    Nella coda vanno i pezzi che il corpo CITA, non tutti quelli recuperati: la
    coda spiega i numeri che si leggono nella risposta, quindi una voce che
    nel corpo non c'e' e' un numero che non porta a niente (26/09/2026: dodici
    voci per una risposta che ne citava tre).

    Una pagina che compare in piu' pezzi si scrive UNA volta sola, con tutti i
    suoi numeri davanti. Su un catalogo e' la norma — un prodotto occupa testo,
    tabella e descrizione della figura — e l'elenco veniva fuori cosi':
    «[2] pagina 19 · [3] pagina 20 · [4] pagina 20 · [5] pagina 20». Otto voci
    per due pagine: chi legge non ha piu' voglia di verificare niente.
    """
    if not risposta or not righe:
        return ""
    per_pagina = {}
    for i, r in enumerate(righe, 1):
        if not re.search(rf"\[{i}\]", risposta):
            continue
        per_pagina.setdefault((r.get("documento"), r.get("page"),
                               r.get("source_id")), []).append(i)
    if not per_pagina:
        return ""
    voci = []
    for (documento, page, source_id), numeri in per_pagina.items():
        pagina = f", pagina {page}" if page is not None else ""
        etichetta = f"{documento}{pagina}"
        # La citazione diventa un COLLEGAMENTO al documento, aperto alla
        # pagina. E' la verifica che il disclaimer chiede: finora si poteva
        # solo sfogliare a mano, e il 20/09/2026 un utente ha concluso «non
        # c'e'» su un prodotto che stava a pagina 8. Ed e' anche la risposta
        # onesta a «questa figura di che prodotto e'?»: il collegamento non
        # afferma niente, mostra la pagina impaginata dal fornitore, dove il
        # codice sta sotto la sua fotina.
        if base and source_id:
            url = documento_mod.firma_url(source_id, documento, page, base, utente)
            etichetta = f"[{etichetta}]({url})"
        voci.append("".join(f"[{n}]" for n in numeri) + f" {etichetta}")
    # Le fonti in riga di citazione: le stesse dei cataloghi (numeri in piccolo,
    # fonti in coda), cosi' che la provenienza si legga uguale su un manuale e
    # su un catalogo.
    return "\n\n---\n> " + MARCA_FONTI + " · ".join(voci) + "_"


def _figura_in_breve(descrizione) -> str:
    """La descrizione della figura: solo l'OGGETTO, non il testo tecnico
    (Material/Shape/Colours stanno nella pagina, a cui porta il link)."""
    d = descrizione or ""
    if "Object:" in d:
        d = d.split("Object:", 1)[1]
    d = d.split("Material:", 1)[0]
    return " ".join(d.split())[:220]


def _figura_distinta(descrizione) -> str:
    """La chiave con cui riconoscere che due didascalie fotografano la STESSA
    figura: l'inizio del breve, con nome e codice del prodotto. Molti crop
    inquadrano la stessa poinsettia e condividono il prefisso; due prodotti
    diversi dello stesso catalogo no (verificato il 26/09/2026: la stessa
    «Poinsettie x5 im Topf» tornava quindici volte in un elenco)."""
    return _figura_in_breve(descrizione)[:64]


def _elenco_figure(righe, base, utente, tutte=False) -> str:
    """Le figure trovate, in un elenco strutturato: descrizione + link alla
    pagina. Lo si usa quando si chiedono TUTTE le voci o quando l'agente non ha
    risposto: nella risposta normale i prodotti li narra l'agente in prosa e i
    collegamenti li mette `_risposta_prodotti`.

    Non si elencano tutte (se `tutte` e' falso): se ne mostrano al massimo
    LIMITE_ELENCO, e si offre il resto invece di annegare la risposta. Una
    pagina di catalogo mostra piu' prodotti, ma molti crop inquadrano lo
    stesso prodotto: si deduplicano le didascalie DISTINTE (vedi
    `_figura_distinta`), per pagina resta la prima occorrenza di ognuna."""
    voci, viste = [], set()
    contate = 0
    for r in righe:
        if r.get("descrizione") is None:
            continue
        chiave = (r.get("documento"), r.get("page"), _figura_distinta(r["descrizione"]))
        if chiave in viste:
            continue
        viste.add(chiave)
        contate += 1
        if not tutte and contate > LIMITE_ELENCO:
            continue
        descr = _figura_in_breve(r.get("descrizione"))
        url = documento_mod.firma_url(r.get("source_id"), r.get("documento"),
                                      r.get("page"), base, utente)
        pag = f", pagina {r['page']}" if r.get("page") is not None else ""
        voci.append(f"- **{descr}** — [{r.get('documento')}{pag}]({url})")
    resto = contate - len(voci)
    testo = "\n".join(voci)
    if resto > 0 and not tutte:
        testo += f"\n\n_Ci sono altre {resto} voci. Vuoi che te le elenchi tutte?_"
    return testo


def _norma(testo: str) -> str:
    """Il testo appiattito (senza spazi, punteggiatura, estensione): per
    riconoscere «Catalogo Gasper Autunno Natale 2026.pdf» nella prosa
    dell'agente anche quando lui scrive «pagina 20 del catalogo Gasper»."""
    return re.sub(r"\W+", "", testo or "").lower()


# Parole che aprono il nome di un file e non lo identificano: un «catalogo»
# non distingue un documento da un altro.
_GENERICI = {"catalogo", "catalog", "guida", "manuale", "listino", "collana",
             "brochure", "de", "di"}


def _varianti(nome: str) -> set:
    """Come il documento puo' essere scritto in prosa: per intero, senza
    estensione, e con la sola parola che lo identifica («Gasper»,
    «FLEURAMI»). Il docstring promises gia' la forma breve ma il codice non la
    cercava: su «Nei due documenti FLEURAMI (pagine 27 e 29)» nessuna variante
    del nome trovava la frase, e la citazione finiva abbinata a un altro
    catalogo — un link a una pagina che non contiene quello che si sta
    dicendo (misurato il 30/09/2026: FLEURAMI 27-29 linkato a Gasper)."""
    fuori = {_norma(nome)}
    if nome.lower().endswith(".pdf"):
        fuori.add(_norma(nome[:-4]))
    for parola in re.split(r"\W+", nome)[:3]:
        if len(parola) > 3 and parola.lower() not in _GENERICI:
            fuori.add(_norma(parola))
            break
    return {v for v in fuori if v}


def _documento_vicino(prima: str, dopo: str, per_doc: dict) -> str | None:
    """A quale documento recuperato appartiene una citazione «pagina N»:
    all'ultimo documento nominato PRIMA della citazione (la sezione di catalogo
    che la contiene, anche a molte righe di distanza), che e' come si legge il
    testo. Finche' si guardava solo una finestra stretta attorno alla citazione,
    le pagine in fondo a un lungo elenco restavano orfane, senza collegamento
    (verificato il 26/09/2026 con elenchi su piu' cataloghi). Se nessun
    documento precede la citazione, si prende il piu' vicino nel testo che la
    segue («pagina 20 del catalogo Gasper»). Un documento recuperato ma mai
    nominato non puo' possedere citazioni. Il documento si riconosce anche da
    una sola parte del nome («Gasper»)."""
    candidati = [(_nome[0], _varianti(_nome[0])) for _nome in per_doc.values()]
    app = _norma(prima)
    ultimo, pos, migliore = None, -1, ""
    for nome, varianti in candidati:
        for cand in varianti:
            # A parita' di posizione vince la variante piu' lunga: il nome
            # intero e' piu' sicuro della parola sola.
            p = app.rfind(cand)
            if p > pos or (p == pos and p >= 0 and len(cand) > len(migliore)):
                ultimo, pos, migliore = nome, p, cand
    if ultimo:
        return ultimo
    app = _norma(dopo)
    migliore, dist, corta = None, 10 ** 9, ""
    for nome, varianti in candidati:
        for cand in varianti:
            p = app.find(cand)
            if p < 0:
                continue
            # Vince il nome che appare per primo; a parita' quello piu' lungo.
            if p < dist or (p == dist and len(cand) > len(corta)):
                migliore, dist, corta = nome, p, cand
    return migliore


def _collega_pagine(risposta, righe, conn, base, utente, gruppi=()):
    """I riferimenti «pagina N» nella prosa dell'agente diventano collegamenti
    alla pagina del documento da cui arrivano. La pagina si risolve su TUTTE le
    figure dei documenti coinvolti nel recupero (non solo quelle tornate in
    `righe`): un articolo che l'agente cita a una pagina non richiamata nel
    turno trova comunque il suo collegamento. Mai un collegamento a una pagina
    che non esiste, o a un documento non recuperato. Torna
    (testo, {chiavi (documento, pagina) collegate}, `ordine`): `ordine` assegna
    a ogni documento il suo numero di fonte in piccolo (¹, ²…), nell'ordine in
    cui e' citato per la prima volta, per marcare l'affermazione con la fonte
    (26/09/2026)."""
    if not risposta or not righe:
        return risposta, set(), []
    validi, per_doc = {}, {}
    mancanti = set()
    for r in righe:
        if r.get("page") is None or r.get("documento") is None:
            continue
        source_id = r.get("source_id")
        if not source_id:
            mancanti.add(r["documento"])
            source_id = ""
        validi.setdefault((r["documento"], r["page"]), source_id)
        per_doc.setdefault(_norma(r["documento"]), (r["documento"], source_id))
    # `source_id` serve per firmare il link e il modello non e' obbligato a
    # chiederlo nella SELECT (col flag SQL_AGENTE sceglie le colonne e potrebbe
    # non scriverlo: KeyError, chat in 500, il 30/09/2026). Non si obbliga il
    # modello a ricordarsene e NON si legge `immagini` per il fatto: la lettura
    # va protetta dagli stessi permessi del resto, quindi si risolve dalla
    # tabella dei documenti e ogni candidato passa per `visibile`, che e' il
    # controllo gia' usato per aprire una pagina. Un documento non visibile
    # semplicemente resta senza firma.
    if mancanti:
        for riga in conn.execute("SELECT DISTINCT source_id, documento "
                                 "FROM documenti WHERE documento = ANY(%s)",
                                 (list(mancanti),)):
            if not documento_mod.visibile(conn, riga["source_id"], gruppi):
                continue
            documento, source_id = riga["documento"], riga["source_id"]
            per_doc[_norma(documento)] = (documento, source_id)
            for chiave in list(validi):
                if chiave[0] == documento:
                    validi[chiave] = source_id
    for nome, source_id in set(per_doc.values()):
        if not source_id:
            continue
        for riga in conn.execute(
            "SELECT page FROM immagini WHERE source_id=%s AND documento=%s AND page IS NOT NULL",
            (source_id, nome)):
            validi.setdefault((nome, riga["page"]), source_id)
    if not validi:
        return risposta, set(), []
    da_sostituire, collegati = [], set()
    ordine, numeri = [], {}
    for m in re.finditer(r"(?i)\b(?:pagin[ae]|pagg?\.?|pp\.|p\.)\s*(\d+(?:\s*(?:,|;|e|–|-)\s*\d+)*)", risposta):
        doc = _documento_vicino(risposta[:m.start()], risposta[m.end():m.end() + 80], per_doc)
        if not doc and len(per_doc) == 1:
            doc = next(iter(per_doc.values()))[0]
        if not doc:
            continue
        pezzi, cursore, fatto, source_id = [], m.start(), False, None
        for d in re.finditer(r"\d+", risposta[m.start():m.end()]):
            num = int(d.group())
            pre = risposta[cursore:m.start() + d.start()]
            if (doc, num) in validi:
                url = documento_mod.firma_url(validi[(doc, num)], doc, num, base, utente)
                pezzi.append(pre + f"[{num}]({url})")
                collegati.add((doc, num))
                fatto = True
                source_id = validi[(doc, num)]
            else:
                pezzi.append(pre + d.group())
            cursore = m.start() + d.end()
        pezzi.append(risposta[cursore:m.end()])
        if fatto:
            if doc not in numeri:
                apice = _apice(len(ordine) + 1)
                numeri[doc] = apice
                ordine.append((doc, source_id, apice))
            # Il numero in piccolo dopo il collegamento marca l'affermazione.
            da_sostituire.append((m.start(), m.end(), "".join(pezzi) + numeri[doc]))
    for inizio, fine, testo in sorted(da_sostituire, reverse=True):
        risposta = risposta[:inizio] + testo + risposta[fine:]
    return risposta, collegati, ordine


# La coda «Fonti:» in fondo alle risposte di catalogo e' ACCESA di default dal
# 26/09/2026: le fonti si lasciano, solo in piccolo (riga in citazione) e
# numerate — ogni affermazione porta il numero della fonte a pedice. La vecchia
# coda «Altre pagine con i prodotti:» era spenta perche' re-iniettava come
# prodotti le didascalie che l'agente aveva scartato («Apple», «nail polish
# bottles»); qui invece si elencano solo documento e pagine. Si ricomincia la
# procedura: CODA_ARTICOLI=0 la disattiva, e quando e' accesa le pagine si
# deduplicano.
AUTOCODA = os.environ.get("CODA_ARTICOLI", "1") != "0"


def _risposta_prodotti(risposta, righe, conn, base, utente, gruppi=()) -> str:
    """La risposta di catalogo finita: la prosa dell'agente con i riferimenti
    «pagina N» trasformati in collegamenti e ogni affermazione marcata dal
    numero in piccolo della sua fonte. In coda, le Fonti (documento e pagine)
    in piccolo, numerate: a ogni fonte il suo numero, nell'ordine in cui e'
    citata nel corpo. La coda si disattiva con CODA_ARTICOLI=0.

    Le pagini della coda sono quelle CITATE nel corpo, non tutte quelle
    recuperate: e' la spiegazione dei numeri in piccolo, quindi elencare pagine
    che il corpo non nomina mette in coda voci che non si possono seguire
    (26/09/2026: «pagine 20, 24, 30, 40, 42, 60, 65, 85, 112, 116, 131, 132,
    133, 134, 135» per una risposta che ne citava sei)."""
    testo, collegati, ordine = _collega_pagine(risposta, righe, conn, base, utente, gruppi)
    if not AUTOCODA or not collegati:
        return testo
    # Per costruzione le pagine citate e i documenti numerati sono gli stessi:
    # una pagina finisce in `collegati` solo mentre al suo documento si assegna
    # il numero in piccolo. Quindi qui la coda non puo' divergere dal corpo.
    citate = {}
    for doc, pagina in collegati:
        citate.setdefault(doc, set()).add(pagina)
    voci = []
    for doc, source_id, apice in ordine:
        pagine = ", ".join(str(p) for p in sorted(citate.get(doc, ())))
        # Anche il NOME del documento e' un collegamento (alla prima pagina), come
        # le pagine nel corpo: stessa firma, stesso meccanismo, nessun ramo nuovo.
        url = documento_mod.firma_url(source_id, doc, None, base, utente)
        voci.append(f"{apice} [{doc}]({url}) — pagine {pagine}")
    testo += "\n\n---\n> " + MARCA_FONTI + " · ".join(voci) + "_"
    return testo


LIMITE_ELENCO = 5        # quante figure elencare prima di offrire il resto


PER_RIGA = 4        # quante figure affiancare
# Quanto puo' essere lunga una didascalia: oltre, la riga della tabella va a
# capo e le miniature si disallineano.
DIDASCALIA = 44


def _sotto_la_figura(riga, didascalia: str) -> str:
    """Cosa c'e' scritto sotto una miniatura.

    L'etichetta se l'abbiamo; altrimenti «pagina N», che sappiamo SEMPRE.
    Per 468 figure su 891 un'etichetta nell'ordine di lettura non esiste
    (misurato il 22/09/2026), e senza ripiego quelle resterebbero mute — senza
    didascalia e, quel che conta, senza il collegamento per andare a vedere.

    «pagina 7» non afferma niente su cosa mostri la figura: dice dove
    guardare, ed e' vero per costruzione. E' la differenza fra un sistema che
    tace e uno che si inventa un'etichetta per riempire la casella.
    """
    if didascalia:
        return didascalia
    n = (riga or {}).get("page") if riga else None
    return f"pagina {n}" if n else ""


def _pagina_url(riga, utente: str) -> str:
    """Il collegamento alla pagina da cui viene una figura, o "" se non si sa.

    `riga` e' la riga della ricerca delle immagini (documento, page,
    source_id). Quando la figura arriva dal ripiego per pagina quella riga non
    c'e': niente collegamento, e va bene — meglio nessun collegamento che uno
    che apre la pagina sbagliata.
    """
    if not riga or not riga.get("source_id") or not riga.get("documento"):
        return ""
    return documento_mod.firma_url(riga["source_id"], riga["documento"], riga.get("page"),
                                   f"https://{APP_HOST}", utente)


def _didascalia(descrizione: str) -> str:
    """Cosa scrivere sotto una miniatura.

    La descrizione di una figura e' «<testo del catalogo che PRECEDE la
    figura> — <descrizione del modello visivo>» (vedi prima_dei_segnaposti in
    ingestion). La didascalia usa la PRIMA meta': sono le parole del catalogo,
    con i codici articolo.

    Non dice «questa e' FSA1001», dice cosa c'e' scritto prima. Resta un
    indizio — se il layout mette il codice altrove, la figura resta senza — ma
    dal 22/09/2026 non e' piu' ambiguo: la finestra si ferma al segnaposto
    precedente, quindi porta al massimo il codice di UN prodotto.

    La seconda meta' — il testo del modello visivo, in inglese — non si
    mostra: serve a cercare, non a leggere.
    """
    testo = " ".join((descrizione or "").split())
    # Il separatore c'e' SOLO se l'etichetta c'e': quando prima della figura
    # non c'era testo di catalogo, la descrizione salvata e' la sola frase del
    # modello visivo. Senza questo controllo la didascalia mostrerebbe quella
    # — una frase in inglese sotto una miniatura — spacciandola per l'etichetta
    # del prodotto (22/09/2026: 85 figure su 891).
    if " — " not in testo:
        return ""
    # Etichetta vuota: la descrizione salvata comincia col separatore, e
    # dividerla darebbe il CONTESTO DOPO — cioe' il prodotto successivo,
    # l'ambiguita' che tutto questo serve a togliere. Il 22/09/2026 usciva
    # «— metallic» sotto una miniatura, che non e' l'etichetta di niente.
    if testo.startswith("—"):
        return ""
    testo = testo.split(" — ")[0]
    # Il testo prima della figura e' tagliato a lunghezza fissa e comincia a
    # meta' parola: «nten diamonds & brilliants». Si riparte dalla prima
    # parola intera — una didascalia si legge, non si decifra.
    if " " in testo[:40]:
        testo = testo.split(" ", 1)[1] if not testo[:1].isupper() else testo
    # «nessun testo» e' quello che il modello scrive quando nel ritaglio non
    # c'e' niente da trascrivere: come didascalia non dice nulla.
    if testo.lower().startswith("nessun testo"):
        return ""
    return testo[:DIDASCALIA].rstrip(" ,;-") + ("…" if len(testo) > DIDASCALIA else "")


def _blocco_immagini(url_per_pos) -> str:
    """Le figure, in markdown: miniature cliccabili, quattro per riga.

    In chat serve riconoscere il prodotto, non leggerne le etichette: si manda
    la versione piccola (`mini=1`) e l'originale resta a un clic di distanza.
    Quattro figure di catalogo a piena risoluzione sono quasi 3 MB per
    risposta, e si vedono comunque rimpicciolite.

    Una TABELLA, non immagini di seguito: LibreChat applica `display: block`
    a ogni `img` (preflight di Tailwind, verificato nel CSS compilato), quindi
    metterle sulla stessa riga di markdown non basta — si impilerebbero lo
    stesso. Ogni cella invece e' un riquadro suo, e le celle stanno in fila.
    """
    # La miniatura porta all'immagine grande; la DIDASCALIA porta alla pagina
    # del documento. Da una figura che non convince si arriva in un clic alla
    # pagina impaginata dal fornitore, dove il codice sta sotto la sua fotina:
    # e' la verifica che il sistema non sa fare da solo per meta' delle figure.
    celle = []
    for n, voce in url_per_pos.items():
        u, d = voce[0], voce[1]
        pagina = voce[2] if len(voce) > 2 else ""
        celle.append((f"[![immagine {n}]({u}&mini=1)]({u})",
                      f"[{d}]({pagina})" if d and pagina else d))
    if not celle:
        return ""
    righe = ["|" + "|".join(" " * 2 for _ in range(PER_RIGA)) + "|",
             "|" + "|".join("---" for _ in range(PER_RIGA)) + "|"]
    for i in range(0, len(celle), PER_RIGA):
        gruppo = celle[i:i + PER_RIGA]
        gruppo += [(" ", "")] * (PER_RIGA - len(gruppo))    # celle vuote in coda
        righe.append("| " + " | ".join(c for c, _ in gruppo) + " |")
        # La didascalia sotto la sua miniatura, nella riga seguente della
        # STESSA tabella: cosi' resta incolonnata con l'immagine anche quando
        # il testo va a capo. Niente HTML: LibreChat lo stampa letterale,
        # quindi <sub> compariva nella risposta. Si salta se nessuna delle
        # quattro ne ha una.
        if any(d for _, d in gruppo):
            righe.append("| " + " | ".join(d or " " for _, d in gruppo) + " |")
    return "\n".join(righe)


def _scelte(trovate):
    """QUANTE figure mostrare, non solo quali.

    Due casi, e la differenza la dice il punteggio di RARITA' che la ricerca
    calcola gia' (`rarita`: la somma dei termini rari della domanda che
    compaiono nella descrizione della figura).

    1. Qualcuna contiene cio' che la domanda NOMINA. Allora sono quelle, e
       basta. Il 22/09/2026 «immagine del prodotto GLA3094» dava una figura
       con rarita' 7,3 e tutte le altre a zero: mostrarne quattro voleva dire
       affermare che anche le altre tre c'entravano.

    2. Nessuna, o tutte allo stesso modo — una domanda descrittiva come
       «sassi rossi», dove il criterio esatto non c'e'. Allora si tengono
       quelle vicine alla migliore (QUOTA_PEGGIO) e si smette quando il salto
       e' netto: non e' una verita', e' un campione onesto che si ferma da
       solo invece di riempire.

    In entrambi i casi si passa da `_sparse`, che copre soggetti diversi
    invece di dare quattro quasi-doppioni.
    """
    if not trovate:
        return []
    massimo = max(r.get("rarita") or 0 for r in trovate)
    pari = [r for r in trovate if (r.get("rarita") or 0) >= massimo]
    # La rarita' decide solo se DISTINGUE. Se tutte le candidate hanno lo
    # stesso punteggio non ha separato niente: e' il caso di «sassi rossi»,
    # dove «sass» e' raro nell'archivio (che e' in tedesco) e compare in
    # decine di descrizioni. Senza questo controllo uscivano 48 figure — il
    # contrario di quello che questa funzione deve fare.
    if massimo > 0 and len(pari) < len(trovate):
        return _sparse(pari, min(len(pari), MAX_IMMAGINI))
    # Qui NON si sa quante servano, e il numero giusto non lo sa nessuno: si
    # tengono quelle vicine alla migliore e non piu' di CAMPIONE. Il tetto
    # serve perche' l'incertezza non diventi abbondanza — il 22/09/2026
    # «immagine del prodotto GRA1041», che figura non ha, ne faceva uscire
    # otto di altri prodotti: quando non si sa si mostra MENO, non di piu'.
    vicine = [r for r in trovate
              if (r.get("distanza") or 0) <= (trovate[0].get("distanza") or 0) * (1 + QUOTA_PEGGIO)]
    return _sparse(vicine or trovate[:1], min(len(vicine) or 1, CAMPIONE))


def _sparse(trovate, quante=None):
    """Le figure scelte COPRENDO cose diverse, non le quattro piu' vicine.

    Prendere i primi quattro risultati di una ricerca a somiglianza da'
    quattro quasi-doppioni: il 22/09/2026, a «immagini dei sassi rossi», sono
    uscite due figure di FSA1001 e due di RAD1001 mentre la risposta parlava
    di QUATTRO prodotti. Nessuna era sbagliata, e insieme raccontavano meta'
    della risposta.

    Non e' un difetto di questo catalogo: e' come si comporta una ricerca a
    somiglianza quando le prime posizioni descrivono la stessa cosa. Si fa un
    giro tenendo una figura per «soggetto» — qui la PAGINA, che e' quanto di
    piu' vicino a «di che prodotto parla» si abbia senza inventare — e poi, se
    restano posti, si riempie con le altre nell'ordine di punteggio.

    L'ordine dentro ogni giro resta quello della ricerca: non si riordina
    niente, si sceglie CHI passa.
    """
    quante = quante or MAX_IMMAGINI
    prime, resto, viste = [], [], set()
    for r in trovate:
        chiave = (r.get("documento"), r.get("page"))
        (resto if chiave in viste else prime).append(r)
        viste.add(chiave)
    return (prime + resto)[:quante]


def _immagini_per_la_domanda(conn, qvec, gruppi, righe, domanda=""):
    """Gli id delle figure degli ARTICOLI che hanno risposto, non della domanda.

    D23 (Sviluppo/TOOL-IMMAGINI.md): prima si trovano gli articoli (i chunk gia'
    recuperati), poi le loro figure — match per codice nella descrizione, e in
    mancanza la verifica LLM in una chiamata sola. Prima si cercava la DOMANDA
    per somiglianza vettoriale e, su «profumatori per auto», uscivano figure
    fuori tema pur dentro la pagina giusta.
    """
    trovate = immagini_articoli.per_articoli(conn, righe, gruppi, MAX_IMMAGINI)
    if trovate:
        return [(r["id"], _didascalia(r.get("descrizione")), r) for r in trovate]
    return [(i, "", None) for i in _immagini_del_turno(righe)]


def _immagini_del_turno(righe):
    """Le immagini dei pezzi recuperati, senza doppioni, fino a MAX_IMMAGINI.

    UNA per pezzo, partendo dai meglio piazzati: cosi' le figure vengono dalle
    pagine che hanno risposto alla domanda, invece di arrivare tutte dalla
    stessa. Serve perche' l'immagine e' legata al pezzo solo dalla PAGINA, e in
    un catalogo una pagina contiene dieci prodotti diversi: su "profumatori per
    auto" un pezzo pertinente stava in una pagina con 28 figure, e ne uscivano
    quattro che non c'entravano (20/09/2026).

    Resta un rattoppo: la correzione vera e' sapere COSA mostra ogni immagine
    (colonna `descrizione` su `immagini`) e scegliere le figure che rispondono
    alla domanda, non quelle che stanno vicino al testo che ha risposto."""
    ids = []
    for r in righe:                      # righe: gia' in ordine di punteggio
        for iid in (r.get("immagini") or [])[:1]:
            if iid not in ids:
                ids.append(iid)
    return ids[:MAX_IMMAGINI]


def _stream_litellm(messages, rotta, uso):
    """Streaming DIRETTO da llama-swap, cedendo i chunk OpenAI-compatible.
    `rotta` resta per compatibilita': il modello e' uno solo (locale). `uso` e'
    un dict da riempire con token_in/token_out letti dall'ultimo chunk."""
    for riga in modello.stream(messages, max_tokens=MAX_TOKEN,
                               temperature=TEMPERATURA):
        dato = riga[6:]
        if dato.strip() == "[DONE]":
            continue
        try:
            pezzo = json.loads(dato)
        except ValueError:
            continue
        if pezzo.get("usage"):
            uso["token_in"] = pezzo["usage"].get("prompt_tokens")
            uso["token_out"] = pezzo["usage"].get("completion_tokens")
            continue  # il chunk di usage non porta testo
        if pezzo.get("model"):
            pezzo["model"] = MODEL_NAME
        # Il modello ragiona ad alta voce: il ragionamento viaggia in
        # delta.reasoning_content. LibreChat non lo conosce: lo si scarta,
        # cosi' arriva solo la risposta.
        delta = (pezzo.get("choices") or [{}])[0].get("delta")
        if isinstance(delta, dict):
            delta.pop("reasoning_content", None)
            if "content" not in delta and not delta.get("tool_calls"):
                continue  # solo ragionamento (o chunk vuoto): non si mostra
        yield f"data: {json.dumps(pezzo)}\n\n"


def _registra_traccia(conn, conversation_id, utente, domanda, righe, decisione,
                      token_in, token_out, latenza_ms, riscritta=False, cercata=None,
                      traccia=None, risposta=None):
    # token_in/out arrivano dall'ultimo chunk di LiteLLM (stream_options
    # include_usage); se la rotta non li espone restano NULL (colonna ammessa).
    conn.execute(
        """INSERT INTO traces (conversation_id, utente, domanda, chunk_ids,
                               retrieval_vuoto, taint, modello, token_in, token_out, latenza_ms,
                               riformulazione, strumenti, ricerca, risposta)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (conversation_id, utente,
         # Nella traccia resta la domanda VERA; se e' stata riscritta per la
         # ricerca si annota anche quella, perche' una risposta strana si
         # spiega guardando cosa e' stato cercato davvero.
         domanda if not riscritta else f"{domanda}\n[cercata: {cercata}]",
         # `id` c'e' solo se il modello lo ha chiesto nella SELECT: con
         # SQL_AGENTE e' lui a scegliere le colonne e puo' scrivere
         # «SELECT documento, page» (misurato il 30/09/2026: KeyError, la chat
         # intera rispondeva 500). La colonna e' bigint[]: registra solo gli id
         # interi davvero tornati, e NULL quando il modello non li ha chiesti
         # (nessun chunk da segnare; la pagina resta in `risposta`).
         [r["id"] for r in righe
          if "id" in r and isinstance(r["id"], int)] or None,
         not righe,
         decisione.get("fonte_contaminante") if decisione.get("interno") else None,
         decisione.get("rotta"),
         token_in, token_out, latenza_ms, riscritta,
         # Cosa ha chiamato e cosa ha cercato davvero, e la risposta come e'
         # stata mandata: senza, ogni diagnosi su una risposta sbagliata e' una
         # ricostruzione a posteriori (migrazione 024).
         json.dumps(traccia.get("strumenti"), ensure_ascii=False) if traccia else None,
         (traccia or {}).get("ricerca"),
         risposta))
    conn.commit()


@app.post("/v1/chat/completions")
async def chat(request: Request):
    inizio = time.monotonic()
    corpo = await request.json()
    authorization = request.headers.get("authorization")
    conversation_id = request.headers.get("x-conversation-id") or None

    # 1. Identita: nessun percorso alternativo senza token valido.
    try:
        claim = identita.verifica(authorization)
    except identita.TokenNonValido as e:
        return JSONResponse({"error": {"message": str(e), "type": "invalid_request_error"}},
                            status_code=401)
    gruppi = identita.gruppi(claim)
    utente = claim.get("sub") or ""
    # Gruppi dell'ultimo token: servono a decidere, quando il browser chiede
    # un'immagine, se quella persona puo' ancora vederla (un <img> non porta
    # identita', solo i cookie del dominio).
    _ricorda_gruppi(conn_gruppi := _conn(), utente, gruppi)
    conn_gruppi.close()

    domanda = _domanda(corpo.get("messages", []))
    conn = _conn()

    # 1-quater. Il titolo automatico di LibreChat non e' una domanda sui
    # cataloghi: e' una richiesta di un titolo per la conversazione. Passava dal
    # flusso di ricerca (agente, glossario, vincoli, indice) come una domanda
    # vera, intasava il modello e faceva andare in timeout l'estrazione dei
    # vincoli (misurato il 23/09/2026: la via unica smetteva di scattare). Qui
    # si riconosce e si risponde direttamente col modello, senza ricerca: e' la
    # stessa conversazione che il frontend manderebbe a qualunque LLM.
    if _e_titolo_librechat(domanda):
        return _titolo_librechat(domanda)

    # 1-bis. Indicizzazione che ha scaricato il modello: si risponde e basta,
    # senza toccare LiteLLM. Nessuna traccia: non c'e' stato nessun turno.
    if _indice_in_aggiornamento(conn):
        conn.close()
        return _risposta_unica(INDICE_IN_AGGIORNAMENTO)

    # 1-ter. "Mostra le immagini": non e' una domanda nuova, e' il si' a
    # un'offerta. Si rifa' il recupero sulla domanda PRECEDENTE — cosi' le ACL
    # sono quelle di chi sta chiedendo adesso, come in ogni altro turno — e si
    # rispondono le figure, senza disturbare il modello.
    storico_messaggi = corpo.get("messages", [])
    if SU_RICHIESTA and vuole_le_immagini(domanda, _ultima_risposta(storico_messaggi)):
        precedente = _domanda_precedente(storico_messaggi)
        qvec_prec = recupero.embedding(precedente, query=True)
        righe, _ = recupero.cerca(conn, precedente, gruppi, qvec=qvec_prec)
        ids = _immagini_per_la_domanda(conn, qvec_prec, gruppi, righe, precedente)
        conn.close()
        if not ids:
            return _risposta_unica("Non ho immagini da mostrare per quella risposta.")
        urls = {i + 1: (immagini.firma_url(iid, f"https://{APP_HOST}", utente),
                        _sotto_la_figura(r, d), _pagina_url(r, utente))
                for i, (iid, d, r) in enumerate(ids)}
        return _risposta_unica("Ecco le figure delle pagine citate:\n\n" + _blocco_immagini(urls))

    # 1-ter-bis. "Si" all'offerta «vuoi che te le elenchi tutte?»: si rifa' la
    # ricerca sulla domanda precedente con piu' figure e si elenca TUTTO, senza
    # il tetto dell'elenco breve.
    if vuole_il_resto(domanda, _ultima_risposta(storico_messaggi)):
        precedente = _domanda_precedente(storico_messaggi)
        righe, _, _ = agente.cerca(conn, precedente, gruppi,
                                   limite=PEZZI_ELENCO,
                                   storia=_storia(storico_messaggi))
        elenco = _elenco_figure(righe, f"https://{APP_HOST}", utente, tutte=True)
        conn.close()
        if elenco:
            return _risposta_unica("Ecco tutte le voci:\n\n" + elenco)
        return _risposta_unica("Non ho trovato altre voci.")

    # L'agente vero: capisce la domanda e decide da se' — cerca le figure per i
    # prodotti, legge il testo per i documenti, risponde ai saluti senza
    # cercare. Sostituisce la catena fissa (riformula -> vincoli -> glossario ->
    # ricerca -> gate -> prompt).
    if GRAFO:
        # D23: il grafo di agenti. La risposta esce MENTRE il redattore la
        # scrive — il grafo gira in un thread e i pezzi passano da una coda.
        # Non cambia una virgola di cosa dice: cambia che chi legge non
        # aspetta diciassette secondi davanti al vuoto (misurato l'1/10/2026:
        # il redattore e' il 20% del tempo di un turno, ed e' l'unico pezzo
        # che l'utente aspetta per intero).
        return _grafo_in_streaming(conn, domanda, gruppi, storico_messaggi,
                                   utente, conversation_id, inizio)
    else:
        righe, risposta, traccia = agente.cerca(conn, domanda, gruppi,
                                                limite=pezzi_da_recuperare(domanda),
                                                storia=_storia(storico_messaggi))

    # La RISPOSTA del modello, con i riferimenti «pagina N» trasformati in
    # collegamenti alla pagina e chiusa dalle Fonti numerate. Vale per i
    # cataloghi E per i documenti: l'agente cita «pagina N» in prosa in entrambi
    # i casi, quindi il collegamento e' lo stesso. Il vecchio ramo per i
    # documenti (_fonti_citate) cercava «[n]» tra parentesi quadre, che l'agente
    # non produce piu': non collegava nulla e il link alla fonte spariva.
    if risposta:
        # Col grafo le citazioni sono «[[3]]», cioe' la riga 3 del database:
        # il collegamento si costruisce dalla riga, non si indovina rileggendo
        # la prosa (che e' quello che sbagliava 4 citazioni su 5).
        risposta = (grafo.rendi(conn, risposta, righe, f"https://{APP_HOST}",
                                utente, gruppi, traccia.get("etichette"))
                    if GRAFO else
                    _risposta_prodotti(risposta, righe, conn, f"https://{APP_HOST}",
                                 utente, gruppi))
        coda = ""
        def gen():
            try:
                yield f"data: {json.dumps({'choices': [{'delta': {'role': 'assistant', 'content': risposta}, 'index': 0}], 'model': MODEL_NAME})}\n\n"
                if coda:
                    yield f"data: {json.dumps({'choices': [{'delta': {'role': 'assistant', 'content': coda}, 'index': 0}]})}\n\n"
                yield "data: [DONE]\n\n"
            finally:
                _registra_traccia(conn, conversation_id, utente, domanda, righe,
                                  {"rotta": "grafo" if GRAFO else "agente"}, None, None,
                                  int((time.monotonic() - inizio) * 1000),
                                  traccia=traccia, risposta=risposta + coda)
                conn.close()
        return StreamingResponse(gen(), media_type="text/event-stream")

    elenco = _elenco_figure(righe, f"https://{APP_HOST}", utente)
    if elenco:
        testo = "Ecco cosa ho trovato:\n\n" + elenco
        def gen():
            try:
                yield f"data: {json.dumps({'choices': [{'delta': {'role': 'assistant', 'content': testo}, 'index': 0}], 'model': MODEL_NAME})}\n\n"
                yield "data: [DONE]\n\n"
            finally:
                _registra_traccia(conn, conversation_id, utente, domanda, righe,
                                  {"rotta": "grafo" if GRAFO else "agente"}, None, None,
                                  int((time.monotonic() - inizio) * 1000),
                                  traccia=traccia, risposta=testo)
                conn.close()
        return StreamingResponse(gen(), media_type="text/event-stream")

    # L'agente ha raccolto i pezzi ma non ha prodotto una risposta: la si
    # costruisce qui, come faceva la catena — contesto + modello.
    messaggi = [{"role": "system",
                 "content": prompt.SYSTEM + "\n" + prompt.contesto(righe) + SUFFISSO_SISTEMA}]
    storico = [_senza_aggiunte(m) for m in corpo.get("messages", [])
               if isinstance(m.get("content"), str) and m.get("role") in ("user", "assistant")]
    messaggi += storico

    def gen():
        uso, testo = {}, []
        try:
            for pezzo in _stream_litellm(messaggi, "ragionamento", uso):
                testo.append(pezzo)
                yield pezzo
            # La coda elenca i pezzi CITATI: serve il testo, che qui arriva a
            # pezzi dallo stream. Si tiene unito e si cerca dentro: i numeri
            # sono cifre tra parentesi quadre, che nello SSE viaggiano come sono.
            finale = _fonti_citate("".join(testo), righe, f"https://{APP_HOST}", utente)
            if finale:
                yield f"data: {json.dumps({'choices': [{'delta': {'role': 'assistant', 'content': finale}, 'index': 0}]})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'upstream_error'}})}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            _registra_traccia(conn, conversation_id, utente, domanda, righe,
                              {"rotta": "ragionamento"}, uso.get("token_in"),
                              uso.get("token_out"),
                              int((time.monotonic() - inizio) * 1000),
                              traccia=traccia, risposta="".join(testo))
            conn.close()

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/documenti")
def servizio_documento(request: Request, f: str = "", d: str = "", scade: str = "",
                       firma: str = "", u: str = ""):
    """Il documento originale, per verificare una citazione.

    Stesse due condizioni delle immagini, per la stessa ragione: firma valida
    (il gate aveva approvato) E permesso ancora valido adesso (la fonte non e'
    stata sospesa, la persona non e' uscita dal gruppo). La pagina sta dopo il
    cancelletto e non arriva qui: non e' un permesso, e' dove guardare.

    Si serve con FileResponse, che risponde alle richieste Range: il
    visualizzatore del browser scarica la pagina che apri, non i 62 MB del
    catalogo.
    """
    if not documento_mod.valida(f, d, scade, firma, u):
        return JSONResponse({"error": "url non valido o scaduto"}, status_code=403)
    conn = _conn()
    try:
        if not documento_mod.visibile(conn, f, _gruppi_della_richiesta(request, conn, u)):
            return JSONResponse({"error": "non consentito"}, status_code=403)
        p = documento_mod.percorso(conn, f, d)
    finally:
        conn.close()
    if not p:
        return JSONResponse({"error": "documento non trovato"}, status_code=404)
    from fastapi.responses import FileResponse
    # `inline`: si apre nel visualizzatore invece di scaricarsi. Il nome del
    # file lo conosce gia' chi legge — sta nella citazione.
    return FileResponse(p, media_type=documento_mod.tipo(p),
                        headers={"Content-Disposition": f'inline; filename="{p.name}"',
                                 "Cache-Control": "private, max-age=300"})


@app.get("/immagini/{img_id}")
def servizio_immagine(request: Request, img_id: int, scade: str = "", firma: str = "", u: str = "",
                      mini: int = 0):
    """Serve un'immagine a DUE condizioni: URL firmato valido, e permesso
    ancora valido per la persona a cui e' stato consegnato.

    La firma da sola direbbe soltanto «il gate aveva approvato», e varrebbe
    fino alla scadenza anche dopo che la fonte e' stata sospesa o la persona e'
    uscita dal gruppo. Il secondo controllo rilegge `sources` adesso: lo stato
    della fonte ha effetto immediato, un cambio di gruppi al primo messaggio
    successivo di quella persona (migrazione 015)."""
    if not immagini.valida(img_id, scade, firma, u):
        return JSONResponse({"error": "url non valido o scaduto"}, status_code=403)
    conn = _conn()
    try:
        if not immagini.visibile(conn, img_id, _gruppi_della_richiesta(request, conn, u)):
            return JSONResponse({"error": "non consentito"}, status_code=403)
        dati = immagini.leggi(conn, img_id)
    finally:
        conn.close()
    if not dati:
        return JSONResponse({"error": "immagine non trovata"}, status_code=404)
    b, tipo = dati
    # `mini` non entra nella firma: non e' un permesso, e' solo la taglia.
    if mini:
        b, tipo = immagini.miniatura(b)
    return Response(content=b, media_type=tipo,
                    headers={"Cache-Control": "private, max-age=300"})


@app.get("/health")
def health():
    return {"ok": True}


class _NessunaQuery:
    """Per la prova: `_collega_pagine` interroga le immagini del documento, e
    qui non serve sapere quali sono."""

    def execute(self, *a, **k):
        return []


def _prova():
    """La coda delle fonti elenca le citazioni del CORPO, non tutta la pool.

    Il difetto era esattamente questo (26/09/2026: la coda elencava quindici
    pagine per una risposta che ne citava sei, e sui documenti di testo faceva
    comparire [n] che nella risposta non c'erano): chi legge la coda per
    verificare un prodotto e' mandato a cercare una pagina che il corpo non ha
    mai nominato.
    """
    righe = [{"id": 1, "documento": "cat.pdf", "page": 20, "source_id": "s1",
              "descrizione": "Object: palla", "content": "palla"},
             {"id": 2, "documento": "cat.pdf", "page": 40, "source_id": "s1",
              "descrizione": "Object: cesto", "content": "cesto"}]
    testo = _risposta_prodotti("Il set e' a pagina 20 del catalogo.", righe,
                               _NessunaQuery(), "", "u")
    # La coda finisce con «— pagine 20_»: due pagine darebbero «pagine 20, 40_».
    assert testo.endswith("— pagine 20_"), testo[-140:]
    # Il corpo senza citazioni non ha numeri da spiegare: nessuna coda.
    assert _risposta_prodotti("Il set e' in catalogo.", righe,
                              _NessunaQuery(), "", "u").find("Fonti:") == -1
    # Documenti di testo: solo i numeri presenti nella risposta. `[2]` e' il
    # pezzo numero 2 del contesto, cioe' la pagina 40: la 20 non deve comparire.
    coda = _fonti_citate("Il manuale dice [2] che il limite e' 3 pezzi.", righe)
    assert "pagina 40" in coda and "pagina 20" not in coda, coda
    assert _fonti_citate("Il manuale non cita nessun pezzo.", righe) == ""
    print("main: fonti allineate alle citazioni")


if __name__ == "__main__":
    _prova()
