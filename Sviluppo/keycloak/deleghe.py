"""Configurazione dell'amministrazione in Keycloak: allineamento al template e
deleghe (Fine-Grained Admin Permissions v2).

    py keycloak/deleghe.py            # applica (idempotente)

Lo chiama avvia.py a ogni avvio. Rende vera la regola della specifica
dell'amministrazione (§3.2): "nessuno si alza i permessi da solo".

PERCHE' SERVE. Con i ruoli classici di Keycloak (manage-users) chi gestisce
gli accessi puo' aggiungersi al profilo "Amministratore completo", assegnarsi
admin-ruoli, o reimpostare la password di un amministratore ed entrare con il
suo account. Verificato il 18/09/2026: tutte e tre riuscivano.

COME. Solo admin-super (Superutente) ha ruoli classici (realm-admin). Tutti
gli altri ruoli admin-* hanno SOLO deleghe: Keycloak non valuta le deleghe per
chi ha anche un solo ruolo classico ("If a realm administrator is assigned one
or more admin roles, it prevents the permissions from being evaluated"), quindi
un ruolo classico in un profilo annullerebbe le deleghe degli altri.

  Utenti:  leggere      -> accessi, ruoli, fonti, revisore
           gestire      -> accessi (crea, modifica, disattiva; password degli
                           operatori: Keycloak la ricava da "manage")
           cambiare gruppi -> accessi, ruoli
  Gruppi:  leggere      -> accessi, ruoli, fonti, revisore
           membri       -> accessi (tutti i gruppi, tranne i profili)
  Profili (/amministratori/*):
           gestire gli account dei membri -> NESSUN delegato
           assegnare/togliere             -> solo ruoli
           Superutente e la radice        -> NESSUN delegato (solo Superutente)
  Creare gruppi, comporre i profili, assegnare ruoli diretti -> solo Superutente.

Verificato da eval/verifica_deleghe.py.
"""
import json
import pathlib
import re
import socket
import sys

import httpx

QUI = pathlib.Path(__file__).resolve().parent.parent
PREFISSO = "deleghe: "          # tutto cio' che lo script crea ha questo nome
REALM = "azienda"
CLIENT_RUOLI = "amministrazione"
# Tutti i ruoli di amministrazione tranne admin-super: SOLO deleghe, mai ruoli classici.
DELEGATI = ("admin-accessi", "admin-fonti", "admin-importazioni", "admin-anomalie",
            "admin-sistemi", "admin-ruoli", "admin-revisore")

_o = socket.getaddrinfo
socket.getaddrinfo = lambda h, p, *a, **k: _o(
    "127.0.0.1" if isinstance(h, str) and h.endswith(".localhost") else h, p, *a, **k)


def env():
    v = {}
    for riga in (QUI / ".env").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z_]+)=(.*)$", riga)
        if m:
            v[m.group(1)] = m.group(2).split("#")[0].strip()
    return v


