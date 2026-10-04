"""IL BANCO DELLA RICERCA — quante righe giuste tornano, e da dove.

    python valutazione/ricerca.py            tutte le strategie senza modello
    python valutazione/ricerca.py coord      aggiunge la query del coordinatore

PERCHE' ESISTE. `banco.py` giudica la RISPOSTA — mente? promette? nega? — e
non chiede mai «ha trovato le pagine che esistono». Due difetti da giorni (il
taglio a 300 caratteri e il `~*` senza confini di parola) l'hanno attraversato
con 27/30; una modifica che costava il 19% di richiamo su `chunks` non ha
mosso un punto. Qui non c'e' nessun giudice: si contano le righe.

COME SI LEGGE. Per ogni domanda, i documenti che POSSONO rispondere sono
scritti a mano qui sotto — letti una volta da una persona. Una riga e'
«giusta» se viene da uno di quelli. Non e' precisione vera (un documento
giusto puo' avere la pagina sbagliata), ed e' voluto: il difetto che cerchiamo
e' piu' grosso di una pagina, ed e' «ha guardato nel posto sbagliato».

LE STRATEGIE
  simile     ORDER BY SIMILE('la domanda') LIMIT 20, nessun WHERE
  indice     le descrizioni dell'indice (D21): quali documenti/pagine c'entrano
  dentro     SIMILE ristretto ai documenti che l'indice ha indicato
  coord      la query che il coordinatore scrive oggi (serve il modello)
"""
import os
import sys
import time

sys.path.insert(0, "/app")

import psycopg
from psycopg.rows import dict_row

from orchestratore import identita, indice, recupero

ACQUISTI = "Responsabile Acquisti,acquisti,azienda-decobrands,tutti".split(",")
SVILUPPO = ["azienda-luis", "sviluppo", "tutti"]
QUANTE = 20

# (domanda, tabella, gruppi, documenti che possono rispondere)
CASI = [
    ("ho bisogno di sassi rossi", "immagini", ACQUISTI,
     ("EUROSAND",)),
    ("nastri bianchi con cuori rossi", "immagini", ACQUISTI,
     ("PACKARA", "Creative_Living", "Gasper")),
    ("hai dei nastri blu?", "immagini", ACQUISTI,
     ("PACKARA", "Creative_Living", "Gasper")),
    ("palline di Natale rosse", "immagini", ACQUISTI,
     ("Creative_Living", "Holly", "Gasper")),
    ("profumatori per ambiente", "immagini", ACQUISTI,
     ("IPURO",)),
    ("a che punto siamo?", "chunks", SVILUPPO,
     ("PROBLEMI-APERTI", "RISULTATI", "SINTESI-SESSIONE", "DECISIONI-APERTE",
      "PIANO-FASE", "VALUTAZIONE-PROGETTO", "PROGETTO-MULTIAGENTE")),
    ("come e' strutturato il progetto?", "chunks", SVILUPPO,
     ("ARCHITETTURA", "PROGETTO-RAG-Aziendale", "PROGETTO-MULTIAGENTE",
      "SPECIFICA-")),
    ("quali problemi sono ancora aperti?", "chunks", SVILUPPO,
     ("PROBLEMI-APERTI", "DECISIONI-APERTE", "RISULTATI")),
    ("che modelli usiamo e perche'?", "chunks", SVILUPPO,
     ("GUIDA-MODELLI", "ANALISI-LITELLM", "JEV-RERANK", "PROGETTO-MULTIAGENTE")),
    ("cosa dice la procedura sui resi", "chunks", ACQUISTI,
     ("DOGANALE", "ESEMPI-FILE")),
]

DSN = os.environ["DATABASE_URL"]
conn = psycopg.connect(DSN, row_factory=dict_row)


def buone(righe, attesi):
    return sum(1 for r in righe
               if any(a.lower() in str(r.get("documento") or "").lower()
                      for a in attesi))


def _esegui(sql, gruppi, domanda):
    from orchestratore import grafo
    righe, _, esito = grafo._mossa_cerca(DSN, gruppi, identita.aziende(gruppi),
                                         {"sql": sql}, domanda, set())
    return ([dict(r) for r in righe], esito)


def simile(domanda, tabella, gruppi, _attesi):
    colonne = ("id, documento, page, descrizione" if tabella == "immagini"
               else "id, documento, page, content")
    return _esegui("SELECT %s FROM %s ORDER BY SIMILE('%s') LIMIT %d"
                   % (colonne, tabella, domanda.replace("'", "''"), QUANTE),
                   gruppi, domanda)


