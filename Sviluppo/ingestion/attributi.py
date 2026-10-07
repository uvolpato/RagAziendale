"""Gli attributi dei prodotti, in COLONNE: QUESTO SCRIVE la tabella `attributi`.

Sta qui, in `ingestion`, e non nell'orchestratore, perche' scrive dati. Uno
legge, l'altro scrive, e non si toccano.

PERCHE' ESISTE. Il VLM descrive ogni figura in PROSA, e la prosa finisce in
un campo di testo solo:

    object: three ribbons with heart motifs
    Colours: white with red hearts  pink with silver hearts
             light blue with white hearts
    Code: 01, 50, 71

L'informazione e' tutta li' — ma attaccata alle parole, non alle colonne.
Cosi' OGNI domanda deve ri-estrarla: su «ci sono anche con i cuori blu?» il
critico deve capire che «light blue with white hearts» e' un nastro azzurro
coi cuori BIANCHI, e sbaglia una volta su tre (`non-promettere`, 0-1/3 su
dodici configurazioni, quattro correzioni che non hanno tenuto).

Il 7/10/2026 e' stato misurato che non e' un limite del modello: con una
didascalia per volta e due frasi sul formato dei colori fa 12/12. Il difetto
e' che gli chiediamo quell'inferenza a ogni domanda invece di una volta sola.

Qui si fa una volta sola, e si scrive. «cuori blu» diventa

    WHERE motivo = 'hearts' AND colore_motivo ~* 'blue'

cioe' un filtro: zero righe vuol dire zero, e «non ci sono» e' vero senza che
nessuno debba giudicare.

PERCHE' NON UN PARSER. Le righe `Colours:` sono regolari all'83%, non al
100%: accanto a «white with red hearts» ci sono «red with glossy finish» (una
finitura, non un motivo), «yellow tulip white tulip orange tulip» (senza
«with»), «dark red dark red dark red» (rumore del VLM) e «black and white».
Un'espressione regolare su questa varieta' si rompe in silenzio, che e' il
modo peggiore. Lo struttura il modello — ma sul TESTO, non sulle immagini:
didascalie corte, nessun token di immagine, e le foto non si rileggono.

PERCHE' NON SI TOCCA IL PROMPT DEL VLM. Quella e' la forma definitiva — il
VLM restituira' json e la prosa restera' come corredo per la ricerca
semantica — e vuole 15.143 figure rilette. Questo invece lavora sulle
didascalie gia' pagate, e si puo' misurare stasera. Se paga, l'altra diventa
il modo di scrivere i documenti nuovi.

    docker compose exec -T orchestratore python /app/ingestion/attributi.py --prova 20
    docker compose exec -T orchestratore python /app/ingestion/attributi.py
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import psycopg
from psycopg.rows import dict_row, tuple_row

sys.path.insert(0, "/app")

from orchestratore import modello  # noqa: E402

# QUANTE INSIEME: quante il server ne serve davvero, chieste a lui.
#
# Non un numero fisso, perche' questo lavoro ha bisogno di un contesto
# RIDICOLO rispetto alla chat — una didascalia da 1.500 caratteri, un prompt
# corto, 700 token di risposta: ~2.000 in tutto, contro i ~16.200 che vuole
# il coordinatore con quaranta righe nello stato. Il 7/10/2026 tre slot hanno
# rotto la chat (contesto per conversazione da 32.768 a 21.845, i casi sui
# testi troncati in silenzio, documenti da 13/18 a 9/18): per l'estrazione
# invece otto slot da 8.192 bastano quattro volte, e la cache KV non cambia
# perche' dipende dal contesto TOTALE, non dal numero di slot.
#
# Quindi la procedura per farlo in fretta, quando la chat non serve a
# nessuno: si alza `--parallel` sulla voce `qwen3-14b` in
# `modelli/llama-swap.yaml`, si lancia, e si rimette a 2. Una voce separata
# NON va bene: il 14b sta in un gruppo esclusivo, e una seconda istanza si
# scambierebbe con la chat scaricando e ricaricando 9 GB a ogni domanda.
#
# Sei ore a due slot, una e mezza-due a otto (non otto volte: la GPU e' una e
# gli stream si dividono il calcolo).
PARALLELO = int(os.environ.get("ATTRIBUTI_PARALLELO", "0")) or modello.slot()

TABELLA = """
CREATE TABLE IF NOT EXISTS attributi (
    immagine_id   bigint  NOT NULL,
    n             integer NOT NULL,          -- l'oggetto nella foto: 1, 2, 3...
    cosa          text,                      -- «ribbon», «christmas ball»
    colore        text,                      -- il colore DELL'OGGETTO
    motivo        text,                      -- «hearts», «stripes», NULL se liscio
    colore_motivo text,                      -- il colore DEL MOTIVO
    materiale     text,
    codice        text,
    PRIMARY KEY (immagine_id, n)
);
CREATE INDEX IF NOT EXISTS attributi_cosa    ON attributi (cosa);
CREATE INDEX IF NOT EXISTS attributi_colore  ON attributi (colore);
CREATE INDEX IF NOT EXISTS attributi_motivo  ON attributi (motivo, colore_motivo);
"""

ISTRUZIONI = """Ti do la didascalia di una foto di catalogo, scritta da chi guardava la foto. Mettila in colonne.

