"""Chiamata al modello: locale (llama-swap) o DeepSeek via API.

litellm e' stato tolto il 24/09/2026. Di norma si parla a llama-swap
(host.docker.internal:1235). Se DEEPSEEK_API_KEY e' presente, si parla invece
a DeepSeek via API (https://api.deepseek.com): serve a MISURARE se il limite e'
il modello locale (35B-A3B MoE) o il flusso. Reversibile: si toglie la key.

Differenze da gestire:
- llama.cpp vuole `reasoning_effort`; DeepSeek lo rifiuta.
- DeepSeek vuole l'header Authorization; llama-swap no.
- llama-swap e' HTTP (niente TLS); DeepSeek e' HTTPS (TLS verificato).

Due velocita', decise per chiamata:
- `chiedi` (ragiona=False): risposta secca e veloce, per i passi MECCANICI
  (estrazione vincoli, riscrittura, indici).
- `messaggio` / `stream`: per l'agente e la risposta, dove serve di piu'.
"""
import os

from orchestratore import egress

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

if DEEPSEEK_KEY:
    BASE = "https://api.deepseek.com/v1"
    MODELLO = os.environ.get("DEEPSEEK_MODELLO", "deepseek-chat")
    AUTH = {"Authorization": f"Bearer {DEEPSEEK_KEY}"}
    VERIFICA = True
else:
    BASE = f"http://{os.environ.get('MODELLI_HOST', 'host.docker.internal:1235')}/v1"
    MODELLO = os.environ.get("MODELLO_CHAT", "qwen3.6-35b-a3b-gsq-hybrid")
    AUTH = {}
    VERIFICA = False

SECONDI = float(os.environ.get("VINCOLI_TIMEOUT", "120"))


def _risposta(messaggi, max_tokens, ragiona, tools=None, tool_choice=None,
              temperature=0.0):
    corpo = {"model": MODELLO, "messages": messaggi,
             "temperature": temperature, "max_tokens": max_tokens}
    # reasoning_effort e' di llama.cpp: DeepSeek lo rifiuta (400).
    if not ragiona and not DEEPSEEK_KEY:
        corpo["reasoning_effort"] = "none"
    if tools:
        corpo["tools"] = tools
        corpo["tool_choice"] = tool_choice or "auto"
    with egress.client(timeout=SECONDI, verify=VERIFICA) as c:
        r = c.post(f"{BASE}/chat/completions", json=corpo, headers=AUTH)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]


_CAPACITA = {"quanti": None, "quando": 0.0}
CAPACITA_TTL = 300.0        # si richiede ogni tanto: la porta si puo' aprire


def _interroga_il_server() -> int | None:
    """QUESTA e' l'unica funzione che sa com'e' fatto il server dei modelli.

    Torna quante richieste serve davvero insieme, o None se non si riesce a
    saperlo. Tutto il resto del codice chiama `slot()` e non sa niente di
    endpoint, prodotti o formati: **se un giorno il server cambia — vLLM,
    TGI, Ollama, un'API — si riscrive solo questo corpo.**

    Oggi si provano due indirizzi noti, nell'ordine: llama-swap mette ogni
    modello dietro `/upstream/<nome>/`, llama.cpp da solo espone `/props`
    sulla radice. Aggiungerne un terzo e' una riga nella lista.
    """
    radice = BASE.rsplit("/v1", 1)[0]
    posti = ((f"{radice}/upstream/{MODELLO}/props", "total_slots"),
             (f"{radice}/props", "total_slots"))
    for indirizzo, campo in posti:
        try:
            with egress.client(timeout=5.0, verify=VERIFICA) as c:
                r = c.get(indirizzo, headers=AUTH)
                r.raise_for_status()
                valore = r.json().get(campo)
            if valore:
                return max(1, int(valore))
        except Exception:
            continue    # server giu', endpoint assente, altro prodotto
    return None


def slot() -> int:
    """Quante richieste il server del modello serve DAVVERO insieme.

    Serve a non sprecare lavoro: con un solo slot, mandare tre chiamate
    insieme non le fa andare in parallelo — si accodano nel server, e tre
    chiamate costano piu' di una perche' ognuna rispedisce tutto il prompt
    (misurato l'1/10/2026: il critico spezzato in tre e' passato da 18,5 a
    29,4 secondi). Il parallelismo sulle QUERY e' un'altra cosa: li' a
    servire e' Postgres, e funziona sempre.

    Non e' scritto da nessuna parte: lo si chiede al server, cosi' il giorno
    che in produzione si alza `--parallel` chi lo usa se ne accorge da solo.
    `MODELLO_SLOT` ha la precedenza su tutto, per un server che non lo dice
    (DeepSeek regge piu' richieste ma non ha l'endpoint).

    In mancanza di tutto vale 1: l'ipotesi prudente, si fa una cosa per
    volta e nessuno ci perde.
    """
    import time as _t
    forzato = os.environ.get("MODELLO_SLOT")
    if forzato:
        try:
            return max(1, int(forzato))
        except ValueError:
            pass
    if _CAPACITA["quanti"] and _t.monotonic() - _CAPACITA["quando"] < CAPACITA_TTL:
        return _CAPACITA["quanti"]
    quanti = _interroga_il_server()
    if quanti is None:
        # Non lo si e' saputo: si usa 1 ma NON lo si ricorda. Il caso tipico
        # e' il modello non ancora caricato — llama-swap lo tira su alla
        # prima richiesta vera — e ricordarsi un 1 preso in quel momento
        # vorrebbe dire lavorare in fila per i cinque minuti successivi su un
        # server che ne regge due (visto l'1/10/2026, dopo un riavvio).
        return 1
    _CAPACITA.update(quanti=quanti, quando=_t.monotonic())
    return quanti


def chiedi(messaggi, max_tokens=4000):
    """Il SOLO testo, senza ragionamento (passi meccanici)."""
    return (_risposta(messaggi, max_tokens, ragiona=False).get("content") or "").strip()


def messaggio(messaggi, max_tokens=2048, tools=None, tool_choice="auto",
              ragiona=False):
    """Il messaggio COMPLETO (content + tool_calls).

    Di default SENZA ragionamento: la scelta degli strumenti e' meccanica, il
    ragionamento la rendeva non deterministica e a volte produceva risposte
    vuote (il 35B si mangiava il budget di token ragionando e non decideva mai).
    Con `ragiona=True` il modello pensa prima di chiamare gli strumenti.
    """
    return _risposta(messaggi, max_tokens, ragiona=ragiona,
                     tools=tools, tool_choice=tool_choice)


def stream(messaggi, max_tokens=4096, temperature=0.2):
    """Generatore di righe SSE (`data: ...`). Serve alla risposta in chat, che
    va in streaming a LibreChat."""
    corpo = {"model": MODELLO, "messages": messaggi,
             "temperature": temperature, "max_tokens": max_tokens,
             "stream": True}
    if not DEEPSEEK_KEY:
        corpo["stream_options"] = {"include_usage": True}
    with egress.client(timeout=300.0, verify=VERIFICA) as c:
        with c.stream("POST", f"{BASE}/chat/completions", json=corpo, headers=AUTH) as r:
            r.raise_for_status()
            for riga in r.iter_lines():
                if riga.startswith("data: "):
                    yield riga
