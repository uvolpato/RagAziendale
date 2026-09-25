"""Servizio di amministrazione (decisione 58): portale dopo il login e
schermate di SPECIFICA-INTERFACCE-AMMINISTRAZIONE.md.

    uvicorn amministrazione.app:app      (nel container: vedi Dockerfile)

Un'interfaccia sola per tutti gli amministratori, superutente compreso
(decisione 68). Utenti, gruppi e profili si gestiscono QUI, con le funzioni di
Keycloak chiamate col token di chi lavora; importazioni (Dagster) e
monitoraggio (Uptime Kuma) restano collegamenti (decisione 60).

Sicurezza:
- Login OIDC con PKCE sul client pubblico `amministrazione`: il servizio non
  custodisce segreti. Sessione in memoria, chiave casuale per processo: dopo
  un riavvio l'SSO di Keycloak fa rientrare senza chiedere nulla.
- I permessi (logica.MATRICE) si applicano QUI, a ogni API. Il client li
  riceve solo per nascondere le voci.
- Vedi come legge Keycloak con il token dell'amministratore stesso: vede solo
  cio' che Keycloak gia' gli concede. Nessun service account.
- Richieste che modificano: header X-Richiesta obbligatorio (una richiesta da
  un altro sito non puo' aggiungerlo senza preflight CORS) + cookie SameSite.
"""
import base64
import csv
import hashlib
import io
import json
import os
import pathlib
import secrets
import sqlite3
import time
import urllib.parse

import httpx
import jwt
import psycopg
from psycopg.rows import dict_row
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from jwt import PyJWKClient

from amministrazione import gestori, logica

QUI = pathlib.Path(__file__).parent
BASE = "/amministrazione"
APP_HOST = os.environ["APP_HOST"]
SSO_HOST = os.environ["SSO_HOST"]
REALM = os.environ.get("REALM", "azienda")
KC_INTERNO = os.environ.get("KEYCLOAK_INTERNO", "http://keycloak:8080")
CLIENT_ID = "amministrazione"
EMITTENTE = f"https://{SSO_HOST}/realms/{REALM}"            # iss: hostname ESTERNO
CALLBACK = f"https://{APP_HOST}{BASE}/oidc/callback"
CHAT = f"https://{APP_HOST}/c/new"
STRUMENTI = {   # URL vuoto = non ancora installato: la pagina segnaposto lo dice
    "keycloak": (f"https://{SSO_HOST}/admin/{REALM}/console/", "Configurazione avanzata", "Keycloak"),
    "dagster": (os.environ.get("DAGSTER_URL", ""), "Gestione importazioni", "Dagster"),
    "uptime": (os.environ.get("UPTIME_URL", ""), "Monitoraggio sistemi", "Uptime Kuma"),
}
# Il servizio `connettori` custodisce i segreti dei collegamenti (decisione 70):
# il pannello gli INOLTRA le password digitate dal Superutente, non ha la chiave.
CONNETTORI_URL = os.environ.get("CONNETTORI_URL", "http://connettori:8000")
CONNETTORI_KEY = os.environ.get("CONNETTORI_KEY", "")
# Servizio azioni (riavvio/stop dei servizi): il pannello gli inoltra la
# richiesta, non tocca mai il socket di Docker.
AZIONI_URL = os.environ.get("AZIONI_URL", "http://azioni:8000")
AZIONI_KEY = os.environ.get("AZIONI_KEY", "")

# Stato dei sistemi da Uptime Kuma: il suo SQLite e' montato in sola lettura.
KUMADB = "/opt/uptime-kuma/kuma.db"
STATI_KUMA = {0: "down", 1: "up", 2: "pending"}


def _stato_sistemi():
    """Semaforo dei sistemi letto da Uptime Kuma (heartbeat + monitor).

    Uptime Kuma non ha una REST API pulita (solo Socket.io): qui si legge
    direttamente il suo SQLite in sola lettura. In caso di errore si dichiara
    «sconosciuto» invece di fingere che vada tutto bene.
    """
    try:
        con = sqlite3.connect(f"file:{KUMADB}?mode=ro", uri=True, timeout=2)
        ultimi = con.execute(
            "SELECT m.id, m.name, m.type, h.status, h.ping FROM heartbeat h"
            " JOIN monitor m ON m.id = h.monitor_id"
            " WHERE m.active = 1 AND h.id IN (SELECT max(id) FROM heartbeat GROUP BY monitor_id)"
            " ORDER BY m.name").fetchall()
        # Ultimi N heartbeat per monitor, per disegnare la barra dei ping (come
        # in Uptime Kuma): dal piu' vecchio al piu' recente.
        recenti = con.execute(
            "SELECT monitor_id, status FROM ("
            "  SELECT monitor_id, status, ROW_NUMBER() OVER (PARTITION BY monitor_id ORDER BY id DESC) AS rn"
            "  FROM heartbeat WHERE monitor_id IN (SELECT id FROM monitor WHERE active = 1)"
            ") x WHERE rn <= 30 ORDER BY monitor_id, rn DESC").fetchall()
        con.close()
        barre = {}
        for mid, status in recenti:
            barre.setdefault(mid, []).append(status)
        monitor = [{"nome": r[1], "tipo": r[2], "stato": STATI_KUMA.get(r[3], "down"),
                    "ping": r[4], "barra": barre.get(r[0], [])} for r in ultimi]
        non_verdi = [m for m in monitor if m["stato"] != "up"]
        giu = any(m["stato"] == "down" for m in monitor)
        semaforo = "critico" if giu else ("attenzione" if non_verdi else "ok")
        return {"semaforo": semaforo, "monitor": monitor, "non_verdi": non_verdi}
    except Exception as e:
        return {"semaforo": "sconosciuto", "monitor": [], "errore": str(e)}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount(f"{BASE}/static", StaticFiles(directory=QUI / "static"), name="static")

# ponytail: sessioni in memoria, un processo solo. Con piu' repliche -> tabella.
SESSIONI: dict[str, dict] = {}
IN_CORSO: dict[str, dict] = {}      # login avviati: state -> verifier, destinazione
DURATA = 10 * 3600                  # come ssoSessionMaxLifespan
_jwks = PyJWKClient(f"{KC_INTERNO}/realms/{REALM}/protocol/openid-connect/certs", cache_keys=True)


def db():
    # ponytail: una connessione per richiesta; pool quando il traffico lo chiede.
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


# ------------------------------------------------------------------- OIDC
def _claim(access_token):
    chiave = _jwks.get_signing_key_from_jwt(access_token)
    c = jwt.decode(access_token, chiave.key, algorithms=["RS256"], issuer=EMITTENTE,
                   options={"verify_aud": False, "require": ["exp", "iat", "sub", "iss"]})
    # aud non e' affidabile per un client pubblico; azp si': il token deve
    # essere stato emesso PER questo client, non riusato da un altro.
    if c.get("azp") != CLIENT_ID:
        raise jwt.InvalidTokenError("token emesso per un altro client")
    return c


def _nuova_sessione(tok):
    c = _claim(tok["access_token"])
    gruppi = [str(g) for g in c.get("groups") or []]
    ruoli = [r for r in c.get("resource_access", {}).get(CLIENT_ID, {}).get("roles", []) if r in logica.RUOLI]
    with db() as conn:
        tutte = [r["codice"] for r in conn.execute("SELECT codice FROM aziende WHERE attiva ORDER BY codice")]
    aziende = [a for a in logica.aziende_da_gruppi(gruppi) if a in tutte] if tutte else []
    if "admin-super" in ruoli:
        aziende = list(tutte)                       # il Superutente vede tutte le aziende
    s = {
        "sub": c["sub"], "username": c.get("preferred_username", ""), "nome": c.get("name") or c.get("preferred_username", ""),
        "gruppi": gruppi, "ruoli": ruoli, "aziende": aziende, "tutte_le_aziende": set(tutte) <= set(aziende),
        "access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
        "scade_token": c["exp"], "scade": time.time() + DURATA,
    }
    s["permessi"] = logica.permessi(ruoli, s["tutte_le_aziende"])
    sid = secrets.token_urlsafe(32)
    SESSIONI[sid] = s
    return sid, s


def _sessione(request: Request):
    s = SESSIONI.get(request.cookies.get("amm_sessione", ""))
    if not s or s["scade"] < time.time():
        return None
    return s


def _token_valido(s):
    """Access token utilizzabile verso Keycloak; lo rinnova se sta scadendo."""
    if s["scade_token"] - 30 > time.time():
        return s["access_token"]
    r = httpx.post(f"{KC_INTERNO}/realms/{REALM}/protocol/openid-connect/token", data={
        "grant_type": "refresh_token", "client_id": CLIENT_ID, "refresh_token": s["refresh_token"]}, timeout=15)
    if r.status_code != 200:
        # La sessione SSO e' finita (logout altrove, scadenza): il pannello non
        # deve restare aperto a meta', con ogni chiamata a Keycloak che fallisce.
        s["scade"] = 0
        raise HTTPException(401, "sessione scaduta: ricaricare la pagina")
    tok = r.json()
    s.update(access_token=tok["access_token"], refresh_token=tok.get("refresh_token", s["refresh_token"]),
             scade_token=_claim(tok["access_token"])["exp"])
    return s["access_token"]


def _vai_al_login(destinazione):
    stato, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    sfida = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    IN_CORSO[stato] = {"verifier": verifier, "dopo": destinazione, "creato": time.time()}
    for k in [k for k, v in IN_CORSO.items() if v["creato"] < time.time() - 600]:
        IN_CORSO.pop(k, None)
    url = f"https://{SSO_HOST}/realms/{REALM}/protocol/openid-connect/auth?" + urllib.parse.urlencode({
        "client_id": CLIENT_ID, "response_type": "code", "scope": "openid",
        "redirect_uri": CALLBACK, "state": stato,
        "code_challenge": sfida, "code_challenge_method": "S256"})
    r = RedirectResponse(url, 302)
    # Lega il login a QUESTO browser: senza, qualcuno potrebbe far completare
    # a un altro il proprio login (login CSRF).
    r.set_cookie("amm_stato", stato, max_age=600, httponly=True, secure=True, samesite="lax", path=BASE)
    return r


