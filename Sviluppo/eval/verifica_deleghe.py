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
    master = c.post(f"{SSO}/realms/master/protocol/openid-connect/token", data={
        "grant_type": "password", "client_id": "admin-cli", "username": "admin",
        "password": E["KEYCLOAK_ADMIN_PASSWORD"]}).json()["access_token"]
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

    prova("crea un gruppo", c.post(f"{A}/groups", headers=acc, json={"name": "verifica-gruppo"}), [403])

    tecnico = {"Authorization": f"Bearer {master}"}
    super_ = token("prova.super")
    superutente = id_gruppo(adm, "/amministratori/Superutente")
    capo_super = id_utente(adm, "prova.super")

    print("\nAmministratore completo (prova.admin) — assegna i profili, ma non Superutente")
    prova("mette l'operatore in 'Responsabile IT'",
          c.put(f"{A}/users/{nuovo}/groups/{it}", headers=adm), [204])
    prova("mette l'operatore in 'Superutente'",
          c.put(f"{A}/users/{nuovo}/groups/{superutente}", headers=adm), [403])
    prova("si mette in 'Superutente'",
          c.put(f"{A}/users/{capo}/groups/{superutente}", headers=adm), [403])
    r = c.put(f"{A}/users/{io}/reset-password", headers=adm,
              json={"type": "password", "value": "Presa-123!", "temporary": False})
    if r.status_code < 300:
        c.put(f"{A}/users/{io}/reset-password", headers=tecnico,
              json={"type": "password", "value": E["TEST_USER_PASSWORD"], "temporary": False})
    prova("reimposta la password di un altro amministratore", r, [403])
    prova("disattiva il Superutente",
          c.put(f"{A}/users/{capo_super}", headers=adm, json={"enabled": False}), [403])
    prova("crea un gruppo", c.post(f"{A}/groups", headers=adm, json={"name": "verifica-gruppo"}), [403])

    print("\nRevisore (prova.revisore) — legge, non modifica")
    rev = token("prova.revisore")
    prova("elenca utenti", c.get(f"{A}/users", headers=rev), [200])
    prova("crea un utente", c.post(f"{A}/users", headers=rev,
          json={"username": "verifica.revisore", "enabled": True}), [403])

    print("\nSuperutente (prova.super) — tutto")
    prova("mette l'operatore in 'Superutente'",
          c.put(f"{A}/users/{nuovo}/groups/{superutente}", headers=super_), [204])
    prova("lo toglie da 'Superutente'",
          c.delete(f"{A}/users/{nuovo}/groups/{superutente}", headers=super_), [204])
    r = c.post(f"{A}/groups", headers=super_, json={"name": "verifica-gruppo"})
    prova("crea un gruppo", r, [201])
    for g in c.get(f"{A}/groups", params={"search": "verifica-gruppo", "exact": "true"}, headers=tecnico).json():
        c.delete(f"{A}/groups/{g['id']}", headers=tecnico)

    print("\nGestore di gruppo (prova.rspp, in sicurezza-gestori) — solo il suo gruppo")
    rspp = token("prova.rspp")
    sicurezza = id_gruppo(adm, "/sicurezza")
    # Un operatore qualunque: "nuovo" ora ha un profilo di amministrazione, e
    # gli account degli amministratori non li tocca nessun delegato (sopra).
    collega = id_utente(adm, "prova.vendite")
    magazzino = id_gruppo(adm, "/magazzino")
    gestori_sic = id_gruppo(adm, "/sicurezza-gestori")
    prova("elenca le persone", c.get(f"{A}/users", headers=rspp), [200])
    prova("vede chi c'e' in 'sicurezza'", c.get(f"{A}/groups/{sicurezza}/members", headers=rspp), [200])
    prova("aggiunge un collega a 'sicurezza'", c.put(f"{A}/users/{collega}/groups/{sicurezza}", headers=rspp), [204])
    prova("lo toglie da 'sicurezza'", c.delete(f"{A}/users/{collega}/groups/{sicurezza}", headers=rspp), [204])
    prova("lo mette in 'magazzino' (non e' il suo gruppo)",
          c.put(f"{A}/users/{collega}/groups/{magazzino}", headers=rspp), [403])
    prova("nomina un altro gestore", c.put(f"{A}/users/{collega}/groups/{gestori_sic}", headers=rspp), [403])
    prova("si aggiunge a 'Amministratore completo'",
          c.put(f"{A}/users/{id_utente(adm, 'prova.rspp')}/groups/{completo}", headers=rspp), [403])
    prova("vede i membri di 'vendite'", c.get(f"{A}/groups/{vendite}/members", headers=rspp), [403])
    prova("crea un utente", c.post(f"{A}/users", headers=rspp, json={"username": "verifica.rspp", "enabled": True}), [403])
    prova("un addetto (prova.sicurezza, non gestore) aggiunge un collega",
          c.put(f"{A}/users/{collega}/groups/{sicurezza}", headers=token("prova.sicurezza")), [403])
    prova("Gestione accessi gestisce ancora 'sicurezza'",
          c.put(f"{A}/users/{collega}/groups/{sicurezza}", headers=acc), [204])
    prova("il Revisore vede ancora i membri di 'sicurezza'",
          c.get(f"{A}/groups/{sicurezza}/members", headers=rev), [200])
    c.delete(f"{A}/users/{collega}/groups/{sicurezza}", headers=tecnico)

    print("\nOperatore (prova.vendite) — nessun accesso all'amministrazione")
    prova("elenca utenti", c.get(f"{A}/users", headers=token("prova.vendite")), [403])

    print("\nRegola: i ruoli di amministrazione si danno SOLO con i profili")
    cid = c.get(f"{A}/clients", params={"clientId": "amministrazione"}, headers=tecnico).json()[0]["id"]
    diretti = [f"{u['username']}:{r['name']}"
               for r in c.get(f"{A}/clients/{cid}/roles", headers=tecnico).json()
               for u in c.get(f"{A}/clients/{cid}/roles/{r['name']}/users", headers=tecnico).json()]
    # /roles/{r}/users elenca anche chi lo riceve da un gruppo? No: solo le
    # assegnazioni dirette. I divieti sui profili non proteggono queste.
    esiti.append(not diretti)
    print(f"  {'PASS' if not diretti else 'FAIL'}  nessun ruolo admin-* assegnato direttamente"
          + ("" if not diretti else f": {diretti}"))

    # Pulizia con l'account tecnico: un delegato, giustamente, non puo'
    # cancellare un utente che e' diventato membro di un profilo.
    c.delete(f"{A}/users/{nuovo}", headers=tecnico)

    print(f"\n{sum(esiti)}/{len(esiti)} passati")
    if not all(esiti):
        sys.exit(1)
    print("Deleghe: nessuno si alza i permessi da solo.")


if __name__ == "__main__":
    main()
