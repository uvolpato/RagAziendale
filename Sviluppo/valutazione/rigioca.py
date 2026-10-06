"""RIGIOCA un turno da un nodo, con un prompt diverso.

    docker compose exec -T orchestratore python /app/valutazione/rigioca.py \
        coordinatore "ci sono anche con i cuori blu?"

PERCHE' ESISTE. Una coppia di banchi costa 45-50 minuti d'orologio, e il
6/10/2026 ho speso la giornata a cambiare una frase e rimisurare trenta turni
per vedere cosa cambiava in uno. Qui si esegue il turno UNA volta, si tiene
lo stato a ogni confine di nodo, e poi si riparte dal nodo che interessa con
un prompt diverso: il lavoro fatto prima di quel nodo non si rifà.

COME FUNZIONA. `_prompt()` legge `GRAFO_PROMPT_<AGENTE>` dall'ambiente prima
del predefinito, quindi per cambiare il prompt di un agente basta scrivere
quella variabile: non serve toccare il codice. E il checkpointer permette di
ripartire da un punto invece che dall'inizio — possibile solo da quando le
risorse del turno (la connessione, lo streaming) sono uscite dallo stato.

COSA NON E'. Non è un metro. Un turno rigiocato è UN campione, e su questo
sistema un campione è un aneddoto (vedi `doc/HANDOFF-GRAFO-AGENTICO.md`
§1-bis: lo stesso caso dà risultati opposti a seconda del contesto della
corsa). Serve a LEGGERE cosa cambia in una risposta, non a decidere se una
modifica è buona: per quello resta il banco intero.
"""
import os
import sys

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, "/app")

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from orchestratore import grafo, identita  # noqa: E402

GRUPPI = os.environ.get("VAL_GRUPPI", "azienda-decobrands,acquisti").split(",")


def stato_iniziale(domanda, storia):
    return {
        "domanda": domanda, "storia": storia or [{"role": "user",
                                                  "content": domanda}],
        "dsn": os.environ["DATABASE_URL"],
        "gruppi": GRUPPI, "aziende": identita.aziende(GRUPPI),
        "query_fatte": [], "eseguite": 0, "respinte": 0, "riviste": 0,
        "impegnativa": True, "rispondibile": True, "ambito": "", "manca": [],
        "mosse": [], "esiti": [], "chiarimento": "", "base": "",
        "utente": "rigioca", "resa": False, "oggetto": "", "attributi": [],
        "dove": "", "righe": [], "verdetti": {}, "confermate": [],
        "campione": [], "etichette": {}, "risposta": "", "passi": 0,
        "traccia": [],
    }


def dal_nodo(compilato, cfg, nodo):
    """Il checkpoint da cui `nodo` sta per partire, il PRIMO che lo fa.

    La storia torna dal più recente al più vecchio, quindi il primo che ha
    quel nodo davanti è l'ultima volta che è girato: per il coordinatore,
    che gira più volte, si prende il giro iniziale — l'ultimo della lista.
    """
    buoni = [s for s in compilato.get_state_history(cfg) if nodo in (s.next or ())]
    return buoni[-1] if buoni else None


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__.strip().splitlines()[0]
                 + "\n  rigioca.py <nodo> <domanda> [prompt alternativo]")
    nodo, domanda = sys.argv[1], sys.argv[2]
    alternativo = sys.argv[3] if len(sys.argv) > 3 else ""
    if alternativo and os.path.exists(alternativo):
        alternativo = open(alternativo, encoding="utf-8").read()

    compilato = grafo._grafo.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "rigioca"}}
    with psycopg.connect(os.environ["DATABASE_URL"],
                         row_factory=dict_row) as conn:
        segno = grafo._CONNESSIONE.set(conn)
        try:
            primo = compilato.invoke(stato_iniziale(domanda, None), cfg)
            print("=== come e' andata ===")
            print((primo.get("risposta") or "").strip()[:600])
            print("\npercorso:", " > ".join(
                n.get("strumento", "?") for n in primo.get("traccia") or []))

            punto = dal_nodo(compilato, cfg, nodo)
            if punto is None:
                sys.exit("\nil nodo %r non compare nel percorso" % nodo)
            print("\n=== si riparte da `%s` ===" % nodo)
            if not alternativo:
                print("(nessun prompt alternativo: si rigioca identico, "
                      "e serve a vedere quanto il turno e' stabile)")
            else:
                os.environ["GRAFO_PROMPT_%s" % nodo.upper()] = alternativo
                print("prompt alternativo: %d caratteri" % len(alternativo))
            # Si FORCA il checkpoint invece di riprendere in posto: cosi'
            # la storia del primo giro resta leggibile, e si possono provare
            # due prompt diversi dallo stesso punto.
            fork = compilato.update_state(punto.config, {"passi": 0})
            dopo = compilato.invoke(None, fork)
            print()
            print((dopo.get("risposta") or "").strip()[:600])
            print("\npercorso:", " > ".join(
                n.get("strumento", "?") for n in dopo.get("traccia") or []))
        finally:
            grafo._CONNESSIONE.reset(segno)


if __name__ == "__main__":
    main()