def allinea_al_template(c, A, ok):
    """Porta un realm GIA' ESISTENTE al passo col template, solo aggiungendo.

    --import-realm non tocca un realm che c'e' gia': senza questo, ogni
    modifica al template vale solo per le installazioni nuove e lo sviluppo
    diverge in silenzio. Mai cancellazioni e mai sovrascritture di client
    (la partialImport in OVERWRITE ricrea il client e perde le assegnazioni
    dei suoi ruoli): gruppi e utenti nuovi con SKIP, il client con PUT.
    """
    reso = json.loads((QUI / "keycloak" / "realm-azienda.json").read_text(encoding="utf-8"))

    # Ruoli mancanti
    r = ok(c.post(f"{A}/partialImport", json={"ifResourceExists": "SKIP", "roles": reso.get("roles", {})})).json()
    if r.get("added"):
        print(f"  realm: aggiunti {r['added']} ruoli dal template")

    # Gruppi e sottogruppi mancanti, con i loro ruoli: la partialImport salta un
    # gruppo esistente INSIEME ai figli nuovi (es. /amministratori/Superutente).
    cid0 = ok(c.get(f"{A}/clients", params={"clientId": CLIENT_RUOLI})).json()[0]["id"]

    def allinea_gruppo(g, padre=None):
        percorso = g["path"]
        esiste = c.get(f"{A}/group-by-path/{percorso.lstrip('/')}")
        if esiste.status_code == 404:
            corpo = {k: g[k] for k in ("name", "attributes") if k in g}
            dove = f"{A}/groups/{padre}/children" if padre else f"{A}/groups"
            ok(c.post(dove, json=corpo))
            print(f"  realm: gruppo {percorso} creato")
            esiste = c.get(f"{A}/group-by-path/{percorso.lstrip('/')}")
        gid = ok(esiste).json()["id"]
        voluti = g.get("clientRoles", {}).get(CLIENT_RUOLI, [])
        if voluti:
            presenti = {x["name"] for x in ok(c.get(f"{A}/groups/{gid}/role-mappings/clients/{cid0}")).json()}
            mancanti = [ok(c.get(f"{A}/clients/{cid0}/roles/{n}")).json() for n in voluti if n not in presenti]
            if mancanti:
                ok(c.post(f"{A}/groups/{gid}/role-mappings/clients/{cid0}", json=mancanti))
                print(f"  realm: {percorso} + {[m['name'] for m in mancanti]}")
        for figlio in g.get("subGroups", []):
            allinea_gruppo(figlio, gid)

    for g in reso["groups"]:
        allinea_gruppo(g)

    # Utenti mancanti (i gruppi ora esistono tutti)
    r = ok(c.post(f"{A}/partialImport", json={"ifResourceExists": "SKIP", "users": reso["users"]})).json()
    if r.get("added"):
        print(f"  realm: aggiunti {r['added']} utenti dal template")

    # Utenti gia' presenti: si aggiungono i gruppi del template che mancano
    for u in reso["users"]:
        trovati = ok(c.get(f"{A}/users", params={"username": u["username"], "exact": "true"})).json()
        if not trovati:
            continue
        uid = trovati[0]["id"]
        attuali = {g["path"] for g in ok(c.get(f"{A}/users/{uid}/groups", params={"max": 500})).json()}
        for percorso in set(u.get("groups", [])) - attuali:
            gid = ok(c.get(f"{A}/group-by-path/{percorso.lstrip('/')}")).json()["id"]
            ok(c.put(f"{A}/users/{uid}/groups/{gid}"))
            print(f"  realm: {u['username']} aggiunto a {percorso}")

    # Ruoli composti: SKIP non aggiorna un ruolo che esiste gia'
    rm = ok(c.get(f"{A}/clients", params={"clientId": "realm-management"})).json()[0]["id"]
    cid = ok(c.get(f"{A}/clients", params={"clientId": CLIENT_RUOLI})).json()[0]["id"]
    for ruolo in reso.get("roles", {}).get("client", {}).get(CLIENT_RUOLI, []):
        voluti = ruolo.get("composites", {}).get("client", {}).get("realm-management", [])
        if not voluti or ruolo["name"] in DELEGATI:          # delegati: vedi sotto, niente classici
            continue
        presenti = {x["name"] for x in ok(c.get(
            f"{A}/clients/{cid}/roles/{ruolo['name']}/composites/clients/{rm}")).json()}
        mancanti = [ok(c.get(f"{A}/clients/{rm}/roles/{n}")).json() for n in voluti if n not in presenti]
        if mancanti:
            rid = ok(c.get(f"{A}/clients/{cid}/roles/{ruolo['name']}")).json()["id"]
            ok(c.post(f"{A}/roles-by-id/{rid}/composites", json=mancanti))
            print(f"  realm: {ruolo['name']} + {[m['name'] for m in mancanti]}")

    # Client amministrazione: impostazioni del template, mapper compresi
    voluto = next(x for x in reso["clients"] if x["clientId"] == CLIENT_RUOLI)
    attuale = ok(c.get(f"{A}/clients/{cid}")).json()
    campi = ("publicClient", "standardFlowEnabled", "directAccessGrantsEnabled",
             "redirectUris", "webOrigins", "description")
    if any(attuale.get(k) != voluto.get(k) for k in campi) or attuale.get("bearerOnly") \
            or any(attuale.get("attributes", {}).get(k) != v for k, v in voluto["attributes"].items()):
        attuale.update({k: voluto[k] for k in campi if k in voluto})
        attuale["bearerOnly"] = False
        attuale.setdefault("attributes", {}).update(voluto["attributes"])
        ok(c.put(f"{A}/clients/{cid}", json=attuale))
        print("  realm: client amministrazione aggiornato")
    nomi = {m["name"] for m in ok(c.get(f"{A}/clients/{cid}/protocol-mappers/models")).json()}
    for m in voluto.get("protocolMappers", []):
        if m["name"] not in nomi:
            ok(c.post(f"{A}/clients/{cid}/protocol-mappers/models", json=m))
            print(f"  realm: mapper {m['name']} aggiunto al client amministrazione")


