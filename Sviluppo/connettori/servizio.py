"""Servizio `connettori` (SPECIFICA-CONNETTORI.md §4.1, §7).

E' l'UNICO componente che possiede CHIAVE_CREDENZIALI e che cifra/decifra i
segreti dei collegamenti, in memoria, al momento dell'uso. Il pannello di
amministrazione NON ha la chiave: inoltra i segreti qui (quando il Superutente
li digita), e qui vengono cifrati e salvati. Un segreto salvato non torna mai
indietro, ne' al pannello ne' nei log.

Il servizio espone anche la prova di un collegamento, l'elenco delle aziende
del gestionale (per l'abbinamento) e l'avvio di un'importazione; tutto il resto
(storico, controlli, anomalie, stato) lo fa il motore `base.py`.

    uvicorn connettori.servizio:app     (nel container: vedi servizio-connettori/Dockerfile)
"""
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row
from fastapi import Depends, FastAPI, Header, HTTPException

from connettori import base, segreti

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _db():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def _chiave_per_versione(versione):
    """Versione 1 = chiave corrente, 0 = precedente (rotazione in corso)."""
    if versione == 0:
        return os.environ.get("CHIAVE_CREDENZIALI_PRECEDENTE") or os.environ["CHIAVE_CREDENZIALI"]
    return os.environ["CHIAVE_CREDENZIALI"]


# All'avvio: senza chiave il servizio non parte (fallimento rumoroso, come
# identita.autocontrollo). Chi ruba solo il database non ha i segreti.
@app.on_event("startup")
def _controllo_avvio():
    mancanti = [v for v in ("DATABASE_URL", "CHIAVE_CREDENZIALI", "CONNETTORI_KEY")
                if not os.environ.get(v)]
    if mancanti:
        raise SystemExit("configurazione incompleta, il servizio non parte: " + ", ".join(mancanti))
    _avvia_scheduler()
    _avvia_controllo_sistemi()


# -------------------------------------------------------- controllo sistemi
# I servizi giu' (da Uptime Kuma) diventano anomalie, e si chiudono da sole
# quando tornano su. Gira qui perche' c'e' gia' l'accesso al DB e un ciclo in
# background; legge Uptime Kuma in sola lettura.
STATI_KUMA = {0: "down", 1: "up", 2: "pending"}


def _stato_uptime():
    try:
        con = sqlite3.connect("file:/opt/uptime-kuma/kuma.db?mode=ro", uri=True, timeout=2)
        righe = con.execute(
            "SELECT m.name, h.status FROM heartbeat h JOIN monitor m ON m.id = h.monitor_id"
            " WHERE m.active = 1 AND h.id IN (SELECT max(id) FROM heartbeat GROUP BY monitor_id)"
        ).fetchall()
        con.close()
        return {r[0]: STATI_KUMA.get(r[1], "down") for r in righe}
    except Exception:
        return {}


def _avvia_controllo_sistemi():
    if os.environ.get("CONTROLLO_SISTEMI_OFF") == "1":
        return

    def ciclo():
        time.sleep(20)
        while True:
            try:
                stati = _stato_uptime()
                with _db() as conn:
                    for nome, stato in stati.items():
                        impronta = f"servizio:{nome}"
                        if stato == "down":
                            conn.execute(
                                "SELECT segnala_anomalia(%s, 'errore', 'sistemi', %s, %s, NULL, %s, '{}')",
                                (impronta, f"Il servizio {nome} non risponde",
                                 "Controlla il servizio in Monitoraggio sistemi.", f"servizio:{nome}"))
                        else:
                            conn.execute("SELECT chiudi_anomalia(%s)", (impronta,))
            except Exception as e:
                print(f"controllo sistemi: {e}")
            time.sleep(int(os.environ.get("CONTROLLO_SISTEMI_INTERVALLO", "30")))

    threading.Thread(target=ciclo, daemon=True, name="controllo-sistemi").start()


