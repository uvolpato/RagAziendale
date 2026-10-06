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
import re
import time
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from orchestratore import (egress, identita, mappa, modello,
                           recupero, sql_agente, vincoli as v)

ROTTA = os.environ.get("LLM_RAGIONAMENTO", "ragionamento")
SECONDI = float(os.environ.get("AGENTE_TIMEOUT", "120"))
MAX_PASSI = int(os.environ.get("AGENTE_MAX_PASSI", "4"))
VUOTO = (
    "0 righe. La query e' lecita e i permessi sono gia' applicati: semplicemente "
    "non trova niente, e questo e' un risultato, non un errore.\n"
    "Se non hai ancora provato, ALLARGA e riprova UNA volta sola:\n"
    "- togli un termine dall'AND, oppure metti i sinonimi in OR dentro lo stesso "
    "termine: cerca «palloni» come «soccer ball|football|fußball» e non come due "
    "condizioni separate;\n"
    "- prova l'altra tabella (le figure hanno solo la didascalia, il testo del "
    "documento e' nell'altra);\n"
    "- guarda che parole usano davvero i dati: la parola italiana dell'oggetto "
    "non c'e' mai.\n"
    "Non ripetere la stessa query: se hai gia' allargato e resta vuoto, ALLORA "
    "rispondi all'utente che non hai trovato nulla. Una risposta onesta e' "
    "sempre meglio di una pagina inventata."
)
# 1 = l'agente RAGIONA prima di chiamare gli strumenti (ragiona=True). Col
# ragionamento il modello puo' capire «ho gia' la risposta, mi fermo» invece di
# continuare a cercare. Reversibile. Costa piu' token, percio' il budget sale.
AGENTE_RAGIONA = os.environ.get("AGENTE_RAGIONA", "") == "1"
MAX_TOKEN = int(os.environ.get("AGENTE_MAX_TOKEN",
                               "8192" if AGENTE_RAGIONA else "2048"))
# 1 = il guardrail forza la ricerca anche quando non c'e' un oggetto ma c'e' un
# colore («color crema»): i termini del colore diventano l'oggetto da cercare.
GUARDIA_SENZA_OGGETTO = os.environ.get("GUARDIA_SENZA_OGGETTO", "") == "1"
# 1 = guardrail FORTE (reversibile): la prima ricerca la fa SEMPRE il sistema,
# con l'intent estratto, prima che il modello decida. Copre sia «il modello non
# cerca» sia «cerca con l'oggetto sbagliato». Quando e' acceso, il guardrail
# reattivo di _nodo_agente si spegne (la ricerca e' gia' fatta).
GUARDIA_FORTE = os.environ.get("GUARDIA_FORTE", "") == "1"
# 1 = il modello interroga il database scrivendo la SELECT (sql_agente.py), e i
# permessi li mette il codice. Con 0 resta il sistema di strumenti di prima
# (cerca/cerca_figure/estrae), che ognuno ha dentro le sue regole fisse.
# Reversibile: si cambia questa riga, non il codice sotto.
SQL_AGENTE = os.environ.get("SQL_AGENTE", "") == "1"
# 1 = pre-selezione dei documenti con l'indice (indice.py): prima si sceglie
# QUALI cataloghi c'entrano (descrizione del documento), poi si cerca dentro.
INDICE_PRESELEZIONE = os.environ.get("INDICE_PRESELEZIONE", "") == "1"

