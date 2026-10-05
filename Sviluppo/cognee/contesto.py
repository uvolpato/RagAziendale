"""`only_context` contro `GRAPH_COMPLETION`: quanto costa chiedere al grafo.

    docker compose --profile cognee run --rm --entrypoint python cognee /app/contesto.py

PERCHE'. `GRAPH_COMPLETION` non e' un recupero: SCRIVE la risposta col
modello, e misurato il 5/10/2026 costa 46 secondi. Se l'orchestratore lo
chiamasse cosi', chi fa una domanda aspetterebbe il nostro turno PIU' quello
di Cognee.

`only_context=True` restituisce il contesto — i pezzi che il grafo ha
collegato — senza generare. Cosi' Cognee fa l'unica cosa che noi non sappiamo
fare, seguire i collegamenti, e la risposta la scrive il nostro redattore come
sempre: un recupero in piu', non un turno in piu'.

Qui si misurano entrambi sulla stessa domanda e si guarda cosa torna.
"""
import asyncio
import sys
import time

sys.path.insert(0, "/app")

from prova import configura

DOMANDE = [
    "a che punto e' il progetto RAG aziendale?",
    "quali problemi sono ancora aperti?",
]


async def principale():
    cognee = configura()
    from cognee.modules.users.methods import get_user_by_email
    from cognee.modules.users.permissions.methods import get_readable_datasets

    servizio = await get_user_by_email("ingestione-sviluppo@assistente.locale")
    dati = {d.name: d for d in await get_readable_datasets(servizio.id)}
    d = dati["sviluppo"]
    print("dataset `sviluppo`: %s\n" % d.id)

    for domanda in DOMANDE:
        print("=" * 72)
        print("  %s" % domanda)
        print("=" * 72, flush=True)
        for come, extra in (("GRAPH_COMPLETION (scrive la risposta)", {}),
                            ("only_context (restituisce i pezzi)",
                             {"only_context": True})):
            t0 = time.monotonic()
            try:
                fuori = await cognee.search(
                    query_text=domanda, user=servizio,
                    query_type=cognee.SearchType.GRAPH_COMPLETION,
                    dataset_ids=[d.id], **extra)
                testo = " ".join(str(fuori).split())
            except Exception as e:
                testo = "%s: %s" % (type(e).__name__, str(e).split("\n")[0])
            print("\n%-40s %5.1fs\n   %s"
                  % (come, time.monotonic() - t0, testo[:600]), flush=True)
        print(flush=True)


if __name__ == "__main__":
    asyncio.run(principale())
