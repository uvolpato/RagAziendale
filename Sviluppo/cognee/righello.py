"""Scarica il tokenizer di bge-m3 e lo mette sotto il nome che Cognee chiede.

Gira in COSTRUZIONE, dove la rete e' lecita. A valle l'immagine e' offline.
"""
import json
import os
import pathlib
import shutil

from huggingface_hub import hf_hub_download

VERO = "BAAI/bge-m3"
# Il nome con cui lo chiede Cognee: e' l'id del modello su llama-swap.
CHIESTO = os.environ.get("EMBEDDING_MODELLO", "text-embedding-bge-m3-embeddings")

sorgente = hf_hub_download(VERO, "tokenizer.json")
print("tokenizer vero:", sorgente)

hub = pathlib.Path(os.environ.get("HF_HOME", "/opt/hf")) / "hub"
cartella = hub / ("models--" + CHIESTO.replace("/", "--"))
revisione = "locale"
dentro = cartella / "snapshots" / revisione
dentro.mkdir(parents=True, exist_ok=True)
shutil.copy(sorgente, dentro / "tokenizer.json")
(cartella / "refs").mkdir(parents=True, exist_ok=True)
(cartella / "refs" / "main").write_text(revisione)
print("voce di cache creata:", cartella)

# La prova, qui e subito: se non si carica, la costruzione fallisce invece di
# lasciare il difetto a valle.
from tokenizers import Tokenizer
t = Tokenizer.from_file(str(dentro / "tokenizer.json"))
print("prova: %d token per una frase" % len(t.encode("sassi rossi decorativi").ids))
print(json.dumps({"vero": VERO, "chiesto": CHIESTO}, ensure_ascii=False))