ISTRUZIONI_SISTEMA = (
    "Sei un assistente che cerca in un archivio aziendale di cataloghi e documenti.\n"
    "Il tuo compito e' INDICARE dove stanno le cose, non estrarre codici o dati: "
    "dai i riferimenti (documento e pagina), poi e' la persona a guardare.\n"
    "Comunichi SEMPRE in italiano, qualunque sia la lingua dei documenti: "
    "traduci i nomi dei prodotti (es. «Strauß mit Amaryllis» → «bouquet con "
    "amaryllis»), i codici articolo restano invariati.\n"
    "LINGUE: i cataloghi sono in LINGUE DIVERSE (italiano, tedesco, inglese, "
    "francese) e lo stesso oggetto ha un nome diverso in ogni lingua. Cercare "
    "solo la parola dell'utente in una lingua puo' NON trovare il prodotto che "
    "invece c'e'. Scrivi la query in PIU' lingue: «sassi» va cercato anche come "
    "«pietre», «deco rocks», «Steine», «stones»; «lavanda» come «lavender», "
    "«lavendel». Se ti sono stati dati dei termini per l'oggetto, USALI nella "
    "query. `cerca_esatta` e' SOLO per i codici articolo.\n"
    "QUERY: la query che passi a `cerca` e' tua, e arriva cosi' com'e' alla "
    "ricerca: non viene riscritta. Percio' deve contenere TUTTO quello che "
    "serve a trovare il prodotto, non una parola sola. Quando la domanda ha un "
    "oggetto e un attributo, mettici entrambi: per «nastri bianchi con cuori "
    "rossi» la query deve dire sia i nastri sia il bianco sia i cuori rossi, "
    "nelle lingue del catalogo. La query e' quello che il prodotto deve "
    "RICORDARE, quindi scrivila come lo descriveresti tu il prodotto che "
    "cerchi.\n"
    "Quando hai trovato PRODOTTI (figure di un catalogo), scegli tu come "
    "organizzare la risposta — prosa, elenchi, raggruppamenti per catalogo o "
    "per pagina: il modo piu' adatto a quello che presenti, senza schemi "
    "fissi, perche' le pubblicazioni possono essere molto diverse. Chi legge "
    "deve capire al volo cosa c'e' e dove guardare. Condizione che non salta "
    "mai: ogni articolo che citi nomina la sua pagina (es. «a pagina 24 del "
    "catalogo Gasper»), perche' il sistema trasforma «pagina N» nel "
    "collegamento — nessun articolo senza pagina. Enumera TUTTI gli articoli "
    "pertinenti che hai trovato, senza scorciarli.\n"
    "Per i DOCUMENTI di testo (policy, manuali, guide), riassumi il contenuto in "
    "prosa continua, con parole tue: NON elencare i passi uno per uno. Cita la "
    "pagina di ogni cosa che dici. Le fonti e i collegamenti stanno in fondo alla "
    "risposta, non nel corpo.\n"
    "Quando la risposta elenca piu' ARTICOLI (prodotti di un catalogo), "
    "presentali come elenco, senza seppellirli nella prosa.\n"
    "\n"
    "Rispondi solo su cio' che hai visto nei risultati degli strumenti: mai a "
    "memoria, mai citare un documento o una pagina che non hai visto in un "
    "risultato. Se non trovi niente, dillo: «non trovo documenti su questo».\n"
    "NON generare tu una sezione «Fonti» o «Riferimenti» in fondo: la aggiunge "
    "il sistema automaticamente. Tu cita solo le pagine nel corpo del testo.\n"
    "\n"
    "Strategia, che decidi TU secondo il tipo di domanda:\n"
    "- PRODOTTO o oggetto (es. «nastri blu», «vasi»): cerca PRIMA le figure con "
    "`cerca_figure`, e solo se non basta il testo. Rispondi indicando le pagine.\n"
    "- ASTRATTA (un bisogno senza oggetto, es. «un regalo per una trentenne»): "
    "parti dal contesto, arriva a prodotti concreti con `cerca_figure`. Rispondi "
    "indicando le pagine.\n"
    "- TESTO (come si fa una cosa, cosa dice una policy o un manuale): leggi i "
    "passi con `cerca` e `pagina`, e riassumi il contenuto citando la pagina.\n"
    "- ATTRIBUTO VISIVO o TESTO. La forma, il materiale e le misure sono VISIVI e "
    "stanno nelle figure: per quelli `cerca_figure`. Il COLORE puo' stare sia nel "
    "TESTO (codici colore, es. «DST1001 rot red») sia nelle FIGURE (le foto del "
    "prodotto): provalo in ENTRAMBI, `cerca` e `cerca_figure`. Il NOME del prodotto, "
    "la FRAGRANZA, il codice e il prezzo sono TESTO: `cerca`.\n"
    "- OGGETTO + ATTRIBUTO (es. «nastri BIANCHI con CUORI ROSSI»): sono due "
    "informazioni diverse e il prodotto giusto le deve avere entrambe. "
    "`cerca_figure` risponde all'oggetto e ti torna anche roba che c'entra solo a "
    "meta', quindi LEGGI le didascalie e scegli quelle che dicono la combinazione "
    "per intero: se dici «bianchi con cuori rossi», serve «white with red "
    "hearts», non un pezzo che ha il bianco e un altro che ha i cuori. Poi usa "
    "`cerca` con la combinazione intera per beccare quello che alle figure non e' "
    "uscito, e metti insieme le due ricerche senza perdere nessuna delle due.\n"
    "- Se la stessa descrizione compare in piu' cataloghi, sono prodotti "
    "diversi: citane tutti, ciascuno con la sua pagina.\n"
    "- Due pezzi che descrivono lo stesso prodotto ma con NUMERO DI PEZZI "
    "diverso («tre nastri...» e «due nastri...») sono due prodotti diversi, non "
    "la stessa cosa detta due volte: sono articoli separati del catalogo. "
    "Elenca entrambi, ciascuno con la sua pagina. Non fermarti al primo se la "
    "risposta ti chiede piu' pezzi.\n"
    "\n"
    "- Se la domanda e' un saluto o una chiacchiera, NON chiamare strumenti.\n"
    "- Quando hai gia' le pagine che rispondono, RISPONDI e fermati: non ripetere "
    "la stessa ricerca con parole diverse, non inseguire varianti. Se `cerca_figure` "
    "torna vuota per un oggetto, il prodotto sta nel TESTO: usa `cerca` UNA volta, "
    "poi rispondi con quello che hai. Se `cerca_figure` torna su pezzi che NON "
    "hanno l'attributo chiesto, quello e' il segnale che il prodotto sta nel "
    "testo: usa `cerca` con la combinazione intera UNA volta.\n"
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
        "description": "Cerca nei cataloghi per SIGNIFICATO e restituisce i passi piu' "
                       "pertinenti. Capisce le lingue: «lavanda» trova anche «lavender», "
                       "«sassi» trova anche «pietre decorative». Usala per oggetti, "
                       "fragranze e concetti. I vincoli di colore/misura/prezzo sono gia' "
                       "applicati dal sistema.",
        "parameters": {"type": "object",
                       "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "cerca_esatta",
        "description": "Cerca un CODICE articolo o un nome proprio ESATTO (es. DST2040), "
                       "identico in tutte le lingue. NON usarla per parole da tradurre: "
                       "«lavanda» non trova «lavender», per quelle usa `cerca`.",
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
                       "all'oggetto. Traduci l'oggetto nel termine del catalogo: «sassi» "
                       "si cerca come «pietre» o «deco rocks». Il colore e' gia' "
"filtrato dal sistema, ma attenzione: il filtro accetta anche "
                        "pezzi che c'entrano solo in parte, quindi dalla lista che "
                        "ricevi devi scegliere TU quelli che descrivono davvero la "
                        "combinazione chiesta, e scartare le altre. Tutte le parole che "
                       "distinguono il prodotto vanno nel campo «oggetto»: "
                       "«nastri bianchi con cuori rossi» -> «nastri ribbons white "
                       "red hearts bianco cuori rot weiß». Più parole scrivi, "
                       "più la ricerca e' precisa. Se la combinazione esatta non "
                       "c'e' fra le figure, usa anche `cerca`.",
        "parameters": {"type": "object",
                       "properties": {
                           "oggetto": {
                               "type": "string",
                               "description": "Cosa cerchi, in tutte le sue "
                                              "parti e nelle lingue del catalogo: "
                                              "l'oggetto e gli attributi che lo "
                                              "distinguono (colore, motivo, "
                                              "formato)."},
                           "limite": {"type": "integer",
                                      "description": "Quante figure restituire. "
                                                     "Di norma ne ricevi gia' 40. "
                                                     "Alza a 80 quando la persona "
                                                     "vuole un elenco completo di "
                                                     "un intero catalogo."}},
"required": ["oggetto"]}}},
]

