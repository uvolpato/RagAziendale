"""Verifica end-to-end dell'amministrazione, via HTTPS come un browser.

    ./.venv/Scripts/python.exe eval/verifica_amministrazione.py

Richiede l'ambiente avviato (py avvia.py) e i dati di esempio
(py amministrazione/esempio.py). Controlla:
  - dove atterra ciascuno dopo il login (operatore in chat, admin alla scelta)
  - che i permessi li applichi il SERVER, non l'interfaccia
  - i flussi con motivo obbligatorio e il registro
  - Vedi come contro Keycloak con il token dell'amministratore
  - che i file statici non contengano colori scritti a mano ne' residui
Cancella cio' che crea (tranne le righe di registro: quelle non si cancellano).
"""
import pathlib
import re
import socket
import sys

import httpx
import psycopg

QUI = pathlib.Path(__file__).resolve().parent.parent
E = {m.group(1): m.group(2).split("#")[0].strip()
     for m in re.finditer(r"^([A-Z_]+)=(.*)$", (QUI / ".env").read_text(encoding="utf-8"), re.M)}
APP = f"https://{E['APP_HOST']}"
API = f"{APP}/amministrazione/api"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/140.0"}
_o = socket.getaddrinfo
socket.getaddrinfo = lambda h, p, *a, **k: _o(
    "127.0.0.1" if isinstance(h, str) and h.endswith(".localhost") else h, p, *a, **k)
esiti = []


def prova(nome, descrizione):
    def deco(f):
        try:
            f()
            esiti.append(True)
            print(f"  PASS  {nome}  {descrizione}")
        except AssertionError as e:
            esiti.append(False)
            print(f"  FAIL  {nome}  {descrizione}\n        {e}")
        except Exception as e:
            esiti.append(False)
            print(f"  ERR   {nome}  {descrizione}\n        {type(e).__name__}: {e}")
        return f
    return deco


def login(utente, partenza="/"):
    """Login completo dalla radice, seguendo i redirect come un browser.
    Restituisce (client, url finale, percorso)."""
    c = httpx.Client(verify=False, follow_redirects=False, timeout=30, headers=UA)
    url, percorso = APP + partenza, []
    for _ in range(20):
        r = c.get(url)
        if r.status_code in (301, 302, 303):
            url = str(httpx.URL(url).join(r.headers["location"]))
            percorso.append(url)
            continue
        m = re.search(r'id="kc-form-login"[^>]*action="([^"]+)"', r.text)
        if r.status_code == 200 and m:
            r = c.post(m.group(1).replace("&amp;", "&"), data={
                "username": utente, "password": E["TEST_USER_PASSWORD"], "credentialId": ""})
            assert r.status_code in (302, 303), f"credenziali rifiutate per {utente}: {r.status_code}"
            url = str(httpx.URL(url).join(r.headers["location"]))
            percorso.append(url)
            continue
        return c, url, percorso
    raise AssertionError("troppi redirect: " + " -> ".join(p.split("?")[0] for p in percorso[-6:]))


H = {"X-Richiesta": "1"}
db = psycopg.connect(f"postgresql://postgres:{E['POSTGRES_PASSWORD']}@localhost:55432/rag", autocommit=True)

print("\n--- Dove si atterra dopo il login (spec §4) " + "-" * 25)
SESS = {}


@prova("A1", "operatore: dritto in chat, senza pagine intermedie")
def _():
    c, url, perc = login("prova.vendite")
    assert url.startswith(APP + "/c/new"), f"atterrato su {url}"
    assert any("/amministrazione/dopo-login" in p for p in perc), "non e' passato dal portale"
    SESS["operatore"] = c


@prova("A2", "amministratore: la scelta Chat / Amministrazione")
def _():
    c, url, perc = login("prova.admin")
    assert "/amministrazione/#/scelta" in perc[-1], f"ultimo redirect: {perc[-1]}"
    assert c.get(url).status_code == 200
    SESS["admin"] = c


