"""La prova di Cognee (D13), una cartella per volta e l'isolamento per primo.

    docker compose run --rm cognee --quanti 5          catena intera su 5 file
    docker compose run --rm cognee --quanti 0          tutta la cartella
    docker compose run --rm cognee --solo-cerca        cerca su cio' che c'e'
    docker compose run --rm cognee --gruppo acquisti   un altro gruppo

LE CARTELLE NON SONO QUI: sono la configurazione dei gruppi, la tabella
`sources` (una cartella per gruppo, decisioni 61-64 e 71). Un percorso scritto
a mano in questo file e' la copia piu' insidiosa, perche' continua a funzionare:
`sviluppo` e' gia' collegata in `sources` a `luis/sviluppo`, dentro la quale c'e'
anche il progetto Luis B2B, e l'indice del gruppo si farebbe su una sotto-cartella
mentre il resto del sistema guarda l'altra. Il gruppo si sceglie a mano da riga di
comando; la cartella no, la legge `sources` come fa l'ingestione.

L'ORDINE E' QUELLO DEL PROGETTO, non il mio: `PROBLEMI-APERTI.md` §6-bis dice
che la prova di 1-2 giorni deve COMINCIARE dalla T1.14 — stessa domanda, due
utenti, e la dimostrazione che il secondo non vede i dati del primo — prima di
qualunque valutazione sulla qualita'. Il motivo e' scritto nella
documentazione di Cognee: se `ENABLE_BACKEND_ACCESS_CONTROL` e' falso, i
parametri dataset vengono IGNORATI e la ricerca gira su tutti i dati. Un
interruttore che fallisce APERTO, mentre il nostro filtro fallisce chiuso.

Per questo qui ci sono DUE dataset, due gruppi: quello che si indicizza e uno di
controllo. Senza un secondo dataset l'isolamento non si puo' dimostrare.

NIENTE VERSO FUORI: il modello e gli embedding sono i nostri, su llama-swap,
e il Dockerfile mette un proxy nel vuoto per tutto il resto. La
documentazione avverte che configurare un solo provider locale fa ricadere
l'altro su OpenAI; non ci si fida della configurazione.
"""
import argparse
import asyncio
import os
import pathlib
import re
import time
from urllib.parse import quote

RADICE = pathlib.Path(os.environ.get("RADICE_CARTELLE", "/cartelle"))
# Le stesse regole di `indicizza.py`: relativo alla radice, niente UNC e niente
# `..`. La validazione per esteso sta in `amministrazione/logica.py`, all'atto
# del collegamento; qui si controlla solo che nessuno abbia cambiato la riga dopo.
PERCORSO_VALIDO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/-]*$")


