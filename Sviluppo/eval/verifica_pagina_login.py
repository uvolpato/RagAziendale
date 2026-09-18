"""Verifica la pagina di accesso resa da Keycloak: struttura, i18n, errori.

    ./.venv/Scripts/python.exe eval/verifica_pagina_login.py

Controlla che la resa corrisponda al prototipo Prototipi/login-sso-keycloak.html
senza rompere Keycloak:
  - gli elementi del prototipo ci sono (logo, titolo, sottotitolo, nota)
  - i campi di Keycloak sono intatti: nomi, ordine, tabindex
  - niente residui del prototipo (data-od-id, JS di simulazione)
  - i testi vengono da messages_*.properties in it/en/de
  - credenziali errate -> alert con il messaggio di Keycloak
  - credenziali corrette -> redirect al callback con il codice
"""
import pathlib
import re
import socket
import sys
import urllib.parse

import httpx

QUI = pathlib.Path(__file__).parent.parent
SSO = "https://sso.localhost"
APP = "https://assistente.localhost"
REALM = "azienda"

_o = socket.getaddrinfo
socket.getaddrinfo = lambda h, p, *a, **k: _o(
    "127.0.0.1" if isinstance(h, str) and h.endswith(".localhost") else h, p, *a, **k)

esiti = []


def da_env(chiave):
    for riga in (QUI / ".env").read_text(encoding="utf-8").splitlines():
        m = re.match(rf"^{chiave}=(.*)$", riga)
        if m:
            return m.group(1).split("#")[0].strip()
    raise SystemExit(f"{chiave} assente da .env")


def prova(nome, descrizione):
    def deco(f):
        try:
            f()
            esiti.append((nome, True, ""))
            print(f"  PASS  {nome}  {descrizione}")
        except AssertionError as e:
            esiti.append((nome, False, str(e)))
            print(f"  FAIL  {nome}  {descrizione}\n        {e}")
        except Exception as e:
            esiti.append((nome, False, f"{type(e).__name__}: {e}"))
            print(f"  ERR   {nome}  {descrizione}\n        {type(e).__name__}: {e}")
        return f
    return deco


def url_auth(locale=None):
    p = {
        "client_id": "librechat",
        "response_type": "code",
        "scope": "openid",
        "redirect_uri": f"{APP}/oauth/openid/callback",
        "state": "verifica",
    }
    if locale:
        p["ui_locales"] = locale
    return (f"{SSO}/realms/{REALM}/protocol/openid-connect/auth?"
            + urllib.parse.urlencode(p))


def pagina(c, locale=None):
    r = c.get(url_auth(locale))
    assert r.status_code == 200, f"HTTP {r.status_code}"
    return r.text


def azione(html):
    m = re.search(r'id="kc-form-login"[^>]*action="([^"]+)"', html)
    assert m, "form kc-form-login senza action"
    return m.group(1).replace("&amp;", "&")


