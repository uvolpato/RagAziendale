"""Test della fase 3: segreti cifrati, catalogo e vincoli del database.

    ./.venv/Scripts/python.exe -m connettori.test_collegamenti   (da Sviluppo/, ambiente su)

La parte di sicurezza (cifratura Fernet, rotazione, niente segreti in chiaro) e
quella di vincolo (una sola azienda per coppia collegamento+codice, stato
valido, FK) si testano qui, senza fastapi ne' il gestionale vero.
"""
import base64
import pathlib
import re

import psycopg
from psycopg.rows import dict_row

from connettori import base, segreti

QUI = pathlib.Path(__file__).resolve().parent.parent
E = {m.group(1): m.group(2).split("#")[0].strip()
     for m in re.finditer(r"^([A-Z_]+)=(.*)$", (QUI / ".env").read_text(encoding="utf-8"), re.M)}
URL = f"postgresql://postgres:{E['POSTGRES_PASSWORD']}@localhost:55432/rag"

esiti = []
_conn = None


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
        finally:
            if _conn is not None:
                try:
                    _conn.rollback()
                except Exception:
                    pass
        return f
    return deco


def pulisci(conn):
    conn.execute("DELETE FROM aziende WHERE codice LIKE 'conn-t%'")
    conn.execute("DELETE FROM collegamenti WHERE id LIKE 'conn-test-%'")
    conn.commit()


def main():
    global _conn
    conn = _conn = psycopg.connect(URL, row_factory=dict_row)
    pulisci(conn)

    print("\n--- Cifratura (segreti) " + "-" * 44)

    @prova("S1", "cifra e decifra: andata e ritorno, senza segreti in chiaro")
    def _():
        blob = segreti.cifra({"password": "s3gr3to", "utente": "ro"}, "chiave-di-prova")
        assert b"password" not in blob and b"s3gr3to" not in blob, "il blob contiene il segreto in chiaro"
        assert segreti.decifra(blob, "chiave-di-prova") == {"password": "s3gr3to", "utente": "ro"}

    @prova("S2", "chiave sbagliata -> SegretoIlleggibile, non un valore qualunque")
    def _():
        blob = segreti.cifra({"password": "x"}, "chiave-a")
        try:
            segreti.decifra(blob, "chiave-b")
            raise AssertionError("decifrato con la chiave sbagliata")
        except segreti.SegretoIlleggibile:
            pass

    @prova("S3", "rotazione: ricifra con la nuova e decifra con la nuova")
    def _():
        vecchio = segreti.cifra({"password": "x"}, "chiave-vecchia")
        nuovo = segreti.ricifra(vecchio, "chiave-vecchia", "chiave-nuova")
        assert segreti.decifra(nuovo, "chiave-nuova") == {"password": "x"}

    @prova("S4", "chiave Fernet o stringa qualsiasi: entrambe valide")
    def _():
        fernet = base64.urlsafe_b64encode(b"a" * 32).decode()
        a = segreti.cifra({"p": 1}, fernet)
        b = segreti.cifra({"p": 1}, "una-chiave-qualunque")
        assert segreti.decifra(a, fernet) == {"p": 1}
        assert segreti.decifra(b, "una-chiave-qualunque") == {"p": 1}

    print("\n--- Catalogo e connettori " + "-" * 40)

    @prova("S5", "catalogo() legge i connettori installati dal codice")
    def _():
        c = base.catalogo()
        assert "integra" in c and "prova" in c
        assert c["integra"]["multi_azienda"] is True
        assert "entita" in c["integra"] and "parametri" in c["integra"]

    @prova("S6", "connettore_di con parametri espliciti (quelli decifrati)")
    def _():
        c = base.connettore_di("prova", parametri={"host": "x", "password": "s"})
        assert c.parametri == {"host": "x", "password": "s"}

    print("\n--- Vincoli del database " + "-" * 40)

    @prova("S7", "stato del collegamento fuori dai valori ammessi -> rifiutato")
    def _():
        try:
            conn.execute("INSERT INTO collegamenti (id, nome, tipo, stato)"
                         " VALUES ('conn-test-s7', 'Prova', 'prova', 'boh')")
            raise AssertionError("accettato uno stato non valido")
        except psycopg.errors.CheckViolation:
            conn.rollback()

    @prova("S8", "la stessa coppia collegamento+codice_origine non si abbina due volte")
    def _():
        conn.execute("INSERT INTO collegamenti (id, nome, tipo) VALUES ('conn-test-s8', 'Prova', 'prova')")
        conn.execute("INSERT INTO aziende (codice, ragione_sociale, collegamento, codice_origine)"
                     " VALUES ('conn-t1', 'Uno', 'conn-test-s8', '001')")
        try:
            conn.execute("INSERT INTO aziende (codice, ragione_sociale, collegamento, codice_origine)"
                         " VALUES ('conn-t2', 'Due', 'conn-test-s8', '001')")
            raise AssertionError("accettato un doppio abbinamento")
        except psycopg.errors.UniqueViolation:
            conn.rollback()
        # Aziende non ancora collegate (collegamento NULL) restano libere.
        conn.execute("INSERT INTO aziende (codice, ragione_sociale) VALUES ('conn-t3', 'Tre')")

    @prova("S9", "collegamento inesistente -> rifiutato dalla FK")
    def _():
        try:
            conn.execute("INSERT INTO aziende (codice, ragione_sociale, collegamento)"
                         " VALUES ('conn-t4', 'Quattro', 'inesistente')")
            raise AssertionError("accettato un collegamento inesistente")
        except psycopg.errors.ForeignKeyViolation:
            conn.rollback()

    pulisci(conn)
    conn.close()

    print("\n" + "=" * 72)
    falliti = [e for e in esiti if not e]
    print(f"{len(esiti) - len(falliti)}/{len(esiti)} passati")
    if falliti:
        raise SystemExit(1)
    print("Segreti, catalogo e vincoli: verdi.")


if __name__ == "__main__":
    main()
