"""Riformulazione della domanda per la RICERCA (non per la risposta).

Il recupero cerca l'ultimo messaggio dell'utente, e basta. In una conversazione
questo si rompe al secondo turno: «ne ho bisogno in auto» non dice di cosa, e
«posso vedere la foto del lime light?» fa pescare il limone nelle foto di un
catalogo di sabbie (visto il 20/09/2026: la risposta arrivava dai vasi).

Qui si prendono le ultime battute e si chiede al modello VELOCE di riscrivere
la domanda in forma autonoma: «foto del profumatore IPURO Lime Light». Si cerca
quella. Tre limiti che rendono il passo innocuo:

- **i permessi non c'entrano**: si riscrivono solo le parole da cercare, il
  filtro su gruppi e aziende resta nella query SQL;
- **la risposta vede la conversazione vera**, non la riscrittura;
- **se fallisce non blocca niente**: si torna alla domanda originale, cioe' al
  comportamento di prima.

Non si allega tutta la conversazione alla ricerca: la media di profumatori +
auto + immagini + lime e' un punto vicino a tutto e a niente, e la ricerca
testuale cercherebbe ogni parola mai detta. Una domanda precisa vale piu' di
un riassunto.
"""
import json
import os

from orchestratore import egress

LITELLM = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000").rstrip("/")
MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
# Rotta veloce se c'e', altrimenti il modello principale: la riscrittura e' un
# lavoro corto e meccanico, non merita il modello grosso, ma non vale la pena
# rinunciarci se il veloce non e' configurato.
VELOCE = os.environ.get("LLM_VELOCE", "veloce")
PRINCIPALE = os.environ.get("LLM_RAGIONAMENTO", "ragionamento")
SECONDI = float(os.environ.get("RIFORMULA_TIMEOUT", "15"))
# Oltre questa lunghezza la domanda si regge da sola: riscriverla costa un
# secondo e non aggiunge niente.
MAX_CARATTERI = 200
BATTUTE = 4          # quante battute precedenti bastano a sciogliere un "ne"

ISTRUZIONI = (
    "Riscrivi l'ultima domanda in UNA domanda autonoma per un motore di ricerca documentale.\n"
    "Sostituisci i riferimenti impliciti (ne, quello, questo, la seconda) con i nomi espliciti "
    "presi dalla conversazione; conserva marche, linee di prodotto e nomi di documento.\n"
    "Rispondi SOLO con la domanda riscritta: una riga, niente virgolette, niente spiegazioni.\n"
    "\n"
    "Esempio\n"
    "Conversazione:\n"
    "Utente: quali fragranze ha la linea IPURO Essentials?\n"
    "Assistente: Offre fragranze floreali e fruttate.\n"
    "Ultima domanda: ne ho bisogno in auto\n"
    "Riscrittura: fragranze IPURO Essentials per auto"
)


# Rotte che hanno gia' risposto male: non si ritentano a ogni turno. Oggi
# `veloce` non e' configurato in litellm-config e risponde 400: la riformulazione
# ripiega sul modello principale, e senza questa memoria pagherebbe una chiamata
# a vuoto per ogni domanda.
_rotte_rotte = set()


def _chiedi(rotta, messaggi):
    testa = {"Authorization": f"Bearer {MASTER_KEY}"} if MASTER_KEY else {}
    corpo = {"model": rotta, "messages": messaggi, "temperature": 0, "max_tokens": 120}
    with egress.client(timeout=SECONDI, verify=False) as c:
        r = c.post(f"{LITELLM}/v1/chat/completions", json=corpo, headers=testa)
        r.raise_for_status()
        return (r.json()["choices"][0]["message"].get("content") or "").strip()


def per_la_ricerca(domanda: str, storico: list, suffisso: str = "") -> tuple[str, bool]:
    """(domanda da cercare, e' stata riscritta).

    `storico` sono i messaggi della conversazione COMPRESA quella di adesso.
    Si riscrive solo se c'e' davvero una conversazione dietro.
    """
    precedenti = [m for m in storico
                  if isinstance(m.get("content"), str) and m.get("role") in ("user", "assistant")][:-1]
    if not precedenti or len(domanda) > MAX_CARATTERI:
        return domanda, False
    conversazione = "\n".join(
        f"{'Utente' if m['role'] == 'user' else 'Assistente'}: {m['content'][:500]}"
        for m in precedenti[-BATTUTE:])
    messaggi = [{"role": "system", "content": (suffisso + "\n" if suffisso else "") + ISTRUZIONI},
                {"role": "user",
                 "content": f"Conversazione:\n{conversazione}\n\nUltima domanda: {domanda}\nRiscrittura:"}]
    for rotta in (VELOCE, PRINCIPALE):
        if rotta in _rotte_rotte:
            continue
        try:
            testo = _chiedi(rotta, messaggi)
        except Exception as e:
            print(f"riformulazione non riuscita su {rotta}: {type(e).__name__}: {e}", flush=True)
            _rotte_rotte.add(rotta)
            continue
        testo = testo.strip().strip('"').strip()
        # Una riga sola: se il modello si dilunga si tiene la prima frase utile.
        testo = next((r.strip() for r in testo.splitlines() if r.strip()), "")
        for scoria in ("/no_think", "/think", "Riscrittura:"):
            testo = testo.replace(scoria, "").strip()
        if testo and len(testo) <= 300:
            return testo, testo.lower() != domanda.strip().lower()
        return domanda, False
    return domanda, False


def _prova():
    """Controllo delle regole che non chiamano il modello."""
    assert per_la_ricerca("ho bisogno di profumatori", []) == ("ho bisogno di profumatori", False)
    solo_questa = [{"role": "user", "content": "ho bisogno di profumatori"}]
    assert per_la_ricerca("ho bisogno di profumatori", solo_questa)[1] is False
    lunga = "x" * (MAX_CARATTERI + 1)
    storico = [{"role": "user", "content": "prima"}, {"role": "assistant", "content": "poi"},
               {"role": "user", "content": lunga}]
    assert per_la_ricerca(lunga, storico) == (lunga, False), "una domanda lunga non si riscrive"
    print("riformula: regole locali ok")


if __name__ == "__main__":
    _prova()
