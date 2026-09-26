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
  (estrazione vincoli, glossario, riscrittura, indici).
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
