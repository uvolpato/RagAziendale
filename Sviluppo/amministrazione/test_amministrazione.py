"""Test dell'amministrazione: regole pure e garanzie del database.

    ./.venv/Scripts/python.exe -m amministrazione.test_amministrazione   (da Sviluppo/, ambiente su)

Il flusso completo via HTTPS (login, permessi sulle API, Vedi come con
Keycloak) sta in eval/verifica_amministrazione.py.
"""
import json
import pathlib
import re
import sys

import psycopg

from amministrazione import logica as L

QUI = pathlib.Path(__file__).resolve().parent.parent
E = {m.group(1): m.group(2).split("#")[0].strip()
     for m in re.finditer(r"^([A-Z_]+)=(.*)$", (QUI / ".env").read_text(encoding="utf-8"), re.M)}
esiti = []
_conn = None


def prova(nome, descrizione):
    """Come in test_gate: il rollback dopo ogni test evita la cascata di
    'current transaction is aborted' che nasconde il difetto vero."""
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
                _conn.rollback()
        return f
    return deco


print("\n--- Permessi (spec §3.4) " + "-" * 44)


@prova("P1", "il revisore legge tutto e non modifica nulla")
def _():
    p = L.permessi(["admin-revisore"], True)
    assert [k for k, v in p.items() if v == "M"] == ["vedicome"], p
    for voce in ("panoramica", "fonti", "anomalie", "registro", "aspetto", "utenti", "gruppi", "profili",
                 "aziende", "dagster", "uptime"):
        assert p.get(voce) == "L", (voce, p.get(voce))
    assert "struttura" not in p and "keycloak" not in p


@prova("P1b", "solo il Superutente ha struttura e console di Keycloak")
def _():
    for r in L.RUOLI:
        p = L.permessi([r], True)
        assert ("struttura" in p) == (r == "admin-super") and ("keycloak" in p) == (r == "admin-super"), r
        assert p.get("aziende") in ((None, "L") if r != "admin-super" else ("M",)), (r, p.get("aziende"))
    assert all(v == "M" for v in L.permessi(["admin-super"], True).values())


@prova("P2", "i ruoli si sommano e M vince su L")
def _():
    p = L.permessi(["admin-anomalie", "admin-fonti"], True)
    assert p["fonti"] == "M" and p["anomalie"] == "M" and p["vedicome"] == "M", p
    assert "aspetto" not in p


@prova("P3", "Dagster e Uptime Kuma solo a chi ha TUTTE le aziende (§3.3)")
def _():
    assert "dagster" not in L.permessi(["admin-importazioni"], False)
    assert "uptime" not in L.permessi(["admin-sistemi", "admin-revisore"], False)
    assert L.permessi(["admin-importazioni"], True)["dagster"] == "L"


@prova("P4", "nessun ruolo = nessun permesso (operatore)")
def _():
    assert L.permessi([], True) == {} and L.permessi(["ruolo-inventato"], True) == {}


@prova("P5", "anomalie e registro della propria area (§3.4 note 2 e 3)")
def _():
    assert L.aree_anomalie(["admin-accessi"]) == {"accessi"}
    assert L.aree_anomalie(["admin-accessi", "admin-anomalie"]) is None
    assert L.aree_registro(["admin-fonti"]) == {"fonti", "vedi-come"}
    assert L.aree_registro(["admin-revisore"]) is None


@prova("P6", "aziende dai gruppi 'azienda-<codice>'")
def _():
    assert L.aziende_da_gruppi(["tutti", "azienda-luis", "azienda-", "vendite", "azienda-decobrands"]) == ["decobrands", "luis"]


print("\n--- Contrasto (spec §2.1.4, §11) " + "-" * 36)


@prova("C1", "rapporti WCAG noti: bianco/nero 21:1, bianco/blu Decobrands > 4.5")
def _():
    assert abs(L.contrasto("#ffffff", "#000000") - 21) < 0.01
    assert L.contrasto("#0d6efd", "#ffffff") >= 4.5


@prova("C2", "la correzione proposta raggiunge AA e resta vicina al colore")
def _():
    for colore in ("#ffff00", "#7fd3ff", "#c62828", "#2e7d32"):
        c = L.correggi(colore, "#ffffff")
        assert L.contrasto(c, "#ffffff") >= L.AA, (colore, c)
    assert L.correggi("#0d6efd", "#ffffff") == "#0d6efd", "un colore gia' valido non si tocca"
    scuro = L.correggi("#333333", "#111111")
    assert L.contrasto(scuro, "#111111") >= L.AA, scuro


@prova("C3", "le tre palette di prova passano, un giallo con testo bianco no")
def _():
    for m in ("#0d6efd", "#2e7d32", "#c62828"):
        assert L.verifica_palette({**L.PREDEFINITA, "marchio": m}) == [], m
    err = L.verifica_palette({**L.PREDEFINITA, "marchio": "#ffff00"})
    assert err and "Correzione proposta" in err[0], err


@prova("C4", "colori non validi rifiutati (niente CSS iniettato nel tema)")
def _():
    assert L.verifica_palette({**L.PREDEFINITA, "marchio": "red; } body { display:none"})
    assert L.verifica_palette({**L.PREDEFINITA, "superficie": "#fff"})


