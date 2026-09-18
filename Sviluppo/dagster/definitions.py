"""Code location Dagster: pianifica le importazioni dai gestionali (decisione 60).

Dagster qui fornisce SOLO l'orologio (le schedule), lo storico delle esecuzioni
e la UI. Il lavoro vero (decifrare le credenziali, estrarre, fondere nello
storico SCD2) lo fa il servizio `connettori`; questo codice lo chiama via HTTP.

Due job:
- `importazioni_programmate`: un giro dello scheduler (importa tutto cio' che e'
  dovuto secondo le frequenze). E' quello che parte dalla schedule.
- `importa_singola`: importa UNA entita per UNA azienda, per l'"Avvia ora".

Si usa lo stdlib (`urllib`) e non `httpx`: l'immagine `dagster/dagster` non
garantisce httpx, e qui basta una POST.
"""
import json
import os
import urllib.request

from dagster import Config, Definitions, ScheduleDefinition, job, op

SERVIZIO = os.environ.get("CONNETTORI_URL", "http://connettori:8000")
CHIAVE = os.environ.get("CONNETTORI_KEY", "")


def _post(percorso, corpo=None):
    dati = json.dumps(corpo).encode() if corpo is not None else None
    richiesta = urllib.request.Request(
        SERVIZIO + percorso, data=dati, method="POST",
        headers={"Authorization": "Bearer " + CHIAVE,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(richiesta, timeout=600) as risposta:
        return json.loads(risposta.read().decode("utf-8"))


def _segnala(titolo, dettaglio):
    """I fallimenti del run finiscono anche nelle anomalie del pannello, non
    solo nello storico di Dagster (il "sistema generale")."""
    try:
        _post("/v1/anomalia", {"impronta": "esegui-dovute", "titolo": titolo,
                               "dettaglio": dettaglio})
    except Exception:
        pass            # se anche il servizio e' giu', non c'e' nessuno a cui segnalare


def _chiudi_anomalia():
    try:
        _post("/v1/chiudi-anomalia", {"impronta": "esegui-dovute"})
    except Exception:
        pass


@op
def esegui_importazioni_dovute():
    try:
        ris = _post("/v1/esegui-dovute")
        _chiudi_anomalia()
        return ris
    except Exception as e:
        _segnala("Importazioni programmate non riuscite", {"errore": str(e)[:500]})
        raise


@job
def importazioni_programmate():
    esegui_importazioni_dovute()


class ImportaConfig(Config):
    collegamento: str
    azienda: str
    entita: str
    completa: bool = False


@op
def importa_entita(config: ImportaConfig):
    return _post(f"/v1/collegamenti/{config.collegamento}/importa",
                 {"azienda": config.azienda, "entita": config.entita,
                  "completa": config.completa})


@job
def importa_singola():
    importa_entita()


defs = Definitions(
    jobs=[importazioni_programmate, importa_singola],
    schedules=[
        ScheduleDefinition(
            job=importazioni_programmate,
            cron_schedule="*/15 * * * *",
            name="importazioni_ogni_15_minuti",
        ),
    ],
)
