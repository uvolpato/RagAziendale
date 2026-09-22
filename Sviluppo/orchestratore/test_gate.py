"""I test che contano piu' di tutti gli altri.

    py -m orchestratore.test_gate     (da Sviluppo/, con l'ambiente su)

Non sono unit test di cortesia: T1.8 e T1.14 sono l'artefatto di conformita'.
Sono scritti per essere leggibili come specifica da chi non li ha scritti —
il codice di sicurezza non puo' restare letto da una sola persona.

Richiedono l'ambiente avviato: py avvia.py
"""
import os
import sys
import urllib.parse
import urllib.request
import json
import re
import pathlib

# --- configurazione per l'esecuzione dall'host (fuori dai container) -------
QUI = pathlib.Path(__file__).parent.parent


def da_env(chiave, default=None):
    for riga in (QUI / ".env").read_text(encoding="utf-8").splitlines():
        m = re.match(rf"^{chiave}=(.*)$", riga)
        if m:
            return m.group(1).split("#")[0].strip() or default
    return default


os.environ.setdefault("DATABASE_URL",
    f"postgresql://postgres:{da_env('POSTGRES_PASSWORD')}@localhost:55432/rag")
os.environ.setdefault("OIDC_JWKS_URL",
    "http://localhost:8081/realms/azienda/protocol/openid-connect/certs")
os.environ.setdefault("OIDC_ISSUER", "https://sso.localhost/realms/azienda")
os.environ.setdefault("OIDC_AUDIENCE", "orchestratore")
os.environ.setdefault("EGRESS_INTERNAL", "host.docker.internal,localhost,keycloak")
os.environ.setdefault("EGRESS_EXTERNAL", "api.provider-esempio.com")
# Serve a T1.21: e' la chiave con cui si firmano gli URL delle immagini.
os.environ.setdefault("ORCHESTRATOR_KEY", da_env("ORCHESTRATOR_KEY") or "prova")

import psycopg                                    # noqa: E402
from orchestratore import egress, gate, identita, recupero   # noqa: E402

KEYCLOAK = "http://localhost:8081"
esiti = []
_conn = None


def prova(nome, descrizione):
    """Esegue un test e ne riporta l'esito.

    Il rollback dopo ogni test non e' cosmetico: senza, un errore SQL lascia
    la transazione abortita e TUTTI i test successivi falliscono con
    "current transaction is aborted", nascondendo il difetto vero dietro
    una cascata di errori finti.
    """
    def deco(f):
        try:
            f()
            esiti.append((nome, True, descrizione, ""))
            print(f"  PASS  {nome}  {descrizione}")
        except AssertionError as e:
            esiti.append((nome, False, descrizione, str(e)))
            print(f"  FAIL  {nome}  {descrizione}\n        {e}")
        except Exception as e:
            esiti.append((nome, False, descrizione, f"{type(e).__name__}: {e}"))
            print(f"  ERR   {nome}  {descrizione}\n        {type(e).__name__}: {e}")
        finally:
            if _conn is not None:
                _conn.rollback()
        return f
    return deco


def token_di(utente):
    dati = urllib.parse.urlencode({
        "grant_type": "password", "client_id": "test-token",
        "username": utente, "password": da_env("TEST_USER_PASSWORD"),
    }).encode()
    url = f"{KEYCLOAK}/realms/azienda/protocol/openid-connect/token"
    with urllib.request.urlopen(url, data=dati, timeout=20) as r:
        return json.load(r)["access_token"]


