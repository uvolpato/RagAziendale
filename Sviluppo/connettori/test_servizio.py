"""Test del servizio connettori (la parte di sicurezza del pannello).

    ./.venv/Scripts/python.exe -m connettori.test_servizio   (da Sviluppo/, ambiente su)

Verifica il contratto della decisione 70: il segreto non torna mai indietro,
nemmeno cifrato; l'accesso e' autenticato; la prova di un collegamento funziona
e aggiorna lo stato. Usa il connettore `prova` (in memoria), non serve Integra.
"""
import os
import pathlib
import re

import psycopg
from psycopg.rows import dict_row

QUI = pathlib.Path(__file__).resolve().parent.parent
E = {m.group(1): m.group(2).split("#")[0].strip()
     for m in re.finditer(r"^([A-Z_]+)=(.*)$", (QUI / ".env").read_text(encoding="utf-8"), re.M)}

os.environ["DATABASE_URL"] = f"postgresql://postgres:{E['POSTGRES_PASSWORD']}@localhost:55432/rag"
os.environ.setdefault("CHIAVE_CREDENZIALI", "chiave-di-sviluppo-per-i-test")
os.environ.setdefault("CONNETTORI_KEY", "chiave-interna-di-prova")

from fastapi.testclient import TestClient                    # noqa: E402
from connettori import servizio                              # noqa: E402

AUTH = {"Authorization": "Bearer chiave-interna-di-prova"}
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


def main():
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM aziende WHERE codice LIKE 'conn-t%'")
        conn.execute("DELETE FROM collegamenti WHERE id LIKE 'conn-test-%'")

    with TestClient(servizio.app) as client:

        @prova("V1", "senza token -> 401")
        def _():
            assert client.get("/v1/collegamenti").status_code == 401

        @prova("V2", "creazione: il segreto non torna indietro, nemmeno cifrato")
        def _():
            r = client.post("/v1/collegamenti", headers=AUTH, json={
                "nome": "Integra prova", "tipo": "prova",
                "parametri": {"host": "192.168.1.41"},
                "segreti": {"password": "s3gr3to"}})
            assert r.status_code == 200, r.text
            corpo = r.json()
            assert "segreti" not in corpo and "segreti_impostati" in corpo
            assert corpo["segreti_impostati"] is True and corpo["id"] == "integra-prova"

        @prova("V3", "il blob nel database non contiene il segreto in chiaro")
        def _():
            with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
                blob = conn.execute("SELECT segreti FROM collegamenti WHERE id='integra-prova'").fetchone()[0]
            assert b"s3gr3to" not in bytes(blob) and b"password" not in bytes(blob)

        @prova("V4", "prova: ok e stato aggiornato a 'funzionante'")
        def _():
            r = client.post("/v1/collegamenti/integra-prova/prova", headers=AUTH)
            assert r.status_code == 200, r.text
            assert r.json()["stato"] == "funzionante"
            assert r.json()["prova"]["ok"] is True

        @prova("V5", "elenco aziende del gestionale (per l'abbinamento)")
        def _():
            r = client.get("/v1/collegamenti/integra-prova/aziende", headers=AUTH)
            assert r.status_code == 200 and r.json()[0]["codice"] == "001"

        @prova("V6", "catalogo dei connettori dal codice")
        def _():
            r = client.get("/v1/connettori", headers=AUTH)
            tipi = {c["tipo"] for c in r.json()}
            assert "integra" in tipi and "prova" in tipi, tipi

        @prova("V7", "connettore sconosciuto -> 422")
        def _():
            r = client.post("/v1/collegamenti", headers=AUTH, json={
                "nome": "X", "tipo": "inesistente", "segreti": {}})
            assert r.status_code == 422

        @prova("V8", "prova in memoria prima del salvataggio: ok, senza scritture")
        def _():
            prima = len(client.get("/v1/collegamenti", headers=AUTH).json())
            r = client.post("/v1/prova", headers=AUTH,
                            json={"tipo": "prova", "parametri": {}, "segreti": {"password": "x"}})
            assert r.status_code == 200 and r.json()["ok"] is True
            assert client.post("/v1/prova", headers=AUTH, json={"tipo": "inesistente"}).status_code == 422
            assert len(client.get("/v1/collegamenti", headers=AUTH).json()) == prima

        @prova("V9", "esegui-dovute: un giro dello scheduler, autenticato")
        def _():
            assert client.post("/v1/esegui-dovute").status_code == 401
            r = client.post("/v1/esegui-dovute", headers=AUTH)
            assert r.status_code == 200 and isinstance(r.json()["eseguiti"], list)

        @prova("V10", "anomalie da Dagster: segnala e chiudi (nel sistema generale)")
        def _():
            assert client.post("/v1/anomalia", headers=AUTH,
                               json={"impronta": "test", "titolo": "Prova"}).status_code == 200
            with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
                assert conn.execute("SELECT 1 FROM anomalie WHERE impronta = 'dagster:test'").fetchone()
            client.post("/v1/chiudi-anomalia", headers=AUTH, json={"impronta": "test"})
            with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
                conn.execute("DELETE FROM anomalie WHERE impronta = 'dagster:test'")

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM collegamenti WHERE id LIKE 'conn-test-%' OR id='integra-prova'")

    print("\n" + "=" * 72)
    falliti = [e for e in esiti if not e]
    print(f"{len(esiti) - len(falliti)}/{len(esiti)} passati")
    if falliti:
        raise SystemExit(1)
    print("Servizio connettori: verdi.")


if __name__ == "__main__":
    main()
