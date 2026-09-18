"""Servizio azioni: riavvio e stop dei servizi, con whitelist e registro.

L'UNICO componente che tocca Docker. Espone due sole operazioni (riavvio, stop),
solo sui container in whitelist, autenticato con un segreto condiviso. Ogni
azione finisce nel registro modifiche (area 'sistemi'), così chi ha riavviato
cosa resta tracciato.

Perche' esiste un servizio apposta: il socket di Docker montato da' il controllo
COMPLETO sull'host. Quindi qui c'e' solo questo, nient'altro: whitelist
obbligatoria, nessuna operazione al di fuori di riavvio/stop, e il pannello non
vede mai il socket.
"""
import os

import docker
import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

DOCKER = docker.from_env()


def _whitelist():
    return {s.strip() for s in os.environ.get("AZIONI_ALLOWED", "").split(",") if s.strip()}


def _autocontrollo():
    mancanti = [v for v in ("AZIONI_KEY", "DATABASE_URL", "AZIONI_ALLOWED") if not os.environ.get(v)]
    if mancanti:
        raise SystemExit("configurazione incompleta, il servizio non parte: " + ", ".join(mancanti))


_autocontrollo()


def autenticato(authorization: str = Header(default="")):
    if authorization != f"Bearer {os.environ['AZIONI_KEY']}":
        raise HTTPException(401, "non autorizzato")


def _contenitore(nome):
    if nome not in _whitelist():
        raise HTTPException(403, f"il servizio {nome} non e' nella whitelist")
    trovati = DOCKER.containers.list(filters={"label": f"com.docker.compose.service={nome}"})
    if not trovati:
        raise HTTPException(404, f"container non trovato: {nome}")
    return trovati[0]


def _registra(chi, chi_nome, azione, oggetto):
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(
            "INSERT INTO registro_modifiche (chi, chi_nome, area, azione, oggetto)"
            " VALUES (%s, %s, 'sistemi', %s, %s)",
            (chi, chi_nome, azione, oggetto))


@app.post("/v1/azioni/{nome}/riavvia")
def riavvia(nome: str, corpo: dict, _=Depends(autenticato)):
    c = _contenitore(nome)
    c.restart()
    _registra(corpo.get("chi"), corpo.get("chi_nome"), f"Ha riavviato il servizio {nome}", nome)
    return {"ok": True}


@app.post("/v1/azioni/{nome}/ferma")
def ferma(nome: str, corpo: dict, _=Depends(autenticato)):
    c = _contenitore(nome)
    c.stop()
    _registra(corpo.get("chi"), corpo.get("chi_nome"), f"Ha fermato il servizio {nome}", nome)
    return {"ok": True}
