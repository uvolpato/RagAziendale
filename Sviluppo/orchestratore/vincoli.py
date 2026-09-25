"""Estrazione dei vincoli di attributo dalla domanda (D19).

La domanda «dammi 10 articoli viola» contiene due cose: un intent (che cosa
cerca, «addobbi natalizi») e un vincolo (una proprieta' ENUMERABILE,
colore=viola). Il vincolo non puo' stare SOLO nel vettore: si diluisce nella
frase (misurato il 23/09/2026: il vero natalizio violaceo sta a posizione
753 su 1434 per coseno). Va cercato per CO-OCCORRENZA testuale con i termini
che il catalogo usa davvero.

Qui il modello scompone la domanda in intent + vincoli, e per ogni vincolo
suggerisce i termini di ricerca nelle lingue dei cataloghi (it/de/en): da
«viola» esce viola|lila|violet|purple|purpur, con cui il ramo must-match
trova la stella p.219 e le palline p.221 che il vettore perde.

Niente tabelle, niente liste scritte a mano che invecchiano: la mappa
parola->termini vive nel modello multilingue (e nel glossario del corpus,
`glossario.py`). Niente reindex: il must-match
lavora sul testo gia' salvato nei chunk.

Se il modello fallisce non si blocca niente: si torna alla domanda senza
vincoli, comportamento di oggi. L'onesta' e' nel PASSO dopo, non qui: se il
ramo must-match non trova riscontro, il flusso risponde «non ho trovato
articoli viola» senza chiamare il modello (vedi main.py).

NON sono vincoli i termini di categoria o contenuto («natalizi»,
«profumatori», «palline»): quelli sono l'intent e li cerca il ramo
vettoriale. Il filtro in codice tiene solo gli attributi enumerabili:
colore, misura, formato, prezzo. Qualunque cosa il modello scriva, qui si
tiene solo quello. Il prezzo è una soglia, non una stringa: il must-match
aggiunge l'intervallo numerico reale sotto il valore (vedi
_intervalli_numerici), altrimenti «sotto i 2 euro» non troverebbe mai
«1,85 €».
"""
import json
import re

from orchestratore import modello

ATTRIBUTI = {"colore", "misura", "formato", "prezzo"}

ISTRUZIONI = (
    "Sei un motore di ricerca su cataloghi prodotto. Dividi la richiesta in "
    "INTENT e VINCOLI.\n"
    "INTENT: l'oggetto che la persona cerca, senza i filtri espliciti. Da "
    "«nastri blu» l'intent e' «nastri»; da «palline viola» l'intent e' "
    "«palline». Il colore NON fa parte dell'intent: va nei VINCOLI.\n"
    "INTENT_TERMINI: le ALTRE PAROLE con cui quell'oggetto si dice, nelle "
    "lingue dei cataloghi (italiano, tedesco, inglese). Usa la TUA conoscenza: "
    "da «nastri» escono «nastri, ribbons, bänder, tapes»; da «sassi» escono "
    "«pietre, ciottoli, pebbles, kiesel». Dai ALMENO 4-5 parole, includendo i "
    "sinonimi piu' comuni nei cataloghi (per «profumatore» anche «diffusore, "
    "duft, fragranze»). Solo parole che indicano lo STESSO oggetto, non "
    "materiali o cose affini.\n"
    "CONTESTO: le parole della richiesta che indicano il TEMA, l'OCCASIONE o "
    "l'USO — non l'oggetto e non gli attributi enumerabili. Esempi: «natalizie» "
    "in «profumatore con essenze natalizie», «per un matrimonio», «da esterno», "
    "«regalo per una ragazza di 30 anni». Per ognuna, le parole corrispondenti "
    "nelle lingue dei cataloghi. Da «natalizie» escono «natale, christmas, "
    "weihnachten, winter». Se non c'e', CONTESTO e' vuoto.\n"
    "VINCOLI: solo le proprieta' ENUMERABILI che la persona chiede "
    "esplicitamente. Attributi ammessi: colore, misura, formato, prezzo. "
    "NON sono vincoli le parole di categoria o contenuto («natalizi», "
    "«profumatori», «palline»): quelle restano nell'intent.\n"
    "ATTENZIONE: un attributo NEGATO non e' un vincolo. Se la persona dice "
    "«senza il colore rosso», «NON lime light», «non guardare al prezzo», "
    "NON mettere quel vincolo: cercarlo lo farebbe trovare proprio cio' che "
    "la persona esclude (misurato il 23/09/2026: «non guardare il prezzo» "
    "diventava prezzo<3€). I vincoli sono solo gli attributi che la persona "
    "VUOLE, mai quelli che rifiuta.\n"
    "Per ogni vincolo indica i TERMINI DI RICERCA: come quel valore puo' "
    "essere scritto nei cataloghi, nelle lingue dei cataloghi (italiano, "
    "tedesco, inglese), incluse le varianti di scrittura. Da «viola» escono "
    "termini come viola, lila, violet, purple, purpur. Solo parole che un "
    "catalogo puo' davvero scrivere. Per i valori numerici (misura, prezzo) "
    "includi l'unita' come la scriverrebbe il catalogo: da «sotto i 2 euro» "
    "termini «2 €», «2 euro», mai la sola cifra «2».\n"
    "Se non ci sono vincoli espliciti, VINCOLI e' vuoto. Non inventare "
    "vincoli.\n"
    "Rispondi SOLO con JSON della forma {\"intent\": \"...\", "
    "\"intent_termini\": [\"...\",\"...\"], \"contesto\": [\"...\",\"...\"], "
    "\"vincoli\": [{\"attributo\": \"colore\", \"valore\": \"viola\", "
    "\"termini\": [\"viola\",\"lila\",\"violet\"]}]} senza spiegazioni.\n"
    # Qwen3 ragiona se non gli si dice di no, e il ragionamento finisce in
    # reasoning_content: content torna VUOTO e l'estrazione fallisce in
    # silenzio. Estrarre vincoli e' meccanico: non serve pensarci.
    "/no_think"
)


