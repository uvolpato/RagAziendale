"""Il pezzo giusto e' arrivato al modello: la RISPOSTA lo usa?

    D=/tmp/val-$(date +%s)
    docker compose cp valutazione orchestratore:$D
    docker compose exec -T orchestratore python $D/risposte.py

Fino al 22/09/2026 si misurava solo il RECUPERO, e andava bene: se il pezzo
non arriva, nessun prompt lo salva. Ma una chat vera di quel giorno ha chiesto
«ho bisogno di sassi rossi» e ha avuto una riga con un codice solo, mentre il
recupero aveva portato otto pezzi da sette pagine. Li' il recupero aveva
funzionato: a perdere era la risposta.

Tre colonne, sulla domanda CORTA (quella che si scrive davvero):

    contesto  quanti dei riscontri stanno negli otto pezzi mandati al modello
    risposta  quanti di QUELLI arrivano nel testo della risposta
    righe     quanto e' lunga la risposta

Non basta «c'e' / non c'e'»: una domanda di catalogo ha piu' risposte giuste
(piu' codici, piu' formati) e riportarne UNA e' un difetto anche quando quella
e' esatta. Misurato il 22/09/2026: il dato c'era 14 volte su 20 e la risposta
lo diceva 13 volte — ma quasi tutte le risposte erano di UNA RIGA.
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
from orchestratore import prompt, recupero      # noqa: E402

QUI = pathlib.Path(__file__).parent
K = 8
LITELLM = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000").rstrip("/")
CHIAVE = os.environ.get("LITELLM_MASTER_KEY", "")


def piatto(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def quanti(testo, riscontri):
    """Quali dei riscontri compaiono in questo testo."""
    p = piatto(testo)
    return [r for r in riscontri if piatto(r) in p]


def rispondi(domanda, righe):
    """La stessa chiamata di main.py: system + contesto, niente storico."""
    r = httpx.post(f"{LITELLM}/v1/chat/completions",
                   headers={"Authorization": "Bearer " + CHIAVE},
                   json={"model": os.environ.get("LLM_RAGIONAMENTO", "ragionamento"),
                         "temperature": float(os.environ.get("TEMPERATURA", "0.2")),
                         "messages": [
                             {"role": "system",
                              "content": prompt.SYSTEM + "\n" + prompt.contesto(righe)},
                             {"role": "user", "content": domanda}]},
                   timeout=600.0)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def main():
    dati = json.loads((QUI / "domande-eurosand.json").read_text(encoding="utf-8"))
    gruppi, documento = dati["gruppi"], dati["documento"]
    indice = dati.get("pagine_indice", [])
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)

    print(f"{'id':>3}  {'contesto':>8} {'risposta':>8}  {'righe':>5}  domanda corta")
    print("-" * 74)
    nc = nr = 0
    persi = []
    for q in dati["domande"]:
        if not q.get("corta"):
            continue
        testo, ris = q["corta"], q["riscontro"]
        righe, _ = recupero.cerca(conn, testo, gruppi, recupero.embedding(testo), limite=K)
        buoni = [r for r in righe
                 if r.get("documento") == documento and r.get("page") not in indice]
        nel_contesto = quanti(" ".join(r.get("content", "") for r in buoni), ris)
        risposta = rispondi(testo, righe)
        # Solo quelli che il modello POTEVA dire: se il dato non era nel
        # contesto e compare nella risposta, non e' merito, e' invenzione.
        nella_risposta = quanti(risposta, nel_contesto)
        nc += len(nel_contesto)
        nr += len(nella_risposta)
        if nel_contesto and len(nella_risposta) < len(nel_contesto):
            persi.append(q["id"])
        print(f"{q['id']:>3}  {len(nel_contesto):>4}/{len(ris):<3} "
              f"{len(nella_risposta):>4}/{len(nel_contesto):<3}  "
              f"{len(risposta.splitlines()):>5}  {testo[:36]}")
    print("-" * 74)
    print(f"riscontri nel contesto {nc}   riportati nella risposta {nr} "
          f"({nr/max(nc,1):.0%})")
    if persi:
        print(f"risposte che ne riportano MENO di quelli che avevano: {persi}")


if __name__ == "__main__":
    main()
