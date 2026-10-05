"""La prova di Cognee (D13), una cartella per volta e l'isolamento per primo.

    docker compose run --rm cognee --quanti 5          catena intera su 5 file
    docker compose run --rm cognee --quanti 0          tutta la cartella
    docker compose run --rm cognee --solo-cerca        cerca su cio' che c'e'

L'ORDINE E' QUELLO DEL PROGETTO, non il mio: `PROBLEMI-APERTI.md` §6-bis dice
che la prova di 1-2 giorni deve COMINCIARE dalla T1.14 — stessa domanda, due
utenti, e la dimostrazione che il secondo non vede i dati del primo — prima di
qualunque valutazione sulla qualita'. Il motivo e' scritto nella
documentazione di Cognee: se `ENABLE_BACKEND_ACCESS_CONTROL` e' falso, i
parametri dataset vengono IGNORATI e la ricerca gira su tutti i dati. Un
interruttore che fallisce APERTO, mentre il nostro filtro fallisce chiuso.

Per questo qui ci sono DUE dataset: `sviluppo` (la cartella vera, 36
documenti di progetto) e `acquisti` (due soli documenti, come controllo).
Senza un secondo dataset l'isolamento non si puo' dimostrare.

NIENTE VERSO FUORI: il modello e gli embedding sono i nostri, su llama-swap,
e il Dockerfile mette un proxy nel vuoto per tutto il resto. La
documentazione avverte che configurare un solo provider locale fa ricadere
l'altro su OpenAI; non ci si fida della configurazione.
"""
import argparse
import asyncio
import os
import pathlib
import time

RADICE = pathlib.Path(os.environ.get("RADICE_CARTELLE", "/cartelle"))
CARTELLE = {
    "sviluppo": RADICE / "luis" / "sviluppo" / "progetto RagAziendale",
    "acquisti": RADICE / "decobrands" / "acquisti",
}

# Le due domande che oggi il sistema sbaglia, e una di controllo.
DOMANDE = [
    "a che punto siamo?",
    "quali problemi sono ancora aperti?",
    "come e' strutturato il progetto?",
]


def configura():
    """Tutto locale, e l'isolamento ACCESO. Prima di toccare un documento."""
    import cognee
    # Il nostro modello di embedding si chiama `text-embedding-bge-m3-...`, e
    # litellm lo scambia per un `text-embedding-3` di OpenAI: rifiuta
    # `dimensions` con UnsupportedParamsError. Qui i parametri che il nostro
    # server non conosce si lasciano cadere invece di far fallire la chiamata
    # — e' la via che l'errore stesso indica.
    import litellm
    litellm.drop_params = True

    host = os.environ.get("MODELLI_HOST", "host.docker.internal:1235")
    base = host if "://" in host else "http://" + host
    # `custom` con un endpoint OpenAI-compatibile: llama-swap lo e', ed e' il
    # modo in cui lo chiamano anche l'orchestratore e l'ingestione.
    cognee.config.set_llm_config({
        "llm_provider": "custom",
        "llm_model": "openai/" + os.environ.get("MODELLO_CHAT", "qwen3-14b"),
        "llm_endpoint": base + "/v1",
        "llm_api_key": "locale",
    })
    # GLI EMBEDDING, e non e' un dettaglio: la documentazione dice che
    # «configuring only one [provider] will cause the other to fall back to
    # OpenAI». Configurare il modello e dimenticare gli embedding manda i
    # documenti aziendali fuori. Si mette qui E nelle variabili d'ambiente del
    # compose, perche' su un interruttore che fallisce aperto non si risparmia.
    emb = os.environ.get("EMBEDDING_MODELLO", "text-embedding-bge-m3-embeddings")
    cognee.config.set_embedding_config({
        # `openai_compatible`, non `custom`: il secondo cade nel motore
        # LiteLLM, che ricava il tokenizer dal nome del modello e con il
        # nostro va a cercare un repo HuggingFace che non esiste. Questo ramo
        # e' scritto per un server autoospitato — ed e' llama-swap.
        "embedding_provider": "openai_compatible",
        # Senza il prefisso `openai/`: qui il nome si manda tale e quale.
        "embedding_model": emb,
        # Solo la BASE: litellm ci aggiunge lui `/embeddings`, e con il
        # percorso completo chiamava `/v1/embeddings/embeddings` -> 404.
        "embedding_endpoint": base + "/v1",
        "embedding_api_key": "locale",
        "embedding_dimensions": int(os.environ.get("EMBEDDING_DIMENSIONI", "1024")),
        # Il righello, per nome. La variabile d'ambiente non viene letta:
        # Cognee ricava il tokenizer dal NOME del modello
        # («openai/text-embedding-bge-m3-embeddings», che su HuggingFace
        # non esiste) se non glielo si dice qui.
        "huggingface_tokenizer": "BAAI/bge-m3",
    })
    cognee.config.set_vector_db_config({"vector_db_provider": "lancedb"})
    cognee.config.set_graph_db_config({"graph_database_provider": "kuzu"})
    cognee.config.set_relational_db_config({"db_provider": "sqlite"})
    dati = os.environ.get("COGNEE_DATI", "/dati")
    cognee.config.data_root_directory(dati + "/documenti")
    cognee.config.system_root_directory(dati + "/sistema")
    return cognee