def _destinazione(s, request):
    """Dove va chi ha appena fatto login (spec §4)."""
    if not s["ruoli"]:
        return CHAT                                     # operatore: dritto in chat
    ricordata = request.cookies.get("amm_scelta")
    if ricordata == "chat":
        return CHAT
    if ricordata == "amministrazione":
        return f"{BASE}/#/{logica.home(s['permessi'])}"
    return f"{BASE}/#/scelta"


@app.get(f"{BASE}/dopo-login")
def dopo_login(request: Request):
    """Dove Caddy manda il callback della chat. Nessuna pagina: solo 302.
    Riautentica SEMPRE: il cookie amm_sessione può riferirsi a un login SSO
    precedente (utente cambiato). Con SSO ancora attivo Keycloak risponde
    subito, senza schermata."""
    return _vai_al_login("dopo-login")


@app.get(f"{BASE}/oidc/callback")
def callback(request: Request, code: str = "", state: str = ""):
    atteso = IN_CORSO.pop(state, None)
    if not code or not atteso or request.cookies.get("amm_stato") != state:
        return PlainTextResponse("Accesso non valido o scaduto: ricaricare la pagina.", 400)
    r = httpx.post(f"{KC_INTERNO}/realms/{REALM}/protocol/openid-connect/token", data={
        "grant_type": "authorization_code", "client_id": CLIENT_ID, "code": code,
        "redirect_uri": CALLBACK, "code_verifier": atteso["verifier"]}, timeout=15)
    if r.status_code != 200:
        return PlainTextResponse("Accesso rifiutato da Keycloak.", 400)
    sid, s = _nuova_sessione(r.json())
    dest = _destinazione(s, request) if atteso["dopo"] == "dopo-login" else f"{BASE}/{atteso['dopo']}"
    if not s["ruoli"]:
        dest = CHAT
    risp = RedirectResponse(dest, 302)
    risp.set_cookie("amm_sessione", sid, max_age=DURATA, httponly=True, secure=True, samesite="lax", path=BASE)
    risp.delete_cookie("amm_stato", path=BASE)
    return risp


@app.get(f"{BASE}/esci")
def esci(request: Request):
    SESSIONI.pop(request.cookies.get("amm_sessione", ""), None)
    # /login e' intercettato da Caddy e passa dall'end_session di Keycloak:
    # si chiude anche la sessione SSO, non solo la nostra.
    r = RedirectResponse(f"https://{APP_HOST}/login", 302)
    r.delete_cookie("amm_sessione", path=BASE)
    return r


@app.get(f"{BASE}/")
@app.get(BASE)
def pagina(request: Request):
    s = _sessione(request)
    if not s:
        return _vai_al_login("")
    if not s["ruoli"]:
        return RedirectResponse(CHAT, 302)
    return FileResponse(QUI / "static" / "index.html", headers={"Cache-Control": "no-store"})


@app.get(f"{BASE}/scelta")
def vai_alla_scelta(request: Request):
    """Il link «Torna alla scelta» dalla chat: mostra la scelta ANCHE se
    l'utente ha ricordato «chat» (amm_scelta). La preferenza ricordata vale
    solo al login normale, non quando si chiede esplicitamente la scelta."""
    s = _sessione(request)
    if not s:
        return _vai_al_login("scelta")
    if not s["ruoli"]:
        return RedirectResponse(CHAT, 302)        # operatore: nessuna scelta
    return RedirectResponse(f"{BASE}/#/scelta", 302)


@app.get(f"{BASE}/tema.css")
def tema():
    """I 5 token configurabili dalla palette salvata. Tutto il resto deriva."""
    with db() as conn:
        p = conn.execute("SELECT palette FROM aspetto WHERE id = 1").fetchone()["palette"]
    righe = "".join(f"  --{k}: {p[k]};\n" for k in logica.CONFIGURABILI if k in p)
    # Il tema scuro ridefinisce sfondo e superficie: la palette salvata vale
    # per il chiaro, il marchio vale per entrambi.
    scuro = "".join(f"  --{k}: {p[k]};\n" for k in ("marchio", "marchio-secondario", "marchio-testo") if k in p)
    css = (f":root {{\n{righe}}}\nhtml[data-tema=\"scuro\"] {{\n{scuro}}}\n"
           f"@media (prefers-color-scheme: dark) {{ html:not([data-tema]) {{\n{scuro}}} }}\n")
    return Response(css, media_type="text/css", headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------- dipendenze API
def utente(request: Request):
    s = _sessione(request)
    if not s:
        raise HTTPException(401, "sessione assente o scaduta")
    if not s["ruoli"]:
        raise HTTPException(403, "nessun ruolo di amministrazione")
    if request.method not in ("GET", "HEAD") and request.headers.get("X-Richiesta") != "1":
        raise HTTPException(403, "richiesta non valida")
    return s


def serve(s, voce, livello="L"):
    """Solleva 403 se il permesso manca. livello 'M' = modifica."""
    p = s["permessi"].get(voce)
    if p is None or (livello == "M" and p != "M"):
        raise HTTPException(403, f"richiede il permesso {livello} su {voce}")


def registra(conn, s, area, azione, oggetto=None, azienda=None, motivo=None, prima=None, dopo=None):
    conn.execute(
        "INSERT INTO registro_modifiche (chi, chi_nome, area, azione, oggetto, azienda, motivo, prima, dopo)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (s["username"], s["nome"], area, azione, oggetto, azienda, motivo,
         json.dumps(prima) if prima is not None else None, json.dumps(dopo) if dopo is not None else None))


def _visibile_per_azienda(s, azienda):
    """Oggetto senza azienda: visibile a tutti. Altrimenti serve l'azienda."""
    return azienda is None or azienda in s["aziende"]


# --------------------------------------------------------------------- API
@app.get(f"{BASE}/api/io")
def io_(s=Depends(utente)):
    return {"username": s["username"], "nome": s["nome"], "aziende": s["aziende"],
            "tutte_le_aziende": s["tutte_le_aziende"], "permessi": s["permessi"],
            "ruoli": [logica.NOMI_RUOLI[r] for r in s["ruoli"]],
            "strumenti": {k: {"url": v[0], "nome": v[1], "prodotto": v[2]} for k, v in STRUMENTI.items()
                          if k in s["permessi"]},
            "chat": CHAT, "assistente": os.environ.get("NOME_ASSISTENTE", "Assistente aziendale"),
            "home": logica.home(s["permessi"])}


@app.post(f"{BASE}/api/scelta")
async def scelta(request: Request, s=Depends(utente)):
    v = (await request.json()).get("scelta")
    r = JSONResponse({"ok": True})
    if v in ("chat", "amministrazione"):
        r.set_cookie("amm_scelta", v, max_age=365 * 86400, httponly=True, secure=True, samesite="lax", path=BASE)
    else:
        r.delete_cookie("amm_scelta", path=BASE)
    return r


@app.get(f"{BASE}/api/aziende")
def aziende(s=Depends(utente)):
    with db() as conn:
        return [r for r in conn.execute(
            "SELECT codice, ragione_sociale FROM aziende WHERE attiva AND codice = ANY(%s) ORDER BY ragione_sociale",
            (s["aziende"],))]


def _fonti(conn, s):
    righe = conn.execute("""
        SELECT s.*,
               (SELECT count(DISTINCT documento) FROM chunks c WHERE c.source_id = s.id) AS documenti,
               (SELECT count(DISTINCT (documento, page)) FROM chunks c WHERE c.source_id = s.id) AS pagine,
               (SELECT max(updated_at) FROM chunks c WHERE c.source_id = s.id) AS aggiornata,
               (SELECT count(*) FROM documenti d WHERE d.source_id = s.id AND d.in_lettura IS NOT NULL) AS in_lettura
          FROM sources s
         WHERE s.aziende && %s::text[] OR s.aziende = '{}'
         ORDER BY s.descrizione""", (s["aziende"],)).fetchall()
    for f in righe:
        # Modificabile solo da chi ha TUTTE le aziende della fonte (§3.3).
        f["modificabile"] = s["permessi"].get("fonti") == "M" and set(f["aziende"]) <= set(s["aziende"])
    return righe


@app.get(f"{BASE}/api/fonti")
def fonti(s=Depends(utente)):
    serve(s, "fonti")
    with db() as conn:
        return _fonti(conn, s)


@app.get(f"{BASE}/api/fonti/{{fid}}/documenti")
def documenti_fonte(fid: str, s=Depends(utente)):
    """Cosa l'indicizzazione ha trovato nella fonte, file per file: serve a
    chi approva per vedere cosa entra PRIMA di attivarla. Solo metadati e
    stato, mai il contenuto: il testo si legge dalla chat, attraverso il gate."""
    serve(s, "fonti")
    with db() as conn:
        f = conn.execute("SELECT aziende, provenienza FROM sources WHERE id = %s", (fid,)).fetchone()
        if not f:
            raise HTTPException(404, "fonte inesistente")
        if f["aziende"] and not set(f["aziende"]) & set(s["aziende"]):
            raise HTTPException(403, "la fonte riguarda aziende a cui non sei abilitato")
        righe = conn.execute("""
            SELECT d.documento, d.stato, d.errore, d.pezzi, d.dimensione, d.modificato_il, d.indicizzato_il,
                   d.in_lettura, d.pagine_fatte, d.pagine_totali,
                   d.figure_fatte, d.figure_totali,
                   (SELECT count(*) FROM chunks c WHERE c.source_id = d.source_id AND c.documento = d.documento
                     AND c.embedding IS NULL) AS senza_vettori,
                   (SELECT count(*) FROM immagini i WHERE i.source_id = d.source_id AND i.documento = d.documento
                     AND i.embedding IS NULL) AS senza_vettori_immagini
              FROM documenti d WHERE d.source_id = %s ORDER BY d.documento""", (fid,)).fetchall()
        doppi = {r["documento"] for r in conn.execute("""
            SELECT unnest(array_agg(documento)) AS documento FROM documenti WHERE source_id = %s
             GROUP BY impronta HAVING count(*) > 1""", (fid,))}
    for r in righe:
        r["doppione"] = r["documento"] in doppi
    return {"provenienza": f["provenienza"], "documenti": righe}


