"""Sinonimi multilingue per la ricerca (D20).

I cataloghi sono trilingue (it/de/en) e il vettore, pur multilingue, NON
collega i nomi di dominio fra lingue: misurato il 23/09/2026, coseno(«sassi
rossi», «palline natalizie rosse») = 0.5065 mentre coseno(«sassi rossi»,
«river pebbles dunkelrot») = 0.4415. I colori il vettore li collega da solo; i
NOMI delle cose no.

Qui sta il dizionario parola -> sinonimi nelle lingue dei cataloghi, in una
tabella Postgres (migrazione 018). Lo consulta il ramo di ricerca per
estendere la query: «sassi» aggiunge «pietre, ciottoli, pebbles, kiesel».

Come lo POPOLA il modello (8B): al primo incontro di una parola che non c'e'
nel dizionario. Non a ogni domanda: UNA volta per parola, poi il risultato
resta in cache (RAM) e in tabella. E' lo stesso meccanismo di RAGFlow
(rag/nlp/synonym.py), ma senza Redis e senza WordNet — solo il dizionario di
dominio, che e' cio' che un catalogo di decorazioni richiede.

Una parola con array vuoto significa «gia' guardata, nessun sinonimo»: cosi'
l'8B non viene ri-chiamato per «bisogno», «quanto», «costa» a ogni turno.

Niente stoplist scritte a mano: decide il modello, e la cache rende il costo
una tantum. Se il modello non risponde, la parola semplicemente non porta
sinonimi e la ricerca procede com'era — degrada in silenzio, come gli altri
passi LLM.
"""
import json
import os
import threading

from orchestratore import modello

ISTRUZIONI = (
    "Dammi, nelle lingue italiano, tedesco e inglese, le parole DIVERSE con cui "
    "un catalogo di decorazioni e giardinaggio chiamerebbe la cosa indicata.\n"
    "NON ripetere la parola stessa: voglio sinonimi e traduzioni, non "
    "declinazioni.\n"
    "Massimo 6 parole, le piu' utili per trovare il prodotto in un catalogo.\n"
    "Esempio: «sassi» -> «pietre, ciottoli, pebbles, river pebbles, kiesel».\n"
    "Se la parola non e' un oggetto o materiale da catalogo (per esempio "
    "«bisogno», «quanto», «costa», «ho», «di», o un COLORE come «rosso», "
    "«blu», «verde»), rispondi SOLO con VUOTO.\n"
    "Rispondi solo con parole separate da virgola.\n"
    "/no_think"
)

# Cache in RAM: parola -> lista sinonimi (anche vuota, per le parole gia'
# guardate). Caricata dalla tabella al primo uso, aggiornata in scrittura.
_cache: dict[str, list] = {}
_caricate: set[str] = set()
_lock = threading.Lock()

# Parole di FUNZIONE che non si mandano mai al modello: articoli, preposizioni,
# congiunzioni, pronomi (it/en), e le scorie del prompt di sistema di LibreChat
# («provide», «title», «using»...). Il 23/09/2026 il modello, interrogato su
# «una» (l'articolo), ha risposto «pietra, stone, rock, kiesel» e su «for» /
# «using» ha risposto «stone, stein»: una parola di funzione non e' un oggetto
# di catalogo per definizione, quindi chiedere sinonimi e' rumore garantito.
# Stoplist, non cerotto: e' la CLASSE «parole di funzione», vale per qualunque
# domanda in italiano o inglese.
PAROLE_DI_FUNZIONE = {
    # italiano: articoli, preposizioni, congiunzioni, pronomi, verbi ausiliari
    "a", "ad", "al", "alla", "alle", "ai", "agli", "con", "da", "dal", "dalla",
    "dai", "dalle", "di", "del", "della", "dei", "delle", "e", "ed", "il", "lo",
    "la", "i", "gli", "le", "in", "nel", "nella", "nei", "nelle", "non", "o",
    "od", "per", "su", "sul", "sulla", "sui", "sulle", "tra", "fra", "un", "uno",
    "una", "che", "chi", "cui", "come", "quando", "quanto", "quale", "quali",
    "cosa", "ci", "vi", "ne", "mi", "ti", "si", "me", "te", "se", "noi", "voi",
    "loro", "mio", "mia", "tuo", "tua", "suo", "sua", "questo", "questa", "quello",
    "quella", "essere", "sono", "sei", "e'", "era", "ho", "hai", "ha", "abbiamo",
    "avete", "hanno", "devo", "devi", "voglio", "vuole", "avrei", "serve",
    "servono", "bisogno", "c'e'", "cioe'", "anche", "piu'", "meno", "senza",
    "con", "dove", "perche'", "quindi", "allora", "dunque", "avete", "avrei",
    # inglese: articoli, preposizioni, congiunzioni, pronomi, ausiliari
    "the", "a", "an", "and", "or", "but", "for", "nor", "so", "yet", "of", "in",
    "on", "at", "to", "from", "with", "by", "about", "into", "through", "over",
    "under", "out", "up", "down", "between", "i", "you", "he", "she", "it", "we",
    "they", "me", "him", "her", "us", "them", "my", "your", "his", "our", "their",
    "is", "are", "am", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "shall", "should", "can", "could",
    "may", "might", "must", "this", "that", "these", "those", "what", "which",
    "who", "whom", "when", "where", "why", "how", "if", "then", "else", "not",
    "no", "yes", "very", "just", "also", "only", "there", "here", "off",
    # scorie del prompt di titolo di LibreChat
    "provide", "concise", "word", "less", "title", "case", "conventions",
    "conversation", "using", "return", "itself", "only",
}


