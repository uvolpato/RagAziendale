"""Connettore Integra (SPECIFICA-CONNETTORI.md §10).

Legge il gestionale Integra con un utente di SOLA LETTURA, attraverso il
database parallelo `rag` (postgres_fdw verso Integra): le viste `rag_*` sono
li' e il connettore le traduce nelle colonne del modello canonico. La
traduzione vive nei file `viste/<tabella>.sql`: aggiungere un'entita' =
aggiungere una vista, il connettore non cambia.

Stato attuale: il contratto (prova con verifica della sola lettura, estrai da
vista, aziende, schema) e' completo. Le viste sono scritte contro le viste
reali `rag_*`; restano da VERIFICARE contro il database `rag` reale, che in
sviluppo non e' raggiungibile. Le mappature di partenza sono in
MODELLO-DATI-GESTIONALE.md §5 e nella tabella della specifica §10.
"""
import json
import pathlib

import psycopg
from psycopg.rows import dict_row

QUI = pathlib.Path(__file__).parent

# Viste che il connettore legge: servono al controllo di sola lettura in
# prova(). Le viste rag_* stanno nel database parallelo "rag". rag_pagamenti_clienti
# e' esclusa di proposito: espone IBAN/ABI/CAB/mandato (dati minimizzati, §7).
TABELLE_LETTE = ["rag_prodotti", "rag_clienti", "rag_indirizzi_clienti",
                 "rag_ordini_clienti", "rag_righe_ordini",
                 "rag_listini_testata", "rag_listini_righe", "rag_tabpag",
                 "rag_tabpor", "rag_tabspe"]


class Connettore:
    def __init__(self, parametri=None):
        self.parametri = parametri or {}

    # ---- catalogo ---------------------------------------------------------
    def manifesto(self):
        return json.loads((QUI / "manifesto.json").read_text(encoding="utf-8"))

    def entita(self):
        return self.manifesto()["entita"]

    # ---- connessione di sola lettura --------------------------------------
    def _dsn(self):
        p = self.parametri
        mancanti = [k for k in ("host", "database", "utente") if not p.get(k)]
        if mancanti:
            raise ValueError(
                "connettore Integra non configurato in .env: " +
                ", ".join(f"CONNETTORE_INTEGRA_{k.upper()}" for k in mancanti))
        return (f"host={p['host']} port={p.get('porta', 5432)} dbname={p['database']} "
                f"user={p['utente']} password={p.get('password', '')} "
                f"connect_timeout=5 sslmode={p.get('ssl', 'preferita')}")

    def _conn(self):
        return psycopg.connect(self._dsn(), row_factory=dict_row)

    def _solo_lettura(self, conn):
        """L'utente non deve poter scrivere: verifica, non raccomandazione (§4.3).

        Un utente superutente o con permessi di INSERT/UPDATE/DELETE sulle
        tabelle lette viene rifiutato: e' il controllo che il kit di conformita'
        pretende da ogni connettore.
        """
        ruoli = conn.execute("SELECT rolsuper, rolcreaterole, rolcreatedb"
                             " FROM pg_roles WHERE rolname = current_user").fetchone()
        if ruoli and ruoli["rolsuper"]:
            return "l'utente e' superutente: serve un utente di sola lettura"
        con_permessi = []
        for t in TABELLE_LETTE:
            for priv in ("INSERT", "UPDATE", "DELETE"):
                r = conn.execute(
                    "SELECT has_table_privilege(current_user, %s, %s)", (t, priv)).fetchone()
                if r and r["has_table_privilege"]:
                    con_permessi.append(f"{priv} su {t}")
        if con_permessi:
            return "l'utente puo' modificare i dati: " + ", ".join(sorted(set(con_permessi)))
        return None

    # ---- interfaccia comune (base.py) -------------------------------------
    def prova(self):
        """Si collega e verifica: raggiungibile, credenziali valide, sola lettura."""
        try:
            with self._conn() as conn:
                versione = conn.execute("SHOW server_version").fetchone()["server_version"]
                errore = self._solo_lettura(conn)
                if errore:
                    return {"ok": False, "motivo": errore}
                return {"ok": True, "versione": versione}
        except psycopg.Error as e:
            return {"ok": False, "motivo": f"non raggiungibile o credenziali non valide: {e}"}
        except ValueError as e:
            return {"ok": False, "motivo": str(e)}

    def aziende(self):
        """Le aziende presenti nel gestionale (multi-azienda: azi_cdazi)."""
        # ponytail: NON esiste una vista rag_aziende fra quelle fornite. Servira'
        # una vista sull'anagrafica aziende di Integra prima della multi-azienda.
        with self._conn() as conn:
            return conn.execute(
                "SELECT codice, ragione_sociale, partita_iva FROM rag_aziende"
                " ORDER BY codice").fetchall()

    def _vista(self, tabella):
        f = QUI / "viste" / f"{tabella}.sql"
        if not f.exists():
            raise NotImplementedError(
                f"vista non ancora scritta: viste/{tabella}.sql "
                "(mappature di partenza in MODELLO-DATI-GESTIONALE.md §5)")
        return f.read_text(encoding="utf-8")

    def _parametri(self, sql, codice_azienda, dal):
        """Passa solo i parametri che la vista usa davvero: le viste rag_* hanno
        gia' il filtro azienda hardcoded, quindi non tutte usano %(codice_azienda)s."""
        p = {}
        if "%(codice_azienda)s" in sql:
            p["codice_azienda"] = codice_azienda
        if "%(dal)s" in sql:
            p["dal"] = dal
        return p

    def estrai(self, tabella, codice_azienda, dal=None):
        sql = self._vista(tabella)
        with self._conn() as conn:
            for r in conn.execute(sql, self._parametri(sql, codice_azienda, dal)):
                yield r

    def identificativi(self, tabella, codice_azienda):
        sql = self._vista(tabella)
        with self._conn() as conn:
            return {r["id_origine"] for r in
                    conn.execute(sql, self._parametri(sql, codice_azienda, None))}

    def leggi_diretto(self, cosa, codice_azienda, chiave):
        # Giacenza, fido, prezzo netto: letture in diretta (decisione 47).
        # Il prezzo netto va chiesto a chi conosce Integra (decisione 48):
        # non si ricostruisce il motore prezzi.
        raise NotImplementedError(f"lettura diretta '{cosa}' non ancora implementata")

    def schema(self):
        """Impronta delle colonne lette: il controllo 'il gestionale e' cambiato'. """
        with self._conn() as conn:
            out = {}
            for t in TABELLE_LETTE:
                righe = conn.execute(
                    "SELECT column_name, data_type FROM information_schema.columns"
                    " WHERE table_name = %s ORDER BY ordinal_position", (t,)).fetchall()
                out[t] = [(r["column_name"], r["data_type"]) for r in righe]
            return out