async def cartelle_dei_gruppi():
    """Gruppo -> cartelle collegate, lette dalla configurazione (`sources`).

    Una cartella assente o un percorso non valido vengono detti e saltati, non
    sono un errore: `magazzino` e' collegata e la cartella non c'e', e va bene
    cosi'. Un gruppo senza piu' cartelle non entra e basta.
    """
    import asyncpg
    c = await asyncpg.connect(
        host=os.environ.get("PGHOST", "postgres"),
        port=int(os.environ.get("PGPORT", "5432")),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        database=os.environ.get("RAG_DB", "rag"))
    try:
        righe = await c.fetch("""
            SELECT DISTINCT g AS gruppo, s.percorso, s.aziende
            FROM sources s, unnest(s.acl_groups) g
            WHERE s.provenienza = 'cartella' AND s.stato IN ('attiva', 'attesa')
            ORDER BY 1, 2""")
    finally:
        await c.close()
    gruppi = {}
    for r in righe:
        percorso = str(r["percorso"])
        if not PERCORSO_VALIDO.match(percorso) or ".." in percorso:
            print("   %s: percorso non valido, saltato: %r" % (r["gruppo"], percorso), flush=True)
            continue
        cartella = RADICE / percorso
        if not cartella.is_dir():
            print("   %s: cartella assente, saltata: %s" % (r["gruppo"], cartella), flush=True)
            continue
        gruppo = gruppi.setdefault(str(r["gruppo"]), {"cartelle": [], "aziende": []})
        gruppo["cartelle"].append(cartella)
        for a in r["aziende"] or []:
            if str(a) not in gruppo["aziende"]:
                gruppo["aziende"].append(str(a))
    return gruppi

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
    # Il TEMPO DI ELABORAZIONE. Cognee non ha una manopola di timeout per le
    # chiamate al modello: le passa a litellm e basta (verificato: in
    # `infrastructure/llm/` non c'e' nessun `request_timeout`). Con il rate
    # limit a 2 ogni 30s le chiamate si accodano, e una generazione lunga su
    # contesto grande con due slot occupati puo' metterci minuti: il default di
    # litellm taglia la chiamata e la fa finire come fallimento, spendendo
    # comunque la GPU. 20 minuti e' abbondante per una descrizione.
    litellm.request_timeout = 1200

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
    # Il nostro Postgres, che c'e' gia' ed e' sorvegliato — non sqlite e
    # LanceDB dentro un volume. Database separato: le tabelle di Cognee non si
    # mescolano a `rag`, e si buttano senza toccare niente.
    pg = {
        "db_host": os.environ.get("PGHOST", "postgres"),
        "db_port": os.environ.get("PGPORT", "5432"),
        "db_name": os.environ.get("COGNEE_DB", "cognee"),
        "db_username": os.environ.get("PGUSER", "postgres"),
        "db_password": os.environ["PGPASSWORD"],
    }
    cognee.config.set_relational_db_config(dict(pg, db_provider="postgres"))
    cognee.config.set_vector_db_config({
        "vector_db_provider": "pgvector",
        "vector_db_host": pg["db_host"], "vector_db_port": pg["db_port"],
        "vector_db_name": pg["db_name"], "vector_db_username": pg["db_username"],
        "vector_db_password": pg["db_password"],
        # Il GESTORE dataset->database, che restava su lancedb mentre il
        # provider era pgvector: con l'isolamento acceso Cognee si RIFIUTA di
        # partire se i due non combaciano — «Cannot add support for
        # multi-user access control mode» — invece di spegnere i permessi in
        # silenzio. Fallisce chiuso, ed e' il comportamento giusto.
        "vector_dataset_database_handler": "pgvector",
    })
    # Il grafo resta Kuzu: incorporato, un file, nessun servizio in piu'.
    # Il grafo resta Kuzu/ladybug: incorporato, un file, nessun servizio in
    # piu'. Ma SENZA SOTTOPROCESSO: Cognee apre il database in un processo
    # separato per operazione, e quei processi si contendono il lucchetto del
    # file fra una chiamata e l'altra — il 5/10/2026 l'ingestione dei 36
    # documenti e' morta al decimo con «Could not set lock on file», dopo che
    # i primi nove erano passati. Tenendolo nel processo la contesa sparisce.
    #
    # Resta vero che un file a scrittore singolo non si legge mentre si
    # scrive: con l'indicizzazione in corso il grafo e' cieco. Quello si
    # risolve solo con un motore concorrente (neo4j), che e' il «servizio in
    # piu' da sorvegliare» che DECISIONI-APERTE mette fra i costi.
    cognee.config.set_graph_db_config({
        "graph_database_provider": "kuzu",
        "graph_database_subprocess_enabled": False,
    })
    dati = os.environ.get("COGNEE_DATI", "/dati")
    # Le cartelle si creano QUI: su un volume nuovo non esistono, e sqlite non
    # crea il suo file dentro una directory che manca — «unable to open
    # database file», che sembra un problema di permessi e non lo e'.
    for dove in (dati + "/documenti", dati + "/sistema"):
        os.makedirs(dove, exist_ok=True)
    cognee.config.data_root_directory(dati + "/documenti")
    cognee.config.system_root_directory(dati + "/sistema")
    return cognee