def main():
    pw = da_env("TEST_USER_PASSWORD")
    cl = dict(verify=False, follow_redirects=False, timeout=30.0)

    print("\n--- Struttura del prototipo " + "-" * 40)
    with httpx.Client(**cl) as c:
        html = pagina(c, "it")

        @prova("P1", "elementi del prototipo presenti")
        def _():
            for ident in ["az-logo", "az-titolo", "az-sottotitolo", "az-nota"]:
                assert f'id="{ident}"' in html, f"manca #{ident}"

        @prova("P2", "campi di Keycloak intatti: nomi e ordine")
        def _():
            for nome in ['name="username"', 'name="password"',
                         'name="rememberMe"', 'name="credentialId"',
                         'name="login"']:
                assert nome in html, f"manca {nome}"
            # L'ordine conta per autofill e navigazione a tab.
            assert html.index('name="username"') < html.index('name="password"') \
                < html.index('name="rememberMe"'), "ordine dei campi alterato"

        @prova("P3", "tabindex 2..7 preservati")
        def _():
            for t in ['tabindex="2"', 'tabindex="3"', 'tabindex="4"',
                      'tabindex="5"', 'tabindex="6"', 'tabindex="7"']:
                assert t in html, f"manca {t}"

        @prova("P4", "nessun residuo del prototipo")
        def _():
            for residuo in ["data-od-id", "panel-success", "reset-demo",
                            "Ripeti la demo", "Prototipo dimostrativo",
                            "login-sso-screen"]:
                assert residuo not in html, f"residuo del prototipo: {residuo}"

        @prova("P5", "i due fogli di stile, il nostro DOPO quello di Keycloak")
        def _():
            fogli = re.findall(r'href="([^"]*login/azienda/css/[^"]+)"', html)
            nomi = [f.rsplit("/", 1)[-1] for f in fogli]
            assert nomi == ["login.css", "tema-azienda.css"], \
                f"fogli inattesi o in ordine sbagliato: {nomi}"
            for f in fogli:
                r = c.get(SSO + f if f.startswith("/") else f)
                assert r.status_code == 200, f"{f} -> HTTP {r.status_code}"

        @prova("P6", "il logo e un PNG valido (firma dei byte)")
        def _():
            m = re.search(r'href="([^"]*login/azienda/)css/', html)
            assert m, "percorso risorse non trovato"
            r = c.get(SSO + m.group(1) + "img/logo.png")
            assert r.status_code == 200, f"HTTP {r.status_code}"
            # PNG e  89 50 4E 47 0D 0A 1A 0A ; un doppio trattino nei commenti
            # non influisce qui, ma un logo non-PNG fa fallire la firma.
            assert r.content.startswith(b"\x89PNG\r\n\x1a\n"), \
                f"non e un PNG: {r.content[:8]!r}"

    print("\n--- Multilingua " + "-" * 52)
    attesi = {
        "it": ("Accedi al tuo account aziendale", "Area riservata"),
        "en": ("Sign in to your company account", "Restricted area"),
        "de": ("Bei Ihrem Unternehmenskonto anmelden", "Geschützter Bereich"),
    }
    for lingua, (titolo, sotto) in attesi.items():
        @prova(f"P7-{lingua}", f"testi da messages_{lingua}.properties")
        def _(lingua=lingua, titolo=titolo, sotto=sotto):
            with httpx.Client(**cl) as c:
                h = pagina(c, lingua)
            assert f'lang="{lingua}"' in h, f"html lang non e {lingua}"
            assert titolo in h, f"titolo assente: {titolo!r}"
            assert sotto in h, f"sottotitolo assente: {sotto!r}"

    print("\n--- Comportamento " + "-" * 50)

    @prova("P8", "credenziali errate -> alert, nessun codice emesso")
    def _():
        with httpx.Client(**cl) as c:
            html = pagina(c, "it")
            r = c.post(azione(html), data={
                "username": "prova.vendite", "password": "password-sbagliata",
                "credentialId": ""})
            assert r.status_code == 200, \
                f"atteso il rendering della pagina, ottenuto {r.status_code}"
            assert "code=" not in r.headers.get("location", ""), \
                "codice emesso con credenziali errate"
            assert "pf-c-alert" in r.text or "kc-feedback-text" in r.text, \
                "nessun alert nella pagina di errore"
            assert 'aria-invalid="true"' in r.text, \
                "i campi non sono marcati non validi"
            assert "input-error" in r.text or "alert-error" in r.text, \
                "manca il messaggio d'errore per campo"

    @prova("P9", "credenziali corrette -> redirect al callback con il codice")
    def _():
        with httpx.Client(**cl) as c:
            html = pagina(c, "it")
            r = c.post(azione(html), data={
                "username": "prova.vendite", "password": pw,
                "credentialId": ""})
            assert r.status_code in (302, 303), \
                f"atteso redirect, ottenuto {r.status_code}"
            dove = r.headers.get("location", "")
            assert "/oauth/openid/callback" in dove, f"redirect inatteso: {dove}"
            assert "code=" in dove, f"nessun codice: {dove}"

    @prova("P10", "rememberMe abilitato sul realm, altrimenti la casella non esiste")
    def _():
        with httpx.Client(**cl) as c:
            html = pagina(c, "it")
        assert 'id="rememberMe"' in html, \
            "realm.rememberMe disattivato: Keycloak non rende la casella"

    print("\n" + "=" * 70)
    falliti = [e for e in esiti if not e[1]]
    print(f"{len(esiti) - len(falliti)}/{len(esiti)} passati")
    if falliti:
        for nome, _ok, err in falliti:
            print(f"  FAIL {nome}: {err}")
        sys.exit(1)
    print("Pagina di accesso: conforme al prototipo, Keycloak intatto.")


if __name__ == "__main__":
    main()
