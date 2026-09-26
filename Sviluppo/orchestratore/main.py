"""Server HTTP dell'orchestratore: l'endpoint OpenAI-compatible che LibreChat
chiama, e il nodo che cuce la catena RAG.

    uvicorn orchestratore.main:app

Flusso di un turno (SPECIFICA-CONNETTORI? no, questa e' la chat):

    1. verifica del token (identita.py) -> gruppi e aziende
    2. embedding della domanda (recupero.embedding) -> vettore o None
    3. ricerca ibrida con ACL nella query (recupero.cerca)
    4. gate: contaminazione e scelta della rotta (gate.applica)
    5. prompt (prompt.py) + chiamata a LiteLLM in streaming
    6. traccia su `traces` per la diagnosi

Il modello e' una variabile di LiteLLM: qui si conosce solo il nome logico
della rotta decisa dal gate. L'endpoint espone UN SOLO modello ("assistente-v1"):
LibreChat non fa scegliere nulla all'utente (librechat.yaml.tmpl).
"""
import json
import os
import re
import time

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from orchestratore import documento as documento_mod
from orchestratore import agente, egress, gate, identita, immagini, immagini_articoli, indice, memoria, modello, prompt, recupero, ricerca_agente, riformula, vincoli

app = FastAPI()

MODEL_NAME = "assistente-v1"
LITELLM = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000").rstrip("/")
MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
APP_HOST = os.environ.get("APP_HOST", "assistente.localhost")
# Temperatura bassa ma non zero: a zero anche la decodifica avida puo' entrare
# in loop su un contesto pieno di righe di tabella quasi uguali.
TEMPERATURA = float(os.environ.get("TEMPERATURA", "0.2"))
# Budget di token della risposta. Con il 27B "rco" (reasoning content output)
# il modello ragiona ad alta voce PRIMA di rispondere: senza un budget
# sufficiente il ragionamento si mangia i token e il `content` resta vuoto
# (verificato il 24/09/2026). 4096 copre ragionamento + risposta con citazioni.
MAX_TOKEN = int(os.environ.get("MAX_TOKEN_RISPOSTA", "4096"))
# NIENTE frequency_penalty da qui. Provata il 22/09/2026 e scartata subito:
# punisce i token gia' usati, e una citazione come «[2]» si ripete
# legittimamente dieci volte in una risposta. Il modello ha smesso di citare i
# numeri e ha scritto «il natur [n], il creme [n], il rosa [n]».
# Contro la degenerazione agisce `--repeat-penalty 1.1` sul server
# (modelli/llama-swap.yaml): finestra corta, non tocca le citazioni.

# Quante figure si mostrano: NON un numero fisso. Il tetto esiste per non
# allagare la chat, ma quante mostrarne lo decide la domanda (vedi _scelte).
# Fino al 22/09/2026 erano sempre quattro, riempite con le piu' vicine che
# c'erano: nel testo non si vede, perche' il modello scarta cio' che non
# serve, ma una figura mostrata e' un'affermazione — «questa c'entra» — e
# riempire significa dirne tre false.
MAX_IMMAGINI = 12
# Quanto puo' essere peggiore di quella buona una figura perche' valga la pena
# mostrarla lo stesso, quando la domanda NON nomina niente di preciso: il 25
# per cento di distanza in piu'. Serve solo per le domande descrittive, dove
# non c'e' un criterio esatto e il numero giusto non lo sa nessuno.
QUOTA_PEGGIO = float(os.environ.get("IMMAGINI_QUOTA_PEGGIO", "0.25"))
# Quante se ne mostrano quando la domanda non permette di sceglierle con
# esattezza: un campione, non un catalogo.
CAMPIONE = int(os.environ.get("IMMAGINI_CAMPIONE", "4"))
# Coda del prompt di sistema, per le stranezze del modello del momento: sta in
# configurazione perche' cambia col modello, e cambiare modello non deve voler
# dire toccare il codice. Oggi serve per Qwen3, che ragiona a voce alta: senza
# "/no_think" LM Studio manda il ragionamento in reasoning_content e LibreChat
# riceve una risposta VUOTA (provato il 20/09/2026). Per il RAG il ragionamento
# non serve: la risposta deve stare nei documenti recuperati.
SUFFISSO_SISTEMA = os.environ.get("SUFFISSO_SISTEMA", "")
# Prova A/B: salta riformulazione e estrazione vincoli (i due passi LLM che
# iniettano errori). Si cerca la domanda dell'utente TALE E QUALE, senza
# riscrittura ne' pool must-match: e' il comportamento di RAGFlow/Onyx.
# Variabile d'ambiente, non codice: per tornare indietro basta riavviare senza.
SENZA_ESTRAZIONE = os.environ.get("SENZA_ESTRAZIONE", "") == "1"


@app.on_event("startup")
def _autocontrollo():
    # Fallimento rumoroso al boot, non silenzioso in esercizio.
    identita.autocontrollo()


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


# Lock preso dall'indicizzazione mentre il modello di chat e' scaricato per
# fare spazio a Docling sulla GPU (ingestion/indicizza.py, BLOCCO_LLM).
BLOCCO_LLM = 7_310_062
# Messaggio GENERICO di proposito: lo legge chiunque, e i nomi dei file o il
# loro numero direbbero cosa sta entrando nelle aree di altri.
INDICE_IN_AGGIORNAMENTO = ("Sto aggiornando l'indice dei documenti e in questo momento non posso rispondere. "
                           "Riprova fra qualche minuto.")


def _indice_in_aggiornamento(conn) -> bool:
    """True se l'indicizzazione ha scaricato il modello. Senza questo controllo
    la prima domanda lo farebbe ricaricare a meta' lettura (LM Studio carica su
    richiesta), e i due si toglierebbero la VRAM a vicenda. Il lock e' della
    connessione dell'indicizzazione: se quel servizio muore, sparisce."""
    with conn.cursor() as cur:
        cur.execute("""SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory'
                                       AND classid = 0 AND objid = %s AND objsubid = 1 AND granted) AS bloccato""",
                    (BLOCCO_LLM,))
        return bool(cur.fetchone()["bloccato"])