def allinea_client_oauth(c, A, ok, client_id="dagster"):
    """Client per oauth2-proxy (SSO dei servizi esterni, decisione 60).

    --import-realm non lo aggiunge a un realm esistente: qui lo si crea o
    allinea al template a ogni avvio, in modo idempotente. Il secret non si
    legge (mascherato), quindi si riscrive sempre dal template.
    """
    reso = json.loads((QUI / "keycloak" / "realm-azienda.json").read_text(encoding="utf-8"))
    voluto = next((x for x in reso["clients"] if x["clientId"] == client_id), None)
    if not voluto:
        return
    campi = {k: voluto[k] for k in voluto if k != "protocolMappers"}
    trovati = c.get(f"{A}/clients", params={"clientId": client_id})
    if trovati.status_code == 404 or not trovati.json():
        ok(c.post(f"{A}/clients", json=campi))
        cid = ok(c.get(f"{A}/clients", params={"clientId": client_id})).json()[0]["id"]
        print(f"  realm: client {client_id} creato")
    else:
        cid = trovati.json()[0]["id"]
        ok(c.put(f"{A}/clients/{cid}", json=campi))
        print(f"  realm: client {client_id} allineato")
    nomi = {m["name"] for m in ok(c.get(f"{A}/clients/{cid}/protocol-mappers/models")).json()}
    for m in voluto.get("protocolMappers", []):
        if m["name"] not in nomi:
            ok(c.post(f"{A}/clients/{cid}/protocol-mappers/models", json=m))
            print(f"  realm: mapper {m['name']} aggiunto al client {client_id}")


