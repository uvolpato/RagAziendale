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
import time

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from orchestratore import egress, gate, identita, immagini, prompt, recupero

app = FastAPI()

MODEL_NAME = "assistente-v1"
LITELLM = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000").rstrip("/")
MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
APP_HOST = os.environ.get("APP_HOST", "assistente.localhost")
MAX_IMMAGINI = 4          # quante immagini citare in fondo alla risposta: oltre appesantisce
# Coda del prompt di sistema, per le stranezze del modello del momento: sta in
# configurazione perche' cambia col modello, e cambiare modello non deve voler
# dire toccare il codice. Oggi serve per Qwen3, che ragiona a voce alta: senza
# "/no_think" LM Studio manda il ragionamento in reasoning_content e LibreChat
# riceve una risposta VUOTA (provato il 20/09/2026). Per il RAG il ragionamento
# non serve: la risposta deve stare nei documenti recuperati.
SUFFISSO_SISTEMA = os.environ.get("SUFFISSO_SISTEMA", "")


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


def _risposta_unica(testo: str):
    """Una risposta finta ma ben formata: LibreChat si aspetta lo stream SSE."""
    def gen():
        yield f"data: {json.dumps({'choices': [{'delta': {'role': 'assistant', 'content': testo}, 'index': 0}], 'model': MODEL_NAME})}\n\n"
        yield f"data: {json.dumps({'choices': [{'delta': {}, 'index': 0, 'finish_reason': 'stop'}], 'model': MODEL_NAME})}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


def _domanda(messages) -> str:
    """L'ultimo messaggio dell'utente: e' la domanda di questo turno."""
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"].strip()
    return ""


def _immagini_del_turno(righe):
    """Le immagini dei chunk recuperati, senza doppioni, fino a MAX_IMMAGINI.

    Le descrizioni VLM stanno gia' nel testo dei chunk (le produce Docling in
    ingestion): qui si cita solo l'URL firmato, in fondo alla risposta, cosi'
    l'utente vede le figure a cui il testo si riferisce."""
    ids = []
    for r in righe:
        for iid in (r.get("immagini") or []):
            if iid not in ids:
                ids.append(iid)
    return ids[:MAX_IMMAGINI]


def _stream_litellm(messages, rotta, uso):
    """Chiama LiteLLM in streaming e cede i chunk OpenAI-compatible cosi come
    arrivano, riscrivendo solo il nome del modello (assistente-v1). `uso` e' un
    dict da riempire con token_in/token_out letti dall'ultimo chunk. Il [DONE]
    lo emette il chiamante: qui si cede solo il flusso del modello."""
    testa = {"Authorization": f"Bearer {MASTER_KEY}"} if MASTER_KEY else {}
    corpo = {
        "model": rotta,
        "messages": messages,
        "stream": True,
        # Con include_usage LiteLLM manda l'uso dei token nell'ultimo chunk:
        # serve alla traccia (token_in/token_out) senza una seconda chiamata.
        "stream_options": {"include_usage": True},
    }
    with egress.client(timeout=300.0, verify=False) as c:
        with c.stream("POST", f"{LITELLM}/v1/chat/completions", json=corpo, headers=testa) as r:
            r.raise_for_status()
            for riga in r.iter_lines():
                if not riga:
                    continue
                if riga.startswith("data: "):
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
                    yield f"data: {json.dumps(pezzo)}\n\n"


def _registra_traccia(conn, conversation_id, utente, domanda, righe, decisione,
                      token_in, token_out, latenza_ms):
    # token_in/out arrivano dall'ultimo chunk di LiteLLM (stream_options
    # include_usage); se la rotta non li espone restano NULL (colonna ammessa).
    conn.execute(
        """INSERT INTO traces (conversation_id, utente, domanda, chunk_ids,
                               retrieval_vuoto, taint, modello, token_in, token_out, latenza_ms)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (conversation_id, utente, domanda,
         [r["id"] for r in righe] if righe else None,
         not righe,
         decisione.get("fonte_contaminante") if decisione.get("interno") else None,
         decisione.get("rotta"),
         token_in, token_out, latenza_ms))
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

    domanda = _domanda(corpo.get("messages", []))
    conn = _conn()

    # 1-bis. Indicizzazione che ha scaricato il modello: si risponde e basta,
    # senza toccare LiteLLM. Nessuna traccia: non c'e' stato nessun turno.
    if _indice_in_aggiornamento(conn):
        conn.close()
        return _risposta_unica(INDICE_IN_AGGIORNAMENTO)

    # 2. Embedding della domanda (puo degradare a full-text).
    qvec = recupero.embedding(domanda)

    # 3. Ricerca con ACL nella query.
    righe, degradato = recupero.cerca(conn, domanda, gruppi, qvec=qvec)

    # 4. Gate: contaminazione e rotta. Puo rifiutare il turno.
    try:
        decisione = gate.applica(conn, conversation_id, righe)
    except gate.RispostaRifiutata as e:
        conn.close()
        return JSONResponse(
            {"error": {"message": str(e), "type": "risposta_rifiutata"}}, status_code=403)

    # 5. Prompt: system + contesto + la cronologia dei messaggi.
    ids_immagini = _immagini_del_turno(righe)
    url_per_pos = {i + 1: immagini.firma_url(iid, f"https://{APP_HOST}")
                   for i, iid in enumerate(ids_immagini)}
    messaggi = [{"role": "system",
                 "content": prompt.SYSTEM + "\n" + prompt.contesto(righe) + SUFFISSO_SISTEMA}]
    storico = [m for m in corpo.get("messages", [])
               if isinstance(m.get("content"), str) and m.get("role") in ("user", "assistant")]
    messaggi += storico

    def _immagini_finali():
        """Il blocco markdown con le figure citate, in coda alla risposta."""
        if not url_per_pos:
            return ""
        return ("\n\n" + "\n".join(f"![immagine {n}]({u})"
                                    for n, u in url_per_pos.items()))

    def gen():
        uso = {}
        try:
            for pezzo in _stream_litellm(messaggi, decisione["rotta"], uso):
                yield pezzo
            finale = _immagini_finali()
            if finale:
                yield f"data: {json.dumps({'choices': [{'delta': {'role': 'assistant', 'content': finale}, 'index': 0}]})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'upstream_error'}})}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            _registra_traccia(conn, conversation_id, utente, domanda, righe, decisione,
                              uso.get("token_in"), uso.get("token_out"),
                              int((time.monotonic() - inizio) * 1000))
            conn.close()

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/immagini/{img_id}")
def servizio_immagine(img_id: int, scade: str = "", firma: str = ""):
    """Serve un'immagine SOLO con URL firmato valido: la firma viene emessa dal
    gate quando ha ammesso il chunk. Niente id libero = niente IDOR sulle
    immagini altrui."""
    if not immagini.valida(img_id, scade, firma):
        return JSONResponse({"error": "url non valido o scaduto"}, status_code=403)
    conn = _conn()
    try:
        dati = immagini.leggi(conn, img_id)
    finally:
        conn.close()
    if not dati:
        return JSONResponse({"error": "immagine non trovata"}, status_code=404)
    b, tipo = dati
    return Response(content=b, media_type=tipo,
                    headers={"Cache-Control": "private, max-age=300"})


@app.get("/health")
def health():
    return {"ok": True}
