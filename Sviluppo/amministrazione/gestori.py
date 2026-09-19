"""Gestori di gruppo: chi sta in `<gruppo>-gestori` aggiunge e toglie i
colleghi del solo `<gruppo>` (per esempio `sicurezza-gestori` -> `sicurezza`).

Lo applica Keycloak con le deleghe (FGAP v2), non questo codice: il pannello
chiama Keycloak con il token del gestore, e un errore qui non puo' dare piu'
di quanto Keycloak conceda.

    allinea(client_keycloak)    # client httpx gia' autenticato, con base_url
                                # = .../admin/realms/<realm>

Lo chiamano keycloak/deleghe.py a ogni avvio (amministratore tecnico) e il
pannello quando il Superutente crea, rinomina o elimina un gruppo (con il suo
token: solo lui puo' modificare le deleghe). Idempotente: cancella e ricrea
tutto cio' che porta il prefisso.

Tre regole di Keycloak 26.7, verificate il 19/09/2026, che spiegano la forma:
- due permessi sullo stesso scope si sommano in E, non in O: aggiungere un
  permesso "i gestori leggono gli utenti" accanto a quello degli
  amministratori li blocca tutti. Per questo i gestori stanno nel ruolo
  `gestore-gruppo`, e deleghe.py lo mette nelle stesse politiche a ruoli;
- un permesso sul singolo gruppo prevale su quello generale: sul gruppo
  gestito vanno ripetuti anche i lettori e Gestione accessi;
- dentro un permesso vale solo UNANIMOUS, ma una politica aggregata puo'
  essere AFFIRMATIVE: e' cosi' che si scrive "amministratori O gestori";
- sulla SINGOLA persona i permessi generali sugli utenti valgono solo se li
  ha tutti (vedere, gestire, cambiare i gruppi): al gestore, che non deve
  poter modificare le persone, "cambiare i gruppi" generale non basta e
  Keycloak risponde 403. Serve un permesso che elenca le persone una per
  una, con gli stessi ruoli di deleghe.py piu' il gestore. Gli account
  degli amministratori NON sono nell'elenco: restano sotto le regole dei
  profili. Una persona creata dopo l'ultimo allineamento il gestore non la
  puo' aggiungere finche' non si riallinea (avvio, o Superutente dal pannello).
"""
RUOLO = "gestore-gruppo"
SUFFISSO = "-gestori"
PREFISSO = "gestori: "
CLIENT_RUOLI = "amministrazione"
# Come keycloak/deleghe.py, che li importa da qui: una lista sola.
LETTORI = ("admin-accessi", "admin-ruoli", "admin-fonti", "admin-revisore")
GESTIONE_MEMBRI = ("admin-accessi",)
CAMBIANO_GRUPPI = ("admin-accessi", "admin-ruoli")     # "cambiare i gruppi di un utente" in deleghe.py
RADICE_PROFILI = "amministratori"


def coppie(nomi_gruppi):
    """Nomi dei gruppi di primo livello -> [(gruppo, gruppo-gestori)] esistenti."""
    nomi = set(nomi_gruppi)
    return sorted((n[:-len(SUFFISSO)], n) for n in nomi
                  if n.endswith(SUFFISSO) and n[:-len(SUFFISSO)] in nomi)


def gestiti(gruppi_utente):
    """Gruppi del token -> gruppi che quella persona gestisce."""
    return sorted(g[:-len(SUFFISSO)] for g in gruppi_utente if g.endswith(SUFFISSO) and len(g) > len(SUFFISSO))


