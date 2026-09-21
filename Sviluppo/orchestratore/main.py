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
import re
import time

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from orchestratore import egress, gate, identita, immagini, prompt, recupero, riformula

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
    """Toglie dalla cronologia le righe che ha scritto il SISTEMA, non il
    modello: l'offerta delle immagini e i collegamenti alle figure.

    Rimandargliele indietro lo porta a imitarle: il modello le riscriveva in
    coda alla propria risposta, e l'offerta compariva due volte (visto il
    20/09/2026, conversazione sui profumatori). Al modello interessa cosa ha
    detto, non come il sistema ha decorato la risposta."""
    if messaggio.get("role") != "assistant":
        return messaggio
    righe = [r for r in messaggio["content"].splitlines()
             if MARCA_OFFERTA not in r and not r.lstrip().startswith("![immagine")]
    return {**messaggio, "content": "\n".join(righe).strip()}


def _domanda(messages) -> str:
    """L'ultimo messaggio dell'utente: e' la domanda di questo turno."""
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"].strip()
    return ""


# Le immagini non si attaccano piu' a ogni risposta: si OFFRONO, e si mostrano
# a chi le chiede. Vederle serve di rado (un catalogo, uno schema), e quattro
# figure in fondo a ogni risposta sono rumore che nasconde il testo.
# IMMAGINI_SU_RICHIESTA=0 torna al comportamento di prima.
SU_RICHIESTA = os.environ.get("IMMAGINI_SU_RICHIESTA", "1") != "0"
# Frase dell'offerta. Contiene MARCA: al turno dopo si guarda se l'assistente
# aveva davvero offerto qualcosa, prima di interpretare un "si" come consenso.
MARCA_OFFERTA = "immagini collegate a questa risposta"
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
PEZZI_ELENCO = 20


def pezzi_da_recuperare(domanda: str) -> int:
    """Quanti pezzi mettere nel contesto: di piu' per le domande di elenco."""
    return PEZZI_ELENCO if ELENCO.search(domanda or "") else 8


def _fonti_citate(righe) -> str:
    """Documento e pagina di ogni pezzo citato, in coda alla risposta.

    Il modello cita «[7]» e basta: chi legge non ha modo di sapere che [7] e'
    «CATALOGO IPURO 2025.pdf, pagina 8», quindi per verificare deve sfogliare a
    mano — ed e' successo davvero il 20/09/2026, con un utente che ha concluso
    «non c'e'» su un prodotto che stava a pagina 8. Il disclaimer chiede di
    verificare sempre la fonte citata: senza questo elenco non e' possibile.
    I numeri corrispondono all'ordine in cui i pezzi entrano nel CONTESTO
    (prompt.contesto), quindi [n] qui e [n] nella risposta sono lo stesso pezzo.
    """
    if not righe:
        return ""
    voci = []
    for i, r in enumerate(righe, 1):
        pagina = f", pagina {r['page']}" if r.get("page") is not None else ""
        voci.append(f"[{i}] {r['documento']}{pagina}")
    return "\n\n---\n_Fonti: " + " · ".join(voci) + "_"


def _blocco_immagini(url_per_pos) -> str:
    """Le figure, in markdown, una per riga."""
    return "\n".join(f"![immagine {n}]({u})" for n, u in url_per_pos.items())


