"""T1.1 / T1.3 — verifica che i token del realm abbiano la forma attesa.

Controlla, per ogni utente di prova:
  - il token viene emesso
  - contiene il claim `groups` con i gruppi giusti
  - `aud` include `orchestratore`  <- senza audience mapper l'orchestratore
                                      rifiuta tutto, ed e' l'errore che costa
                                      mezza giornata
Solo stdlib.
"""
import base64
import json
import pathlib
import re
import urllib.parse
import urllib.request

KEYCLOAK = "http://localhost:8081"
REALM = "azienda"

ATTESI = {
    "prova.magazzino": {"tutti", "magazzino", "azienda-luis"},
    "prova.vendite": {"tutti", "vendite", "azienda-luis"},
    "prova.direzione": {"tutti", "vendite", "amministrazione", "direzione", "azienda-luis"},
}


def env(chiave):
    f = pathlib.Path(__file__).parent.parent / ".env"
    for riga in f.read_text(encoding="utf-8").splitlines():
        m = re.match(rf"^{chiave}=(.*)$", riga)
        if m:
            return m.group(1).split("#")[0].strip()
    raise SystemExit(f"{chiave} assente da .env")


def token(utente, password):
    dati = urllib.parse.urlencode({
        "grant_type": "password",
        "client_id": "test-token",
        "username": utente,
        "password": password,
    }).encode()
    url = f"{KEYCLOAK}/realms/{REALM}/protocol/openid-connect/token"
    try:
        with urllib.request.urlopen(url, data=dati, timeout=20) as r:
            return json.load(r)["access_token"]
    except urllib.error.HTTPError as e:
        print(f"      {e.code}: {e.read()[:160].decode(errors='replace')}")
        return None


def payload(jwt):
    p = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


def main():
    pw = env("TEST_USER_PASSWORD")
    esiti = []

    for utente, gruppi_attesi in ATTESI.items():
        print(f"\n{utente}")
        jwt = token(utente, pw)
        if not jwt:
            esiti.append((utente, False, "nessun token"))
            continue
        d = payload(jwt)
        gruppi = set(d.get("groups") or [])
        aud = d.get("aud")
        aud = set(aud if isinstance(aud, list) else [aud] if aud else [])

        ok_gruppi = gruppi == gruppi_attesi
        ok_aud = "orchestratore" in aud

        print(f"   iss    = {d.get('iss')}")
        print(f"   groups = {sorted(gruppi)}  {'OK' if ok_gruppi else 'ATTESI ' + str(sorted(gruppi_attesi))}")
        print(f"   aud    = {sorted(aud)}  {'OK' if ok_aud else 'MANCA orchestratore'}")
        print(f"   durata = {d.get('exp', 0) - d.get('iat', 0)}s")

        esiti.append((utente, ok_gruppi and ok_aud,
                      "" if ok_gruppi and ok_aud else "claim non conformi"))

    print("\n" + "=" * 50)
    falliti = [e for e in esiti if not e[1]]
    for u, ok, nota in esiti:
        print(f"  {'PASS' if ok else 'FAIL'}  {u} {nota}")
    if falliti:
        raise SystemExit(f"\n{len(falliti)} verifiche fallite")
    print("\nT1.1 e T1.3: verdi.")


if __name__ == "__main__":
    main()