# ---------------------------------------------------------- scheduler ponte
# Prima di Dagster (decisione 60): un ciclo in background esegue le importazioni
# dovute. Dagster sostituira' questo thread chiamando lo stesso /v1/.../importa.
# ponytail: un solo processo, un solo thread. Si spegne con SCHEDULER_OFF=1.
def _esegui_una(azienda, entita, rileva_cancellazioni):
    with _db() as conn:
        a = conn.execute("SELECT collegamento FROM aziende WHERE codice = %s", (azienda,)).fetchone()
        if not a or not a["collegamento"]:
            return
        collegamento = conn.execute("SELECT * FROM collegamenti WHERE id = %s", (a["collegamento"],)).fetchone()
        connettore = _connettore_del(collegamento)
    with _db() as conn:
        base.importa(conn, connettore, azienda, entita, rileva_cancellazioni=rileva_cancellazioni)


def _avvia_scheduler():
    if os.environ.get("SCHEDULER_OFF") == "1":
        return
    from connettori import scheda
    intervallo = max(15, int(os.environ.get("SCHEDULER_INTERVALLO", "60")))

    def ciclo():
        time.sleep(10)          # lascia partire il servizio
        while True:
            try:
                with _db() as conn:
                    scheda.esegui(conn, None, _esegui_una)
            except Exception as e:
                print(f"scheduler: {e}")
            time.sleep(intervallo)

    threading.Thread(target=ciclo, daemon=True, name="scheduler-importazioni").start()


def autenticato(authorization: str = Header(default="")):
    if authorization != f"Bearer {os.environ['CONNETTORI_KEY']}":
        raise HTTPException(401, "non autorizzato")


# ------------------------------------------------------------ proiezione
def _uscita(riga):
    """Il collegamento come lo vede il pannello: MAI i segreti, nemmeno cifrati."""
    return {
        "id": riga["id"], "nome": riga["nome"], "tipo": riga["tipo"],
        "parametri": riga["parametri"], "stato": riga["stato"],
        "ultima_prova": riga["ultima_prova"], "attivo": riga["attivo"],
        "segreti_impostati": riga["segreti"] is not None,
        "segreti_cambiati_da": riga["segreti_cambiati_da"],
        "segreti_cambiati_il": riga["segreti_cambiati_il"],
        "creato_il": riga["creato_il"],
    }


def _collegamento(conn, cid, per_update=False):
    q = "SELECT * FROM collegamenti WHERE id = %s" + (" FOR UPDATE" if per_update else "")
    r = conn.execute(q, (cid,)).fetchone()
    if not r:
        raise HTTPException(404, "collegamento inesistente")
    return r


def _connettore_del(collegamento):
    """Il connettore con i parametri completi (non segreti + segreti decifrati)."""
    if collegamento["segreti"] is None:
        raise HTTPException(422, "il collegamento non ha segreti salvati")
    segreto = segreti.decifra(collegamento["segreti"],
                              _chiave_per_versione(collegamento["versione_chiave"]))
    return base.connettore_di(collegamento["tipo"], {**collegamento["parametri"], **segreto})


def _stato_da_prova(ris):
    if ris.get("ok"):
        return "funzionante"
    m = (ris.get("motivo") or "").lower()
    if any(k in m for k in ("superutente", "modificare", "scrivere", "permess", "sola lettura")):
        return "troppi_permessi"
    if any(k in m for k in ("password", "credenzial", "authentication")):
        return "credenziali_non_valide"
    return "non_raggiungibile"


def _slug(tipo, nome):
    b = re.sub(r"[^a-z0-9]+", "-", nome.lower()).strip("-")
    return (b or tipo)[:40]


# ---------------------------------------------------------------- catalogo
@app.get("/v1/connettori")
def connettori(_=Depends(autenticato)):
    return [{"tipo": t, "nome": m["nome"], "versione": m["versione"],
             "multi_azienda": m.get("multi_azienda", False),
             "parametri": m.get("parametri", []), "entita": m.get("entita", {})}
            for t, m in sorted(base.catalogo().items())]


