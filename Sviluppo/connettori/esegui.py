"""Esecuzione da riga di comando di una importazione (SPECIFICA-CONNETTORI.md §7.1).

Prima di Dagster e del pannello, il motore si avvia cosi':

    py -m connettori.esegui --azienda luis --entita soggetti
    py -m connettori.esegui --azienda luis --entita soggetti --completa

`--completa` forza la lettura completa (rileva le cancellazioni) anche su una
entita configurata incrementale. Utile per la prima importazione e per le
letture complete notturne.
"""
import os
import pathlib
import re
import sys

import psycopg
from psycopg.rows import dict_row

from connettori import base

QUI = pathlib.Path(__file__).resolve().parent.parent


def carica_env():
    """Legge .env nell'ambiente del processo: cosi' parametri_da_env() vede le
    credenziali dei connettori (CONNETTORE_*) senza passarle a mano."""
    f = QUI / ".env"
    if not f.exists():
        raise SystemExit("manca .env — copiarlo da .env.example e compilarlo")
    for riga in f.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z_]+)=(.*)$", riga)
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2).split("#")[0].strip()


def argomento(nome, default=None):
    for i, a in enumerate(sys.argv):
        if a == nome and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main():
    carica_env()
    azienda = argomento("--azienda")
    entita = argomento("--entita")
    completa = "--completa" in sys.argv
    if not azienda or not entita:
        raise SystemExit("uso: py -m connettori.esegui --azienda <codice> --entita <nome> [--completa]")

    url = os.environ.get("DATABASE_URL") or \
        f"postgresql://postgres:{os.environ.get('POSTGRES_PASSWORD')}@localhost:55432/rag"
    with psycopg.connect(url, row_factory=dict_row) as conn:
        a = conn.execute("SELECT connettore FROM aziende WHERE codice = %s", (azienda,)).fetchone()
        if not a:
            raise SystemExit(f"azienda sconosciuta: {azienda}")
        if not a["connettore"]:
            raise SystemExit(f"l'azienda {azienda} non ha un connettore configurato")
        connettore = base.connettore_di(a["connettore"])
        riepilogo = base.importa(conn, connettore, azienda, entita,
                                 rileva_cancellazioni=True if completa else None)
    print(f"  ok: {riepilogo['righe']} righe lette, {riepilogo['versioni']} versioni nuove "
          f"({riepilogo['azienda']}/{riepilogo['entita']})")


if __name__ == "__main__":
    main()