@app.post(f"{BASE}/api/fonti/{{fid}}/documenti/rielabora")
async def rielabora_documento(fid: str, request: Request, s=Depends(utente)):
    """Rileggere un documento DA SUBITO: la riga sparisce da `documenti` e il
    prossimo giro di ingestion lo tratta come nuovo (prima non si rileggeva se
    impronta e data non cambiavano). Si registra nel registro delle modifiche."""
    serve(s, "fonti", "M")
    corpo = await request.json()
    documento = (corpo.get("documento") or "").strip()
    if not documento:
        raise HTTPException(422, "documento mancante")
    with db() as conn:
        f = conn.execute("SELECT aziende FROM sources WHERE id = %s", (fid,)).fetchone()
        if not f:
            raise HTTPException(404, "fonte inesistente")
        if f["aziende"] and not set(f["aziende"]) & set(s["aziende"]):
            raise HTTPException(403, "la fonte riguarda aziende a cui non sei abilitato")
        with conn.transaction():
            conn.execute("DELETE FROM chunks WHERE source_id = %s AND documento = %s", (fid, documento))
            conn.execute("DELETE FROM immagini WHERE source_id = %s AND documento = %s", (fid, documento))
            cur = conn.execute("DELETE FROM documenti WHERE source_id = %s AND documento = %s RETURNING documento",
                               (fid, documento))
            if not cur.fetchone():
                raise HTTPException(404, "documento inesistente")
        registra(conn, s, "fonti", "rielabora documento", oggetto=documento,
                 azienda=f["aziende"][0] if len(f["aziende"]) == 1 else None)
    return {"ok": True}


@app.post(f"{BASE}/api/fonti/{{fid}}/residenza")
async def residenza(fid: str, request: Request, s=Depends(utente)):
    """§7.3: verso 'servizi esterni' e' un'approvazione (motivo obbligatorio,
    firma e data); il ritorno a 'resta in azienda' e' immediato ma registrato."""
    serve(s, "fonti", "M")
    corpo = await request.json()
    voluta, motivo = corpo.get("residenza"), (corpo.get("motivo") or "").strip()
    if voluta not in ("interno", "cloud_ok"):
        raise HTTPException(422, "residenza non valida")
    if voluta == "cloud_ok" and not motivo:
        raise HTTPException(422, "il motivo e' obbligatorio")
    with db() as conn:
        f = conn.execute("SELECT * FROM sources WHERE id = %s FOR UPDATE", (fid,)).fetchone()
        if not f:
            raise HTTPException(404, "fonte inesistente")
        if not set(f["aziende"]) <= set(s["aziende"]):
            raise HTTPException(403, "la fonte riguarda aziende a cui non sei abilitato")
        if voluta == "cloud_ok":
            conn.execute("UPDATE sources SET residency='cloud_ok', approvato_da=%s, approvato_il=now() WHERE id=%s",
                         (s["nome"] or s["username"], fid))
            azione = f"Ha reso la fonte «{f['descrizione']}» utilizzabile da servizi esterni"
        else:
            conn.execute("UPDATE sources SET residency='interno' WHERE id=%s", (fid,))
            azione = f"Ha riportato la fonte «{f['descrizione']}» a «Resta in azienda»"
        registra(conn, s, "fonti", azione, f["descrizione"], ",".join(f["aziende"]) or None, motivo or None,
                 {"residenza": f["residency"]}, {"residenza": voluta})
    return {"ok": True}


@app.get(f"{BASE}/api/fonti/tipi")
def tipi_fonte(s=Depends(utente)):
    serve(s, "fonti")
    return logica.TIPI_FONTE


@app.post(f"{BASE}/api/fonti")
async def nuova_fonte(request: Request, s=Depends(utente)):
    """Nuova fonte. Oggi solo il tipo cartella (decisioni 61-64 e 71, una
    cartella per gruppo); gli altri tipi del catalogo arrivano con il loro
    connettore. Nasce 'in attesa': si indicizza subito, ma l'assistente la usa
    solo quando qualcuno la attiva dopo averla controllata."""
    serve(s, "fonti", "M")
    c = await request.json()
    tipo = c.get("provenienza") or "cartella"
    if tipo not in logica.TIPI_DISPONIBILI:
        raise HTTPException(422, f"il tipo di fonte «{tipo}» non e' ancora disponibile")
    gruppo = (c.get("gruppo") or "").strip()
    fid = (c.get("id") or "").strip().lower()
    descrizione = (c.get("descrizione") or "").strip()
    percorso = logica.percorso_cartella(c.get("percorso"))
    aziende_n = sorted({str(a) for a in c.get("aziende", []) if a})
    if not logica.CODICE_AZIENDA.match(fid):
        raise HTTPException(422, "codice non valido: minuscole, cifre e trattini (es. «sicurezza-luis»)")
    if not descrizione:
        raise HTTPException(422, "il nome e' obbligatorio")
    if not percorso:
        raise HTTPException(422, "percorso non valido: relativo alla radice delle cartelle, es. «luis/sicurezza»")
    if not aziende_n:
        raise HTTPException(422, "serve almeno un'azienda: nessun valore predefinito (decisione 53)")
    if not set(aziende_n) <= set(s["aziende"]):
        raise HTTPException(403, "puoi assegnare solo aziende a cui sei abilitato")
    esistenti = {g["name"] for g in _kc(s, "/groups", max=1000, briefRepresentation="true")}
    if gruppo not in esistenti or gruppo.startswith("azienda-") or gruppo == RADICE_PROFILI:
        raise HTTPException(422, "gruppo inesistente: si crea prima in Accessi, Gruppi")
    lettori, owner = [gruppo], "da-assegnare"
    if gruppo + gestori.SUFFISSO in esistenti:
        # I gestori vedono la cartella che gestiscono, e ne rispondono.
        lettori.append(gruppo + gestori.SUFFISSO)
        owner = gruppo + gestori.SUFFISSO
    with db() as conn:
        if conn.execute("SELECT 1 FROM sources WHERE id = %s", (fid,)).fetchone():
            raise HTTPException(409, "esiste gia' una fonte con questo codice")
        if conn.execute("SELECT 1 FROM sources WHERE provenienza = 'cartella' AND percorso = %s", (percorso,)).fetchone():
            raise HTTPException(409, "questa cartella e' gia' collegata a un'altra fonte")
        conn.execute("""INSERT INTO sources (id, descrizione, percorso, acl_groups, owner, versione_autoritativa,
                                             tipo, provenienza, stato, aziende)
                        VALUES (%s, %s, %s, %s, %s, %s, 'documenti', 'cartella', 'attesa', %s)""",
                     (fid, descrizione, percorso, lettori, owner,
                      "la cartella: solo versioni valide, _bozze e _archivio esclusi (decisione 64)", aziende_n))
        registra(conn, s, "fonti", f"Ha collegato la cartella «{descrizione}»", descrizione, ",".join(aziende_n),
                 dopo={"percorso": percorso, "gruppi": lettori, "responsabile": owner})
    return {"ok": True, "id": fid, "gruppi": lettori, "responsabile": owner}


@app.get(f"{BASE}/api/gruppi")
def gruppi(s=Depends(utente)):
    """Gruppi operativi assegnabili a una fonte: da Keycloak, esclusi quelli
    di azienda e i profili di amministrazione (non sono gruppi di lettura)."""
    serve(s, "fonti")
    nomi = []
    for g in _kc(s, "/groups", max=500, briefRepresentation="true"):
        if not g["name"].startswith("azienda-") and g["name"] != "amministratori":
            nomi.append(g["name"])
    return sorted(nomi)


@app.patch(f"{BASE}/api/fonti/{{fid}}")
async def modifica_fonte(fid: str, request: Request, s=Depends(utente)):
    """§7.2: chi la vede, aziende, responsabile, versione, stato.
    La residenza ha il suo flusso (§7.3) e non passa di qui."""
    serve(s, "fonti", "M")
    c = await request.json()
    gruppi_n = sorted({str(g) for g in c.get("gruppi", []) if g})
    aziende_n = sorted({str(a) for a in c.get("aziende", []) if a})
    owner = (c.get("owner") or "").strip()
    versione = (c.get("versione") or "").strip() or None
    stato = c.get("stato")
    motivo = (c.get("motivo") or "").strip() or None
    if not gruppi_n:
        raise HTTPException(422, "serve almeno un gruppo: una fonte che nessuno vede non ha senso")
    if not aziende_n:
        raise HTTPException(422, "serve almeno un'azienda: nessun valore predefinito (decisione 53)")
    if not set(aziende_n) <= set(s["aziende"]):
        raise HTTPException(403, "puoi assegnare solo aziende a cui sei abilitato")
    if stato not in ("attiva", "attesa", "sospesa"):
        raise HTTPException(422, "stato non valido")
    if stato == "attiva" and (not owner or owner == "da-assegnare" or not versione):
        raise HTTPException(422, "per attivare una fonte servono responsabile e versione di riferimento")
    if stato == "sospesa" and not motivo:
        raise HTTPException(422, "il motivo e' obbligatorio per sospendere una fonte")
    with db() as conn:
        f = conn.execute("SELECT * FROM sources WHERE id = %s FOR UPDATE", (fid,)).fetchone()
        if not f:
            raise HTTPException(404, "fonte inesistente")
        if not set(f["aziende"]) <= set(s["aziende"]):
            raise HTTPException(403, "la fonte riguarda aziende a cui non sei abilitato")
        prima = {"gruppi": f["acl_groups"], "aziende": f["aziende"], "responsabile": f["owner"],
                 "versione": f["versione_autoritativa"], "stato": f["stato"]}
        dopo = {"gruppi": gruppi_n, "aziende": aziende_n, "responsabile": owner or "da-assegnare",
                "versione": versione, "stato": stato}
        if prima == dopo:
            return {"ok": True}
        conn.execute("UPDATE sources SET acl_groups=%s, aziende=%s, owner=%s, versione_autoritativa=%s, stato=%s WHERE id=%s",
                     (gruppi_n, aziende_n, dopo["responsabile"], versione, stato, fid))
        cosa = [k for k in dopo if dopo[k] != prima[k]]
        registra(conn, s, "fonti", f"Ha modificato la fonte «{f['descrizione']}» ({', '.join(cosa)})",
                 f["descrizione"], ",".join(sorted(set(f["aziende"]) | set(aziende_n))) or None, motivo,
                 {k: prima[k] for k in cosa}, {k: dopo[k] for k in cosa})
    return {"ok": True}


