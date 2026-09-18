"""Test del motore di importazione e dei connettori (SPECIFICA-CONNETTORI.md §12).

    ./.venv/Scripts/python.exe -m connettori.test_connettori   (da Sviluppo/, ambiente su)

Verificano le garanzie che contano dello storico SCD2 (MODELLO-DATI-GESTIONALE.md
§4): l'idempotenza, la modifica che chiude una versione e ne apre una, la
cancellazione che segna senza perdere cronologia, il calo sospetto che blocca
senza toccare i dati, e la deduplica delle anomalie. Tutto con il connettore di
prova (in memoria): non serve l'Integra vero, che in sviluppo non e' raggiungibile.
"""
import pathlib
import re

import psycopg
from psycopg.rows import dict_row

from connettori import base, conformita
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
                try:
                    _conn.rollback()
                except Exception:
                    pass
        return f
    return deco


def pulisci(conn):
    for t in ("soggetti", "soggetti_ruoli", "indirizzi", "articoli", "listini", "listini_righe"):
        conn.execute(f"DELETE FROM erp_storico.{t} WHERE azienda = %s", (AZIENDA,))
    conn.execute("DELETE FROM erp.sincronizzazioni WHERE azienda = %s", (AZIENDA,))
    conn.execute("DELETE FROM anomalie WHERE impronta LIKE %s", (f"importazione:{AZIENDA}/%",))
    conn.execute("DELETE FROM aziende WHERE codice = %s", (AZIENDA,))
    conn.commit()


def semina_azienda(conn):
    conn.execute("INSERT INTO aziende (codice, ragione_sociale, connettore, codice_origine)"
                 " VALUES (%s, 'Connettore Prova', 'prova', '001') ON CONFLICT (codice) DO NOTHING", (AZIENDA,))
    conn.commit()


def riga(id_origine, **campi):
    """Riga di business con id canonico gia' composto (per i test di fusione)."""
    return {"id": f"{AZIENDA}:{id_origine}", **campi}


def riga_origine(id_origine, **campi):
    """Riga come la emette un connettore: id_origine, non id canonico."""
    return {"id_origine": id_origine, **campi}


def correnti(conn, tabella="soggetti"):
    return conn.execute(f"SELECT id FROM erp.{tabella} WHERE azienda = %s ORDER BY id", (AZIENDA,)).fetchall()


