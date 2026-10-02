"""LA TARATURA DEL GIUDICE: tre risposte sbagliate di cui so il verdetto.

    D=/tmp/val-$(date +%s)
    docker compose cp valutazione orchestratore:$D
    docker compose exec -T orchestratore sh -c "cd /app && python $D/calibra.py"

Si lancia ogni volta che si tocca `P_GIUDICE` in banco.py, PRIMA di
credere a un punteggio.

Un metro va tarato in due versi, e io ne avevo controllato uno solo. Che
non suoni quando non deve: lo si vede subito, perche' i casi buoni
passano. Che suoni quando DEVE: non si vede affatto, e un giudice che
assolve tutto da' punteggi bellissimi.

E' lo stesso errore per cui la prima versione del banco, che giudicava con
le regex, promuoveva «No, non ho trovato palline di Natale gialle. [[1]]»
— il [[n]] c'era, il criterio taceva, il totale diceva 4/5.

Le tre risposte qui sotto sono VERE: le ha scritte il sistema il
2/10/2026, una per ciascuna delle tre colpe. Se il giudice non le
riconosce, non e' usabile — e un punteggio preso con un giudice non tarato
e' peggio di nessun punteggio, perche' ci si costruiscono sopra delle
decisioni."""
import os, sys
import psycopg
from psycopg.rows import dict_row
sys.path.insert(0, "/app")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import banco
from orchestratore import identita, operatori

G = banco.GRUPPI
conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
A = identita.aziende(G)

PROVE = [
 ("promette", "ci sono anche con i cuori blu?",
  [("user","ho bisogno di nastri bianchi con cuori rossi"),
   ("assistant","Ho trovato diversi nastri bianchi con cuori rossi a pagina 39 e 5.")],
  "Ho trovato nastri con cuori blu: 1. Nastro in tessuto con cuori rossi e "
  "puntini rossi, bordi visibilmente cuciti, codice 01 pagina 39. 2. Due nastri "
  "in tessuto con motivo a cuori, superficie texturizzata, codice 01 pagina 27."),
 ("nega", "cosa avete di sportivo?", [],
  "Ciao! Non ho trovato informazioni specifiche su prodotti sportivi "
  "nell'archivio. Tuttavia, ci sono alcune opzioni che potrebbero interessarti: "
  "- Figurine di un gatto cartoon in posa atletica con manubri - Figurine di "
  "Babbo Natale sul tapis roulant - Confezioni regalo con bottiglie e scatole"),
 ("non_verificabile", "ho bisogno di nastri bianchi con cuori rossi", [],
  "Ho trovato diversi nastri bianchi con cuori rossi nei cataloghi Packara: "
  "- Pagina 39: nastro bianco con cuori rossi, codice 01. - Pagina 5: tre "
  "nastri con motivi a cuore su fondo bianco."),
]

# E tre risposte GIUSTE, perche' un giudice si tara in due versi. La terza
# e' quella che mi mancava e che mi e' costata un falso allarme sistematico:
# a una domanda vaga il sistema mostra fra cosa scegliere, e li' non cita
# niente per costruzione — zero righe citate non e' una prova di colpa.
BUONE = [
 ("archivio", "quanti cataloghi ci sono?", [], "i cataloghi sono 13 [[1]]."),
 ("prodotto", "ho bisogno di nastri bianchi con cuori rossi", [],
  "Ho trovato diversi nastri bianchi con cuori rossi: - Nastro bianco con "
  "cuori rossi [[1]] - Nastro con motivi a cuore su fondo bianco [[2]]."),
 ("ventaglio", "mi proponi qualcosa per il compleanno di mia mamma?", [],
  "Ci sono confezioni regalo, diffusori per ambienti e decorazioni natalizie. "
  "- Confezioni regalo eleganti - Diffusori per ambienti profumati - "
  "Decorazioni natalizie. Vuoi esplorare una di queste opzioni?"),
]

for attesa, domanda, storia, risposta in PROVE:
    caso = {"attesa": next(c["attesa"] for c in banco.BATTERIA
                           if c["domanda"] == domanda)}
    conv = [{"role": r, "content": c} for r, c in storia]
    conv.append({"role": "user", "content": domanda})
    campione = operatori.vicinato(conn, "immagini", domanda, G, A, quanti=10)
    v = banco.giudica(caso, conv, risposta, [], campione) or {}
    trovate = [c for c, _ in banco.COLPE if v.get(c)]
    print("%-18s attesa=%-18s trovate=%s  %s"
          % ("OK " if attesa in trovate else "MANCATA",
             attesa, trovate or "nessuna",
             str(v.get(attesa + "_dove") or "")[:80]))

print()
for nome, domanda, storia, risposta in BUONE:
    caso = {"attesa": next((c["attesa"] for c in banco.BATTERIA
                            if c["domanda"] == domanda), "")}
    conv = [{"role": r, "content": c} for r, c in storia]
    conv.append({"role": "user", "content": domanda})
    campione = operatori.vicinato(conn, "immagini", domanda, G, A, quanti=10)
    v = banco.giudica(caso, conv, risposta, [], campione) or {}
    trovate = [c for c, _ in banco.COLPE if v.get(c)]
    print("%-18s buona=%-18s trovate=%s  %s"
          % ("OK " if not trovate else "FALSO ALLARME", nome,
             trovate or "nessuna",
             "; ".join(str(v.get(c + "_dove") or "")[:60] for c in trovate)))
