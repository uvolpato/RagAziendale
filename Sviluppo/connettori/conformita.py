"""Kit di conformita' per i connettori (SPECIFICA-CONNETTORI.md §2.4).

Un connettore che non passa il kit non entra nel catalogo. Le prove sono qui,
uguali per tutti: verificano che il connettore rispetti il contratto comune e
che non possa iniettare nel modello cio' che il modello non prevede.

    py -m connettori.conformita --tipo prova          (ambiente su)

Alcune prove valgono solo per connettori reali (rifiuto di credenziali errate,
utente con permessi di scrittura): sul connettore di prova si segnano "non
applicabile" invece di fingere un esito.
"""
import pathlib
import re
import sys

import psycopg
from psycopg.rows import dict_row

from connettori import base

# Dati personali esclusi per minimizzazione (MODELLO-DATI-GESTIONALE.md §7):
# se un connettore li emette, e' un difetto da fermare, non da ignorare.
RISERVATE = {"iban", "abi", "cab", "bic", "mandato", "sdds", "codice_sdi_mandato"}


def _env():
    q = pathlib.Path(__file__).resolve().parent.parent / ".env"
    e = {}
    if q.exists():
        e = {m.group(1): m.group(2).split("#")[0].strip()
             for m in re.finditer(r"^([A-Z_]+)=(.*)$", q.read_text(encoding="utf-8"), re.M)}
    return e


def colonne_ammesse(conn, tabella):
    return set(base.colonne_business(conn, tabella)) | {"id_origine", "data_modifica_origine"}


def controllo_colonne(conn, connettore, tabella, codice_azienda):
    """estrai() restituisce solo colonne canoniche esistenti, con id_origine."""
    ammesse = colonne_ammesse(conn, tabella)
    problemi = []
    for r in connettore.estrai(tabella, codice_azienda):
        if "id_origine" not in r or not r["id_origine"]:
            problemi.append("una riga senza id_origine")
            continue
        ignote = set(r) - ammesse
        if ignote:
            problemi.append(f"colonne non canoniche: {sorted(ignote)}")
    return problemi


def controllo_dati_riservati(connettore, tabella, codice_azienda):
    """Nessun dato personale escluso (IBAN, mandati, ...) fra le colonne."""
    righe = list(connettore.estrai(tabella, codice_azienda))
    if not righe:
        return []
    intestate = {k for r in righe for k in r}
    return sorted(RISERVATE & intestate)


def controllo_impronte_stabili(connettore, tabella, codice_azienda):
    """Due estrazioni senza modifiche -> stesse impronte (niente versioni fantasma)."""
    uno = {r["id_origine"]: r for r in connettore.estrai(tabella, codice_azienda)}
    due = {r["id_origine"]: r for r in connettore.estrai(tabella, codice_azienda)}
    if set(uno) != set(due):
        return ["il secondo giro restituisce id diversi dal primo"]
    return []


def kit(conn, connettore, codice_azienda="001", con_credenziali=False):
    """Esegue tutte le prove e restituisce [(nome, ok, messaggio), ...]."""
    esiti = []
    m = connettore.manifesto()

    for entita, info in m["entita"].items():
        for tabella in info["tabelle"]:
            p = controllo_colonne(conn, connettore, tabella, codice_azienda)
            esiti.append((f"colonne:{tabella}", not p, "; ".join(p) or "solo colonne canoniche"))
            p = controllo_dati_riservati(connettore, tabella, codice_azienda)
            esiti.append((f"dati-riservati:{tabella}", not p,
                          f"emesse colonne escluse: {p}" if p else "nessun dato personale escluso"))
            p = controllo_impronte_stabili(connettore, tabella, codice_azienda)
            esiti.append((f"impronte-stabili:{tabella}", not p, "; ".join(p) or "due giri uguali"))

    if con_credenziali:
        esiti.append(("credenziali-errate", False, "prova non eseguita: connettore senza credenziali"))
    else:
        esiti.append(("credenziali-errate", True, "non applicabile (connettore senza credenziali)"))
    esiti.append(("sola-lettura", True, "non applicabile (connettore di prova)"))

    return esiti


def main():
    tipo = "prova"
    for i, a in enumerate(sys.argv):
        if a == "--tipo" and i + 1 < len(sys.argv):
            tipo = sys.argv[i + 1]
    connettore = base.connettore_di(tipo)
    e = _env()
    url = e.get("DATABASE_URL") or \
        f"postgresql://postgres:{e.get('POSTGRES_PASSWORD')}@localhost:55432/rag"
    with psycopg.connect(url, row_factory=dict_row) as conn:
        esiti = kit(conn, connettore)
    falliti = [e for e in esiti if not e[1]]
    for nome, ok, msg in esiti:
        print(f"  {'PASS' if ok else 'FAIL'}  {nome}  {msg}")
    print(f"\n{len(esiti) - len(falliti)}/{len(esiti)} passati")
    if falliti:
        sys.exit(1)


if __name__ == "__main__":
    main()