def _filtro_anomalie(s):
    aree = logica.aree_anomalie(s["ruoli"])
    return aree, "(azienda IS NULL OR azienda = ANY(%(aziende)s)) AND (%(tutte)s OR sistema = ANY(%(aree)s))"


@app.get(f"{BASE}/api/anomalie")
def anomalie(s=Depends(utente)):
    serve(s, "anomalie")
    aree, dove = _filtro_anomalie(s)
    with db() as conn:
        return conn.execute(f"SELECT * FROM anomalie WHERE {dove} ORDER BY ultima DESC",
                            {"aziende": s["aziende"], "tutte": aree is None, "aree": list(aree or [])}).fetchall()


@app.get(f"{BASE}/api/anomalie/{{aid}}")
def anomalia(aid: int, s=Depends(utente)):
    serve(s, "anomalie")
    aree, dove = _filtro_anomalie(s)
    with db() as conn:
        a = conn.execute(f"SELECT * FROM anomalie WHERE id = %(id)s AND {dove}",
                         {"id": aid, "aziende": s["aziende"], "tutte": aree is None, "aree": list(aree or [])}).fetchone()
        if not a:
            raise HTTPException(404, "anomalia inesistente o fuori dalle tue aree")
        a["eventi"] = conn.execute("SELECT * FROM anomalie_eventi WHERE anomalia_id = %s ORDER BY quando DESC, id DESC",
                                   (aid,)).fetchall()
        return a


FINO = {"1g": "1 day", "7g": "7 days", "30g": "30 days", "sempre": None}


@app.post(f"{BASE}/api/anomalie/{{aid}}/azione")
async def azione_anomalia(aid: int, request: Request, s=Depends(utente)):
    serve(s, "anomalie", "M")
    c = await request.json()
    tipo, testo = c.get("azione"), (c.get("testo") or "").strip()
    with db() as conn:
        a = conn.execute("SELECT * FROM anomalie WHERE id = %s FOR UPDATE", (aid,)).fetchone()
        if not a or not _visibile_per_azienda(s, a["azienda"]):
            raise HTTPException(404, "anomalia inesistente")
        ev = lambda t, x=None: conn.execute(
            "INSERT INTO anomalie_eventi (anomalia_id, chi, tipo, testo) VALUES (%s,%s,%s,%s)", (aid, s["username"], t, x))
        if tipo == "prendi":
            conn.execute("UPDATE anomalie SET stato='presa', assegnata_a=%s WHERE id=%s", (s["username"], aid))
            ev("presa")
            frase = "Ha preso in carico"
        elif tipo == "assegna":
            a_chi = (c.get("a") or "").strip()
            if not a_chi:
                raise HTTPException(422, "a chi?")
            conn.execute("UPDATE anomalie SET stato='presa', assegnata_a=%s WHERE id=%s", (a_chi, aid))
            ev("assegnata", a_chi)
            frase = f"Ha assegnato a {a_chi}"
        elif tipo == "commenta":
            if not testo:
                raise HTTPException(422, "commento vuoto")
            ev("commento", testo)
            return {"ok": True}                         # un commento non e' una modifica
        elif tipo == "risolvi":
            conn.execute("UPDATE anomalie SET stato='risolta', risolta_auto=false, ignorata_fino=NULL WHERE id=%s", (aid,))
            ev("risolta", testo or None)
            frase = "Ha risolto"
        elif tipo == "ignora":
            if not testo:
                raise HTTPException(422, "il motivo e' obbligatorio")
            if c.get("fino") not in FINO:
                raise HTTPException(422, "fino a quando?")
            intervallo = FINO[c["fino"]]
            conn.execute("UPDATE anomalie SET stato='ignorata', ignorata_fino = CASE WHEN %s::interval IS NULL"
                         " THEN NULL ELSE now() + %s::interval END WHERE id=%s", (intervallo, intervallo, aid))
            ev("ignorata", testo)
            frase = "Ha ignorato"
        else:
            raise HTTPException(422, "azione sconosciuta")
        registra(conn, s, "anomalie", f"{frase}: {a['titolo']}", a["titolo"], a["azienda"], testo or None,
                 {"stato": a["stato"], "assegnata_a": a["assegnata_a"]}, None)
    return {"ok": True}


@app.get(f"{BASE}/api/assegnabili")
def assegnabili(s=Depends(utente)):
    """Chi ha usato l'amministrazione con il ruolo anomalie: si ricava dal
    registro e dalle anomalie, senza chiedere a Keycloak diritti in piu'."""
    serve(s, "anomalie", "M")
    with db() as conn:
        return [r["chi"] for r in conn.execute(
            "SELECT DISTINCT chi FROM (SELECT chi FROM registro_modifiche WHERE area='anomalie'"
            " UNION SELECT assegnata_a FROM anomalie WHERE assegnata_a IS NOT NULL UNION SELECT %s) x"
            " WHERE chi IS NOT NULL ORDER BY chi", (s["username"],))]


def _registro(conn, s, limite=None):
    aree = logica.aree_registro(s["ruoli"])
    q = ("SELECT * FROM registro_modifiche WHERE (azienda IS NULL OR string_to_array(azienda, ',') && %(aziende)s)"
         " AND (%(tutte)s OR area = ANY(%(aree)s)) ORDER BY quando DESC, id DESC")
    if limite:
        q += f" LIMIT {int(limite)}"
    return conn.execute(q, {"aziende": s["aziende"], "tutte": aree is None, "aree": list(aree or [])}).fetchall()


@app.get(f"{BASE}/api/registro")
def registro(s=Depends(utente)):
    serve(s, "registro")
    with db() as conn:
        return _registro(conn, s)


@app.get(f"{BASE}/api/registro.csv")
def registro_csv(s=Depends(utente)):
    serve(s, "registro")
    with db() as conn:
        righe = _registro(conn, s)
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["quando", "chi", "area", "azione", "oggetto", "azienda", "motivo", "prima", "dopo"])
    for r in righe:
        w.writerow([r["quando"].isoformat(), r["chi_nome"] or r["chi"], r["area"], r["azione"], r["oggetto"] or "",
                    r["azienda"] or "", r["motivo"] or "", json.dumps(r["prima"]) if r["prima"] else "",
                    json.dumps(r["dopo"]) if r["dopo"] else ""])
    # BOM: Excel italiano apre l'UTF-8 correttamente solo con il BOM.
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="registro-modifiche.csv"'})


@app.get(f"{BASE}/api/panoramica")
def panoramica(s=Depends(utente)):
    serve(s, "panoramica")
    aree, dove = _filtro_anomalie(s)
    par = {"aziende": s["aziende"], "tutte": aree is None, "aree": list(aree or [])}
    with db() as conn:
        out = {"anomalie": None, "importazioni": None, "fonti": None, "sistemi": _stato_sistemi(),
               "modifiche": _registro(conn, s, 5)}
        if "anomalie" in s["permessi"]:
            out["anomalie"] = {
                "conteggi": conn.execute(f"SELECT gravita, count(*) n FROM anomalie WHERE stato IN ('aperta','presa')"
                                         f" AND {dove} GROUP BY gravita", par).fetchall(),
                "nuove_24h": conn.execute(f"SELECT count(*) n FROM anomalie WHERE prima > now() - interval '24 hours'"
                                          f" AND {dove}", par).fetchone()["n"],
                "piu_gravi": conn.execute(
                    f"SELECT * FROM anomalie WHERE stato IN ('aperta','presa') AND {dove} ORDER BY"
                    " array_position(ARRAY['critico','errore','attenzione','info'], gravita), ultima DESC LIMIT 3",
                    par).fetchall()}
        out["importazioni"] = conn.execute(
            "SELECT s.*, a.ragione_sociale FROM erp.sincronizzazioni s JOIN aziende a ON a.codice = s.azienda"
            " WHERE s.azienda = ANY(%s) ORDER BY a.ragione_sociale, s.entita", (s["aziende"],)).fetchall()
        if "fonti" in s["permessi"]:
            out["fonti"] = [f for f in _fonti(conn, s) if f["stato"] == "attesa" or not f["owner"]
                            or f["owner"] == "da-assegnare"]
    return out


# --------------------------------------------------------------- Vedi come
def _kc(s, percorso, metodo="GET", corpo=None, **params):
    """Chiamata all'API di amministrazione di Keycloak CON IL TOKEN
    DELL'AMMINISTRATORE: le deleghe (keycloak/deleghe.py) le applica Keycloak,
    non questo codice. Un errore qui non puo' dare piu' di quanto Keycloak
    conceda a quella persona."""
    r = httpx.request(metodo, f"{KC_INTERNO}/admin/realms/{REALM}{percorso}", params=params, json=corpo,
                      timeout=15, headers={"Authorization": "Bearer " + _token_valido(s)})
    if r.status_code == 401:
        s["scade"] = 0                     # sessione SSO chiusa altrove: si rientra
        raise HTTPException(401, "sessione scaduta: ricaricare la pagina")
    if r.status_code == 403:
        raise HTTPException(403, "Keycloak non ti consente questa operazione")
    if r.status_code == 404:
        raise HTTPException(404, "non trovato")
    if r.status_code == 409:
        raise HTTPException(409, "esiste gia' (nome utente, email o gruppo gia' usati)")
    r.raise_for_status()
    return r.json() if r.content else None