def _ricorda_gruppi(conn, utente: str, gruppi: list) -> None:
    """Ultimo elenco di gruppi visto nel token di questa persona (migrazione
    015). Non e' un archivio di identita': solo il `sub` e i gruppi, che stanno
    gia' nel token."""
    if not utente:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO gruppi_utente (utente, gruppi, aggiornato_il)
                           VALUES (%s, %s, now())
                           ON CONFLICT (utente) DO UPDATE SET gruppi = EXCLUDED.gruppi,
                             aggiornato_il = now()""", (utente, gruppi))
        conn.commit()
    except Exception as e:      # non si rompe un turno di chat per questo
        print(f"gruppi non registrati per {utente}: {type(e).__name__}: {e}", flush=True)


def _gruppi_della_richiesta(request, conn, utente: str) -> list:
    """I gruppi di chi sta chiedendo l'immagine, nell'ordine di affidabilita'.

    1. `X-Forwarded-Groups`, messo da oauth2-proxy dopo aver autenticato la
       persona con Keycloak: e' la SESSIONE, quindi sono i gruppi di adesso.
    2. Altrimenti l'ultimo elenco visto nel token di quella persona
       (migrazione 015): serve finche' la sessione non c'e' — per esempio
       quando l'orchestratore viene raggiunto dalla rete interna.

    In entrambi i casi il permesso si ricontrolla su `sources`: la sessione
    dice CHI sei, non COSA puoi vedere.
    """
    intestazione = (request.headers.get("x-forwarded-groups") or "") if request is not None else ""
    if intestazione:
        # oauth2-proxy le separa con virgola; Keycloak le scrive con lo slash
        # davanti perche' i gruppi sono un albero (full.path): "/vendite".
        return [g.strip().lstrip("/") for g in intestazione.split(",") if g.strip()]
    return _gruppi_noti(conn, utente)


def _gruppi_noti(conn, utente: str) -> list:
    """I gruppi dell'ultimo token di quella persona, o [] se non li abbiamo."""
    if not utente:
        return []
    with conn.cursor() as cur:
        cur.execute("SELECT gruppi FROM gruppi_utente WHERE utente = %s", (utente,))
        riga = cur.fetchone()
    return (riga["gruppi"] if isinstance(riga, dict) else riga[0]) if riga else []


def _risposta_unica(testo: str):
    """Una risposta finta ma ben formata: LibreChat si aspetta lo stream SSE."""
    def gen():
        yield f"data: {json.dumps({'choices': [{'delta': {'role': 'assistant', 'content': testo}, 'index': 0}], 'model': MODEL_NAME})}\n\n"
        yield f"data: {json.dumps({'choices': [{'delta': {}, 'index': 0, 'finish_reason': 'stop'}], 'model': MODEL_NAME})}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


def _senza_aggiunte(messaggio: dict) -> dict:
    """Toglie dalla cronologia TUTTE le righe che ha scritto il SISTEMA, non il
    modello: l'elenco delle fonti, l'offerta delle immagini, i collegamenti
    alle figure, e il filetto che le separa.

    Rimandargliele indietro lo porta a imitarle. Il 20/09/2026 era l'offerta a
    comparire due volte; il 21/09/2026, sulla stessa conversazione, erano le
    FONTI: il modello ricopiava di sana pianta l'elenco del turno prima e il
    sistema gli accodava quello vero, con numeri diversi. Due blocchi «Fonti»
    di seguito, e i riferimenti [n] del primo puntavano ai pezzi di un altro
    turno — cioe' esattamente il contrario di quello che l'elenco serve a
    fare. Al modello interessa cosa ha detto, non come il sistema ha decorato
    la risposta."""
    if messaggio.get("role") != "assistant":
        return messaggio
    righe = []
    dopo_immagine = False
    for r in messaggio["content"].splitlines():
        if MARCA_OFFERTA in r or MARCA_FONTI in r:
            continue
        if "![immagine" in r:
            # Con le figure se ne vanno intestazione e separatore della loro
            # tabella, che altrimenti restano orfani. Si tolgono solo QUI,
            # attaccati a una riga di figure: «|---|---|» da solo e' anche il
            # separatore di una tabella scritta dal modello, e quella resta.
            while righe and IMPALCATURA_TABELLA.match(righe[-1]):
                righe.pop()
            # La riga delle didascalie viene SUBITO dopo quella delle figure:
            # la riconosce la posizione, non un marcatore — e' testo piano in
            # una cella di tabella, e dal 24/09/2026 niente <sub> la segna.
            dopo_immagine = True
            continue
        if dopo_immagine and r.lstrip().startswith("|"):
            dopo_immagine = False
            continue
        dopo_immagine = False
        righe.append(r)
    # Il filetto restava orfano dell'elenco che introduceva.
    while righe and righe[-1].strip() in ("---", ""):
        righe.pop()
    return {**messaggio, "content": "\n".join(righe).strip()}


def _domanda(messages) -> str:
    """L'ultimo messaggio dell'utente: e' la domanda di questo turno."""
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"].strip()
    return ""


# Il prompt che LibreChat manda per chiedere il TITOLO automatico della
# conversazione. Non e' una domanda sui documenti: e' una richiesta di servizio
# del frontend. Se passa dal flusso di ricerca, intasa il modello e fa scattare
# i timeout (misurato il 23/09/2026).
TITOLO_LIBRECHAT = re.compile(
    r"provide a concise,?\s+5-word-or-less\s+title", re.I
)


def _e_titolo_librechat(domanda) -> bool:
    return bool(domanda) and bool(TITOLO_LIBRECHAT.search(domanda))


def _titolo_librechat(domanda):
    """Genera il titolo della conversazione, senza ricerca ne' gate.

    LibreChat manda gia' l'intera conversazione dentro la domanda; qui la si
    rimanda al modello e si restituisce la risposta. Nessun filtro ACL: non si
    leggono documenti, si riassume solo cio' che l'utente ha gia' scritto e
    visto nella sua stessa chat."""
    messaggi = [{"role": "system", "content": SUFFISSO_SISTEMA},
                {"role": "user", "content": domanda}]

    def gen():
        try:
            for pezzo in _stream_litellm(messaggi, os.environ.get("LLM_RAGIONAMENTO", "ragionamento"), {}):
                yield pezzo
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'upstream_error'}})}\n\n"
            yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