@app.post("/v1/prova")
def prova_nuovo(corpo: dict, _=Depends(autenticato)):
    """Prova un collegamento NON ancora salvato: parametri e segreti in memoria.

    Il pannello lo usa nel modulo di creazione, PRIMA di salvare: niente viene
    scritto nel database, i segreti restano nel corpo della richiesta e tornano
    solo come esito (ok/motivo), mai come valori.
    """
    tipo = (corpo.get("tipo") or "").strip()
    if tipo not in base.catalogo():
        raise HTTPException(422, f"connettore sconosciuto: {tipo}")
    connettore = base.connettore_di(tipo, {**(corpo.get("parametri") or {}),
                                           **(corpo.get("segreti") or {})})
    return connettore.prova()


# ------------------------------------------------------------ collegamenti
@app.get("/v1/collegamenti")
def elenco(_=Depends(autenticato)):
    with _db() as conn:
        righe = conn.execute("SELECT * FROM collegamenti ORDER BY nome").fetchall()
        abbinate = conn.execute(
            "SELECT collegamento, codice, ragione_sociale, codice_origine"
            " FROM aziende WHERE collegamento IS NOT NULL").fetchall()
    per_col = {}
    for a in abbinate:
        per_col.setdefault(a["collegamento"], []).append(
            {"codice": a["codice"], "ragione_sociale": a["ragione_sociale"],
             "codice_origine": a["codice_origine"]})
    return [dict(_uscita(r), aziende=per_col.get(r["id"], [])) for r in righe]


@app.post("/v1/collegamenti")
def crea(corpo: dict, _=Depends(autenticato)):
    nome = (corpo.get("nome") or "").strip()
    tipo = (corpo.get("tipo") or "").strip()
    if not nome or not tipo:
        raise HTTPException(422, "nome e tipo obbligatori")
    if tipo not in base.catalogo():
        raise HTTPException(422, f"connettore sconosciuto: {tipo}")
    cid = (corpo.get("id") or "").strip() or _slug(tipo, nome)
    blob = segreti.cifra(corpo["segreti"], os.environ["CHIAVE_CREDENZIALI"]) \
        if corpo.get("segreti") else None
    with _db() as conn:
        if conn.execute("SELECT 1 FROM collegamenti WHERE id = %s", (cid,)).fetchone():
            raise HTTPException(409, "esiste gia' un collegamento con questo id")
        conn.execute(
            "INSERT INTO collegamenti (id, nome, tipo, parametri, segreti, versione_chiave)"
            " VALUES (%s,%s,%s,%s,%s,1)",
            (cid, nome, tipo, json.dumps(corpo.get("parametri") or {}), blob))
    return _carica(cid)


@app.put("/v1/collegamenti/{cid}")
def modifica(cid: str, corpo: dict, _=Depends(autenticato)):
    with _db() as conn:
        r = _collegamento(conn, cid, per_update=True)
        nome = corpo["nome"].strip() if "nome" in corpo else r["nome"]
        parametri = json.dumps(corpo["parametri"]) if "parametri" in corpo else \
            (r["parametri"] if isinstance(r["parametri"], str) else json.dumps(r["parametri"]))
        attivo = bool(corpo["attivo"]) if "attivo" in corpo else r["attivo"]
        segreto = r["segreti"]
        versione = r["versione_chiave"]
        cambiato_da = r["segreti_cambiati_da"]
        cambia_segreto = bool(corpo.get("segreti"))
        if cambia_segreto:
            segreto = segreti.cifra(corpo["segreti"], os.environ["CHIAVE_CREDENZIALI"])
            versione = 1
            cambiato_da = corpo.get("chi") or "pannello"
        conn.execute(
            "UPDATE collegamenti SET nome=%s, parametri=%s, attivo=%s, segreti=%s,"
            " versione_chiave=%s, segreti_cambiati_da=%s,"
            " segreti_cambiati_il = CASE WHEN %s THEN now() ELSE segreti_cambiati_il END"
            " WHERE id=%s",
            (nome, parametri, attivo, segreto, versione, cambiato_da, cambia_segreto, cid))
    return _carica(cid)