def _messaggio(domanda, catalogo):
    """Il messaggio utente: la domanda, e basta.

    `catalogo` era un esperimento (23/09/2026): passare le descrizioni dei
    documenti al modello per fargli produrre i termini giusti. E' fallito — il
    modello, davanti all'elenco del catalogo, elencava i MATERIALI (organza,
    jute, voile) invece dei termini dell'oggetto («nastri» -> ribbons, tapes).
    I termini rari che il modello non sa li aggiunge il glossario del corpus
    (`glossario.espandi`), estratto a parte dai nomi trilingue dei cataloghi."""
    return f"Richiesta: {domanda}"

def _chiedi(messaggi):
    # Passi meccanici: sempre al modello locale, senza ragionamento.
    return modello.chiedi(messaggi)


def _termini(lista) -> list:
    """I termini dal JSON del modello, spezzando le virgole e scartando vuoti e
    doppioni. Il modello a volte li manda come una stringa unica con le virgole
    dentro («transparente, durchsichtig, klar») invece che come array: spezzare
    qui rende il parsing robusto invece di fidarsi del formato."""
    out = []
    for t in (lista or []):
        for pezzo in str(t).split(","):
            pezzo = pezzo.strip()
            if pezzo and pezzo not in out:
                out.append(pezzo)
    return out


def termini_colore(trovati) -> list:
    """I termini degli attributi enumerabili (colore/misura/...) appiattiti in
    una lista. Serve al guardrail: quando non c'e' un oggetto ma c'e' un colore
    («color crema»), quei termini diventano la query di ricerca."""
    out = []
    for v in (trovati or []):
        for t in v.get("termini") or []:
            if t and t not in out:
                out.append(t)
    return out


def estrae(domanda: str, catalogo=None):
    """(intent, intent_termini, contesto, vincoli) estratti dalla domanda.

    `intent_termini` = i modi in cui l'intent puo' essere scritto nei
    cataloghi nelle loro lingue (es. «sassi» -> sassi, pietre, ciottoli,
    pebbles, river pebbles, stones, kiesel). `contesto` = il tema, l'occasione
    o l'uso (es. «natalizie» -> natale, christmas, weihnachten), che entra
    nella ricerca come espansione morbida. `catalogo`, se passato, e' l'elenco
    (documento, descrizione) di cio' che i documenti contengono davvero.

    `vincoli` = lista di {"attributo", "valore", "termini"}. Restano solo gli
    attributi enumerabili (colore/misura/formato/prezzo). Se il modello non
    risponde o risponde male si torna ("", [], [], []), comportamento di oggi.
    """
    if not domanda:
        return "", [], [], []
    messaggi = [{"role": "system", "content": ISTRUZIONI},
                {"role": "user", "content": _messaggio(domanda, catalogo)}]
    try:
        testo = _chiedi(messaggi)
    except Exception as e:
        print(f"estrazione vincoli non riuscita ({type(e).__name__}: {e})", flush=True)
        return "", [], [], []
    testo = testo.split("```")[-2] if "```" in testo else testo
    testo = testo.strip().strip("`")
    try:
        dati = json.loads(testo)
    except Exception:
        return "", [], [], []
    vincoli = []
    for v in dati.get("vincoli") or []:
        if not isinstance(v, dict):
            continue
        attr = str(v.get("attributo", "")).strip().lower()
        if attr not in ATTRIBUTI:
            continue
        termini = _termini(v.get("termini"))
        if not termini:
            continue
        vincoli.append({"attributo": attr,
                        "valore": str(v.get("valore", "")).strip(),
                        "termini": termini})
    intent = str(dati.get("intent", "")).strip()
    intent_termini = _termini(dati.get("intent_termini"))
    contesto = _termini(dati.get("contesto"))
    if vincoli or intent or contesto:
        return intent, intent_termini, contesto, vincoli
    return "", [], [], []