# ===========================================================================
# Dati di prova: due sorgenti con ACL e residenza diverse.
# ===========================================================================
SEMI = """
INSERT INTO sources (id, descrizione, percorso, acl_groups, residency, owner,
                     approvato_da, approvato_il, stato, versione_autoritativa, aziende)
VALUES ('t-manuali', 'Manuali (prova)', '/prova/manuali', '{tutti}',
        'cloud_ok', 'uff.tecnico', 'direzione', now(), 'attiva', 'v1', '{luis}'),
       ('t-listini', 'Listini (prova)', '/prova/listini', '{vendite,direzione}',
        'interno', 'commerciale', NULL, NULL, 'attiva', 'v1', '{luis}'),
       -- Aperta a tutti i gruppi, ma SOSPESA: non deve rispondere (T1.15).
       ('t-sospesa', 'Vecchia (prova)', '/prova/vecchia', '{tutti}',
        'interno', 'uff.tecnico', NULL, NULL, 'sospesa', 'v0', '{luis}'),
       -- Aperta a tutti i gruppi, ma di un'ALTRA azienda (T1.16).
       ('t-altra', 'Altra azienda (prova)', '/prova/altra', '{tutti}',
        'interno', 'uff.tecnico', NULL, NULL, 'attiva', 'v1', '{decobrands}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO chunks (source_id, documento, page, content, content_hash)
VALUES ('t-manuali', 'manuale.pdf', 12,
        'La garanzia della serie X dura 24 mesi dalla consegna.', 'h1'),
       ('t-listini', 'listino.pdf', 3,
        'Sconto riservato del 18 percento sulla serie X per il cliente.', 'h2'),
       ('t-sospesa', 'vecchio.pdf', 1,
        'La garanzia della serie X durava 12 mesi (edizione superata).', 'h3'),
       ('t-altra', 'altra.pdf', 1,
        'Garanzia della serie X presso l altra azienda: 36 mesi.', 'h4')
ON CONFLICT DO NOTHING;
"""


def semina(conn):
    with conn.cursor() as cur:
        cur.execute(SEMI)
    conn.commit()


def pulisci(conn):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM conversation_taint WHERE conversation_id LIKE 'test-%'")
        cur.execute("DELETE FROM sources WHERE id LIKE 't-%'")   # CASCADE sui chunk
    conn.commit()


