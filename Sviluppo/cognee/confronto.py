"""Il grafo contro il nostro indice, sullo stesso metro.

    docker compose --profile cognee run --rm --no-deps \\
        --entrypoint python cognee /app/confronto.py

LA DOMANDA A CUI RISPONDE. Collegare il grafo alla chat costa tre lavori — un
lettore nell'orchestratore, una mossa per il coordinatore, la traduzione dei
permessi a ogni domanda. Valgono la pena solo se il contesto che il grafo
restituisce e' migliore di quello che il nostro indice porta oggi. Qui si
misura, con lo stesso criterio del banco della ricerca: quanti dei documenti
che possono rispondere compaiono nel contesto.

COME. Per ogni domanda si chiede a Cognee `only_context` (il modo che abbiamo
scelto: 1,5 secondi contro i 307 di `GRAPH_COMPLETION`, che sfora il contesto
del nostro modello) e si guarda quali documenti nomina. Non si giudica la
risposta: si contano le fonti, come fa `valutazione/ricerca.py`.

IL METRO NOSTRO, dalla stessa misura del 5/10/2026 con l'indice completo:
    a che punto siamo?                   indice 0/4   dentro  0/20
    come e' strutturato il progetto?            2/4          18/20
    quali problemi sono ancora aperti?          3/4          20/20
    che modelli usiamo e perche'?               1/4           9/20
"""
import asyncio
import re
import sys
import time

sys.path.insert(0, "/app")

from prova import configura

# domanda -> i documenti che possono rispondere, scritti a mano una volta.
# Sono gli stessi di `valutazione/ricerca.py`: stesso metro, altro motore.
CASI = [
    ("a che punto siamo?",
     ("PROBLEMI-APERTI", "RISULTATI", "SINTESI-SESSIONE", "DECISIONI-APERTE",
      "PIANO-FASE", "VALUTAZIONE-PROGETTO", "PROGETTO-MULTIAGENTE")),
    ("come e' strutturato il progetto?",
     ("ARCHITETTURA", "PROGETTO-RAG-Aziendale", "PROGETTO-MULTIAGENTE",
      "SPECIFICA-")),
    ("quali problemi sono ancora aperti?",
     ("PROBLEMI-APERTI", "DECISIONI-APERTE", "RISULTATI")),
    ("che modelli usiamo e perche'?",
     ("GUIDA-MODELLI", "ANALISI-LITELLM", "JEV-RERANK", "PROGETTO-MULTIAGENTE")),
]

NOME = re.compile(r"[\w\-\.]+\.md", re.I)


async def principale():
    cognee = configura()
    from cognee.modules.users.methods import get_user_by_email
    from cognee.modules.users.permissions.methods import get_readable_datasets

    servizio = await get_user_by_email("ingestione-sviluppo@assistente.locale")
    dati = {d.name: d for d in await get_readable_datasets(servizio.id)}
    d = dati["sviluppo"]

    print("%-36s %-7s %-7s %s" % ("domanda", "sec", "buoni", "documenti nel contesto"))
    print("-" * 110)
    buoni = totali = 0
    for domanda, attesi in CASI:
        t0 = time.monotonic()
        try:
            fuori = await cognee.search(
                query_text=domanda, user=servizio,
                query_type=cognee.SearchType.GRAPH_COMPLETION,
                dataset_ids=[d.id], only_context=True,
                # La memoria di sessione va SPENTA: senza, il contesto
                # comincia con «Previous conversation:» e si porta dietro le
                # domande precedenti di un'altra conversazione.
                session_id=None)
            testo = " ".join(str(fuori).split())
        except Exception as e:
            print("%-36s %-7s %s" % (domanda[:36], "-",
                                     "%s: %s" % (type(e).__name__, str(e)[:60])))
            continue
        secondi = time.monotonic() - t0
        nomi = sorted(set(NOME.findall(testo)))
        n = sum(1 for x in nomi if any(a.lower() in x.lower() for a in attesi))
        buoni, totali = buoni + n, totali + len(nomi)
        print("%-36s %-7.1f %d/%-5d %s"
              % (domanda[:36], secondi, n, len(nomi),
                 ", ".join(x[:26] for x in nomi[:5]) or "(nessun nome)"))
    print("-" * 110)
    print("documenti giusti nel contesto: %d su %d nominati" % (buoni, totali))


if __name__ == "__main__":
    asyncio.run(principale())