def _intervalli_numerici(v: dict) -> list:
    """Gli intervalli numerici reali che il valore del vincolo e' una soglia
    per. Senza, «sotto i 2 euro» cerca `\\m2\\ €\\M` e i cataloghi scrivono
    «€ 1,85» (simbolo prima del numero): 0 chunk, e la barriera risponde
    «non esiste il dato» quando esiste (falso negativo sistematico, misurato
    il 23/09/2026 su «decorazioni sotto i 2 euro»: i prezzi 1,00-1,95
    esistono in EUROSAND). Una soglia e' un CONFRONTO, non una stringa: per
    il prezzo si aggiunge l'intervallo reale, in entrambe le grafie dei
    cataloghi («1,85» e «1.85»).

    Solo il prezzo e' una soglia per costruzione («sotto», «meno di», «a
    meno di»): le misure sono valori esatti («da 60 cm») e il must-match
    sulla stringa «60 cm» gia' funziona (verificato 8/8 il 23/09/2026).
    Generare «tutto sotto 60» per la misura inonderebbe la pool. E' la
    classe del vincolo, non un cerotto sul caso specifico.
    """
    if v.get("attributo") != "prezzo":
        return []
    # Il valore può essere «2», «2 €», «1,50»: si estrae il numero. «costo»
    # (vincolo di identità, es. «quanto costa DST2040») non lo è e non
    # genera intervalli — restano solo i termini del modello.
    m = re.search(r"(\d+(?:[,.]\d+)?)", str(v.get("valore", "")))
    if not m:
        return []
    soglia = float(m.group(1).replace(",", "."))
    if not 0 < soglia <= 500:
        # 0 non definisce un intervallo; oltre 500 non e' un catalogo di
        # decorazioni ma un'altra storia — l'intervallo vero (confronto
        # numerico) e' il confine con il DB gestionale, qui restano i termini
        # esatti del modello. Sotto, anche «sotto 1 euro» deve generare
        # «0,xx», non restare a 0 chunk.
        return []
    parte = int(soglia)
    # Le cifre intere sotto la soglia, due decimali: è così che i cataloghi
    # scrivono i prezzi («1,85», «1.85»).
    intere = "|".join(str(i) for i in range(parte))
    termini = [rf"\m(?:{intere})[,.]\d{{2}}\M"] if intere else []
    # La parte intera UGUALE alla soglia vale solo se la soglia ha i decimali
    # («sotto 1,50» include «1,20» ma non «1,50»): si limitano i decimali.
    decimi = int(round((soglia - parte) * 100))
    if decimi > 0:
        decimali = "|".join(f"{d:02d}" for d in range(decimi))
        termini.append(rf"\m{parte}[,.](?:{decimali})\M")
    return termini


def regex(vincoli: list) -> str:
    """L'espressione regolare del must-match per i vincoli dati, o ''.

    I termini sono vincolati ai confini di parola: senza, «viola» matcha
    anche «violazione» nelle policy e la pool del colore si contamina
    (misurato il 23/09/2026). PostgreSQL supporta \\m (inizio parola) e \\M
    (fine parola). Il confine a destra dopo una cifra e' necessario: «2» in
    «12,50» sta dentro la parola «12,50»? No: la virgola rompe la parola.
    Per il prezzo si aggiungono gli intervalli reali sotto la soglia: senza,
    «sotto i 2 euro» cercherebbe «2 €» e perderebbe «1,85 €» (vedi
    _intervalli_numerici).
    """
    termini = []
    for v in vincoli:
        for t in v["termini"]:
            t = t.strip()
            if t:
                termini.append(f"\\m{re.escape(t)}\\M")
        termini.extend(_intervalli_numerici(v))
    # dict.fromkeys conserva l'ordine e toglie i doppioni, senza riordinarli.
    return "|".join(dict.fromkeys(termini))


