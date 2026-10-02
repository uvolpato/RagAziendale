"""La stessa batteria del banco, con il THINK SPENTO ovunque.

    docker compose exec -T orchestratore sh -c "cd /app && python $D/senza_think.py 3"

L'ipotesi, ed e' dell'utente: da quando i prompt sono in XML il modello
lavora la struttura a fondo anche senza ragionare, quindi il think e la
forma del prompt sono in parte SOSTITUTI — e si stava pagando due volte la
stessa cosa.

Vale la pena provarla perche' il think e' quasi tutto il tempo di un turno:
col coordinatore che ragiona una chiamata costa 10-22 secondi, senza 1-3.

Il 2/10/2026 la stessa prova, fatta sui prompt in PROSA, era andata male:
da 288 a 178 secondi e due casi rotti («mi serve qualcosa di blu» smetteva
di costruire il ventaglio, «cosa avete di sportivo» rispondeva che articoli
sportivi non ce n'erano). Da li' avevo concluso che sulle domande aperte il
ragionamento non e' spreco ma qualita'. Quella conclusione vale per quei
prompt: se la forma nuova fa il lavoro che prima faceva il think, non vale
piu', e va rimisurata invece che ricordata.

Non tocca il sistema: spegne il think dall'esterno, sostituendo `_decide`
con una versione che passa sempre `ragiona=False`.
"""
import sys

sys.path.insert(0, "/app")
from orchestratore import grafo                                  # noqa: E402

_vero = grafo._decide


def _senza_pensiero(messaggi, strumenti, tentativi=2, ragiona=False,
                    su_pensiero=None):
    return _vero(messaggi, strumenti, tentativi=tentativi, ragiona=False,
                 su_pensiero=su_pensiero)


grafo._decide = _senza_pensiero

import banco                                                     # noqa: E402

if __name__ == "__main__":
    print("THINK SPENTO ovunque\n")
    banco.main()