@app.get(f"{BASE}/api/vedi-come/utenti")
def vc_utenti(q: str = "", s=Depends(utente)):
    serve(s, "vedicome")
    return [{"id": u["id"], "username": u["username"], "nome": " ".join(x for x in (u.get("firstName"), u.get("lastName")) if x),
             "email": u.get("email", ""), "attivo": u.get("enabled", False)}
            for u in _kc(s, "/users", search=q, max=50, briefRepresentation="true")]


@app.get(f"{BASE}/api/vedi-come/utenti/{{uid}}")
def vc_dettaglio(uid: str, s=Depends(utente)):
    serve(s, "vedicome")
    u = _dall_indice(s, uid)
    gruppi = [g["name"] for g in u["_gruppi"]]
    aziende = logica.aziende_da_gruppi(gruppi)
    with db() as conn:
        fonti = conn.execute("SELECT id, descrizione, tipo, residency, stato, aziende, acl_groups FROM sources"
                             " ORDER BY descrizione").fetchall()
        nome = " ".join(x for x in (u.get("firstName"), u.get("lastName")) if x) or u["username"]
        registra(conn, s, "vedi-come", f"Ha consultato le fonti visibili da {nome}", nome)
    visibili, non = [], []
    for f in fonti:
        ok, motivo, avvisi = logica.visibilita(f, gruppi, aziende, u.get("enabled", False))
        (visibili if ok else non).append({**f, "motivo": motivo, "avvisi": avvisi})
    return {"utente": {"nome": nome, "username": u["username"], "email": u.get("email", ""),
                       "attivo": u.get("enabled", False)},
            "gruppi": [g for g in gruppi if not g.startswith("azienda-")], "aziende": aziende,
            "visibili": visibili, "non_visibili": non}


# ------------------------------------------------------------------ Accessi
# Utenti, gruppi e profili (decisione 68): l'interfaccia e' la nostra, le
# funzioni sono quelle di Keycloak, chiamate con il token di chi lavora.
RADICE_PROFILI = "amministratori"


def _classifica(gruppi):
    """Gruppi Keycloak di un utente -> (operativi, aziende, profili)."""
    op, az, pr = [], [], []
    for g in gruppi:
        if g["path"].startswith(f"/{RADICE_PROFILI}/"):
            pr.append(g["name"])
        elif g["name"].startswith("azienda-"):
            az.append(g["name"][len("azienda-"):])
        elif g["name"] != RADICE_PROFILI:
            op.append(g["name"])
    return sorted(op), sorted(az), sorted(pr)


def _utente_breve(u):
    return {"id": u["id"], "username": u["username"],
            "nome": " ".join(x for x in (u.get("firstName"), u.get("lastName")) if x) or u["username"],
            "nome_proprio": u.get("firstName", ""), "cognome": u.get("lastName", ""),
            "email": u.get("email", ""), "attivo": u.get("enabled", False)}


def _nel_mio_ambito(s, aziende_utente):
    """§3.3: chi e' limitato ad alcune aziende gestisce solo le loro persone
    (e quelle ancora senza azienda, per poterle sistemare). Keycloak non conosce
    le aziende: questo controllo sta qui."""
    return s["tutte_le_aziende"] or not aziende_utente or bool(set(aziende_utente) & set(s["aziende"]))


def _indice(s):
    """uid -> utente con i suoi gruppi, costruito SOLO con letture "di elenco".

    Keycloak 26.7 con le deleghe fini: il permesso "leggere gli utenti" vale
    per gli elenchi ma NON per /users/{id} ne' /users/{id}/groups (verificato:
    403 al Revisore, 200 con un permesso sul singolo utente). Elenco utenti,
    albero dei gruppi e membri di ogni gruppo invece funzionano per chiunque
    abbia la lettura. Bonus: una chiamata per gruppo, non una per persona.
    ponytail: tutto in memoria, ok fino a qualche migliaio di persone."""
    utenti = {u["id"]: {**u, "_gruppi": []}
              for u in _kc(s, "/users", max=5000, briefRepresentation="false")}

    def visita(g):
        for m in _kc(s, f"/groups/{g['id']}/members", max=5000, briefRepresentation="true"):
            if m["id"] in utenti:
                utenti[m["id"]]["_gruppi"].append({"name": g["name"], "path": g["path"]})
        figli = g.get("subGroups") or (_kc(s, f"/groups/{g['id']}/children", max=500) if g.get("subGroupCount") else [])
        for f in figli:
            visita(f)

    for g in _kc(s, "/groups", max=500, briefRepresentation="false"):
        visita(g)
    return utenti


def _dall_indice(s, uid):
    u = _indice(s).get(uid)
    if not u:
        raise HTTPException(404, "persona non trovata")
    return u


def _gruppo_da_percorso(s, percorso):
    return _kc(s, f"/group-by-path/{percorso.lstrip('/')}")


@app.get(f"{BASE}/api/utenti")
def utenti(q: str = "", s=Depends(utente)):
    serve(s, "utenti")
    out = []
    for u in _indice(s).values():
        op, az, pr = _classifica(u["_gruppi"])
        testo = " ".join(str(u.get(k) or "") for k in ("username", "firstName", "lastName", "email")).lower()
        if (not q or q.lower() in testo) and _nel_mio_ambito(s, az):
            out.append({**_utente_breve(u), "gruppi": op, "aziende": az, "profili": pr})
    return sorted(out, key=lambda x: x["nome"].lower())


@app.get(f"{BASE}/api/utenti/{{uid}}")
def utente_dettaglio(uid: str, s=Depends(utente)):
    serve(s, "utenti")
    u = _dall_indice(s, uid)
    op, az, pr = _classifica(u["_gruppi"])
    if not _nel_mio_ambito(s, az):
        raise HTTPException(403, "persona di un'azienda a cui non sei abilitato")
    return {**_utente_breve(u), "gruppi": op, "aziende": az, "profili": pr,
            "creato_il": u.get("createdTimestamp"),
            "federato": bool(u.get("federationLink"))}      # dalla directory aziendale: anagrafica in sola lettura


def _password_temporanea():
    # Leggibile al telefono, abbastanza lunga da non indovinarsi; si cambia al
    # primo accesso (temporary=True).
    return "-".join(secrets.token_hex(3) for _ in range(3)) + "-A1"


@app.post(f"{BASE}/api/utenti")
async def crea_utente(request: Request, s=Depends(utente)):
    serve(s, "utenti", "M")
    c = await request.json()
    username = (c.get("username") or "").strip().lower()
    if not username or " " in username:
        raise HTTPException(422, "nome utente obbligatorio, senza spazi")
    aziende = [a for a in c.get("aziende", []) if a]
    if not aziende:
        raise HTTPException(422, "almeno un'azienda: senza, la persona non vedrebbe nessun dato")
    if not set(aziende) <= set(s["aziende"]):
        raise HTTPException(403, "puoi abilitare solo aziende a cui sei abilitato")
    _kc(s, "/users", "POST", {"username": username, "firstName": c.get("nome_proprio", ""),
                              "lastName": c.get("cognome", ""), "email": c.get("email") or None,
                              "enabled": True, "emailVerified": False})
    uid = _kc(s, "/users", username=username, exact="true")[0]["id"]
    for percorso in ["/tutti"] + [f"/{g}" for g in c.get("gruppi", []) if g] + [f"/azienda-{a}" for a in aziende]:
        _kc(s, f"/users/{uid}/groups/{_gruppo_da_percorso(s, percorso)['id']}", "PUT")
    pw = _password_temporanea()
    _kc(s, f"/users/{uid}/reset-password", "PUT", {"type": "password", "value": pw, "temporary": True})
    with db() as conn:
        registra(conn, s, "accessi", f"Ha creato l'utente {username}", username, ",".join(aziende),
                 dopo={"gruppi": c.get("gruppi", []), "aziende": aziende})
    return {"id": uid, "password_temporanea": pw}


@app.put(f"{BASE}/api/utenti/{{uid}}")
async def modifica_utente(uid: str, request: Request, s=Depends(utente)):
    serve(s, "utenti", "M")
    c = await request.json()
    ind = _dall_indice(s, uid)
    _, az, _ = _classifica(ind["_gruppi"])
    if not _nel_mio_ambito(s, az):
        raise HTTPException(403, "persona di un'azienda a cui non sei abilitato")
    u = _kc(s, f"/users/{uid}")          # rappresentazione completa: la gestione la concede
    prima = {k: u.get(k) for k in ("firstName", "lastName", "email", "enabled")}
    dopo = dict(prima)
    for k, campo in (("firstName", "nome_proprio"), ("lastName", "cognome"), ("email", "email")):
        if campo in c:
            dopo[k] = c[campo] or None
    if "attivo" in c:
        dopo["enabled"] = bool(c["attivo"])
        if not dopo["enabled"] and not (c.get("motivo") or "").strip():
            raise HTTPException(422, "il motivo e' obbligatorio per disattivare una persona")
    if dopo == prima:
        return {"ok": True}
    _kc(s, f"/users/{uid}", "PUT", {**u, **dopo})
    azione = ("Ha disattivato" if prima["enabled"] and not dopo["enabled"] else
              "Ha riattivato" if not prima["enabled"] and dopo["enabled"] else "Ha modificato")
    with db() as conn:
        registra(conn, s, "accessi", f"{azione} l'utente {u['username']}", u["username"], ",".join(az) or None,
                 (c.get("motivo") or "").strip() or None,
                 {k: prima[k] for k in prima if prima[k] != dopo[k]}, {k: dopo[k] for k in dopo if prima[k] != dopo[k]})
    return {"ok": True}