def main():
    global _conn
    conn = _conn = psycopg.connect(URL, row_factory=dict_row)
    pulisci(conn)

    print("\n--- Impronta e colonne " + "-" * 44)

    @prova("C1", "impronta stabile e sensibile; id/azienda non ne fanno parte")
    def _():
        colonne = base.colonne_business(conn, "soggetti")
        # id e azienda sono colonne di versionamento: lo stesso contenuto con
        # id o azienda diversi produce la STESSA impronta.
        assert base.hash_riga({"id": "X:1", "ragione_sociale": "Rossi"}, colonne) == \
               base.hash_riga({"id": "Y:9", "ragione_sociale": "Rossi"}, colonne)
        # ...ma un campo di business diverso cambia l'impronta.
        assert base.hash_riga({"ragione_sociale": "Rossi"}, colonne) != \
               base.hash_riga({"ragione_sociale": "Bianchi"}, colonne)
        # Stessa riga, due volte: identica.
        assert base.hash_riga(riga("1", ragione_sociale="Rossi S.r.l.", partita_iva="111"), colonne) == \
               base.hash_riga(riga("1", ragione_sociale="Rossi S.r.l.", partita_iva="111"), colonne)

    @prova("C2", "colonne_business esclude il versionamento e include extra")
    def _():
        colonne = set(base.colonne_business(conn, "soggetti"))
        assert not (colonne & base.VERSIONAMENTO), colonne & base.VERSIONAMENTO
        assert "extra" in colonne and "ragione_sociale" in colonne

    print("\n--- Fusione SCD2 " + "-" * 52)

    @prova("C3", "inserimento + idempotenza: reimportare non duplica")
    def _():
        pulisci(conn)
        righe = [riga("1", ragione_sociale="Rossi S.r.l."), riga("2", ragione_sociale="Bianchi S.p.A.")]
        assert base.fusione(conn, "soggetti", AZIENDA, righe, True) == 2
        conn.commit()
        assert len(correnti(conn)) == 2
        assert base.fusione(conn, "soggetti", AZIENDA, righe, True) == 0
        conn.commit()
        assert len(correnti(conn)) == 2

    @prova("C4", "modifica: chiude la versione precedente e ne apre una nuova")
    def _():
        pulisci(conn)
        base.fusione(conn, "soggetti", AZIENDA, [riga("1", ragione_sociale="Rossi S.r.l.")], True)
        conn.commit()
        base.fusione(conn, "soggetti", AZIENDA, [riga("1", ragione_sociale="Rossi S.r.l.", partita_iva="222")], True)
        conn.commit()
        storico = conn.execute(
            "SELECT registrato_al, cancellato FROM erp_storico.soggetti"
            " WHERE azienda = %s AND id = %s ORDER BY versione", (AZIENDA, f"{AZIENDA}:1")).fetchall()
        assert len(storico) == 2, storico
        assert storico[0]["registrato_al"] is not None, "la versione vecchia non e' chiusa"
        assert storico[1]["registrato_al"] is None and not storico[1]["cancellato"]
        assert len(correnti(conn)) == 1

    @prova("C5", "cancellazione in lettura completa: chiusa e segnata, cronologia intatta")
    def _():
        pulisci(conn)
        base.fusione(conn, "soggetti", AZIENDA, [riga("1", ragione_sociale="A"), riga("2", ragione_sociale="B")], True)
        conn.commit()
        # Lettura completa senza l'id 1: deve risultare cancellato, non sparito.
        v = base.fusione(conn, "soggetti", AZIENDA, [riga("2", ragione_sociale="B")], True)
        conn.commit()
        assert v == 1
        ids = [r["id"] for r in correnti(conn)]
        assert ids == [f"{AZIENDA}:2"], ids                       # la vista non mostra il cancellato
        riga1 = conn.execute("SELECT cancellato, registrato_al FROM erp_storico.soggetti"
                             " WHERE azienda = %s AND id = %s AND registrato_al IS NULL",
                             (AZIENDA, f"{AZIENDA}:1")).fetchone()
        assert riga1 and riga1["cancellato"], "manca la versione 'cancellato' corrente"

    @prova("C6", "senza rilevazione cancellazioni (incrementale): la riga assente resta corrente")
    def _():
        pulisci(conn)
        base.fusione(conn, "soggetti", AZIENDA, [riga("1", ragione_sociale="A"), riga("2", ragione_sociale="B")], True)
        conn.commit()
        base.fusione(conn, "soggetti", AZIENDA, [riga("1", ragione_sociale="A")], False)
        conn.commit()
        assert len(correnti(conn)) == 2, "una lettura incrementale ha cancellato righe"

    print("\n--- Motore completo (importa) " + "-" * 40)

    @prova("C7", "prima importazione -> versioni; seconda -> zero (idempotente), stato ok")
    def _():
        pulisci(conn)
        semina_azienda(conn)
        p = Prova()
        p.dati = {"soggetti": [riga_origine("1", ragione_sociale="Rossi S.r.l."),
                               riga_origine("2", ragione_sociale="Bianchi S.p.A.")],
                  "soggetti_ruoli": []}
        r1 = base.importa(conn, p, AZIENDA, "soggetti")
        assert r1["versioni"] == 2 and r1["righe"] == 2
        r2 = base.importa(conn, p, AZIENDA, "soggetti")
        assert r2["versioni"] == 0
        stato = conn.execute("SELECT esito, righe_lette FROM erp.sincronizzazioni"
                             " WHERE azienda=%s AND entita='soggetti'", (AZIENDA,)).fetchone()
        assert stato["esito"] == "ok" and stato["righe_lette"] == 2

    @prova("C8", "calo sospetto -> blocco con anomalia critica, storico intatto")
    def _():
        pulisci(conn)
        semina_azienda(conn)
        conn.execute("INSERT INTO erp.sincronizzazioni (azienda, entita, esito, righe_lette)"
                     " VALUES (%s,'soggetti','ok',100)"
                     " ON CONFLICT (azienda,entita) DO UPDATE SET esito='ok', righe_lette=100", (AZIENDA,))
        conn.commit()
        p = Prova()
        p.dati = {"soggetti": [riga_origine("1", ragione_sociale="Rossi S.r.l.")], "soggetti_ruoli": []}
        try:
            base.importa(conn, p, AZIENDA, "soggetti")
            raise AssertionError("il calo non e' stato bloccato")
        except base.ImportazioneBloccata:
            pass
        a = conn.execute("SELECT gravita, stato FROM anomalie WHERE impronta = %s",
                         (f"importazione:{AZIENDA}/soggetti:crollo",)).fetchone()
        assert a and a["gravita"] == "critico", a
        assert len(correnti(conn)) == 0, "il calo ha toccato lo storico"

    @prova("C9", "connettore che fallisce -> anomalia 'errore' deduplicata, non blocco")
    def _():
        pulisci(conn)
        semina_azienda(conn)

        class Guasto:
            def entita(self):
                return {"soggetti": {"strategia": "completa", "tabelle": ["soggetti"]}}
            def estrai(self, *a, **k):
                raise RuntimeError("gestionale giu'")

        for _ in range(2):
            try:
                base.importa(conn, Guasto(), AZIENDA, "soggetti")
            except RuntimeError:
                pass
        a = conn.execute("SELECT sistema, occorrenze, stato FROM anomalie WHERE impronta = %s",
                         (f"importazione:{AZIENDA}/soggetti:errore",)).fetchone()
        assert a and a["sistema"] == "importazioni" and a["occorrenze"] == 2, a

    @prova("C10", "entita che il connettore non fornisce -> EntitaNonDisponibile")
    def _():
        pulisci(conn)
        semina_azienda(conn)
        p = Prova()
        try:
            base.importa(conn, p, AZIENDA, "documenti_vendita")
            raise AssertionError("accettata un'entita inesistente")
        except base.EntitaNonDisponibile:
            pass

    print("\n--- Kit di conformita' " + "-" * 44)

    @prova("C11", "connettore pulito -> kit tutto verde")
    def _():
        p = Prova()
        p.dati = {"soggetti": [riga_origine("1", ragione_sociale="Rossi S.r.l.")],
                  "soggetti_ruoli": [], "articoli": []}
        esiti = conformita.kit(conn, p)
        falliti = [e for e in esiti if not e[1]]
        assert not falliti, falliti

    @prova("C12", "una colonna esclusa (IBAN) -> il kit la segnala")
    def _():
        p = Prova()
        p.dati = {"soggetti": [riga_origine("1", ragione_sociale="Rossi S.r.l.", iban="IT00X")]}
        esiti = conformita.kit(conn, p)
        dati_riservati = [e for e in esiti if e[0].startswith("dati-riservati:soggetti")]
        assert dati_riservati and not dati_riservati[0][1], esiti

    pulisci(conn)
    conn.close()

    print("\n" + "=" * 72)
    falliti = [e for e in esiti if not e[1]]
    print(f"{len(esiti) - len(falliti)}/{len(esiti)} passati")
    if falliti:
        for nome, _, desc, err in falliti:
            print(f"  FAIL {nome}: {err}")
        raise SystemExit(1)
    print("Motore di importazione e connettori: verdi.")


if __name__ == "__main__":
    main()
