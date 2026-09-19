"""Verifica end-to-end delle cartelle per gruppo: dal pannello alla ricerca.

    ./.venv/Scripts/python.exe eval/verifica_cartelle.py

Richiede l'ambiente avviato. La cartella di prova Sviluppo/cartelle/luis/sicurezza
la prepara da se' se manca (PROVA, qui sotto). Controlla, in ordine:
  C  il pannello collega la cartella (e rifiuta percorsi e gruppi sbagliati)
  I  l'indicizzazione legge cio' che deve e salta bozze, archivio e fogli;
     doppioni e file cancellati si vedono
  G  il gate: in attesa nessuno trova nulla; attiva, la trova solo chi e' nel
     gruppo o nei gestori (stesso codice dell'orchestratore, recupero.py)
  P  il gestore aggiunge e toglie un collega dal pannello, e solo nel suo gruppo
La ricerca qui e' testuale: i vettori dipendono da LM Studio, le ACL no.
Lascia la cartella com'era; la fonte resta collegata e attiva.
"""
import base64
import json
import pathlib
import re
import shutil
import socket
import subprocess
import sys

import httpx
import psycopg

QUI = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(QUI))
from orchestratore import recupero  # noqa: E402

E = {m.group(1): m.group(2).split("#")[0].strip()
     for m in re.finditer(r"^([A-Z_]+)=(.*)$", (QUI / ".env").read_text(encoding="utf-8"), re.M)}
APP = f"https://{E['APP_HOST']}"
API = f"{APP}/amministrazione/api"
SSO = f"https://{E['SSO_HOST']}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/140.0"}
H = {"X-Richiesta": "1"}
FONTE, PERCORSO = "sicurezza-luis", "luis/sicurezza"
CARTELLA = QUI / "cartelle" / PERCORSO
_o = socket.getaddrinfo
socket.getaddrinfo = lambda h, p, *a, **k: _o(
    "127.0.0.1" if isinstance(h, str) and h.endswith(".localhost") else h, p, *a, **k)
esiti = []
db = psycopg.connect(f"postgresql://postgres:{E['POSTGRES_PASSWORD']}@localhost:55432/rag", autocommit=True)


def prova(nome, descrizione, ok, dettaglio=""):
    esiti.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {nome}  {descrizione}" + ("" if ok else f"\n        {dettaglio}"))


def login(utente):
    """Login OIDC completo come un browser (come verifica_amministrazione.py)."""
    c = httpx.Client(verify=False, follow_redirects=False, timeout=60, headers=UA)
    url = f"{APP}/amministrazione/"
    for _ in range(20):
        r = c.get(url)
        if r.status_code in (301, 302, 303):
            url = str(httpx.URL(url).join(r.headers["location"]))
            continue
        m = re.search(r'id="kc-form-login"[^>]*action="([^"]+)"', r.text)
        if r.status_code == 200 and m:
            r = c.post(m.group(1).replace("&amp;", "&"), data={
                "username": utente, "password": E["TEST_USER_PASSWORD"], "credentialId": ""})
            url = str(httpx.URL(url).join(r.headers["location"]))
            continue
        return c
    raise SystemExit(f"login di {utente} non riuscito")


def gruppi_di(utente):
    """I gruppi che il gate riceverebbe: il claim groups del token."""
    t = httpx.post(f"{SSO}/realms/azienda/protocol/openid-connect/token", verify=False, data={
        "grant_type": "password", "client_id": "test-token", "username": utente,
        "password": E["TEST_USER_PASSWORD"]}).json()["access_token"]
    p = t.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4))).get("groups") or []


def trova(utente, domanda):
    righe, _ = recupero.cerca(db, domanda, gruppi_di(utente), qvec=None, limite=10)
    return [r for r in righe if r["source_id"] == FONTE]


def indicizza():
    r = subprocess.run(["docker", "compose", "run", "--rm", "-T", "ingestion", "python", "indicizza.py", "--una-volta"],
                       cwd=QUI, capture_output=True, text=True, timeout=3600)
    if r.returncode:
        raise SystemExit("indicizzazione non riuscita:\n" + r.stdout[-2000:] + r.stderr[-2000:])
    return r.stdout


def documenti():
    return {r[0]: r[1] for r in db.execute("SELECT documento, stato FROM documenti WHERE source_id = %s", (FONTE,))}


def anomalia_aperta(prefisso):
    return db.execute("SELECT count(*) FROM anomalie WHERE impronta LIKE %s AND stato IN ('aperta','presa')",
                      (prefisso + "%",)).fetchone()[0]