def query_di_ricerca(originale: str, intent_termini: list, vincoli: list) -> str:
    """La domanda da dare a embedding e rerank: originale piu' i termini nelle
    lingue dei cataloghi.

    La domanda dell'utente e' in una lingua; i cataloghi sono plurilingua. Su
    «sassi rossi» il cross-encoder dava -11 ai «river pebbles dunkelrot dark
    red» di EUROSAND p.64 e -6,5 ai natalizi INGE, che NON erano sassi ma
    vincevano lo stesso: il prodotto vero era dentro la pool del vincolo ma
    annega nel ranking. Con i termini del catalogo in query, il pezzo vero
    sale al primo posto (misurato il 23/09/2026).

    I termini del vincolo sono gia' in altre lingue (viola|lila|violet); qui
    si aggiungono anche quelli dell'INTENT, che prima non arrivavano mai alla
    ricerca. Niente nuovi servizi: e' la stessa estrazione D19, usata anche
    per il caso in cui il vincolo non c'e'.
    """
    termini = list(intent_termini or [])
    for v in vincoli:
        termini.extend(v.get("termini") or [])
    if not termini:
        return originale
    # dict.fromkeys conserva l'ordine e toglie i doppioni, come in regex().
    # La domanda originale resta in testa: l'utente coglie il senso, i
    # termini delle lingue dei cataloghi chiudono il collegamento.
    return originale + " " + " ".join(dict.fromkeys(termini))


def messaggio_vuoto(vincoli: list) -> str:
    """La risposta onesta quando il must-match non trova nulla.

    Fa da barriera: se non c'e' riscontro nei documenti, si dice che non c'e'
    riscontro, senza che il modello possa inventarlo. La forma dipende
    dall'attributo: la stessa architettura per colore, misura, formato, prezzo.
    """
    if not vincoli:
        return ("Nei documenti a cui ho accesso non ho trovato articoli "
                "che corrispondono alla tua richiesta.")
    valore = ", ".join(v["valore"] for v in vincoli if v.get("valore")) or \
        "con quelle caratteristiche"
    attributi = {v["attributo"] for v in vincoli if v.get("valore")}
    if "colore" in attributi:
        testo = f"articoli di colore {valore}"
    elif "misura" in attributi or "formato" in attributi:
        testo = f"articoli della misura {valore}"
    elif "prezzo" in attributi:
        testo = f"articoli con prezzo {valore}"
    else:
        testo = f"articoli {valore}"
    return f"Nei documenti a cui ho accesso non ho trovato {testo}."


def _prova():
    """Regole locali che non chiamano il modello."""
    assert regex([]) == ""
    assert regex([{"attributo": "colore", "valore": "viola",
                   "termini": ["lila", "violet", "lila"]}]) == \
        "\\mlila\\M|\\mviolet\\M"
    # Il filtro per attributo sta in estrae(), non qui.
    # La soglia del prezzo genera l'intervallo reale: «2 €» da sola non
    # troverebbe «1,85 €» nei cataloghi (misurato il 23/09/2026).
    r = regex([{"attributo": "prezzo", "valore": "2",
                "termini": ["2 €", "2 euro"]}])
    assert "(?:0|1)[,.]\\d{2}" in r, r
    # Soglia decimale: «sotto 1,50» deve prendere «1,20» ma non «1,50».
    r = regex([{"attributo": "prezzo", "valore": "1,50",
                "termini": ["1,50"]}])
    assert "\\m(?:0)[,.]\\d{2}\\M" in r and "\\m1[,.]" in r and "|48|49)\\M" in r, r
    # Sotto 1 euro (parte intera 0): solo l'intervallo dei decimi.
    r = regex([{"attributo": "prezzo", "valore": "0,99", "termini": ["0,99"]}])
    assert "\\m0[,.]" in r and "|97|98)\\M" in r, r
    # Un codice come «2040» di «DST2040» non genera intervalli sotto 500.
    r = regex([{"attributo": "misura", "valore": "2040", "termini": ["2040"]}])
    assert r == "\\m2040\\M", r
    # La query di ricerca arricchita: originale piu' intent_termini e termini
    # dei vincoli, senza doppioni e senza alterare l'ordine della domanda.
    q = query_di_ricerca("ho bisogno di sassi rossi",
                         ["sassi", "pietre", "ciottoli", "pebbles", "sassi"],
                         [{"attributo": "colore", "valore": "rosso",
                           "termini": ["rosso", "red"]}])
    assert q == ("ho bisogno di sassi rossi sassi pietre ciottoli "
                 "pebbles rosso red"), q
    # Senza termini resta la domanda come l'utente l'ha scritta.
    assert query_di_ricerca("quanto costa il DST2040", [], []) == \
        "quanto costa il DST2040"
    print("vincoli: regole locali ok")


if __name__ == "__main__":
    _prova()