def allinea(c):
    def ok(r):
        if r.status_code >= 400:
            raise RuntimeError(f"{r.request.method} {r.request.url.path} -> {r.status_code} {r.text[:200]}")
        return r

    gruppi = {g["name"]: g for g in ok(c.get("/groups", params={"max": 1000, "briefRepresentation": "true"})).json()}
    lista = coppie(gruppi)

    cid = ok(c.get("/clients", params={"clientId": CLIENT_RUOLI})).json()[0]["id"]
    ruolo = c.get(f"/clients/{cid}/roles/{RUOLO}")
    if ruolo.status_code == 404:
        ok(c.post(f"/clients/{cid}/roles", json={
            "name": RUOLO, "description": "Gestore di un gruppo: aggiunge e toglie i colleghi del suo gruppo. "
                                          "Si ha stando in un gruppo <nome>-gestori, mai assegnato direttamente"}))
        ruolo = c.get(f"/clients/{cid}/roles/{RUOLO}")
    ruolo = ok(ruolo).json()

    # Il ruolo ai gruppi -gestori (e a nessun altro): e' cio' che fa entrare il
    # gestore nel pannello e nelle politiche a ruoli di deleghe.py.
    con_ruolo = {g["name"] for g in ok(c.get(f"/clients/{cid}/roles/{RUOLO}/groups", params={"max": 1000})).json()}
    voluti = {gg for _, gg in lista}
    for nome in voluti - con_ruolo:
        ok(c.post(f"/groups/{gruppi[nome]['id']}/role-mappings/clients/{cid}", json=[ruolo]))
    for nome in con_ruolo - voluti:
        g = gruppi.get(nome) or ok(c.get(f"/group-by-path/{nome}")).json()
        ok(c.request("DELETE", f"/groups/{g['id']}/role-mappings/clients/{cid}", json=[ruolo]))

    ap = ok(c.get("/clients", params={"clientId": "admin-permissions"})).json()[0]["id"]
    rs = f"/clients/{ap}/authz/resource-server"
    for tipo in ("permission", "policy"):
        for x in ok(c.get(f"{rs}/{tipo}", params={"max": 1000})).json():
            if x["name"].startswith(PREFISSO):
                c.delete(f"{rs}/{tipo}/{x['id']}")
    if not lista:
        return []

    def id_ruolo(n):
        return ok(c.get(f"/clients/{cid}/roles/{n}")).json()["id"]

    def politica(tipo, nome, **campi):
        return ok(c.post(f"{rs}/policy/{tipo}", json={"name": PREFISSO + nome, "logic": "POSITIVE", **campi})).json()["id"]

    # Le persone che un gestore puo' mettere nel suo gruppo: tutte tranne gli
    # amministratori (membri di un qualsiasi gruppo sotto /amministratori).
    def membri_albero(g):
        ids = {m["id"] for m in ok(c.get(f"/groups/{g['id']}/members", params={"max": 5000, "briefRepresentation": "true"})).json()}
        for f in ok(c.get(f"/groups/{g['id']}/children", params={"max": 500})).json():
            ids |= membri_albero(f)
        return ids
    radice = gruppi.get(RADICE_PROFILI)
    amministratori = membri_albero(radice) if radice else set()
    persone = [u["id"] for u in ok(c.get("/users", params={"max": 10000, "briefRepresentation": "true"})).json()
               if u["id"] not in amministratori]
    if persone:
        cambiano = politica("role", "cambia i gruppi delle persone",
                            roles=[{"id": id_ruolo(n), "required": False} for n in CAMBIANO_GRUPPI + (RUOLO,)])
        ok(c.post(f"{rs}/permission/scope", json={
            "name": PREFISSO + "cambiare i gruppi delle persone", "resourceType": "Users",
            "scopes": ["manage-group-membership"], "resources": persone, "policies": [cambiano],
            "decisionStrategy": "UNANIMOUS"}))

    lettori = politica("role", "amministratori che leggono",
                       roles=[{"id": id_ruolo(n), "required": False} for n in LETTORI])
    membri = politica("role", "amministratori che gestiscono i membri",
                      roles=[{"id": id_ruolo(n), "required": False} for n in GESTIONE_MEMBRI])
    for gruppo, gg in lista:
        chi = politica("group", f"sta in {gg}", groups=[{"id": gruppi[gg]["id"], "extendChildren": False}])
        legge = politica("aggregate", f"legge {gruppo}", decisionStrategy="AFFIRMATIVE", policies=[lettori, chi])
        cambia = politica("aggregate", f"cambia i membri di {gruppo}", decisionStrategy="AFFIRMATIVE",
                          policies=[membri, chi])
        for nome, scopes, pol in ((f"leggere {gruppo}", ["view", "view-members"], legge),
                                  (f"membri di {gruppo}", ["manage-membership"], cambia)):
            ok(c.post(f"{rs}/permission/scope", json={
                "name": PREFISSO + nome, "resourceType": "Groups", "scopes": scopes,
                "resources": [gruppi[gruppo]["id"]], "policies": [pol], "decisionStrategy": "UNANIMOUS"}))
    return lista


if __name__ == "__main__":
    assert coppie(["sicurezza", "sicurezza-gestori", "orfano-gestori", "vendite"]) == [("sicurezza", "sicurezza-gestori")]
    assert gestiti(["tutti", "sicurezza-gestori", "-gestori", "azienda-luis"]) == ["sicurezza"]
    print("ok")
