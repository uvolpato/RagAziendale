"""Interroga l'orchestratore dall'interno della rete Docker (eseguito con
docker compose exec -T orchestratore python /tmp/verifica_chat.py).

Recupera il token di prova.sicurezza da Keycloak (http://keycloak:8080) e
interroga /v1/chat/completions dell'orchestratore locale (localhost:8000).
La password di prova va passata via TEST_USER_PASSWORD (in .env).
"""
import json
import os
import urllib.request

PW = os.environ["TEST_USER_PASSWORD"]

DOMANDE = [
    "Cosa dice la policy sulla gestione delle password?",
    "A chi vanno segnalati gli incidenti di sicurezza?",
    "Che cos'e' il GDPR e come viene citato nei documenti?",
    "Cosa prevede la formazione e sensibilizzazione dei dipendenti?",
    "Quali standard ISO/IEC 27000 sono citati?",
]


def token(utente="prova.sicurezza"):
    dati = urllib.parse.urlencode({
        "grant_type": "password", "client_id": "test-token",
        "username": utente, "password": PW}).encode()
    r = urllib.request.urlopen(
        "http://keycloak:8080/realms/azienda/protocol/openid-connect/token",
        data=dati, timeout=20)
    return json.load(r)["access_token"]


def chiedi(tok, domanda):
    corpo = json.dumps({
        "model": "assistente-v1",
        "messages": [{"role": "user", "content": domanda}],
        "stream": True,
    }).encode()
    req = urllib.request.Request(
        "http://localhost:8000/v1/chat/completions", data=corpo,
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json",
                 "X-Conversation-Id": "test-chat-1"})
    testo = []
    with urllib.request.urlopen(req, timeout=300) as r:
        for riga in r:
            riga = riga.decode("utf-8", "replace").strip()
            if not riga.startswith("data:") or "[DONE]" in riga:
                continue
            try:
                p = json.loads(riga[5:].strip())
            except ValueError:
                continue
            delta = p.get("choices", [{}])[0].get("delta", {})
            if delta.get("content"):
                testo.append(delta["content"])
    return "".join(testo).strip()


def main():
    tok = token()
    for i, d in enumerate(DOMANDE, 1):
        print(f"\n===== DOMANDA {i} =====")
        print("Q:", d)
        try:
            r = chiedi(tok, d)
            print("A:", r if r else "(risposta vuota)")
        except Exception as e:
            print("ERRORE:", f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
