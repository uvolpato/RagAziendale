"""Pianificazione delle importazioni (SPECIFICA-CONNETTORI.md §7.5).

Scheduler ponte, prima di Dagster (decisione 60): calcola quali (azienda,
entita) sono "dovute" in base alla frequenza e all'ultima esecuzione in
`erp.sincronizzazioni`, e le esegue. Dagster sostituira' questo ciclo chiamando
lo STESSO motore (`base.importa`): qui cambia solo chi decide "adesso tocca a X",
non come si importa.

ponytail: la rilevazione delle cancellazioni avviene solo sulla lettura
completa. Per le entita incrementali (soggetti, articoli) serve una lettura
completa notturna separata; qui si rimanda a un passo successivo.
"""
from datetime import datetime, timezone

from connettori import base

# Frequenze predefinite per entita, in secondi (§7.5). Le modifiche per azienda
# stanno in `pianificazioni` (migrazione 007).
FREQUENZE = {
    "documenti_vendita": 15 * 60,
    "documenti_acquisto": 15 * 60,
    "soggetti": 60 * 60,
    "articoli": 60 * 60,
    "scadenze": 60 * 60,
    "listini": 24 * 60 * 60,
    "codici": 24 * 60 * 60,
    "giacenze": 24 * 60 * 60,
}


def aziende_da_importare(conn):
    """Le aziende attive con un collegamento attivo e i segreti salvati."""
    return conn.execute(
        "SELECT a.codice AS azienda, a.collegamento, c.tipo"
        " FROM aziende a JOIN collegamenti c ON c.id = a.collegamento"
        " WHERE a.attiva AND c.attivo AND c.segreti IS NOT NULL"
        " ORDER BY a.codice").fetchall()


def frequenza(conn, azienda, entita):
    """Secondi fra due esecuzioni, o None se l'importazione e' disattivata."""
    r = conn.execute("SELECT frequenza_secondi, attiva FROM pianificazioni"
                     " WHERE azienda = %s AND entita = %s", (azienda, entita)).fetchone()
    if r and not r["attiva"]:
        return None
    if r:
        return r["frequenza_secondi"]
    return FREQUENZE.get(entita)


def ultima_esecuzione(conn, azienda, entita):
    r = conn.execute("SELECT completata_il FROM erp.sincronizzazioni"
                     " WHERE azienda = %s AND entita = %s", (azienda, entita)).fetchone()
    return r["completata_il"] if r else None


def dovute(conn, adesso=None):
    """Importazioni dovute: [(azienda, entita, strategia), ...].

    `strategia` (incrementale o completa) dice al chiamante se rilevare le
    cancellazioni: si rilevano solo sulla lettura completa.
    """
    adesso = adesso or datetime.now(timezone.utc)
    catalogo = base.catalogo()
    out = []
    for a in aziende_da_importare(conn):
        for entita, info in catalogo.get(a["tipo"], {}).get("entita", {}).items():
            freq = frequenza(conn, a["azienda"], entita)
            if freq is None:
                continue
            ultima = ultima_esecuzione(conn, a["azienda"], entita)
            if ultima is not None and (adesso - ultima).total_seconds() < freq:
                continue
            out.append((a["azienda"], entita, info["strategia"]))
    return out


def esegui(conn, adesso, esegui_una):
    """Esegue le importazioni dovute.

    `esegui_una(azienda, entita, rileva_cancellazioni)` fa l'importazione; un
    errore su una entita non ferma il giro (importa registra gia' stato e
    anomalia).
    """
    for azienda, entita, strategia in dovute(conn, adesso):
        try:
            esegui_una(azienda, entita, rileva_cancellazioni=(strategia == "completa"))
        except Exception as e:
            print(f"  importazione {azienda}/{entita} non riuscita: {e}")