def _chiedi(messaggi) -> str:
    return modello.chiedi(messaggi)


def _genera(parola: str) -> list:
    """Chiede al modello i sinonimi multilingue di `parola`. Mai solleva."""
    messaggi = [{"role": "system", "content": ISTRUZIONI},
                {"role": "user", "content": parola}]
    try:
        testo = _chiedi(messaggi)
    except Exception:
        return []
    testo = testo.split("```")[-2] if "```" in testo else testo
    testo = testo.strip().strip("`").strip()
    if not testo or testo.lower() == "vuoto":
        return []
    return _pulisci(testo, parola)


def _carica(conn):
    """Carica la tabella nella cache RAM, una volta sola."""
    if "ok" in _caricate:
        return
    with _lock:
        if "ok" in _caricate:
            return
        with conn.cursor() as cur:
            cur.execute("SELECT parola, sinonimi FROM sinonimi")
            for parola, sinonimi in cur.fetchall():
                _cache[parola] = list(sinonimi)
        _caricate.add("ok")


def sinonimi_di(conn, parola: str) -> list:
    """I sinonimi di una parola, dalla cache o dal modello (una volta sola)."""
    parola = parola.strip().lower()
    if not parola:
        return []
    # Le parole di funzione non sono oggetti di catalogo: niente chiamata al
    # modello, niente voce nel dizionario. Il 23/09/2026 il modello ha risposto
    # «pietra, stone» all'articolo «una», tirando su i sassi su ogni domanda.
    if parola in PAROLE_DI_FUNZIONE:
        return []
    _carica(conn)
    with _lock:
        if parola in _cache:
            return _cache[parola]
    sinonimi = _genera(parola)
    with _lock:
        _cache[parola] = sinonimi
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO sinonimi (parola, sinonimi) VALUES (%s, %s)
               ON CONFLICT (parola) DO UPDATE SET sinonimi = EXCLUDED.sinonimi,
                 aggiornato_il = now()""",
            (parola, sinonimi),
        )
    conn.commit()
    return sinonimi


def arricchisci(conn, domanda: str) -> str:
    """La domanda estesa coi sinonimi multilingue delle parole di contenuto.

    Le parole si prendono cosi' come sono (minuscole, senza punteggiatura), e
    per ognuna si cercano i sinonimi. La domanda originale resta in testa: i
    sinonimi CHIUDONO il collegamento linguistico, non lo sostituiscono.

    Se il modello e' giu' o non risponde, torna la domanda com'era: la ricerca
    non peggiora mai rispetto a oggi.
    """
    if not domanda:
        return domanda
    import re
    parole = re.findall(r"[a-zàèéìòù]+", domanda.lower())
    aggiunte = []
    for p in parole:
        for s in sinonimi_di(conn, p):
            if s not in aggiunte and s not in parole:
                aggiunte.append(s)
    if not aggiunte:
        return domanda
    return domanda + " " + " ".join(aggiunte)


# Quanti sinonimi AL MASSIMO per parola entrano nella query. Il prompt chiede
# «massimo 6», ma il 23/09/2026 l'8B ha risposto con 30 parole su «candele»
# (wicks, wax, votives, beeswax, melt, bath salts...) e la query gonfia
# diluiva l'embedding e inquinava la ricerca. Il limite sta QUI, in codice:
# fidarsi del modello su un prompt e' il bug, non la richiesta.
MAX_SINONIMI = int(os.environ.get("SINONIMI_MAX", "6"))


def _pulisci(testo: str, parola: str) -> list:
    """Estrae i sinonimi dalla risposta del modello (virgole, punti, minuscole,
    senza la parola stessa, senza doppioni, al massimo MAX_SINONIMI). Separata
    perche' e' l'unica regola verificabile senza modello ne' DB."""
    out = []
    for t in testo.replace(";", ",").split(","):
        t = t.strip().strip(".").lower()
        if t and t != parola and t not in out:
            out.append(t)
    return out[:MAX_SINONIMI]


def _prova():
    assert _pulisci("pietre, ciottoli, Pebbles., pietre", "sassi") == \
        ["pietre", "ciottoli", "pebbles"]
    # «VUOTO» non passa da _pulisci: lo intercetta _genera prima.
    assert _pulisci("", "quanto") == []
    assert _pulisci("sassi, sassi", "sassi") == []
    # Le parole di funzione non vanno mai al modello.
    for p in ("una", "for", "using", "the", "di", "non", "e", "che", "also"):
        assert p in PAROLE_DI_FUNZIONE, p
    # Il limite duro: il modello non puo' gonfiare la query oltre MAX_SINONIMI.
    lunghi = ",".join(f"s{i}" for i in range(40))
    assert len(_pulisci(lunghi, "x")) == MAX_SINONIMI
    print("sinonimi: regole locali ok")


if __name__ == "__main__":
    _prova()