async def prepara(cognee):
    """Le tabelle di Cognee, su Postgres.

    Con sqlite nascevano alla prima scrittura; su Postgres no, e la
    prima chiamata muore con «relation \"principals\" does not
    exist». Si chiama una volta, e' idempotente."""
    from cognee.infrastructure.databases.relational import (
        create_db_and_tables)
    await create_db_and_tables()


def documenti(cartelle, quanti):
    """I file di un gruppo: i .md se ci sono, altrimenti tutto.

    Le esclusioni sono quelle dell'ingestion (`indicizza.py:174`): niente
    cartelle che cominciano per `_` o `.` (`_bozze`, `_archivio`) e quindi
    niente `_sorgenti`, che e' la cache delle conversioni e non materiale da
    mettere in un grafo. Un gruppo puo' avere piu' cartelle (`sicurezza`: una
    per azienda): si mette tutto insieme e si ordina per nome, cosi' lo stesso
    file produce sempre lo stesso grafo."""
    files = []
    for cartella in cartelle:
        scelti = [p for p in cartella.rglob("*") if p.is_file()
                  and not any(x.startswith(("_", "."))
                              for x in p.relative_to(cartella).parts)
                  and not p.name.startswith("~$")]
        md = sorted(p for p in scelti if p.suffix.lower() == ".md")
        files += md if md else sorted(scelti)
    files.sort()
    return files[:quanti] if quanti else files


def uri_di(path):
    """La chiave con cui Cognee registra il documento: `source_uri`.

    Va costruita a mano invece di `Path.as_uri()`: quello rifiuta un percorso
    senza unita' (quindi si rompe appena lo stesso codice gira su Windows), e
    la codificazione deve coincidere al carattere con quella di Cognee, o il
    confronto con il database non trova niente.
    """
    return "file://" + quote(str(path))


async def digeriti(dataset_id):
    """I documenti che Cognee ha GIA' portati nel grafo, per PERCORSO.

    Serve a non rifare il lavoro: un giro intero costa ~2h di GPU, e senza questo
    controllo ogni riavvio ripagava da capo i file del giro precedente. La domanda
    la risponde `data.pipeline_status`, che Cognee aggiorna da solo: un file e'
    finito quando `cognify_pipeline` per QUEL dataset dice
    `DATA_ITEM_PROCESSING_COMPLETED`. I file falliti non lo hanno, e quindi
    vengono rifatti — che e' il punto.

    Si confronta il percorso (`external_metadata._cognee.source_uri`) e NON il
    nome: il 5/10/2026 `AGENTS.md` e `README.md` esistono gia' nel progetto
    RagAziendale, indicizzati da prima, e sono anche il nome di quattro file
    diversi dentro Luis B2B. Sul nome quei quattro sparivano in silenzio: la
    regola "non re-indicizzare" non puo' mangiare documenti nuovi.

    Non e' pero' una garanzia: il 05/10/2026 un file (SINTESI-SESSIONE-27-09)
    risultava completato pur avendo segnalato l'errore, perche' il fallimento
    e' avvenuto dopo, nella fase di sintesi. Percio' `--rifai` esiste: se un
    file e' segnato come fatto e non lo e', si rifa quello e basta.
    """
    # `get_async_session` e non `get_relational_engine`: l'adapter non espone
    # `begin()`, e la sessione e' quella che Cognee usa davvero.
    from cognee.infrastructure.databases.relational import get_async_session
    from sqlalchemy import text as sql
    async with get_async_session() as c:
        righe = (await c.execute(sql(
            "select external_metadata->'_cognee'->>'source_uri' as uri,"
            " pipeline_status from data where dataset_id = :d"),
            {"d": dataset_id})).fetchall()
    finiti = set()
    for uri, stato in righe:
        segno = (stato or {}).get("cognify_pipeline", {}).get(str(dataset_id))
        if segno == "DATA_ITEM_PROCESSING_COMPLETED" and uri:
            finiti.add(uri)
    return finiti


