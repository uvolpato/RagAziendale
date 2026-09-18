"""T1.4 — il login SSO completo, fatto da uno script invece che a mano.

Percorre tutto il giro: LibreChat -> Keycloak -> form -> callback -> sessione.
Se si rompe, dice DOVE si rompe, invece di lasciare un "non funziona".

    ./.venv/Scripts/python.exe eval/verifica_login.py [utente]
"""
import pathlib
import re
import socket
import sys

import httpx

QUI = pathlib.Path(__file__).parent.parent
APP = "https://assistente.localhost"

# I browser risolvono *.localhost da soli (RFC 6761), il resolver di Windows
# no: Python otterrebbe "getaddrinfo failed" su un indirizzo che nel browser
# funziona. Si risolve qui, cosi il test non dipende dal file hosts.
_originale = socket.getaddrinfo


def _getaddrinfo(host, porta, *a, **kw):
    if isinstance(host, str) and host.endswith(".localhost"):
        host = "127.0.0.1"
    return _originale(host, porta, *a, **kw)


socket.getaddrinfo = _getaddrinfo


def da_env(chiave):
    for riga in (QUI / ".env").read_text(encoding="utf-8").splitlines():
        m = re.match(rf"^{chiave}=(.*)$", riga)
        if m:
            return m.group(1).split("#")[0].strip()
    raise SystemExit(f"{chiave} assente da .env")


def passo(n, cosa):
    print(f"{n}. {cosa}", end=" ... ", flush=True)