def _e_chiacchiera(domanda) -> bool:
    """Saluto o chiacchiera (non una ricerca)? Una risposta secca del modello,
    senza ragionamento: costa ~1,5 s e salva l'intera ricerca sui saluti."""
    if not domanda or len(domanda.strip()) > 120:
        return False
    try:
        testo = modello.chiedi([{
            "role": "system",
            "content": ("Rispondi SOLO «si» o «no».\n"
                        "«si» = e' un saluto, un ringraziamento o una chiacchiera "
                        "che NON chiede di cercare nei documenti (es. «ciao», "
                        "«grazie», «come stai», «ok»).\n"
                        "«no» = chiede qualcosa da cercare nei documenti."),
        }, {"role": "user", "content": domanda}], max_tokens=8)
        return testo.strip().lower().startswith("si")
    except Exception:
        return False


def _chiacchiera(domanda):
    """Risposta diretta a un saluto: niente ricerca, niente ragionamento."""
    try:
        testo = modello.chiedi([{"role": "system", "content": SUFFISSO_SISTEMA},
                                {"role": "user", "content": domanda}])
    except Exception:
        testo = "Ciao! Come posso aiutarti?"
    return _risposta_unica(testo)


# Le immagini non si attaccano piu' a ogni risposta: si OFFRONO, e si mostrano
# a chi le chiede. Vederle serve di rado (un catalogo, uno schema), e quattro
# figure in fondo a ogni risposta sono rumore che nasconde il testo.
# IMMAGINI_SU_RICHIESTA=0 torna al comportamento di prima.
SU_RICHIESTA = os.environ.get("IMMAGINI_SU_RICHIESTA", "1") != "0"
# Frase dell'offerta. Contiene MARCA: al turno dopo si guarda se l'assistente
# aveva davvero offerto qualcosa, prima di interpretare un "si" come consenso.
MARCA_OFFERTA = "immagini collegate a questa risposta"
# Offerta di mostrare il resto dell'elenco: al turno dopo si riconosce il "si'".
MARCA_ALTRO = "Vuoi che te le elenchi tutte?"
# Le due marche servono a due cose insieme: scrivere la riga (_fonti_citate,
# _blocco_offerta) e RICONOSCERLA nella cronologia per toglierla
# (_senza_aggiunte). Una sola costante, cosi' non possono divergere.
MARCA_FONTI = "_Fonti: "
# Intestazione e riga di separazione della tabella delle figure: celle VUOTE,
# solo barre, trattini e spazi. Tolta la riga con le immagini resterebbero
# orfane, e il modello se le rivedrebbe in cronologia. Una tabella vera del
# modello ha del testo nelle celle, quindi non combacia.
IMPALCATURA_TABELLA = re.compile(r"^\s*\|[\s|:-]*\|\s*$")
# "Si", "mostra", "fammi vedere": un consenso, non una domanda. Deve essere un
# messaggio breve, altrimenti "quali immagini ci sono nel catalogo?" verrebbe
# scambiato per un si'.
CONSENSO = re.compile(r"^(si|sì|certo|ok|va bene|volentieri|vedi|vediamo|mostra\w*|"
                      r"fammi vedere|fammele vedere|le voglio vedere|immagini|foto|figure)\b", re.I)


# "Fammi vedere le foto dei diffusori" e' una domanda E una richiesta di
# immagini: si risponde CON le figure, non offrendole. Si cercano i nomi delle
# figure (foto, immagine, figura, illustrazione) e non verbi generici come
# "vedere", che compaiono anche in "vorrei vedere se avete profumatori".
CHIEDE_IMMAGINI = re.compile(r"\b(foto|fotografi\w*|immagin\w*|figur\w*|illustrazion\w*)\b", re.I)


def chiede_le_immagini(domanda: str) -> bool:
    """L'utente ha chiesto lui stesso di vedere le figure."""
    return bool(CHIEDE_IMMAGINI.search(domanda or ""))


def vuole_le_immagini(domanda: str, ultima_risposta: str) -> bool:
    """True se l'utente sta dicendo di si' a un'offerta di immagini appena
    fatta. Servono ENTRAMBE le condizioni: l'offerta nel turno precedente e una
    risposta breve e affermativa."""
    if not (ultima_risposta and MARCA_OFFERTA in ultima_risposta):
        return False
    d = domanda.strip()
    return len(d) <= 60 and bool(CONSENSO.match(d))


def vuole_il_resto(domanda: str, ultima_risposta: str) -> bool:
    """True se l'utente dice di si' all'offerta «vuoi che te le elenchi tutte?».
    Stesso meccanismo di vuole_le_immagini: l'offerta prima, il consenso dopo."""
    if not (ultima_risposta and MARCA_ALTRO in ultima_risposta):
        return False
    d = domanda.strip()
    return len(d) <= 60 and bool(CONSENSO.match(d))


def _ultima_risposta(messages) -> str:
    """L'ultimo messaggio dell'assistente: serve a sapere se l'offerta c'e' stata."""
    for m in reversed(messages):
        if m.get("role") == "assistant" and isinstance(m.get("content"), str):
            return m["content"]
    return ""


def _domanda_precedente(messages) -> str:
    """La domanda prima di questa: e' quella a cui le immagini si riferiscono."""
    trovate = [m["content"].strip() for m in messages
               if m.get("role") == "user" and isinstance(m.get("content"), str)]
    return trovate[-2] if len(trovate) > 1 else ""


# "Quali fragranze ci sono?", "elenca i formati", "che colori avete": la
# risposta sta in DIECI pezzi diversi, uno per variante, non negli otto che
# bastano a una domanda puntuale. Su un catalogo otto pezzi danno un elenco di
# due voci e sembra che il resto non esista (visto il 20/09/2026).
ELENCO = re.compile(r"\b(quali|quante|elenca|elencare|lista|tutt[ei]|che\s+\w+\s+ci\s+sono|"
                    r"che\s+\w+\s+(avete|ci sono|esistono)|assortimento|gamma|catalogo completo)\b", re.I)