print("\n--- Vedi come: stesso predicato del gate (spec §8) " + "-" * 18)
FONTE = {"acl_groups": ["vendite"], "stato": "attiva", "aziende": ["luis"]}


@prova("V1", "vede se e solo se i gruppi si intersecano (come recupero.py)")
def _():
    assert L.visibilita(FONTE, ["tutti", "vendite"], ["luis"])[0] is True
    ok, motivo, _a = L.visibilita(FONTE, ["tutti", "magazzino"], ["luis"])
    assert ok is False and "vendite" in motivo


@prova("V2", "fonte sospesa o di un'altra azienda: NON visibile, con il motivo (come il gate)")
def _():
    ok, motivo, _a = L.visibilita({**FONTE, "stato": "sospesa"}, ["vendite"], ["luis"])
    assert ok is False and "sospesa" in motivo, motivo
    ok, motivo, _a = L.visibilita({**FONTE, "aziende": ["decobrands"]}, ["vendite"], ["luis"])
    assert ok is False and "decobrands" in motivo, motivo


@prova("V3", "utente disattivato non vede nulla")
def _():
    assert L.visibilita(FONTE, ["vendite"], ["luis"], attivo=False)[0] is False


print("\n--- Database (migrazione 003) " + "-" * 39)
_conn = psycopg.connect(f"postgresql://postgres:{E['POSTGRES_PASSWORD']}@localhost:55432/rag")


def q(sql, *a):
    return _conn.execute(sql, a)


@prova("D1", "stessa impronta = una anomalia sola con il contatore")
def _():
    for _ in range(3):
        q("SELECT segnala_anomalia('t-d1','errore','importazioni','x')")
    n, occ = q("SELECT count(*), max(occorrenze) FROM anomalie WHERE impronta='t-d1'").fetchone()
    assert (n, occ) == (1, 3), (n, occ)


@prova("D2", "una risolta che si ripresenta torna aperta, con la cronologia")
def _():
    aid = q("SELECT segnala_anomalia('t-d2','errore','importazioni','x')").fetchone()[0]
    q("UPDATE anomalie SET stato='risolta', assegnata_a='qualcuno' WHERE id=%s", aid)
    q("SELECT segnala_anomalia('t-d2','errore','importazioni','x')")
    stato, ass = q("SELECT stato, assegnata_a FROM anomalie WHERE id=%s", aid).fetchone()
    tipi = [r[0] for r in q("SELECT tipo FROM anomalie_eventi WHERE anomalia_id=%s ORDER BY id", aid)]
    assert stato == "aperta" and ass is None and tipi[-1] == "riaperta", (stato, tipi)


@prova("D3", "ignorata: resta ignorata finche' non scade, poi torna aperta")
def _():
    aid = q("SELECT segnala_anomalia('t-d3','attenzione','qualita','x')").fetchone()[0]
    q("UPDATE anomalie SET stato='ignorata', ignorata_fino=now() + interval '7 days' WHERE id=%s", aid)
    q("SELECT segnala_anomalia('t-d3','attenzione','qualita','x')")
    assert q("SELECT stato FROM anomalie WHERE id=%s", aid).fetchone()[0] == "ignorata"
    q("UPDATE anomalie SET ignorata_fino=now() - interval '1 minute' WHERE id=%s", aid)
    q("SELECT segnala_anomalia('t-d3','attenzione','qualita','x')")
    assert q("SELECT stato FROM anomalie WHERE id=%s", aid).fetchone()[0] == "aperta"


@prova("D4", "chiusura automatica distinta da quella di una persona (§9.3)")
def _():
    aid = q("SELECT segnala_anomalia('t-d4','errore','sistemi','x')").fetchone()[0]
    q("SELECT chiudi_anomalia('t-d4')")
    assert q("SELECT stato, risolta_auto FROM anomalie WHERE id=%s", aid).fetchone() == ("risolta", True)


@prova("D5", "il registro modifiche non si modifica ne' si cancella")
def _():
    q("INSERT INTO registro_modifiche (chi, area, azione) VALUES ('test','fonti','x')")
    for sql in ("UPDATE registro_modifiche SET chi='altro'", "DELETE FROM registro_modifiche"):
        q("SAVEPOINT s")
        try:
            q(sql)
            raise AssertionError(f"accettato: {sql}")
        except psycopg.errors.RaiseException:
            q("ROLLBACK TO SAVEPOINT s")


@prova("D6", "una fonte non si attiva senza versione di riferimento")
def _():
    try:
        q("INSERT INTO sources (id, descrizione, percorso, acl_groups, owner, stato) VALUES ('t-d6','x','x','{g}','o','attiva')")
        raise AssertionError("fonte attiva senza versione accettata")
    except psycopg.errors.CheckViolation:
        pass


@prova("D7", "'servizi esterni' ancora vincolato alla firma (invariante 9)")
def _():
    try:
        q("INSERT INTO sources (id, descrizione, percorso, acl_groups, owner, residency) VALUES ('t-d7','x','x','{g}','o','cloud_ok')")
        raise AssertionError("cloud_ok senza firma accettato")
    except psycopg.errors.CheckViolation:
        pass


print(f"\n{sum(esiti)}/{len(esiti)} passati")
sys.exit(0 if all(esiti) else 1)
