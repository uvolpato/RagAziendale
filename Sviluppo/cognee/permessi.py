"""La catena dei permessi, nell'ordine giusto, e la prova in tre direzioni.

    docker compose --profile cognee run --rm --entrypoint python cognee /app/permessi.py --quanti 2

PERCHE' L'ORDINE CONTA. La prima stesura creava i tenant DOPO aver ingerito, e
`create_tenant(nome, utente)` imposta il tenant ATTIVO di quell'utente: il
proprietario e' finito in un tenant nuovo e i suoi dataset — creati fuori da
ogni tenant — sono diventati invisibili a lui stesso. Il contenitore si decide
PRIMA, e chi ingerisce ci scrive dentro.

LA SEQUENZA
  1. il tenant dell'azienda
  2. l'utente di SERVIZIO dentro quel tenant: e' lui che possiede i dataset
  3. l'ingestione come lui, con `create_authorized_dataset`, cosi' il dataset
     nasce gia' con read/write/share al proprietario (`cognee.add` nudo no:
     lascia il proprietario senza `share`, e senza `share` non puo' concedere
     niente a nessuno)
  4. un ruolo per gruppo, e il permesso AL RUOLO
  5. le persone entrano nel ruolo ed ereditano

LA PROVA, in tre direzioni — perche' una sola non basta:
  - chi ha il permesso RISPONDE          (altrimenti abbiamo solo chiuso)
  - chi non ce l'ha NON VEDE             (ne' indicando il dataset ne' senza)
  - dopo la REVOCA, chi rispondeva SMETTE

La terza e' quella che di solito non si prova. Oggi il nostro permesso e' una
`WHERE` letta adesso, quindi sospendere una fonte ha effetto nello stesso
istante; Cognee MEMORIZZA le ACL, e una revoca che non viene propagata lascia
l'accesso aperto senza che nessuno se ne accorga.
"""
import argparse
import asyncio
import pathlib
import sys

sys.path.insert(0, "/app")

from prova import (cartelle_dei_gruppi, configura, digeriti, documenti,
                   nel_dataset, prepara, uri_di)

DOMANDA = "a che punto e' il progetto RAG aziendale?"

# azienda -> (gruppo Keycloak, cartella/dataset). Oggi uno a uno.
AREE = [
    ("azienda-luis", "sviluppo", "sviluppo"),
    ("azienda-decobrands", "acquisti", "acquisti"),
]


def mostra(etichetta, esito, atteso):
    print("\n%s\n   atteso: %s\n   %s"
          % (etichetta, atteso, " ".join(str(esito).split())[:260]), flush=True)


def risponde(esito) -> bool:
    """Vero se la risposta contiene informazione, non un rifiuto."""
    # Una LISTA VUOTA non e' una risposta: contarla come tale faceva
    # passare «concede» mentre l'utente non riceveva niente.
    if isinstance(esito, (list, tuple)) and not esito:
        return False
    t = " ".join(str(esito).split()).lower()
    if not t or t in ("[]", "()") or "error" in t:
        return False
    vuote = ("non contiene informazioni", "non sono disponibili",
             "non e' menzionato", "non è menzionato", "nessuna informazione",
             "non fornisce informazioni", "got it")
    return not any(v in t for v in vuote)


# `digeriti()` sta in `prova.py`: e' la stessa domanda per tutti e mezzo, e
# due copie di un controllo che decide se spendere due ore di GPU divergono.