def documenti(nome, quanti):
    cartella = CARTELLE[nome]
    files = sorted(p for p in cartella.rglob("*.md") if p.is_file())
    if not files:
        files = sorted(p for p in cartella.rglob("*") if p.is_file())
    return files[:quanti] if quanti else files


async def carica(cognee, nome, quanti):
    files = documenti(nome, quanti)
    print("\n== %s: %d file ==" % (nome, len(files)), flush=True)
    for f in files:
        print("   " + f.name, flush=True)
    t0 = time.monotonic()
    await cognee.add([str(f) for f in files], dataset_name=nome)
    print("   aggiunti in %.0fs" % (time.monotonic() - t0), flush=True)
    t0 = time.monotonic()
    await cognee.cognify(datasets=[nome])
    print("   grafo costruito in %.0fs" % (time.monotonic() - t0), flush=True)


async def cerca(cognee, dataset, domanda):
    try:
        fuori = await cognee.search(
            query_text=domanda,
            query_type=cognee.SearchType.GRAPH_COMPLETION,
            datasets=[dataset])
    except Exception as e:
        return "%s: %s" % (type(e).__name__, e)
    if not fuori:
        return "(niente)"
    return " ".join(str(fuori[0]).split())[:400]


async def principale(args):
    cognee = configura()
    if not args.solo_cerca:
        await carica(cognee, "sviluppo", args.quanti)
        # Il CONTROLLO: due documenti di un'altra azienda, in un altro
        # dataset. Servono solo a dimostrare che non si vedono.
        await carica(cognee, "acquisti", 2)

    print("\n===== LE DOMANDE, DENTRO `sviluppo` =====", flush=True)
    for d in DOMANDE:
        print("\n- %s\n  %s" % (d, await cerca(cognee, "sviluppo", d)), flush=True)

    print("\n===== T1.14: LA PROVA DELL'ISOLAMENTO =====", flush=True)
    print("La stessa domanda, i due dataset. Chi sta in `acquisti` non deve "
          "vedere niente del progetto.", flush=True)
    sonda = "a che punto e' il progetto RAG aziendale?"
    print("\n- dentro `sviluppo`:\n  %s"
          % await cerca(cognee, "sviluppo", sonda), flush=True)
    print("\n- dentro `acquisti` (NON deve sapere):\n  %s"
          % await cerca(cognee, "acquisti", sonda), flush=True)

    # Un grafo PER DATASET: `visualize_graph` prende `dataset`, quindi il
    # disegno rispetta gli stessi confini della ricerca. Il nome non sta fra
    # quelli di primo livello (li' c'e' `cognee_network_visualization`).
    from cognee.api.v1.visualize.visualize import visualize_graph
    for nome in ("sviluppo", "acquisti"):
        dove = "%s/grafo-%s.html" % (os.environ.get("COGNEE_DATI", "/dati"), nome)
        try:
            await visualize_graph(dove, dataset=nome)
            print("\ngrafo di `%s` disegnato in %s" % (nome, dove), flush=True)
        except Exception as e:
            print("\ngrafo di `%s` non disegnato (%s: %s)"
                  % (nome, type(e).__name__, e), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--quanti", type=int, default=5,
                   help="quanti file di `sviluppo` (0 = tutti)")
    p.add_argument("--solo-cerca", action="store_true")
    asyncio.run(principale(p.parse_args()))