STRUMENTO_SQL = {
    "type": "function", "function": {
        "name": "interroga",
        "description": (
            "Cerca in TUTTO l'archivio con una query che scrivi tu. È il modo più "
            "preciso che hai: con gli altri strumenti il sistema sceglie al posto "
            "tua quali parole pesare, con `interroga` le scegli tu. Usalo quando "
            "sai esattamente cosa cercare, e in particolare per mettere insieme più "
            "condizioni (oggetto E colore E motivo), che gli altri strumenti non fanno."
        ),
        "parameters": {"type": "object",
                       "properties": {
                           "sql": {"type": "string",
                                   "description": (
                                       "Una SELECT su immagini, chunks o documenti. "
                                       "Vedi la mappa dei dati per cosa contiene ogni "
                                       "campo e come è scritto. Metti sempre "
                                       "documento e page: sono quello che citi alla "
                                       "persona.")},
                           "motivo": {"type": "string",
                                      "description": "Cosa stai cercando, in una riga."}},
                       "required": ["sql"]}}}

# Con SQL_AGENTE c'e' UNO strumento solo, `interroga`, e il prompt lo dice per
# quello che e'. Non si rattacca quello vecchio: quello parla di `cerca`,
# `cerca_figure`, `pagina` e `cerca_esatta`, strumenti che qui non esistono, e
# un prompt che ordina strumenti inesistenti fa smettere di usare anche
# l'unico che c'e' (misurato il 30/09/2026: cinque domande di fila, zero
# chiamate a `interroga`, tutte risposte «non trovo documenti su questo»).
# Restano solo le regole che valgano comunque: in italiano, con la pagina, senza
# inventare, e senza la sezione Fonti (che aggiunge il sistema).
if SQL_AGENTE:
    STRUMENTI = [STRUMENTO_SQL]
    ISTRUZIONI_SISTEMA = (
        "Sei un assistente che cerca in un archivio aziendale di cataloghi e "
        "documenti.\n"
        "Hai UNO strumento solo: `interroga`, con cui cerchi scrivendo TU la "
        "query SQL. Non ce n'e' un altro, e non ne serve: la mappa dei dati che "
        "hai ricevuto dice cosa c'e', in che lingua e' scritto e come sono fatte "
        "le tabelle. Leggila, e decidi da te dove e come cercare.\n"
        "Il tuo compito e' INDICARE dove stanno le cose, non estrarre codici o "
        "dati: dai i riferimenti (documento e pagina), poi e' la persona a "
        "guardare.\n"
        "Comunichi SEMPRE in italiano, qualunque sia la lingua dei documenti: "
        "traduci i nomi dei prodotti, i codici articolo restano invariati.\n"
        "Rispondi solo su cio' che hai visto nei risultati: mai a memoria, mai "
        "citare un documento o una pagina che non hai visto in un risultato. Se "
        "non trovi niente, dillo esplicitamente.\n"
        "Ogni articolo che citi nomina la sua pagina (es. «a pagina 24 del "
        "catalogo Gasper»): il sistema trasforma «pagina N» nel collegamento, e "
        "nessun articolo senza pagina. Le fonti e i collegamenti stanno in fondo "
        "alla risposta, aggiunti dal sistema: NON generare tu una sezione "
        "«Fonti» o «Riferimenti».\n"
        "Come presenti quello che trovi lo decidi tu (prosa, elenco, raggruppato "
        "per catalogo), senza schemi fissi. Se sono articoli di catalogo, "
        "presentali come elenco. Per i documenti di testo riassumi in prosa, "
        "senza elencare i passi uno per uno.\n"
        "Regole di fondo:\n"
        "- La domanda contiene TUTTO quello che il prodotto deve avere: metti "
        "ogni condizione nella WHERE, con AND (per «nastri bianchi con cuori "
        "rossi»: il nastro E il bianco E i cuori rossi, ognuno nelle parole dei "
        "cataloghi). Se togli un termine, entrano anche gli articoli che NON "
        "hanno quella cosa, e finisci per consigliare il prodotto sbagliato.\n"
        "- Se la domanda e' un saluto o una chiacchiera, NON chiamare strumenti.\n"
        "- Hai un solo strumento e ti serve UNA volta: quando hai le pagine che "
        "rispondono, rispondi e fermati.\n"
        "- Se lo strumento ti dice che la query e' sbagliata, leggila e "
        "correggila: non cambiare domanda.\n"
        "- Se trovi l'OGGETTO ma non il QUALIFICATORE chiesto (i «diffusori» ma "
        "non «natalizi»), dillo in modo distinto: «ho trovato i diffusori a "
        "pagina P, ma non specificamente natalizi». Non mescolare un risultato "
        "generico con uno del qualificatore per far credere che esista cio' che "
        "non hai trovato.\n"
        "- Non inventare: se non trovi, dillo. Mai codici o prezzi inventati.\n"
    )


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


