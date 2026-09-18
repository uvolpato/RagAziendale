"""Cifratura dei segreti dei collegamenti (SPECIFICA-CONNETTORI.md §4.1).

Cifratura autenticata con Fernet (AES + HMAC): la crittografia non si scrive
a mano, e `cryptography` e' gia' dipendenza indiretta di PyJWT. Le chiavi non
stanno nel database: `CHIAVE_CREDENZIALI` (e `CHIAVE_CREDENZIALI_PRECEDENTE`
per la rotazione) sono in .env.

Il modulo e' puro: prende le chiavi come argomenti, cosi' si testa da solo e
non legge mai l'ambiente. Il collegamento chiave <-> versione lo fa il servizio.
"""
import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken


class SegretoIlleggibile(ValueError):
    """Il segreto non si decifra: chiave sbagliata, o blob corrotto."""


def chiave_fernet(chiave):
    """Una chiave Fernet valida da qualunque stringa in .env.

    Se e' gia' una chiave Fernet (url-safe base64 di 32 byte, come da
    `openssl rand -base64 32`) la si usa com'e'; altrimenti la si deriva con
    sha256: cosi' chi compila .env non deve indovinare il formato.
    """
    if not chiave:
        raise ValueError("chiave vuota")
    try:
        grezzo = base64.urlsafe_b64decode(chiave.encode())
        if len(grezzo) == 32:
            return Fernet(base64.urlsafe_b64encode(grezzo))
    except Exception:
        pass
    derivata = hashlib.sha256(chiave.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(derivata))


def cifra(segreto, chiave):
    """Il segreto (dict) cifrato come blob binario per la colonna `segreti`."""
    return chiave_fernet(chiave).encrypt(
        json.dumps(segreto, sort_keys=True, ensure_ascii=False).encode("utf-8"))


def decifra(blob, chiave):
    """Il segreto decifrato, o SegretoIlleggibile."""
    try:
        testo = chiave_fernet(chiave).decrypt(bytes(blob))
        return json.loads(testo.decode("utf-8"))
    except (InvalidToken, ValueError, TypeError) as e:
        raise SegretoIlleggibile("chiave non valida o segreto corrotto") from e


def ricifra(blob, chiave_vecchia, chiave_nuova):
    """Per la rotazione: decifra con la vecchia, ricifra con la nuova."""
    return cifra(decifra(blob, chiave_vecchia), chiave_nuova)
