"""T1.14 — l'isolamento si DIMOSTRA, non si legge nella documentazione.

    docker compose --profile cognee run --rm --entrypoint python cognee /app/t114.py

E' il cancello che `PROBLEMI-APERTI.md` §6-bis mette davanti a qualunque
valutazione sulla qualita' di Cognee, e il motivo e' scritto nella
documentazione: se `ENABLE_BACKEND_ACCESS_CONTROL` e' falso, «i parametri
dataset vengono IGNORATI e le interrogazioni girano su tutti i dati del
sistema, indipendentemente dai permessi». Un interruttore che fallisce
APERTO, mentre il nostro filtro fallisce chiuso.

DUE UTENTI, non due dataset. La prova fatta la notte del 4/10 passava due
`datasets=[...]` diversi con lo STESSO utente predefinito: dimostra che il
parametro funziona, non che i permessi tengono. Qui ci sono due utenti veri,
ognuno con un permesso solo, e tre domande:

  1. l'utente di `acquisti` chiede del progetto INDICANDO `sviluppo`
     -> deve essere rifiutato, non servito
  2. l'utente di `acquisti` chiede del progetto SENZA indicare niente
     -> e' il caso che svela un interruttore aperto: se risponde col
        progetto, i permessi non tengono
  3. che dataset vede ognuno dei due
     -> ognuno il suo, e nient'altro
"""
import asyncio
import os
import sys

sys.path.insert(0, "/app")

from prova import configura

DOMANDA = "a che punto e' il progetto RAG aziendale?"


def riga(etichetta, testo, atteso):
    piatto = " ".join(str(testo).split())
    print("\n%s\n  atteso: %s\n  %s" % (etichetta, atteso, piatto[:300]),
          flush=True)


async def principale():
    cognee = configura()
    from cognee.modules.users.methods import (create_user, get_default_user,
                                              get_user_by_email)
    from cognee.modules.users.permissions.methods import (
        get_readable_datasets, give_permission_on_dataset)

    padrone = await get_default_user()
    nomi = {d.name: d for d in await get_readable_datasets(padrone)}
    print("dataset esistenti: %s" % ", ".join(sorted(nomi)) or "(nessuno)")

    utenti = {}
    for area in ("sviluppo", "acquisti"):
        posta = "%s@prova.locale" % area
        try:
            u = await create_user(posta, "prova-" + area, is_verified=True)
        except Exception:
            u = await get_user_by_email(posta)
        utenti[area] = u
        if area in nomi:
            try:
                await give_permission_on_dataset(u, nomi[area].id, "read")
            except Exception as e:
                print("permesso su %s non dato (%s: %s)"
                      % (area, type(e).__name__, e))
        print("utente %s: %s" % (area, getattr(u, "id", "?")))

    acq, svi = utenti["acquisti"], utenti["sviluppo"]

    # 1. chiede del progetto indicando il dataset di un altro
    try:
        f = await cognee.search(query_text=DOMANDA, user=acq,
                                query_type=cognee.SearchType.GRAPH_COMPLETION,
                                datasets=["sviluppo"])
        riga("1. l'utente di `acquisti` chiede indicando `sviluppo`", f,
             "un rifiuto, non una risposta")
    except Exception as e:
        riga("1. l'utente di `acquisti` chiede indicando `sviluppo`",
             "%s: %s" % (type(e).__name__, e), "un rifiuto, non una risposta")

    # 2. IL CASO CHE CONTA: senza indicare niente
    try:
        f = await cognee.search(query_text=DOMANDA, user=acq,
                                query_type=cognee.SearchType.GRAPH_COMPLETION)
        riga("2. lo stesso utente chiede SENZA indicare il dataset", f,
             "niente del progetto: vede solo `acquisti`")
    except Exception as e:
        riga("2. lo stesso utente chiede SENZA indicare il dataset",
             "%s: %s" % (type(e).__name__, e),
             "niente del progetto: vede solo `acquisti`")

    # 3. e il controllo positivo: chi ha il permesso risponde
    try:
        f = await cognee.search(query_text=DOMANDA, user=svi,
                                query_type=cognee.SearchType.GRAPH_COMPLETION)
        riga("3. l'utente di `sviluppo` chiede senza indicare il dataset", f,
             "la risposta sul progetto: il permesso c'e'")
    except Exception as e:
        riga("3. l'utente di `sviluppo` chiede senza indicare il dataset",
             "%s: %s" % (type(e).__name__, e),
             "la risposta sul progetto: il permesso c'e'")

    print("\n4. che dataset vede ognuno")
    for area, u in utenti.items():
        visti = [d.name for d in await get_readable_datasets(u)]
        print("   %-10s vede: %s" % (area, ", ".join(sorted(visti)) or "(niente)"),
              flush=True)


if __name__ == "__main__":
    asyncio.run(principale())
