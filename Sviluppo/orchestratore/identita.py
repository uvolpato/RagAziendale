"""Verifica del token e ricavo dei gruppi.

NESSUN percorso alternativo: senza un token valido non si risponde. Niente
utente anonimo, niente default, niente "se manca il token usa tutti".

Il motivo e' concreto: LibreChat sostituisce i placeholder non risolti con
STRINGHE VUOTE, non con testo letterale. Una configurazione sbagliata o un
aggiornamento producono quindi un header Authorization vuoto, senza alcun
errore. Se la verifica avesse un ramo permissivo, il sistema fallirebbe
aperto e nessuno se ne accorgerebbe.

Dettaglio verificato sul realm: il token dichiara iss con l'hostname ESTERNO
(https://sso.localhost/realms/azienda) anche interrogando Keycloak
dall'interno. Quindi si valida iss sul valore esterno e si scaricano le
chiavi da quello interno: due variabili distinte, non una.
"""
import os
import threading
import time

import jwt
from jwt import PyJWKClient


class TokenNonValido(Exception):
    """Qualsiasi ragione per cui un token non e' accettabile."""


_jwks = None
_lock = threading.Lock()


def _client_chiavi():
    global _jwks
    with _lock:
        if _jwks is None:
            url = os.environ["OIDC_JWKS_URL"]          # interno: http://keycloak:8080/...
            _jwks = PyJWKClient(url, cache_keys=True, lifespan=600)
        return _jwks


def verifica(authorization: str | None) -> dict:
    """Restituisce i claim del token, oppure solleva TokenNonValido.

    `authorization` e' il valore grezzo dell'header, compreso "Bearer ".
    """
    if not authorization or not authorization.strip():
        raise TokenNonValido("header Authorization assente o vuoto")

    parti = authorization.split()
    if len(parti) != 2 or parti[0].lower() != "bearer" or not parti[1].strip():
        raise TokenNonValido("header Authorization malformato")
    grezzo = parti[1]

    try:
        chiave = _client_chiavi().get_signing_key_from_jwt(grezzo)
        claim = jwt.decode(
            grezzo,
            chiave.key,
            algorithms=["RS256"],
            issuer=os.environ["OIDC_ISSUER"],           # esterno
            audience=os.environ["OIDC_AUDIENCE"],
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.PyJWTError as e:
        raise TokenNonValido(f"{type(e).__name__}: {e}") from e

    if not claim.get("sub"):
        raise TokenNonValido("claim sub assente")
    return claim


def gruppi(claim: dict) -> list[str]:
    """I gruppi Keycloak dell'utente.

    Un utente senza gruppi non vede nulla: il filtro e' un'intersezione, e
    l'intersezione con l'insieme vuoto e' vuota. Corretto per costruzione,
    senza casi speciali.
    """
    g = claim.get("groups") or []
    if not isinstance(g, list):
        raise TokenNonValido("claim groups non e' una lista")
    return [str(x) for x in g if x]


def aziende(gruppi_utente: list[str]) -> list[str]:
    """Le aziende a cui l'utente e' abilitato: gruppi 'azienda-<codice>'
    (decisione 53). Nessuna azienda = nessun dato, come per i gruppi.
    Stessa regola di amministrazione/logica.aziende_da_gruppi: i due servizi
    girano in container separati, la riga e' duplicata apposta."""
    return sorted({g[len("azienda-"):] for g in gruppi_utente
                   if g.startswith("azienda-") and len(g) > len("azienda-")})


def autocontrollo():
    """Chiamato all'avvio: se la configurazione e' incompleta il servizio
    NON parte. Un fallimento rumoroso al boot invece di uno silenzioso in
    esercizio."""
    servono = ["OIDC_JWKS_URL", "OIDC_ISSUER", "OIDC_AUDIENCE",
               "EGRESS_INTERNAL", "DATABASE_URL"]
    mancanti = [v for v in servono if not os.environ.get(v)]
    if mancanti:
        raise SystemExit(
            "configurazione incompleta, il servizio non parte: "
            + ", ".join(mancanti)
        )
    # Le chiavi devono essere raggiungibili: se Keycloak non risponde ora,
    # non risponderebbe nemmeno alla prima richiesta di un utente.
    for tentativo in range(10):
        try:
            _client_chiavi().get_signing_keys()
            return
        except Exception as e:
            if tentativo == 9:
                raise SystemExit(f"JWKS non raggiungibile: {e}")
            time.sleep(3)