async def principale(quanti, solo_prova=False, tutti=False, rifai=False):
    cognee = configura()
    await prepara(cognee)
    # Le cartelle vengono dalla CONFIGURAZIONE dei gruppi (`sources`), non da
    # qui: un percorso scritto nel codice e' il modo piu' economico di
    # indicizzare la cartella sbagliata e non accorgersene. Sotto-cartelle
    # comprese, e `_sorgenti`/`_bozze` escluse: lo fa `documenti()`.
    gruppi = await cartelle_dei_gruppi()
    if not solo_prova:
        mancanti = [g for _, g, _ in AREE if g not in gruppi]
        if mancanti:
            raise SystemExit("gruppi senza cartella collegata in `sources`: %s"
                             % ", ".join(mancanti))
    from cognee.modules.data.methods import create_authorized_dataset
    from cognee.modules.users.methods import create_user, get_user_by_email
    from cognee.modules.users.permissions.methods import (
        authorized_give_permission_on_datasets, get_readable_datasets)
    from cognee.modules.users.permissions.methods import (
        authorized_revoke_permission_on_datasets)
    from cognee.modules.users.roles.methods import add_user_to_role, create_role
    from cognee.modules.users.tenants.methods import (add_user_to_tenant,
                                                      create_tenant)

    async def utente(posta, nota):
        try:
            return await create_user(posta, "prova", is_verified=True)
        except Exception:
            return await get_user_by_email(posta)

    if tutti:
        # L'INGESTIONE DEL RESTO, sugli oggetti che la prova ha gia' creato.
        import time
        from cognee.modules.users.permissions.methods import (
            get_readable_datasets as _leggibili)
        for azienda, gruppo, cartella in AREE:
            if gruppo != "sviluppo":
                continue          # oggi solo i documenti, non i cataloghi
            servizio = await get_user_by_email(
                "ingestione-%s@assistente.locale" % gruppo)
            dati = {d.name: d for d in await _leggibili(servizio.id)}
            if cartella not in dati:
                raise SystemExit("il dataset `%s` non esiste per %s: prima "
                                 "`permessi.py` senza --tutti"
                                 % (cartella, servizio.email))
            d = dati[cartella]
            files = documenti(gruppi[gruppo]["cartelle"], 0)
            print("%s: %d file in tutto" % (gruppo, len(files)), flush=True)
            # I file gia' nel grafo si saltano: senza, ogni riavvio ripagava
            # da capo i documenti del giro precedente (~30 min di GPU buttati).
            # Il confronto e' sul PERCORSO, non sul nome: vedi `digeriti()`.
            pronti = set() if rifai else await digeriti(d.id)
            nel = set() if rifai else await nel_dataset(d.id)
            if pronti:
                print("   %d gia' nel grafo, li salto" % len(pronti), flush=True)
            da_fare = [f for f in files if uri_di(f) not in pronti]
            for n, f in enumerate(files, 1):
                if uri_di(f) in pronti:
                    print("  %2d/%d %-52s   gia' fatto"
                          % (n, len(files), f.name[:52]), flush=True)
            if not da_fare:
                print("   niente da fare", flush=True)
                continue

            # UN SOLO cognify per tutti i file, non uno per file.
            # `cognify` accetta solo `datasets`: richiamarlo dentro il ciclo
            # faceva rigeografizzare TUTTO il dataset ogni volta, quindi al
            # file 10 si rilavoravano anche i 9 precedenti — lavoro che cresce
            # col quadrato dei file, e con esso il numero di chiamate in
            # coda contro i 2 slot di llama-swap. Da li' i 429.
            # `incremental_loading` (default True) fa saltare da solo quanto e'
            # gia' nel grafo, quindi il giro riparte solo sui falliti.
            #
            # `chunks_per_batch=2`: i chunk di un documento vengono estratti in
            # parallelo, e senza questo numero si mettono in coda tutti insieme.
            # Due, per i due slot reali.
            # `raise_on_error=False`: un file che fallisce non deve impedire
            # agli altri 35 di essere digeriti.
            #
            # `add` solo di quelli che NON ci sono gia': re-addere un file
            # presente crea una riga in piu', e il dataset si empie di doppioni.
            da_aggiungere = [f for f in da_fare if uri_di(f) not in nel]
            print("   %d file, %d gia' nel dataset (non li ri-aggiungo), "
                  "un solo cognify" % (len(da_fare), len(da_fare) - len(da_aggiungere)),
                  flush=True)
            t0 = time.monotonic()
            if da_aggiungere:
                await cognee.add([str(f) for f in da_aggiungere], dataset_id=d.id,
                                 user=servizio)
            await cognee.cognify(datasets=[d.id], user=servizio,
                                 chunks_per_batch=2, data_per_batch=1,
                                 incremental_loading=True, raise_on_error=False)
            print("   giro finito in %.0fs" % (time.monotonic() - t0),
                  flush=True)
            fatti = await digeriti(d.id)
            mancanti = [f.name for f in da_fare if uri_di(f) not in fatti]
            if mancanti:
                print("   NON RIUSCITI (%d): %s"
                      % (len(mancanti), ", ".join(mancanti)), flush=True)
            else:
                print("   tutti i file sono nel grafo", flush=True)
        return

    stato = {}
    if solo_prova:
        # Si ritrova per nome quello che c'e' gia', senza rifare `cognify`.
        from cognee.modules.users.permissions.methods import get_role
        for azienda, gruppo, cartella in AREE:
            servizio = await get_user_by_email(
                "ingestione-%s@assistente.locale" % gruppo)
            persona = await get_user_by_email("%s@prova.locale" % gruppo)
            dati = {d.name: d for d in await get_readable_datasets(servizio.id)}
            ruolo = await get_role(servizio.tenant_id, gruppo)
            stato[gruppo] = {"persona": persona, "servizio": servizio,
                             "dataset": dati.get(cartella),
                             "ruolo": getattr(ruolo, "id", ruolo)}
            print("%-10s dataset=%s ruolo=%s" % (
                gruppo, getattr(stato[gruppo]["dataset"], "name", "?"),
                stato[gruppo]["ruolo"]))
    for azienda, gruppo, cartella in ([] if solo_prova else AREE):
        print("\n===== %s / gruppo %s / cartella %s =====" % (azienda, gruppo, cartella))

        # 1-2. il tenant, e dentro di esso chi possiede i dati.
        servizio = await utente("ingestione-%s@assistente.locale" % gruppo, azienda)
        tenant_id = await create_tenant(azienda, servizio.id)
        # RILEGGERE dopo il tenant: `create_tenant` cambia l'utente nel
        # database, e l'oggetto in memoria resta quello di prima. Con quello
        # vecchio in mano, `add` muore con «permission: [write]» — che sembra
        # un problema di permessi e invece e' un oggetto scaduto.
        servizio = await get_user_by_email(servizio.email)
        print("   tenant %s, proprietario %s" % (tenant_id, servizio.email))

        # 3. il dataset nasce AUTORIZZATO: il creatore ha read/write/share.
        d = await create_authorized_dataset(cartella, servizio)
        files = documenti(gruppi[gruppo]["cartelle"], quanti)
        print("   %d file: %s" % (len(files), ", ".join(f.name for f in files)))
        await cognee.add([str(f) for f in files], dataset_id=d.id, user=servizio)
        await cognee.cognify(datasets=[d.id], user=servizio)
        print("   grafo costruito")

        # 4-5. il ruolo prende il permesso, la persona entra nel ruolo.
        ruolo_id = await create_role(gruppo, servizio.id)
        await authorized_give_permission_on_datasets(ruolo_id, [d.id], "read",
                                                     servizio.id)
        persona = await utente("%s@prova.locale" % gruppo, gruppo)
        # `set_as_active_tenant=True`: senza, la persona entra
        # nell'associazione ma il suo `tenant_id` resta vuoto, e
        # `get_all_user_permission_datasets` filtra con
        # `dataset.tenant_id == user.tenant_id` — vuoto contro pieno,
        # scarta tutto. Il permesso c'era: non si vedeva.
        await add_user_to_tenant(persona.id, tenant_id, servizio.id,
                                 set_as_active_tenant=True)
        await add_user_to_role(persona.id, ruolo_id, servizio.id)
        print("   ruolo %s -> permesso `read`; %s nel ruolo"
              % (gruppo, persona.email))
        stato[gruppo] = {"persona": persona, "ruolo": ruolo_id, "dataset": d,
                         "servizio": servizio}

    async def chiedi(persona, **extra):
        try:
            return await cognee.search(
                query_text=DOMANDA, user=persona,
                query_type=cognee.SearchType.GRAPH_COMPLETION, **extra)
        except Exception as e:
            return "%s: %s" % (type(e).__name__, str(e).split("\n")[0][:150])

    svi, acq = stato["sviluppo"], stato["acquisti"]
    for gruppo, s in stato.items():
        visti = [d.name for d in await get_readable_datasets(s["persona"].id)]
        print("   prima di chiedere, %s vede: %s"
              % (gruppo, ", ".join(sorted(visti)) or "(niente)"))
    print("\n" + "=" * 72)
    print("  LA PROVA, in tre direzioni")
    print("=" * 72)

    a = await chiedi(svi["persona"])
    mostra("1. chi HA il permesso (dal gruppo) chiede", a,
           "RISPONDE: altrimenti abbiamo solo chiuso")
    uno = risponde(a)

    # DIAGNOSI: lo stesso utente, ma indicando il dataset per ID. Se qui
    # risponde, il permesso c'e' e il problema e' come la ricerca risolve il
    # dataset quando non glielo si dice; se tace anche qui, non arriva ai dati.
    a2 = await chiedi(svi["persona"], dataset_ids=[svi["dataset"].id])
    mostra("1-bis. lo stesso, indicando il dataset per ID", a2,
           "serve a capire DOVE si perde")
    # E il proprietario, che i dati li ha scritti lui.
    a3 = await chiedi(svi["servizio"])
    mostra("1-ter. il PROPRIETARIO chiede", a3,
           "se tace anche lui, il problema non sono i permessi")

    b = await chiedi(acq["persona"])
    mostra("2. chi NON ce l'ha chiede, senza indicare il dataset", b,
           "niente del progetto")
    due = not risponde(b)

    c = await chiedi(acq["persona"], datasets=["sviluppo"])
    mostra("3. chi NON ce l'ha chiede INDICANDO `sviluppo`", c,
           "un rifiuto")
    tre = not risponde(c)

    # LA REVOCA, che e' la meta' che non si prova mai.
    # La variante AUTORIZZATA, che prende gli id: quella di basso livello
    # vuole l'oggetto del principal e con un UUID muore.
    await authorized_revoke_permission_on_datasets(
        svi["ruolo"], [svi["dataset"].id], "read", svi["servizio"].id)
    d = await chiedi(svi["persona"])
    mostra("4. REVOCATO il permesso al ruolo, chi prima rispondeva chiede", d,
           "SMETTE di rispondere")
    quattro = not risponde(d)

    print("\n5. che dataset vede ognuno, dopo la revoca")
    for gruppo, s in stato.items():
        visti = [x.name for x in await get_readable_datasets(s["persona"].id)]
        print("   %-10s vede: %s" % (gruppo, ", ".join(sorted(visti)) or "(niente)"))

    print("\n" + "=" * 72)
    for nome, ok in (("concede", uno), ("non vede, senza dataset", due),
                     ("non vede, col dataset", tre), ("REVOCA", quattro)):
        print("  %-26s %s" % (nome, "passa" if ok else "NON PASSA"))
    print("=" * 72)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--quanti", type=int, default=2)
    p.add_argument("--solo-prova", action="store_true",
                   help="solo le quattro verifiche, sui dati gia' costruiti")
    p.add_argument("--tutti", action="store_true",
                   help="ingerisce tutta la cartella, riusando il setup")
    p.add_argument("--rifai", action="store_true",
                   help="rifai anche i file che sono gia' nel grafo")
    a = p.parse_args()
    asyncio.run(principale(a.quanti, a.solo_prova, a.tutti, a.rifai))
