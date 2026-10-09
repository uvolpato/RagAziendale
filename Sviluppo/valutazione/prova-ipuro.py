"""Prova su IPURO col flusso VERO di ricerca: intent + cerca.

    docker compose cp valutazione orchestratore:/tmp/val
    docker compose exec -T orchestratore python /tmp/val/prova-ipuro.py

Come l'agente (agente.py: _nodo_capisce + _esegui): dalla domanda si estraggono
intent e termini, e si cerca con la query arricchita dai termini. Stampa anche
i termini, cosi' si vede che cosa ha aggiunto il modello.

Due misure, come prima:
    contesto   quanti riscontri stanno negli 8 pezzi mandati al modello
    risposta   quanti di QUELLI arrivano nel testo della risposta
"""
import json
import os
import pathlib
import sys
import unicodedata

import httpx
import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, "/app")
from orchestratore import prompt, recupero, vincoli as v      # noqa: E402

QUI = pathlib.Path(__file__).parent
K = 8
BASE = os.environ.get("RISPOSTE_BASE", "http://host.docker.internal:1235/v1").rstrip("/")
MODELLO = os.environ.get("RISPOSTE_MODELLO", "qwen3-14b")


def piatto(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def quanti(testo, riscontri):
    p = piatto(testo)
    return [r for r in riscontri if piatto(r) in p]


def rispondi(domanda, righe):
    r = httpx.post(f"{BASE}/chat/completions",
                   json={"model": MODELLO, "temperature": 0,
                         "messages": [
                             {"role": "system", "content": prompt.SYSTEM + "\n" + prompt.contesto(righe)},
                             {"role": "user", "content": domanda}]},
                   timeout=600.0)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def main():
    dati = json.loads((QUI / "domande-ipuro.json").read_text(encoding="utf-8"))
    gruppi, documento = dati["gruppi"], dati["documento"]
    indice = dati.get("pagine_indice", [])
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)

    print(f"modello: {MODELLO}  ({BASE})")
    print(f"{'id':>3}  {'contesto':>8} {'risposta':>8}  {'righe':>5}  domanda corta   [termini espansi]")
    print("-" * 92)
    nc = nr = 0
    misurate = 0
    for q in dati["domande"]:
        testo, ris = q.get("corta"), q.get("riscontro")
        if not testo:
            continue
        intent, termini, contesto, vincoli = v.estrae(testo)
        query = testo + " " + " ".join(termini) + " " + " ".join(contesto)
        righe, _ = recupero.cerca(conn, query, gruppi, qvec=recupero.embedding(query),
                                  limite=K, vincolo=v.regex(vincoli))
        espansi = ", ".join(termini) if termini else "-"
        if q.get("senza_risposta"):
            risposta = rispondi(testo, righe)
            print(f"{q['id']:>3}  {'--':>8} {'--':>8}  {len(risposta.splitlines()):>5}  {testo[:28]:<28} [{espansi[:36]}]")
            print(f"      >>> {risposta[:180].replace(chr(10), ' / ')}")
            continue
        buoni = [r for r in righe
                 if r.get("documento") == documento and r.get("page") not in indice]
        nel_contesto = quanti(" ".join(r.get("content", "") for r in buoni), ris)
        risposta = rispondi(testo, righe)
        nella_risposta = quanti(risposta, nel_contesto)
        nc += len(nel_contesto)
        nr += len(nella_risposta)
        misurate += 1
        print(f"{q['id']:>3}  {len(nel_contesto):>4}/{len(ris):<3} "
              f"{len(nella_risposta):>4}/{len(nel_contesto):<3}  "
              f"{len(risposta.splitlines()):>5}  {testo[:28]:<28} [{espansi[:36]}]")
    print("-" * 92)
    print(f"riscontri nel contesto {nc}   riportati nella risposta {nr} "
          f"({nr/max(nc,1):.0%}) su {misurate} domande misurate")


if __name__ == "__main__":
    main()