async def nel_dataset(dataset_id):
    """Tutti i documenti che Cognee ha gia' presi, per percorso, in QUALSIASI stato.

    Serve a non re-addere: `add` su un file gia' presente puo' crearne una riga
    in piu', e il dataset si riempie di doppioni. Al grafo, invece, ci pensa
    `cognify` con `incremental_loading`: un file aggiunto e non ancora
    digerito resta in coda e viene lavorato al giro dopo, senza che nessuno lo
    ri-accoda.
    """
    from cognee.infrastructure.databases.relational import get_async_session
    from sqlalchemy import text as sql
    async with get_async_session() as c:
        righe = (await c.execute(sql(
            "select external_metadata->'_cognee'->>'source_uri' as uri"
            " from data where dataset_id = :d"),
            {"d": dataset_id})).fetchall()
    return {uri for (uri,) in righe if uri}


async def utente_e_dataset(gruppo, aziende, crea=True):
    """L'utenza di riferimento del GRUPPO e il suo dataset: uno solo.

    La regola del gruppo: si scrive come `ingestione-<gruppo>@assistente.locale`,
    e tutti gli utenti del gruppo leggono quello per il ruolo. Senza questo,
    `cognee.add` scrive come `default_user@example.com` e Cognee crea un dataset
    NUOVO con lo stesso nome del dataset di servizio: il grafo si biforca in due
    e il lettore (`grafo_api.py:42-50`), che risolve il nome sull'utenza di
    servizio, mostra solo il primo. Il 5/10/2026 c'erano due `sviluppo` e due
    `acquisti`, con 5 documenti che nessuno vedeva.

    Anche `create_authorized_dataset` crea SEMPRE un dataset nuovo — non fa
    get-or-create, verificato in `data/methods/create_authorized_dataset.py` — e
    il tenant va prima, perche' un dataset creato fuori dal tenant non e'
    visibile a chi sta nel ruolo. Quindi: riuso il dataset che l'utenza di
    riferimento ha gia', e solo se non c'e' lo creo.
    """
    from cognee.infrastructure.databases.exceptions import (
        EntityAlreadyExistsError)
    from cognee.modules.data.methods import (create_authorized_dataset,
                                             get_authorized_dataset_by_name)
    from cognee.modules.users.methods import create_user, get_user_by_email
    from cognee.modules.users.tenants.methods import create_tenant

    posta = "ingestione-%s@assistente.locale" % gruppo
    try:
        servizio = await create_user(posta, "prova", is_verified=True)
    except Exception:
        servizio = await get_user_by_email(posta)

    if crea:
        if not aziende:
            raise SystemExit("il gruppo %r non ha aziende collegate: il suo "
                             "dataset starebbe fuori da ogni tenant" % gruppo)
        if len(aziende) > 1:
            print("   %s: il gruppo copre %s ma un dataset sta in un solo "
                  "tenant: prendo %s, gli utenti delle altre aziende non lo "
                  "vedranno finche' il gruppo non ha una cartella per azienda"
                  % (gruppo, ", ".join(aziende), aziende[0]), flush=True)
        try:
            await create_tenant("azienda-%s" % aziende[0], servizio.id)
        except EntityAlreadyExistsError:
            pass    # il tenant c'e' gia', e l'utenza e' gia' dentro
        else:
            # Dopo `create_tenant` l'utenza cambia nel database e l'oggetto in
            # memoria e' scaduto: con quello vecchio `add` muore con
            # «permission: [write]», che sembra un problema di permessi.
            servizio = await get_user_by_email(posta)

    d = await get_authorized_dataset_by_name(gruppo, servizio, "write")
    if d:
        return servizio, d.id
    if not crea:
        return servizio, None
    d = await create_authorized_dataset(gruppo, servizio)
    print("   %s: dataset nuovo %s (utenza %s)" % (gruppo, d.id, posta), flush=True)
    return servizio, d.id