def main():
    global _conn
    conn = _conn = psycopg.connect(os.environ["DATABASE_URL"])
    pulisci(conn)
    semina(conn)

    print("\n--- Identita' (T1.6, T1.7b) " + "-" * 40)

    @prova("T1.7b", "header Authorization vuoto -> rifiutato, nessun utente di default")
    def _():
        for valore in [None, "", "   ", "Bearer", "Bearer   ", "Basic abc"]:
            try:
                identita.verifica(valore)
                raise AssertionError(f"accettato header non valido: {valore!r}")
            except identita.TokenNonValido:
                pass

    @prova("T1.6a", "firma alterata -> rifiutato")
    def _():
        t = token_di("prova.vendite")
        testa, corpo, firma = t.split(".")
        alterato = f"{testa}.{corpo}.{firma[:-4]}AAAA"
        try:
            identita.verifica(f"Bearer {alterato}")
            raise AssertionError("token con firma alterata accettato")
        except identita.TokenNonValido:
            pass

    @prova("T1.6b", "audience sbagliata -> rifiutato")
    def _():
        t = token_di("prova.vendite")
        atteso = os.environ["OIDC_AUDIENCE"]
        os.environ["OIDC_AUDIENCE"] = "qualcun-altro"
        try:
            identita.verifica(f"Bearer {t}")
            raise AssertionError("token con audience sbagliata accettato")
        except identita.TokenNonValido:
            pass
        finally:
            os.environ["OIDC_AUDIENCE"] = atteso

    @prova("T1.6c", "token valido -> accettato, gruppi estratti")
    def _():
        claim = identita.verifica(f"Bearer {token_di('prova.vendite')}")
        g = set(identita.gruppi(claim))
        assert g == {"tutti", "vendite", "azienda-luis"}, f"gruppi inattesi: {g}"
        assert identita.aziende(list(g)) == ["luis"]

    print("\n--- ACL (T1.14) " + "-" * 52)

    @prova("T1.14", "stessa domanda, due utenti: il filtro ACL agisce nella query SQL")
    def _():
        domanda = "sconto serie X"
        magazzino = identita.gruppi(identita.verifica(f"Bearer {token_di('prova.magazzino')}"))
        vendite = identita.gruppi(identita.verifica(f"Bearer {token_di('prova.vendite')}"))

        r_mag, _ = recupero.cerca(conn, domanda, magazzino)
        r_ven, _ = recupero.cerca(conn, domanda, vendite)

        fonti_mag = {r["source_id"] for r in r_mag}
        fonti_ven = {r["source_id"] for r in r_ven}

        assert "t-listini" not in fonti_mag, \
            f"il magazzino vede il listino riservato: {fonti_mag}"
        assert "t-listini" in fonti_ven, \
            f"vendite NON vede il listino che dovrebbe vedere: {fonti_ven}"

    @prova("T1.25", "gli altri modi di guardare rispettano le ACL come la ricerca")
    def _():
        """Ricerca esatta, pagina e elenco documenti sono strumenti che
        chiamera' un AGENTE: se uno dei tre dimentica i permessi, il ciclo li
        aggira senza che nessuno se ne accorga. Stessa prova della T1.14, sugli
        strumenti nuovi."""
        magazzino = identita.gruppi(identita.verifica(f"Bearer {token_di('prova.magazzino')}"))
        vendite = identita.gruppi(identita.verifica(f"Bearer {token_di('prova.vendite')}"))

        # 1. ricerca esatta: la parola c'e', ma non per tutti.
        mag = recupero.cerca_esatta(conn, "Sconto riservato", magazzino)
        ven = recupero.cerca_esatta(conn, "Sconto riservato", vendite)
        assert mag == [], f"il magazzino trova il listino con la ricerca esatta: {mag}"
        assert any(r["source_id"] == "t-listini" for r in ven), "vendite NON trova il listino"

        # 2. pagina: conoscere documento e pagina non deve bastare.
        assert recupero.pagina(conn, "listino.pdf", 3, magazzino) == [], \
            "la pagina si apre anche senza permesso"
        assert recupero.pagina(conn, "listino.pdf", 3, vendite), "vendite non apre la sua pagina"

        # 3. elenco: non deve rivelare nemmeno i NOMI dei documenti altrui.
        visti = {r["documento"] for r in recupero.documenti_visibili(conn, magazzino)}
        assert "listino.pdf" not in visti, f"l'elenco rivela un documento non consentito: {visti}"
        assert "manuale.pdf" in visti, f"l'elenco non mostra quello consentito: {visti}"

        # 4. senza gruppi, tutti e tre non rispondono.
        assert recupero.cerca_esatta(conn, "garanzia", []) == []
        assert recupero.pagina(conn, "manuale.pdf", 12, []) == []
        assert recupero.documenti_visibili(conn, []) == []

        # 5. fonte SOSPESA e fonte di un'ALTRA azienda restano fuori (T1.15/T1.16).
        tutti = ["tutti", "azienda-luis"]
        fonti = {r["source_id"] for r in recupero.cerca_esatta(conn, "garanzia", tutti, limite=50)}
        assert "t-sospesa" not in fonti, f"una fonte sospesa risponde: {fonti}"
        assert "t-altra" not in fonti, f"una fonte di un'altra azienda risponde: {fonti}"

    @prova("T1.26", "la ricerca esatta cerca testo, non un motivo con i jolly")
    def _():
        """`%` dentro il termine non deve diventare «qualsiasi cosa»: senza
        neutralizzarlo, cercare «100%» restituirebbe tutto il consentito."""
        tutti = ["tutti", "azienda-luis"]
        assert recupero.cerca_esatta(conn, "%", tutti) == [], "il jolly di LIKE passa"
        assert recupero.cerca_esatta(conn, "_", tutti) == [], "il jolly di un carattere passa"
        assert recupero.cerca_esatta(conn, "   ", tutti) == [], "termine vuoto"
        # e una ricerca vera continua a funzionare, maiuscole ignorate
        assert any("garanzia" in r["content"].lower()
                   for r in recupero.cerca_esatta(conn, "GARANZIA della serie X", tutti))

    @prova("T1.14b", "utente senza gruppi -> nessun risultato")
    def _():
        righe, _ = recupero.cerca(conn, "garanzia", [])
        assert righe == [], f"utente senza gruppi ha ottenuto {len(righe)} chunk"

    print("\n--- Stato e aziende (T1.15-T1.17, decisione 67) " + "-" * 20)

    @prova("T1.15", "fonte sospesa -> non risponde, anche se il gruppo la vede")
    def _():
        righe, _ = recupero.cerca(conn, "garanzia serie X", ["tutti", "azienda-luis"])
        fonti = {r["source_id"] for r in righe}
        assert "t-sospesa" not in fonti, f"una fonte sospesa ha risposto: {fonti}"
        assert "t-manuali" in fonti, f"la fonte attiva non risponde: {fonti}"

    @prova("T1.16", "fonte di un'altra azienda -> invisibile, anche con il gruppo giusto")
    def _():
        luis, _ = recupero.cerca(conn, "garanzia serie X", ["tutti", "azienda-luis"])
        deco, _ = recupero.cerca(conn, "garanzia serie X", ["tutti", "azienda-decobrands"])
        assert "t-altra" not in {r["source_id"] for r in luis}, "Luis vede i dati dell'altra azienda"
        assert {r["source_id"] for r in deco} == {"t-altra"}, f"Decobrands vede: {deco}"

    @prova("T1.17", "utente senza aziende -> nessun risultato, anche con i gruppi")
    def _():
        righe, _ = recupero.cerca(conn, "garanzia serie X", ["tutti", "vendite", "direzione"])
        assert righe == [], f"utente senza aziende ha ottenuto {len(righe)} chunk"

    print("\n--- Gate e contaminazione (T1.8, T1.9, T1.10) " + "-" * 22)

    @prova("T1.10", "turno interno senza rotta interna -> rifiuto esplicito, non risposta parziale")
    def _():
        os.environ.pop("LLM_RAGIONAMENTO_INTERNO", None)
        vendite = ["tutti", "vendite", "azienda-luis"]
        righe, _ = recupero.cerca(conn, "sconto serie X", vendite)
        assert any(r["residency"] == "interno" for r in righe), "il seme non contiene fonti interne"
        try:
            gate.applica(conn, "test-conv-1", righe)
            raise AssertionError("il gate ha lasciato passare un turno interno")
        except gate.RispostaRifiutata:
            pass

    @prova("T1.9", "la contaminazione e' della conversazione: persiste sui turni successivi")
    def _():
        # Turno successivo, domanda del tutto innocua, zero fonti interne.
        righe_pulite = [{"source_id": "t-manuali", "residency": "cloud_ok"}]
        try:
            gate.applica(conn, "test-conv-1", righe_pulite)
            raise AssertionError("la conversazione contaminata e' tornata pulita")
        except gate.RispostaRifiutata:
            pass
        assert gate.leggi(conn, "test-conv-1"), "taint non persistito a database"

    @prova("T1.8", "turno interno: nessuna richiesta verso host esterni")
    def _():
        os.environ.pop("LLM_RAGIONAMENTO_INTERNO", None)
        with egress.registra_destinazioni() as visti:
            righe, _ = recupero.cerca(conn, "sconto serie X", ["tutti", "vendite", "azienda-luis"])
            try:
                gate.applica(conn, "test-conv-2", righe)
            except gate.RispostaRifiutata:
                pass
            # Anche forzando una chiamata esterna, la guardia la blocca.
            g = egress.GuardiaTransport()
            try:
                g.controlla("https://api.provider-esempio.com/v1/chat/completions")
                raise AssertionError("chiamata esterna consentita in turno interno")
            except egress.EgressVietato:
                pass
        esterni = [h for h in visti if h == "api.provider-esempio.com"]
        assert esterni == ["api.provider-esempio.com"], \
            "la destinazione esterna non e' stata registrata"
        # Registrata come tentativo, ma bloccata prima di partire.

    @prova("T1.8b", "host non dichiarato -> errore anche su turno pulito")
    def _():
        egress.turno_interno.set(False)
        g = egress.GuardiaTransport()
        try:
            g.controlla("https://telemetria-sconosciuta.example/raccolta")
            raise AssertionError("host non dichiarato consentito")
        except egress.EgressNonDichiarato:
            pass

    @prova("T1.8c", "turno pulito: gli host esterni dichiarati sono consentiti")
    def _():
        egress.turno_interno.set(False)
        g = egress.GuardiaTransport()
        g.controlla("https://api.provider-esempio.com/v1/chat/completions")
        g.controlla("http://host.docker.internal:1234/v1/embeddings")

    print("\n--- Degrado (T1.3c) " + "-" * 48)

    @prova("T1.3c", "embedding non disponibile -> ricerca degradata su full-text, non errore")
    def _():
        righe, degradato = recupero.cerca(conn, "garanzia serie X",
                                          ["tutti", "azienda-luis"], qvec=None)
        assert degradato is True, "il degrado non e' segnalato"
        assert len(righe) >= 1, "il full-text non ha trovato nulla"
        assert righe[0]["source_id"] == "t-manuali"

    print("\n--- Immagini: firma e permesso attuale " + "-" * 29)

    @prova("T1.21", "l'immagine si serve solo con firma valida E permesso ancora valido")
    def _():
        from orchestratore import immagini
        from orchestratore.main import _gruppi_noti, _ricorda_gruppi
        import psycopg.rows
        c = psycopg.connect(os.environ["DATABASE_URL"], row_factory=psycopg.rows.dict_row)
        try:
            with c.cursor() as cur:
                cur.execute("""INSERT INTO immagini (source_id, documento, page, percorso)
                               VALUES ('t-listini', 'listino.pdf', 3, 't-prova/x.png')
                               ON CONFLICT (source_id, percorso) DO UPDATE SET page = 3
                               RETURNING id""")
                img = cur.fetchone()["id"]
            c.commit()

            # La firma e' legata alla persona: cambiando utente non vale piu'.
            url = immagini.firma_url(img, "https://x", "utente-a")
            scade = url.split("scade=")[1].split("&")[0]
            firma = url.split("firma=")[1].split("&")[0]
            assert immagini.valida(img, scade, firma, "utente-a")
            assert not immagini.valida(img, scade, firma, "utente-b"), \
                "un collegamento inoltrato non deve valere per un altro utente"
            assert not immagini.valida(img, scade, "0" * 32, "utente-a")

            # Il permesso si rilegge adesso: t-listini e' di vendite/direzione.
            _ricorda_gruppi(c, "utente-a", ["vendite", "azienda-luis"])
            _ricorda_gruppi(c, "utente-b", ["magazzino", "azienda-luis"])
            assert immagini.visibile(c, img, _gruppi_noti(c, "utente-a")), \
                "chi ha il gruppo giusto deve vedere la figura"
            assert not immagini.visibile(c, img, _gruppi_noti(c, "utente-b")), \
                "chi non ha il gruppo non deve vederla, firma o no"

            # Fonte sospesa: l'immagine smette di essere servita SUBITO.
            with c.cursor() as cur:
                cur.execute("UPDATE sources SET stato = 'sospesa' WHERE id = 't-listini'")
            c.commit()
            assert not immagini.visibile(c, img, _gruppi_noti(c, "utente-a")), \
                "una fonte sospesa non serve piu' le sue immagini"
        finally:
            with c.cursor() as cur:
                cur.execute("UPDATE sources SET stato = 'attiva' WHERE id = 't-listini'")
                cur.execute("DELETE FROM gruppi_utente WHERE utente IN ('utente-a','utente-b')")
            c.commit()
            c.close()


    print("\n--- Immagini su richiesta " + "-" * 43)

    @prova("T1.19", "le immagini si mostrano solo a chi dice di si' a un'offerta fatta davvero")
    def _():
        from orchestratore.main import MARCA_OFFERTA, vuole_le_immagini
        offerta = f"Risposta... _Ci sono 3 {MARCA_OFFERTA}: scrivi «mostra» se vuoi vederle._"
        for si in ("si", "sì", "mostra", "mostrale", "fammi vedere", "ok", "Certo", "volentieri"):
            assert vuole_le_immagini(si, offerta), f"«{si}» dopo l'offerta doveva mostrare le immagini"
        # Domande vere: non sono un consenso, nemmeno se parlano di immagini.
        for no in ("quali immagini ci sono nel catalogo dei diffusori?",
                   "mostrami tutti i documenti sulla sicurezza informatica del 2025 per favore",
                   "e le misure?"):
            assert not vuole_le_immagini(no, offerta), f"«{no}» e' una domanda, non un si'"
        # Senza offerta nel turno prima, "si" resta una risposta qualsiasi.
        assert not vuole_le_immagini("si", "Una risposta senza immagini collegate.")
        assert not vuole_le_immagini("mostra", "")

    @prova("T1.20", "la cronologia rimandata al modello non contiene le righe aggiunte dal sistema")
    def _():
        from orchestratore.main import MARCA_OFFERTA, _senza_aggiunte
        risposta = ("I diffusori IPURO CLASSIC sono adatti all'auto [4].\n\n"
                    "_Ci sono 4 " + MARCA_OFFERTA + ": scrivi \u00abmostra\u00bb._\n"
                    "![immagine 1](https://assistente.localhost/immagini/1?firma=x)")
        pulita = _senza_aggiunte({"role": "assistant", "content": risposta})["content"]
        assert "IPURO CLASSIC" in pulita, "la risposta vera non deve sparire"
        assert MARCA_OFFERTA not in pulita, "l\u2019offerta rimandata indietro fa imitare la riga al modello"
        assert "![immagine" not in pulita, "i collegamenti li aggiunge il sistema, non il modello"
        utente = {"role": "user", "content": "ne ho bisogno in auto"}
        assert _senza_aggiunte(utente) == utente, "le domande dell\u2019utente non si toccano"

    @prova("T1.22", "l'elenco delle fonti non torna indietro al modello, che altrimenti lo ricopia")
    def _():
        from orchestratore.main import _fonti_citate, _senza_aggiunte, MARCA_FONTI
        righe = [{"documento": "CATALOGO IPURO 2025.pdf", "page": 19},
                 {"documento": "CATALOGO IPURO 2025.pdf", "page": 20}]
        risposta = "Le candele profumate sono a pagina 20 [2]." + _fonti_citate(righe)
        pulita = _senza_aggiunte({"role": "assistant", "content": risposta})["content"]
        assert "candele profumate" in pulita, "la risposta vera non deve sparire"
        assert MARCA_FONTI not in pulita, "il 21/09/2026 il modello ricopiava l\u2019elenco del turno prima"
        assert not pulita.rstrip().endswith("---"), "il filetto resta orfano dell\u2019elenco"

    @prova("T1.24", "le figure stanno quattro per riga e l'impalcatura non torna al modello")
    def _():
        from orchestratore.main import PER_RIGA, _blocco_immagini, _senza_aggiunte
        blocco = _blocco_immagini(
            {n: (f"https://x/immagini/{n}?firma=y", f"COD{n} rosso") for n in range(1, 5)})
        corpo = [r for r in blocco.splitlines() if "![immagine" in r]
        assert len(corpo) == 1, f"quattro figure stanno su una riga sola: {blocco}"
        assert corpo[0].count("![immagine") == PER_RIGA, corpo[0]
        # LibreChat mette display:block su ogni img: senza tabella si impilano.
        assert blocco.splitlines()[1].startswith("|---"), "serve la riga di separazione, o non e' una tabella"
        # La didascalia sta SOTTO la sua miniatura, nella riga dopo: serve a
        # dire di che prodotto e' la figura, che il testo della risposta da
        # solo non garantisce (22/09/2026: quattro figure per due prodotti).
        sotto = blocco.splitlines()[blocco.splitlines().index(corpo[0]) + 1]
        assert sotto.count("<sub>") == PER_RIGA, f"manca la riga delle didascalie: {blocco}"
        assert "COD3 rosso" in sotto and sotto.index("COD1") < sotto.index("COD3"), sotto
        # Senza didascalie la riga non si aggiunge: niente celle vuote inutili.
        nude = _blocco_immagini({n: (f"https://x/{n}", "") for n in range(1, 3)})
        assert "<sub>" not in nude, nude
        pulita = _senza_aggiunte({"role": "assistant", "content": "Ecco i vasi.\n\n" + blocco})["content"]
        assert pulita.strip() == "Ecco i vasi.", f"non deve restare impalcatura: {pulita!r}"
        # Una tabella scritta dal MODELLO ha testo nelle celle: non si tocca.
        tabella = "Ecco i vasi.\n\n| Codice | Colore |\n|---|---|\n| DST2001 | rosso |"
        assert _senza_aggiunte({"role": "assistant", "content": tabella})["content"] == tabella

    @prova("T1.23", "le fonti sulla stessa pagina si raggruppano invece di ripetersi")
    def _():
        from orchestratore.main import _fonti_citate
        # Su un catalogo un prodotto occupa testo, tabella e descrizione della
        # figura: la stessa pagina arriva piu' volte.
        righe = [{"documento": "CATALOGO IPURO 2025.pdf", "page": 20},
                 {"documento": "CATALOGO IPURO 2025.pdf", "page": 20},
                 {"documento": "CATALOGO IPURO 2025.pdf", "page": 20},
                 {"documento": "CATALOGO IPURO 2025.pdf", "page": 6}]
        elenco = _fonti_citate(righe)
        assert elenco.count("CATALOGO IPURO 2025.pdf") == 2, f"una voce per pagina: {elenco}"
        assert "[1][2][3] CATALOGO IPURO 2025.pdf, pagina 20" in elenco, elenco
        assert "[4] CATALOGO IPURO 2025.pdf, pagina 6" in elenco, elenco


    @prova("T1.27", "le figure vengono dalle pagine che hanno risposto, non da tutto il documento")
    def _():
        """Restringere al DOCUMENTO non restringe niente quando la risposta
        viene da un catalogo solo. Il 22/09/2026 alla domanda «sassi rossi»,
        con il testo che citava le pagine 4, 6 e 7, sono uscite sfere di
        acciaio (pagina 31), ciottoli di fiume (63) e stelline natalizie (92)
        — sotto la frase «le figure delle pagine citate»."""
        import psycopg.rows
        c = psycopg.connect(os.environ["DATABASE_URL"], row_factory=psycopg.rows.dict_row)
        try:
            with c.cursor() as cur:
                cur.execute("""INSERT INTO immagini (source_id, documento, page, percorso, descrizione, embedding)
                               VALUES ('t-manuali', 'manuale.pdf', 12, 't-p/giusta.png',
                                       'la figura della pagina che ha risposto', %s),
                                      ('t-manuali', 'manuale.pdf', 99, 't-p/lontana.png',
                                       'una figura di un altra pagina dello stesso documento', %s)
                               ON CONFLICT (source_id, percorso) DO UPDATE
                                 SET page = EXCLUDED.page, embedding = EXCLUDED.embedding""",
                            ([0.1] * 1024, [0.1] * 1024))
            c.commit()
            gruppi = ["tutti", "azienda-luis"]
            qvec = [0.1] * 1024
            tutte = recupero.immagini_pertinenti(c, qvec, gruppi, ["manuale.pdf"], 10)
            assert {r["page"] for r in tutte} >= {12, 99}, "senza filtro si vede tutto il documento"
            solo12 = recupero.immagini_pertinenti(c, qvec, gruppi, ["manuale.pdf"], 10, pagine=[12])
            assert {r["page"] for r in solo12} == {12},                 f"la figura di un altra pagina non deve uscire: {[r['page'] for r in solo12]}"
            # La catena INTERA, non i pezzi: il 22/09/2026 una riga rimasta
            # appesa a una funzione cancellata ha fatto rispondere 500 a ogni
            # domanda, e nessun test se n'e' accorto perche' tutti chiamavano
            # le parti una per una. Qui si percorre quello che percorre la chat.
            from orchestratore.main import _immagini_per_la_domanda
            righe_finte = [{"documento": "manuale.pdf", "page": 12, "id": 1}]
            scelte = _immagini_per_la_domanda(c, qvec, gruppi, righe_finte, "la figura")
            assert isinstance(scelte, list) and scelte, f"nessuna figura scelta: {scelte}"
            for voce in scelte:
                assert isinstance(voce, tuple) and len(voce) == 2, f"serve (id, didascalia): {voce}"
            # QUANTE: mai un numero fisso, ma nemmeno tutte. Il 22/09/2026, a
            # «sassi rossi», tutte le candidate avevano la stessa rarita' e
            # ne uscivano 48: una rarita' uguale per tutti non distingue
            # niente, quindi e' il caso incerto e vale il tetto.
            from orchestratore.main import CAMPIONE, MAX_IMMAGINI, _scelte
            def finta(i, rarita, distanza):
                return {"id": i, "documento": "d", "page": i,
                        "rarita": rarita, "distanza": distanza}
            pari = [finta(i, 8.0, 0.54 + i / 1000) for i in range(30)]
            assert len(_scelte(pari)) <= CAMPIONE, "tutte pari: vale il tetto del campione"
            distingue = [finta(0, 7.3, 0.42)] + [finta(i, 0.0, 0.30) for i in range(1, 9)]
            assert [r["id"] for r in _scelte(distingue)] == [0], "la rarita' che distingue decide da sola"
            assert len(_scelte([finta(i, 3.0, 0.4) for i in range(40)])) <= MAX_IMMAGINI
        finally:
            with c.cursor() as cur:
                cur.execute("DELETE FROM immagini WHERE percorso LIKE 't-p/%%'")
            c.commit()
            c.close()

    print("\n--- Indicizzazione che scarica il modello " + "-" * 27)

    @prova("T1.18", "mentre l'indicizzazione tiene il lock, la chat risponde senza chiamare il modello")
    def _():
        from orchestratore import main as orch
        import psycopg.rows
        letto = psycopg.connect(os.environ["DATABASE_URL"], row_factory=psycopg.rows.dict_row)
        indicizzazione = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
        try:
            assert not orch._indice_in_aggiornamento(letto), "il lock risulta preso senza che nessuno lo abbia preso"
            indicizzazione.execute("SELECT pg_advisory_lock(%s)", (orch.BLOCCO_LLM,))
            assert orch._indice_in_aggiornamento(letto), "il lock preso non viene visto: la chat ricaricherebbe il modello"
            # Il messaggio non deve dire QUALI documenti: lo legge chiunque.
            testo = orch.INDICE_IN_AGGIORNAMENTO
            assert "indice" in testo.lower() and not any(c in testo for c in (".pdf", "/", "pagina")), testo
            indicizzazione.close()                 # il lock e' della connessione: muore con lei
            assert not orch._indice_in_aggiornamento(letto), "il lock resta preso dopo la chiusura: chat muta per sempre"
        finally:
            indicizzazione.close()
            letto.close()

    pulisci(conn)
    conn.close()

    print("\n" + "=" * 72)
    falliti = [e for e in esiti if not e[1]]
    print(f"{len(esiti) - len(falliti)}/{len(esiti)} passati")
    if falliti:
        for nome, _, desc, err in falliti:
            print(f"  FAIL {nome}: {err}")
        sys.exit(1)
    print("Gate, ACL ed egress: verdi.")


if __name__ == "__main__":
    main()
