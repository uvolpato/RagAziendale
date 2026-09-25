"""Chiamata DIRETTA al modello locale (llama-swap), senza litellm.

litellm e' stato tolto il 24/09/2026: il modello e' uno solo e locale, quindi
l'orchestratore parla direttamente con llama-swap (host.docker.internal:1235).
Questo permette anche di mandare `reasoning_effort` — che litellm buttava via.

Due velocita', decise per chiamata:
- `chiedi` (ragiona=False): reasoning_effort "none" -> risposta secca e veloce,
  per i passi MECCANICI (estrazione vincoli, glossario, riscrittura, indici).
- `messaggio` / `stream` (ragionamento acceso): per l'agente e la risposta,
  dove il ragionamento serve (domande astratte, scelta degli strumenti).
"""

import os

from orchestratore import egress

HOST = os.environ.get("MODELLI_HOST", "host.docker.internal:1235")
MODELLO = os.environ.get("MODELLO_CHAT", "qwen3.6-35b-a3b-gsq-hybrid")
SECONDI = float(os.environ.get("VINCOLI_TIMEOUT", "120"))


def _risposta(messaggi, max_tokens, ragiona, tools=None, tool_choice=None,
              temperature=0.0):
    corpo = {"model": MODELLO, "messages": messaggi,
             "temperature": temperature, "max_tokens": max_tokens}
    if not ragiona:
        corpo["reasoning_effort"] = "none"
    if tools:
        corpo["tools"] = tools
        corpo["tool_choice"] = tool_choice or "auto"
    with egress.client(timeout=SECONDI, verify=False) as c:
        r = c.post(f"http://{HOST}/v1/chat/completions", json=corpo)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]


def chiedi(messaggi, max_tokens=4000):
    """Il SOLO testo, senza ragionamento (passi meccanici)."""
    return (_risposta(messaggi, max_tokens, ragiona=False).get("content") or "").strip()


def messaggio(messaggi, max_tokens=2048, tools=None, tool_choice="auto"):
    """Il messaggio COMPLETO (content + tool_calls), SENZA ragionamento.

    La scelta degli strumenti e' meccanica: il ragionamento la rendeva non
    deterministica e a volte produceva risposte vuote (il 35B si mangiava il
    budget di token ragionando e non decideva mai). Senza, il modello decide e
    produce tool_calls affidabili."""
    return _risposta(messaggi, max_tokens, ragiona=False,
                     tools=tools, tool_choice=tool_choice)


def stream(messaggi, max_tokens=4096, temperature=0.2):
    """Generatore di righe SSE (`data: ...`), con ragionamento. Serve alla
    risposta in chat, che va in streaming a LibreChat."""
    corpo = {"model": MODELLO, "messages": messaggi,
             "temperature": temperature, "max_tokens": max_tokens,
             "stream": True, "stream_options": {"include_usage": True}}
    with egress.client(timeout=300.0, verify=False) as c:
        with c.stream("POST", f"http://{HOST}/v1/chat/completions", json=corpo) as r:
            r.raise_for_status()
            for riga in r.iter_lines():
                if riga.startswith("data: "):
                    yield riga