async def carica(cognee, nome, cartelle, quanti, servizio, dataset_id):
    files = documenti(cartelle, quanti)
    print("\n== %s: %d file su %s ==" % (nome, len(files), dataset_id), flush=True)
    # SOLO QUELLO CHE MANCA, e in due tempi distinti perche' sono due cose
    # diverse: nel dataset (non si ri-adda, altrimenti spuntano doppioni) e nel
    # grafo (non si rifa, e un giro completo costa ~2h di GPU).
    pronti, nel = await digeriti(dataset_id), await nel_dataset(dataset_id)
    mancanti = [f for f in files if uri_di(f) in pronti]
    da_aggiungere = [f for f in files if uri_di(f) not in nel]
    da_grafo = [f for f in files if uri_di(f) not in pronti]
    print("   %d gia' nel grafo, non li rifaccio" % len(mancanti), flush=True)
    print("   %d da aggiungere, %d da portare nel grafo"
          % (len(da_aggiungere), len(da_grafo)), flush=True)
    for f in da_grafo:
        print("   " + f.name, flush=True)
    t0 = time.monotonic()
    # `user` + `dataset_id`, non il nome: e' il percorso che garantisce che il
    # documento finisca sul dataset del GRUPPO e non su uno gemello.
    if da_aggiungere:
        await cognee.add([str(f) for f in da_aggiungere], user=servizio,
                          dataset_id=dataset_id)
        print("   aggiunti in %.0fs" % (time.monotonic() - t0), flush=True)
    if not da_grafo:
        print("   niente da fare", flush=True)
        return
    t0 = time.monotonic()
    # I quattro parametri, non sono un dettaglio: senza, con i due slot di
    # llama-swap (rate limit 2 ogni 30s) `cognify` mette in coda tutti i chunk
    # insieme e il giro muore con 429 — e` successo il 5/10/2026 sul gruppo di
    # controllo, dopo 27 minuti di GPU. `incremental_loading` fa saltare quello
    # che e' gia' nel grafo, `raise_on_error=False` lascia indietro il file che
    # fallisce invece di buttare via gli altri. Le ragioni sono in permessi.py.
    await cognee.cognify(datasets=[dataset_id], user=servizio,
                         chunks_per_batch=2, data_per_batch=1,
                         incremental_loading=True, raise_on_error=False)
    print("   grafo costruito in %.0fs" % (time.monotonic() - t0), flush=True)


async def cerca(cognee, dataset_id, domanda, servizio):
    try:
        fuori = await cognee.search(
            query_text=domanda,
            query_type=cognee.SearchType.GRAPH_COMPLETION,
            dataset_ids=[dataset_id], user=servizio)
    except Exception as e:
        return "%s: %s" % (type(e).__name__, e)
    if not fuori:
        return "(niente)"
    return " ".join(str(fuori[0]).split())[:400]