@app.post(f"{BASE}/api/utenti/{{uid}}/password")
def reimposta_password(uid: str, s=Depends(utente)):
    """Password temporanea mostrata UNA volta a chi la reimposta, da cambiare
    al primo accesso. Su un amministratore Keycloak la rifiuta (deleghe)."""
    serve(s, "utenti", "M")
    u = _dall_indice(s, uid)
    _, az, _ = _classifica(u["_gruppi"])
    if not _nel_mio_ambito(s, az):
        raise HTTPException(403, "persona di un'azienda a cui non sei abilitato")
    pw = _password_temporanea()
    _kc(s, f"/users/{uid}/reset-password", "PUT", {"type": "password", "value": pw, "temporary": True})
    with db() as conn:
        registra(conn, s, "accessi", f"Ha reimpostato la password di {u['username']}", u["username"], ",".join(az) or None)
    return {"password_temporanea": pw}


@app.post(f"{BASE}/api/utenti/{{uid}}/gruppi")
async def gruppi_utente(uid: str, request: Request, s=Depends(utente)):
    """Aggiunge/toglie gruppi operativi, aziende e profili. Chi puo' cosa lo
    decide Keycloak (i profili solo Gestione amministratori, Superutente solo
    il Superutente); l'ambito per azienda lo controlla questo codice."""
    c = await request.json()
    aggiungi, togli = c.get("aggiungi", []), c.get("togli", [])
    profili = [p for p in aggiungi + togli if p.startswith(f"/{RADICE_PROFILI}/")]
    serve(s, "profili" if profili and len(profili) == len(aggiungi + togli) else "utenti", "M")
    u = _dall_indice(s, uid)
    _, az, _ = _classifica(u["_gruppi"])
    if not _nel_mio_ambito(s, az):
        raise HTTPException(403, "persona di un'azienda a cui non sei abilitato")
    for p in aggiungi + togli:
        if p.startswith("/azienda-") and p[len("/azienda-"):] not in s["aziende"]:
            raise HTTPException(403, f"non sei abilitato all'azienda {p[len('/azienda-'):]}")
    for p in aggiungi:
        _kc(s, f"/users/{uid}/groups/{_gruppo_da_percorso(s, p)['id']}", "PUT")
    for p in togli:
        _kc(s, f"/users/{uid}/groups/{_gruppo_da_percorso(s, p)['id']}", "DELETE")
    with db() as conn:
        registra(conn, s, "accessi", f"Ha cambiato i gruppi di {u['username']}", u["username"], ",".join(az) or None,
                 prima={"tolti": togli}, dopo={"aggiunti": aggiungi})
    return {"ok": True}


def _gruppi_operativi(s):
    return [g for g in _kc(s, "/groups", max=500, briefRepresentation="true")
            if g["name"] != RADICE_PROFILI and not g["name"].startswith("azienda-")]


@app.get(f"{BASE}/api/gruppi-operativi")
def gruppi_operativi(s=Depends(utente)):
    serve(s, "gruppi")
    with db() as conn:
        uso = {}
        for r in conn.execute("SELECT unnest(acl_groups) g, descrizione FROM sources"):
            uso.setdefault(r["g"], []).append(r["descrizione"])
    out = []
    for g in _gruppi_operativi(s):
        membri = _kc(s, f"/groups/{g['id']}/members", max=1000, briefRepresentation="true")
        out.append({"id": g["id"], "nome": g["name"], "membri": len(membri), "fonti": sorted(uso.get(g["name"], []))})
    return sorted(out, key=lambda x: x["nome"])


@app.get(f"{BASE}/api/gruppi-operativi/{{gid}}/membri")
def membri_gruppo(gid: str, s=Depends(utente)):
    serve(s, "gruppi")
    return [_utente_breve(u) for u in _kc(s, f"/groups/{gid}/members", max=1000, briefRepresentation="true")]


@app.post(f"{BASE}/api/gruppi-operativi")
async def crea_gruppo(request: Request, s=Depends(utente)):
    serve(s, "struttura", "M")
    nome = ((await request.json()).get("nome") or "").strip().lower()
    if not nome or nome.startswith("azienda-") or nome == RADICE_PROFILI or "/" in nome:
        raise HTTPException(422, "nome non valido (niente '/', non 'azienda-...' ne' 'amministratori')")
    _kc(s, "/groups", "POST", {"name": nome})
    with db() as conn:
        registra(conn, s, "accessi", f"Ha creato il gruppo operativo {nome}", nome)
    return {"ok": True, "gestori": _allinea_gestori(s)}


@app.put(f"{BASE}/api/gruppi-operativi/{{gid}}")
async def rinomina_gruppo(gid: str, request: Request, s=Depends(utente)):
    serve(s, "struttura", "M")
    nome = ((await request.json()).get("nome") or "").strip().lower()
    g = _kc(s, f"/groups/{gid}")
    if not nome or nome.startswith("azienda-") or "/" in nome:
        raise HTTPException(422, "nome non valido")
    with db() as conn:
        # Le fonti citano il gruppo per nome: si rinominano insieme, nella
        # stessa transazione del registro, o una fonte smetterebbe di essere vista.
        conn.execute("UPDATE sources SET acl_groups = array_replace(acl_groups, %s, %s)", (g["name"], nome))
        _kc(s, f"/groups/{gid}", "PUT", {**g, "name": nome})
        registra(conn, s, "accessi", f"Ha rinominato il gruppo {g['name']} in {nome}", nome,
                 prima={"nome": g["name"]}, dopo={"nome": nome})
    return {"ok": True, "gestori": _allinea_gestori(s)}


@app.delete(f"{BASE}/api/gruppi-operativi/{{gid}}")
def elimina_gruppo(gid: str, request: Request, s=Depends(utente)):
    serve(s, "struttura", "M")
    g = _kc(s, f"/groups/{gid}")
    if g["name"] == "tutti":
        raise HTTPException(422, "il gruppo 'tutti' e' quello predefinito: non si elimina")
    if _kc(s, f"/groups/{gid}/members", max=1, briefRepresentation="true"):
        raise HTTPException(422, "il gruppo ha ancora dei membri")
    with db() as conn:
        if conn.execute("SELECT 1 FROM sources WHERE %s = ANY(acl_groups)", (g["name"],)).fetchone():
            raise HTTPException(422, "il gruppo e' usato da almeno una fonte")
        _kc(s, f"/groups/{gid}", "DELETE")
        registra(conn, s, "accessi", f"Ha eliminato il gruppo {g['name']}", g["name"])
    return {"ok": True, "gestori": _allinea_gestori(s)}


def _allinea_gestori(s):
    """Deleghe dei gestori di gruppo (gestori.py) con il token del Superutente:
    solo lui puo' modificare le deleghe di Keycloak. Un errore non annulla
    l'operazione sul gruppo: si segnala, e l'avvio successivo riallinea."""
    try:
        with httpx.Client(base_url=f"{KC_INTERNO}/admin/realms/{REALM}", timeout=60,
                          headers={"Authorization": "Bearer " + _token_valido(s)}) as c:
            return {"ok": True, "gruppi": [g for g, _ in gestori.allinea(c)]}
    except Exception as e:
        return {"ok": False, "errore": str(e)[:300]}


@app.post(f"{BASE}/api/gestori/allinea")
def allinea_gestori(s=Depends(utente)):
    """Dopo aver creato persone nuove: senza riallineare, il gestore non le
    puo' aggiungere al suo gruppo (gestori.py spiega perche')."""
    serve(s, "struttura", "M")
    esito = _allinea_gestori(s)
    if not esito["ok"]:
        raise HTTPException(502, "riallineamento non riuscito: " + esito["errore"])
    with db() as conn:
        registra(conn, s, "accessi", "Ha riallineato i permessi dei gestori di gruppo", None,
                 dopo={"gruppi": esito["gruppi"]})
    return esito


# ------------------------------------------------------- Gestori di gruppo
# Chi sta in <nome>-gestori gestisce le persone di <nome>. Qui si chiama
# Keycloak col token del gestore: e' Keycloak a limitarlo al suo gruppo
# (gestori.py). Il controllo qui sotto serve solo a dare un errore chiaro.
def _mio_gruppo(s, nome):
    if nome not in gestori.gestiti(s["gruppi"]):
        raise HTTPException(403, "non sei gestore di questo gruppo")
    return _gruppo_da_percorso(s, f"/{nome}")


@app.get(f"{BASE}/api/gestiti")
def gestiti(s=Depends(utente)):
    serve(s, "gestiti")
    out = []
    with db() as conn:
        for nome in gestori.gestiti(s["gruppi"]):
            g = _mio_gruppo(s, nome)
            membri = [_utente_breve(u) for u in _kc(s, f"/groups/{g['id']}/members", max=1000, briefRepresentation="true")]
            cartelle = conn.execute("""
                SELECT s.id, s.descrizione, s.percorso, s.stato, s.aziende,
                       (SELECT count(*) FROM documenti d WHERE d.source_id = s.id AND d.stato = 'indicizzato') AS documenti,
                       (SELECT count(*) FROM documenti d WHERE d.source_id = s.id AND d.stato = 'errore') AS errori,
                       (SELECT max(d.indicizzato_il) FROM documenti d WHERE d.source_id = s.id) AS aggiornata
                  FROM sources s WHERE %s = ANY(acl_groups) AND provenienza = 'cartella'
                 ORDER BY s.descrizione""", (nome,)).fetchall()
            out.append({"nome": nome, "id": g["id"], "membri": sorted(membri, key=lambda u: u["nome"].lower()),
                        "cartelle": cartelle})
    return out


@app.get(f"{BASE}/api/gestiti/{{nome}}/candidati")
def candidati(nome: str, q: str = "", s=Depends(utente)):
    serve(s, "gestiti")
    g = _mio_gruppo(s, nome)
    dentro = {u["id"] for u in _kc(s, f"/groups/{g['id']}/members", max=1000, briefRepresentation="true")}
    return [_utente_breve(u) for u in _kc(s, "/users", search=q, max=20, briefRepresentation="true")
            if u["id"] not in dentro and u.get("enabled", False)]


