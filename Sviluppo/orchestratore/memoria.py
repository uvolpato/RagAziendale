"""Memoria agentica: la conversazione e' il contesto dell'agente.

L'agente riceve l'INTERA conversazione (milestone AGENTS.md): capisce da se'
saluti, consensi, «mostrami il resto». Il contesto del modello pero' e' finito
(32768 token): quando la storia lo supera, la si comprime — una finestra recente
di turni integrali + un riassunto PESATO del resto (oggetti, vincoli, decisioni;
niente chiacchiere ne' testo dei pezzi recuperati, che sta nel DB e si ri-cerca).

Il riassunto e' incrementale per costruzione: a ogni superamento della soglia si
riassume [riassunto precedente + turni nuovi], non tutta la storia da capo.
"""
import os

from orchestratore import modello

# Soglia in TOKEN (stimati a 4 caratteri l'uno). Sotto, la storia passa intera.
SOGLIA_TOKEN = int(os.environ.get("MEMORIA_SOGLIA", "22000"))
# Ultimi N messaggi tenuti integrali; il resto finisce nel riassunto.
RECENTI = int(os.environ.get("MEMORIA_RECENTI", "8"))

ISTRUZIONI_RIASSUNTO = (
    "Riassumi la conversazione fin qui, in modo che chi legge possa riprendere "
    "il filo senza averla letta.\n"
    "Conserva: gli oggetti e i vincoli delle domande (nastri, blu, sotto i 2 "
    "euro), cosa e' stato risposto e mostrato, le decisioni prese.\n"
    "Butta: saluti, chiacchiere, e il TESTO dei documenti citati (quei dati "
    "stanno nell'archivio e si ri-cercano).\n"
    "Poche righe, in italiano.\n"
    "/no_think"
)


def _stima(messaggi) -> int:
    """Stima grossolana dei token: 4 caratteri = 1 token. Basta per la soglia."""
    return sum(len(m.get("content") or "") for m in messaggi) // 4


def comprimi(messaggi) -> list:
    """La storia, compressa se supera la soglia; altrimenti com'era."""
    if not messaggi or _stima(messaggi) <= SOGLIA_TOKEN:
        return messaggi
    vecchi = messaggi[:-RECENTI]
    recenti = messaggi[-RECENTI:]
    riassunto = _riassumi(vecchi)
    if not riassunto:
        return recenti
    return ([{"role": "user",
              "content": "Riassunto di cio' che si e' detto finora (per riprendere il filo):\n" + riassunto}]
            + recenti)


def _riassumi(messaggi) -> str:
    """Un riassunto pesato dei messaggi, dal modello (passo meccanico). Vuoto se
    il modello non risponde: in quel caso si tengono solo i recenti."""
    testo = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in messaggi)
    try:
        return modello.chiedi([
            {"role": "system", "content": ISTRUZIONI_RIASSUNTO},
            {"role": "user", "content": testo},
        ])
    except Exception:
        return ""


def _prova():
    brevi = [{"role": "user", "content": "ciao"}]
    assert comprimi(brevi) == brevi   # sotto soglia: non si tocca niente
    # la stima e' grossolana ma cresce con la lunghezza
    assert _stima([{"role": "user", "content": "x" * 100}]) \
        < _stima([{"role": "user", "content": "x" * 1000}])
    print("memoria: regole locali ok")


if __name__ == "__main__":
    _prova()
