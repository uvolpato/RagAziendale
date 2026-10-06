"""LA SCELTA, data al modello da sola.

    docker compose exec -T orchestratore python /app/valutazione/prova-scelta.py [volte]

PERCHE' ESISTE. Il 5/10/2026 la mossa `leggi` e' entrata nel flusso e in 30
giri di banco non e' stata scelta nemmeno una volta. Davanti a un fatto cosi'
ci sono due spiegazioni opposte, e confonderle costa la notte:

- **limite del modello**: il 14b non capisce che «a che punto siamo?» si
  risponde aprendo un documento;
- **difetto nostro**: lo capisce benissimo, e il flusso gli mette davanti uno
  stato in cui quella mossa non sembra la sua.

Questo file le separa. Niente flusso, niente redattore, niente giudice: si
prende la decisione ESATTA che il coordinatore prende — il suo prompt vero, la
mappa vera, lo stato vero — e si guarda cosa sceglie, N volte. Se in
isolamento sceglie giusto e nel flusso no, il difetto e' nostro e si ripara in
casa. Se sbaglia anche qui, il modello e' il collo di bottiglia e la risposta
e' provarne uno piu' grande — non aggiungere una frase al prompt.

COME SI LEGGE. Per ogni domanda c'e' la mossa ATTESA, scritta a mano da chi la
domanda l'ha pensata, e la distribuzione di quello che il modello ha scelto.
Due giri: il primo da stato vuoto, il secondo dopo un `esplora` VERO (eseguito
sul database, non finto), che e' il momento in cui il banco lo vede girare a
vuoto.

Le domande di catalogo ci sono come CONTROLLO: se `leggi` si prende anche
quelle, il guadagno sui testi lo paghiamo sui cataloghi, e si vede subito.
"""
import collections
import os
import sys

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, "/app")

from orchestratore import grafo, identita  # noqa: E402

GRUPPI = ["azienda-luis", "sviluppo", "acquisti"]

# L'attesa e' scritta qui, non dedotta: e' il metro, e un metro si discute
# prima di misurare. `leggi` quando la risposta STA SCRITTA in un documento,
# `cerca` quando la risposta e' una riga o un conteggio.
DOMANDE = [
    {"domanda": "a che punto siamo col progetto?",
     "attesa": {"esplora", "leggi"}, "poi": {"leggi"},
     "perche": "lo stato di un progetto sta scritto in un documento, non in una riga"},
    {"domanda": "cosa dice la procedura doganale sulle importazioni?",
     "attesa": {"esplora", "leggi"}, "poi": {"leggi"},
     "perche": "una procedura si legge, non si cerca per parola"},
    {"domanda": "quali sono i modelli di riferimento che usiamo?",
     "attesa": {"esplora", "leggi", "cerca"}, "poi": {"leggi"},
     "perche": "sta scritto nelle regole del progetto"},
    {"domanda": "com'e' finita la decisione su LiteLLM?",
     "attesa": {"esplora", "leggi"}, "poi": {"leggi"},
     "perche": "una decisione e' raccontata in un documento, e ha una data"},
    # CONTROLLO: qui `leggi` sarebbe sbagliata.
    {"domanda": "quanti cataloghi ci sono?",
     "attesa": {"cerca", "esplora"}, "poi": {"cerca"},
     "perche": "e' un conteggio su `documenti`, non un testo da leggere"},
    {"domanda": "hai palline di Natale rosse?",
     "attesa": {"cerca"}, "poi": {"cerca"},
     "perche": "il prodotto sta nelle didascalie: e' una riga"},
]


def stato_base(conn, domanda):
    """Lo stato come lo costruisce `grafo.cerca`, senza il resto del flusso."""
    return {
        "domanda": domanda, "intent": "", "colore": [], "contesto": [],
        "messaggi": [], "storia": [{"role": "user", "content": domanda}],
        "conn": conn, "dsn": os.environ["DATABASE_URL"],
        "gruppi": GRUPPI, "aziende": identita.aziende(GRUPPI),
        "query_fatte": [], "eseguite": 0, "respinte": 0, "riviste": 0,
        "impegnativa": True, "rispondibile": True, "ambito": "", "manca": [],
        "mosse": [], "esiti": [], "chiarimento": "", "su_pezzo": None,
        "base": "", "utente": "prova", "resa": False, "oggetto": "",
        "attributi": [], "dove": "", "righe": [], "verdetti": {},
        "confermate": [], "campione": [], "etichette": {}, "risposta": "",
        "passi": 0, "traccia": [],
    }


