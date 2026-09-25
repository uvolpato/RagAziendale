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

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, "/app")
from orchestratore import glossario, recupero, vincoli      # noqa: E402

QUI = pathlib.Path(__file__).parent
K = int(os.environ.get("VALUTAZIONE_K", "8"))       # quanti pezzi arrivano al modello


def piatto(s: str) -> str:
    """Per il confronto: minuscole, senza accenti, spazi normalizzati. L'OCR
    dei cataloghi sbaglia gli accenti e raddoppia gli spazi."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def trovata(conn, testo: str, gruppi: list, riscontri: list, documento: str, indice: list,
            vincolo: str = ""):
    """(trovata?, posizione del primo pezzo buono, pezzi).

    Il pezzo buono deve venire dal documento giusto e NON dalle pagine
    dell'indice: l'indice del catalogo elenca ogni nome di prodotto, quindi
    combacia con quasi tutto e proverebbe zero (visto il 21/09/2026: la
    domanda sulle microplastiche risultava «trovata» a pagina 2, mentre
    l'informazione vera sta a pagina 28)."""
    pezzi, _ = recupero.cerca(conn, testo, gruppi, recupero.embedding(testo),
                              limite=K, vincolo=vincolo)
    attesi = [piatto(x) for x in riscontri]
    for i, p in enumerate(pezzi, 1):
        if p.get("documento") != documento or p.get("page") in indice:
            continue
        if any(a in piatto(p.get("content", "")) for a in attesi):
            return True, i, pezzi
    return False, None, pezzi


def arricchita(conn, domanda: str):
    """La domanda come la cerca davvero l'agente (agente.py, _nodo_capisce +
    _esegui): i termini multilingue del modello (vincoli) piu' quelli del
    glossario del corpus, e il vincolo regex dell'attributo enumerabile.
    Torna (query, vincolo)."""
    _, intent_termini, trovati = vincoli.estrae(domanda)
    termini = glossario.espandi(conn, intent_termini)
    q = domanda
    if termini:
        q = q + " " + " ".join(termini)
    return q, vincoli.regex(trovati)


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
    print(f"{'id':>3}  {'lunga':>6}  {'corta':>6}  {'arricchita':>10}  {'solo termini':>12}  domanda corta")
    print("-" * 76)

    esiti = []
    for d in con_risposta:
        ok_base, pos_base, _ = trovata(conn, d["domanda"], gruppi, d["riscontro"], documento, indice)
        testo = d.get("corta") or d["domanda"]
        ok_ris, pos_ris, _ = trovata(conn, testo, gruppi, d["riscontro"], documento, indice)
        q, vincolo = arricchita(conn, testo)
        ok_arr, pos_arr, _ = trovata(conn, q, gruppi, d["riscontro"], documento, indice,
                                     vincolo=vincolo)
        ok_ter, pos_ter, _ = trovata(conn, q, gruppi, d["riscontro"], documento, indice)
        esiti.append((d, ok_base, ok_ris, ok_arr, ok_ter))
        segno = lambda ok, pos: (f"#{pos}" if ok else "no")     # noqa: E731
        print(f"{d['id']:>3}  {segno(ok_base, pos_base):>6}  {segno(ok_ris, pos_ris):>6}  "
              f"{segno(ok_arr, pos_arr):>10}  {segno(ok_ter, pos_ter):>12}  {testo[:28]}")

    b = sum(1 for _, ok, _, _, _ in esiti if ok)
    r = sum(1 for _, _, ok, _, _ in esiti if ok)
    a = sum(1 for _, _, _, ok, _ in esiti if ok)
    t = sum(1 for _, _, _, _, ok in esiti if ok)
    n = len(esiti)
    print("-" * 76)
    print(f"trovate: lunga {b}/{n} ({b/n:.0%})   corta {r}/{n} ({r/n:.0%})   "
          f"arricchita {a}/{n} ({a/n:.0%})   solo termini {t}/{n} ({t/n:.0%})")

    recuperate = [d["id"] for d, _, oc, oa, _ in esiti if oa and not oc]
    perse = [d["id"] for d, _, oc, oa, _ in esiti if oc and not oa]
    vincolo_dannoso = [d["id"] for d, _, _, oa, ot in esiti if ot and not oa]
    mai = [d["id"] for d, ob, oc, oa, ot in esiti if not ob and not oc and not oa and not ot]
    if recuperate:
        print(f"l'arricchimento RECUPERA: {recuperate}")
    if perse:
        print(f"l'arricchimento PERDE:    {perse}")
    if vincolo_dannoso:
        print(f"il vincolo toglie:        {vincolo_dannoso}")
    if mai:
        print(f"non trovate mai:         {mai}")
        print("  (o il pezzo non e' nell'indice, o il `riscontro` e' scritto male: vanno guardate a mano)")


if __name__ == "__main__":
    inizio = time.time()
    main()
    print(f"\n{time.time() - inizio:.0f}s")