PEZZI_ELENCO = 20


def pezzi_da_recuperare(domanda: str) -> int:
    """Quanti pezzi mettere nel contesto: di piu' per le domande di elenco."""
    return PEZZI_ELENCO if ELENCO.search(domanda or "") else 8


def _fonti_citate(righe, base: str = "", utente: str = "") -> str:
    """Documento e pagina di ogni pezzo citato, in coda alla risposta.

    Il modello cita «[7]» e basta: chi legge non ha modo di sapere che [7] e'
    «CATALOGO IPURO 2025.pdf, pagina 8», quindi per verificare deve sfogliare a
    mano — ed e' successo davvero il 20/09/2026, con un utente che ha concluso
    «non c'e'» su un prodotto che stava a pagina 8. Il disclaimer chiede di
    verificare sempre la fonte citata: senza questo elenco non e' possibile.
    I numeri corrispondono all'ordine in cui i pezzi entrano nel CONTESTO
    (prompt.contesto), quindi [n] qui e [n] nella risposta sono lo stesso pezzo.

    Una pagina che compare in piu' pezzi si scrive UNA volta sola, con tutti i
    suoi numeri davanti. Su un catalogo e' la norma — un prodotto occupa testo,
    tabella e descrizione della figura — e l'elenco veniva fuori cosi':
    «[2] pagina 19 · [3] pagina 20 · [4] pagina 20 · [5] pagina 20». Otto voci
    per due pagine: chi legge non ha piu' voglia di verificare niente.
    """
    if not righe:
        return ""
    per_pagina = {}
    for i, r in enumerate(righe, 1):
        per_pagina.setdefault((r["documento"], r.get("page"), r.get("source_id")), []).append(i)
    voci = []
    for (documento, page, source_id), numeri in per_pagina.items():
        pagina = f", pagina {page}" if page is not None else ""
        etichetta = f"{documento}{pagina}"
        # La citazione diventa un COLLEGAMENTO al documento, aperto alla
        # pagina. E' la verifica che il disclaimer chiede: finora si poteva
        # solo sfogliare a mano, e il 20/09/2026 un utente ha concluso «non
        # c'e'» su un prodotto che stava a pagina 8. Ed e' anche la risposta
        # onesta a «questa figura di che prodotto e'?»: il collegamento non
        # afferma niente, mostra la pagina impaginata dal fornitore, dove il
        # codice sta sotto la sua fotina.
        if base and source_id:
            url = documento_mod.firma_url(source_id, documento, page, base, utente)
            etichetta = f"[{etichetta}]({url})"
        voci.append("".join(f"[{n}]" for n in numeri) + f" {etichetta}")
    return "\n\n---\n" + MARCA_FONTI + " · ".join(voci) + "_"


def _figura_in_breve(descrizione) -> str:
    """La descrizione della figura: solo l'OGGETTO, non il testo tecnico
    (Material/Shape/Colours stanno nella pagina, a cui porta il link)."""
    d = descrizione or ""
    if "Object:" in d:
        d = d.split("Object:", 1)[1]
    d = d.split("Material:", 1)[0]
    return " ".join(d.split())[:220]


def _elenco_figure(righe, base, utente, tutte=False) -> str:
    """Le figure trovate, in un elenco strutturato: descrizione + link alla
    pagina. E' la risposta per i PRODOTTI: non un «Fonti» in fondo, ma l'elenco
    stesso — ogni voce dice cos'e' e porta alla pagina che lo mostra.

    Non si elencano tutte (se `tutte` e' falso): se ne mostrano al massimo
    LIMITE_ELENCO, e si offre il resto invece di annegare la risposta."""
    voci, viste = [], set()
    contate = 0
    for r in righe:
        if r.get("descrizione") is None:
            continue
        chiave = (r.get("documento"), r.get("page"))
        if chiave in viste:
            continue
        viste.add(chiave)
        contate += 1
        if not tutte and contate > LIMITE_ELENCO:
            continue
        descr = _figura_in_breve(r.get("descrizione"))
        url = documento_mod.firma_url(r.get("source_id"), r.get("documento"),
                                      r.get("page"), base, utente)
        pag = f", pagina {r['page']}" if r.get("page") is not None else ""
        voci.append(f"- **{descr}** — [{r['documento']}{pag}]({url})")
    resto = contate - len(voci)
    testo = "\n".join(voci)
    if resto > 0 and not tutte:
        testo += f"\n\n_Ci sono altre {resto} voci. Vuoi che te le elenchi tutte?_"
    return testo


LIMITE_ELENCO = 5        # quante figure elencare prima di offrire il resto


PER_RIGA = 4        # quante figure affiancare
# Quanto puo' essere lunga una didascalia: oltre, la riga della tabella va a
# capo e le miniature si disallineano.
DIDASCALIA = 44


def _sotto_la_figura(riga, didascalia: str) -> str:
    """Cosa c'e' scritto sotto una miniatura.

    L'etichetta se l'abbiamo; altrimenti «pagina N», che sappiamo SEMPRE.
    Per 468 figure su 891 un'etichetta nell'ordine di lettura non esiste
    (misurato il 22/09/2026), e senza ripiego quelle resterebbero mute — senza
    didascalia e, quel che conta, senza il collegamento per andare a vedere.

    «pagina 7» non afferma niente su cosa mostri la figura: dice dove
    guardare, ed e' vero per costruzione. E' la differenza fra un sistema che
    tace e uno che si inventa un'etichetta per riempire la casella.
    """
    if didascalia:
        return didascalia
    n = (riga or {}).get("page") if riga else None
    return f"pagina {n}" if n else ""