async def principale(args):
    gruppi = await cartelle_dei_gruppi()
    print("\n===== I GRUPPI COLLEGATI (cartelle dalla configurazione) =====", flush=True)
    for nome, info in sorted(gruppi.items()):
        print("   %-11s %-28s %s"
              % (nome, ", ".join(info["aziende"]) or "-",
                 ", ".join(str(c) for c in info["cartelle"])), flush=True)
    if args.gruppo not in gruppi:
        raise SystemExit("il gruppo %r non ha nessuna cartella collegata; "
                         "i gruppi collegati sono %s"
                         % (args.gruppo, ", ".join(sorted(gruppi)) or "nessuno"))
    # Il CONTROLLO serve solo a dimostrare che un gruppo non vede gli altri: se
    # non e' collegato la prova non si fa, e si dice perche'.
    controllo = args.controllo if args.controllo in gruppi else None
    if args.controllo and not controllo:
        print("\n(`%s` non e' collegato a nessuna cartella: la prova "
              "dell'isolamento si ferma a un gruppo)" % args.controllo, flush=True)

    cognee = configura()
    # UTENTE DI RIFERIMENTO E DATASET, una coppia per gruppo: e' la regola del
    # gruppo, e va risolta una volta sola cosi' caricamento, domande e disegno
    # parlano tutti dello stesso grafo.
    target = {}
    for g in (n for n in (args.gruppo, controllo) if n):
        servizio, dataset_id = await utente_e_dataset(
            g, gruppi[g]["aziende"], crea=not args.solo_cerca)
        if dataset_id is None:
            raise SystemExit("il gruppo %r non ha ancora un dataset (nella "
                             "prova `--solo-cerca` non se ne crea uno)" % g)
        target[g] = (servizio, dataset_id)
        print("   %-11s %s  dataset %s"
              % (g, servizio.email, dataset_id), flush=True)

    if not args.solo_cerca:
        await carica(cognee, args.gruppo, gruppi[args.gruppo]["cartelle"],
                     args.quanti, *target[args.gruppo])
        # Il CONTROLLO: due documenti di un'altra azienda, in un altro
        # dataset. Servono solo a dimostrare che non si vedono.
        if controllo:
            await carica(cognee, controllo, gruppi[controllo]["cartelle"], 2,
                         *target[controllo])

    print("\n===== LE DOMANDE, DENTRO `%s` =====" % args.gruppo, flush=True)
    for d in DOMANDE:
        print("\n- %s\n  %s" % (d, await cerca(cognee, target[args.gruppo][1],
                                                d, target[args.gruppo][0])),
              flush=True)

    if controllo:
        print("\n===== T1.14: LA PROVA DELL'ISOLAMENTO =====", flush=True)
        print("La stessa domanda, i due dataset. Chi sta in `%s` non deve "
              "vedere niente del gruppo `%s`." % (controllo, args.gruppo), flush=True)
        sonda = "a che punto e' il progetto RAG aziendale?"
        print("\n- dentro `%s`:\n  %s"
              % (args.gruppo, await cerca(cognee, target[args.gruppo][1], sonda,
                                          target[args.gruppo][0])), flush=True)
        print("\n- dentro `%s` (NON deve sapere):\n  %s"
              % (controllo, await cerca(cognee, target[controllo][1], sonda,
                                        target[controllo][0])), flush=True)

    # Un grafo PER DATASET: `visualize_graph` prende `dataset` (nome o UUID), e
    # il disegno rispetta gli stessi confini della ricerca. Gli passo l'ID, non
    # il nome: il nome e' ambiguo finche' esiste il dataset gemello.
    # `full=True`: senza, `visualize_graph` disegna solo un sotto-grafo di 500
    # nodi attorno ai 10 di grado piu' alto, e i documenti (grado basso) spariscono
    # — il 5/10/2026 nell'export di `sviluppo` c'erano 29 chunk su 45 file.
    from cognee.api.v1.visualize.visualize import visualize_graph
    for nome in (n for n in (args.gruppo, controllo) if n):
        dove = "%s/grafo-%s.html" % (os.environ.get("COGNEE_DATI", "/dati"), nome)
        try:
            await visualize_graph(dove, dataset=target[nome][1],
                                  user=target[nome][0], full=args.full)
            print("\ngrafo di `%s` disegnato in %s" % (nome, dove), flush=True)
        except Exception as e:
            print("\ngrafo di `%s` non disegnato (%s: %s)"
                  % (nome, type(e).__name__, e), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--quanti", type=int, default=5,
                   help="quanti file del gruppo (0 = tutti)")
    p.add_argument("--gruppo", default="sviluppo",
                   help="il gruppo da indicizzare (la cartella la legge `sources`)")
    p.add_argument("--controllo", default="acquisti",
                   help="il secondo gruppo, per la prova dell'isolamento")
    p.add_argument("--solo-cerca", action="store_true")
    p.add_argument("--full", action="store_true",
                   help="grafo intero invece del sotto-grafo di 500 nodi")
    asyncio.run(principale(p.parse_args()))