def main():
    utente = sys.argv[1] if len(sys.argv) > 1 else "prova.vendite"
    password = da_env("TEST_USER_PASSWORD")

    # verify=False: la CA di Caddy e' interna. In produzione si verifica.
    with httpx.Client(verify=False, follow_redirects=False, timeout=30.0) as c:

        passo(1, "radice senza sessione: nessuna pagina servita")
        # 302, non 200: se LibreChat servisse la shell, il browser
        # disegnerebbe la schermata di benvenuto prima di accorgersi di non
        # avere sessione.
        r = c.get(f"{APP}/")
        assert r.status_code in (301, 302, 303), \
            f"la radice ha servito una pagina (HTTP {r.status_code}): lampeggia"
        assert "/oauth/openid" in r.headers.get("location", ""), \
            f"redirect inatteso: {r.headers.get('location')}"
        print("ok (302 -> /oauth/openid)")

        passo(2, "bottone SSO configurato")
        cfg = c.get(f"{APP}/api/config").json()
        assert cfg.get("openidLoginEnabled") is True, "openidLoginEnabled non attivo"
        print(f'ok ("{cfg.get("openidLabel")}")')

        passo(3, "redirect verso Keycloak")
        r = c.get(f"{APP}/oauth/openid")
        assert r.status_code in (302, 303), f"atteso 302, ottenuto {r.status_code}"
        auth_url = r.headers["location"]
        assert "sso.localhost" in auth_url, f"redirect inatteso: {auth_url}"
        print("ok")

        passo(4, "pagina di login Keycloak")
        r = c.get(auth_url)
        assert r.status_code == 200, f"HTTP {r.status_code}"
        m = re.search(r'action="([^"]+)"', r.text)
        assert m, "form di login non trovato nella pagina Keycloak"
        action = m.group(1).replace("&amp;", "&")
        print("ok")

        passo(5, f"invio credenziali ({utente})")
        r = c.post(action, data={"username": utente, "password": password,
                                 "credentialId": ""})
        if r.status_code == 200 and "Invalid username or password" in r.text:
            raise SystemExit("credenziali rifiutate da Keycloak")
        assert r.status_code in (302, 303), f"atteso redirect, ottenuto {r.status_code}"
        callback = r.headers["location"]
        assert "/oauth/openid/callback" in callback, f"redirect inatteso: {callback}"
        print("ok")

        passo(6, "callback: atterraggio su /c/new, non sulla radice")
        # Caddy riscrive la Location del callback. Se atterrasse su /,
        # il redirect incondizionato sulla radice rimanderebbe a Keycloak,
        # che ha ormai la sessione, e la catena non terminerebbe mai.
        r = c.get(callback)
        assert r.status_code in (302, 303), f"atteso redirect, ottenuto {r.status_code}"
        dove = r.headers.get("location", "")
        assert dove.rstrip("/").endswith("/c/new"), (
            f"atterraggio su {dove!r} invece che su /c/new: con il redirect "
            "incondizionato sulla radice questo produce un CICLO"
        )
        print(f"ok (-> {dove})")

        passo(7, "scambio del cookie di refresh con il token")
        # Il callback lascia un cookie di refresh, non un token utilizzabile:
        # il client SPA lo scambia con un access token via /api/auth/refresh e
        # solo dopo chiama le API con Authorization: Bearer. Chiamare
        # /api/user col solo cookie da "No auth token", che sembra un login
        # fallito e non lo e'.
        r = c.post(f"{APP}/api/auth/refresh")
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:160]}"
        token = r.json().get("token")
        assert token, f"nessun token nella risposta: {r.text[:160]}"
        print("ok")

        passo(8, "sessione attiva")
        r = c.get(f"{APP}/api/user", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:120]}"
        u = r.json()
        print("ok")

        print(f"\n   utente in LibreChat : {u.get('username') or u.get('email')}")
        print(f"   provider            : {u.get('provider')}")
        print(f"   ruolo               : {u.get('role')}")

        # --- Logout: la parte che sembrava non funzionare ------------------
        # Con OPENID_AUTO_REDIRECT attivo, chiudere solo la sessione di
        # LibreChat non basta: quella SSO di Keycloak resta aperta, il
        # redirect automatico torna a Keycloak, che emette un nuovo codice
        # senza chiedere nulla, e l'utente rientra in chat. Sembra che
        # "disconnetti" non faccia niente.
        print()
        passo(9, "logout")
        r = c.post(f"{APP}/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code in (200, 302, 303), f"HTTP {r.status_code}"
        corpo = r.text[:200]
        print("ok")

        passo(10, "sessione LibreChat chiusa")
        r = c.get(f"{APP}/api/user", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code in (401, 403), \
            f"la sessione e' ancora valida (HTTP {r.status_code})"
        print("ok")

        passo(11, "/login intercettato da Caddy, non servito da LibreChat")
        # Il browser, dopo il logout, naviga su /login. Caddy lo intercetta e
        # manda all'end_session_endpoint: e' cosi' che la pagina non lampeggia
        # e la sessione SSO viene chiusa davvero.
        r = c.get(f"{APP}/login")
        assert r.status_code in (301, 302, 303), \
            f"LibreChat ha servito /login (HTTP {r.status_code}): la pagina lampeggia"
        logout_sso = r.headers["location"]
        assert "openid-connect/logout" in logout_sso, \
            f"redirect inatteso: {logout_sso}"
        print("ok (-> end_session_endpoint)")

        passo(12, "sessione SSO di Keycloak chiusa")
        r = c.get(logout_sso)                       # Keycloak chiude la sessione
        while r.status_code in (301, 302, 303):     # e rimanda a /oauth/openid
            r = c.get(r.headers["location"])
            if "/callback" in str(r.headers.get("location", "")):
                raise AssertionError(
                    "Keycloak ha emesso un nuovo codice senza chiedere "
                    "credenziali: la sessione SSO NON e' stata chiusa."
                )
        assert r.status_code == 200 and 'action="' in r.text, \
            f"atteso il form di login, ottenuto HTTP {r.status_code}: {r.text[:120]}"
        print("ok (richiede di nuovo le credenziali)")

        if corpo and "redirect" in corpo.lower():
            print(f"\n   risposta del logout : {corpo[:120]}")

    # --- La verifica che conta di piu': la catena TERMINA ------------------
    # Un ciclo di redirect non si vede in nessuno dei passi precedenti: ogni
    # singolo salto e' corretto, e' la sequenza che non finisce. Qui si
    # seguono i redirect con un tetto e si controlla dove si atterra.
    print()
    passo(13, "visita alla radice da autenticato: la catena termina")
    with httpx.Client(verify=False, follow_redirects=False, timeout=30.0) as c:
        # login
        r = c.get(f"{APP}/oauth/openid")
        r = c.get(r.headers["location"])
        action = re.search(r'action="([^"]+)"', r.text).group(1).replace("&amp;", "&")
        r = c.post(action, data={"username": utente, "password": password,
                                 "credentialId": ""})
        c.get(r.headers["location"])

        # ora, da autenticato, si digita il dominio nudo
        url, salti, percorso = f"{APP}/", 0, []
        while salti < 12:
            r = c.get(url)
            if r.status_code not in (301, 302, 303):
                break
            url = str(httpx.URL(url).join(r.headers["location"]))
            percorso.append(httpx.URL(url).path or "/")
            salti += 1
        else:
            raise AssertionError(
                "CICLO: 12 redirect senza atterrare. Percorso: "
                + " -> ".join(percorso)
            )
        assert r.status_code == 200, f"atterrato su HTTP {r.status_code}"
        atterrato = httpx.URL(url).path
        assert atterrato.rstrip("/").endswith("/c/new"), \
            f"atterrato su {atterrato!r} invece di /c/new"
        print(f"ok ({salti} salti -> {atterrato}, nessuna pagina intermedia)")

    print("\nT1.4: verde. Radice senza pagina intermedia, login e logout completi.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"FALLITO\n\n  -> {e}")
        sys.exit(1)
