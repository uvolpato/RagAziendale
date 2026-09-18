"""Smoke test degli embedding su LM Studio. Solo stdlib.

Due domande:
 1. bge-m3 produce 1024 dimensioni? (lock-in dello schema)
 2. il reranker caricato come modello di embedding produce qualcosa di sensato
    o spazzatura silenziosa?
"""
import json
import math
import urllib.error
import urllib.request

BASE = "http://localhost:1234/v1"

# Coppia ovvia: doc 0 pertinente, gli altri no.
CASI = [
    (
        "quanto dura la garanzia",
        [
            "La garanzia dura 24 mesi dalla data di consegna.",   # atteso 1o
            "Istruzioni per il montaggio del supporto a parete.",
            "Il carrello va lubrificato ogni 500 ore di esercizio.",
        ],
    ),
    (
        # Il punto debole noto del vettoriale: i codici articolo.
        "ricambio ART-4471",
        [
            "Il cuscinetto ART-4471 sostituisce il precedente ART-4470.",  # atteso 1o
            "I ricambi sono disponibili presso i centri autorizzati.",
            "La garanzia dura 24 mesi dalla data di consegna.",
        ],
    ),
]

MODELLI = [
    "text-embedding-bge-m3-embeddings",
    "text-embedding-mxbai-embed-large-v1",
    "text-embedding-nomic-embed-text-v1.5",
    "text-embedding-bge-reranker-v2-m3",   # NON e' un modello di embedding
]


def embed(model, testi, timeout=300):
    corpo = json.dumps({"model": model, "input": testi}).encode()
    req = urllib.request.Request(
        f"{BASE}/embeddings", data=corpo,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        dati = json.load(r)
    if "data" not in dati:
        raise RuntimeError(json.dumps(dati)[:300])
    return [d["embedding"] for d in dati["data"]]


def cos(a, b):
    num = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return num / (na * nb) if na and nb else 0.0


for model in MODELLI:
    print("=" * 70)
    print(model)
    try:
        for domanda, docs in CASI:
            vettori = embed(model, [domanda] + docs)
            dim = len(vettori[0])
            punteggi = [cos(vettori[0], v) for v in vettori[1:]]
            ordine = sorted(range(len(punteggi)), key=lambda i: -punteggi[i])
            esito = "OK " if ordine[0] == 0 else "NO "
            margine = punteggi[0] - max(punteggi[1:])
            print(f"  dim={dim}  [{esito}] \"{domanda}\"")
            for i, p in enumerate(punteggi):
                marca = " <- atteso" if i == 0 else ""
                print(f"      doc{i}: {p:+.4f}{marca}")
            print(f"      margine sul 2o: {margine:+.4f}")
    except urllib.error.HTTPError as e:
        print(f"  HTTPError {e.code}: {e.read()[:300].decode(errors='replace')}")
    except Exception as e:
        print(f"  ERRORE: {type(e).__name__}: {e}")
    print()