def main():
    e = env()
    base = f"https://{e['SSO_HOST']}"
    ca = QUI / "certs" / "caddy-root.crt"
    c = httpx.Client(verify=str(ca) if ca.exists() else False, timeout=30)
    tok = c.post(f"{base}/realms/master/protocol/openid-connect/token", data={
        "grant_type": "password", "client_id": "admin-cli",
        "username": "admin", "password": e["KEYCLOAK_ADMIN_PASSWORD"]})
    tok.raise_for_status()
    c.headers["Authorization"] = "Bearer " + tok.json()["access_token"]
    A = f"{base}/admin/realms/{REALM}"

    def ok(r):
        if r.status_code >= 400:
            raise SystemExit(f"{r.request.method} {r.request.url} -> {r.status_code} {r.text[:300]}")
        return r

    allinea_al_template(c, A, ok)
    for client_oauth in ("dagster", "uptime", "litellm", "litellm-sso"):
        allinea_client_oauth(c, A, ok, client_oauth)

    # 1. Deleghe attive sul realm
    realm = ok(c.get(A)).json()
    if not realm.get("adminPermissionsEnabled"):
        ok(c.put(A, json={"adminPermissionsEnabled": True}))
        print("  deleghe attivate sul realm")

    # 2. Nessun ruolo delegato con ruoli classici di realm-management.
    # Keycloak NON valuta le deleghe per chi ne ha anche uno solo: chi somma
    # un ruolo "classico" a Gestione accessi perderebbe i permessi delegati,
    # e Gestione amministratori con manage-users potrebbe farsi Superutente.
    cid = ok(c.get(f"{A}/clients", params={"clientId": CLIENT_RUOLI})).json()[0]["id"]
    rm = ok(c.get(f"{A}/clients", params={"clientId": "realm-management"})).json()[0]["id"]
    ruoli = {}
    for nome in DELEGATI:
        r = ok(c.get(f"{A}/clients/{cid}/roles/{nome}")).json()
        ruoli[nome] = r
        classici = ok(c.get(f"{A}/clients/{cid}/roles/{nome}/composites/clients/{rm}")).json()
        if classici:
            ok(c.request("DELETE", f"{A}/roles-by-id/{r['id']}/composites", json=classici))
            print(f"  {nome}: tolti {len(classici)} ruoli classici")

    # 3. Client delle deleghe, creato da Keycloak all'attivazione
    ap = ok(c.get(f"{A}/clients", params={"clientId": "admin-permissions"})).json()[0]["id"]
    RS = f"{A}/clients/{ap}/authz/resource-server"

    # Idempotenza: si cancella e ricrea tutto cio' che porta il nostro prefisso
    for tipo in ("permission", "policy"):
        for x in ok(c.get(f"{RS}/{tipo}", params={"max": 500})).json():
            if x["name"].startswith(PREFISSO):
                c.delete(f"{RS}/{tipo}/{x['id']}")

    def politica(nome, nomi_ruoli, logica="POSITIVE"):
        return ok(c.post(f"{RS}/policy/role", json={
            "name": PREFISSO + nome, "logic": logica,
            "roles": [{"id": ruoli[n]["id"], "required": False} for n in nomi_ruoli]})).json()["id"]

    def permesso(nome, tipo, scopes, politiche, risorse=()):
        r = c.post(f"{RS}/permission/scope", json={
            "name": PREFISSO + nome, "resourceType": tipo, "scopes": scopes,
            "resources": list(risorse), "policies": politiche,
            # FGAP v2 accetta solo UNANIMOUS (AFFIRMATIVE -> 400, verificato).
            # "Uno qualsiasi di questi ruoli" si esprime con UNA politica che
            # elenca piu' ruoli, ciascuno non obbligatorio.
            "decisionStrategy": "UNANIMOUS"})
        if r.status_code >= 400:
            raise SystemExit(f"permesso «{nome}» rifiutato da Keycloak: {r.status_code} {r.text[:200]}")

    LETTORI = ["admin-accessi", "admin-ruoli", "admin-fonti", "admin-revisore"]
    p_lettori = politica("legge utenti e gruppi", LETTORI)
    p_accessi = politica("e' Gestione accessi", ["admin-accessi"])
    p_ruoli = politica("e' Gestione amministratori", ["admin-ruoli"])
    p_nessuno = politica("NESSUN delegato", LETTORI, "NEGATIVE")
    p_gruppi_utente = politica("cambia i gruppi di un utente", ["admin-accessi", "admin-ruoli"])

    permesso("leggere gli utenti", "Users", ["view"], [p_lettori])
    permesso("creare e modificare gli utenti", "Users", ["manage"], [p_accessi])
    permesso("cambiare i gruppi di un utente", "Users", ["manage-group-membership"], [p_gruppi_utente])
    permesso("leggere i gruppi e i membri", "Groups", ["view", "view-members"], [p_lettori])
    permesso("gestire i membri dei gruppi", "Groups", ["manage-membership"], [p_accessi])

    # 4. Profili di amministrazione (sottoalbero di /amministratori)
    def albero(g):
        yield g
        for f in ok(c.get(f"{A}/groups/{g['id']}/children", params={"max": 500})).json():
            yield from albero(f)

    radice = [g for g in ok(c.get(f"{A}/groups", params={"search": "amministratori", "exact": "true"})).json()
              if g["name"] == "amministratori"]
    tutti = list(albero(radice[0])) if radice else []
    intoccabili = [g["id"] for g in tutti if g["name"] in ("amministratori", "Superutente")]
    profili = [g["id"] for g in tutti if g["id"] not in intoccabili]
    if tutti:
        # Su un gruppo con permessi SPECIFICI il permesso generale non vale piu'
        # (precedenza di Keycloak), nemmeno per la sola lettura: senza questa
        # riga il Revisore non vede i profili ne' i gruppi di chi ne ha uno
        # (verificato: 403 su /users/{id}/groups e /group-by-path/amministratori).
        permesso("leggere i profili", "Groups", ["view", "view-members"], [p_lettori], [g["id"] for g in tutti])
        # Nessun delegato modifica, disattiva o reimposta la password di un
        # amministratore, ne' lo impersona: e' cosi' che si prende un account.
        permesso("NESSUNO tocca gli account degli amministratori", "Groups",
                 ["manage-members", "impersonate-members"], [p_nessuno], [g["id"] for g in tutti])
        # Solo Gestione amministratori assegna e toglie i profili (non Gestione
        # accessi: il permesso specifico prevale su quello generale al punto 3).
        permesso("solo Gestione amministratori assegna i profili", "Groups",
                 ["manage-membership", "manage-membership-of-members"], [p_ruoli], profili)
        # Superutente: lo assegna solo un altro Superutente (ruoli classici).
        permesso("NESSUN delegato assegna Superutente", "Groups",
                 ["manage-membership", "manage-membership-of-members"], [p_nessuno], intoccabili)
    print(f"  deleghe applicate (profili: {len(profili)}, protetti: {len(intoccabili)})")


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as err:
        sys.exit(f"deleghe: {err}")
