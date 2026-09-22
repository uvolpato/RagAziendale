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

# Per provare un ALTRO modello senza toccare la configurazione del servizio:
#   RISPOSTE_BASE=http://host.docker.internal:1235 RISPOSTE_MODELLO=...
# si parla direttamente a llama-swap invece che a LiteLLM.
BASE = os.environ.get("RISPOSTE_BASE", LITELLM).rstrip("/")
MODELLO = os.environ.get("RISPOSTE_MODELLO", os.environ.get("LLM_RAGIONAMENTO", "ragionamento"))

# Dove si CONGELANO i contesti. Con questo file la misura si spezza in due:
#
#   1. senza il file: si cerca (servono embedding e rerank) e si salva cio'
#      che e' arrivato al modello;
#   2. col file: si genera e basta, e i modelli della ricerca si possono
#      SCARICARE.
#
# Due ragioni, e la seconda vale piu' della prima.
#
# La VRAM: un 27B, l'embedding e il reranker insieme non stanno in 16 GB.
# Windows manda in memoria CONDIVISA quello che avanza senza dire niente, e il
# 22/09/2026 il 27B girava a 1,8 token al secondo mentre nvidia-smi segnava il
# 96 per cento di utilizzo — che sembra una scheda che lavora, ed e' una
# scheda che aspetta il bus.
#
# La MISURA: confrontando due modelli sullo STESSO contesto congelato si
# confrontano i modelli. Ricercando ogni volta si confronta anche il recupero,
# che non e' cio' che si sta chiedendo.
CONTESTI = os.environ.get("RISPOSTE_CONTESTI", "")


def piatto(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def quanti(testo, riscontri):
    """Quali dei riscontri compaiono in questo testo."""
    p = piatto(testo)
    return [r for r in riscontri if piatto(r) in p]


def rispondi(domanda, righe):
    """La stessa chiamata di main.py: system + contesto, niente storico."""
    r = httpx.post(f"{BASE}/v1/chat/completions",
                   headers={"Authorization": "Bearer " + CHIAVE},
                   json={"model": MODELLO,
                         # ZERO, non il 0,2 di produzione: a 0,2 la stessa
                         # domanda sullo stesso contesto dava 19 riscontri in
                         # una corsa e 16 in quella dopo (22/09/2026), e con
                         # quel rumore non si distingue un miglioramento da
                         # una fluttuazione. Misura una configurazione un po'
                         # diversa da quella vera: e' il prezzo per avere un
                         # numero che si puo' confrontare con quello di ieri.
                         "temperature": 0,
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

    congelati, da_salvare = {}, bool(CONTESTI)
    if CONTESTI and pathlib.Path(CONTESTI).is_file():
        congelati = {int(k): v for k, v in
                     json.loads(pathlib.Path(CONTESTI).read_text(encoding="utf-8")).items()}
        da_salvare = False
        print(f"contesti congelati: {CONTESTI} ({len(congelati)} domande) "
              f"— non si cerca, si genera soltanto")
    print(f"modello: {MODELLO}  ({BASE})")
    print(f"{'id':>3}  {'contesto':>8} {'risposta':>8}  {'righe':>5}  domanda corta")
    print("-" * 74)
    nc = nr = 0
    persi = []
    for q in dati["domande"]:
        if not q.get("corta"):
            continue
        testo, ris = q["corta"], q["riscontro"]
        if q["id"] in congelati:
            righe = congelati[q["id"]]
        else:
            righe, _ = recupero.cerca(conn, testo, gruppi, recupero.embedding(testo), limite=K)
            # Solo cio' che serve al prompt: documento, pagina, testo. Gli id e
            # i punteggi cambiano a ogni reindicizzazione e renderebbero il
            # file congelato illeggibile fra una settimana.
            congelati[q["id"]] = [{"documento": r.get("documento"), "page": r.get("page"),
                                   "content": r.get("content")} for r in righe]
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
    if da_salvare:
        pathlib.Path(CONTESTI).write_text(json.dumps(congelati, ensure_ascii=False),
                                          encoding="utf-8")
        print(f"\ncontesti salvati in {CONTESTI}: ora embedding e rerank si possono scaricare")
    print("-" * 74)
    print(f"riscontri nel contesto {nc}   riportati nella risposta {nr} "
          f"({nr/max(nc,1):.0%})")
    if persi:
        print(f"risposte che ne riportano MENO di quelli che avevano: {persi}")


if __name__ == "__main__":
    main()