def _formatta_generica(righe):
    """Le righe come il modello le ha chieste: ogni colonna che ha messo nella
    SELECT, con il suo nome. Un formato fisso qui mentirebbe su quello che
    chiede (un conteggio non e' un testo, una pagina non c'e' in certe
    tabelle), e lui deve poter leggere quello che davvero vuole sapere."""
    if not righe:
        return "(nessun risultato)"
    fuori = []
    for r in righe:
        parti = []
        for colonna, valore in r.items():
            if valore is None:
                continue
            testo = " ".join(str(valore).split())
            if len(testo) > ASSAGGIO:
                testo = testo[:ASSAGGIO] + " …"
            parti.append(f"{colonna}={testo}")
        fuori.append(" | ".join(parti))
    return "\n".join(fuori)


def _pre_seleziona(conn, qvec, gruppi):
    """I documenti piu' pertinenti (indice.py), o None se l'indice non c'e' o la
    pre-selezione e' spenta. Con None la ricerca resta su tutto, come prima."""
    if not INDICE_PRESELEZIONE or qvec is None:
        return None
    from orchestratore import indice
    doc = indice.pertinenti(conn, qvec, gruppi, quanti=4)
    return [d[1] for d in doc] if doc else None


def _esegui(nome, argomenti, conn, gruppi, aziende=None, vincolo="",
            intent_termini=None, contesto=None, colore=None):
    """Esegue uno strumento e torna (righe, testo_per_il_modello).

    `gruppi`, `aziende`, `vincolo`, `intent_termini`, `contesto` e `colore`
    arrivano dalla chiusura, non dal modello: e' il guardrail. I termini
    multilingue dell'oggetto (intent_termini) si aggiungono alla query perche'
    il catalogo scrive «ribbons» dove la persona dice «nastri» (D19,
    query_di_ricerca).
    """
    aziende = aziende or []
    termini = intent_termini or []
    contesto = contesto or []
    colore = colore or []
    if nome == "cerca":
        q = str(argomenti.get("query", "")).strip()
        if not q:
            return [], "(query vuota)"
        # La query e' DEL MODELLO, con le sue parole: e' lui che ha letto la
        # domanda e sa cosa cerca. Non si riscrive. Prima qui la query veniva
        # SOSTITUITA da «termini[1:] + contesto», scartando quello che il
        # modello aveva scritto: la sua ricerca non arrivava mai all'embedding,
        # solo la lista di termini. Il modello riceve gia' i termini nelle
        # lingue del catalogo (nodo _nodo_capisce) e puo' aggiungere i colori e
        # il contesto che gli servono: e' la sua visione d'insieme, non una
        # formula. «nastri bianchi con cuori rossi» sul testo: senza la riscrittura
        # la p.27 («white with red hearts») esce, con la riscrittura la query
        # diventava «ribbons bänder tapes cuori hearts herzen» e finiva al 15°
        # posto, fuori dal limite (misurato il 30/09/2026).
        qvec = recupero.embedding(q, query=True)
        documenti = _pre_seleziona(conn, qvec, gruppi)
        righe, _ = recupero.cerca(conn, q, gruppi, qvec=qvec,
                                  limite=8, vincolo=vincolo, documenti=documenti)
        return righe, _formatta(righe)
    if nome == "interroga":
        # La query la scrive il MODELLO, parole e condizioni sue: e' lui che
        # ha letto la domanda e sa cosa cerca. Non viene riscritta, non
        # scomposta, non filtrata. I permessi li mette il codice dentro
        # `sql_agente`, quindi il modello non puo' aggirarli scrivendo la
        # WHERE: non e' una riga che lui vede, nasce dopo (sql_agente.py).
        sql = str(argomenti.get("sql", "")).strip()
        if not sql:
            return [], "(query vuota)"
        righe, problemi = sql_agente.esegui(conn, sql, gruppi, aziende)
        if problemi:
            # Si dicono per intero: altrimenti il modello riscrive la stessa
            # query e non impara cosa aveva sbagliato.
            return [], "QUERY RESPINTA:\n" + "\n".join(f"- {p}" for p in problemi)
        if not righe:
            # Zero righe NON e' un errore: la query e' lecita e i permessi
            # sono applicati. Senza questo messaggio il modello non lo sapeva
            # e ripeteva la query identica fino a esaurire i passi, poi
            # rispondeva nel vuoto (misurato il 30/09/2026 su «diffusori
            # natalizi»: 3 query identiche, 108 secondi, risposta vuota).
            return [], VUOTO
        return righe, _formatta_generica(righe)
    if nome == "cerca_figure":
        oggetto = str(argomenti.get("oggetto", "")).strip()
        if not oggetto:
            return [], "(oggetto vuoto)"
        try:
            limite = int(argomenti.get("limite") or 40)
        except (TypeError, ValueError):
            limite = 40
        # Il PESO di `cerca_figure` conta i termini che il MODELLO ha prodotto:
        # l'oggetto in tutte le sue lingue (`termini`) e il motivo/occasione
        # (`contesto`). Non e' una regola che decide al posto suo, e' il suo
        # ragionamento che il codice misura: se il modello dice che la domanda e'
        # di nastri e di cuori, «white with red hearts» (che nomina tutti e due)
        # va avanti a «ribbons and bows» (che non ne nomina nessuno). L'`oggetto`
        # che il modello scrive qui resta solo filtro: e' un blocco unico, e come
        # regex matcha niente, perche' in una didascalia le parole non sono
        # attaccate cosi' (misurato il 30/09/2026).
        # Senza `contesto` nel peso p27 non entrava nella pool, e il modello non
        # poteva sceglierlo: non lo ignorava, non lo vedeva.
        documenti = _pre_seleziona(
            conn, recupero.embedding(" ".join([oggetto] + termini + contesto + colore), query=True), gruppi)
        righe = recupero.cerca_figure(conn, termini + contesto, vincolo, gruppi,
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


def _nota(strumento, argomenti, righe, t0) -> dict:
    """Una chiamata sulla traccia: che strumento, con quali argomenti, quante
    righe tornate e quanto ci ha messo. Senza questo, «perche' ha risposto
    cosi'?» si risponde ricostruendo a mano (migrazione 024)."""
    return {"strumento": strumento, "argomenti": argomenti, "righe": len(righe),
            "ms": int((time.monotonic() - t0) * 1000)}


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
    aziende: list           # per l'ACL di sql_agente: dai gruppi «azienda-<codice>»
    vincolo: str              # regex must-match da vincoli.estrae (D19)
    intent_termini: list      # termini multilingue dell'oggetto, per il modello
    pezzi: dict               # chunk accumulati, per id
    passi: int
    traccia: list             # chiamate agli strumenti, in ordine (migrazione 024)


def _nodo_capisce(stato: Stato) -> dict:
    """Passo D19: intent + vincoli dalla domanda. Degrada in silenzio.

    NON cerca: la ricerca la fa l'AGENTE coi suoi strumenti. Qui si estraggono
    solo il vincolo di colore/misura/prezzo (must-match, che resta) e i termini
    multilingue (servono a `cerca_figure` per il confronto esatto). La query del
    testo la decide il modello, con le sue parole: se dice «sassi» e il catalogo
    risponde «sabbia», e' lui a giudicare e a riprovare con «pietre».
    """
    if SQL_AGENTE:
        # Il percorso SQL non smonta la domanda. `estrae` (il vecchio guardrail
        # D19) divideva «nastri bianchi con cuori rossi» in oggetto + colore +
        # motivo e li rifilava al modello gia' spacchettati («per l'oggetto usa
        # ribbons... | per il colore usa bianco/white... | per il motivo usa
        # cuori/hearts...»): il modello li riuniva in frasi, `~* 'white ribbon'
        # AND ~* 'red hearts'`, e la didascalia vera («white with red hearts»,
        # parole non attaccate) non usciva (misurato il 30/09/2026: 0 righe
        # contro 21 della stessa domanda scritta a parole separate). La mappa
        # dei dati e' gia' nel prompt: il modello legge la domanda intera e
        # scrive la query da solo, come faccio io, senza un'intermediario che
        # gli spezza il concetto. Col `intent` vuoto si spegne anche il
        # guardrail reattivo che forzava `cerca_figure` (strumento che in
        # questo percorso non esiste).
        return {"vincolo": "", "intent_termini": [], "intent": "",
                "colore": [], "contesto": [], "pezzi": {},
                "messaggi": [], "traccia": stato.get("traccia") or []}
    intent, intent_termini, contesto, trovati = v.estrae(stato["domanda"])
    vincolo = v.regex(trovati)
    colore = v.termini_colore(trovati)
    # Al modello si dice TUTTO quello che la domanda chiede, nelle lingue dei
    # cataloghi: l'oggetto, gli attributi e il contesto. Non e' una formula da
    # applicare al posto suo, e' il materiale con cui lui scrive la query: se
   # non sa che «rossi» vuol dire anche «red» e «rot», non puo' scrivere una
    # query che trova il prodotto (era il buco di «nastri bianchi con cuori
    # rossi»: la query finiva senza i colori e la pagina giusta non usciva).
    parti = []
    if intent_termini and len(intent_termini) > 1:
        parti.append("per l'oggetto usa questi termini (lingue del catalogo): "
                     + ", ".join(intent_termini[1:]))
    if colore:
        parti.append("per il colore usa questi termini (lingue del catalogo): "
                     + ", ".join(colore))
    if contesto:
        parti.append("per il motivo/occasione usa questi termini: "
                     + ", ".join(contesto))
    # La mappa dei dati NON va qui: e' gia' nel system messaggio (vedi `cerca`),
    # e qui finirebbe due volte in un turno solo.
    messaggi = ([{"role": "system", "content":
                  "Nella query che scrivi mettici tutto quello che la domanda chiede. "
                  + " | ".join(parti) + "."}] if parti else [])
    return {"vincolo": vincolo, "intent_termini": intent_termini,
            "intent": intent, "colore": colore, "contesto": contesto,
            "pezzi": {}, "messaggi": messaggi,
            "traccia": stato.get("traccia") or []}


# Il modello in SQL_AGENTE puo' rispondere «non ho trovato» senza aver MAI
# chiamato `interroga`: e' il «liberta'» che la mappa non toglie, e un utente che
# chiede un prodotto riceve un falso negativo su qualcosa che esiste (misurato il
# 30/09/2026: «ho bisogno di nastri bianchi con cuori rossi» -> «non ho trovato»
# con STRUMENTI vuoti). Il guardrail scarta la risposta prematura e lo rimanda a
# cercare: non decide COSA cercare (quello resta suo), decide solo che PRIMA
# deve cercare. Un saluto («ciao») non e' un «non ho trovato» e passa liscio.
NUDGE_SQL = (
    "Non hai ancora cercato nulla con `interroga` prima di rispondere: scrivi "
    "la SELECT, eseguila e rispondi solo su cio' che vedi nei risultati."
)
_NON_TROVATO = re.compile(
    r"non\s+(?:ho\s+)?trovato|non\s+trovo|nessun\s+risultato|nessuna\s+"
    r"corrispondenza|non\s+esiste|non\s+ci\s+sono", re.I)


def _pare_non_trovato(testo: str) -> bool:
    return bool(_NON_TROVATO.search(testo or ""))


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
    # In SQL_AGENTE il guardrail reattivo di sopra non ha un intent (estrae e'
    # spento) ne' uno strumento `cerca_figure`: il suo posto lo prende questo.
    if (SQL_AGENTE and not messaggio.get("tool_calls")
            and not (stato.get("traccia") or [])
            and _pare_non_trovato(messaggio.get("content") or "")):
        # Risposta prematura: la si scarta e si rimanda a interroga. Il
        # `_prossimo` riporta ad `agente` finche' non ha cercato qualcosa.
        messaggio["content"] = ""
        return {"messaggi": [messaggio,
                             {"role": "user", "content": NUDGE_SQL}],
                "passi": stato.get("passi", 0) + 1}
    return {"messaggi": [messaggio], "passi": stato.get("passi", 0) + 1}


RIPETIZIONE = (
    "Questa identica chiamata l'hai gia' fatta in questo turno, e non la si "
    "riflette: il risultato e' quello che hai gia' davanti, zero righe se cosi' "
    "era. Cambia qualcosa nella query — togli un termine dall'AND, metti i "
    "sinonimi in OR dentro un termine, prova l'altra tabella — oppure, se hai "
    "gia' provato, rispondi all'utente che non hai trovato nulla. Non "
    "rispondere nel vuoto."
)


def _gia_fatta(stato: Stato, nome: str, argomenti: dict) -> bool:
    """True se questa ESATTA chiamata e' gia' stata eseguita nel turno.

    Non e' una regola di ricerca, e' un freno allo spreco: il modello puo'
    ripetere la query identica anche leggendo l'avviso che tornava zero righe
    (misurato il 30/09/2026 su «diffusori natalizi»: tre volte la stessa
    query, 108 secondi, e la risposta finale non c'era). Rileggere la stessa
    cosa da Postgres dara' esattamente lo stesso zero: si risponde una volta
    sola e gli si dice cosa fare."""
    if not isinstance(argomenti, dict):
        return False
    # Solo la parte che cambia il risultato: `motivo` e' un commento in
    # chiaro, e due query identiche con due motivi diversi sono la STESSA
    # ricerca (misurato il 30/09/2026: su «diffusori natalizi» la stessa query e'
    # stata eseguita due volte perche' il commento era diverso).
    def peso(a):
        return {k: v for k, v in a.items() if k in ("sql", "query", "oggetto",
                                                   "termine", "pagina")}
    # `[:-1]`: l'ultimo messaggio e' la chiamata che STIAMO per eseguire, non
    # una precedente. Senza questo scarto la funzione trovava se stessa, non
    # eseguiva niente e rispondeva al modello «questa l'hai gia' fatta» (misurato
    # il 30/09/2026: cinque domande, zero query eseguite, zero pezzi).
    for m in (stato.get("messaggi") or [])[:-1]:
        for tc in m.get("tool_calls") or []:
            if tc.get("function", {}).get("name") != nome:
                continue
            try:
                prima = json.loads(tc.get("function", {}).get("arguments") or "{}")
            except (TypeError, ValueError):
                continue
            if isinstance(prima, dict) and peso(prima) and peso(prima) == peso(argomenti):
                return True
    return False


def _nodo_strumenti(stato: Stato) -> dict:
    ultimo = stato["messaggi"][-1]
    pezzi = dict(stato.get("pezzi") or {})
    traccia = list(stato.get("traccia") or [])
    messaggi = []
    for tc in ultimo.get("tool_calls") or []:
        nome = tc.get("function", {}).get("name", "")
        try:
            argomenti = json.loads(tc.get("function", {}).get("arguments") or "{}")
        except (TypeError, ValueError):
            argomenti = {}
        t0 = time.monotonic()
        if _gia_fatta(stato, nome, argomenti):
            # Non si esegue due volte: si dice e si fa contare il passo.
            messaggi.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                             "content": RIPETIZIONE})
            continue
        righe, testo = _esegui(nome, argomenti, stato["conn"], stato["gruppi"],
                               stato.get("aziende", []),
                               stato.get("vincolo", ""), stato.get("intent_termini", []),
                               stato.get("contesto", []), stato.get("colore", []))
        traccia.append(_nota(nome, argomenti, righe, t0))
        for i, r in enumerate(righe):
            # I pezzi si raccolgono per id, ma `interroga` lascia al modello
            # scegliere le colonne: se scrive «SELECT documento, page» non c'e'
            # `id`, e va benissimo (misurato il 30/09/2026: KeyError 'id', la
            # chat intera rispondeva 500). La chiave allora e' la posizione
            # della riga, che basta a tenere ordine e unicita'.
            pezzi.setdefault(r.get("id") or f"{nome}:{i}", r)
        messaggi.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                         "content": testo})
    return {"messaggi": messaggi, "pezzi": pezzi, "traccia": traccia}


