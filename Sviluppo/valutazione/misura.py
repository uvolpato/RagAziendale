"""Il metro: quante domande vere trovano la pagina giusta.

    D=/tmp/val-$(date +%s)
    docker compose cp valutazione orchestratore:$D
    docker compose exec -T orchestratore python $D/misura.py

Una cartella NUOVA ogni volta, e non e' pedanteria: `docker compose cp` su una
cartella che esiste gia' copia DENTRO invece che sopra, quindi si rimisura la
versione vecchia senza accorgersene (successo il 21/09/2026: due misure
identiche che sembravano una conferma). E i file arrivano di root, mentre il
container gira come utente, quindi non si possono nemmeno cancellare.

Si misura il RECUPERO, non la risposta: se il pezzo giusto non arriva al
modello, nessun prompt lo salva. Una domanda e' «trovata» se almeno uno dei
pezzi recuperati contiene almeno uno dei `riscontro` della domanda.

Due colonne, perche' e' li' che il metro mentiva:

    lunga   la domanda come l'ha scritta l'azienda, tre righe e ben formata
    corta   la stessa domanda come la scrive una persona nella casella della
            chat, due o cinque parole

Il 22/09/2026 una chat vera ha chiesto «ho bisogno di sassi rossi» e ha avuto
una risposta di una riga con un codice solo, mentre il metro segnava 18/20.
Le domande d'oro sono LUNGHE: portano dentro il contesto, i sinonimi e il
dominio. Nessuno scrive cosi' in una chat. Questa colonna misura quello che
succede davvero.

La riscrittura della domanda stava qui fino al 22/09/2026: bocciata tre volte
(21/09 guadagno zero, 22/09 15/20 e 17/20 contro 17 e 18). Il racconto e' in
RISULTATI.md; la colonna e' andata via perche' una decisione chiusa non si
rimisura a ogni giro.
"""
import json
import os
import pathlib
import sys
import time
import unicodedata

import httpx
import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, "/app")
from orchestratore import recupero      # noqa: E402

QUI = pathlib.Path(__file__).parent
K = int(os.environ.get("VALUTAZIONE_K", "8"))       # quanti pezzi arrivano al modello
LITELLM = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000").rstrip("/")
CHIAVE = os.environ.get("LITELLM_MASTER_KEY", "")

# Bocciata tre volte sulle domande LUNGHE, dove toglie contesto invece di
# aggiungerne. Qui torna per rispondere a un'altra domanda: sulle domande
# CORTE, dove il contesto manca davvero, lo aggiunge?
RISCRITTURA = ("Riscrivi la richiesta come la scriverebbe un catalogo di prodotti per la casa e il "
               "giardino. Usa i termini merceologici, non il parlato. UNA riga, solo i termini. /no_think")


def riscrivi(domanda: str) -> str:
    r = httpx.post(f"{LITELLM}/chat/completions",
                   headers={"Authorization": "Bearer " + CHIAVE},
                   json={"model": os.environ.get("LLM_RAGIONAMENTO", "ragionamento"),
                         "temperature": 0, "max_tokens": 80,
                         "messages": [{"role": "system", "content": RISCRITTURA},
                                      {"role": "user", "content": domanda}]},
                   timeout=300.0)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip().replace('\n', " ") or domanda


def piatto(s: str) -> str:
    """Per il confronto: minuscole, senza accenti, spazi normalizzati. L'OCR
    dei cataloghi sbaglia gli accenti e raddoppia gli spazi."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def trovata(conn, testo: str, gruppi: list, riscontri: list, documento: str, indice: list):
    """(trovata?, posizione del primo pezzo buono, pezzi).

    Il pezzo buono deve venire dal documento giusto e NON dalle pagine
    dell'indice: l'indice del catalogo elenca ogni nome di prodotto, quindi
    combacia con quasi tutto e proverebbe zero (visto il 21/09/2026: la
    domanda sulle microplastiche risultava «trovata» a pagina 2, mentre
    l'informazione vera sta a pagina 28)."""
    pezzi, _ = recupero.cerca(conn, testo, gruppi, recupero.embedding(testo), limite=K)
    attesi = [piatto(x) for x in riscontri]
    for i, p in enumerate(pezzi, 1):
        if p.get("documento") != documento or p.get("page") in indice:
            continue
        if any(a in piatto(p.get("content", "")) for a in attesi):
            return True, i, pezzi
    return False, None, pezzi


def main():
    dati = json.loads((QUI / "domande-eurosand.json").read_text(encoding="utf-8"))
    gruppi, documento = dati["gruppi"], dati["documento"]
    indice = dati.get("pagine_indice", [])
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)

    # Le domande senza risposta non si misurano qui: non c'e' niente da
    # trovare. Servono alla prova della RISPOSTA, che e' un'altra misura.
    con_risposta = [d for d in dati["domande"] if not d.get("senza_risposta")]
    senza = [d for d in dati["domande"] if d.get("senza_risposta")]

    print(f"{len(con_risposta)} domande con risposta · primi {K} pezzi · gruppi {gruppi}")
    print(f"(e {len(senza)} senza risposta, che si misurano sulla risposta, non sul recupero)\n")
    print(f"{'id':>3}  {'lunga':>6}  {'corta':>6}  {'c+ris':>6}  domanda corta -> riscritta")
    print("-" * 110)

    esiti = []
    for d in con_risposta:
        ok_base, pos_base, _ = trovata(conn, d["domanda"], gruppi, d["riscontro"], documento, indice)
        testo = d.get("corta") or d["domanda"]
        ok_ris, pos_ris, _ = trovata(conn, testo, gruppi, d["riscontro"], documento, indice)
        risc = riscrivi(testo)
        ok_cr, pos_cr, _ = trovata(conn, risc, gruppi, d["riscontro"], documento, indice)
        esiti.append((d, ok_base, ok_ris, ok_cr))
        segno = lambda ok, pos: (f"#{pos}" if ok else "no")     # noqa: E731
        print(f"{d['id']:>3}  {segno(ok_base, pos_base):>6}  {segno(ok_ris, pos_ris):>6}  "
              f"{segno(ok_cr, pos_cr):>6}  {testo[:30]:30} -> {risc[:42]}")

    b = sum(1 for _, ok, _, _ in esiti if ok)
    r = sum(1 for _, _, ok, _ in esiti if ok)
    c = sum(1 for _, _, _, ok in esiti if ok)
    n = len(esiti)
    print("-" * 110)
    print(f"trovate: lunga {b}/{n} ({b/n:.0%})   corta {r}/{n} ({r/n:.0%})   "
          f"corta riscritta {c}/{n} ({c/n:.0%})")

    meglio = [d["id"] for d, _, oc, ocr in esiti if ocr and not oc]
    peggio = [d["id"] for d, _, oc, ocr in esiti if oc and not ocr]
    mai = [d["id"] for d, ob, oc, ocr in esiti if not ob and not oc and not ocr]
    if meglio:
        print(f"la riscrittura RECUPERA:   {meglio}")
    if peggio:
        print(f"la riscrittura PERDE:      {peggio}")
    if mai:
        print(f"non trovate mai:         {mai}")
        print("  (o il pezzo non e' nell'indice, o il `riscontro` e' scritto male: vanno guardate a mano)")


if __name__ == "__main__":
    inizio = time.time()
    main()
    print(f"\n{time.time() - inizio:.0f}s")
