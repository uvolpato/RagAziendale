"""Il grafo di Cognee MENTRE cresce, letto dal file vero.

    docker compose --profile cognee run --rm \
        -v "%TEMP%:/out" --entrypoint python cognee /app/grafo_live.py --out /out

Non e' una copia: si apre il Kuzu in sola lettura mentre l'ingestione scrive.
Il lock di Kuzu e' TRANSITORIO (lo tiene solo durante i checkpoint), quindi qui
si ritenta e basta: 8 aperture su 8 in mezza ora di ingestione.

I dati non viaggiano con `fetch` — su `file://` il browser lo blocca — ma con un
.js che si riscrive e si ricarica da solo. Cosi' zero server, zero dipendenze,
e il file si apre con un doppio clic.

Un dettaglio che costa poco e conta: le posizioni sono inizializzate dagli HASH
degli id, quindi due caricamenti disegnano lo stesso grafo nello stesso posto e
la vista non salta mentre l'ingestione aggiunge nodi.
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


async def risolvi(nome, dati):
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="sviluppo")
    ap.add_argument("--out", default=os.environ.get("COGNEE_DATI", "/dati"))
    ap.add_argument("--ogni", type=int, default=8, help="secondi fra un dump e l'altro")
    a = ap.parse_args()
    dati = os.environ.get("COGNEE_DATI", "/dati")

    dataset_id = asyncio.run(risolvi(a.dataset, dati))
    if not dataset_id:
        sys.exit("dataset %r non trovato" % a.dataset)
    percorso = percorso_grafo(dati, dataset_id)
    if not percorso:
        sys.exit("nessun Kuzu per il dataset %s" % dataset_id)
    print("dataset %s -> %s" % (dataset_id, percorso), flush=True)
    print("scrivo in %s, ogni %ds (Ctrl-C per fermare)" % (a.out, a.ogni), flush=True)

    while True:
        try:
            nodi, archi = leggi_grafo(percorso)
            gradi = {}
            for x in archi:
                gradi[x["s"]] = gradi.get(x["s"], 0) + 1
                gradi[x["t"]] = gradi.get(x["t"], 0) + 1
            for n in nodi:
                n["grado"] = gradi.get(n["id"], 0)
            corpo = json.dumps({"nodi": nodi, "archi": archi,
                                "quando": time.strftime("%H:%M:%S")},
                               ensure_ascii=False)
            # `window.GRAFO = {...};` e non JSON puro: e' un .js, e cosi'
            # <script src> lo carica anche da file://, dove fetch e' bloccato.
            with open("%s/grafo-live.js" % a.out, "w", encoding="utf-8") as fh:
                fh.write("window.GRAFO = " + corpo + ";\n")
            print("%s  %d nodi, %d archi" % (time.strftime("%H:%M:%S"),
                                             len(nodi), len(archi)), flush=True)
        except Exception as e:
            print("%s  non letto: %s" % (time.strftime("%H:%M:%S"),
                                        type(e).__name__), flush=True)
        time.sleep(a.ogni)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass