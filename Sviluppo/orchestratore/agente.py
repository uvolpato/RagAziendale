"""L'agente vero (D17, fase 5): un ciclo LangGraph di strumenti di recupero.

Sostituisce il surrogato `ricerca_agente.py` (cerca-guarda-riprova con un solo
strumento). Qui il modello ha PIU' strumenti — cerca (col vincolo colore),
cerca_esatta, pagina, grep, documenti — e decide da se' quali chiamare e quante
volte, con un tetto (MAX_PASSI).

Il flusso e' quello di una persona davanti ai dati (introspezione del 24/09):

    capisce (intent + vincoli, D19) -> cerca -> guarda cosa torna
         -> se sbaglia: grep (dove sta davvero?) / pagina (leggi) -> riprova
         -> rispondi onestamente

Guardrail (VALUTAZIONE-ORCHESTRATORE-AGENTE.md §4), non negoziabili:

- **Il modello decide COSA cercare, mai COSA puo' vedere.** I gruppi li inietta
  il server dentro gli strumenti (chiusura su `gruppi`): non passano mai dal
  modello e non compaiono in nessun prompt.
- **Ciclo limitato.** MAX_PASSI chiamate di strumenti al massimo.
- **I pezzi restano chunk veri del database.** Il modello non inventa testo.
- **"Non lo so" e' una risposta possibile.**
"""

import json
import operator
import os
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from orchestratore import egress, glossario, modello, recupero

ROTTA = os.environ.get("LLM_RAGIONAMENTO", "ragionamento")
SECONDI = float(os.environ.get("AGENTE_TIMEOUT", "120"))
MAX_PASSI = int(os.environ.get("AGENTE_MAX_PASSI", "4"))
# 1 = l'agente RAGIONA prima di chiamare gli strumenti (ragiona=True). Col
# ragionamento il modello puo' capire «ho gia' la risposta, mi fermo» invece di
# continuare a cercare. Reversibile. Costa piu' token, percio' il budget sale.
AGENTE_RAGIONA = os.environ.get("AGENTE_RAGIONA", "") == "1"
MAX_TOKEN = int(os.environ.get("AGENTE_MAX_TOKEN",
                               "8192" if AGENTE_RAGIONA else "2048"))
# 1 = non espandere i termini col glossario del corpus (solo quelli del modello).
SENZA_GLOSSARIO = os.environ.get("SENZA_GLOSSARIO", "") == "1"
# 1 = il guardrail forza la ricerca anche quando non c'e' un oggetto ma c'e' un
# colore («color crema»): i termini del colore diventano l'oggetto da cercare.
GUARDIA_SENZA_OGGETTO = os.environ.get("GUARDIA_SENZA_OGGETTO", "") == "1"
# 1 = guardrail FORTE (reversibile): la prima ricerca la fa SEMPRE il sistema,
# con l'intent estratto, prima che il modello decida. Copre sia «il modello non
# cerca» sia «cerca con l'oggetto sbagliato». Quando e' acceso, il guardrail
# reattivo di _nodo_agente si spegne (la ricerca e' gia' fatta).
GUARDIA_FORTE = os.environ.get("GUARDIA_FORTE", "") == "1"
# 1 = pre-selezione dei documenti con l'indice (indice.py): prima si sceglie
# QUALI cataloghi c'entrano (descrizione del documento), poi si cerca dentro.
INDICE_PRESELEZIONE = os.environ.get("INDICE_PRESELEZIONE", "") == "1"