@app.post("/v1/collegamenti/{cid}/prova")
def prova(cid: str, _=Depends(autenticato)):
    with _db() as conn:
        connettore = _connettore_del(_collegamento(conn, cid))
        ris = connettore.prova()
        conn.execute("UPDATE collegamenti SET stato=%s, ultima_prova=%s WHERE id=%s",
                     (_stato_da_prova(ris),
                      json.dumps({"quando": datetime.now(timezone.utc).isoformat(), **ris}), cid))
    return dict(_carica(cid), prova=ris)


@app.get("/v1/collegamenti/{cid}/aziende")
def aziende(cid: str, _=Depends(autenticato)):
    with _db() as conn:
        return _connettore_del(_collegamento(conn, cid)).aziende()


@app.post("/v1/collegamenti/{cid}/importa")
def importa(cid: str, corpo: dict, _=Depends(autenticato)):
    azienda = (corpo.get("azienda") or "").strip()
    entita = (corpo.get("entita") or "").strip()
    if not azienda or not entita:
        raise HTTPException(422, "azienda e entita obbligatorie")
    with _db() as conn:
        r = _collegamento(conn, cid)
        a = conn.execute("SELECT collegamento FROM aziende WHERE codice = %s", (azienda,)).fetchone()
        if not a:
            raise HTTPException(404, "azienda inesistente")
        if a["collegamento"] != cid:
            raise HTTPException(409, f"l'azienda {azienda} non e' abbinata a questo collegamento")
        connettore = _connettore_del(r)
    with _db() as conn:
        return base.importa(conn, connettore, azienda, entita,
                            rileva_cancellazioni=True if corpo.get("completa") else None)


@app.post("/v1/esegui-dovute")
def esegui_dovute(_=Depends(autenticato)):
    """Un giro dello scheduler: importa tutto cio' che e' dovuto.

    E' il punto d'ingresso che Dagster (o il suo scheduler ponte) invoca a ogni
    scadenza. Qui stanno la chiave e la logica "cosa e' dovuto": Dagster fornisce
    solo l'orologio, lo storico e la UI, non il motore.
    """
    from connettori import scheda
    eseguiti = []

    def una(azienda, entita, rileva_cancellazioni):
        eseguiti.append({"azienda": azienda, "entita": entita})
        _esegui_una(azienda, entita, rileva_cancellazioni)

    with _db() as conn:
        scheda.esegui(conn, None, una)
    return {"eseguiti": eseguiti}


@app.post("/v1/anomalia")
def segnala_anomalia_esterna(corpo: dict, _=Depends(autenticato)):
    """Un componente esterno (Dagster) segnala un problema: finisce nelle
    anomalie deduplicate del pannello, non solo nei log di Dagster."""
    impronta = "dagster:" + ((corpo.get("impronta") or "errore").strip()[:80])
    titolo = (corpo.get("titolo") or "Importazioni programmate non riuscite").strip()[:200]
    dettaglio = corpo.get("dettaglio") or {}
    with _db() as conn:
        conn.execute("SELECT segnala_anomalia(%s,%s,%s,%s,%s,%s,%s,%s)",
                     (impronta, "errore", "importazioni", titolo,
                      "Le importazioni programmate non sono riuscite. Apri Gestione importazioni (Dagster) per i log.",
                      None, "servizio:dagster", json.dumps(dettaglio, ensure_ascii=False)))
    return {"ok": True}


@app.post("/v1/chiudi-anomalia")
def chiudi_anomalia_esterna(corpo: dict, _=Depends(autenticato)):
    impronta = "dagster:" + ((corpo.get("impronta") or "errore").strip()[:80])
    with _db() as conn:
        conn.execute("SELECT chiudi_anomalia(%s)", (impronta,))
    return {"ok": True}


def _carica(cid):
    with _db() as conn:
        return _uscita(_collegamento(conn, cid))
