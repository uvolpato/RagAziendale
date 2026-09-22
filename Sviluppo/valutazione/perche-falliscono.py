"""Le domande che falliscono: perche'?

    D=/tmp/val-$(date +%s)
    docker compose cp valutazione orchestratore:$D
    docker compose exec -T orchestratore python $D/perche-falliscono.py

Per ogni domanda non trovata mostra il pezzo che AVREBBE dovuto rispondere (il
primo che contiene un riscontro, nel documento giusto, fuori dall'indice) e a
che distanza sta dalla domanda. Tre esiti possibili, e tre correzioni diverse:

  ASSENTE      il testo non e' nell'indice   -> problema di LETTURA
  LONTANO      c'e' ma la distanza e' alta   -> problema di RAPPRESENTAZIONE
  VICINO       c'e' ed e' vicino             -> problema di FUSIONE/soglia
"""
import json
import os
import pathlib
import sys
import unicodedata

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, "/app")
from orchestratore import recupero      # noqa: E402

QUI = pathlib.Path(__file__).parent
# Le quattro che la domanda CORTA perde pur trovandole con quella lunga
# (misurato il 22/09/2026), piu' le due che non si trovano mai.
FALLITE = [1, 6, 9, 14, 8, 16]
# La domanda com'e' scritta nella casella della chat, non quella lunga: e' la'
# che il recupero cade da 18/20 a 14/20.
CORTA = True


def piatto(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def main():
    dati = json.loads((QUI / "domande-eurosand.json").read_text(encoding="utf-8"))
    documento, indice = dati["documento"], dati.get("pagine_indice", [])
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)

    for d in dati["domande"]:
        if d["id"] not in FALLITE:
            continue
        testo = (d.get("corta") if CORTA else None) or d["domanda"]
        q = recupero.embedding(testo)
        print(f"\n=== {d['id']}  {d['categoria']}")
        print(f"    {testo[:96]}")
        # Il confronto si fa in Python, non con ILIKE: nel testo estratto le
        # due parole di «Nachhaltige Verpackung» possono essere separate da un
        # a capo, e ILIKE non lo vede. Con il confronto sbagliato una pagina
        # che c'e' sembra assente, e si va a cercare un difetto di lettura che
        # non esiste (quasi successo il 21/09/2026).
        tutte = conn.execute(
            """SELECT posizione, d, page, content FROM (
                 SELECT row_number() OVER (ORDER BY embedding <=> %s::vector) AS posizione,
                        embedding <=> %s::vector AS d, page, content, documento
                 FROM chunks WHERE embedding IS NOT NULL) x
               WHERE documento = %s AND NOT (page = ANY(%s)) ORDER BY d""",
            (q, q, documento, indice)).fetchall()
        for marca in d["riscontro"]:
            m = piatto(marca)
            righe = [r for r in tutte if m in piatto(r["content"])]
            if not righe:
                print(f"    {marca:22} ASSENTE dall'indice (fuori dalle pagine {indice})")
                continue
            r = righe[0]
            corpo = " ".join(r["content"].split())
            # Quanto di questo pezzo e' prosa? Poche parole lunghe = tabella.
            parole = [w for w in corpo.split() if w.isalpha() and len(w) > 3]
            prosa = len(parole) / max(len(corpo.split()), 1)
            print(f"    {marca:22} posizione {r['posizione']:>5}  d={r['d']:.4f}  "
                  f"p{r['page']:<4} prosa {prosa:.0%}")
            print(f"    {'':22} {corpo[:104]}")


if __name__ == "__main__":
    main()