ISTRUZIONI_SISTEMA = (
    "Sei un assistente che cerca in un archivio aziendale di cataloghi e documenti.\n"
    "Il tuo compito e' INDICARE dove stanno le cose, non estrarre codici o dati: "
    "dai i riferimenti (documento e pagina), poi e' la persona a guardare.\n"
    "Per i PRODOTTI trovati, scrivi la risposta in modo articolato: per ognuno una "
    "breve descrizione in parole tue (che cos'e', materiale, colori) e la pagina. "
    "Riassumi il testo tecnico delle descrizioni, NON ricopiarlo tale e quale.\n"
    "\n"
    "Strategia, che decidi TU secondo il tipo di domanda:\n"
    "- PRODOTTO o oggetto (es. «nastri blu», «vasi»): cerca PRIMA le figure con "
    "`cerca_figure`, e solo se non basta il testo. Rispondi indicando le pagine.\n"
    "- ASTRATTA (un bisogno senza oggetto, es. «un regalo per una trentenne»): "
    "parti dal contesto, arriva a prodotti concreti con `cerca_figure`. Rispondi "
    "indicando le pagine.\n"
    "- TESTO (come si fa una cosa, cosa dice una policy o un manuale): leggi i "
    "passi con `cerca` e `pagina`, e riassumi il contenuto citando la pagina.\n"
    "\n"
    "- Se la domanda e' un saluto o una chiacchiera, NON chiamare strumenti.\n"
    "- Quando hai gia' le pagine che rispondono, RISPONDI e fermati: non ripetere "
    "la stessa ricerca con parole diverse, non inseguire varianti. Se `cerca_figure` "
    "torna vuota per un oggetto, il prodotto sta nel TESTO: usa `cerca` UNA volta, "
    "poi rispondi con quello che hai.\n"
    "- Se trovi l'OGGETTO ma non il QUALIFICATORE chiesto (es. i «diffusori» ma "
    "non «natalizi»), dillo in modo distinto: «ho trovato i diffusori a pagina P, "
    "ma non specificamente natalizi». NON mescolare un risultato generico con uno "
    "del qualificatore per far credere che esista cio' che non hai trovato.\n"
    "- Non inventare: se non trovi, dillo. Mai codici o prezzi inventati.\n"
    "- Rispondi in italiano, citando documento e pagina.\n"
)


# Gli strumenti che il modello puo' chiamare.
STRUMENTI = [
    {"type": "function", "function": {
        "name": "cerca",
        "description": "Cerca nei cataloghi e restituisce i passi piu' pertinenti. "
                       "I vincoli di colore/misura/prezzo sono gia' applicati dal sistema.",
        "parameters": {"type": "object",
                       "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "cerca_esatta",
        "description": "Cerca una parola o un codice ESATTO (es. DST2040, un nome proprio).",
        "parameters": {"type": "object",
                       "properties": {"termine": {"type": "string"}},
                       "required": ["termine"]}}},
    {"type": "function", "function": {
        "name": "grep",
        "description": "Quanti pezzi e in quali documenti compare una parola. Serve a "
                       "capire se una cosa esiste davvero e dove, prima di fidarsi di un 'non trovato'.",
        "parameters": {"type": "object",
                       "properties": {"termine": {"type": "string"}},
                       "required": ["termine"]}}},
    {"type": "function", "function": {
        "name": "pagina",
        "description": "Legge TUTTI i passi di una pagina di un documento, per capire il contesto.",
        "parameters": {"type": "object",
                       "properties": {"documento": {"type": "string"},
                                      "pagina": {"type": "integer"}},
                       "required": ["documento", "pagina"]}}},
    {"type": "function", "function": {
        "name": "documenti",
        "description": "Elenca i cataloghi disponibili e cosa contiene ciascuno (la "
                       "descrizione del contenuto). Usalo sulle domande APERTE, prima "
                       "di cercare, per ancorarti a cio' che l'archivio ha davvero.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "cerca_figure",
        "description": "Cerca le FIGURE (immagini dei prodotti) che corrispondono "
                       "all'oggetto. Per i cataloghi: trova dove sta il prodotto e la "
                       "sua pagina. I vincoli di colore/misura sono gia' applicati.",
        "parameters": {"type": "object",
                       "properties": {
                           "oggetto": {"type": "string"},
                           "limite": {"type": "integer",
                                      "description": "Quante figure restituire. Alza a 20 "
                                                     "quando la persona chiede di elencarle tutte."}},
                       "required": ["oggetto"]}}},
]


# Quanto testo di ogni pezzo si fa vedere al modello in uno strumento.
ASSAGGIO = 250


def _formatta(righe):
    if not righe:
        return "(nessun risultato)"
    fuori = []
    for r in righe:
        doc = r.get("documento", "?")
        pag = f", pagina {r['page']}" if r.get("page") is not None else ""
        testo = " ".join((r.get("content") or "").split())
        if len(testo) > ASSAGGIO:
            testo = testo[:ASSAGGIO] + " …"
        fuori.append(f"[{doc}{pag}] {testo}")
    return "\n".join(fuori)


def _formatta_figure(righe):
    """Le figure in forma leggibile: documento, pagina, descrizione."""
    if not righe:
        return "(nessuna figura)"
    fuori = []
    for r in righe:
        doc = r.get("documento", "?")
        pag = f", pagina {r['page']}" if r.get("page") is not None else ""
        testo = " ".join((r.get("descrizione") or "").split())
        if len(testo) > ASSAGGIO:
            testo = testo[:ASSAGGIO] + " …"
        fuori.append(f"[{doc}{pag}] {testo}")
    return "\n".join(fuori)


def _pre_seleziona(conn, qvec, gruppi):
    """I documenti piu' pertinenti (indice.py), o None se l'indice non c'e' o la
    pre-selezione e' spenta. Con None la ricerca resta su tutto, come prima."""
    if not INDICE_PRESELEZIONE or qvec is None:
        return None
    from orchestratore import indice
    doc = indice.pertinenti(conn, qvec, gruppi, quanti=4)
    return [d[1] for d in doc] if doc else None


def _esegui(nome, argomenti, conn, gruppi, vincolo="", intent_termini=None,
            contesto=None):
    """Esegue uno strumento e torna (righe, testo_per_il_modello).

    `gruppi`, `vincolo`, `intent_termini` e `contesto` arrivano dalla chiusura,
    non dal modello: e' il guardrail. I termini multilingue dell'oggetto
    (intent_termini) si aggiungono alla query perche' il catalogo scrive
    «ribbons» dove la persona dice «nastri» (D19, query_di_ricerca).
    """
    termini = intent_termini or []
    contesto = contesto or []
    if nome == "cerca":
        q = str(argomenti.get("query", "")).strip()
        if not q:
            return [], "(query vuota)"
        if termini:
            q = q + " " + " ".join(termini)
        if contesto:
            q = q + " " + " ".join(contesto)
        qvec = recupero.embedding(q)
        documenti = _pre_seleziona(conn, qvec, gruppi)
        righe, _ = recupero.cerca(conn, q, gruppi, qvec=qvec,
                                  limite=8, vincolo=vincolo, documenti=documenti)
        return righe, _formatta(righe)
    if nome == "cerca_figure":
        oggetto = str(argomenti.get("oggetto", "")).strip()
        if not oggetto:
            return [], "(oggetto vuoto)"
        try:
            limite = int(argomenti.get("limite") or 12)
        except (TypeError, ValueError):
            limite = 12
        # Pre-selezione sull'INTERA query (oggetto + termini + contesto), ma il
        # REGEX della figura resta sul solo oggetto: il contesto («christmas»)
        # serve a scegliere il catalogo, non a tirare su figure natalizie quando
        # si cerca un diffusore (misurato il 25/09/2026).
        documenti = _pre_seleziona(
            conn, recupero.embedding(" ".join([oggetto] + termini + contesto)), gruppi)
        righe = recupero.cerca_figure(conn, [oggetto] + termini, vincolo, gruppi,
                                      limite=limite, documenti=documenti)
        return righe, _formatta_figure(righe)
    if nome == "cerca_esatta":
        t = str(argomenti.get("termine", "")).strip()
        if not t:
            return [], "(termine vuoto)"
        righe = recupero.cerca_esatta(conn, t, gruppi, limite=8)
        return righe, _formatta(righe)
    if nome == "grep":
        t = str(argomenti.get("termine", "")).strip()
        if not t:
            return [], "(termine vuoto)"
        righe = recupero.grep(conn, t, gruppi)
        if not righe:
            return [], f"(il termine '{t}' non compare in nessun documento visibile)"
        testo = "\n".join(f"- {r['documento']}: {r['pezzi']} pezzi "
                          f"(pagine {r['prima']}-{r['ultima']})" for r in righe)
        return [], testo
    if nome == "pagina":
        righe = recupero.pagina(conn, str(argomenti.get("documento", "")),
                                argomenti.get("pagina"), gruppi)
        return righe, _formatta(righe)
    if nome == "documenti":
        from orchestratore import indice
        descrizioni = indice.descrizioni_visibili(conn, gruppi)
        if descrizioni:
            testo = "\n".join(f"- {d}: {desc}" for d, desc in descrizioni)
        else:
            righe = recupero.documenti_visibili(conn, gruppi)
            testo = "\n".join(f"- {r['documento']} ({r['pezzi']} passi)" for r in righe)
        return [], testo or "(nessun documento visibile)"
    return [], "(strumento sconosciuto)"


def _chiama(messaggi):
    """Una chiamata al modello, con gli strumenti. Torna il messaggio assistant."""
    m = modello.messaggio(messaggi, MAX_TOKEN, tools=STRUMENTI, ragiona=AGENTE_RAGIONA)
    m.pop("reasoning_content", None)   # il ragionamento non si rimanda al modello
    return m


class Stato(TypedDict):
    domanda: str
    intent: str               # l'oggetto estratto da vincoli.estrae (per il guardrail)
    colore: list              # termini degli attributi enumerabili (guardia senza oggetto)
    contesto: list            # tema/occasione/uso: per la PRE-SELEZIONE, non nel regex
    messaggi: Annotated[list, operator.add]
    conn: Any
    gruppi: list
    vincolo: str              # regex must-match da vincoli.estrae (D19)
    intent_termini: list      # termini multilingue dell'oggetto, per il modello
    pezzi: dict               # chunk accumulati, per id
    passi: int


def _nodo_capisce(stato: Stato) -> dict:
    """Il passo D19: intent + vincoli dalla domanda. Degrada in silenzio."""
    from orchestratore import vincoli as v
    intent, intent_termini, contesto, trovati = v.estrae(stato["domanda"])
    # I termini del modello («sassi» -> «pietre») si espandono coi termini del
    # CORPUS («pietre» -> «rocks, dekosteine»), che il modello non sa.
    if not SENZA_GLOSSARIO:
        intent_termini = glossario.espandi(stato["conn"], intent_termini)
    # Il CONTESTO resta SEPARATO dall'oggetto: serve alla PRE-SELEZIONE del
    # catalogo, non al regex della figura. Se finisse nel regex («christmas»)
    # tirerebbe su le decorazioni natalizie invece dei diffusori (misurato
    # il 25/09/2026: «profumatore con essenze natalizie»).
    vincolo = v.regex(trovati)
    colore = v.termini_colore(trovati)
    pezzi, messaggi = {}, []
    # Guardrail FORTE: la prima ricerca la fa il sistema, con l'intent estratto,
    # e il risultato arriva al modello gia' pronto (non puo' ne' non cercare ne'
    # cercare con l'oggetto sbagliato).
    if GUARDIA_FORTE:
        oggetto = intent
        if not oggetto and GUARDIA_SENZA_OGGETTO and colore:
            # Solo colore: l'oggetto e' il primo termine e il resto entra nei
            # termini, cosi' la regex copre tutta la famiglia («crema, cream,
            # beige, ivory»). Con la sola «crema» (italiano, assente) la ricerca
            # troverebbe zero anche se «cream» ha 260 figure.
            oggetto = colore[0]
            intent_termini = intent_termini + [c for c in colore if c not in intent_termini]
        if oggetto:
            righe, testo = _esegui("cerca_figure", {"oggetto": oggetto},
                                   stato["conn"], stato["gruppi"], vincolo,
                                   intent_termini, contesto)
            pezzi = {r["id"]: r for r in righe}
            messaggi = [
                {"role": "assistant", "content": "", "tool_calls": [{
                    "id": "forza_ricerca", "type": "function",
                    "function": {"name": "cerca_figure",
                                 "arguments": json.dumps({"oggetto": oggetto})}}]},
                {"role": "tool", "tool_call_id": "forza_ricerca", "content": testo},
            ]
            # Fallback: la figura non c'e' (il prodotto sta nel TESTO: «profumatore»
            # non compare nelle descrizioni figura, ma il catalogo ne parla in
            # prosa). Si cerca anche nel testo e si passa il risultato.
            if not righe:
                righe2, testo2 = _esegui("cerca", {"query": oggetto},
                                         stato["conn"], stato["gruppi"], vincolo,
                                         intent_termini, contesto)
                pezzi = {r["id"]: r for r in righe2}
                messaggi += [
                    {"role": "assistant", "content": "", "tool_calls": [{
                        "id": "forza_ricerca_testo", "type": "function",
                        "function": {"name": "cerca",
                                     "arguments": json.dumps({"query": oggetto})}}]},
                    {"role": "tool", "tool_call_id": "forza_ricerca_testo", "content": testo2},
                ]
    return {"vincolo": vincolo, "intent_termini": intent_termini,
            "intent": intent, "colore": colore, "contesto": contesto,
            "pezzi": pezzi, "messaggi": messaggi}


def _nodo_agente(stato: Stato) -> dict:
    messaggio = _chiama(stato["messaggi"])
    # Guardrail REATTIVO: scatta solo se quello forte e' spento. Se il modello
    # non ha chiamato nessuno strumento e c'e' un oggetto, si forza la ricerca.
    # Senza, sul follow-up («e nastri azzurri?» dopo «sassi») il modello imita
    # la risposta precedente e inventa la pagina (misurato il 25/09/2026:
    # «Nastri azzurri: p. 38-39» senza alcuna ricerca).
    if (not GUARDIA_FORTE and not messaggio.get("tool_calls")
            and stato.get("passi", 0) == 0):
        oggetto = stato.get("intent")
        # senza oggetto ma con un colore («color crema»): si cerca il colore.
        if not oggetto and GUARDIA_SENZA_OGGETTO:
            oggetto = (stato.get("colore") or [""])[0]
        if oggetto:
            messaggio["tool_calls"] = [{
                "id": "forza_ricerca",
                "type": "function",
                "function": {"name": "cerca_figure",
                             "arguments": json.dumps({"oggetto": oggetto})},
            }]
    return {"messaggi": [messaggio], "passi": stato.get("passi", 0) + 1}


def _nodo_strumenti(stato: Stato) -> dict:
    ultimo = stato["messaggi"][-1]
    pezzi = dict(stato.get("pezzi") or {})
    messaggi = []
    for tc in ultimo.get("tool_calls") or []:
        nome = tc.get("function", {}).get("name", "")
        try:
            argomenti = json.loads(tc.get("function", {}).get("arguments") or "{}")
        except (TypeError, ValueError):
            argomenti = {}
        righe, testo = _esegui(nome, argomenti, stato["conn"], stato["gruppi"],
                               stato.get("vincolo", ""), stato.get("intent_termini", []),
                               stato.get("contesto", []))
        for r in righe:
            pezzi.setdefault(r["id"], r)
        messaggi.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                         "content": testo})
    return {"messaggi": messaggi, "pezzi": pezzi}