CAMPIONE = QUI.parent / "documenti_test" / "campione"
PROVA = {   # file di prova: testo scritto qui, PDF presi dal campione
    "procedure-emergenza/piano-evacuazione.md":
        "# Piano di evacuazione (DOCUMENTO DI PROVA)\n\n## Allarme\nAl suono continuo della sirena tutti "
        "lasciano l'edificio dalle uscite di emergenza, senza usare l'ascensore.\n\n## Punto di raccolta\n"
        "Il punto di raccolta e' il **parcheggio nord**, accanto alla cabina elettrica. Il coordinatore "
        "dell'emergenza fa l'appello.\n",
    "schede-sicurezza/SDS-acetone.txt":
        "Scheda di sicurezza ACETONE (DOCUMENTO DI PROVA).\n\nPericoli: liquido e vapori facilmente "
        "infiammabili (H225). Provoca grave irritazione oculare (H319).\n\nConservazione: armadio per infiammabili.\n",
    "_bozze/nuova-procedura.txt": "bozza da non indicizzare: PAROLA-SEGRETA-BOZZA\n",
    "listino-dpi.xlsx": "non e' un vero foglio: non deve nemmeno essere aperto\n",
    "procedure-emergenza/MAN-004-istruzioni-gruppo-soccorso.pdf": "MAN-004-istruzioni-gruppo-soccorso.pdf",
    "formazione/PRO-004-procedura-formazione.pdf": "PRO-004-procedura-formazione.pdf",
    "formazione/copia-PRO-004.pdf": "PRO-004-procedura-formazione.pdf",
    "schede-sicurezza/PES-003-dichiarazione-foto.pdf": "PES-003-dichiarazione-foto.pdf",
}


def prepara_cartella():
    """La cartella di prova, se manca: sta fuori da git come i documenti veri."""
    for rel, contenuto in PROVA.items():
        dest = CARTELLA / rel
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(".pdf"):
            origine = CAMPIONE / contenuto
            if not origine.exists():
                raise SystemExit(f"manca {origine}: rigenerare il campione con documenti_test/genera_campione.py")
            shutil.copyfile(origine, dest)
        else:
            dest.write_text(contenuto, encoding="utf-8")


