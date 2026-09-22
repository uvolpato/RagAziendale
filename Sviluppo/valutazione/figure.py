"""Chiedere la FIGURA di un articolo: esce quella giusta?

    D=/tmp/val-$(date +%s)
    docker compose cp valutazione orchestratore:$D
    docker compose exec -T orchestratore python $D/figure.py

Il 22/09/2026 una chat vera ha chiesto le immagini di FSA1001, DST2001,
DST1001 e RAD1001 e ha ricevuto FSA1043 (crema), FSA1041 (oro), FSA1013NE
(rosa neon) e FSA107ONE (arancio neon). Il codice STA nella descrizione di
ogni figura — «FSA1043 creme cream The image shows...» — quindi il dato c'e'.
A sbagliare e' la ricerca: le immagini si cercano SOLO col vettore, e per un
vettore FSA1001 e FSA1043 sono lo stesso punto. E' lo stesso difetto che sul
testo si e' corretto con l'IDF, e che le immagini non hanno mai avuto.

Come per `codici.py`, le domande si PESCANO. Ma dal TESTO, non dalle
descrizioni: la prima versione di questo metro pescava i codici dalle
descrizioni delle figure e dava 12 su 12 mentre in chat il guasto era
evidente. Misurava i casi che funzionano — per costruzione non poteva pescare
un codice che nelle descrizioni non c'era, cioe' esattamente quelli che
sbagliavano.

Dal testo si pescano tutti. Un codice che sta in una riga di tabella accanto
a un segnaposto DEVE poter ritrovare la sua figura: se non ci riesce, e' un
difetto, non un caso fuori portata.

Trovata = fra le figure restituite ce n'e' UNA la cui descrizione contiene il
codice chiesto. Si misura anche la posizione: prima o quarta cambia molto,
perche' in chat se ne mostrano quattro.
"""
import os
import re
import sys

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, "/app")
from orchestratore import recupero      # noqa: E402

QUANTE = 4           # quante figure si mostrano in chat
QUANTI = 12          # quanti codici si provano
ALTRE = []           # le altre pagine in gara (vedi main)
GRUPPI = ["acquisti", "azienda-decobrands"]
CODICE = re.compile(r"\b(?=[A-Z0-9-]*[0-9])(?=[A-Z0-9-]*[A-Z])[A-Z][A-Z0-9-]{4,11}\b")

FORME = [
    ("nudo", "{c}"),
    ("naturale", "immagine del prodotto {c}"),
]


def codici(conn):
    """(codice, pagina) dal TESTO delle pagine che HANNO figure.

    Si chiede solo di pagine che contengono figure: su una pagina senza
    immagini non c'e' niente da trovare, e contarla come fallimento
    misurerebbe il catalogo invece della ricerca.
    """
    visibili = {d["documento"] for d in recupero.documenti_visibili(conn, GRUPPI)}
    con_figure = {(r["documento"], r["page"]) for r in conn.execute(
        "SELECT DISTINCT documento, page FROM immagini WHERE page IS NOT NULL").fetchall()}
    righe = [r for r in conn.execute(
        "SELECT documento, page, content FROM chunks WHERE page IS NOT NULL ORDER BY id").fetchall()
        if r["documento"] in visibili and (r["documento"], r["page"]) in con_figure]
    quante = {}
    for r in righe:
        for c in set(CODICE.findall(r["content"])):
            quante[c] = quante.get(c, 0) + 1
    # Un codice che compare ovunque non identifica un articolo: e' una sigla
    # di colonna o un'intestazione.
    visti = set()
    fuori = []
    for r in righe:
        for c in sorted(set(CODICE.findall(r["content"]))):
            if quante[c] <= 3 and c not in visti:
                visti.add(c)
                fuori.append((c, r["page"]))
    fuori.sort()
    # Sparsi nell'archivio invece che tutti dalla stessa pagina.
    passo = max(len(fuori) // QUANTI, 1)
    return fuori[::passo][:QUANTI]


def main():
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    elenco = codici(conn)
    if not elenco:
        print("nessun codice pescato dalle descrizioni: il metro non si applica qui")
        return
    # Le altre pagine che accompagnano quella giusta: sono le pagine reali di
    # una risposta, prese dalle figure pescate, cosi' la concorrenza e' quella
    # vera e non una scelta mia.
    global ALTRE
    ALTRE = [p for _, p in elenco][:5]
    print(f"{len(elenco)} figure pescate dall'archivio · prime {QUANTE} figure\n")
    print(f"{'codice':>12}  " + "  ".join(f"{n:>9}" for n, _ in FORME)
          + "   cosa esce al primo posto")
    print("-" * 86)
    conta = {n: 0 for n, _ in FORME}
    for c, pagina in elenco:
        pos, primo = {}, ""
        for nome, forma in FORME:
            testo = forma.format(c=c)
            # Le pagine che il TESTO ha citato, non una sola: in chat sono
            # sei, e la sola pagina 4 di EUROSAND ha 37 figure — oltre cento
            # candidate per quattro posti. Con una pagina sola il metro dava
            # 12 su 12 e non vedeva il guasto che si vedeva in chat.
            figure = recupero.immagini_pertinenti(
                conn, recupero.embedding(testo), GRUPPI, limite=QUANTE,
                pagine=sorted({pagina} | set(ALTRE)))
            pos[nome] = next((i for i, f in enumerate(figure, 1)
                              if c.lower() in (f.get("descrizione") or "").lower()), None)
            conta[nome] += 1 if pos[nome] else 0
            if nome == FORME[-1][0] and figure:
                primo = " ".join((figure[0].get("descrizione") or "").split())[:34]
        segno = lambda v: (f"#{v}" if v else "no")      # noqa: E731
        print(f"{c:>12}  " + "  ".join(f"{segno(pos[n]):>9}" for n, _ in FORME)
              + f"   {primo}")
    print("-" * 86)
    n = len(elenco)
    for nome, _ in FORME:
        print(f"  {nome:9} {conta[nome]}/{n} ({conta[nome]/n:.0%})")
    print("\nSi cerca dentro la PAGINA giusta: se sbaglia qui, sbaglia a maggior")
    print("ragione su tutto il documento.")


if __name__ == "__main__":
    main()