def un_giro(conn, domanda):
    """(mosse del primo giro, mosse del giro dopo un esplora vero, dove).

    L'analista gira per davvero: se e' lui a sbagliare `dove` su una domanda
    di testo, si deve vedere qui e non piu' tardi.
    """
    stato = stato_base(conn, domanda)
    stato.update(grafo._nodo_analista(stato) or {})
    prima = grafo._nodo_coordinatore(stato) or {}
    stato.update(prima)
    mosse1 = [n for n, _ in (prima.get("mosse") or [])]

    # Il secondo giro parte da un `esplora` ESEGUITO: e' lo stato in cui il
    # banco vede il coordinatore ripetersi.
    stato["mosse"] = [("esplora", {"cerca": domanda})]
    stato.update(grafo._nodo_mosse(stato) or {})
    stato["passi"] = 1
    dopo = grafo._nodo_coordinatore(stato) or {}
    mosse2 = [n for n, _ in (dopo.get("mosse") or [])]
    return mosse1, mosse2, stato.get("dove") or "?"


# La prova NUDA: gli stessi strumenti, senza il nostro prompt.
#
# E' la domanda che separa le due spiegazioni in modo definitivo. Qui il
# modello riceve cinque righe di contesto, le descrizioni VERE delle mosse e
# l'esito VERO di un `esplora`. Niente mappa dei dati, niente regole del
# coordinatore, niente stato. Se qui sceglie bene e nel flusso no, non e' il
# modello: e' quello che gli mettiamo intorno.
NUDO = """Rispondi a domande su un archivio aziendale di documenti e cataloghi.
Hai delle mosse a disposizione. Scegline UNA, quella che porta alla risposta."""


def un_giro_nudo(conn, domanda):
    """La scelta col prompt ridotto al minimo, dopo un esplora vero."""
    stato = stato_base(conn, domanda)
    stato["mosse"] = [("esplora", {"cerca": domanda})]
    stato.update(grafo._nodo_mosse(stato) or {})
    esiti = "\n".join(stato.get("esiti") or [])
    messaggi = [{"role": "system", "content": NUDO},
                {"role": "user", "content": domanda},
                {"role": "user", "content": "Hai gia' guardato l'indice "
                                            "dell'archivio, ed ecco cosa "
                                            "dice:\n" + esiti}]
    scelte = grafo._decide(messaggi, grafo._strumenti_del_coordinatore())
    return [n for n, _ in (scelte or [])]


def main():
    volte = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    if "--nudo" in sys.argv:
        with psycopg.connect(os.environ["DATABASE_URL"],
                             row_factory=dict_row) as conn:
            print("LA SCELTA NUDA — senza il nostro prompt, %d volte\n" % volte)
            for caso in DOMANDE:
                c = collections.Counter()
                for _ in range(volte):
                    c.update(un_giro_nudo(conn, caso["domanda"]) or ["(niente)"])
                giuste = sum(v for k, v in c.items() if k in caso["poi"])
                print("«%s»\n   atteso %s -> %s   giuste %d/%d"
                      % (caso["domanda"], "/".join(sorted(caso["poi"])),
                         dict(c), giuste, volte))
        return
    with psycopg.connect(os.environ["DATABASE_URL"],
                         row_factory=dict_row) as conn:
        print("LA SCELTA IN ISOLAMENTO — %d volte per domanda\n" % volte)
        for caso in DOMANDE:
            uno, due = collections.Counter(), collections.Counter()
            dove = set()
            for _ in range(volte):
                m1, m2, d = un_giro(conn, caso["domanda"])
                uno.update(m1 or ["(niente)"])
                due.update(m2 or ["(niente)"])
                dove.add(d)
            giuste = sum(v for k, v in due.items() if k in caso["poi"])
            print("«%s»" % caso["domanda"])
            print("   atteso dopo esplora: %s — %s"
                  % ("/".join(sorted(caso["poi"])), caso["perche"]))
            print("   dove (analista): %s" % ", ".join(sorted(dove)))
            print("   1o giro:  %s" % dict(uno))
            print("   2o giro:  %s   ->  giuste %d/%d"
                  % (dict(due), giuste, volte))
            print()


if __name__ == "__main__":
    main()
