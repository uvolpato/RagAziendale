"""Immagini dei documenti: firma degli URL e lettura dal volume condiviso.

Le immagini sono servite dall'orchestratore dietro un URL FIRMATO a scadenza:
la firma (HMAC) viene emessa solo quando il gate ha gia' ammesso il chunk a cui
l'immagine appartiene. Chi indovina l'id senza firma non ottiene nulla, e la
scadenza corta limita la condivisione dell'URL. Le ACL stanno sulla fonte
(ereditate dal chunk): niente duplicazione del dato di sicurezza.
"""
import hashlib
import hmac
import os
import pathlib
import time

RADICE = pathlib.Path(os.environ.get("IMMAGINI", "/immagini"))
VALIDITA = 15 * 60            # secondi: abbastanza per la lettura, corto contro la condivisione


def _chiave():
    # ORCHESTRATOR_KEY e' gia' un segreto dell'orchestratore (e di LibreChat,
    # che e' il nostro frontend fidato). Non serve un secondo segreto per firmare
    # URL che solo il gate puo' far emettere.
    return os.environ["ORCHESTRATOR_KEY"].encode()


def firma_url(img_id: int, base: str) -> str:
    """URL assoluto firmato per un'immagine, valido VALIDITA secondi."""
    scade = int(time.time()) + VALIDITA
    firma = hmac.new(_chiave(), f"{img_id}:{scade}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{base}/immagini/{img_id}?scade={scade}&firma={firma}"


def valida(img_id: int, scade: str, firma: str) -> bool:
    """La firma dell'URL e' autentica e non scaduta."""
    try:
        if int(scade) < int(time.time()):
            return False
        attesa = hmac.new(_chiave(), f"{img_id}:{scade}".encode(), hashlib.sha256).hexdigest()[:32]
        return hmac.compare_digest(attesa, firma or "")
    except (ValueError, TypeError):
        return False


def leggi(conn, img_id: int):
    """(bytes, content_type) dell'immagine, o None. Legge il percorso dal DB."""
    r = conn.execute("SELECT percorso FROM immagini WHERE id = %s", (img_id,)).fetchone()
    if not r:
        return None
    p = RADICE / r["percorso"]
    if not p.is_file():
        return None
    return p.read_bytes(), "image/png"
