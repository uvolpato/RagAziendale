"""Le tre cose che `leggi` deve fare, e che non si vedono dal banco.

    docker compose exec -T orchestratore python /app/valutazione/prova-leggi.py

Il banco misura la risposta; qui si misura la mossa. Tre asserzioni, perche'
tre sono le cose che il primo collaudo ha sbagliato o avrebbe potuto:

1. APRE. Un documento di testo si apre per nome parziale.
2. CONTINUA SENZA RIPETERE. La maniglia del seguito era `page`, e su un .md
   `page` e' NULL: «richiama con page=1» non riprendeva niente e `page >= 3`
   toglieva il documento intero. Adesso e' il numero del pezzo.
3. NON ESCE DAI PERMESSI. L'ACL e' quella di `sql_agente._applica_acl` e non
   una scritta qui, ma una mossa che apre documenti per nome e' esattamente
   il posto dove un filtro dimenticato non si noterebbe.
"""
import os
import re
import sys

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, "/app")

from orchestratore import grafo, identita  # noqa: E402

DOC = "PROGETTO-MULTIAGENTE"
SUO = ["azienda-luis", "sviluppo"]      # chi quel documento lo vede
ALTRI = ["azienda-decobrands", "acquisti"]   # chi no


def prova(conn):
    righe, testo = grafo._mossa_leggi(conn, {"documento": DOC}, SUO,
                                      identita.aziende(SUO))
    assert righe, "non ha aperto il documento: " + testo
    assert all(r.get("content") for r in righe), "righe senza testo"
    print("apre          %d pezzi" % len(righe))

    seguito = re.search(r"da=(\d+)", testo)
    assert seguito, "documento lungo senza maniglia per il seguito: " + testo
    dopo, _ = grafo._mossa_leggi(conn, {"documento": DOC,
                                        "da": int(seguito.group(1))},
                                 SUO, identita.aziende(SUO))
    assert dopo, "il seguito non ha riportato niente"
    primi = {r["id"] for r in righe}
    assert not (primi & {r["id"] for r in dopo}), "il seguito ripete i pezzi"
    print("continua      %d pezzi, nessuno ripetuto" % len(dopo))

    vietate, _ = grafo._mossa_leggi(conn, {"documento": DOC}, ALTRI,
                                    identita.aziende(ALTRI))
    assert not vietate, "FUGA: aperto da un gruppo che non lo puo' vedere"
    print("permessi      chiuso a chi non lo vede")


if __name__ == "__main__":
    with psycopg.connect(os.environ["DATABASE_URL"],
                         row_factory=dict_row) as conn:
        prova(conn)
    print("tutto a posto")
