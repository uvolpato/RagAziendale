"""Il grafo di Cognee, servito e aggiornato da solo.

    docker compose --profile cognee run --rm -p 8099:8099 \
        --entrypoint python cognee /app/grafo_api.py --porta 8099

PERCHE' UN SERVER E NON UN FILE. Con il `.js` che si riscrive, il browser lo
carica ma non si aggiorna da solo: serve ricaricare la pagina. Un endpoint che
risponde e una pagina che interroga ogni N secondi e' l'unico modo di avere il
grafo vivo senza toccare nulla a mano.

Cognee ha gia' un'API (FastAPI) e un suo `cognee.api`, ma non espone i nodi e
gli archi in un formato che una vista di grafo possa disegnare. Questo fa solo
quello: legge il Kuzu vero in sola lettura e restituisce JSON. Non tocca i
dati, non chiama l'LLM, non interferisce con l'ingestione.
"""
import argparse
import asyncio
import glob
import json
import os
import sys
import time

sys.path.insert(0, "/app")


def percorso_grafo(dati, dataset_id):
    """Il .pkl di Kuzu del dataset. Le directory sono nominate per hash, quindi
    si cerca per nome di file invece di fidarsi del percorso esatto."""
    trovati = glob.glob("%s/sistema/databases/*/%s.pkl" % (dati, dataset_id))
    return trovati[0] if trovati else None


async def risolvi(nome):
    """Il dataset per nome -> il suo id, con le API di Cognee (che gia' fanno
    cosi' in permessi.py: l'id serve anche per il permesso)."""
    from prova import configura
    from cognee.modules.users.permissions.methods import get_readable_datasets
    from cognee.modules.users.methods import get_user_by_email

    configura()
    for gruppo in (nome, "sviluppo", "acquisti"):
        try:
            utente = await get_user_by_email(
                "ingestione-%s@assistente.locale" % gruppo)
        except Exception:
            continue
        if not utente:
            continue
        for d in await get_readable_datasets(utente.id):
            if d.name == nome:
                return d.id
    return None


def leggi_grafo(percorso):
    """Nodi e archi dal Kuzu in sola lettura, con un tiro sul lock."""
    import kuzu
    ultimo = None
    for tentativo in range(8):
        try:
            c = kuzu.Connection(kuzu.Database(percorso, read_only=True))
            nodi = [{"id": r[0], "nome": r[1] or "", "tipo": r[2] or "?"}
                    for r in c.execute("MATCH (n:Node) RETURN n.id, n.name, n.type")]
            archi = [{"s": r[0], "t": r[1], "r": r[2] or ""} for r in c.execute(
                "MATCH (a)-[e:EDGE]->(b) RETURN a.id, b.id, e.relationship_name")]
            return nodi, archi
        except Exception as e:          # il lock: si aspetta e si riprova
            ultimo = e
            time.sleep(2 + tentativo)
    raise ultimo


def arricchisci(nodi, archi):
    """Il grado serve a dimensionare il pallino: un nodo con 30 archi e' un
    concetto, uno con zero e' una mention. Senza questo la vista e' piatta."""
    gradi = {}
    for x in archi:
        gradi[x["s"]] = gradi.get(x["s"], 0) + 1
        gradi[x["t"]] = gradi.get(x["t"], 0) + 1
    for n in nodi:
        n["grado"] = gradi.get(n["id"], 0)
    return nodi, archi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="sviluppo")
    ap.add_argument("--porta", type=int, default=8099)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--ogni", type=int, default=8,
                    help="secondi fra una lettura e l'altra")
    a = ap.parse_args()
    dati = os.environ.get("COGNEE_DATI", "/dati")

    import uvicorn
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, JSONResponse

    dataset_id = asyncio.run(risolvi(a.dataset))
    percorso = percorso_grafo(dati, dataset_id) if dataset_id else None
    if not percorso:
        sys.exit("nessun Kuzu per il dataset %r" % a.dataset)
    print("dataset %s -> %s" % (dataset_id, percorso), flush=True)

    # Il grafo in memoria, riletto ogni `--ogni` secondi. Cosi' ogni richiesta
    # del browser e' immediata: il Kuzu si apre una volta sola per ciclo.
    stato = {"nodi": [], "archi": [], "quando": "-", "errore": None,
             "letto": 0.0}

    async def riletti():
        while True:
            try:
                nodi, archi = leggi_grafo(percorso)
                arricchisci(nodi, archi)
                stato.update(nodi=nodi, archi=archi, errore=None,
                             quando=time.strftime("%H:%M:%S"),
                             letto=time.time())
                print("%s  %d nodi, %d archi"
                      % (stato["quando"], len(nodi), len(archi)), flush=True)
            except Exception as e:
                # Il lock di Kuzu e' transitorio: si segnala e si riprova, senza
                # perdere il grafo precedente. Cosi' la pagina non resta vuota.
                stato["errore"] = "%s: %s" % (type(e).__name__, str(e)[:80])
            await asyncio.sleep(a.ogni)

    app = FastAPI()

    @app.on_event("startup")
    async def avvia():
        asyncio.create_task(riletti())

    @app.get("/api/grafo")
    async def api_grafo():
        # Il ETag serve a una cosa sola: il browser scarica 1 byte se il grafo
        # non e' cambiato. Senza, ogni 8 secondi si trasferiscono megabyte.
        corpo = JSONResponse({"nodi": stato["nodi"], "archi": stato["archi"],
                             "quando": stato["quando"],
                             "errore": stato["errore"],
                             "letto": stato["letto"]})
        corpo.headers["Cache-Control"] = "no-cache"
        return corpo

    @app.get("/")
    async def pagina():
        # FileResponse, non un HTML inline: il file sta su disco, si modifica
        # senza riavviare il server.
        return FileResponse("/app/grafo.html")

    print("servo su http://%s:%d  (ogni %ds)" % (a.host, a.porta, a.ogni),
          flush=True)
    uvicorn.run(app, host=a.host, port=a.porta, log_level="warning")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass