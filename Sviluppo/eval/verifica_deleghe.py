"""Verifica le deleghe di amministrazione (keycloak/deleghe.py).

    ./.venv/Scripts/python.exe eval/verifica_deleghe.py

Regola sotto test (specifica amministrazione §3.2): chi gestisce gli accessi
gestisce gli operatori, ma NON puo' alzarsi i permessi ne' prendere il posto
di un amministratore. Cancella cio' che crea.
"""
import pathlib
import re
import socket
import sys

import httpx

QUI = pathlib.Path(__file__).resolve().parent.parent
_o = socket.getaddrinfo
socket.getaddrinfo = lambda h, p, *a, **k: _o(
    "127.0.0.1" if isinstance(h, str) and h.endswith(".localhost") else h, p, *a, **k)

E = {m.group(1): m.group(2).split("#")[0].strip()
     for m in re.finditer(r"^([A-Z_]+)=(.*)$", (QUI / ".env").read_text(encoding="utf-8"), re.M)}
SSO = f"https://{E['SSO_HOST']}"
A = f"{SSO}/admin/realms/azienda"
c = httpx.Client(verify=False, timeout=30)
esiti = []


def token(utente):
    r = c.post(f"{SSO}/realms/azienda/protocol/openid-connect/token", data={
        "grant_type": "password", "client_id": "test-token",
        "username": utente, "password": E["TEST_USER_PASSWORD"]})
    r.raise_for_status()
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def prova(nome, r, attesi):
    ok = r.status_code in attesi
    esiti.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {nome}: HTTP {r.status_code}")


def id_utente(h, nome):
    return c.get(f"{A}/users", params={"username": nome, "exact": "true"}, headers=h).json()[0]["id"]


def id_gruppo(h, percorso):
    return c.get(f"{A}/group-by-path/{percorso.lstrip('/')}", headers=h).json()["id"]


def main():
    adm = token("prova.admin")
    acc = token("prova.accessi")
    io = id_utente(adm, "prova.accessi")
    capo = id_utente(adm, "prova.admin")
    vendite = id_gruppo(adm, "/vendite")
    completo = id_gruppo(adm, "/amministratori/Amministratore completo")
    it = id_gruppo(adm, "/amministratori/Responsabile IT")

    print("\nGestione accessi (prova.accessi) — il lavoro normale")
    prova("elenca utenti", c.get(f"{A}/users", headers=acc), [200])
    prova("crea un operatore", c.post(f"{A}/users", headers=acc,
          json={"username": "prova.verifica", "enabled": True}), [201])
    nuovo = id_utente(adm, "prova.verifica")
    prova("lo mette in 'vendite'", c.put(f"{A}/users/{nuovo}/groups/{vendite}", headers=acc), [204])
    prova("reimposta la password di un operatore",
          c.put(f"{A}/users/{nuovo}/reset-password", headers=acc,
                json={"type": "password", "value": "Temporanea-123!", "temporary": True}), [204])

    print("\nGestione accessi — tentativi di alzarsi i permessi (devono FALLIRE)")
    prova("si aggiunge a 'Amministratore completo'",
          c.put(f"{A}/users/{io}/groups/{completo}", headers=acc), [403])
    prova("aggiunge l'operatore a 'Responsabile IT'",
          c.put(f"{A}/users/{nuovo}/groups/{it}", headers=acc), [403])
    master = c.post(f"{SSO}/realms/master/protocol/openid-connect/token", data={
        "grant_type": "password", "client_id": "admin-cli", "username": "admin",
        "password": E["KEYCLOAK_ADMIN_PASSWORD"]}).json()["access_token"]
    ruoli_realm = c.get(f"{A}/roles/offline_access",
                        headers={"Authorization": f"Bearer {master}"}).json()
    ruoli_realm = [ruoli_realm]
    prova("si assegna un ruolo del realm",
          c.post(f"{A}/users/{io}/role-mappings/realm", headers=acc, json=ruoli_realm[:1]), [403])
    r = c.put(f"{A}/users/{capo}/reset-password", headers=acc,
              json={"type": "password", "value": "Presa-123!", "temporary": False})
    if r.status_code < 300:
        # Il buco e' aperto: rimettere subito la password, o il test lascia
        # un amministratore con una password nota a chi l'ha presa.
        c.put(f"{A}/users/{capo}/reset-password", headers={"Authorization": f"Bearer {master}"},
              json={"type": "password", "value": E["TEST_USER_PASSWORD"], "temporary": False})
    prova("reimposta la password di un amministratore", r, [403])
    prova("disattiva un amministratore",
          c.put(f"{A}/users/{capo}", headers=acc, json={"enabled": False}), [403])
    prova("toglie un amministratore dal suo profilo",
          c.delete(f"{A}/users/{capo}/groups/{completo}", headers=acc), [403])

    print("\nAmministratore completo (prova.admin) — puo' assegnare i profili")
    prova("mette l'operatore in 'Responsabile IT'",
          c.put(f"{A}/users/{nuovo}/groups/{it}", headers=adm), [204])

    print("\nOperatore (prova.vendite) — nessun accesso all'amministrazione")
    prova("elenca utenti", c.get(f"{A}/users", headers=token("prova.vendite")), [403])

    c.delete(f"{A}/users/{nuovo}", headers=adm)   # pulizia

    print(f"\n{sum(esiti)}/{len(esiti)} passati")
    if not all(esiti):
        sys.exit(1)
    print("Deleghe: nessuno si alza i permessi da solo.")


if __name__ == "__main__":
    main()
