"""Deleghe di amministrazione in Keycloak (Fine-Grained Admin Permissions v2).

    py keycloak/deleghe.py            # applica (idempotente)

Lo chiama avvia.py a ogni avvio. Rende vera la regola della specifica
dell'amministrazione (§3.2): "nessuno si alza i permessi da solo".

PERCHE' SERVE. Con i ruoli classici di Keycloak (manage-users) chi gestisce
gli accessi puo' aggiungersi al profilo "Amministratore completo", assegnarsi
admin-ruoli, o reimpostare la password di un amministratore ed entrare con il
suo account. Verificato il 18/09/2026: tutte e tre riuscivano.

COME. admin-accessi NON ha ruoli di realm-management: i ruoli classici
scavalcano le deleghe ("If a realm administrator is assigned one or more admin
roles, it prevents the permissions from being evaluated"). Riceve invece:
  - Utenti (tutti):  view, manage, manage-group-membership
                     — NON map-roles (i ruoli si assegnano solo con i profili),
                       NON impersonate
                     — NON reset-password ESPLICITO. Senza, Keycloak la ricava
                       da "manage": consentita sugli operatori, NEGATA sugli
                       amministratori dal divieto sui gruppi. Concederla
                       esplicitamente su tutti gli utenti scavalcava il divieto
                       (verificato: cambiava la password di un amministratore
                       e si entrava al suo posto).
  - Gruppi (tutti):  view, view-members, manage-membership
  - Gruppi di /amministratori: DIVIETO su manage-membership, manage-members,
    manage-membership-of-members, impersonate-members. Un permesso specifico
    ha precedenza su quello generale, e un DENY vince sempre.
admin-ruoli e admin-revisore restano sui ruoli classici: il primo e' il
super-amministratore per costruzione, il secondo e' in sola lettura.
"""
import pathlib
import re
import socket
import sys

import httpx

QUI = pathlib.Path(__file__).resolve().parent.parent
PREFISSO = "deleghe: "          # tutto cio' che lo script crea ha questo nome
REALM = "azienda"
CLIENT_RUOLI = "amministrazione"

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

    # 1. Deleghe attive sul realm
    realm = ok(c.get(A)).json()
    if not realm.get("adminPermissionsEnabled"):
        ok(c.put(A, json={"adminPermissionsEnabled": True}))
        print("  deleghe attivate sul realm")

    # 2. admin-accessi senza ruoli classici di realm-management
    cid = ok(c.get(f"{A}/clients", params={"clientId": CLIENT_RUOLI})).json()[0]["id"]
    ruolo = ok(c.get(f"{A}/clients/{cid}/roles/admin-accessi")).json()
    rm = ok(c.get(f"{A}/clients", params={"clientId": "realm-management"})).json()[0]["id"]
    classici = ok(c.get(f"{A}/clients/{cid}/roles/admin-accessi/composites/clients/{rm}")).json()
    if classici:
        ok(c.request("DELETE", f"{A}/roles-by-id/{ruolo['id']}/composites", json=classici))
        print(f"  admin-accessi: tolti {len(classici)} ruoli classici")

    # 3. Client delle deleghe, creato da Keycloak all'attivazione
    ap = ok(c.get(f"{A}/clients", params={"clientId": "admin-permissions"})).json()[0]["id"]
    RS = f"{A}/clients/{ap}/authz/resource-server"

    # Idempotenza: si cancella e ricrea tutto cio' che porta il nostro prefisso
    for tipo in ("permission", "policy"):
        for x in ok(c.get(f"{RS}/{tipo}", params={"max": 500})).json():
            if x["name"].startswith(PREFISSO):
                c.delete(f"{RS}/{tipo}/{x['id']}")

    def politica(nome, logica):
        return ok(c.post(f"{RS}/policy/role", json={
            "name": PREFISSO + nome, "logic": logica,
            "roles": [{"id": ruolo["id"], "required": False}]})).json()["id"]

    si = politica("e' admin-accessi", "POSITIVE")
    no = politica("NON per admin-accessi", "NEGATIVE")

    def permesso(nome, tipo, scopes, politica_id, risorse=()):
        ok(c.post(f"{RS}/permission/scope", json={
            "name": PREFISSO + nome, "resourceType": tipo, "scopes": scopes,
            "resources": list(risorse), "policies": [politica_id],
            "decisionStrategy": "UNANIMOUS"}))

    permesso("admin-accessi gestisce gli utenti", "Users",
             ["view", "manage", "manage-group-membership"], si)
    permesso("admin-accessi gestisce l'appartenenza ai gruppi", "Groups",
             ["view", "view-members", "manage-membership"], si)

    # 4. Divieto sui profili di amministrazione (tutto il sottoalbero)
    def albero(g):
        yield g["id"]
        figli = ok(c.get(f"{A}/groups/{g['id']}/children", params={"max": 500})).json()
        for f in figli:
            yield from albero(f)

    radice = [g for g in ok(c.get(f"{A}/groups", params={"search": "amministratori", "exact": "true"})).json()
              if g["name"] == "amministratori"]
    protetti = list(albero(radice[0])) if radice else []
    if protetti:
        permesso("admin-accessi NON tocca i profili di amministrazione", "Groups",
                 ["manage-membership", "manage-members", "manage-membership-of-members",
                  "impersonate-members"], no, protetti)
    print(f"  deleghe applicate (gruppi protetti: {len(protetti)})")


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as err:
        sys.exit(f"deleghe: {err}")