def _pagina_url(riga, utente: str) -> str:
    """Il collegamento alla pagina da cui viene una figura, o "" se non si sa.

    `riga` e' la riga della ricerca delle immagini (documento, page,
    source_id). Quando la figura arriva dal ripiego per pagina quella riga non
    c'e': niente collegamento, e va bene — meglio nessun collegamento che uno
    che apre la pagina sbagliata.
    """
    if not riga or not riga.get("source_id") or not riga.get("documento"):
        return ""
    return documento_mod.firma_url(riga["source_id"], riga["documento"], riga.get("page"),
                                   f"https://{APP_HOST}", utente)


def _didascalia(descrizione: str) -> str:
    """Cosa scrivere sotto una miniatura.

    La descrizione di una figura e' «<testo del catalogo che PRECEDE la
    figura> — <descrizione del modello visivo>» (vedi prima_dei_segnaposti in
    ingestion). La didascalia usa la PRIMA meta': sono le parole del catalogo,
    con i codici articolo.

    Non dice «questa e' FSA1001», dice cosa c'e' scritto prima. Resta un
    indizio — se il layout mette il codice altrove, la figura resta senza — ma
    dal 22/09/2026 non e' piu' ambiguo: la finestra si ferma al segnaposto
    precedente, quindi porta al massimo il codice di UN prodotto.

    La seconda meta' — il testo del modello visivo, in inglese — non si
    mostra: serve a cercare, non a leggere.
    """
    testo = " ".join((descrizione or "").split())
    # Il separatore c'e' SOLO se l'etichetta c'e': quando prima della figura
    # non c'era testo di catalogo, la descrizione salvata e' la sola frase del
    # modello visivo. Senza questo controllo la didascalia mostrerebbe quella
    # — una frase in inglese sotto una miniatura — spacciandola per l'etichetta
    # del prodotto (22/09/2026: 85 figure su 891).
    if " — " not in testo:
        return ""
    # Etichetta vuota: la descrizione salvata comincia col separatore, e
    # dividerla darebbe il CONTESTO DOPO — cioe' il prodotto successivo,
    # l'ambiguita' che tutto questo serve a togliere. Il 22/09/2026 usciva
    # «— metallic» sotto una miniatura, che non e' l'etichetta di niente.
    if testo.startswith("—"):
        return ""
    testo = testo.split(" — ")[0]
    # Il testo prima della figura e' tagliato a lunghezza fissa e comincia a
    # meta' parola: «nten diamonds & brilliants». Si riparte dalla prima
    # parola intera — una didascalia si legge, non si decifra.
    if " " in testo[:40]:
        testo = testo.split(" ", 1)[1] if not testo[:1].isupper() else testo
    # «nessun testo» e' quello che il modello scrive quando nel ritaglio non
    # c'e' niente da trascrivere: come didascalia non dice nulla.
    if testo.lower().startswith("nessun testo"):
        return ""
    return testo[:DIDASCALIA].rstrip(" ,;-") + ("…" if len(testo) > DIDASCALIA else "")


def _blocco_immagini(url_per_pos) -> str:
    """Le figure, in markdown: miniature cliccabili, quattro per riga.

    In chat serve riconoscere il prodotto, non leggerne le etichette: si manda
    la versione piccola (`mini=1`) e l'originale resta a un clic di distanza.
    Quattro figure di catalogo a piena risoluzione sono quasi 3 MB per
    risposta, e si vedono comunque rimpicciolite.

    Una TABELLA, non immagini di seguito: LibreChat applica `display: block`
    a ogni `img` (preflight di Tailwind, verificato nel CSS compilato), quindi
    metterle sulla stessa riga di markdown non basta — si impilerebbero lo
    stesso. Ogni cella invece e' un riquadro suo, e le celle stanno in fila.
    """
    # La miniatura porta all'immagine grande; la DIDASCALIA porta alla pagina
    # del documento. Da una figura che non convince si arriva in un clic alla
    # pagina impaginata dal fornitore, dove il codice sta sotto la sua fotina:
    # e' la verifica che il sistema non sa fare da solo per meta' delle figure.
    celle = []
    for n, voce in url_per_pos.items():
        u, d = voce[0], voce[1]
        pagina = voce[2] if len(voce) > 2 else ""
        celle.append((f"[![immagine {n}]({u}&mini=1)]({u})",
                      f"[{d}]({pagina})" if d and pagina else d))
    if not celle:
        return ""
    righe = ["|" + "|".join(" " * 2 for _ in range(PER_RIGA)) + "|",
             "|" + "|".join("---" for _ in range(PER_RIGA)) + "|"]
    for i in range(0, len(celle), PER_RIGA):
        gruppo = celle[i:i + PER_RIGA]
        gruppo += [(" ", "")] * (PER_RIGA - len(gruppo))    # celle vuote in coda
        righe.append("| " + " | ".join(c for c, _ in gruppo) + " |")
        # La didascalia sotto la sua miniatura, nella riga seguente della
        # STESSA tabella: cosi' resta incolonnata con l'immagine anche quando
        # il testo va a capo. Niente HTML: LibreChat lo stampa letterale,
        # quindi <sub> compariva nella risposta. Si salta se nessuna delle
        # quattro ne ha una.
        if any(d for _, d in gruppo):
            righe.append("| " + " | ".join(d or " " for _, d in gruppo) + " |")
    return "\n".join(righe)


