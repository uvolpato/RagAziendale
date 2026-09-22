"""Il documento ORIGINALE, aperto alla pagina citata.

Serve a rispondere a una domanda che il sistema non sa: «questa figura di che
prodotto e'?». Su una pagina di catalogo il codice sta SOTTO la sua fotina, e
l'occhio li abbina in mezzo secondo; il testo estratto perde quella
disposizione, e il 22/09/2026 abbiamo misurato che per 468 figure su 891 non
c'e' nessuna etichetta ricavabile dall'ordine di lettura.

Il collegamento non AFFERMA niente. Non dice «questa miniatura e' FSA1001» —
cosa che per meta' delle figure non possiamo sostenere — dice «viene da pagina
7», che e' vero per costruzione, e lascia guardare la pagina come l'ha
impaginata il fornitore.

Tre cose che lo rendono sicuro quanto le immagini, perche' sono le stesse:

- **URL firmato a scadenza** (HMAC su documento, pagina, scadenza e persona):
  emesso solo quando il gate ha gia' ammesso quel pezzo;
- **permesso ricontrollato al momento della consegna**, su `sources`: una
  fonte sospesa smette di rispondere subito, anche se il collegamento e' di
  stamattina;
- **niente percorsi nell'URL**: si passano l'id della fonte e il nome del
  documento, che sono gia' scritti nelle citazioni. La cartella sul disco non
  esce mai.

Il browser scarica solo la pagina che apri: FileResponse risponde alle
richieste Range, e il visualizzatore PDF le usa. Su un catalogo da 62 MB fa
la differenza fra un collegamento e un'attesa.
"""
import hashlib
import hmac
import os
import pathlib
import time

from .immagini import RADICE, _chiave

VALIDITA = int(os.environ.get("DOCUMENTI_VALIDITA_MIN",
                              os.environ.get("IMMAGINI_VALIDITA_MIN", "480"))) * 60


def _firma(source_id: str, documento: str, scade, utente: str = "") -> str:
    messaggio = f"{source_id}:{documento}:{scade}:{utente}".encode()
    return hmac.new(_chiave(), messaggio, hashlib.sha256).hexdigest()[:32]


def firma_url(source_id: str, documento: str, pagina, base: str, utente: str = "") -> str:
    """URL assoluto del documento, aperto alla pagina se c'e'.

    `#page=N` e' la sintassi che Chrome, Edge e Firefox rispettano per aprire
    un PDF a una pagina. Sta dopo il cancelletto, quindi NON arriva al server:
    non si firma e non si controlla, ed e' giusto cosi' — non e' un permesso,
    e' dove guardare.
    """
    from urllib.parse import quote
    scade = int(time.time()) + VALIDITA
    q = (f"f={quote(source_id, safe='')}&d={quote(documento, safe='')}"
         f"&scade={scade}&firma={_firma(source_id, documento, scade, utente)}"
         + (f"&u={quote(utente, safe='')}" if utente else ""))
    return f"{base}/documenti?{q}" + (f"#page={int(pagina)}" if pagina else "")


def valida(source_id: str, documento: str, scade: str, firma: str, utente: str = "") -> bool:
    try:
        if int(scade) < int(time.time()):
            return False
        return hmac.compare_digest(
            _firma(source_id or "", documento or "", scade, utente or ""), firma or "")
    except (ValueError, TypeError):
        return False


def visibile(conn, source_id: str, gruppi: list) -> bool:
    """Stesso predicato della ricerca e delle immagini: gruppi, aziende, stato
    della fonte, letti da `sources` ADESSO."""
    from .identita import aziende as aziende_di
    aziende = aziende_di(gruppi or [])
    if not gruppi or not aziende:
        return False
    with conn.cursor() as cur:
        cur.execute("""SELECT EXISTS (
                         SELECT 1 FROM sources s
                          WHERE s.id = %s AND s.acl_groups && %s::text[]
                            AND s.aziende && %s::text[] AND s.stato = 'attiva') AS ok""",
                    (source_id, gruppi, aziende))
        riga = cur.fetchone()
        return bool(riga["ok"] if isinstance(riga, dict) else riga[0])


def percorso(conn, source_id: str, documento: str):
    """Il file sul disco, o None.

    Il percorso si ricostruisce dal DB (`sources.percorso` + il nome del
    documento) e si verifica che stia DENTRO la radice: il nome arriva da un
    URL, e senza questo controllo un `../` lo porterebbe altrove. Non e'
    teoria — e' l'unico punto del sistema in cui un pezzo di URL diventa un
    percorso di file.
    """
    with conn.cursor() as cur:
        cur.execute("""SELECT s.percorso FROM documenti d JOIN sources s ON s.id = d.source_id
                        WHERE d.source_id = %s AND d.documento = %s""", (source_id, documento))
        riga = cur.fetchone()
    if not riga:
        return None
    cartella = riga["percorso"] if isinstance(riga, dict) else riga[0]
    radice = pathlib.Path(RADICE).resolve()
    p = (radice / cartella / documento).resolve()
    if not p.is_file() or radice not in p.parents:
        return None
    return p


TIPI = {".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8",
        ".md": "text/markdown; charset=utf-8", ".csv": "text/csv; charset=utf-8"}


def tipo(p) -> str:
    """Il media type, con un ripiego che NON si apre nel browser: un tipo
    sbagliato su un file eseguibile sarebbe il modo piu' semplice di fare
    danni, e qui si serve materiale che arriva da cartelle condivise."""
    return TIPI.get(pathlib.PurePath(p).suffix.lower(), "application/octet-stream")


def _prova():
    """Le regole che non toccano il database."""
    os.environ.setdefault("ORCHESTRATOR_KEY", "prova")
    u = firma_url("f1", "Catalogo (1).pdf", 7, "https://x", "utente")
    assert "#page=7" in u and "documenti?" in u
    assert "%20" in u or "+" in u, "il nome con spazi va codificato"
    import urllib.parse as up
    q = up.parse_qs(up.urlparse(u).query)
    assert valida("f1", "Catalogo (1).pdf", q["scade"][0], q["firma"][0], "utente")
    assert not valida("f1", "Catalogo (1).pdf", q["scade"][0], q["firma"][0], "altro"), \
        "la firma deve legare la persona"
    assert not valida("f1", "ALTRO.pdf", q["scade"][0], q["firma"][0], "utente"), \
        "la firma deve legare il documento"
    assert not valida("f1", "Catalogo (1).pdf", "0", q["firma"][0], "utente"), "scaduto"
    assert tipo("a.pdf") == "application/pdf"
    assert tipo("a.exe") == "application/octet-stream", "niente tipi che il browser apre"
    assert "#page=" not in firma_url("f1", "a.pdf", None, "https://x")
    print("documento: regole verdi")


if __name__ == "__main__":
    _prova()