def _prossimo(stato: Stato) -> str:
    ultimo = stato["messaggi"][-1]
    if ultimo.get("tool_calls") and stato.get("passi", 0) < MAX_PASSI:
        return "strumenti"
    # In SQL_AGENTE, se l'ultimo messaggio e' il nudge del guardrail (il modello
    # aveva risposto «non ho trovato» senza cercare), si torna all'agente per
    # lasciargli scrivere la SELECT. Un saluto non produce il nudge e finisce qui.
    if (SQL_AGENTE and not ultimo.get("tool_calls")
            and ultimo.get("role") == "user"
            and ultimo.get("content") == NUDGE_SQL
            and stato.get("passi", 0) < MAX_PASSI):
        return "agente"
    return "fine"


_grafo = StateGraph(Stato)
_grafo.add_node("capisce", _nodo_capisce)
_grafo.add_node("agente", _nodo_agente)
_grafo.add_node("strumenti", _nodo_strumenti)
_grafo.add_edge(START, "capisce")
_grafo.add_edge("capisce", "agente")
_grafo.add_conditional_edges("agente", _prossimo,
                             {"strumenti": "strumenti", "agente": "agente",
                              "fine": END})
_grafo.add_edge("strumenti", "agente")
_compilato = _grafo.compile()


def cerca(conn, domanda: str, gruppi: list, limite: int = 8, storia: list = None):
    """L'agente: (righe, risposta, traccia).

    `storia` e' la conversazione intera (milestone): se c'e', l'agente capisce
    da se' saluti, consensi e «mostrami il resto», invece di ricevere la sola
    domanda del turno. `domanda` resta per l'estrazione di intent/vincoli.

    `traccia` e' quello che il turno ha davvero fatto — le chiamate agli
    strumenti e la riga «cosa stavamo cercando» — da salvare nelle tracce
    (migrazione 024)."""
    messaggi = [{"role": "system", "content": ISTRUZIONI_SISTEMA}]
    if storia:
        messaggi += [m for m in storia if m.get("role") in ("user", "assistant")]
    else:
        messaggi.append({"role": "user", "content": domanda})
    # La mappa dei dati: dove sta cosa e in che lingua e' scritta. Va nel
    # system messaggio, non nello stato, perche' e' costante e non cambia da
    # una domanda all'altra (e non finisce nelle tracce a ogni turno). Solo con
    # SQL_AGENTE: a sistema spento il prompt deve essere esattamente quello di
    # prima, e la mappa non serve a chi non scrive la query (misurato il
    # 30/09/2026: era inserita anche a flag spento, quindi il funzionante era
    # gia' stato toccato).
    if SQL_AGENTE:
        messaggi.insert(1, {"role": "system",
                            "content": mappa.mappa_dati(conn)})
    stato = _compilato.invoke({
        "domanda": domanda,
        "intent": "",
        "colore": [],
        "contesto": [],
        "messaggi": messaggi,
        "conn": conn, "gruppi": gruppi, "aziende": identita.aziende(gruppi),
        "vincolo": "", "intent_termini": [],
        "pezzi": {}, "passi": 0, "traccia": [],
    })
    righe = list((stato.get("pezzi") or {}).values())[:limite]
    risposta = (stato["messaggi"][-1].get("content") or "").strip()
    if not risposta:
        # Il modello puo' esaurire i passi senza scrivere niente: si e' visto
        # il 30/09/2026 su «diffusori natalizi», che e' rimasta muta. Un utente
        # non riceve una risposta vuota: o ci sono pezzi e si dice cosa si e'
        # trovato, o si dice onestamente che non si e' trovato nulla. Entrambe
        # le frasi dicono solo quello che e' successo, non inventano.
        risposta = ("Non ho trovato nulla che risponda alla domanda."
                    if not righe else
                    "Ho trovato delle pagine, ma non sono riuscito a "
                    "formulare una risposta: guarda i riferimenti qui sotto.")
    return righe, risposta, {
        "strumenti": stato.get("traccia") or [],
        "ricerca": "intent=%s | termini=%s | contesto=%s | vincolo=%s | pezzi=%d" % (
            stato.get("intent") or "-",
            ",".join(stato.get("intent_termini") or []) or "-",
            ",".join(stato.get("contesto") or []) or "-",
            (stato.get("vincolo") or "-")[:120], len(righe)),
    }


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