def _scelte(trovate):
    """QUANTE figure mostrare, non solo quali.

    Due casi, e la differenza la dice il punteggio di RARITA' che la ricerca
    calcola gia' (`rarita`: la somma dei termini rari della domanda che
    compaiono nella descrizione della figura).

    1. Qualcuna contiene cio' che la domanda NOMINA. Allora sono quelle, e
       basta. Il 22/09/2026 «immagine del prodotto GLA3094» dava una figura
       con rarita' 7,3 e tutte le altre a zero: mostrarne quattro voleva dire
       affermare che anche le altre tre c'entravano.

    2. Nessuna, o tutte allo stesso modo — una domanda descrittiva come
       «sassi rossi», dove il criterio esatto non c'e'. Allora si tengono
       quelle vicine alla migliore (QUOTA_PEGGIO) e si smette quando il salto
       e' netto: non e' una verita', e' un campione onesto che si ferma da
       solo invece di riempire.

    In entrambi i casi si passa da `_sparse`, che copre soggetti diversi
    invece di dare quattro quasi-doppioni.
    """
    if not trovate:
        return []
    massimo = max(r.get("rarita") or 0 for r in trovate)
    pari = [r for r in trovate if (r.get("rarita") or 0) >= massimo]
    # La rarita' decide solo se DISTINGUE. Se tutte le candidate hanno lo
    # stesso punteggio non ha separato niente: e' il caso di «sassi rossi»,
    # dove «sass» e' raro nell'archivio (che e' in tedesco) e compare in
    # decine di descrizioni. Senza questo controllo uscivano 48 figure — il
    # contrario di quello che questa funzione deve fare.
    if massimo > 0 and len(pari) < len(trovate):
        return _sparse(pari, min(len(pari), MAX_IMMAGINI))
    # Qui NON si sa quante servano, e il numero giusto non lo sa nessuno: si
    # tengono quelle vicine alla migliore e non piu' di CAMPIONE. Il tetto
    # serve perche' l'incertezza non diventi abbondanza — il 22/09/2026
    # «immagine del prodotto GRA1041», che figura non ha, ne faceva uscire
    # otto di altri prodotti: quando non si sa si mostra MENO, non di piu'.
    vicine = [r for r in trovate
              if (r.get("distanza") or 0) <= (trovate[0].get("distanza") or 0) * (1 + QUOTA_PEGGIO)]
    return _sparse(vicine or trovate[:1], min(len(vicine) or 1, CAMPIONE))


def _sparse(trovate, quante=None):
    """Le figure scelte COPRENDO cose diverse, non le quattro piu' vicine.

    Prendere i primi quattro risultati di una ricerca a somiglianza da'
    quattro quasi-doppioni: il 22/09/2026, a «immagini dei sassi rossi», sono
    uscite due figure di FSA1001 e due di RAD1001 mentre la risposta parlava
    di QUATTRO prodotti. Nessuna era sbagliata, e insieme raccontavano meta'
    della risposta.

    Non e' un difetto di questo catalogo: e' come si comporta una ricerca a
    somiglianza quando le prime posizioni descrivono la stessa cosa. Si fa un
    giro tenendo una figura per «soggetto» — qui la PAGINA, che e' quanto di
    piu' vicino a «di che prodotto parla» si abbia senza inventare — e poi, se
    restano posti, si riempie con le altre nell'ordine di punteggio.

    L'ordine dentro ogni giro resta quello della ricerca: non si riordina
    niente, si sceglie CHI passa.
    """
    quante = quante or MAX_IMMAGINI
    prime, resto, viste = [], [], set()
    for r in trovate:
        chiave = (r.get("documento"), r.get("page"))
        (resto if chiave in viste else prime).append(r)
        viste.add(chiave)
    return (prime + resto)[:quante]


def _immagini_per_la_domanda(conn, qvec, gruppi, righe, domanda=""):
    """Gli id delle figure degli ARTICOLI che hanno risposto, non della domanda.

    D23 (Sviluppo/TOOL-IMMAGINI.md): prima si trovano gli articoli (i chunk gia'
    recuperati), poi le loro figure — match per codice nella descrizione, e in
    mancanza la verifica LLM in una chiamata sola. Prima si cercava la DOMANDA
    per somiglianza vettoriale e, su «profumatori per auto», uscivano figure
    fuori tema pur dentro la pagina giusta.
    """
    trovate = immagini_articoli.per_articoli(conn, righe, gruppi, MAX_IMMAGINI)
    if trovate:
        return [(r["id"], _didascalia(r.get("descrizione")), r) for r in trovate]
    return [(i, "", None) for i in _immagini_del_turno(righe)]


def _immagini_del_turno(righe):
    """Le immagini dei pezzi recuperati, senza doppioni, fino a MAX_IMMAGINI.

    UNA per pezzo, partendo dai meglio piazzati: cosi' le figure vengono dalle
    pagine che hanno risposto alla domanda, invece di arrivare tutte dalla
    stessa. Serve perche' l'immagine e' legata al pezzo solo dalla PAGINA, e in
    un catalogo una pagina contiene dieci prodotti diversi: su "profumatori per
    auto" un pezzo pertinente stava in una pagina con 28 figure, e ne uscivano
    quattro che non c'entravano (20/09/2026).

    Resta un rattoppo: la correzione vera e' sapere COSA mostra ogni immagine
    (colonna `descrizione` su `immagini`) e scegliere le figure che rispondono
    alla domanda, non quelle che stanno vicino al testo che ha risposto."""
    ids = []
    for r in righe:                      # righe: gia' in ordine di punteggio
        for iid in (r.get("immagini") or [])[:1]:
            if iid not in ids:
                ids.append(iid)
    return ids[:MAX_IMMAGINI]


def _stream_litellm(messages, rotta, uso):
    """Streaming DIRETTO da llama-swap, cedendo i chunk OpenAI-compatible.
    `rotta` resta per compatibilita': il modello e' uno solo (locale). `uso` e'
    un dict da riempire con token_in/token_out letti dall'ultimo chunk."""
    for riga in modello.stream(messages, max_tokens=MAX_TOKEN,
                               temperature=TEMPERATURA):
        dato = riga[6:]
        if dato.strip() == "[DONE]":
            continue
        try:
            pezzo = json.loads(dato)
        except ValueError:
            continue
        if pezzo.get("usage"):
            uso["token_in"] = pezzo["usage"].get("prompt_tokens")
            uso["token_out"] = pezzo["usage"].get("completion_tokens")
            continue  # il chunk di usage non porta testo
        if pezzo.get("model"):
            pezzo["model"] = MODEL_NAME
        # Il modello ragiona ad alta voce: il ragionamento viaggia in
        # delta.reasoning_content. LibreChat non lo conosce: lo si scarta,
        # cosi' arriva solo la risposta.
        delta = (pezzo.get("choices") or [{}])[0].get("delta")
        if isinstance(delta, dict):
            delta.pop("reasoning_content", None)
            if "content" not in delta and not delta.get("tool_calls"):
                continue  # solo ragionamento (o chunk vuoto): non si mostra
        yield f"data: {json.dumps(pezzo)}\n\n"


