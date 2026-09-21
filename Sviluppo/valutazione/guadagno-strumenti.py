"""Quanto guadagnano i due strumenti nuovi, sulle domande vere.

    D=/tmp/val-$(date +%s)
    docker compose cp valutazione orchestratore:$D
    docker compose exec -T orchestratore python $D/guadagno-strumenti.py

Non si misura il potenziale: si misura cosa succede usandoli come li userebbe
un agente, con le informazioni che ha lui (la DOMANDA, non la risposta).

1. RICERCA ESATTA — prende dalla domanda le parole che sembrano un NOME:
   fra virgolette, o con la maiuscola in mezzo alla frase, o un codice. Sono
   quelle che un agente estrarrebbe. Poi le cerca alla lettera.

2. PAGINA INTERA — guarda se fra i primi 8 pezzi della ricerca normale ce
   n'e' almeno uno della STESSA PAGINA del pezzo che risponde. Se si', dare la
   pagina invece del frammento sarebbe bastato: l'informazione era li' accanto
   e il sistema non poteva chiederla.
"""
import json
import os
import pathlib
import re
import sys
import unicodedata

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, "/app")
from orchestratore import recupero      # noqa: E402

QUI = pathlib.Path(__file__).parent
K = 8

# Un nome, non una parola qualsiasi: fra virgolette, oppure con la maiuscola
# ma NON a inizio frase, oppure un codice tipo E5500 / MAK9018 / B02G1.
VIRGOLETTE = re.compile(r"['‘’“”]([^'‘’“”]{3,30})['‘’“”]")
MAIUSCOLA = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-ZÀÈÉÌÒÙ][a-zàèéìòù]{3,})\b")
CODICE = re.compile(r"\b([A-Z]{1,5}\d{3,}[A-Z]*)\b")


def piatto(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def nomi(domanda: str) -> list:
    """Le parole della domanda che un agente proverebbe a cercare alla lettera."""
    trovati = VIRGOLETTE.findall(domanda) + CODICE.findall(domanda) + MAIUSCOLA.findall(domanda)
    # Via i nomi propri di persona/azienda generici e i doppioni.
    fuori = {"europallet", "amfori"} & set()      # nessuno escluso per ora
    visti, out = set(), []
    for t in trovati:
        t = t.strip()
        if len(t) >= 3 and t.lower() not in visti and t.lower() not in fuori:
            visti.add(t.lower())
            out.append(t)
    return out


def main():
    dati = json.loads((QUI / "domande-eurosand.json").read_text(encoding="utf-8"))
    gruppi, documento = dati["gruppi"], dati["documento"]
    indice = dati.get("pagine_indice", [])
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)

    def buono(r, attesi):
        return (r.get("documento") == documento and r.get("page") not in indice
                and any(a in piatto(r.get("content", "")) for a in attesi))

    print(f"{'id':>3}  {'oggi':>5}  {'esatta':>6}  {'pagina':>6}  termini cercati alla lettera")
    print("-" * 96)
    ogg = esa = pag = 0
    for d in dati["domande"]:
        if d.get("senza_risposta"):
            continue
        attesi = [piatto(x) for x in d["riscontro"]]
        pezzi, _ = recupero.cerca(conn, d["domanda"], gruppi,
                                  recupero.embedding(d["domanda"]), limite=K)
        ok_oggi = any(buono(p, attesi) for p in pezzi)

        # 1. ricerca esatta sui nomi estratti dalla domanda
        termini = nomi(d["domanda"])
        ok_esatta = False
        for t in termini:
            if any(buono(r, attesi) for r in recupero.cerca_esatta(conn, t, gruppi, limite=K)):
                ok_esatta = True
                break

        # 2. la pagina dei pezzi gia' recuperati conteneva la risposta?
        ok_pagina = ok_oggi
        if not ok_oggi:
            for p in pezzi:
                if p.get("documento") != documento or p.get("page") in indice:
                    continue
                if any(buono(r, attesi)
                       for r in recupero.pagina(conn, p["documento"], p["page"], gruppi)):
                    ok_pagina = True
                    break

        ogg += ok_oggi
        esa += ok_esatta
        pag += ok_pagina
        s = lambda b: "si" if b else "no"        # noqa: E731
        print(f"{d['id']:>3}  {s(ok_oggi):>5}  {s(ok_esatta):>6}  {s(ok_pagina):>6}  "
              f"{', '.join(termini[:5])[:52]}")

    n = sum(1 for d in dati["domande"] if not d.get("senza_risposta"))
    print("-" * 96)
    print(f"oggi                       {ogg}/{n}")
    print(f"solo ricerca esatta        {esa}/{n}")
    print(f"oggi + pagina intera       {pag}/{n}")
    # Unione: l'agente puo' usarli tutti e due.
    print("\n(l'agente li usa insieme: il totale utile e' l'unione, non la colonna migliore)")


if __name__ == "__main__":
    main()
