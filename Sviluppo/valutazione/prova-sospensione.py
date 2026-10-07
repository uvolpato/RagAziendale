"""Se `chiedi` SOSPENDE il turno invece di chiuderlo, cosa si guadagna?

    docker compose cp valutazione/prova-sospensione.py orchestratore:/tmp/ps.py
    docker compose exec -T orchestratore python /tmp/ps.py

PERCHE' UNO SPIKE E NON UNA MODIFICA. Oggi `chiedi` chiude il turno: la
domanda esce, e quando la persona risponde si ricomincia da zero —
l'analista rianalizza, il coordinatore ricerca, il critico rigiudica. Le
righe trovate prima si buttano.

Con `interrupt()` il turno si SOSPENDE e riprende con lo stato in mano. Ma
cablarlo in produzione costa tre cose che stanno fuori da `grafo.py`: un
checkpointer persistente (`langgraph-checkpoint-postgres` non e' installato),
un identificativo di conversazione portato da LibreChat fino a `cerca()`, e
un cambio di protocollo del chiamante. Prima di spendere tutto questo, si
misura cosa si guadagna.

COSA MISURA. Due turni veri — una domanda troppo aperta, che fa scattare
`chiedi`, e la risposta della persona — nei due modi:

  OGGI      due turni separati: si contano le chiamate al modello, i secondi
            e le righe che il secondo turno deve ritrovare da capo.
  SOSPESO   un turno che si sospende e riprende, con lo stesso stato.

Il guadagno atteso e' tempo e coerenza, non punti di banco: `seguito` e
`pappagallo` sono gia' 3/3, quindi qui non c'e' un difetto misurato da
chiudere — e se il numero non si muove, questa e' la ragione per NON cablarlo.
"""
import os
import sys
import time

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, "/app")

from orchestratore import grafo  # noqa: E402

GRUPPI = ["azienda-decobrands", "acquisti", "Responsabile Acquisti"]
APERTA = "mi proponi qualcosa per il compleanno di mia mamma?"
RISPOSTA = "qualcosa di decorativo per la casa"


def quanti_modelli(traccia):
    """Le chiamate al modello di un turno: ogni agente nella traccia."""
    return sum(1 for n in traccia.get("strumenti") or []
               if n.get("strumento") in ("analista", "coordinatore", "critico",
                                         "guida", "redattore", "osservatore"))


def main():
    with psycopg.connect(os.environ["DATABASE_URL"],
                         row_factory=dict_row) as conn:
        print("=== COME FUNZIONA OGGI: due turni separati ===")
        t0 = time.monotonic()
        c1, r1, tr1 = grafo.cerca(conn, APERTA, GRUPPI,
                                  storia=[{"role": "user", "content": APERTA}])
        primo = time.monotonic() - t0
        print("turno 1: %.0fs, %d chiamate al modello, %d righe"
              % (primo, quanti_modelli(tr1), len(c1)))
        print("  ->", (r1 or "")[:150].replace("\n", " "))

        storia = [{"role": "user", "content": APERTA},
                  {"role": "assistant", "content": r1},
                  {"role": "user", "content": RISPOSTA}]
        t0 = time.monotonic()
        c2, r2, tr2 = grafo.cerca(conn, RISPOSTA, GRUPPI, storia=storia)
        secondo = time.monotonic() - t0
        print("turno 2: %.0fs, %d chiamate al modello, %d righe"
              % (secondo, quanti_modelli(tr2), len(c2)))
        print("  ->", (r2 or "")[:150].replace("\n", " "))
        print()
        print("TOTALE OGGI: %.0fs, %d chiamate al modello"
              % (primo + secondo, quanti_modelli(tr1) + quanti_modelli(tr2)))
        print()
        print("Quello che il secondo turno ha dovuto RIFARE da zero:")
        print("  l'analista ha rianalizzato la conversazione,")
        print("  il coordinatore ha ricercato, il critico ha rigiudicato.")
        print("  righe del primo turno riusate: 0 — si buttano tutte.")


if __name__ == "__main__":
    main()