def _registra_traccia(conn, conversation_id, utente, domanda, righe, decisione,
                      token_in, token_out, latenza_ms, riscritta=False, cercata=None):
    # token_in/out arrivano dall'ultimo chunk di LiteLLM (stream_options
    # include_usage); se la rotta non li espone restano NULL (colonna ammessa).
    conn.execute(
        """INSERT INTO traces (conversation_id, utente, domanda, chunk_ids,
                               retrieval_vuoto, taint, modello, token_in, token_out, latenza_ms,
                               riformulazione)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (conversation_id, utente,
         # Nella traccia resta la domanda VERA; se e' stata riscritta per la
         # ricerca si annota anche quella, perche' una risposta strana si
         # spiega guardando cosa e' stato cercato davvero.
         domanda if not riscritta else f"{domanda}\n[cercata: {cercata}]",
         [r["id"] for r in righe] if righe else None,
         not righe,
         decisione.get("fonte_contaminante") if decisione.get("interno") else None,
         decisione.get("rotta"),
         token_in, token_out, latenza_ms, riscritta))
    conn.commit()


@app.post("/v1/chat/completions")
async def chat(request: Request):
    inizio = time.monotonic()
    corpo = await request.json()
    authorization = request.headers.get("authorization")
    conversation_id = request.headers.get("x-conversation-id") or None

    # 1. Identita: nessun percorso alternativo senza token valido.
    try:
        claim = identita.verifica(authorization)
    except identita.TokenNonValido as e:
        return JSONResponse({"error": {"message": str(e), "type": "invalid_request_error"}},
                            status_code=401)
    gruppi = identita.gruppi(claim)
    utente = claim.get("sub") or ""
    # Gruppi dell'ultimo token: servono a decidere, quando il browser chiede
    # un'immagine, se quella persona puo' ancora vederla (un <img> non porta
    # identita', solo i cookie del dominio).
    _ricorda_gruppi(conn_gruppi := _conn(), utente, gruppi)
    conn_gruppi.close()

    domanda = _domanda(corpo.get("messages", []))
    conn = _conn()

    # 1-quater. Il titolo automatico di LibreChat non e' una domanda sui
    # cataloghi: e' una richiesta di un titolo per la conversazione. Passava dal
    # flusso di ricerca (agente, glossario, vincoli, indice) come una domanda
    # vera, intasava il modello e faceva andare in timeout l'estrazione dei
    # vincoli (misurato il 23/09/2026: la via unica smetteva di scattare). Qui
    # si riconosce e si risponde direttamente col modello, senza ricerca: e' la
    # stessa conversazione che il frontend manderebbe a qualunque LLM.
    if _e_titolo_librechat(domanda):
        return _titolo_librechat(domanda)

    # 1-bis. Indicizzazione che ha scaricato il modello: si risponde e basta,
    # senza toccare LiteLLM. Nessuna traccia: non c'e' stato nessun turno.
    if _indice_in_aggiornamento(conn):
        conn.close()
        return _risposta_unica(INDICE_IN_AGGIORNAMENTO)

    # 1-ter. "Mostra le immagini": non e' una domanda nuova, e' il si' a
    # un'offerta. Si rifa' il recupero sulla domanda PRECEDENTE — cosi' le ACL
    # sono quelle di chi sta chiedendo adesso, come in ogni altro turno — e si
    # rispondono le figure, senza disturbare il modello.
    storico_messaggi = corpo.get("messages", [])
    if SU_RICHIESTA and vuole_le_immagini(domanda, _ultima_risposta(storico_messaggi)):
        precedente = _domanda_precedente(storico_messaggi)
        qvec_prec = recupero.embedding(precedente)
        righe, _ = recupero.cerca(conn, precedente, gruppi, qvec=qvec_prec)
        ids = _immagini_per_la_domanda(conn, qvec_prec, gruppi, righe, precedente)
        conn.close()
        if not ids:
            return _risposta_unica("Non ho immagini da mostrare per quella risposta.")
        urls = {i + 1: (immagini.firma_url(iid, f"https://{APP_HOST}", utente),
                        _sotto_la_figura(r, d), _pagina_url(r, utente))
                for i, (iid, d, r) in enumerate(ids)}
        return _risposta_unica("Ecco le figure delle pagine citate:\n\n" + _blocco_immagini(urls))

    # 1-ter-bis. "Si" all'offerta «vuoi che te le elenchi tutte?»: si rifa' la
    # ricerca sulla domanda precedente con piu' figure e si elenca TUTTO, senza
    # il tetto dell'elenco breve.
    if vuole_il_resto(domanda, _ultima_risposta(storico_messaggi)):
        precedente = _domanda_precedente(storico_messaggi)
        righe, _ = agente.cerca(conn, precedente, gruppi,
                                limite=PEZZI_ELENCO,
                                storia=memoria.comprimi(storico_messaggi))
        elenco = _elenco_figure(righe, f"https://{APP_HOST}", utente, tutte=True)
        conn.close()
        if elenco:
            return _risposta_unica("Ecco tutte le voci:\n\n" + elenco)
        return _risposta_unica("Non ho trovato altre voci.")

    # L'agente vero: capisce la domanda e decide da se' — cerca le figure per i
    # prodotti, legge il testo per i documenti, risponde ai saluti senza
    # cercare. Sostituisce la catena fissa (riformula -> vincoli -> glossario ->
    # ricerca -> gate -> prompt).
    righe, risposta = agente.cerca(conn, domanda, gruppi,
                                   limite=pezzi_da_recuperare(domanda),
                                   storia=memoria.comprimi(storico_messaggi))

    # La RISPOSTA del modello (articolata), con i link alle pagine come Fonti.
    # Prima si rispondeva coi PRODOTTI sempre con l'elenco grezzo delle figure
    # perche' il modello inventava la pagina; col guardrail + Qwen3-14B la
    # risposta e' affidabile. L'elenco grezzo resta come ripiego.
    if risposta:
        # Per i CATALOGHI (figure) la risposta si chiude con l'elenco dei
        # prodotti + link alla pagina; per i documenti basta la citazione
        # nelle fonti.
        elenco = _elenco_figure(righe, f"https://{APP_HOST}", utente)
        coda = ("\n\n" + elenco) if elenco else _fonti_citate(righe, f"https://{APP_HOST}", utente)
        def gen():
            try:
                yield f"data: {json.dumps({'choices': [{'delta': {'role': 'assistant', 'content': risposta}, 'index': 0}], 'model': MODEL_NAME})}\n\n"
                if coda:
                    yield f"data: {json.dumps({'choices': [{'delta': {'role': 'assistant', 'content': coda}, 'index': 0}]})}\n\n"
                yield "data: [DONE]\n\n"
            finally:
                _registra_traccia(conn, conversation_id, utente, domanda, righe,
                                  {"rotta": "agente"}, None, None,
                                  int((time.monotonic() - inizio) * 1000))
                conn.close()
        return StreamingResponse(gen(), media_type="text/event-stream")

    elenco = _elenco_figure(righe, f"https://{APP_HOST}", utente)
    if elenco:
        testo = "Ecco cosa ho trovato:\n\n" + elenco
        def gen():
            try:
                yield f"data: {json.dumps({'choices': [{'delta': {'role': 'assistant', 'content': testo}, 'index': 0}], 'model': MODEL_NAME})}\n\n"
                yield "data: [DONE]\n\n"
            finally:
                _registra_traccia(conn, conversation_id, utente, domanda, righe,
                                  {"rotta": "agente"}, None, None,
                                  int((time.monotonic() - inizio) * 1000))
                conn.close()
        return StreamingResponse(gen(), media_type="text/event-stream")

    # L'agente ha raccolto i pezzi ma non ha prodotto una risposta: la si
    # costruisce qui, come faceva la catena — contesto + modello.
    messaggi = [{"role": "system",
                 "content": prompt.SYSTEM + "\n" + prompt.contesto(righe) + SUFFISSO_SISTEMA}]
    storico = [_senza_aggiunte(m) for m in corpo.get("messages", [])
               if isinstance(m.get("content"), str) and m.get("role") in ("user", "assistant")]
    messaggi += storico

    def gen():
        uso = {}
        try:
            for pezzo in _stream_litellm(messaggi, "ragionamento", uso):
                yield pezzo
            finale = _fonti_citate(righe, f"https://{APP_HOST}", utente)
            if finale:
                yield f"data: {json.dumps({'choices': [{'delta': {'role': 'assistant', 'content': finale}, 'index': 0}]})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'upstream_error'}})}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            _registra_traccia(conn, conversation_id, utente, domanda, righe,
                              {"rotta": "ragionamento"}, uso.get("token_in"),
                              uso.get("token_out"),
                              int((time.monotonic() - inizio) * 1000))
            conn.close()

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/documenti")
def servizio_documento(request: Request, f: str = "", d: str = "", scade: str = "",
                       firma: str = "", u: str = ""):
    """Il documento originale, per verificare una citazione.

    Stesse due condizioni delle immagini, per la stessa ragione: firma valida
    (il gate aveva approvato) E permesso ancora valido adesso (la fonte non e'
    stata sospesa, la persona non e' uscita dal gruppo). La pagina sta dopo il
    cancelletto e non arriva qui: non e' un permesso, e' dove guardare.

    Si serve con FileResponse, che risponde alle richieste Range: il
    visualizzatore del browser scarica la pagina che apri, non i 62 MB del
    catalogo.
    """
    if not documento_mod.valida(f, d, scade, firma, u):
        return JSONResponse({"error": "url non valido o scaduto"}, status_code=403)
    conn = _conn()
    try:
        if not documento_mod.visibile(conn, f, _gruppi_della_richiesta(request, conn, u)):
            return JSONResponse({"error": "non consentito"}, status_code=403)
        p = documento_mod.percorso(conn, f, d)
    finally:
        conn.close()
    if not p:
        return JSONResponse({"error": "documento non trovato"}, status_code=404)
    from fastapi.responses import FileResponse
    # `inline`: si apre nel visualizzatore invece di scaricarsi. Il nome del
    # file lo conosce gia' chi legge — sta nella citazione.
    return FileResponse(p, media_type=documento_mod.tipo(p),
                        headers={"Content-Disposition": f'inline; filename="{p.name}"',
                                 "Cache-Control": "private, max-age=300"})


@app.get("/immagini/{img_id}")
def servizio_immagine(request: Request, img_id: int, scade: str = "", firma: str = "", u: str = "",
                      mini: int = 0):
    """Serve un'immagine a DUE condizioni: URL firmato valido, e permesso
    ancora valido per la persona a cui e' stato consegnato.

    La firma da sola direbbe soltanto «il gate aveva approvato», e varrebbe
    fino alla scadenza anche dopo che la fonte e' stata sospesa o la persona e'
    uscita dal gruppo. Il secondo controllo rilegge `sources` adesso: lo stato
    della fonte ha effetto immediato, un cambio di gruppi al primo messaggio
    successivo di quella persona (migrazione 015)."""
    if not immagini.valida(img_id, scade, firma, u):
        return JSONResponse({"error": "url non valido o scaduto"}, status_code=403)
    conn = _conn()
    try:
        if not immagini.visibile(conn, img_id, _gruppi_della_richiesta(request, conn, u)):
            return JSONResponse({"error": "non consentito"}, status_code=403)
        dati = immagini.leggi(conn, img_id)
    finally:
        conn.close()
    if not dati:
        return JSONResponse({"error": "immagine non trovata"}, status_code=404)
    b, tipo = dati
    # `mini` non entra nella firma: non e' un permesso, e' solo la taglia.
    if mini:
        b, tipo = immagini.miniatura(b)
    return Response(content=b, media_type=tipo,
                    headers={"Cache-Control": "private, max-age=300"})


@app.get("/health")
def health():
    return {"ok": True}