def _immagini_per_la_domanda(conn, qvec, gruppi, righe):
    """Gli id delle figure che rispondono alla domanda.

    Si cerca fra le DESCRIZIONI delle immagini (prodotte dal modello visivo,
    vettorizzate come i pezzi), restando nei documenti che hanno risposto: la
    figura deve venire dalla stessa fonte del testo citato, altrimenti si
    mostrano prodotti di un catalogo mentre la risposta parla di un altro.

    Se i vettori non ci sono — embedding giu', oppure documenti letti prima
    che le descrizioni venissero salvate — si torna alla scelta per pagina.
    """
    documenti = list({r["documento"] for r in righe}) if righe else None
    trovate = recupero.immagini_pertinenti(conn, qvec, gruppi, documenti, MAX_IMMAGINI)
    if trovate:
        return [r["id"] for r in trovate]
    return _immagini_del_turno(righe)


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
                      token_in, token_out, latenza_ms, riscritta=False, cercata=None):
    # token_in/out arrivano dall'ultimo chunk di LiteLLM (stream_options
    # include_usage); se la rotta non li espone restano NULL (colonna ammessa).
    conn.execute(
        """INSERT INTO traces (conversation_id, utente, domanda, chunk_ids,
                               retrieval_vuoto, taint, modello, token_in, token_out, latenza_ms,
                               riformulazione)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (conversation_id, utente,
         # Nella traccia resta la domanda VERA; se e' stata riscritta per la
         # ricerca si annota anche quella, perche' una risposta strana si
         # spiega guardando cosa e' stato cercato davvero.
         domanda if not riscritta else f"{domanda}\n[cercata: {cercata}]",
         [r["id"] for r in righe] if righe else None,
         not righe,
         decisione.get("fonte_contaminante") if decisione.get("interno") else None,
         decisione.get("rotta"),
         token_in, token_out, latenza_ms, riscritta))
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
        qvec_prec = recupero.embedding(precedente)
        righe, _ = recupero.cerca(conn, precedente, gruppi, qvec=qvec_prec)
        ids = _immagini_per_la_domanda(conn, qvec_prec, gruppi, righe)
        conn.close()
        if not ids:
            return _risposta_unica("Non ho immagini da mostrare per quella risposta.")
        urls = {i + 1: immagini.firma_url(iid, f"https://{APP_HOST}", utente) for i, iid in enumerate(ids)}
        return _risposta_unica("Ecco le figure delle pagine citate:\n\n" + _blocco_immagini(urls))

    # 2. La domanda per la RICERCA: in una conversazione l'ultima frase da sola
    # non basta ("ne ho bisogno in auto"). Si riscrive con le battute
    # precedenti; se non si puo', resta com'era.
    cercata, riscritta = riformula.per_la_ricerca(domanda, storico_messaggi, SUFFISSO_SISTEMA)
    if riscritta:
        print(f"riformulata: {domanda!r} -> {cercata!r}", flush=True)

    # 3. Embedding della domanda (puo degradare a full-text) e ricerca con ACL.
    qvec = recupero.embedding(cercata)
    righe, degradato = recupero.cerca(conn, cercata, gruppi, qvec=qvec,
                                      limite=pezzi_da_recuperare(domanda))

    # 4. Gate: contaminazione e rotta. Puo rifiutare il turno.
    try:
        decisione = gate.applica(conn, conversation_id, righe)
    except gate.RispostaRifiutata as e:
        conn.close()
        return JSONResponse(
            {"error": {"message": str(e), "type": "risposta_rifiutata"}}, status_code=403)

    # 5. Prompt: system + contesto + la cronologia dei messaggi.
    ids_immagini = _immagini_per_la_domanda(conn, qvec, gruppi, righe)
    url_per_pos = {i + 1: immagini.firma_url(iid, f"https://{APP_HOST}", utente)
                   for i, iid in enumerate(ids_immagini)}
    messaggi = [{"role": "system",
                 "content": prompt.SYSTEM + "\n" + prompt.contesto(righe) + SUFFISSO_SISTEMA}]
    storico = [_senza_aggiunte(m) for m in corpo.get("messages", [])
               if isinstance(m.get("content"), str) and m.get("role") in ("user", "assistant")]
    messaggi += storico

    chieste = chiede_le_immagini(domanda)
    # Offerta gia' fatta nel turno precedente per le STESSE figure: ripeterla a
    # ogni risposta e' rumore (sei risposte, sei offerte identiche, viste il
    # 20/09/2026). Chi voleva vederle ha gia' avuto l'occasione di dirlo.
    gia_offerte = MARCA_OFFERTA in _ultima_risposta(storico_messaggi)

    def _immagini_finali():
        """Coda della risposta: le figure se le ha chieste l'utente (o se
        SU_RICHIESTA e' spento), altrimenti l'offerta."""
        if not url_per_pos:
            # Le ha chieste e non ce ne sono: meglio dirlo che tacere.
            return "\n\n_Non ho figure collegate a questa risposta._" if chieste else ""
        if not SU_RICHIESTA or chieste:
            return "\n\n" + _blocco_immagini(url_per_pos)
        if gia_offerte:
            return ""
        quante = len(url_per_pos)
        return (f"\n\n_Ci sono {quante} {MARCA_OFFERTA}"
                f"{' (figure, schemi, foto dei prodotti)' if quante > 1 else ''}: "
                f"scrivi «mostra» se vuoi vederle._")

    def gen():
        uso = {}
        try:
            for pezzo in _stream_litellm(messaggi, decisione["rotta"], uso):
                yield pezzo
            finale = _fonti_citate(righe) + _immagini_finali()
            if finale:
                yield f"data: {json.dumps({'choices': [{'delta': {'role': 'assistant', 'content': finale}, 'index': 0}]})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'upstream_error'}})}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            _registra_traccia(conn, conversation_id, utente, domanda, righe, decisione,
                              uso.get("token_in"), uso.get("token_out"),
                              int((time.monotonic() - inizio) * 1000), riscritta, cercata)
            conn.close()

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/immagini/{img_id}")
def servizio_immagine(request: Request, img_id: int, scade: str = "", firma: str = "", u: str = ""):
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
    return Response(content=b, media_type=tipo,
                    headers={"Cache-Control": "private, max-age=300"})


@app.get("/health")
def health():
    return {"ok": True}
