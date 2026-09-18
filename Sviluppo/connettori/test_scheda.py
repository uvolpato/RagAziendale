"""Test dello scheduler e della fotografia (giacenze).

    ./.venv/Scripts/python.exe -m connettori.test_scheda   (da Sviluppo/, ambiente su)

Verifica che la fotografia (non SCD2) scriva la snapshot ed sia idempotente, e
che lo scheduler calcoli le importazioni "dovute" in base a frequenza e stato.
"""
import pathlib
import re
from datetime import date, datetime, timezone

import psycopg
from psycopg.rows import dict_row

from connettori import base, scheda
from connettori.prova import Connettore as Prova

QUI = pathlib.Path(__file__).resolve().parent.parent
E = {m.group(1): m.group(2).split("#")[0].strip()
     for m in re.finditer(r"^([A-Z_]+)=(.*)$", (QUI / ".env").read_text(encoding="utf-8"), re.M)}
URL = f"postgresql://postgres:{E['POSTGRES_PASSWORD']}@localhost:55432/rag"

AZIENDA = "conn-prova"
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
    conn.execute("DELETE FROM erp.giacenze_istantanee WHERE azienda = %s", (AZIENDA,))
    conn.execute("DELETE FROM erp.sincronizzazioni WHERE azienda = %s", (AZIENDA,))
    conn.execute("DELETE FROM pianificazioni WHERE azienda = %s", (AZIENDA,))
    conn.execute("DELETE FROM aziende WHERE codice = %s", (AZIENDA,))
    conn.execute("DELETE FROM collegamenti WHERE id = 'conn-test-col'")
    conn.commit()


def semina(conn):
    conn.execute("INSERT INTO collegamenti (id, nome, tipo, segreti)"
                 " VALUES ('conn-test-col', 'Prova', 'prova', %s) ON CONFLICT (id) DO UPDATE SET segreti=EXCLUDED.segreti",
                 (b'\x00\x01',))
    conn.execute("INSERT INTO aziende (codice, ragione_sociale, connettore, collegamento, codice_origine)"
                 " VALUES (%s, 'Connettore Prova', 'prova', 'conn-test-col', '001') ON CONFLICT (codice) DO UPDATE SET collegamento='conn-test-col'",
                 (AZIENDA,))
    conn.commit()


def main():
    global _conn
    conn = _conn = psycopg.connect(URL, row_factory=dict_row)
    pulisci(conn)

    print("\n--- Fotografia (giacenze) " + "-" * 40)

    @prova("F1", "giacenze: snapshot scritta e idempotente, non SCD2")
    def _():
        pulisci(conn)
        semina(conn)
        p = Prova()
        p.dati = {"giacenze_istantanee": [
            {"articolo_id_origine": "100", "magazzino": "01", "esistenza": 10, "disponibile": 8},
            {"articolo_id_origine": "101", "magazzino": "01", "esistenza": 0, "disponibile": 0}]}
        r = base.importa(conn, p, AZIENDA, "giacenze")
        assert r["righe"] == 2, r
        righe = conn.execute("SELECT * FROM erp.giacenze_istantanee WHERE azienda = %s", (AZIENDA,)).fetchall()
        assert len(righe) == 2 and righe[0]["data"] == date.today()
        assert {x["articolo_id"] for x in righe} == {f"{AZIENDA}:100", f"{AZIENDA}:101"}
        # Re-run: stesso numero di righe (upsert), non duplica.
        base.importa(conn, p, AZIENDA, "giacenze")
        assert len(conn.execute("SELECT 1 FROM erp.giacenze_istantanee WHERE azienda = %s", (AZIENDA,)).fetchall()) == 2

    print("\n--- Scheduler (dovute) " + "-" * 44)

    @prova("F2", "senza esecuzioni -> tutto dovuto; dopo l'esecuzione -> non piu'")
    def _():
        pulisci(conn)
        semina(conn)
        adesso = datetime.now(timezone.utc)
        dovute = scheda.dovute(conn, adesso)
        assert any(a == AZIENDA and e == "soggetti" for a, e, _ in dovute), dovute
        # Simula un'esecuzione recente di tutto: niente dovuto.
        for entita in ("soggetti", "articoli", "giacenze"):
            conn.execute("INSERT INTO erp.sincronizzazioni (azienda, entita, completata_il, esito)"
                         " VALUES (%s,%s,now(),'ok') ON CONFLICT (azienda,entita) DO UPDATE SET completata_il=now(), esito='ok'",
                         (AZIENDA, entita))
        conn.commit()
        assert scheda.dovute(conn, datetime.now(timezone.utc)) == []

    @prova("F3", "frequenza: predefinita, poi modificata e disattivata dalla pianificazione")
    def _():
        pulisci(conn)
        semina(conn)
        assert scheda.frequenza(conn, AZIENDA, "soggetti") == scheda.FREQUENZE["soggetti"]
        conn.execute("INSERT INTO pianificazioni (azienda, entita, frequenza_secondi) VALUES (%s,'soggetti',120)",
                     (AZIENDA,))
        conn.commit()
        assert scheda.frequenza(conn, AZIENDA, "soggetti") == 120
        conn.execute("UPDATE pianificazioni SET attiva=false WHERE azienda=%s AND entita='soggetti'", (AZIENDA,))
        conn.commit()
        assert scheda.frequenza(conn, AZIENDA, "soggetti") is None

    pulisci(conn)
    conn.close()

    print("\n" + "=" * 72)
    falliti = [e for e in esiti if not e]
    print(f"{len(esiti) - len(falliti)}/{len(esiti)} passati")
    if falliti:
        raise SystemExit(1)
    print("Scheduler e fotografia: verdi.")


if __name__ == "__main__":
    main()