def main():
    prepara_cartella()
    # Si riparte puliti: la fonte e il suo indice (i chunk vanno via in cascata).
    db.execute("DELETE FROM sources WHERE id = %s", (FONTE,))

    print("\n--- Collegare la cartella dal pannello " + "-" * 30)
    sup = login("prova.super")
    tipi = {t["id"]: t["disponibile"] for t in sup.get(f"{API}/fonti/tipi").json()}
    r = sup.post(f"{API}/fonti", headers=H, json={"provenienza": "sharepoint", "id": "prova-sp", "descrizione": "x",
                                                  "gruppo": "sicurezza", "aziende": ["luis"], "percorso": "luis/x"})
    prova("C0", "catalogo dei tipi di fonte: la cartella c'e', un tipo non ancora pronto e' rifiutato",
          tipi.get("cartella") is True and tipi.get("sharepoint") is False and r.status_code == 422, (tipi, r.text))
    r = sup.post(f"{API}/fonti", headers=H, json={"id": "prova-unc", "descrizione": "x", "gruppo": "sicurezza",
                                                  "aziende": ["luis"], "percorso": "\\\\server\\qualita"})
    prova("C1", "un percorso di rete assoluto (\\\\server\\...) e' rifiutato", r.status_code == 422, r.text)
    r = sup.post(f"{API}/fonti", headers=H, json={"id": "prova-x", "descrizione": "x", "gruppo": "inesistente",
                                                  "aziende": ["luis"], "percorso": "luis/x"})
    prova("C2", "un gruppo che non esiste e' rifiutato", r.status_code == 422, r.text)
    r = login("prova.accessi").post(f"{API}/fonti", headers=H, json={
        "id": FONTE, "descrizione": "x", "gruppo": "sicurezza", "aziende": ["luis"], "percorso": PERCORSO})
    prova("C3", "Gestione accessi (fonti in sola lettura) non collega cartelle", r.status_code == 403, r.text)
    r = sup.post(f"{API}/fonti", headers=H, json={"id": FONTE, "descrizione": "Documenti della sicurezza",
                                                  "gruppo": "sicurezza", "aziende": ["luis"], "percorso": PERCORSO})
    d = r.json() if r.status_code == 200 else {}
    prova("C4", "la cartella si collega: la vedono gruppo e gestori, i gestori ne rispondono",
          r.status_code == 200 and d.get("gruppi") == ["sicurezza", "sicurezza-gestori"]
          and d.get("responsabile") == "sicurezza-gestori", r.text)
    stato = db.execute("SELECT stato FROM sources WHERE id = %s", (FONTE,)).fetchone()
    prova("C5", "nasce in attesa di approvazione", stato and stato[0] == "attesa", stato)

    print("\n--- Indicizzazione " + "-" * 50)
    print(indicizza().strip().splitlines()[-1][:300])
    doc = documenti()
    attesi = {"procedure-emergenza/piano-evacuazione.md", "schede-sicurezza/SDS-acetone.txt",
              "procedure-emergenza/MAN-004-istruzioni-gruppo-soccorso.pdf", "formazione/PRO-004-procedura-formazione.pdf",
              "formazione/copia-PRO-004.pdf"}
    prova("I1", "letti i documenti della cartella (Markdown, testo, PDF)",
          all(doc.get(x) == "indicizzato" for x in attesi), doc)
    prova("I2", "saltati _bozze e i fogli di calcolo",
          not any(k.startswith("_bozze/") or k.endswith(".xlsx") for k in doc)
          and not db.execute("SELECT 1 FROM chunks WHERE content LIKE '%%PAROLA-SEGRETA-BOZZA%%'").fetchone(), doc)
    prova("I3", "la scansione senza testo (PES-003) passa dall'OCR o risulta vuota, senza bloccare il resto",
          doc.get("schede-sicurezza/PES-003-dichiarazione-foto.pdf") in ("indicizzato", "vuoto"), doc)
    prova("I4", "la copia doppia di PRO-004 apre un'anomalia", anomalia_aperta(f"doppione:{FONTE}:") == 1)
    copia = CARTELLA / "formazione" / "copia-PRO-004.pdf"
    tenuta = copia.read_bytes()
    copia.unlink()
    try:
        indicizza()
        prova("I5", "un file cancellato esce dall'indice e il doppione si chiude",
              "formazione/copia-PRO-004.pdf" not in documenti() and anomalia_aperta(f"doppione:{FONTE}:") == 0
              and not db.execute("SELECT 1 FROM chunks WHERE source_id = %s AND documento = 'formazione/copia-PRO-004.pdf'",
                                 (FONTE,)).fetchone())
    finally:
        copia.write_bytes(tenuta)      # la cartella di prova torna com'era

    print("\n--- Il gate: chi trova i documenti " + "-" * 34)
    prova("G1", "fonte in attesa: nemmeno chi e' nel gruppo la trova", not trova("prova.sicurezza", "punto di raccolta"))
    r = sup.patch(f"{API}/fonti/{FONTE}", headers=H, json={
        "gruppi": ["sicurezza", "sicurezza-gestori"], "aziende": ["luis"], "owner": "sicurezza-gestori",
        "versione": "la cartella: solo versioni valide, _bozze e _archivio esclusi (decisione 64)", "stato": "attiva"})
    prova("G2", "il Superutente la attiva dal pannello", r.status_code == 200, r.text)
    righe = trova("prova.sicurezza", "punto di raccolta")
    prova("G3", "attiva: un addetto alla sicurezza trova il piano di evacuazione",
          any(x["documento"] == "procedure-emergenza/piano-evacuazione.md" for x in righe), righe)
    prova("G4", "il gestore (prova.rspp) trova la scheda dell'acetone", bool(trova("prova.rspp", "acetone infiammabili")))
    prova("G5", "un commerciale (prova.vendite) non trova nulla della cartella", not trova("prova.vendite", "punto di raccolta"))
    prova("G6", "la direzione, fuori dal gruppo, non trova nulla", not trova("prova.direzione", "acetone"))

    print("\n--- Il gestore dal pannello " + "-" * 41)
    rspp = login("prova.rspp")
    io = rspp.get(f"{API}/io").json()
    prova("P1", "il gestore entra nel pannello, con la sola voce 'I miei gruppi'",
          io.get("home") == "gestiti" and set(io.get("permessi", {})) == {"gestiti"}, io.get("permessi"))
    g = rspp.get(f"{API}/gestiti").json()
    prova("P2", "vede il suo gruppo e la sua cartella",
          [x["nome"] for x in g] == ["sicurezza"] and FONTE in [f["id"] for f in g[0]["cartelle"]], g)
    cand = rspp.get(f"{API}/gestiti/sicurezza/candidati", params={"q": "prova.vendite"}).json()
    vend = next((u for u in cand if u["username"] == "prova.vendite"), None)
    prova("P3", "cerca un collega da aggiungere", vend is not None, cand)
    r = rspp.post(f"{API}/gestiti/sicurezza/membri/{vend['id']}", headers=H)
    prova("P4", "lo aggiunge al gruppo sicurezza", r.status_code == 200, r.text)
    prova("P5", "il collega ora trova il piano di evacuazione", bool(trova("prova.vendite", "punto di raccolta")))
    r = rspp.delete(f"{API}/gestiti/sicurezza/membri/{vend['id']}", headers=H)
    prova("P6", "lo toglie, e il collega non trova piu' nulla",
          r.status_code == 200 and not trova("prova.vendite", "punto di raccolta"), r.text)
    r = rspp.post(f"{API}/gestiti/vendite/membri/{vend['id']}", headers=H)
    prova("P7", "non gestisce un gruppo che non e' suo", r.status_code == 403, r.text)
    prova("P8", "non vede le altre schermate (Utenti, Fonti)",
          rspp.get(f"{API}/utenti").status_code == 403 and rspp.get(f"{API}/fonti").status_code == 403)
    r = rspp.post(f"{API}/gestiti/sicurezza/membri/{vend['id']}", headers={})
    prova("P9", "senza l'header X-Richiesta la modifica e' rifiutata", r.status_code == 403, r.text)
    reg = db.execute("""SELECT count(*) FROM registro_modifiche WHERE chi = 'prova.rspp'
                         AND azione LIKE '%%prova.vendite%%gruppo sicurezza%%'""").fetchone()[0]
    prova("P10", "aggiunta e rimozione sono nel registro", reg >= 2, reg)

    print(f"\n{sum(esiti)}/{len(esiti)} passati")
    sys.exit(0 if all(esiti) else 1)


if __name__ == "__main__":
    main()
