"""Guardia di egress: l'unico punto da cui esce traffico HTTP.

Il principio e' che non esista un secondo client. Ogni chiamata verso
l'esterno passa da qui, quindi un percorso dimenticato non diventa un canale
di uscita: semplicemente non ha modo di partire.

Tre regole:
  1. host non dichiarato in nessuna allowlist -> errore, SEMPRE, anche su
     turno pulito. Cosi una dipendenza aggiunta distrattamente si nota subito.
  2. host in EGRESS_EXTERNAL -> vietato se la conversazione e' contaminata.
  3. host in EGRESS_INTERNAL -> sempre consentito.

La contaminazione viaggia in un contextvar, non come parametro: se fosse un
parametro, prima o poi qualcuno scriverebbe una chiamata senza passarlo.
"""
import contextvars
import os
import urllib.parse

import httpx

# Contaminazione del turno corrente. Impostata dal gate, letta dalla guardia.
turno_interno: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "turno_interno", default=False
)

# Registro delle destinazioni contattate nel turno: e' cio' che il test di
# conformita' ispeziona (T1.8).
_contattati: contextvars.ContextVar[list] = contextvars.ContextVar(
    "contattati", default=None
)


class EgressNonDichiarato(RuntimeError):
    """Host assente da entrambe le allowlist."""


class EgressVietato(RuntimeError):
    """Host esterno richiesto durante un turno con dati interni."""


def _lista(nome):
    return {h.strip() for h in os.environ.get(nome, "").split(",") if h.strip()}


class GuardiaTransport(httpx.BaseTransport):
    """Transport che decide se una richiesta puo' partire, prima che parta."""

    def __init__(self, interni=None, esterni=None, sotto=None):
        self.interni = interni if interni is not None else _lista("EGRESS_INTERNAL")
        self.esterni = esterni if esterni is not None else _lista("EGRESS_EXTERNAL")
        self._sotto = sotto or httpx.HTTPTransport()

    def controlla(self, url):
        host = urllib.parse.urlsplit(str(url)).hostname or ""
        registro = _contattati.get()
        if registro is not None:
            registro.append(host)

        if host in self.interni:
            return
        if host in self.esterni:
            if turno_interno.get():
                raise EgressVietato(
                    f"host esterno '{host}' richiesto in un turno con dati interni"
                )
            return
        raise EgressNonDichiarato(
            f"host '{host}' non dichiarato in EGRESS_INTERNAL ne in EGRESS_EXTERNAL"
        )

    def handle_request(self, request):
        self.controlla(request.url)
        return self._sotto.handle_request(request)


def client(**kw):
    """L'unico costruttore di client HTTP dell'applicazione."""
    return httpx.Client(transport=GuardiaTransport(), **kw)


class registra_destinazioni:
    """Context manager per i test: raccoglie gli host contattati nel blocco.

        with registra_destinazioni() as visti:
            ...
        assert "api.provider.com" not in visti
    """

    def __enter__(self):
        self._lista = []
        self._token = _contattati.set(self._lista)
        return self._lista

    def __exit__(self, *e):
        _contattati.reset(self._token)
        return False