@app.post(f"{BASE}/api/gestiti/{{nome}}/membri/{{uid}}")
def aggiungi_membro(nome: str, uid: str, s=Depends(utente)):
    serve(s, "gestiti", "M")
    g = _mio_gruppo(s, nome)
    u = next((x for x in _kc(s, "/users", max=10000, briefRepresentation="true") if x["id"] == uid), None)
    if not u:
        raise HTTPException(404, "persona non trovata")
    try:
        _kc(s, f"/users/{uid}/groups/{g['id']}", "PUT")
    except HTTPException as e:
        if e.status_code == 403:
            raise HTTPException(403, "Keycloak non lo consente: e' un amministratore, oppure una persona creata "
                                     "da poco (serve il riallineamento dal Superutente, in Gruppi)")
        raise
    with db() as conn:
        registra(conn, s, "accessi", f"Ha aggiunto {u['username']} al gruppo {nome}", u["username"],
                 dopo={"gruppo": nome})
    return {"ok": True}


@app.delete(f"{BASE}/api/gestiti/{{nome}}/membri/{{uid}}")
def togli_membro(nome: str, uid: str, s=Depends(utente)):
    serve(s, "gestiti", "M")
    g = _mio_gruppo(s, nome)
    u = next((x for x in _kc(s, f"/groups/{g['id']}/members", max=1000, briefRepresentation="true")
              if x["id"] == uid), None)
    if not u:
        raise HTTPException(404, "la persona non e' nel gruppo")
    _kc(s, f"/users/{uid}/groups/{g['id']}", "DELETE")
    with db() as conn:
        registra(conn, s, "accessi", f"Ha tolto {u['username']} dal gruppo {nome}", u["username"],
                 prima={"gruppo": nome})
    return {"ok": True}


@app.get(f"{BASE}/api/profili")
def profili(s=Depends(utente)):
    serve(s, "profili")
    radice = _gruppo_da_percorso(s, f"/{RADICE_PROFILI}")
    cid = _kc(s, "/clients", clientId=CLIENT_ID)[0]["id"] if "struttura" in s["permessi"] else None
    out = []
    for g in _kc(s, f"/groups/{radice['id']}/children", max=500, briefRepresentation="false"):
        ruoli = g.get("clientRoles", {}).get(CLIENT_ID)
        if ruoli is None and cid:
            ruoli = [r["name"] for r in _kc(s, f"/groups/{g['id']}/role-mappings/clients/{cid}")]
        membri = [_utente_breve(u) for u in _kc(s, f"/groups/{g['id']}/members", max=1000, briefRepresentation="true")]
        out.append({"id": g["id"], "nome": g["name"], "percorso": g["path"], "ruoli": sorted(ruoli or []),
                    "membri": membri})
    return {"profili": sorted(out, key=lambda x: x["nome"]),
            "ruoli": [{"id": k, "nome": logica.NOMI_RUOLI[k]} for k in logica.RUOLI_PROFILO]}


@app.put(f"{BASE}/api/profili/{{gid}}/ruoli")
async def ruoli_profilo(gid: str, request: Request, s=Depends(utente)):
    """Comporre un profilo e' 'struttura': solo il Superutente."""
    serve(s, "struttura", "M")
    # Il ruolo di gestore non si compone in un profilo: lo da' il gruppo -gestori.
    voluti = {r for r in (await request.json()).get("ruoli", []) if r in logica.RUOLI_PROFILO}
    g = _kc(s, f"/groups/{gid}")
    if not g["path"].startswith(f"/{RADICE_PROFILI}/"):
        raise HTTPException(422, "non e' un profilo di amministrazione")
    cid = _kc(s, "/clients", clientId=CLIENT_ID)[0]["id"]
    presenti = {r["name"]: r for r in _kc(s, f"/groups/{gid}/role-mappings/clients/{cid}")}
    tutti = {r["name"]: r for r in _kc(s, f"/clients/{cid}/roles")}
    da_aggiungere = [tutti[n] for n in voluti - set(presenti)]
    da_togliere = [presenti[n] for n in set(presenti) - voluti if n in logica.RUOLI_PROFILO]
    if da_aggiungere:
        _kc(s, f"/groups/{gid}/role-mappings/clients/{cid}", "POST", da_aggiungere)
    if da_togliere:
        _kc(s, f"/groups/{gid}/role-mappings/clients/{cid}", "DELETE", da_togliere)
    with db() as conn:
        registra(conn, s, "accessi", f"Ha cambiato i ruoli del profilo {g['name']}", g["name"],
                 prima={"ruoli": sorted(presenti)}, dopo={"ruoli": sorted(voluti)})
    return {"ok": True}


@app.post(f"{BASE}/api/profili")
async def crea_profilo(request: Request, s=Depends(utente)):
    serve(s, "struttura", "M")
    nome = ((await request.json()).get("nome") or "").strip()
    if not nome or "/" in nome:
        raise HTTPException(422, "nome non valido")
    radice = _gruppo_da_percorso(s, f"/{RADICE_PROFILI}")
    _kc(s, f"/groups/{radice['id']}/children", "POST", {"name": nome})
    with db() as conn:
        registra(conn, s, "accessi", f"Ha creato il profilo {nome}", nome)
    return {"ok": True}


# ------------------------------------------------------------------ Aziende
# Decisione 69: ogni azienda ha il SUO connettore al gestionale di
# riferimento. Un'azienda esiste in due posti che devono restare allineati:
# la riga in `aziende` e il gruppo Keycloak 'azienda-<codice>' che abilita le
# persone. Si creano insieme; se il secondo fallisce, si annulla il primo.
@app.get(f"{BASE}/api/aziende-gestione")
def aziende_gestione(s=Depends(utente)):
    serve(s, "aziende")
    with db() as conn:
        righe = conn.execute("""
            SELECT a.*, (SELECT max(completata_il) FROM erp.sincronizzazioni x
                          WHERE x.azienda = a.codice AND x.esito = 'ok') AS ultima_importazione
              FROM aziende a ORDER BY a.attiva DESC, a.ragione_sociale""").fetchall()
    for a in righe:
        g = httpx.get(f"{KC_INTERNO}/admin/realms/{REALM}/group-by-path/azienda-{a['codice']}", timeout=15,
                      headers={"Authorization": "Bearer " + _token_valido(s)})
        a["gruppo_presente"] = g.status_code == 200
        a["persone"] = len(_kc(s, f"/groups/{g.json()['id']}/members", max=5000, briefRepresentation="true")) \
            if g.status_code == 200 else None
        a["connettore_nome"] = logica.CONNETTORI.get(a["connettore"] or "", "Non collegata")
    return {"aziende": righe, "connettori": logica.CONNETTORI}


def _campi_azienda(c):
    rs = (c.get("ragione_sociale") or "").strip()
    if not rs:
        raise HTTPException(422, "la ragione sociale e' obbligatoria")
    # Il tipo di connettore e' ricavato dal collegamento (decisione 70): se il
    # collegamento c'e', il tipo lo si deriva lato server; altrimenti resta il
    # valore inviato (o nullo). Non si valida contro un elenco fisso qui.
    return {"ragione_sociale": rs, "partita_iva": (c.get("partita_iva") or "").strip() or None,
            "connettore": (c.get("connettore") or "").strip() or None,
            "codice_origine": (c.get("codice_origine") or "").strip() or None,
            "collegamento": (c.get("collegamento") or "").strip() or None,
            "note": (c.get("note") or "").strip() or None}


@app.post(f"{BASE}/api/aziende-gestione")
async def crea_azienda(request: Request, s=Depends(utente)):
    serve(s, "aziende", "M")
    c = await request.json()
    codice = (c.get("codice") or "").strip().lower()
    if not logica.CODICE_AZIENDA.match(codice):
        raise HTTPException(422, "codice non valido: minuscole, cifre e trattini, 2-31 caratteri (es. «luis»)")
    campi = _campi_azienda(c)
    with db() as conn:
        if conn.execute("SELECT 1 FROM aziende WHERE codice = %s", (codice,)).fetchone():
            raise HTTPException(409, "esiste gia' un'azienda con questo codice")
        if campi["collegamento"] and not conn.execute(
                "SELECT 1 FROM collegamenti WHERE id = %s", (campi["collegamento"],)).fetchone():
            raise HTTPException(422, "collegamento inesistente")
        if campi["collegamento"]:
            campi["connettore"] = conn.execute(
                "SELECT tipo FROM collegamenti WHERE id = %s", (campi["collegamento"],)).fetchone()["tipo"]
        _kc(s, "/groups", "POST", {"name": f"azienda-{codice}",
                                   "attributes": {"descrizione": [f"Abilita ai dati di {campi['ragione_sociale']}"]}})
        try:
            conn.execute("INSERT INTO aziende (codice, ragione_sociale, partita_iva, connettore, codice_origine, collegamento, note)"
                         " VALUES (%(codice)s, %(ragione_sociale)s, %(partita_iva)s, %(connettore)s, %(codice_origine)s, %(collegamento)s, %(note)s)",
                         {"codice": codice, **campi})
            registra(conn, s, "accessi", f"Ha creato l'azienda {campi['ragione_sociale']}", campi["ragione_sociale"],
                     codice, dopo={"codice": codice, **campi})
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "questa azienda del gestionale e' gia' abbinata a un'altra azienda")
        except Exception:
            g = _kc(s, f"/group-by-path/azienda-{codice}")
            _kc(s, f"/groups/{g['id']}", "DELETE")
            raise
    s["aziende"] = sorted(set(s["aziende"]) | {codice})     # il Superutente la vede subito
    return {"ok": True}


