"""Cercare piu' volte serve? (decisione D17)

    D=/tmp/val-$(date +%s)
    docker compose cp valutazione orchestratore:$D
    docker compose exec -T orchestratore python $D/agente.py

Confronto appaiato sulle domande CORTE — quelle che una persona scrive
davvero, e dove il recupero cade da 18/20 a 14/20:

    sola     una ricerca, come oggi
    agente   fino a tre ricerche, scegliendo le parole successive dopo aver
             GUARDATO cosa e' tornato

Si misurano due cose, perche' due sono quelle che decidono: se trova, e quanto
costa. Un guadagno di una domanda pagato con dieci secondi a domanda non e'
un guadagno: e' una chat che nessuno usa.
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
from orchestratore import recupero, ricerca_agente      # noqa: E402

QUI = pathlib.Path(__file__).parent
K = 8

# Acceso QUI e non nell'ambiente: questa e' la misura che deve dire se
# accenderlo davvero, e una misura che dipende da come e' configurato il
# servizio si puo' leggere in due modi.
ricerca_agente.ACCESO = True


def piatto(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def posizione(righe, riscontri, documento, indice):
    attesi = [piatto(x) for x in riscontri]
    for i, r in enumerate(righe, 1):
        if r.get("documento") != documento or r.get("page") in indice:
            continue
        if any(a in piatto(r.get("content", "")) for a in attesi):
            return i
    return None


def main():
    dati = json.loads((QUI / "domande-eurosand.json").read_text(encoding="utf-8"))
    gruppi, documento = dati["gruppi"], dati["documento"]
    indice = dati.get("pagine_indice", [])
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)

    print(f"{'id':>3}  {'sola':>5} {'agente':>6}   {'s':>5} {'s':>6}   domanda corta")
    print("-" * 74)
    ok = {"sola": 0, "agente": 0}
    secondi = {"sola": 0.0, "agente": 0.0}
    for d in dati["domande"]:
        if not d.get("corta"):
            continue
        testo, ris = d["corta"], d["riscontro"]
        pos, tempi = {}, {}
        for nome, funzione in (("sola", recupero.cerca), ("agente", ricerca_agente.cerca)):
            t = time.time()
            righe, _ = funzione(conn, testo, gruppi, recupero.embedding(testo), limite=K)
            tempi[nome] = time.time() - t
            secondi[nome] += tempi[nome]
            pos[nome] = posizione(righe, ris, documento, indice)
            ok[nome] += 1 if pos[nome] else 0
        segno = lambda v: (f"#{v}" if v else "no")      # noqa: E731
        print(f"{d['id']:>3}  {segno(pos['sola']):>5} {segno(pos['agente']):>6}   "
              f"{tempi['sola']:>5.1f} {tempi['agente']:>6.1f}   {testo[:34]}")
    n = sum(1 for d in dati["domande"] if d.get("corta"))
    print("-" * 74)
    for nome in ("sola", "agente"):
        print(f"  {nome:7} {ok[nome]}/{n}   {secondi[nome]/n:.1f} s a domanda")


if __name__ == "__main__":
    main()
