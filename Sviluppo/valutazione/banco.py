"""Il BANCO: la stessa batteria, N volte, con un verdetto per ogni giro.

    D=/tmp/val-$(date +%s)
    docker compose cp valutazione orchestratore:$D
    docker compose exec -T orchestratore sh -c "cd /app && python $D/banco.py 3 > /var/tmp/banco.txt 2>&1"
    docker compose exec -T orchestratore cat /var/tmp/banco.txt

Una cartella NUOVA ogni volta (`cp` su una che esiste copia DENTRO, e si
rimisura la versione vecchia credendo di aver cambiato qualcosa). L'esito
si scrive in `/var/tmp`, non nella cartella copiata: quella arriva di root.

PERCHE' ESISTE. Il 2/10/2026 ho cambiato quattro prompt e dopo ognuno ho
concluso qualcosa da UNA esecuzione. Tre di quelle conclusioni erano
sbagliate, e me ne sono accorto solo rifacendo la prova per caso:
«l'analista non scrive piu' `manca: prezzo`» (lo riscriveva, 1 su 2), «il
compleanno e' tornato a 151 secondi» (due giri dopo faceva 65 e 47), «ha
risposto senza interrogare l'archivio» (un caso su tre). Su un modello che
decide, una esecuzione non e' una misura: e' un aneddoto.

COSA GIUDICA, e perche' cosi'. Non la bellezza della risposta — per quella
c'e' `aperte.py`, che fa giudicare un modello. Qui si guardano solo cose
che il CODICE puo' verificare da solo, perche' servono a dire «questa
modifica ha rotto qualcosa» senza che io debba leggere venti risposte:

    mosse       quali agenti sono stati chiamati, in ordine
    eseguite    quante query sono davvero andate al database
    citate      quante righe la risposta nomina con [[n]]
    secondi     quanto e' costato

Ogni domanda porta la sua ATTESA, scritta come una proprieta' strutturale e
mai come «deve rispondere X»: «un saluto non interroga l'archivio», «un
dato sull'archivio non si inventa, si legge», «una domanda troppo generica
si chiede, e la si chiede sulle strade lette nei dati». Se un giorno il
catalogo cambia, queste attese restano vere; una risposta attesa no.
"""
import json
import os
import pathlib
import re
import statistics
import sys
import time

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, "/app")
from orchestratore import grafo                                  # noqa: E402

GRUPPI = os.environ.get("VAL_GRUPPI", "Responsabile Acquisti,acquisti,"
                                      "azienda-decobrands,tutti").split(",")
QUI = pathlib.Path(__file__).resolve().parent


# --------------------------------------------------------------------------
# LE ATTESE. Ognuna riceve il riepilogo di un giro e dice si'/no + perche'.
# Sono proprieta' del COMPORTAMENTO, non del contenuto: cosi' valgono anche
# quando i cataloghi cambiano.
# --------------------------------------------------------------------------
def non_cerca(g):
    """Un saluto, una chiacchiera: non c'e' niente da cercare."""
    return (g["eseguite"] == 0,
            "ha eseguito %d query" % g["eseguite"] if g["eseguite"] else "")


def interroga(g):
    """Un dato sull'archivio non si ricorda: si legge. Rispondere senza aver
    eseguito una query vuol dire inventare il numero."""
    return (g["eseguite"] >= 1, "zero query eseguite: il dato e' inventato"
            if g["eseguite"] == 0 else "")


def trova_e_cita(g):
    """Una domanda di prodotto finisce con delle righe citate: senza `[[n]]`
    la risposta non e' tracciabile a una pagina."""
    if g["eseguite"] == 0:
        return False, "non ha nemmeno cercato"
    if g["citate"] == 0:
        return False, "nessuna riga citata con [[n]]"
    return True, ""


def ventaglio(g):
    """Troppo generica: si chiede, e la domanda si costruisce LEGGENDO
    l'archivio (`guida`). Senza, le strade se le inventa — «accessori,
    decorazioni» non sono reparti di questo archivio."""
    if "guida" not in g["mosse"]:
        return False, "nessun ventaglio: la domanda se l'e' inventata"
    return True, ""


def ventaglio_poi_basta(g):
    """Come sopra, e una volta costruito il ventaglio non si cerca: quelle
    righe non le guardera' nessuno, e il giro costa."""
    ok, perche = ventaglio(g)
    if not ok:
        return ok, perche
    if g["eseguite"]:
        return False, "ventaglio pronto e poi ha cercato lo stesso (%d query)" % g["eseguite"]
    return True, ""


def non_pappagalla(g):
    """Le frasi letterali nei prompt sono la cosa che funziona meglio e il
    rischio piu' grosso insieme: il modello le ricopia. Misurato il
    2/10/2026 nei due versi — tolto l'esempio concreto dal redattore,
    «seguito» e' passato da 3/3 a 0/3; rimesso, e' tornato 3/3. Ma la
    frase usciva identica tre volte su tre, e li' per caso era vera.

    Questa domanda e' la trappola: si chiede una cosa che NON c'entra con
    l'esempio scritto nel prompt. Se la pallina giallo-verde ricompare
    qui, il modello non sta rispondendo: sta recitando."""
    fuori = [p for p in ("giallo-verde", "fiocchi di neve", "i cataloghi sono 13",
                         "ivory") if p in g["risposta"].lower()]
    if fuori:
        return False, "ha ricopiato l'esempio del prompt: " + ", ".join(fuori)
    return True, ""