def dall_indice(domanda, tabella, gruppi, _attesi):
    """Le righe dei documenti che l'INDICE indica, ordinate per somiglianza."""
    vicine = indice.vicine(conn, recupero.embedding(domanda, query=True),
                           gruppi, documenti=4, pagine=8)
    nomi = sorted({d for d, _p, _t in vicine})
    if not nomi:
        return [], "indice vuoto"
    colonne = ("id, documento, page, descrizione" if tabella == "immagini"
               else "id, documento, page, content")
    dentro = " OR ".join("documento = '%s'" % n.replace("'", "''")
                         for n in nomi)
    return _esegui("SELECT %s FROM %s WHERE (%s) ORDER BY SIMILE('%s') LIMIT %d"
                   % (colonne, tabella, dentro, domanda.replace("'", "''"),
                      QUANTE),
                   gruppi, domanda)


def indice_solo(domanda, _tabella, gruppi, attesi):
    """Quante delle DESCRIZIONI che l'indice porta vengono dai documenti giusti.

    Non e' una ricerca di righe: misura il primo stadio da solo, perche' se
    sbaglia lui sbaglia tutto quello che viene dopo."""
    vicine = indice.vicine(conn, recupero.embedding(domanda, query=True),
                           gruppi, documenti=4, pagine=8)
    finte = [{"documento": d} for d, _p, _t in vicine]
    return finte, "%d descrizioni" % len(finte)


def coordinatore(domanda, _tabella, gruppi, _attesi):
    from orchestratore import grafo
    st = {"domanda": domanda, "storia": [{"role": "user", "content": domanda}],
          "conn": conn, "dsn": DSN, "gruppi": gruppi,
          "aziende": identita.aziende(gruppi), "query_fatte": [], "eseguite": 0,
          "respinte": 0, "riviste": 0, "impegnativa": True, "rispondibile": True,
          "ambito": "", "manca": [], "mosse": [], "esiti": [], "chiarimento": "",
          "su_pezzo": None, "base": "", "utente": "", "resa": False,
          "righe": [], "verdetti": {}, "confermate": [], "etichette": {},
          "risposta": "", "passi": 0, "oggetto": "", "attributi": [],
          "dove": "", "campione": [], "traccia": []}
    st.update(grafo._nodo_analista(st))
    st["passi"] = 0
    st.update(grafo._nodo_coordinatore(st))
    sql = next((a.get("sql") for n, a in st.get("mosse") or [] if n == "cerca"),
               None)
    if not sql:
        mosse = ",".join(n for n, _ in st.get("mosse") or [])
        return [], "nessuna cerca (%s), dove=%s" % (mosse or "-", st.get("dove"))
    righe, esito = _esegui(sql, gruppi, domanda)
    return righe, esito


STRATEGIE = [("simile", simile), ("indice", indice_solo), ("dentro", dall_indice)]
if "coord" in sys.argv:
    STRATEGIE.append(("coord", coordinatore))


def main():
    # Con `dict_row` due `count(*)` senza nome diventano la stessa chiave.
    conta = conn.execute(
        "select count(*) filter (where page is null) as documenti, "
        "count(*) filter (where page is not null) as pagine from indice").fetchone()
    doc, pag = conta["documenti"], conta["pagine"]
    print("IL BANCO DELLA RICERCA — indice: %d documenti, %d pagine\n" % (doc, pag))
    testa = "%-36s" % "domanda" + "".join("%-12s" % n for n, _ in STRATEGIE)
    print(testa)
    print("-" * len(testa))
    somme = {n: 0 for n, _ in STRATEGIE}
    for domanda, tabella, gruppi, attesi in CASI:
        riga, note = "%-36s" % domanda[:36], []
        for nome, strategia in STRATEGIE:
            t0 = time.monotonic()
            try:
                righe, esito = strategia(domanda, tabella, gruppi, attesi)
            except Exception as e:
                riga += "%-12s" % "errore"
                note.append("%s: %s: %s" % (nome, type(e).__name__, e))
                continue
            n = buone(righe, attesi)
            somme[nome] += n
            riga += "%-12s" % ("%d/%d" % (n, len(righe)))
            if not righe or "nessuna" in esito:
                note.append("%s: %s" % (nome, esito))
            del t0
        print(riga, flush=True)
        for nota in note:
            print("    " + nota)
    print("-" * len(testa))
    print("%-36s" % "righe giuste in totale"
          + "".join("%-12s" % somme[n] for n, _ in STRATEGIE))


if __name__ == "__main__":
    main()