@app.put(f"{BASE}/api/aziende-gestione/{{codice}}")
async def modifica_azienda(codice: str, request: Request, s=Depends(utente)):
    serve(s, "aziende", "M")
    c = await request.json()
    campi = _campi_azienda(c)
    attiva = bool(c.get("attiva", True))
    motivo = (c.get("motivo") or "").strip() or None
    with db() as conn:
        a = conn.execute("SELECT * FROM aziende WHERE codice = %s FOR UPDATE", (codice,)).fetchone()
        if not a:
            raise HTTPException(404, "azienda inesistente")
        if campi["collegamento"] and not conn.execute(
                "SELECT 1 FROM collegamenti WHERE id = %s", (campi["collegamento"],)).fetchone():
            raise HTTPException(422, "collegamento inesistente")
        if campi["collegamento"]:
            campi["connettore"] = conn.execute(
                "SELECT tipo FROM collegamenti WHERE id = %s", (campi["collegamento"],)).fetchone()["tipo"]
        if a["attiva"] and not attiva and not motivo:
            raise HTTPException(422, "il motivo e' obbligatorio per disattivare un'azienda")
        prima = {k: a[k] for k in (*campi, "attiva")}
        dopo = {**campi, "attiva": attiva}
        cambiati = [k for k in dopo if dopo[k] != prima[k]]
        if not cambiati:
            return {"ok": True}
        try:
            conn.execute("UPDATE aziende SET ragione_sociale=%(ragione_sociale)s, partita_iva=%(partita_iva)s,"
                         " connettore=%(connettore)s, codice_origine=%(codice_origine)s, collegamento=%(collegamento)s,"
                         " note=%(note)s, attiva=%(attiva)s"
                         " WHERE codice=%(codice)s", {**dopo, "codice": codice})
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "questa azienda del gestionale e' gia' abbinata a un'altra azienda")
        azione = ("Ha disattivato" if a["attiva"] and not attiva else
                  "Ha riattivato" if not a["attiva"] and attiva else "Ha modificato")
        registra(conn, s, "accessi", f"{azione} l'azienda {campi['ragione_sociale']}", campi["ragione_sociale"], codice,
                 motivo, {k: prima[k] for k in cambiati}, {k: dopo[k] for k in cambiati})
    return {"ok": True}


# ------------------------------------------------------------------ Gestionali
# Collegamenti ai gestionali (SPECIFICA-CONNETTORI.md §8): il pannello INOLTRA
# al servizio `connettori`, che custodisce la chiave e i segreti. Qui non si
# vede mai un segreto, ne' in chiaro ne' cifrato.
def _connettori(metodo, percorso, corpo=None):
    r = httpx.request(metodo, CONNETTORI_URL + percorso, json=corpo, timeout=60,
                      headers={"Authorization": "Bearer " + CONNETTORI_KEY})
    if r.status_code >= 400:
        dettaglio = ""
        try:
            dettaglio = r.json().get("detail", "")
        except Exception:
            dettaglio = r.text[:200]
        raise HTTPException(502 if r.status_code >= 500 else r.status_code,
                            str(dettaglio) or "errore dal servizio connettori")
    return r.json() if r.content else None


@app.get(f"{BASE}/api/connettori")
def catalogo_connettori(s=Depends(utente)):
    serve(s, "collegamenti")
    return _connettori("GET", "/v1/connettori")


@app.post(f"{BASE}/api/connettori/prova")
async def prova_nuovo_collegamento(request: Request, s=Depends(utente)):
    """Prova un collegamento prima di salvarlo: il segreto passa al servizio e
    non viene mai scritto nel database ne' registrato."""
    serve(s, "collegamenti", "M")
    return _connettori("POST", "/v1/prova", await request.json())


@app.get(f"{BASE}/api/collegamenti")
def collegamenti(s=Depends(utente)):
    serve(s, "collegamenti")
    return _connettori("GET", "/v1/collegamenti")


@app.post(f"{BASE}/api/collegamenti")
async def crea_collegamento(request: Request, s=Depends(utente)):
    serve(s, "collegamenti", "M")
    corpo = await request.json()
    ris = _connettori("POST", "/v1/collegamenti", corpo)
    with db() as conn:
        registra(conn, s, "gestionali", f"Ha creato il collegamento «{corpo.get('nome')}»", ris["id"])
    return ris


@app.put(f"{BASE}/api/collegamenti/{{cid}}")
async def modifica_collegamento(cid: str, request: Request, s=Depends(utente)):
    serve(s, "collegamenti", "M")
    corpo = await request.json()
    # Chi cambia la password (per il registro), MAI il valore della password.
    corpo.setdefault("chi", s["username"])
    ris = _connettori("PUT", f"/v1/collegamenti/{cid}", corpo)
    with db() as conn:
        registra(conn, s, "gestionali", f"Ha modificato il collegamento «{ris['nome']}»", cid)
    return ris


@app.post(f"{BASE}/api/collegamenti/{{cid}}/prova")
def prova_collegamento(cid: str, s=Depends(utente)):
    serve(s, "collegamenti", "M")
    return _connettori("POST", f"/v1/collegamenti/{cid}/prova")


@app.get(f"{BASE}/api/collegamenti/{{cid}}/aziende")
def aziende_collegamento(cid: str, s=Depends(utente)):
    serve(s, "collegamenti")
    return _connettori("GET", f"/v1/collegamenti/{cid}/aziende")


@app.post(f"{BASE}/api/collegamenti/{{cid}}/importa")
async def importa_collegamento(cid: str, request: Request, s=Depends(utente)):
    serve(s, "collegamenti", "M")
    return _connettori("POST", f"/v1/collegamenti/{cid}/importa", await request.json())


@app.get(f"{BASE}/api/importazioni")
def importazioni(s=Depends(utente)):
    """Lo stato delle importazioni (griglia azienda x entita'): lo scrive il
    motore in erp.sincronizzazioni, lo legge qui. Le frequenze stanno in
    pianificazioni (predefinite in base.scheda)."""
    serve(s, "importazioni")
    with db() as conn:
        aziende = conn.execute(
            "SELECT a.codice, a.ragione_sociale, a.collegamento, c.nome AS collegamento_nome"
            " FROM aziende a LEFT JOIN collegamenti c ON c.id = a.collegamento"
            " WHERE a.attiva ORDER BY a.ragione_sociale").fetchall()
        sincro = conn.execute("SELECT * FROM erp.sincronizzazioni ORDER BY azienda, entita").fetchall()
        pian = conn.execute("SELECT * FROM pianificazioni ORDER BY azienda, entita").fetchall()
    return {"aziende": aziende, "sincronizzazioni": sincro, "pianificazioni": pian}


@app.post(f"{BASE}/api/importazioni/avvia")
async def avvia_importazione(request: Request, s=Depends(utente)):
    """«Avvia ora» di una entita' per un'azienda: passa dal servizio connettori
    (che decifra e importa). Lo stato finisce in erp.sincronizzazioni."""
    serve(s, "importazioni", "M")
    c = await request.json()
    azienda = (c.get("azienda") or "").strip()
    entita = (c.get("entita") or "").strip()
    with db() as conn:
        a = conn.execute("SELECT collegamento FROM aziende WHERE codice = %s", (azienda,)).fetchone()
    if not a or not a["collegamento"]:
        raise HTTPException(422, "l'azienda non ha un collegamento al gestionale")
    return _connettori("POST", f"/v1/collegamenti/{a['collegamento']}/importa",
                       {"azienda": azienda, "entita": entita})


def _azioni(metodo, percorso, corpo):
    """Chiamata al servizio azioni (riavvio/stop): il pannello inoltra, non ha
    il socket di Docker."""
    r = httpx.request(metodo, AZIONI_URL + percorso, json=corpo, timeout=60,
                      headers={"Authorization": "Bearer " + AZIONI_KEY})
    if r.status_code >= 400:
        dettaglio = ""
        try:
            dettaglio = r.json().get("detail", "")
        except Exception:
            dettaglio = r.text[:200]
        raise HTTPException(502 if r.status_code >= 500 else r.status_code,
                            str(dettaglio) or "errore dal servizio azioni")
    return r.json() if r.content else None


@app.post(f"{BASE}/api/sistemi/{{nome}}/azione")
async def azione_sistema(nome: str, request: Request, s=Depends(utente)):
    """Riavvia/ferma un servizio (whitelist + registro, nel servizio azioni)."""
    serve(s, "azioni", "M")
    c = await request.json()
    azione = (c.get("azione") or "").strip()
    if azione not in ("riavvia", "ferma"):
        raise HTTPException(422, "azione sconosciuta")
    with db() as conn:
        registra(conn, s, "sistemi", f"Ha chiesto di {'riavviare' if azione == 'riavvia' else 'fermare'} il servizio {nome}", nome)
    return _azioni("POST", f"/v1/azioni/{nome}/{azione}", {"chi": s["username"], "chi_nome": s["nome"]})


# ------------------------------------------------------------------ Aspetto
@app.get(f"{BASE}/api/aspetto")
def aspetto(s=Depends(utente)):
    serve(s, "aspetto")
    with db() as conn:
        r = conn.execute("SELECT * FROM aspetto WHERE id = 1").fetchone()
    return {"palette": r["palette"], "predefinita": logica.PREDEFINITA,
            "aggiornato_da": r["aggiornato_da"], "aggiornato_il": r["aggiornato_il"],
            "identita": {k: os.environ.get(v, "") for k, v in (
                ("azienda", "NOME_AZIENDA"), ("assistente", "NOME_ASSISTENTE"),
                ("benvenuto", "MESSAGGIO_BENVENUTO"), ("pie", "PIE_DI_PAGINA"))}}


@app.put(f"{BASE}/api/aspetto")
async def salva_aspetto(request: Request, s=Depends(utente)):
    serve(s, "aspetto", "M")
    p = {k: str(v).lower() for k, v in (await request.json()).get("palette", {}).items() if k in logica.CONFIGURABILI}
    errori = logica.verifica_palette(p)
    if errori:
        raise HTTPException(422, " ".join(errori))
    with db() as conn:
        prima = conn.execute("SELECT palette FROM aspetto WHERE id = 1 FOR UPDATE").fetchone()["palette"]
        conn.execute("UPDATE aspetto SET palette=%s, aggiornato_da=%s, aggiornato_il=now() WHERE id=1",
                     (json.dumps(p), s["username"]))
        registra(conn, s, "aspetto", "Ha aggiornato la palette", prima=prima, dopo=p)
    return {"ok": True}