UNA VOCE PER OGGETTO. Se la didascalia descrive tre nastri di colori diversi, le voci sono tre. Se descrive un oggetto solo, la voce e' una.

ATTENTO A COSA E' ATTACCATO COSA, perche' e' l'unica cosa che conta qui. I colori si scrivono «<colore dell'oggetto> with <colore> <motivo>»:
- «white with red hearts» -> colore=white, motivo=hearts, colore_motivo=red
- «light blue with white hearts» -> colore=light blue, motivo=hearts, colore_motivo=white
- «red with glossy finish» -> colore=red, motivo vuoto (una finitura non e' un motivo)
- «dark grey» -> colore=dark grey, motivo vuoto
- «black and white» -> colore=black and white, motivo vuoto

Non inventare niente che la didascalia non dica: quello che non c'e' si lascia vuoto. Non tradurre: le parole restano quelle della didascalia."""

STRUMENTO = [{"type": "function", "function": {
    "name": "colonne",
    "description": "Gli oggetti della foto, uno per voce.",
    "parameters": {"type": "object", "properties": {
        "oggetti": {"type": "array", "items": {"type": "object", "properties": {
            # L'ordine dei campi e' l'ordine del ragionamento: prima COS'E',
            # poi di che colore e' LUI, poi se ha un motivo, e solo allora di
            # che colore e' il motivo. E' la stessa forma del campo `oggetto`
            # nel critico, che chiuse i falsi positivi dei «sassi viola».
            "cosa": {"type": "string",
                     "description": "Che cosa e', una o due parole, come la "
                                    "chiama la didascalia: «ribbon», "
                                    "«christmas ball», «vase»."},
            "colore": {"type": "string",
                       "description": "Il colore DELL'OGGETTO. Vuoto se la "
                                      "didascalia non lo dice."},
            "motivo": {"type": "string",
                       "description": "Il motivo stampato o applicato: "
                                      "«hearts», «stripes», «dots», «stars». "
                                      "VUOTO se l'oggetto e' liscio o se "
                                      "quella e' una finitura (glossy, matt, "
                                      "glitter da solo)."},
            "colore_motivo": {"type": "string",
                              "description": "Il colore DEL MOTIVO, non "
                                             "dell'oggetto. Vuoto se non c'e' "
                                             "un motivo."},
            "codice": {"type": "string",
                       "description": "Il codice prodotto di QUESTO oggetto, "
                                      "se la didascalia lo distingue. Vuoto "
                                      "se non c'e'."}},
            "required": ["cosa", "colore", "motivo", "colore_motivo"]}},
        "materiale": {"type": "string",
                      "description": "Il materiale, comune a tutti gli "
                                     "oggetti della foto. Vuoto se non c'e'."}},
        "required": ["oggetti"]}}}]


def _pulisci(v) -> str:
    v = " ".join(str(v or "").split())
    return "" if v.lower() in ("", "n/a", "none", "null", "-", "vuoto") else v


def colonne_di(descrizione: str):
    """[(cosa, colore, motivo, colore_motivo, codice)], materiale."""
    messaggi = [{"role": "system", "content": ISTRUZIONI},
                {"role": "user", "content": " ".join(descrizione.split())[:1500]}]
    try:
        m = modello.messaggio(messaggi, max_tokens=700, tools=STRUMENTO,
                              tool_choice="required", ragiona=False)
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, str(e)[:60])
    for tc in m.get("tool_calls") or []:
        try:
            arg = json.loads(tc["function"].get("arguments") or "{}")
        except (TypeError, ValueError):
            continue
        materiale = _pulisci(arg.get("materiale"))
        fuori = []
        for o in (arg.get("oggetti") or [])[:12]:
            if not isinstance(o, dict):
                continue
            cosa = _pulisci(o.get("cosa"))
            if not cosa:
                continue
            fuori.append((cosa, _pulisci(o.get("colore")),
                          _pulisci(o.get("motivo")),
                          _pulisci(o.get("colore_motivo")),
                          _pulisci(o.get("codice")), materiale))
        return fuori, ""
    return None, "il modello non ha chiamato lo strumento"


def da_fare(conn, limite=None):
    """Le didascalie non ancora messe in colonne."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("""SELECT i.id, i.descrizione FROM immagini i
                        WHERE i.descrizione IS NOT NULL
                          AND length(i.descrizione) > 20
                          AND NOT EXISTS (SELECT 1 FROM attributi a
                                           WHERE a.immagine_id = i.id)
                        ORDER BY i.id""" + (" LIMIT %d" % int(limite)
                                            if limite else ""))
        return cur.fetchall()


def scrivi(conn, immagine_id, voci):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM attributi WHERE immagine_id = %s",
                    (immagine_id,))
        for n, (cosa, colore, motivo, col_mot, codice, materiale) in enumerate(
                voci, 1):
            cur.execute(
                """INSERT INTO attributi (immagine_id, n, cosa, colore, motivo,
                                          colore_motivo, materiale, codice)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (immagine_id, n, cosa or None, colore or None, motivo or None,
                 col_mot or None, materiale or None, codice or None))
    conn.commit()