def niente_prezzi(g):
    """In archivio non c'e' un solo importo (0 righe con una cifra tipo
    12,50 su 17582). Qualunque prezzo nella risposta e' inventato."""
    if re.search(r"\d+[.,]\d{2}\s*(?:€|eur|euro)|[€$£]\s*\d", g["risposta"], re.I):
        return False, "ha scritto un prezzo, e in archivio non ce ne sono"
    return True, ""


BATTERIA = [
    {"nome": "saluto", "domanda": "ciao", "attesa": non_cerca},
    {"nome": "archivio", "domanda": "quanti cataloghi ci sono?",
     "attesa": interroga},
    {"nome": "prodotto", "domanda": "ho bisogno di nastri bianchi con cuori rossi",
     "attesa": trova_e_cita},
    {"nome": "generica", "domanda": "mi serve qualcosa di blu",
     "attesa": ventaglio_poi_basta},
    {"nome": "aperta-regalo", "domanda": "mi proponi qualcosa per il compleanno di mia mamma?",
     "attesa": ventaglio},
    {"nome": "aperta-tema", "domanda": "cosa avete di sportivo?",
     "attesa": ventaglio},
    {"nome": "seguito", "domanda": "niente gialle?",
     "storia": [("user", "hai palline di Natale rosse?"),
                ("assistant", "Si', ne ho trovate: palline di Natale rosse "
                              "lucide nel catalogo Packara.")],
     "attesa": trova_e_cita},
    {"nome": "pappagallo", "domanda": "e di blu invece?",
     "storia": [("user", "hai palline di Natale rosse?"),
                ("assistant", "Si', ne ho trovate: palline di Natale rosse "
                              "lucide nel catalogo Packara.")],
     "attesa": non_pappagalla},
    {"nome": "senza-dato", "domanda": "quanto costano?",
     "storia": [("user", "hai palline di Natale rosse?"),
                ("assistant", "Si', ne ho trovate: palline di Natale rosse "
                              "lucide nel catalogo Packara.")],
     "attesa": niente_prezzi},
]


def un_giro(conn, caso):
    storia = [{"role": r, "content": c} for r, c in caso.get("storia", [])]
    storia.append({"role": "user", "content": caso["domanda"]})
    t0 = time.monotonic()
    try:
        righe, risposta, meta = grafo.cerca(conn, caso["domanda"], GRUPPI,
                                            storia=storia)
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"esploso": "%s: %s" % (type(e).__name__, e), "secondi": 0,
                "mosse": [], "eseguite": 0, "citate": 0, "risposta": ""}
    ric = meta.get("ricerca") or ""
    eseguite = int((re.search(r"query=(\d+)ok", ric) or [0, 0])[1])
    return {"esploso": None,
            "secondi": time.monotonic() - t0,
            "mosse": [s["strumento"] for s in meta.get("strumenti") or []],
            "eseguite": eseguite,
            "citate": len({int(n) for n in re.findall(r"\[\[\s*(\d+)\s*\]\]",
                                                      risposta or "")}),
            "risposta": risposta or ""}


def main():
    volte = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    solo = sys.argv[2] if len(sys.argv) > 2 else None
    casi = [c for c in BATTERIA if not solo or c["nome"] == solo]
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    print("IL BANCO — %d domande x %d giri\n" % (len(casi), volte))
    print("%-14s %-7s %-9s %s" % ("caso", "esito", "secondi", "cosa non va"))
    print("-" * 82)
    totali, esiti = [], {}
    for caso in casi:
        ok_n, tempi, motivi, giri = 0, [], [], []
        for _ in range(volte):
            g = un_giro(conn, caso)
            giri.append(g)
            if g["esploso"]:
                motivi.append(g["esploso"])
                continue
            tempi.append(g["secondi"])
            ok, perche = caso["attesa"](g)
            ok_n += bool(ok)
            if perche:
                motivi.append(perche)
        mediana = statistics.median(tempi) if tempi else 0
        print("%-14s %d/%-5d %6.0fs    %s"
              % (caso["nome"], ok_n, volte, mediana,
                 "; ".join(dict.fromkeys(motivi))[:46]))
        totali.append((caso["nome"], ok_n, volte, mediana))
        esiti[caso["nome"]] = giri
    print("-" * 82)
    print("TOTALE  %d / %d        tempo mediano complessivo %.0fs"
          % (sum(t[1] for t in totali), sum(t[2] for t in totali),
             sum(t[3] for t in totali)))

    # Le mosse e le risposte, per leggere a mano quando un numero sorprende.
    print("\n\n== I GIRI ==")
    for nome, giri in esiti.items():
        print("\n«%s»" % nome)
        for g in giri:
            if g["esploso"]:
                print("   ESPLOSO %s" % g["esploso"])
                continue
            print("   %3.0fs %-2dq %-2dcit  %s" % (
                g["secondi"], g["eseguite"], g["citate"], " > ".join(g["mosse"])))
            print("        %s" % " ".join(g["risposta"].split())[:150])

    (pathlib.Path("/var/tmp") / "banco.json").write_text(
        json.dumps({n: [{k: v for k, v in g.items() if k != "risposta"}
                        for g in gg] for n, gg in esiti.items()},
                   ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