@prova("A3", "la radice non serve pagine a chi non ha sessione (login invariato)")
def _():
    r = httpx.get(APP + "/", verify=False, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/oauth/openid", r.headers.get("location")


@prova("A4", "l'operatore non entra in amministrazione (pagina e API)")
def _():
    c = SESS["operatore"]
    r = c.get(APP + "/amministrazione/")
    assert r.status_code == 302 and r.headers["location"].endswith("/c/new"), r.headers.get("location")
    assert c.get(API + "/io").status_code == 403


print("\n--- Permessi applicati dal server (spec §3.4) " + "-" * 23)


@prova("R1", "Amministratore completo: tutte le voci; console di Keycloak e struttura solo al Superutente")
def _():
    p = SESS["admin"].get(API + "/io").json()["permessi"]
    for v in ("panoramica", "fonti", "vedicome", "anomalie", "registro", "aspetto", "utenti", "gruppi", "profili", "dagster", "uptime"):
        assert v in p, (v, p)
    assert p["fonti"] == "M" and p["aspetto"] == "M" and p["anomalie"] == "M"
    assert "keycloak" not in p and "struttura" not in p, p


@prova("R2", "revisore: tutto in lettura, ogni modifica rifiutata dal server")
def _():
    c = login("prova.revisore", "/amministrazione/")[0]
    SESS["revisore"] = c
    p = c.get(API + "/io").json()["permessi"]
    assert [k for k, v in p.items() if v == "M"] == ["vedicome"], p
    fid = db.execute("SELECT id FROM sources WHERE id LIKE 'esempio-%' LIMIT 1").fetchone()[0]
    aid = db.execute("SELECT id FROM anomalie LIMIT 1").fetchone()[0]
    assert c.post(f"{API}/fonti/{fid}/residenza", headers=H, json={"residenza": "interno"}).status_code == 403
    assert c.post(f"{API}/anomalie/{aid}/azione", headers=H, json={"azione": "prendi"}).status_code == 403
    assert c.put(f"{API}/aspetto", headers=H, json={"palette": {}}).status_code == 403
    assert c.get(f"{API}/registro").status_code == 200 and c.get(f"{API}/fonti").status_code == 200


@prova("R3", "Gestione accessi, solo Luis: niente Dagster/Uptime, fonti in lettura")
def _():
    c = login("prova.accessi", "/amministrazione/")[0]
    io = c.get(API + "/io").json()
    assert io["aziende"] == ["luis"] and not io["tutte_le_aziende"], io
    assert "dagster" not in io["permessi"] and "uptime" not in io["permessi"] and "aspetto" not in io["permessi"]
    assert io["permessi"]["fonti"] == "L"
    assert c.get(API + "/aspetto").status_code == 403
    fonti = c.get(API + "/fonti").json()
    assert fonti and all("luis" in f["aziende"] for f in fonti), "vede fonti di aziende non sue"
    anom = c.get(API + "/anomalie").json()
    assert all(a["sistema"] == "accessi" and a["azienda"] in (None, "luis") for a in anom), "anomalie fuori area"


@prova("R4", "modifica senza header X-Richiesta rifiutata (difesa CSRF)")
def _():
    fid = db.execute("SELECT id FROM sources WHERE id LIKE 'esempio-%' LIMIT 1").fetchone()[0]
    r = SESS["admin"].post(f"{API}/fonti/{fid}/residenza", json={"residenza": "interno"})
    assert r.status_code == 403, r.status_code


print("\n--- Flussi (spec §7.3, §9, §11) " + "-" * 37)


@prova("F1", "'servizi esterni': senza motivo no; con motivo firma, data e registro")
def _():
    c = SESS["admin"]
    fid = "esempio-manuali-tecnici"
    assert c.post(f"{API}/fonti/{fid}/residenza", headers=H, json={"residenza": "cloud_ok"}).status_code == 422
    r = c.post(f"{API}/fonti/{fid}/residenza", headers=H, json={"residenza": "cloud_ok", "motivo": "verifica automatica"})
    assert r.status_code == 200, r.text
    res, da, il = db.execute("SELECT residency, approvato_da, approvato_il FROM sources WHERE id=%s", (fid,)).fetchone()
    assert res == "cloud_ok" and da and il, (res, da, il)
    reg = db.execute("SELECT motivo, prima, dopo FROM registro_modifiche WHERE area='fonti' ORDER BY id DESC LIMIT 1").fetchone()
    assert reg[0] == "verifica automatica" and reg[1] == {"residenza": "interno"}, reg
    assert c.post(f"{API}/fonti/{fid}/residenza", headers=H, json={"residenza": "interno"}).status_code == 200
    assert db.execute("SELECT residency FROM sources WHERE id=%s", (fid,)).fetchone()[0] == "interno"


@prova("F2", "anomalia: prendi, ignora senza motivo no, ignora, commenta, risolvi")
def _():
    c = SESS["admin"]
    aid = db.execute("SELECT segnala_anomalia('verifica-f2','attenzione','qualita','Anomalia di verifica', NULL, 'luis')").fetchone()[0]
    try:
        az = lambda **k: c.post(f"{API}/anomalie/{aid}/azione", headers=H, json=k)
        assert az(azione="prendi").status_code == 200
        assert az(azione="ignora", fino="7g").status_code == 422
        assert az(azione="ignora", fino="7g", testo="nota").status_code == 200
        assert az(azione="commenta", testo="visto").status_code == 200
        assert az(azione="risolvi").status_code == 200
        a = c.get(f"{API}/anomalie/{aid}").json()
        tipi = [e["tipo"] for e in a["eventi"]]
        assert a["stato"] == "risolta" and {"presa", "ignorata", "commento", "risolta"} <= set(tipi), (a["stato"], tipi)
    finally:
        db.execute("DELETE FROM anomalie WHERE id=%s", (aid,))


@prova("F3", "Aspetto: sotto AA non si salva e propone la correzione; il tema la applica")
def _():
    c = SESS["admin"]
    prima = c.get(f"{API}/aspetto").json()["palette"]
    brutta = {**prima, "marchio": "#ffff00", "marchio-testo": "#ffffff"}
    r = c.put(f"{API}/aspetto", headers=H, json={"palette": brutta})
    assert r.status_code == 422 and "Correzione proposta" in r.json()["detail"], r.text
    verde = {**prima, "marchio": "#2e7d32"}
    assert c.put(f"{API}/aspetto", headers=H, json={"palette": verde}).status_code == 200
    assert "--marchio: #2e7d32" in c.get(APP + "/amministrazione/tema.css").text
    assert c.put(f"{API}/aspetto", headers=H, json={"palette": prima}).status_code == 200


@prova("F4", "Vedi come: legge Keycloak col token dell'admin e registra la consultazione")
def _():
    c = SESS["admin"]
    u = c.get(f"{API}/vedi-come/utenti", params={"q": "mario.rossi"}).json()
    assert u and u[0]["username"] == "mario.rossi", u
    n = db.execute("SELECT count(*) FROM registro_modifiche WHERE area='vedi-come'").fetchone()[0]
    d = c.get(f"{API}/vedi-come/utenti/{u[0]['id']}").json()
    vis = {f["id"] for f in d["visibili"]}
    assert "esempio-listini-luis" in vis and "esempio-giacenze-luis" not in vis, vis
    assert d["aziende"] == ["luis"], d["aziende"]
    assert db.execute("SELECT count(*) FROM registro_modifiche WHERE area='vedi-come'").fetchone()[0] == n + 1


@prova("F5", "Vedi come: un operatore riceve 403 (controllo dei ruoli del servizio)")
def _():
    # prova.admin ha admin-ruoli (legge utenti); un operatore non ha la voce
    assert SESS["operatore"].get(f"{API}/vedi-come/utenti").status_code == 403


@prova("F6", "modifica fonte: regole di §7.2 applicate dal server, modifica registrata")
def _():
    c = SESS["admin"]
    fid = "esempio-modulistica"
    orig = db.execute("SELECT acl_groups, aziende, owner, versione_autoritativa, stato FROM sources WHERE id=%s", (fid,)).fetchone()
    base = {"gruppi": ["amministrazione"], "aziende": ["luis"], "owner": "Amministrazione", "versione": "moduli 2026", "stato": "attiva"}
    patch = lambda **k: c.patch(f"{API}/fonti/{fid}", headers=H, json={**base, **k})
    try:
        assert patch(gruppi=[]).status_code == 422, "nessun gruppo accettato"
        assert patch(aziende=[]).status_code == 422, "nessuna azienda accettata"
        assert patch(aziende=["inesistente"]).status_code == 403, "azienda non abilitata accettata"
        assert patch(versione="").status_code == 422, "attiva senza versione accettata"
        assert patch(stato="sospesa").status_code == 422, "sospesa senza motivo accettata"
        assert patch(stato="sospesa", motivo="verifica").status_code == 200
        reg = db.execute("SELECT motivo, dopo FROM registro_modifiche WHERE area='fonti' ORDER BY id DESC LIMIT 1").fetchone()
        assert reg[0] == "verifica" and reg[1].get("stato") == "sospesa", reg
        r = SESS["revisore"].patch(f"{API}/fonti/{fid}", headers=H, json=base)
        assert r.status_code == 403, "il revisore ha modificato una fonte"
    finally:
        db.execute("UPDATE sources SET acl_groups=%s, aziende=%s, owner=%s, versione_autoritativa=%s, stato=%s WHERE id=%s",
                   (*orig, fid))


print("\n--- Accessi dal pannello (decisione 68) " + "-" * 29)
TEC = httpx.Client(verify=False, timeout=30)
TEC.headers["Authorization"] = "Bearer " + TEC.post(
    f"https://{E['SSO_HOST']}/realms/master/protocol/openid-connect/token",
    data={"grant_type": "password", "client_id": "admin-cli", "username": "admin",
          "password": E["KEYCLOAK_ADMIN_PASSWORD"]}).json()["access_token"]
KA = f"https://{E['SSO_HOST']}/admin/realms/azienda"
NUOVO = {}


@prova("X1", "Gestione accessi (solo Luis): crea un operatore Luis, non uno Decobrands")
def _():
    c = login("prova.accessi", "/amministrazione/")[0]
    SESS["accessi"] = c
    r = c.post(f"{API}/utenti", headers=H, json={"username": "verifica.deco", "aziende": ["decobrands"]})
    assert r.status_code == 403, f"azienda non sua accettata: {r.status_code}"
    r = c.post(f"{API}/utenti", headers=H, json={"username": "verifica.pannello", "nome_proprio": "Verifica",
                                                 "cognome": "Pannello", "aziende": ["luis"], "gruppi": ["vendite"]})
    assert r.status_code == 200 and r.json()["password_temporanea"], r.text
    NUOVO["id"] = r.json()["id"]
    elenco = c.get(f"{API}/utenti").json()
    assert all(not u["aziende"] or "luis" in u["aziende"] for u in elenco), "vede persone di altre aziende"
    assert c.post(f"{API}/utenti/{NUOVO['id']}/password", headers=H).status_code == 200


@prova("X2", "Gestione accessi: un profilo passato dal pannello lo rifiuta KEYCLOAK")
def _():
    c = SESS["accessi"]
    r = c.post(f"{API}/utenti/{NUOVO['id']}/gruppi", headers=H,
               json={"aggiungi": ["/magazzino", "/amministratori/Responsabile IT"], "togli": []})
    assert r.status_code == 403, f"profilo assegnato da Gestione accessi: {r.status_code}"
    capo = TEC.get(f"{KA}/users", params={"username": "prova.admin", "exact": "true"}).json()[0]["id"]
    r = c.post(f"{API}/utenti/{capo}/password", headers=H)
    assert r.status_code == 403, f"password di un amministratore reimpostata: {r.status_code}"


@prova("X3", "Amministratore completo: assegna un profilo, non Superutente")
def _():
    c = SESS["admin"]
    assert c.post(f"{API}/utenti/{NUOVO['id']}/gruppi", headers=H,
                  json={"aggiungi": ["/amministratori/Responsabile IT"], "togli": []}).status_code == 200
    r = c.post(f"{API}/utenti/{NUOVO['id']}/gruppi", headers=H,
               json={"aggiungi": ["/amministratori/Superutente"], "togli": []})
    assert r.status_code == 403, f"Superutente assegnato: {r.status_code}"
    assert c.post(f"{API}/gruppi-operativi", headers=H, json={"nome": "verifica"}).status_code == 403


@prova("X4", "revisore: vede persone e profili, non crea nulla")
def _():
    c = SESS["revisore"]
    assert c.get(f"{API}/utenti").status_code == 200 and c.get(f"{API}/profili").status_code == 200
    assert c.post(f"{API}/utenti", headers=H, json={"username": "verifica.rev", "aziende": ["luis"]}).status_code == 403
    # Keycloak 26.7: la lettura "di tutti gli utenti" non apre /users/{id}; il
    # pannello usa solo letture di elenco. Senza, qui il Revisore prendeva 403.
    uid = next(u["id"] for u in c.get(f"{API}/utenti").json() if u["username"] == "mario.rossi")
    assert c.get(f"{API}/utenti/{uid}").status_code == 200
    assert c.get(f"{API}/vedi-come/utenti/{uid}").status_code == 200


@prova("X5", "Superutente: gruppo creato, rinominato (fonti aggiornate), eliminato; profilo ricomposto")
def _():
    c = login("prova.super", "/amministrazione/")[0]
    assert c.post(f"{API}/gruppi-operativi", headers=H, json={"nome": "verifica-a"}).status_code == 200
    gid = next(g["id"] for g in c.get(f"{API}/gruppi-operativi").json() if g["nome"] == "verifica-a")
    db.execute("UPDATE sources SET acl_groups = acl_groups || '{verifica-a}' WHERE id = 'esempio-modulistica'")
    try:
        assert c.put(f"{API}/gruppi-operativi/{gid}", headers=H, json={"nome": "verifica-b"}).status_code == 200
        g = db.execute("SELECT acl_groups FROM sources WHERE id = 'esempio-modulistica'").fetchone()[0]
        assert "verifica-b" in g and "verifica-a" not in g, g
        assert c.delete(f"{API}/gruppi-operativi/{gid}", headers=H).status_code == 422, "eliminato un gruppo usato"
    finally:
        db.execute("UPDATE sources SET acl_groups = array_remove(array_remove(acl_groups, 'verifica-b'), 'verifica-a')"
                   " WHERE id = 'esempio-modulistica'")
    assert c.delete(f"{API}/gruppi-operativi/{gid}", headers=H).status_code == 200
    prof = c.get(f"{API}/profili").json()["profili"]
    it = next(p for p in prof if p["nome"] == "Responsabile IT")
    assert c.put(f"{API}/profili/{it['id']}/ruoli", headers=H, json={"ruoli": it["ruoli"] + ["admin-fonti"]}).status_code == 200
    assert "admin-fonti" in next(p for p in c.get(f"{API}/profili").json()["profili"] if p["nome"] == "Responsabile IT")["ruoli"]
    assert c.put(f"{API}/profili/{it['id']}/ruoli", headers=H, json={"ruoli": it["ruoli"]}).status_code == 200


@prova("X6", "logout dalla sessione SSO: il pannello non resta aperto a meta'")
def _():
    c = SESS["accessi"]
    uid = TEC.get(f"{KA}/users", params={"username": "prova.accessi", "exact": "true"}).json()[0]["id"]
    TEC.post(f"{KA}/users/{uid}/logout")          # come "Esci" nella console di Keycloak
    r = c.get(f"{API}/utenti")
    assert r.status_code == 401, f"atteso 401 dopo il logout SSO, ottenuto {r.status_code}"
    assert c.get(f"{API}/io").status_code == 401, "la sessione del pannello e' sopravvissuta"


@prova("Z1", "Aziende: le crea solo il Superutente, insieme al gruppo di abilitazione")
def _():
    sup = login("prova.super", "/amministrazione/")[0]
    try:
        nuova = {"codice": "verifica-az", "ragione_sociale": "Verifica S.r.l.", "connettore": "integra", "codice_origine": "009"}
        assert SESS["admin"].post(f"{API}/aziende-gestione", headers=H, json=nuova).status_code == 403
        assert sup.post(f"{API}/aziende-gestione", headers=H, json={**nuova, "codice": "Non Valido!"}).status_code == 422
        assert sup.post(f"{API}/aziende-gestione", headers=H, json=nuova).status_code == 200
        assert TEC.get(f"{KA}/group-by-path/azienda-verifica-az").status_code == 200, "gruppo non creato"
        assert sup.post(f"{API}/aziende-gestione", headers=H, json=nuova).status_code == 409
        r = sup.put(f"{API}/aziende-gestione/verifica-az", headers=H, json={**nuova, "attiva": False})
        assert r.status_code == 422, "disattivata senza motivo"
        assert sup.put(f"{API}/aziende-gestione/verifica-az", headers=H,
                       json={**nuova, "attiva": False, "motivo": "verifica"}).status_code == 200
        assert db.execute("SELECT attiva FROM aziende WHERE codice='verifica-az'").fetchone()[0] is False
        az = sup.get(f"{API}/aziende-gestione").json()["aziende"]
        assert any(a["codice"] == "verifica-az" and a["gruppo_presente"] for a in az), az
    finally:
        g = TEC.get(f"{KA}/group-by-path/azienda-verifica-az")
        if g.status_code == 200:
            TEC.delete(f"{KA}/groups/{g.json()['id']}")
        db.execute("DELETE FROM aziende WHERE codice = 'verifica-az'")     # solo nel test: il pannello non elimina


if NUOVO.get("id"):
    TEC.delete(f"{KA}/users/{NUOVO['id']}")          # pulizia


print("\n--- Interfaccia (spec §2, §13) " + "-" * 38)
STATIC = QUI / "amministrazione" / "static"


@prova("U1", "nessun colore scritto a mano fuori dai token (CSS e stili inline)")
def _():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", css), "colori in app.css"
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    inline = re.findall(r"style=\"([^\"]*)\"", js) + re.findall(r"style=\\?'([^']*)'", js)
    brutti = [s for s in inline if re.search(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", s)]
    assert not brutti, brutti[:3]


@prova("U2", "niente residui del prototipo o dello strumento di design")
def _():
    for f in STATIC.iterdir():
        t = f.read_text(encoding="utf-8")
        for residuo in ("data-od-id", "Lorem", "Ripeti la demo", "Solo prototipo", "proto-box"):
            assert residuo not in t, (f.name, residuo)


@prova("U3", "tabelle con intestazioni vere e righe raggiungibili da tastiera")
def _():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert js.count('<th scope="col"') >= 20 and "tr.tabIndex = 0" in js and "e.key === 'Enter'" in js


@prova("U4", "la pagina dichiara i token, il tema salvato e la lingua italiana")
def _():
    h = SESS["admin"].get(APP + "/amministrazione/").text
    assert '<html lang="it">' in h and "tokens.css" in h and "/amministrazione/tema.css" in h


print(f"\n{sum(esiti)}/{len(esiti)} passati")
sys.exit(0 if all(esiti) else 1)