def main():
    prova = None
    if "--prova" in sys.argv:
        prova = int(sys.argv[sys.argv.index("--prova") + 1])
    dsn = os.environ["DATABASE_URL"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(TABELLA)
        conn.commit()
        righe = da_fare(conn, prova)
        print("da mettere in colonne: %d didascalie%s"
              % (len(righe), " (prova)" if prova else ""), flush=True)
        if not righe:
            return

        t0 = time.monotonic()
        fatte = [0]

        def una(riga):
            ident, descrizione = riga
            voci, problema = colonne_di(descrizione)
            return ident, descrizione, voci, problema

        # Ogni scrittura sulla SUA connessione: psycopg non si usa da due
        # thread insieme, ed e' la stessa ragione per cui `_mossa_cerca`
        # apre la sua.
        with ThreadPoolExecutor(max_workers=max(1, PARALLELO)) as pool:
            for ident, descrizione, voci, problema in pool.map(una, righe):
                fatte[0] += 1
                if voci is None:
                    print("  %5d  SALTATA: %s" % (ident, problema), flush=True)
                    continue
                with psycopg.connect(dsn) as scrittura:
                    scrivi(scrittura, ident, voci)
                if prova:
                    print("  %5d  %s" % (ident,
                                         " ".join(descrizione.split())[:90]),
                          flush=True)
                    for v in voci:
                        print("         -> cosa=%s | colore=%s | motivo=%s | "
                              "colore_motivo=%s" % v[:4], flush=True)
                elif fatte[0] % 50 == 0:
                    passati = time.monotonic() - t0
                    print("  %d/%d  %.1f al secondo"
                          % (fatte[0], len(righe), fatte[0] / max(passati, 1)),
                          flush=True)
        print("fatte %d in %.0fs" % (fatte[0], time.monotonic() - t0))


if __name__ == "__main__":
    main()