def _prossimo(stato: Stato) -> str:
    ultimo = stato["messaggi"][-1]
    if ultimo.get("tool_calls") and stato.get("passi", 0) < MAX_PASSI:
        return "strumenti"
    return "fine"


_grafo = StateGraph(Stato)
_grafo.add_node("capisce", _nodo_capisce)
_grafo.add_node("agente", _nodo_agente)
_grafo.add_node("strumenti", _nodo_strumenti)
_grafo.add_edge(START, "capisce")
_grafo.add_edge("capisce", "agente")
_grafo.add_conditional_edges("agente", _prossimo,
                             {"strumenti": "strumenti", "fine": END})
_grafo.add_edge("strumenti", "agente")
_compilato = _grafo.compile()


def cerca(conn, domanda: str, gruppi: list, limite: int = 8, storia: list = None):
    """L'agente: (righe, risposta).

    `storia` e' la conversazione intera (milestone): se c'e', l'agente capisce
    da se' saluti, consensi e «mostrami il resto», invece di ricevere la sola
    domanda del turno. `domanda` resta per l'estrazione di intent/vincoli."""
    messaggi = [{"role": "system", "content": ISTRUZIONI_SISTEMA}]
    if storia:
        messaggi += [m for m in storia if m.get("role") in ("user", "assistant")]
    else:
        messaggi.append({"role": "user", "content": domanda})
    stato = _compilato.invoke({
        "domanda": domanda,
        "intent": "",
        "colore": [],
        "contesto": [],
        "messaggi": messaggi,
        "conn": conn, "gruppi": gruppi, "vincolo": "", "intent_termini": [],
        "pezzi": {}, "passi": 0,
    })
    righe = list((stato.get("pezzi") or {}).values())[:limite]
    risposta = (stato["messaggi"][-1].get("content") or "").strip()
    return righe, risposta


def _prova():
    """Le regole che non chiamano il modello."""
    assert _formatta([]) == "(nessun risultato)"
    assert "[catalogo.pdf, pagina 3] testo" == _formatta(
        [{"id": 1, "documento": "catalogo.pdf", "page": 3, "content": "testo"}])
    assert _prossimo({"messaggi": [{"content": "ciao"}], "passi": 1}) == "fine"
    assert _prossimo({"messaggi": [{"tool_calls": [{"id": "x"}]}], "passi": 1}) == "strumenti"
    assert _prossimo({"messaggi": [{"tool_calls": [{"id": "x"}]}], "passi": 9}) == "fine"
    print("agente: regole verdi")


if __name__ == "__main__":
    _prova()
