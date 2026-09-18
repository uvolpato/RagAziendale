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
                     approvato_da, approvato_il)
VALUES ('t-manuali', 'Manuali (prova)', '/prova/manuali', '{tutti}',
        'cloud_ok', 'uff.tecnico', 'direzione', now()),
       ('t-listini', 'Listini (prova)', '/prova/listini', '{vendite,direzione}',
        'interno', 'commerciale', NULL, NULL)
ON CONFLICT (id) DO NOTHING;

INSERT INTO chunks (source_id, documento, page, content, content_hash)
VALUES ('t-manuali', 'manuale.pdf', 12,
        'La garanzia della serie X dura 24 mesi dalla consegna.', 'h1'),
       ('t-listini', 'listino.pdf', 3,
        'Sconto riservato del 18 percento sulla serie X per il cliente.', 'h2')
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
        assert g == {"tutti", "vendite"}, f"gruppi inattesi: {g}"

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

    @prova("T1.14b", "utente senza gruppi -> nessun risultato")
    def _():
        righe, _ = recupero.cerca(conn, "garanzia", [])
        assert righe == [], f"utente senza gruppi ha ottenuto {len(righe)} chunk"

    print("\n--- Gate e contaminazione (T1.8, T1.9, T1.10) " + "-" * 22)

    @prova("T1.10", "turno interno senza rotta interna -> rifiuto esplicito, non risposta parziale")
    def _():
        os.environ.pop("LLM_RAGIONAMENTO_INTERNO", None)
        vendite = ["tutti", "vendite"]
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
            righe, _ = recupero.cerca(conn, "sconto serie X", ["tutti", "vendite"])
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
                                          ["tutti"], qvec=None)
        assert degradato is True, "il degrado non e' segnalato"
        assert len(righe) >= 1, "il full-text non ha trovato nulla"
        assert righe[0]["source_id"] == "t-manuali"

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
