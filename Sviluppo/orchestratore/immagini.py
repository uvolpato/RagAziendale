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
# Quanto vale un URL firmato. 15 minuti erano troppo pochi: una conversazione
# riaperta dopo pranzo mostrava riquadri vuoti al posto delle figure, e in coda
# ai log una fila di 403 (20/09/2026). Otto ore coprono la giornata di lavoro.
# Il collegamento resta una chiave che vale per chi ce l'ha, ma non e' piu'
# l'unica difesa: le ACL si ricontrollano quando l'immagine viene servita.
VALIDITA = int(os.environ.get("IMMAGINI_VALIDITA_MIN", "480")) * 60


def _chiave():
    # ORCHESTRATOR_KEY e' gia' un segreto dell'orchestratore (e di LibreChat,
    # che e' il nostro frontend fidato). Non serve un secondo segreto per firmare
    # URL che solo il gate puo' far emettere.
    return os.environ["ORCHESTRATOR_KEY"].encode()


def firma_url(img_id: int, base: str, utente: str = "") -> str:
    """URL assoluto firmato per un'immagine, valido VALIDITA secondi.

    La firma lega l'URL anche alla PERSONA a cui e' stato consegnato: cosi' il
    collegamento inoltrato a un collega di un'altra area non e' spendibile da
    lui, perche' serve anche a ritrovare i suoi gruppi quando si serve il file.
    """
    scade = int(time.time()) + VALIDITA
    return f"{base}/immagini/{img_id}?scade={scade}&firma={_firma(img_id, scade, utente)}" \
           + (f"&u={utente}" if utente else "")


def _firma(img_id: int, scade, utente: str = "") -> str:
    messaggio = f"{img_id}:{scade}:{utente}".encode()
    return hmac.new(_chiave(), messaggio, hashlib.sha256).hexdigest()[:32]


def valida(img_id: int, scade: str, firma: str, utente: str = "") -> bool:
    """La firma dell'URL e' autentica e non scaduta."""
    try:
        if int(scade) < int(time.time()):
            return False
        return hmac.compare_digest(_firma(img_id, scade, utente or ""), firma or "")
    except (ValueError, TypeError):
        return False


def visibile(conn, img_id: int, gruppi: list) -> bool:
    """L'immagine appartiene a una fonte che QUESTA persona puo' vedere, ADESSO.

    La firma dice che il gate aveva approvato; questo dice che il permesso vale
    ancora. Senza, un URL emesso stamattina continuerebbe a funzionare dopo che
    la fonte e' stata sospesa o la persona e' uscita dal gruppo — ed e' proprio
    il caso in cui deve smettere. Stesso predicato della ricerca: gruppi,
    aziende e stato della fonte, letti da `sources`.
    """
    from .identita import aziende as aziende_di
    aziende = aziende_di(gruppi or [])
    if not gruppi or not aziende:
        return False
    with conn.cursor() as cur:
        cur.execute("""SELECT EXISTS (
                         SELECT 1 FROM immagini i JOIN sources s ON s.id = i.source_id
                          WHERE i.id = %s AND s.acl_groups && %s::text[]
                            AND s.aziende && %s::text[] AND s.stato = 'attiva') AS ok""",
                    (img_id, gruppi, aziende))
        riga = cur.fetchone()
        return bool(riga["ok"] if isinstance(riga, dict) else riga[0])


def leggi(conn, img_id: int):
    """(bytes, content_type) dell'immagine, o None. Legge il percorso dal DB."""
    r = conn.execute("SELECT percorso FROM immagini WHERE id = %s", (img_id,)).fetchone()
    if not r:
        return None
    p = RADICE / r["percorso"]
    if not p.is_file():
        return None
    return p.read_bytes(), "image/png"


# Lato lungo della miniatura. Una figura di catalogo esce a 1600 px e pesa
# ~700 KB: quattro in fondo a una risposta sono quasi 3 MB, e in chat si
# vedono comunque piccole. Si manda la miniatura e si tiene l'originale a un
# clic di distanza (main._blocco_immagini).
LATO_MINIATURA = int(os.environ.get("IMMAGINI_LATO_MINIATURA", "320"))


def miniatura(dati: bytes, lato: int = LATO_MINIATURA):
    """(bytes, content_type) della versione piccola. JPEG: su una foto di
    prodotto pesa una frazione del PNG, e per una miniatura la perdita non si
    vede. Se l'immagine non e' leggibile si restituisce l'originale: una
    figura grande e' meglio di una figura assente."""
    from io import BytesIO
    try:
        from PIL import Image
        with Image.open(BytesIO(dati)) as img:
            img = img.convert("RGB")
            img.thumbnail((lato, lato))
            buf = BytesIO()
            img.save(buf, "JPEG", quality=80, optimize=True)
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        print(f"miniatura non riuscita: {type(e).__name__}: {e}", flush=True)
        return dati, "image/png"
