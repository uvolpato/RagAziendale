"""Ricerca multiagentica (D23): uno STATO e un coordinatore, non una catena.

NON sostituisce niente: si accende con GRAFO=1 e convive con il flusso di oggi
(`agente.py`), che resta identico a flag spento.

    ┌──────────────────────────────────────────────┐
    │  STATO: la domanda, cosa ho capito, cosa ho  │
    │  cercato e con che esito, cosa e' confermato │
    └───────────────────┬──────────────────────────┘
                        ▼
                 COORDINATORE  ── guarda lo stato INTERO e sceglie la mossa
                        │
        ┌───────────────┼───────────────┬──────────────┐
        ▼               ▼               ▼              ▼
     cerca()×N      verifica()       guarda()      rispondi()
     (in parallelo,  il CRITICO      l'OSSERVATORE   → REDATTORE
      sono ricerche  legge riga       apre la foto
      indipendenti)  per riga         e la manda al VLM
        └───────────────┴───────────────┘
                        │
                        └──▶ torna al coordinatore con lo stato aggiornato

Perche' cosi' e non una catena istradatore→cercatore→critico→redattore.

Una catena mette una decisione in ogni giuntura, e quelle decisioni finiscono
in codice: «dopo il critico, se c'e' un forse vai dall'osservatore, se non c'e'
nessun si torna a cercare, altrimenti scrivi». Sono tre `if` che decidono la
strategia della ricerca — cioe' esattamente il lavoro che volevamo dare al
modello. Chi risponde in questa conversazione non ha una catena: ha davanti
tutto quello che sa, e a ogni passo sceglie la mossa successiva. Qui e' lo
stesso: un solo punto in cui si decide, e la decisione e' un prompt.

Resta UN bivio in codice, ed e' la lettura di una scelta gia' presa: il
coordinatore ha chiamato `rispondi`? Allora si scrive. Non e' il programma che
decide, e' il programma che ubbidisce.

Le mosse di uno stesso giro sono INDIPENDENTI e partono insieme: cercare nelle
foto e cercare nel testo non hanno niente da dirsi, e metterle in fila e' una
scelta che nessuno ha chiesto. Il modello decide quante strade aprire, il
codice le apre tutte nello stesso momento.

Cosa NON e' una decisione del modello, e non lo sara' mai:
- i permessi (ACL in `operatori`/`sql_agente`): non passano da nessun prompt;
- il collegamento «[[3]] -> pagina 27 del catalogo X»: e' una riga del
  database che si legge, non un riferimento da interpretare. Quando lo si
  interpretava, 4 citazioni su 5 finivano sul catalogo sbagliato.

I prompt si cambiano senza toccare il codice: ogni agente legge
GRAFO_PROMPT_<NOME> dall'ambiente.
"""
import base64
import concurrent.futures as futures
import json
import operator
import os
import re
import time
from typing import Annotated, Any, TypedDict

import psycopg
from langgraph.graph import END, START, StateGraph
from psycopg.rows import dict_row

from orchestratore import (documento as documento_mod, egress, identita,
                           immagini as immagini_mod, mappa, modello, operatori)

# Quanto puo' essere lungo il RAGIONAMENTO di una decisione.
#
# Non e' un numero a piacere: deve stare dentro il timeout della chiamata,
# altrimenti e' un fallimento garantito travestito da tetto. Il conto, con i
# numeri veri dell'1/10/2026: il modello fa ~36 token al secondo e la
# chiamata scade a 120 secondi (`VINCOLI_TIMEOUT`), quindi oltre ~4.300
# token non si arriva. Avevo copiato 8192 da `agente.py` senza fare questo
# conto: 8192 / 36 = 228 secondi, il doppio del tempo concesso. Su «mi
# proponi qualcosa per il compleanno di mia mamma?» il modello ha divagato,
# ha sforato, e l'utente ha letto «non sono riuscito a rispondere» dopo due
# minuti e mezzo.
#
# 3.500 token sono ~97 secondi: il ragionamento tipico ne usa 560 (misurato),
# quindi il caso normale non lo tocca e quello che divaga viene troncato
# invece di far fallire il turno.
PENSIERO_MAX = int(os.environ.get("GRAFO_PENSIERO_MAX", "3500"))
GUIDA_CAMPIONE = int(os.environ.get("GRAFO_GUIDA_CAMPIONE", "25"))  # righe alla guida
MAX_PASSI = int(os.environ.get("GRAFO_MAX_PASSI", "5"))      # giri del coordinatore
MAX_RIGHE = int(os.environ.get("GRAFO_MAX_RIGHE", "60"))     # righe tenute in stato
ASSAGGIO = int(os.environ.get("GRAFO_ASSAGGIO", "300"))      # caratteri per riga
PARALLELE = int(os.environ.get("GRAFO_PARALLELE", "4"))      # mosse insieme
# L'osservatore e' SPENTO di default, e non per prudenza: in llama-swap.yaml
# `qwen3-14b` sta nel gruppo «confronto» (exclusive) e il VLM nel gruppo
# «grandi», quindi caricare il VLM SCARICA il modello di chat e ogni verifica
# costa due swap. Prima di accenderlo va spostato il 14b nel gruppo «grandi» e
# misurata la VRAM (8,4 GB di pesi piu' KV, 3,1 del VLM, 0,8 dei piccoli, su
# 16,3 disponibili: ci sta stretto, quindi si misura).
OSSERVATORE = os.environ.get("GRAFO_OSSERVATORE", "") == "1"
MAX_FOTO = int(os.environ.get("GRAFO_MAX_FOTO", "4"))
MODELLO_VLM = os.environ.get("MODELLO_VLM", "qwen/qwen3-vl-4b")
LATO_VLM = int(os.environ.get("GRAFO_LATO_VLM", "768"))


def _prompt(nome: str, predefinito: str) -> str:
    return os.environ.get(f"GRAFO_PROMPT_{nome.upper()}") or predefinito


def _decide(messaggi, strumenti, tentativi: int = 2, ragiona: bool = False,
            su_pensiero=None):
    """Fa DECIDERE al modello, facendogli chiamare uno strumento.

    Serve a togliere il ripiego dal codice. Leggere una decisione con una
    regex ha sempre un ramo «e se non l'ha scritta cosi'?», e quel ramo e' una
    scelta del programma al posto del modello — la peggiore possibile, perche'
    scatta proprio quando il modello non e' stato chiaro. Qui la decisione ha
    una FORMA: o la prende lui, o gliela si richiede.

    Torna la lista delle chiamate [(nome, argomenti)], vuota se dopo
    `tentativi` non ha deciso: quello e' un guasto, non una scelta.

    Con `su_pensiero` il RAGIONAMENTO del modello esce man mano, invece di
    essere buttato. LibreChat sa renderlo come blocco pieghevole sopra la
    risposta: chi aspetta quaranta secondi vede cosa sta succedendo, invece
    di una schermata ferma. E' il pensiero vero, non un riassunto scritto da
    noi.
    """
    nomi = {s["function"]["name"] for s in strumenti}
    messaggi = list(messaggi)
    for _ in range(tentativi):
        m = modello.messaggio(messaggi, max_tokens=PENSIERO_MAX if ragiona else 2048,
                              tools=strumenti, tool_choice="required",
                              ragiona=ragiona)
        pensiero = m.pop("reasoning_content", None)
        if pensiero and callable(su_pensiero):
            su_pensiero(str(pensiero))
        scelte = []
        for tc in m.get("tool_calls") or []:
            nome = tc.get("function", {}).get("name", "")
            if nome not in nomi:
                continue
            try:
                scelte.append((nome, json.loads(
                    tc.get("function", {}).get("arguments") or "{}")))
            except (TypeError, ValueError):
                continue
        if scelte:
            return scelte
        messaggi += [m, {"role": "user", "content":
                         "Non hai deciso niente. Rispondi chiamando uno di "
                         "questi strumenti: " + ", ".join(sorted(nomi)) + "."}]
    return []


# ==========================================================================
# L'ANALISTA — valuta la DOMANDA, prima che si cerchi
# ==========================================================================
# Non instrada e non decide: gira una volta sola, all'inizio, e scrive nello
# stato due cose che il coordinatore da solo non vede.
#
# Il coordinatore guarda i RISULTATI, e «ho trovato 16 righe verificate» gli
# legge sempre come successo — anche quando la domanda era «mi serve il
# rosso» e le 16 righe sono piatti, dischi e mollette per il bucato, tenuti
# insieme solo dal colore (misurato il 30/09/2026: `chiedi` non scattava mai).
# Chi guarda i risultati non si accorge che la DOMANDA era mal posta: bisogna
# guardare la domanda, e guardarla prima.
#
# Lo stesso sguardo serve all'altra meta' del problema: capire di cosa si sta
# parlando dice anche DOVE cercarlo.
P_ANALISTA = """Leggi una domanda fatta a un archivio aziendale e la giudichi. Non cerchi niente e non rispondi: dici che domanda e'.

Nell'archivio ci sono:
- le FOTO dei cataloghi (tabella `immagini`), una riga per foto, con la descrizione di cosa si vede: oggetto, materiale, forma, colori. Qui stanno i PRODOTTI.
- il TESTO dei documenti (tabella `chunks`), nelle lingue originali: nomi commerciali, codici articolo, prezzi, procedure, manuali, policy.
- l'elenco delle fonti (tabella `documenti`): quanti documenti ci sono, di che tipo, quante pagine e quante foto.

Devi dire due cose.

**COSA CHIEDE ADESSO**: scrivi per esteso quello che la persona vuole in questo momento, come lo spiegheresti a un collega che non ha letto la conversazione — e aggiungi dove sta la risposta. Una o due frasi, in italiano.

Questa e' la parte che conta, ed e' facile sbagliarla in un modo preciso: rispondere con una CATEGORIA invece che con la richiesta. «Un prodotto con due attributi, sta nelle foto» non e' una risposta: e' un'etichetta, vale per mille domande diverse e non dice cosa vuole questa persona. Chi legge dopo di te deve poter scrivere la ricerca avendo solo la tua frase.

Sulle domande di SEGUITO sta tutta la difficolta', perche' l'ultima riga non si regge da sola e non va presa alla lettera. Esempio vero: la persona ha chiesto «hai palline di Natale rosse o gialle?», le sono state mostrate solo delle rosse, e adesso scrive «niente gialle?».
- SBAGLIATO: «Un prodotto con due attributi, sta nelle foto.» (categoria, non dice niente)
- SBAGLIATO: «Chiede kit per albero di Natale con palline rosse o gialle.» (e' la domanda di PRIMA, hai perso il seguito)
- SBAGLIATO: «Vuole escludere le palline gialle.» (le vuole, non le sta rifiutando)
- GIUSTO: «Ha gia' visto delle palline di Natale rosse e ora chiede se ce ne sono di GIALLE. Cerca palline di Natale gialle, nelle foto dei cataloghi.»

Guarda sempre cosa e' stato gia' mostrato: un seguito di solito chiede la parte che manca, o restringe, o chiede il dettaglio di una cosa dell'elenco.

**COSA MANCA**: quello che la domanda NON dice e che cambierebbe la risposta. E' la parte difficile, e si sbaglia in tre modi:
- dire che manca qualcosa quando non manca. «Nastri bianchi con cuori rossi» e' completa: c'e' l'oggetto e ci sono gli attributi. Non manca la larghezza, non manca il prezzo: se li volesse li avrebbe chiesti.
- non accorgersi che manca l'essenziale. «Mi serve il rosso» non dice l'OGGETTO: cosi' com'e' seleziona qualunque cosa rossa dell'archivio, e chi legge si ritrova piatti e mollette insieme. «Qualcosa per San Valentino» non dice che TIPO di cosa ne' per chi.
- scambiare quello che manca alla DOMANDA con quello che manca alla RISPOSTA. Qui ci sta la trappola: su «quanto costano?» hai risposto «manca il prezzo» (vero il 2/10/2026). Il prezzo e' quello che la persona VUOLE SAPERE, non un'informazione che lei avrebbe dovuto darti. Chiedere «qual e' il prezzo?» a chi ti ha appena chiesto il prezzo e' la domanda piu' inutile che esista. La prova per distinguerli: potrebbe rispondertelo LEI? Se la risposta e' no, quella cosa non manca alla domanda — e questo campo resta vuoto.

Quando il campo e' vuoto, lascialo vuoto. Una lista vuota e' un'informazione («questa domanda e' completa»), riempirla per forza fa fare a chi viene dopo di te una domanda che non serve a nessuno.

Il criterio e' uno solo: se cercassi cosi' com'e', quello che torna sarebbe UTILE a chi ha chiesto, o sarebbe un mucchio? Se e' un mucchio, quello che lo rende un mucchio e' la cosa che manca.

Non elencare piu' di due cose mancanti, e scrivile come le diresti a voce.

**QUANTO E' IMPEGNATIVA.** Ultima cosa, e serve a chi cerca: dopo di te qualcuno deve decidere dove e come cercare, e puo' farlo di getto oppure fermandosi a ragionare. Ragionare rende molto meglio quando serve e costa il triplo del tempo quando non serve, quindi dillo tu.

NON impegnativa — la strada e' una sola e si vede:
- un saluto, un ringraziamento, una chiacchiera;
- un dato secco sull'archivio: quanti documenti, quante pagine, che cosa c'e';
- un oggetto con i suoi attributi, detto in modo chiaro: «nastri bianchi con cuori rossi», «vasi di ceramica». Si sa cosa cercare e dove.

IMPEGNATIVA — serve pensare:
- la cosa chiesta ha un nome che il catalogo quasi certamente scrive in un altro modo, e va indovinato per tentativi («kit per l'albero di Natale», «sfere che trattengono l'acqua»);
- la risposta sta in due posti e va messa insieme (il prodotto nelle foto, il prezzo nel testo);
- la domanda e' aperta e bisogna arrivarci per gradi («un regalo per una trentenne», «cosa avete di sportivo»);
- e' un seguito che si capisce solo dal discorso di prima;
- e' una richiesta che la prima ricerca ovvia quasi certamente manchera'.

Nel dubbio di' che e' impegnativa: una risposta lenta e giusta e' meglio di una veloce e sbagliata.

**SI PUO' RISPONDERE COSI' COM'E'?** Una domanda si puo' avere capita benissimo e restare comunque senza risposta utile, perche' non dice abbastanza. Il criterio e' quello di prima, applicato fino in fondo: *se cercassi cosi' com'e', quello che torna sarebbe utile a questa persona, o sarebbe un mucchio di cose scollegate?*

Di' NO quando cercare adesso produrrebbe un elenco tenuto insieme da una sola caratteristica e da niente altro: «mi serve qualcosa di blu» restituisce un piatto, una molletta e un mappamondo preso da una guida doganale — tutti blu, nessuno utile. In quel caso la cosa che serve e' una domanda alla persona, non una ricerca.

Di' SI in tutti gli altri casi, e sono la maggioranza. Un saluto e' SI (non c'e' niente da cercare, si risponde e basta). Una domanda con l'oggetto e i suoi attributi e' SI. Una domanda aperta ma con un'occasione o uno scopo («un regalo per una trentenne», «qualcosa per San Valentino») e' SI: c'e' abbastanza per cominciare. Un dato sull'archivio e' SI.

Non e' una scusa per farsi dire tutto prima di muoversi: nel dubbio, SI.

Consegna con lo strumento `analisi`."""

I_ANALISI = [{"type": "function", "function": {
    "name": "analisi",
    "description": "Il tuo giudizio sulla domanda.",
    "parameters": {"type": "object", "properties": {
        "ambito": {"type": "string",
                   "description": "Cosa vuole la persona ADESSO, per esteso, e "
                                  "dove sta la risposta. Non una categoria: la "
                                  "richiesta, scritta come la spiegheresti a "
                                  "un collega che non ha letto la chat."},
        "rispondibile": {"type": "boolean",
                         "description": "false solo se cercare cosi' com'e' "
                                        "darebbe un mucchio di cose scollegate "
                                        "e serve prima una domanda alla persona."},
        "impegnativa": {"type": "boolean",
                        "description": "true se chi cerca deve fermarsi a "
                                       "ragionare, false se la strada e' una "
                                       "sola e si vede."},
        "manca": {"type": "array", "items": {"type": "string"},
                  "description": "Cosa la domanda non dice e cambierebbe la "
                                 "risposta. Vuoto se la domanda e' completa."}},
        "required": ["ambito", "manca", "impegnativa", "rispondibile"]}}}]


def _nodo_analista(stato):
    t0 = time.monotonic()
    messaggi = [{"role": "system", "content": _prompt("analista", P_ANALISTA)}]
    storia = stato.get("storia") or [{"role": "user", "content": stato["domanda"]}]
    messaggi += storia
    # Se questa e' una battuta in mezzo a una conversazione, glielo si DICE.
    # Non e' un giudizio: e' un fatto che il codice conosce con certezza e
    # che altrimenti lui deve dedurre — e lo deduce male. Su «quanto
    # costano?» e «per un matrimonio» leggeva l'ultima riga come se stesse in
    # piedi da sola: cosi' sembrava semplice, e sia l'ambito sia la
    # difficolta' venivano sbagliati (1/10/2026, due volte su due).
    # Richiamato dopo che i dati hanno smentito la previsione: adesso ha una
    # prova che prima non aveva, ed e' il motivo per cui lo si ridisturba.
    if stato.get("riviste"):
        messaggi.append({"role": "user", "content":
                         "Avevi gia' giudicato questa domanda, e chi cerca ci "
                         "ha provato: " + "; ".join(stato.get("esiti") or ["niente"])
                         + ".\nQuello che e' tornato smentisce la previsione di "
                           "prima. Rigiudica con questa prova davanti: forse la "
                           "domanda e' piu' impegnativa di quanto sembrava, o "
                           "forse quello che chiede non si trova cosi'."})
    turni = sum(1 for m in storia if m.get("role") == "user")
    if turni > 1:
        messaggi.append({"role": "user", "content":
                         "Nota: «%s» e' la %da cosa che ti scrive in questa "
                         "conversazione, non una domanda isolata. Da sola non "
                         "si capisce: quello che vuole sta nel discorso di "
                         "prima e in quello che le e' gia' stato mostrato."
                         % (stato["domanda"], turni)})
    # Nel dubbio si ragiona: una risposta lenta e giusta vale piu' di una
    # veloce e sbagliata, e questo e' il valore che vale anche quando
    # l'analista non decide affatto.
    ambito, manca, impegnativa, rispondibile = "", [], True, True
    for nome, arg in _decide(messaggi, I_ANALISI):
        ambito = str(arg.get("ambito") or "").strip()
        manca = [str(m).strip() for m in (arg.get("manca") or []) if str(m).strip()][:2]
        impegnativa = bool(arg.get("impegnativa", True))
        rispondibile = bool(arg.get("rispondibile", True))
    return {"ambito": ambito, "manca": manca, "impegnativa": impegnativa,
            "rispondibile": rispondibile,
            "riviste": stato.get("riviste", 0) + 1,
            "traccia": [_nota("analista", {"manca": manca,
                                           "impegnativa": impegnativa,
                                           "rispondibile": rispondibile}, 0, t0)]}


# ==========================================================================
# IL COORDINATORE — l'unico che decide
# ==========================================================================
P_COORDINATORE = """Sei tu a condurre la ricerca in un archivio aziendale di cataloghi e documenti. Non scrivi la risposta per la persona: decidi la prossima mossa, e la fai fare a chi sa farla.

Hai davanti la conversazione intera e lo stato di quello che hai gia' fatto. A ogni giro guardi tutto e scegli, come faresti tu davanti a un archivio che non conosci: cerchi, guardi cosa e' venuto fuori, e decidi se ti basta.

**La conversazione la leggi tutta, e la query la scrivi su quella, non sull'ultima riga.** Quasi mai l'ultima riga si regge da sola, e non va presa alla lettera: va capita. Prima di scrivere la query, di' a te stesso in una frase cosa sta chiedendo la persona ADESSO, per esteso, come se te lo spiegasse un collega.

Un esempio vero, e tutti e due i modi di sbagliarlo. Dopo «hai palline di Natale rosse o gialle?» le hai mostrato solo delle rosse, e lei scrive «niente gialle?». Vuol dire: *di palline di Natale gialle non ne hai trovate?* Cerca quelle.
- Cercare `~* 'yellow'` e basta e' sbagliato: hai buttato via l'oggetto, e ti riempi la pool di qualunque cosa gialla — una ghirlanda verde con riflessi, un quadro preso da una guida doganale.
- Escludere il giallo (`!~* 'yellow'`) e' sbagliato all'opposto: lei le gialle le VUOLE, non le sta rifiutando.
Sono successe tutte e due. La differenza non la fa una regola su cosa sia un «seguito»: la fa leggere il discorso e capire cosa vuole quella persona.

Dove si cerca:
- le FOTO dei cataloghi (tabella `immagini`): una riga per foto, con la descrizione di cosa si vede — oggetto, materiale, forma, colori. Qui sta il PRODOTTO.
- il TESTO dei documenti (tabella `chunks`), nelle lingue originali: nomi commerciali, codici articolo, prezzi, procedure, manuali, policy.
- l'elenco delle fonti (tabella `documenti`), se la domanda e' sull'archivio stesso.

Le tue mosse:
- `cerca` esegue una query che scrivi tu. PUOI CHIAMARLA PIU' VOLTE NELLO STESSO GIRO, e partono tutte insieme: se la domanda ha due strade indipendenti — le foto e il testo, due modi di chiamare la stessa cosa — aprile entrambe subito. Una ricerca costa 50 millisecondi, un giro in piu' ne costa quattordicimila.
- `verifica` passa le righe trovate a chi le legge una per una e dice se rispondono davvero. Usalo quando hai un mucchio di righe e non tutte c'entrano: e' il caso normale.
- `guarda` riapre la FOTO vera e la fa guardare, per le righe su cui la descrizione non basta a decidere.
- `rispondi` chiude e manda a chi scrive **tutte le righe che hai raccolto, con l'etichetta che hanno in questo momento**. Quelle che nessuno ha verificato arrivano marcate «NON VERIFICATA», e chi legge se lo vedra' scritto in faccia.

**Non si consegna roba che non ha guardato nessuno.** Trovare e controllare sono due lavori, e `cerca` fa solo il primo: una query che torna venti righe ti dice che venti righe contengono quelle parole, non che rispondono alla domanda. Chi le legge una per una e' `verifica`.

Quindi, prima di `rispondi`, guarda lo stato: **ci sono righe senza verdetto?** Se si', la mossa e' `verifica`, non `rispondi`. Non e' un consiglio.

Cosa succede se rispondi lo stesso: quelle righe arrivano a chi scrive marcate «NON VERIFICATA», e la risposta si apre dicendo alla persona che nessuno le ha controllate. Non e' una risposta, e' un elenco grezzo con una scusa davanti — e la persona se ne accorge.

L'unico caso in cui ha senso saltare la verifica e' quando non c'e' niente da verificare: un saluto, oppure un dato secco che la query ha gia' dato per intero (un conteggio, un elenco di documenti). Se hai in mano righe di prodotti o di testo, quelle vanno controllate. Sempre.
- `proponi` costruisce il VENTAGLIO delle strade leggendole nell'archivio, per le domande che si rispondono con una scelta e non con un risultato. **Si chiama UNA VOLTA SOLA.** Il ventaglio resta nello stato: appena ce l'hai, la mossa dopo e' `chiedi`, che lo mostra alla persona. Richiamare `proponi` sullo stesso stato rida' lo stesso ventaglio e costa quattordici secondi per niente; e mettersi a `cerca` dopo averlo costruito e' lo stesso errore — stavi per chiedere, le righe che trovi adesso non le guardera' nessuno.
- `chiedi` si ferma e FA UNA DOMANDA alla persona. E' una mossa come le altre, non una resa.

**Se ti dicono che la domanda NON e' rispondibile cosi' com'e', non cercare.** Chi ha letto la domanda ha gia' stabilito che una ricerca produrrebbe un mucchio di cose tenute insieme da una caratteristica sola — «qualcosa di blu» tira su un piatto, una molletta e un mappamondo. Cercare lo stesso vuol dire spendere due minuti per arrivare a chiedere comunque, con in mano un elenco che non serve a nessuno. In quel caso le mosse sono due: `proponi` per vedere fra cosa si puo' scegliere, poi `chiedi`. Niente `cerca`.

Sul chiedere. Una ricerca serve a qualcuno, e a volte la cosa piu' utile che puoi fare e' una domanda invece di un'altra query. Ma una domanda buona e una cattiva si distinguono:
- CATTIVA: chiedere prima di aver guardato («che tipo di nastri?»). Cerca, poi chiedi: la persona non deve fare il lavoro al posto tuo.
- BUONA: chiedere quando hai visto i dati e la scelta e' davvero sua. «Ho trovato nastri bianchi con cuori rossi in tre larghezze e due materiali: ti interessa un formato in particolare?» «Cerchi il prodotto a catalogo o il suo prezzo di listino?»
- BUONA: chiedere quando la domanda si puo' leggere in due modi e i due modi portano in posti diversi dell'archivio.
- CATTIVA: chiedere quando puoi semplicemente mostrare tutto quello che hai trovato. Se le opzioni sono poche, falle vedere: e' piu' utile di una domanda.
Chiedi al massimo una cosa per volta, concreta, con davanti quello che hai gia' trovato.

Come si conduce:
- Una domanda di prodotto («nastri bianchi con cuori rossi») finisce quando hai le pagine in cui il prodotto ha DAVVERO tutto quello che e' stato chiesto, non una parte.
- Una domanda aperta («un regalo per una trentenne») non ha una parola da cercare: guarda prima cosa contiene l'archivio, poi arriva a prodotti concreti.
- Una domanda su un documento («cosa dice la procedura sui resi») sta nel testo, e finisce quando hai le pagine da riassumere.
- Una domanda sull'archivio («quanti cataloghi ci sono») e' finita appena hai il dato.
- Se e' un saluto o una chiacchiera, chiama subito `rispondi`: non c'e' niente da cercare.
Se la domanda non somiglia a nessuno di questi casi, trattala per quello che e'.

Quando ti fermi: appena hai righe confermate che rispondono, `rispondi`. Non inseguire varianti. E se hai cercato in piu' modi e non c'e' niente, `rispondi` lo stesso: una risposta onesta vale piu' di un'altra ricerca."""


def _strumenti_del_coordinatore(ultima: bool = False,
                                mai_cercato: bool = False,
                                mai_eseguito: bool = False,
                                non_rispondibile: bool = False,
                                ventaglio_pronto: bool = False):
    """Le mosse disponibili ADESSO.

    `guarda` compare solo se l'osservatore e' acceso, e all'ultimo giro
    compaiono solo le mosse che chiudono. Non e' decidere al posto del
    modello: e' non offrirgli una porta che il tetto ha gia' chiuso. Prima il
    tetto stava in `_prossimo`, che scartava in silenzio la mossa appena
    scelta — il modello sceglieva `cerca`, il codice buttava via la scelta e
    andava a scrivere. Un giro sprecato, e una decisione del modello
    cancellata senza dirglielo."""
    chiusura = [
        {"type": "function", "function": {
            "name": "rispondi",
            "description": "Basta cercare: passa a chi scrive la risposta.",
            "parameters": {"type": "object", "properties": {
                "motivo": {"type": "string", "description": "Perche' ti fermi."}},
                "required": []}}},
        {"type": "function", "function": {
            "name": "chiedi",
            "description": ("Fermati e fai una domanda alla persona. Solo dopo "
                            "aver guardato i dati, e solo se la sua risposta "
                            "cambia dove cercare o cosa mostrarle."),
            "parameters": {"type": "object", "properties": {
                "domanda": {"type": "string",
                            "description": "La domanda, una sola, concreta."},
                "motivo": {"type": "string",
                           "description": "Cosa cambia in base alla sua risposta."}},
                "required": ["domanda"]}}},
    ]
    # Non si dice «non ho trovato» senza aver MAI interrogato l'archivio.
    #
    # Qui c'erano due cancelli scritti in codice, e li abbiamo tolti tutti e
    # due per misurare se servissero davvero o fossero impalcatura messa
    # quando il coordinatore era cieco (le spiegazioni dei rifiuti finivano
    # in `esiti`, che nessuno leggeva). La misura dell'1/10/2026 ha dato due
    # risposte diverse:
    #
    # - `chiedi` chiuso a chi non ha ancora cercato: NON serviva. Tolto.
    # - questo: serve. Senza, su «palloni da calcio giganti gonfiabili» il
    #   sistema ha risposto «non ho trovato informazioni su questo
    #   argomento» con due query respinte e ZERO eseguite. La frase nello
    #   stato («qualunque cosa tu dica sarebbe inventata») non e' bastata.
    #
    # E non e' un'eccezione al principio «decide un agente»: non sceglie
    # cosa cercare ne' cosa rispondere. Vieta di AFFERMARE qualcosa senza
    # averlo guardato, che e' la stessa famiglia dell'ACL — un confine di
    # onesta', non una scorciatoia di strategia. `chiedi` resta sempre
    # disponibile, quindi non ci si blocca mai.
    if mai_cercato:
        chiusura = [s for s in chiusura if s["function"]["name"] != "rispondi"]
    # E non si CHIEDE prima di aver guardato: «che tipo di regalo?» senza
    # aver aperto un catalogo e' far fare il lavoro alla persona.
    #
    # Questo cancello l'avevamo tolto stamattina perche' la misura diceva che
    # non serviva — e allora era vero, perche' il verdetto dell'analista
    # sulla rispondibilita' non esisteva ancora. Con quello attivo cambia
    # tutto: su «mi proponi qualcosa per il compleanno di mia mamma?»
    # l'analista dice «non rispondibile», `rispondi` sparisce, resta solo
    # `chiedi` — e il coordinatore chiede al primo giro senza aver eseguito
    # una query (misurato l'1/10/2026: analista > coordinatore > redattore,
    # 0ok/0ko). Due meccanismi giusti che insieme fanno una cosa sbagliata.
    #
    # MA non vale quando l'analista ha gia' detto che la domanda e' troppo
    # aperta per essere cercata. Li' chiedere non e' pigrizia: e' la mossa
    # che un agente ha gia' giudicato necessaria, e il suo verdetto batte la
    # cautela generica di questa riga. Senza questa eccezione, su «mi proponi
    # qualcosa per il compleanno di mia mamma?» il coordinatore era costretto
    # a cercare comunque: 223 secondi per un elenco di oggetti natalizi con
    # le etichette sbagliate, contro i 49 di una domanda onesta (1/10/2026).
    #
    # All'ultima mossa torna comunque, o un turno che non ha mai potuto
    # cercare resterebbe appeso.
    #
    # E non vale nemmeno quando il VENTAGLIO e' gia' pronto, per la ragione
    # letterale del cancello: «aver guardato». La guida l'archivio l'ha
    # guardato — le strade che propone sono lette nelle descrizioni vere
    # delle foto vicine al bisogno, non immaginate. `eseguite` conta le
    # `cerca` e non le vede, quindi da sola dice una cosa falsa.
    #
    # Il prezzo di non leggerlo era tutto il tempo del turno: lo stato
    # diceva al coordinatore «la mossa che resta e' `chiedi`» e `chiedi` non
    # era sul tavolo, cosi' rifaceva `proponi` a vuoto finche' l'ultima
    # mossa non riapriva il cancello — quattro giri, 37 secondi su 61
    # (2/10/2026). `ventaglio_pronto` arrivava qui gia' calcolato e nessuno
    # lo leggeva: stesso difetto di `esiti`, di `rispondibile` e del
    # ventaglio fuori dallo stato.
    if mai_eseguito and not ultima and not non_rispondibile             and not ventaglio_pronto:
        chiusura = [s for s in chiusura if s["function"]["name"] != "chiedi"]
    # L'analista ha detto che cosi' com'e' non si puo' rispondere: cercando
    # verrebbe fuori un mucchio di cose tenute insieme da una caratteristica
    # sola. Allora `rispondi` non si offre e resta `chiedi`, che e' la mossa
    # utile. A chiudere la porta NON e' il codice: e' un altro agente, e il
    # programma si limita a trasportarne il verdetto.
    #
    # Il verdetto NON decade perche' il critico ha confermato delle righe.
    # Ci avevo provato, col ragionamento che «l'evidenza batte la
    # previsione», ed e' sbagliato: su «mi serve qualcosa di blu» il critico
    # ne ha confermate 24 su 40 — correttamente, perche' sono blu davvero —
    # e il cancello si apriva, lasciando passare diciotto citazioni fra cui
    # un mappamondo di una guida doganale (misurato l'1/10/2026). Confermare
    # righe dice che corrispondono alle parole, non che la domanda fosse
    # abbastanza precisa da meritare una risposta: sono due proprieta'
    # diverse, e la seconda la rivede solo chi l'ha giudicata.
    #
    # E NON si riapre nemmeno all'ultima mossa. Ci avevo messo quell'uscita
    # «se no il turno non si chiude», ma il turno si chiude benissimo con
    # `chiedi` — che e' proprio la mossa giusta quando la domanda e' troppo
    # vaga. Con l'uscita, il cancello ritardava e basta: su «mi serve
    # qualcosa di blu» il coordinatore cercava fino a esaurire le mosse e poi
    # rispondeva lo stesso, 19 citazioni (misurato l'1/10/2026).
    #
    # A riaprirlo resta una strada sola, ed e' giusta: l'analista che
    # rigiudica quando i dati lo smentiscono (`_dopo_mosse`).
    if non_rispondibile:
        chiusura = [s for s in chiusura if s["function"]["name"] != "rispondi"]
        # Qui, il 1/10/2026, avevo tolto anche `cerca`: il coordinatore
        # cercava lo stesso, bruciava quattro mosse e in fondo il redattore
        # scriveva l'elenco. Toglierla funzionava — 124 secondi diventavano
        # 18 — ma era il CODICE a decidere la strategia: l'analista ha
        # giudicato la domanda, non ha detto «non cercare», quello
        # l'avevo dedotto io.
        #
        # La causa vera era un'altra, ed e' la stessa di `esiti`: il
        # verdetto non veniva MAI detto al coordinatore, che ne subiva gli
        # effetti senza saperne il motivo. Adesso glielo dice lo stato, a
        # parole, e la strategia la sceglie lui. Qui resta solo il confine
        # di onesta': non si AFFERMA una risposta a una domanda che si e'
        # giudicata non rispondibile.
    if ultima:
        return chiusura
    mosse = [
        {"type": "function", "function": {
            "name": "cerca",
            "description": ("Esegue una SELECT che scrivi tu. Chiamala piu' volte "
                            "nello stesso giro per aprire piu' strade insieme. "
                            "I permessi li mette il sistema: non scriverli."),
            "parameters": {"type": "object", "properties": {
                "sql": {"type": "string",
                        "description": "Una SELECT su una sola tabella. Vedi la mappa dei dati."},
                "motivo": {"type": "string", "description": "Cosa cerchi, in una riga."}},
                "required": ["sql"]}}},
        {"type": "function", "function": {
            "name": "proponi",
            "description": ("Quando la domanda si risponde con una SCELTA e non "
                            "con un risultato («un regalo per mia mamma», «cosa "
                            "avete di sportivo»): costruisce il ventaglio delle "
                            "strade LEGGENDOLE nell'archivio, invece di "
                            "inventarle. Poi chiudi con `chiedi`."),
            "parameters": {"type": "object", "properties": {
                "motivo": {"type": "string",
                           "description": "Cosa c'e' da restringere."}},
                "required": []}}},
        {"type": "function", "function": {
            "name": "verifica",
            "description": ("Fa leggere le righe trovate una per una e dice quali "
                            "rispondono davvero alla domanda."),
            "parameters": {"type": "object", "properties": {
                "motivo": {"type": "string",
                           "description": "Cosa deve avere una riga per andare bene."}},
                "required": []}}},
    ]
    if OSSERVATORE:
        mosse.insert(2, {"type": "function", "function": {
            "name": "guarda",
            "description": ("Riapre le FOTO vere e le fa guardare. Serve quando la "
                            "descrizione scritta non basta a decidere."),
            "parameters": {"type": "object", "properties": {
                "righe": {"type": "array", "items": {"type": "integer"},
                          "description": "I numeri delle righe da guardare."},
                "domanda": {"type": "string",
                            "description": "Cosa chiedere guardando la foto."}},
                "required": ["righe", "domanda"]}}})
    return mosse + chiusura


def _stato_a_parole(stato) -> str:
    """Lo stato come lo leggerebbe una persona. E' quello che il coordinatore
    ha davanti a ogni giro: non un riassunto scelto dal codice, ma tutto."""
    righe, verdetti = stato.get("righe") or [], stato.get("verdetti") or {}
    parti = [f"Domanda: «{stato['domanda']}»"]
    if stato.get("ambito"):
        parti.append("Che domanda e': " + stato["ambito"])
    if not stato.get("rispondibile", True):
        # Il verdetto dell'analista, DETTO. Prima lo usavamo solo per
        # togliere mosse dal tavolo: lui ne subiva gli effetti senza sapere
        # perche', ed e' lo stesso difetto di `esiti` — un giudizio
        # calcolato e mai pronunciato. Qui si dice e basta: cosa farne lo
        # decide lui.
        parti.append(
            "ATTENZIONE, chi ha letto la domanda dice che COSI' COM'E' non "
            "si puo' rispondere: cercando verrebbe fuori un mucchio di cose "
            "tenute insieme da una caratteristica sola, non una risposta "
            "utile. Non spendere una ricerca: `proponi` per vedere fra "
            "cosa si puo' scegliere, poi `chiedi`.")
    if stato.get("manca"):
        parti.append("Chi ha letto la domanda dice che NON dice: "
                     + "; ".join(stato["manca"])
                     + ".\nE' la materia prima di `chiedi`: se cercando cosi' "
                       "com'e' torna un mucchio di cose scollegate, la domanda "
                       "da fare e' questa.")
    if stato.get("chiarimento"):
        # Il ventaglio e' un pezzo di stato DUREVOLE, come le righe e i
        # verdetti, e come loro va detto a ogni giro. Finiva solo negli
        # `esiti`, che raccontano l'ultima mossa e basta: al giro dopo era
        # sparito dalla vista, e il coordinatore rifaceva `proponi` credendo
        # di non averlo. Tre volte di fila sul compleanno (1/10/2026),
        # trentotto secondi. Stesso difetto di `esiti` e di `rispondibile`:
        # un dato che lo stato ha e non pronuncia.
        parti.append("IL VENTAGLIO E' GIA' PRONTO — l'hai costruito tu, e "
                     "non e' cambiato niente da allora:\n" + stato["chiarimento"]
                     + "\nNon ricostruirlo e non cercare: la mossa che resta "
                       "e' `chiedi`, che lo mostra alla persona.")
    if not righe:
        parti.append("Non hai ancora trovato niente.")
    else:
        confermate = [n for n in verdetti if verdetti[n] == "si"]
        dubbie = [n for n in verdetti if verdetti[n] == "forse"]
        parti.append(
            f"Righe trovate: {len(righe)}. Verificate: {len(verdetti)} "
            f"(confermate {len(confermate)}, dubbie {len(dubbie)}, "
            f"scartate {sum(1 for v in verdetti.values() if v == 'no')}).")
        da_verificare = [n for n in range(1, len(righe) + 1) if n not in verdetti]
        if da_verificare:
            # Il fatto, detto per quello che comporta. Non e' un divieto: e'
            # dire cosa esce se si risponde adesso.
            parti.append(
                "%d righe non le ha ancora guardate nessuno. Se rispondi "
                "adesso escono cosi' come sono, marcate «NON VERIFICATA», e "
                "chi legge se lo vede scritto in cima alla risposta."
                % len(da_verificare))
        parti.append("Le righe, col verdetto se c'e':")
        parti.append(_scheda(righe, verdetti=verdetti))
        # Tutte verificate e nessuna buona. Il titolo qui sopra dice
        # «confermate 0», che si legge «vicolo cieco» — e sotto c'e' un muro
        # di [NO]. Ma quelle righe sono arrivate: sono la prova di come questo
        # archivio chiama le cose, ed e' esattamente l'informazione che manca
        # a chi ha cercato con la parola sbagliata. Il 1/10/2026, su «palline
        # rosse o gialle», fra le 40 righe scartate c'era «Christmas ornament
        # ball»: il coordinatore ce l'aveva davanti e ha preferito chiedere
        # alla persona invece di ricercare con quella parola.
        if verdetti and not confermate and len(verdetti) >= len(righe):
            parti.append(
                "NESSUNA riga e' buona, ma tutte queste righe ESISTONO e le hai "
                "trovate tu: sono il vocabolario di questo archivio per la zona "
                "in cui stai cercando. Prima di arrenderti o di chiedere alla "
                "persona, rileggile per come sono SCRITTE, non per quello che "
                "valgono: se dentro c'e' la parola che ti serviva e che non "
                "avevi (il catalogo la chiama cosi', tu l'avevi chiamata in un "
                "altro modo), ricerca con quella. Chiedere alla persona una "
                "parola che ce l'hai gia' sotto gli occhi non la aiuta.")
    # COM'E' ANDATA L'ULTIMA MOSSA, per esteso. Qui dentro ci sono le
    # spiegazioni dei rifiuti («scrivi una parola per condizione»), lo zero
    # righe col vocabolario del vicinato, l'esito della verifica.
    #
    # Venivano calcolate e buttate via: `esiti` finiva nello stato e nessuno
    # lo leggeva. Il coordinatore vedeva solo i contatori, quindi non sapeva
    # PERCHE' una query fosse stata respinta e la riscriveva identica —
    # misurato l'1/10/2026: quattro query, due uguali fra loro, tutte con la
    # stessa frase dentro un ~*, 81 secondi e zero ricerche. Tutto il lavoro
    # fatto per spiegargli gli errori non e' mai arrivato a destinazione.
    if stato.get("esiti"):
        parti.append("Com'e' andata l'ultima mossa:\n"
                     + "\n".join(f"- {e}" for e in stato["esiti"]))
    if stato.get("respinte", 0) or stato.get("eseguite", 0):
        parti.append("Ricerche: %d eseguite, %d respinte prima di partire."
                     % (stato.get("eseguite", 0), stato.get("respinte", 0)))
        if stato.get("respinte", 0) and not stato.get("eseguite", 0):
            parti.append("Non hai ancora interrogato l'archivio NEMMENO UNA "
                         "volta: le tue query sono state tutte respinte prima "
                         "di partire. Qualunque cosa tu dica su cosa c'e' o non "
                         "c'e' sarebbe inventata. Correggi la query e riprova.")
    resta = MAX_PASSI - stato.get("passi", 0)
    parti.append(f"Ti restano {resta} mosse." if resta > 1 else
                 "È L'ULTIMA MOSSA: decidi adesso, o chiudi con `rispondi`.")
    return "\n".join(parti)


def _pensiero_a_chi_guarda(stato):
    """Il ragionamento del coordinatore verso chi sta aspettando, o None.

    Il modello ragiona per decidere la mossa e quel testo lo buttavamo. Ma
    e' esattamente la risposta alla domanda «cosa sta facendo da quaranta
    secondi?», e LibreChat sa renderlo come blocco pieghevole: chi vuole
    guarda, chi non vuole vede solo la risposta.

    Non si inventa niente e non si riassume: esce il pensiero vero. Se la
    chiamata non e' in streaming (prove, valutazioni) non esce niente.
    """
    su_pezzo = stato.get("su_pezzo")
    if not callable(su_pezzo):
        return None
    return lambda testo: su_pezzo(("pensiero", testo))


def _nodo_coordinatore(stato):
    t0 = time.monotonic()
    messaggi = [
        {"role": "system", "content": _prompt("coordinatore", P_COORDINATORE)},
        {"role": "system", "content": mappa.mappa_dati() + "\n" + operatori.MAPPA_OPERATORI},
    ]
    messaggi += stato.get("storia") or [{"role": "user", "content": stato["domanda"]}]
    messaggi.append({"role": "user", "content": "Stato:\n" + _stato_a_parole(stato)})
    ultima = stato.get("passi", 0) >= MAX_PASSI - 1
    # Il coordinatore ragiona se la domanda lo merita, e a dirlo e' l'ANALISTA.
    #
    # Ragionare e' quello che gli ha fatto smettere di arrendersi al primo
    # buco, ed e' anche il 60-70% del tempo di un turno: 14-20 secondi per
    # chiamata contro 2-12, quasi quattro volte a turno. Spegnerlo in base a
    # una regola del codice («se e' andata male, pensa») sarebbe il programma
    # che decide come deve pensare il modello. Lo decide invece l'agente il
    # cui mestiere e' giudicare la domanda — gratis, perche' gira comunque.
    # Nel dubbio ragiona: una risposta lenta e giusta batte una veloce e
    # sbagliata.
    scelte = _decide(messaggi,
                     _strumenti_del_coordinatore(
                         ultima,
                         stato.get("respinte", 0) > 0 and stato.get("eseguite", 0) == 0,
                         stato.get("eseguite", 0) == 0,
                         not stato.get("rispondibile", True),
                         bool(stato.get("chiarimento"))),
                     ragiona=bool(stato.get("impegnativa", True)),
                     su_pensiero=_pensiero_a_chi_guarda(stato))
    # Nessuna decisione dopo due richieste: e' un guasto del modello, non una
    # strategia. Si va a scrivere con quello che c'e' — e resta nella traccia.
    if not scelte:
        scelte = [("rispondi", {"motivo": "il coordinatore non ha deciso"})]
    # La domanda da fare alla persona. Se la GUIDA ne ha gia' costruita una
    # — col ventaglio letto nei dati — quella vince: il coordinatore la
    # riscriverebbe con parole sue, e le parole sue sono proprio quelle
    # inventate che la guida esiste per evitare. Vale anche quando lui non
    # chiede niente: il ventaglio non si cancella.
    chiarimento = (stato.get("chiarimento") or ""
                   or next((str(a.get("domanda") or "").strip()
                            for n, a in scelte if n == "chiedi"), ""))
    return {"mosse": scelte, "passi": stato.get("passi", 0) + 1,
            "chiarimento": chiarimento,
            "traccia": [_nota("coordinatore",
                              {"mosse": [n for n, _ in scelte]}, 0, t0)]}


# ==========================================================================
# LE MOSSE — eseguite insieme, perche' sono indipendenti
# ==========================================================================
def _mossa_cerca(dsn, gruppi, aziende, arg, domanda: str, gia_viste=()):
    """Una ricerca: (righe, testo per il modello, esito).

    Sulla SUA connessione, perche' le mosse di un giro girano insieme e una
    connessione psycopg non si usa da due parti nello stesso momento.

    L'esito distingue tre cose che finora arrivavano al coordinatore tutte
    uguali — un numero piccolo: `respinta` (la query non e' mai partita),
    `vuota` (e' partita e non c'e' niente), `eseguita`. Confonderle e' costato
    un falso negativo con tre query respinte e zero ricerche.
    """
    sql = str(arg.get("sql", "")).strip()
    if not sql:
        return [], "(query vuota)", "respinta"
    # Questa query l'ha gia' fatta in questo turno. Ripeterla non puo' dare un
    # risultato diverso, e lui non ha modo di accorgersene da solo: su «quante
    # foto ha il catalogo Gasper» ha scritto QUATTRO volte la stessa SELECT,
    # tutte eseguite, tutte a zero righe (1/10/2026). Non e' un divieto, e'
    # dirgli un fatto che altrimenti non vede.
    if " ".join(sql.split()).rstrip(";").lower() in gia_viste:
        return [], ("Questa query l'hai gia' eseguita in questo turno, identica. "
                    "Il risultato e' lo stesso di prima e ripeterla non lo "
                    "cambia. Cambia qualcosa: la parola, l'operatore (`=` vuole "
                    "il valore ESATTO, `~*` cerca un pezzo), la tabella — "
                    "oppure fermati e di' quello che hai."), "ripetuta"
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        righe, problemi = operatori.esegui(conn, sql, gruppi, aziende)
        if problemi:
            # Respinta: non ha guardato niente, quindi non ha scoperto niente.
            # Nessun vicinato, o gli daremmo per buona una ricerca mai fatta.
            return [], "QUERY RESPINTA:\n" + "\n".join(f"- {p}" for p in problemi), "respinta"
        if righe:
            return righe, f"{len(righe)} righe.", "eseguita"
        # Zero righe da una query ESEGUITA. Il numero da solo dice «vicolo
        # cieco», e non e' detto: puo' voler dire «hai usato parole che qui
        # non si usano». La differenza sta nei dati, quindi si mostrano.
        vicino = operatori.vicinato(conn, operatori._tabella(sql), domanda,
                                    gruppi, aziende,
                                    colonne=operatori.colonne_chieste(sql))
    testo = ("0 righe. La query e' lecita e i permessi sono gia' applicati: "
             "con QUESTE parole qui dentro non c'e' niente.")
    if vicino:
        testo += ("\nMa guarda come l'archivio scrive la cosa piu' vicina a "
                  "quello che cerchi — sono le sue parole, non le tue:\n"
                  + _scheda(vicino)
                  + "\nNon sono risultati e non si citano: sono il vocabolario. "
                    "Se fra queste righe c'e' il termine che ti serviva, "
                    "riscrivi la query con quello.")
    return [], testo, "vuota"


def _nodo_mosse(stato):
    """Esegue le mosse del giro. Quelle dello stesso giro partono INSIEME."""
    conn, gruppi = stato["conn"], stato["gruppi"]
    righe = list(stato.get("righe") or [])
    verdetti = dict(stato.get("verdetti") or {})
    traccia, esiti = [], []
    chiarimento = stato.get("chiarimento") or ""
    mosse = stato.get("mosse") or []

    eseguite, respinte = stato.get("eseguite", 0), stato.get("respinte", 0)
    viste_sql = set(stato.get("query_fatte") or [])
    ricerche = [(i, a) for i, (n, a) in enumerate(mosse) if n == "cerca"]
    if ricerche:
        t0 = time.monotonic()
        with futures.ThreadPoolExecutor(max_workers=max(1, PARALLELE)) as pool:
            lavori = [pool.submit(_mossa_cerca, stato["dsn"], gruppi,
                                  stato["aziende"], a, stato["domanda"],
                                  viste_sql)
                      for _, a in ricerche]
            risultati = [l.result() for l in lavori]
        viste = {r.get("id") for r in righe if r.get("id") is not None}
        for (_, arg), (nuove, testo, esito) in zip(ricerche, risultati):
            viste_sql.add(" ".join(str(arg.get("sql", "")).split()).rstrip(";").lower())
            if esito == "respinta":
                respinte += 1
            elif esito != "ripetuta":
                eseguite += 1
            for r in nuove:
                chiave = r.get("id")
                if chiave is None or chiave not in viste:
                    viste.add(chiave)
                    righe.append(dict(r))
            esiti.append(f"cerca [{str(arg.get('motivo') or '')[:60]}]: {testo}")
            traccia.append(_nota("cerca", {"sql": str(arg.get("sql", ""))[:300]},
                                 nuove, t0))
        righe = righe[:MAX_RIGHE]

    for nome, arg in mosse:
        if nome == "verifica":
            t0 = time.monotonic()
            nuovi = _critico(stato, righe, str(arg.get("motivo") or ""))
            verdetti.update(nuovi)
            esiti.append("verifica: %d confermate, %d scartate, %d dubbie" % (
                sum(1 for v in nuovi.values() if v == "si"),
                sum(1 for v in nuovi.values() if v == "no"),
                sum(1 for v in nuovi.values() if v == "forse")))
            traccia.append(_nota("critico", {"verificate": len(nuovi)}, 0, t0))
        elif nome == "proponi":
            t0 = time.monotonic()
            # Il ventaglio c'e' gia': rifarlo sullo stesso stato da' lo stesso
            # risultato e costa un giro. E' lo stesso fatto che diciamo per
            # una query ripetuta — il coordinatore non ha modo di accorgersene
            # da solo (1/10/2026: su «cosa avete di sportivo» l'ha chiesto tre
            # volte di fila, trenta secondi buttati e zero citazioni).
            if chiarimento:
                esiti.append(
                    "proponi: il ventaglio l'hai gia' costruito e non e' "
                    "cambiato niente da allora — eccolo:\n" + chiarimento
                    + "\nAdesso o lo mostri alla persona con `chiedi`, o "
                      "scegli tu una delle strade e la cerchi.")
                traccia.append(_nota("guida", {"gia_fatto": True}, 0, t0))
                continue
            proposta = _guida(stato, str(arg.get("motivo") or ""))
            if proposta:
                chiarimento = proposta
                esiti.append("proponi: ventaglio pronto, chiudi con `chiedi`")
            else:
                esiti.append("proponi: non sono riuscito a costruire le strade")
            traccia.append(_nota("guida", {"fatto": bool(proposta)}, 0, t0))
        elif nome == "guarda":
            t0 = time.monotonic()
            numeri = [int(n) for n in (arg.get("righe") or [])
                      if isinstance(n, (int, str)) and str(n).isdigit()][:MAX_FOTO]
            guardate = _osservatore(conn, righe, numeri,
                                    str(arg.get("domanda") or stato["domanda"]))
            # Una riga appena guardata torna DA VERIFICARE: adesso c'e' una
            # prova che prima non c'era, e il verdetto vecchio l'aveva dato
            # chi quella prova non ce l'aveva. Senza questo, il critico non
            # riguarda mai una riga gia' giudicata e la foto e' stata aperta
            # per niente.
            for n in guardate:
                verdetti.pop(n, None)
            esiti.append("guarda: %d foto guardate, ora da riverificare" % len(guardate))
            traccia.append(_nota("osservatore", {"foto": len(guardate)}, 0, t0))

    return {"righe": righe, "verdetti": verdetti, "mosse": [],
            "chiarimento": chiarimento,
            "esiti": esiti, "eseguite": eseguite, "respinte": respinte,
            "query_fatte": sorted(viste_sql),
            "traccia": traccia}


# ==========================================================================
# LA GUIDA — quando la risposta e' una SCELTA, non un risultato
# ==========================================================================
# «Mi proponi qualcosa per il compleanno di mia mamma?» non ha una pagina
# giusta: ha un ventaglio. Finora finiva in uno dei due modi sbagliati —
# un elenco lungo di oggetti presi alla lontana (223 secondi, un albero di
# Natale offerto come regalo), oppure una domanda inventata di sana pianta
# («vuoi decorazioni, abiti o accessori?») con parole che nei cataloghi non
# esistono.
#
# Il vincolo che decide se funziona: le scelte si LEGGONO nei dati, non si
# immaginano. C'era gia' un meccanismo pensato per questo
# (`indice.descrizioni_visibili`, la cui docstring dice proprio «generare
# categorie ANCORATE a cio' che il catalogo contiene davvero, invece di
# inventare zaini e cappelli») ma la tabella `indice` e' vuota e non e' mai
# stata generata. Qui si usa materiale che c'e': le descrizioni vere delle
# foto piu' vicine, per significato, al bisogno espresso.
P_GUIDA = """Una persona ha chiesto qualcosa di aperto: non c'e' una risposta sola, c'e' un ventaglio. Il tuo lavoro e' aiutarla a restringere, mostrandole fra cosa puo' scegliere.

Ti do quello che la persona ha chiesto e un campione VERO di cose che stanno in questo archivio, le piu' vicine al suo bisogno. Leggile, e riconosci le FAMIGLIE che vedi: non le categorie che ti aspetteresti in un negozio, ma quelle che ci sono davvero in queste righe.

La regola che conta: **ogni scelta che proponi deve corrispondere a cose che hai visto nel campione.** Se nel campione ci sono diffusori, confezioni regalo e decorazioni natalizie, quelle sono le scelte. Se proponi «abiti» o «accessori» perche' suonano bene per un regalo, stai inventando — e chi sceglie quella strada trovera' il vuoto.

Se il campione non contiene niente di adatto a quello che chiede, dillo: e' un'informazione utile. «Nei cataloghi non vedo articoli sportivi» vale piu' di cinque scelte finte.

Da tre a cinque scelte, brevi, con parole che una persona capisce — non le etichette inglesi del catalogo. Niente pagine e niente codici: qui non si risponde, si orienta.

Consegna con lo strumento `percorso`."""

I_PERCORSO = [{"type": "function", "function": {
    "name": "percorso",
    "description": "Le strade fra cui la persona puo' scegliere.",
    "parameters": {"type": "object", "properties": {
        "introduzione": {"type": "string",
                         "description": "Una riga: cosa c'e' in archivio di "
                                        "attinente, o che non c'e' niente."},
        "scelte": {"type": "array", "items": {"type": "string"},
                   "description": "Da tre a cinque strade, brevi, ognuna "
                                  "corrispondente a cose viste nel campione."},
        "domanda": {"type": "string",
                    "description": "La domanda con cui chiudere, una riga."}},
        "required": ["introduzione", "scelte", "domanda"]}}}]


def _guida(stato, motivo: str) -> str:
    """Il ventaglio di strade, costruito sulle righe vere. "" se non riesce."""
    campione = operatori.vicinato(
        stato["conn"], "immagini", stato.get("ambito") or stato["domanda"],
        stato["gruppi"], stato["aziende"], quanti=GUIDA_CAMPIONE,
        colonne="id, documento, page, descrizione")
    if not campione:
        return ""
    messaggi = [{"role": "system", "content": _prompt("guida", P_GUIDA)}]
    messaggi += stato.get("storia") or []
    messaggi.append({"role": "user", "content":
                     f"Cosa ha chiesto: {stato.get('ambito') or stato['domanda']}\n"
                     + (f"Cosa serve restringere: {motivo}\n" if motivo else "")
                     + "\nCampione vero dall'archivio:\n" + _scheda(campione)})
    for nome, arg in _decide(messaggi, I_PERCORSO):
        scelte = [str(s).strip() for s in (arg.get("scelte") or []) if str(s).strip()]
        parti = [str(arg.get("introduzione") or "").strip()]
        parti += [f"- {s}" for s in scelte[:5]]
        parti.append(str(arg.get("domanda") or "").strip())
        return "\n".join(p for p in parti if p)
    return ""


# ==========================================================================
# IL CRITICO — verifica, non cerca e non scrive
# ==========================================================================
P_CRITICO = """Verifichi. Non cerchi e non scrivi la risposta: ti do la conversazione con la persona e delle righe trovate in un archivio di cataloghi, e per ognuna dici se quello che e' stato chiesto C'E'.

Leggi la CONVERSAZIONE INTERA, non l'ultima riga. Quasi mai l'ultima riga si regge da sola: dopo «hai palline rosse o gialle?» un «niente gialle?» vuol dire «palline di Natale gialle», non «qualunque cosa gialla». Se giudichi sull'ultima riga soltanto, approvi una ghirlanda verde e un quadro giallo preso da una guida doganale — e' successo davvero. Quello che la persona sta cercando e' quello che si capisce dal discorso.

Due errori opposti, ed e' facile farli tutti e due:

- Troppo largo. Quello che e' stato chiesto deve esserci PER INTERO, in uno stesso articolo. Se la domanda dice «nastri bianchi con cuori rossi», un nastro bianco senza cuori e' no, e un nastro con cuori ma rosa e' no: due cose chieste, due cose che devono stare sullo stesso oggetto, non una qui e una la'.
- Troppo stretto. Una riga e' UNA FOTO, e una foto di catalogo mostra spesso PIU' ARTICOLI insieme, con piu' codici e un elenco di colori. Basta che UNO di quegli articoli sia quello chiesto perche' la riga vada bene: di' tu quale, nel motivo. Una foto con tre nastri di cui uno bianco con cuori rossi va bene. E un articolo che ha quello che serve e in piu' qualcos'altro (dei puntini, un bordo dentellato) va bene lo stesso: il di piu' non toglie.

Non scartare un doppione che non e' un doppione: due articoli con codice diverso sono due articoli, anche se la descrizione si somiglia.

Attento a cosa e' stato chiesto, perche' non sempre e' un prodotto. Se la domanda chiedeva un numero, un totale o l'elenco dei documenti, la riga che PORTA quel dato va bene, anche se non e' un articolo e non ha una pagina. Una riga si scarta perche' non risponde, mai perche' non e' un prodotto.

Usa `forse` solo quando la descrizione scritta non basta a deciderlo: in quel caso scrivi nel motivo la domanda precisa da fare guardando la foto.

Se una riga porta scritto «guardata: ...», qualcuno ha aperto la FOTO vera e ha risposto a quella domanda. Quella e' una prova migliore della didascalia, che era stata scritta senza sapere cosa avremmo chiesto: se le due cose si contraddicono, vale quello che si e' visto nella foto. Ma leggila per intero — «direi di no», «non si vede bene» e «si, ma sono rosa» dicono tre cose diverse.

Consegna i verdetti chiamando lo strumento `verdetti`, uno per ogni riga che ti ho dato. Non saltarne nessuna."""

I_VERDETTI = [{"type": "function", "function": {
    "name": "verdetti",
    "description": "I tuoi verdetti, uno per ogni riga.",
    "parameters": {"type": "object", "properties": {
        "esiti": {"type": "array", "items": {"type": "object", "properties": {
            "riga": {"type": "integer", "description": "Il numero della riga."},
            "esito": {"type": "string", "enum": ["si", "no", "forse"]},
            "motivo": {"type": "string", "description": "Mezza riga."}},
            "required": ["riga", "esito"]}}},
        "required": ["esiti"]}}}]


def _critico(stato, righe, motivo: str) -> dict:
    """{numero riga: si|no|forse} per le righe non ancora verificate.

    A GRUPPI, giudicati in parallelo. Una chiamata sola su quaranta righe
    costava 18,5 secondi — un terzo del tempo di un turno, e il pezzo piu'
    caro rimasto dopo che il ragionamento del coordinatore e' diventato
    adattivo. Le righe sono indipendenti e il prompt gli dice gia' di non
    confrontarle fra loro («due articoli con codice diverso sono due
    articoli»), quindi spezzarle non gli toglie niente.
    """
    gia = stato.get("verdetti") or {}
    da_fare = [(i + 1, r) for i, r in enumerate(righe) if (i + 1) not in gia]
    if not da_fare:
        return {}
    # Si spezza in gruppi SOLO se il server del modello serve davvero piu'
    # richieste insieme, e in tanti gruppi quanti sono i suoi slot.
    #
    # Con un solo slot spezzare fa danno: i thread si accodano dentro il
    # server e ogni chiamata rispedisce tutto il prompt da capo. Misurato
    # l'1/10/2026 con `--parallel 1`: il critico diviso in tre e' passato da
    # 18,5 a 29,4 secondi. Il numero non e' scritto qui ne' in una variabile
    # d'ambiente: lo chiede al server (`modello.slot()`), cosi' il giorno che
    # in produzione si alza `--parallel` il critico se ne accorge da solo.
    slot = min(modello.slot(), max(1, PARALLELE))
    if slot > 1 and len(da_fare) > slot:
        quanti = -(-len(da_fare) // slot)        # righe per gruppo, arrotondate su
        gruppi = [da_fare[i:i + quanti] for i in range(0, len(da_fare), quanti)]
        fuori = {}
        with futures.ThreadPoolExecutor(max_workers=slot) as pool:
            for parte in pool.map(lambda g: _giudica(stato, righe, motivo, g),
                                  gruppi):
                fuori.update(parte)
        return fuori
    return _giudica(stato, righe, motivo, da_fare)


def _giudica(stato, righe, motivo: str, da_fare) -> dict:
    """Un gruppo di righe, una chiamata al critico."""
    numeri = [n for n, _ in da_fare]
    # Il critico riceve la CONVERSAZIONE, non la sola ultima riga.
    #
    # Il 30/09/2026, dopo «hai dei kit per albero di natale ... palline rosse
    # o gialle», la persona ha scritto «niente gialle?». L'analista aveva
    # capito («kit per albero di Natale con palline rosse o gialle»), il
    # coordinatore e il redattore avevano la storia — il critico no. Per lui
    # la domanda era letteralmente «niente gialle?», e ha confermato 14 righe
    # su 20: una ghirlanda verde, della lametta, e un quadro incorniciato
    # preso da una guida doganale. Tutti gialli, tutti giusti rispetto alla
    # domanda che aveva letto. Chi giudica se una riga risponde deve sapere a
    # CHE COSA risponde, e in una conversazione non e' l'ultima riga.
    messaggi = [{"role": "system", "content": _prompt("critico", P_CRITICO)}]
    messaggi += stato.get("storia") or []
    messaggi.append({"role": "user", "content":
                     f"L'ultima cosa che ha scritto: «{stato['domanda']}»\n"
                     + (f"Di cosa si sta parlando: {stato['ambito']}\n"
                        if stato.get("ambito") else "")
                     + (f"Cosa deve avere una riga per andare bene: {motivo}\n" if motivo else "")
                     + "\nRighe da verificare:\n"
                     + _scheda([r for _, r in da_fare], numeri=numeri, visto=True)})
    fuori = {}
    for nome, arg in _decide(messaggi, I_VERDETTI):
        for e in arg.get("esiti") or []:
            try:
                n = int(e.get("riga"))
            except (TypeError, ValueError):
                continue
            esito = str(e.get("esito", "")).lower()
            if n in numeri and esito in ("si", "no", "forse"):
                fuori[n] = esito
                righe[n - 1]["_motivo"] = str(e.get("motivo") or "")[:200]
    return fuori


# ==========================================================================
# L'OSSERVATORE — l'unico che guarda davvero la foto
# ==========================================================================
P_OSSERVATORE = """Guarda questa foto di catalogo e rispondi SOLO alla domanda che ti faccio. Non descrivere l'immagine, non elencare cosa vedi.

Comincia con una parola — si oppure no — e aggiungi mezza riga di motivo. Se la foto non permette di deciderlo, comincia con «non si vede»."""


def _guarda(conn, img_id: int, domanda: str) -> str:
    """Il verdetto del VLM su UNA foto, o "" se non si e' potuto guardare."""
    letto = immagini_mod.leggi(conn, img_id)
    if not letto:
        return ""
    dati, _ = immagini_mod.miniatura(letto[0], LATO_VLM)
    b64 = base64.b64encode(dati).decode()
    corpo = {"model": MODELLO_VLM, "temperature": 0.0, "max_tokens": 120,
             "messages": [
                 {"role": "system", "content": _prompt("osservatore", P_OSSERVATORE)},
                 {"role": "user", "content": [
                     {"type": "text", "text": domanda},
                     {"type": "image_url",
                      "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]}
    host = os.environ.get("MODELLI_HOST", "host.docker.internal:1235")
    try:
        with egress.client(timeout=90.0, verify=False) as c:
            r = c.post(f"http://{host}/v1/chat/completions", json=corpo)
            r.raise_for_status()
            return (r.json()["choices"][0]["message"].get("content") or "").strip()
    except egress.EgressVietato:
        raise
    except Exception as e:
        print(f"osservatore: {type(e).__name__}: {e}", flush=True)
        return ""


def _osservatore(conn, righe, numeri, domanda: str) -> list:
    """GUARDA le foto indicate e scrive cosa si e' visto. Torna i numeri
    guardati.

    Non da' verdetti, e non e' una sfumatura. La versione di prima leggeva la
    risposta del VLM con `startswith(("si", "yes"))`: un «direi di si» non
    cadeva in nessun ramo e la riga restava col verdetto vecchio, in silenzio.
    E' lo stesso ripiego-da-regex che abbiamo tolto dappertutto, rimasto qui
    perche' il VLM e' un modello che descrive e non chiama strumenti.

    La soluzione non e' una regex migliore: e' che guardare e giudicare sono
    due lavori. L'osservatore guarda e riporta; il verdetto lo rifa' il
    critico, che quella frase se la legge insieme alla didascalia.
    """
    guardate = []
    for n in numeri:
        if not (1 <= n <= len(righe)) or righe[n - 1].get("id") is None:
            continue
        risposta = _guarda(conn, righe[n - 1]["id"], domanda)
        if not risposta:
            continue
        righe[n - 1]["_visto"] = f"«{domanda}» -> {risposta}"
        guardate.append(n)
    return guardate


# ==========================================================================
# IL REDATTORE — scrive, e cita le righe per numero
# ==========================================================================
P_REDATTORE = """Scrivi la risposta per la persona, in italiano.

Usi SOLO le righe che ti do. Non aggiungere prodotti, pagine, codici o prezzi che non sono li' dentro: se non c'e', non esiste.

Parli a una persona, non a un collega che conosce il sistema: le «righe» sono una cosa nostra e lei non sa cosa siano. Non nominarle MAI. Niente «non ho ricevuto alcuna riga da elaborare», niente «nessuna riga disponibile»: sono frasi che hai scritto davvero, perfino sotto un «ciao», e chi legge non capisce di cosa parli.

Quando non ti do nessuna riga, guarda PRIMA se c'e' una domanda da fare alla persona.

- C'e' una domanda: falla, e basta. Non dire che non hai trovato niente — nessuno ha cercato, quindi non lo sai, e dirlo manda via una persona che avrebbe avuto quello che cercava.
- Non c'e' nessuna domanda ed era un saluto: rispondi al saluto, senza scuse e senza niente che faccia pensare a un guasto.
- Non c'e' nessuna domanda e si e' cercato davvero: allora si', dillo in una frase.

Ogni riga porta scritto com'e' messa, e la differenza la devi passare a chi legge:
- **SI** — qualcuno l'ha controllata e risponde davvero. Presentala e basta.
- **FORSE** — controllata, ma dalla descrizione non si capiva: dillo («da verificare», «la scheda non lo specifica»). Non spacciarla per certa.
- **NON VERIFICATA** — trovata e mai controllata da nessuno. La puoi dare, ma chi legge deve sapere che e' un risultato grezzo: una riga sola lo dice per tutte («questi risultati non sono stati verificati uno per uno»).
Se sono tutte non verificate, dillo prima dell'elenco, non dopo. Una pagina che sembra controllata e non lo e' e' peggio di una pagina in meno.

E non scrivere MAI frasi come «tutte le informazioni sono state verificate» o «dati controllati». L'etichetta e' di ogni singola riga, non dell'insieme: se anche una sola e' FORSE o NON VERIFICATA, quella frase e' falsa — e l'hai scritta davvero, sotto un elenco in cui sei righe su venti erano confermate. Non rassicurare chi legge su un controllo che non c'e' stato: e' l'unico modo di sbagliare che gli fa prendere una decisione sbagliata senza accorgersene.

Se ti do anche una DOMANDA DA FARE alla persona, chiudi con quella: prima mostri quello che hai trovato (se hai trovato qualcosa), poi la fai, in una riga, senza girarci intorno e senza scusarti.

E in quel caso **non dire che non hai trovato niente**: se c'e' una domanda da fare, vuol dire che la richiesta era troppo generica per cercare — non che l'archivio sia vuoto. «Non trovo niente su questo nei cataloghi» davanti a «mi serve qualcosa di blu» e' falso: di cose blu ce ne sono a decine, semplicemente non si e' guardato. Scrivere che non c'e' niente senza aver guardato e' il modo peggiore di sbagliare, perche' chi legge smette di cercare. Fai la domanda e basta.

Ogni affermazione porta il numero della riga da cui viene, scritto cosi': [[3]]. Il sistema lo trasforma nel collegamento alla pagina giusta, quindi mettilo SEMPRE, subito dopo la cosa che stai dicendo. Non scrivere tu «pagina 27»: al suo posto metti [[3]]. Non scrivere una sezione «Fonti»: la aggiunge il sistema.

**Non scrivere MAI un indirizzo web.** Niente `http://`, niente `(https://...)`, niente `[testo](link)` scritto da te: il collegamento ce lo mette il sistema al posto di [[3]], ed e' quello vero, firmato. Un indirizzo che scrivi tu e' inventato per definizione — non hai modo di conoscerlo — e porta chi legge in un posto che non esiste. E' successo: hai aggiunto `(https://example.com/page40)` dopo una citazione gia' collegata.

Ti hanno dato delle righe perche' rispondono tutte: NON sceglierne un sottoinsieme. Ogni riga che hai davanti compare nella risposta, anche quando due si somigliano — due articoli con codice diverso sono due articoli.

Come presenti quello che hai lo decidi tu: elenco per gli articoli di catalogo, prosa continua per un documento di testo, raggruppato per catalogo se aiuta. Chi legge deve capire al volo cosa c'e' e dove guardare.

Traduci in italiano i nomi dei prodotti («Strauss mit Amaryllis» diventa «bouquet con amaryllis»); i codici articolo restano come sono."""


def _da_consegnare(righe, verdetti):
    """(righe per il redattore, etichetta vera di ognuna).

    Il codice non promuove e non scarta: riporta. Qui stavano tre decisioni
    prese dal programma, e la peggiore mentiva:

    - `if righe and not verdetti: confermate = righe` promuoveva a «verificate»
      venti righe che nessuno aveva guardato. Misurato l'1/10/2026: una
      risposta su tre usciva senza controllo, e chi la leggeva non poteva
      saperlo.
    - `== "si"` buttava via i `forse`: il critico dice «non so» e il codice lo
      traduceva in «no».
    - una riga senza verdetto spariva, quando qualche verdetto c'era.

    L'unica cosa che si toglie e' quello che il CRITICO ha scartato, e non e'
    una scelta del codice: e' obbedire a un agente che ha giudicato. Tutto il
    resto passa con l'etichetta che ha, e come dirlo lo decide il redattore.
    """
    etichette = {n: verdetti.get(n, "non verificata")
                 for n in range(1, len(righe) + 1)}
    tenute = [n for n in sorted(etichette) if etichette[n] != "no"]
    return ([righe[n - 1] for n in tenute],
            {i + 1: etichette[n] for i, n in enumerate(tenute)})


def _nodo_redattore(stato):
    t0 = time.monotonic()
    righe, verdetti = stato.get("righe") or [], stato.get("verdetti") or {}
    confermate, etichette = _da_consegnare(righe, verdetti)
    messaggi = [{"role": "system", "content": _prompt("redattore", P_REDATTORE)}]
    messaggi += stato.get("storia") or [{"role": "user", "content": stato["domanda"]}]
    chiarimento = stato.get("chiarimento") or ""
    messaggi.append({"role": "user", "content":
                     "Righe:\n" + _scheda(confermate, verdetti=etichette, visto=True)
                     + (f"\n\nDOMANDA DA FARE alla persona: {chiarimento}"
                        if chiarimento else "")})
    # Con `su_pezzo` la risposta esce MENTRE il modello la scrive. Non cambia
    # niente di quello che dice: cambia che chi legge non aspetta 17 secondi
    # davanti al vuoto (misurato: il redattore e' il 20% del tempo di un
    # turno, ed e' l'unico pezzo che l'utente aspetta per intero).
    # I «[[3]]» vanno risolti nel flusso, altrimenti si vedrebbero i
    # segnaposti; le fonti vanno in coda, dove gia' stanno.
    su_pezzo = stato.get("su_pezzo")
    if callable(su_pezzo):
        con_fonti = _con_fonti(stato["conn"], confermate, list(stato["gruppi"]))
        sostituisci, coda = collegatore(con_fonti, stato.get("base") or "",
                                        stato.get("utente") or "")
        passa, resi = _a_pezzi(sostituisci), []

        def _manda(testo):
            if testo:
                resi.append(testo)
                su_pezzo(("testo", testo))

        for pezzo in _pezzi_del_modello(messaggi, 2500):
            _manda(passa(pezzo))
        _manda(passa("", fine=True))
        _manda(coda(etichette))
        return {"risposta": "".join(resi), "resa": True,
                "confermate": confermate, "etichette": etichette,
                "traccia": [_nota("redattore", {"righe": len(confermate),
                                                "streaming": True}, 0, t0)]}
    return {"risposta": modello.chiedi(messaggi, max_tokens=2500),
            "confermate": confermate, "etichette": etichette,
            "traccia": [_nota("redattore", {"righe": len(confermate)}, 0, t0)]}


# ==========================================================================
# Le fonti: dal numero alla riga vera, senza indovinare
# ==========================================================================
_CITAZIONE = re.compile(r"\[\[\s*(\d+)\s*\]\]")
_APICI = "¹²³⁴⁵⁶⁷⁸⁹"


def _apice(n: int) -> str:
    return "".join(_APICI[int(c) - 1] if c != "0" else "⁰" for c in str(n))


def _con_fonti(conn, righe, gruppi):
    """Ogni riga con il suo `source_id`. Il coordinatore non e' obbligato a
    chiederlo nella SELECT, e senza non si firma il collegamento. Si risolve
    dalla tabella `documenti`, e ogni candidato passa da `visibile`: lo stesso
    controllo usato per aprire una pagina. Una riga di un documento non
    visibile resta senza firma invece di diventare un link."""
    mancanti = {r["documento"] for r in righe
                if r.get("documento") and not r.get("source_id")}
    if not mancanti:
        return righe
    trovati = {}
    for riga in conn.execute("SELECT DISTINCT source_id, documento FROM documenti "
                             "WHERE documento = ANY(%s)", (list(mancanti),)):
        if documento_mod.visibile(conn, riga["source_id"], gruppi):
            trovati[riga["documento"]] = riga["source_id"]
    for r in righe:
        if not r.get("source_id"):
            r["source_id"] = trovati.get(r.get("documento"), "")
    return righe


def _pezzi_del_modello(messaggi, max_tokens=2500):
    """I pezzi di testo del modello, man mano che arrivano."""
    for riga in modello.stream(messaggi, max_tokens=max_tokens, temperature=0.0):
        if not riga.startswith("data: "):
            continue
        corpo = riga[6:].strip()
        if corpo == "[DONE]":
            break
        try:
            pezzo = json.loads(corpo)["choices"][0]["delta"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError):
            continue
        if pezzo:
            yield pezzo


def _a_pezzi(sostituisci):
    """Trasforma un flusso di testo sostituendo le citazioni «[[3]]» al volo.

    Serve allo streaming: la risposta esce mentre il modello la scrive, ma i
    segnaposti devono diventare collegamenti PRIMA di arrivare agli occhi di
    chi legge. Una citazione puo' arrivare spezzata fra due pezzi («[[» in
    uno, «3]]» nel successivo), quindi si trattiene la coda a partire da una
    parentesi aperta e la si rilascia quando e' completa.

    Torna una funzione `passa(pezzo, fine=False)` che restituisce il testo
    pronto da mandare.
    """
    resto = [""]

    def passa(pezzo: str = "", fine: bool = False) -> str:
        resto[0] += pezzo
        fuori = []
        while True:
            testo = resto[0]
            i = testo.find("[[")
            if i < 0:
                # Niente citazioni aperte: si trattiene solo una eventuale
                # «[» finale, che potrebbe essere l'inizio della prossima.
                taglio = len(testo) - 1 if (testo.endswith("[") and not fine) else len(testo)
                fuori.append(testo[:taglio])
                resto[0] = testo[taglio:]
                break
            j = testo.find("]]", i)
            if j < 0:
                fuori.append(testo[:i])
                resto[0] = testo[i:]
                if fine:                     # mai chiusa: si lascia com'e'
                    fuori.append(resto[0])
                    resto[0] = ""
                break
            fuori.append(testo[:i] + sostituisci(testo[i:j + 2]))
            resto[0] = testo[j + 2:]
        return "".join(fuori)

    return passa


def _nota_verifica(etichette, citate) -> str:
    """La riga sullo stato di verifica delle righe CITATE, scritta dal codice.

    Era un compito del redattore, e lo ha sbagliato due volte di fila nello
    stesso modo: «Tutte le informazioni sono state verificate» sotto un
    elenco in cui sei righe su venti erano confermate (1/10/2026, e di nuovo
    dopo un divieto esplicito nel prompt). E' il modo di sbagliare peggiore,
    perche' rassicura chi legge su un controllo che non c'e' stato.

    Lo stato di verifica e' PROVENIENZA, esattamente come il collegamento alla
    pagina e la coda delle fonti: cose che il codice sa con certezza e che
    infatti non sbaglia mai. Chiederle a un modello e' chiedergli di
    ricordarsi un dato che ha gia' in mano il programma.
    """
    quali = [etichette.get(n) for n in sorted(citate)]
    dubbie = [q for q in quali if q and q != "si"]
    if not quali or not dubbie:
        return ""
    if len(dubbie) == len(quali):
        return ("Nessuna delle voci qui sopra e' stata verificata una per una: "
                "sono risultati di ricerca grezzi.")
    return ("%d delle %d voci qui sopra non sono state verificate una per una."
            % (len(dubbie), len(quali)))


def collegatore(confermate, base: str, utente: str):
    """(sostituisci, coda) per trasformare «[[3]]» nel collegamento vero.

    Diviso in due pezzi perche' servono a due flussi: `rendi` lavora sul
    testo finito, lo streaming lo trasforma mentre scorre. La parte
    deterministica e' la stessa — e deve esserlo: il numero non si
    interpreta, si guarda. `confermate[2]` e' una riga del database, col suo
    documento, la sua pagina e il suo source_id. Non c'e' niente da
    indovinare, quindi non c'e' niente che possa essere indovinato male —
    mentre indovinando, il 30/09/2026, 4 citazioni su 5 finivano sul catalogo
    sbagliato e restavano senza collegamento.
    """
    ordine, numeri, citate = [], {}, set()

    def sostituisci(segnaposto: str) -> str:
        cifre = "".join(c for c in segnaposto if c.isdigit())
        if not cifre:
            return ""
        n = int(cifre)
        if not (1 <= n <= len(confermate)):
            return ""          # un numero che non esiste non diventa un link
        citate.add(n)
        r = confermate[n - 1]
        doc, sid, pagina = r.get("documento"), r.get("source_id"), r.get("page")
        if not doc or not sid:
            return ""
        if doc not in numeri:
            numeri[doc] = _apice(len(ordine) + 1)
            ordine.append([doc, sid, numeri[doc], set()])
        for voce in ordine:
            if voce[0] == doc and pagina is not None:
                voce[3].add(pagina)
        if pagina is None:
            return numeri[doc]
        url = documento_mod.firma_url(sid, doc, pagina, base, utente)
        return f"[pagina {pagina}]({url}){numeri[doc]}"

    def coda(etichette=None) -> str:
        nota = _nota_verifica(etichette or {}, citate)
        if not ordine:
            return ("\n\n> _" + nota + "_") if nota else ""
        voci = []
        for doc, sid, apice, pagine in ordine:
            url = documento_mod.firma_url(sid, doc, None, base, utente)
            elenco = ", ".join(str(p) for p in sorted(pagine))
            voci.append(f"{apice} [{doc}]({url})"
                        + (f" — pagine {elenco}" if elenco else ""))
        fuori = "\n\n---\n> _Fonti: " + " · ".join(voci) + "_"
        return fuori + (("\n> _" + nota + "_") if nota else "")

    return sostituisci, coda


def rendi(conn, risposta: str, confermate: list, base: str, utente: str,
          gruppi=(), etichette=None) -> str:
    """La risposta finita: citazioni collegate e coda delle fonti."""
    if not risposta or not confermate:
        return risposta
    confermate = _con_fonti(conn, confermate, list(gruppi))
    sostituisci, coda = collegatore(confermate, base, utente)
    testo = _CITAZIONE.sub(lambda m: sostituisci(m.group(0)), risposta)
    return testo + coda(etichette)


# ==========================================================================
# Il grafo: stato -> decisione -> mosse -> stato
# ==========================================================================
def _scheda(righe, numeri=None, verdetti=None, visto: bool = False) -> str:
    """Le righe numerate, come le vedono coordinatore, critico e redattore.

    Le colonne le sceglie il coordinatore, quindi non si puo' dare per
    scontato che ci siano `documento`, `page` e `descrizione`. Una riga di
    conteggio (`SELECT count(*) FROM documenti`) non ne ha nessuna: mostrata
    come le altre diventava «1. [?, pagina ?]» seguito dal vuoto, e il critico
    la scartava — non perche' non rispondesse, ma perche' non la VEDEVA
    («quanti cataloghi ci sono» restava senza risposta, il 30/09/2026)."""
    fuori = []
    for i, r in enumerate(righe):
        n = numeri[i] if numeri else i + 1
        capo = f"{n}."
        if verdetti and n in verdetti:
            capo += f" [{verdetti[n].upper()}]"
            # Il MOTIVO, non solo il verdetto. «0 confermate su 40» dice
            # «vicolo cieco»; «e' una pallina singola, non un kit» dice che il
            # problema e' il criterio, non le parole — informazione opposta, e
            # il critico la scriveva gia' senza che nessuno la leggesse.
            if r.get("_motivo"):
                capo += f" ({r['_motivo']})"
        if r.get("documento"):
            capo += f" [{r['documento']}, pagina {r.get('page', '?')}]"
        if visto and r.get("_visto"):
            capo += f" (guardata: {r['_visto']})"
        testo = r.get("descrizione") or r.get("content")
        if not testo:
            testo = "; ".join(f"{c}: {v}" for c, v in r.items()
                              if not str(c).startswith("_")
                              and c not in ("id", "source_id", "documento", "page")
                              and v is not None)
        fuori.append(f"{capo} {' '.join(str(testo).split())[:ASSAGGIO]}".strip())
    return "\n".join(fuori) or "(nessuna riga)"


def _nota(nome, dettagli, righe, t0) -> dict:
    return {"strumento": nome, "argomenti": dettagli,
            "righe": len(righe) if hasattr(righe, "__len__") else righe,
            "ms": int((time.monotonic() - t0) * 1000)}


class Stato(TypedDict):
    domanda: str
    storia: list
    conn: Any
    dsn: str
    gruppi: list
    aziende: list
    query_fatte: list         # le SELECT gia' eseguite in questo turno
    eseguite: int             # query andate davvero al database
    respinte: int             # query bocciate prima di partire (NON sono ricerche)
    riviste: int              # quante volte l'analista ha rigiudicato in questo turno
    impegnativa: bool         # l'analista dice se il coordinatore deve ragionare
    rispondibile: bool        # l'analista dice se si puo' rispondere cosi' com'e'
    ambito: str               # che domanda e', secondo l'analista
    manca: list               # cosa la domanda non dice (materia prima di `chiedi`)
    mosse: list               # le chiamate scelte dal coordinatore in questo giro
    esiti: list               # com'e' andata, in parole
    chiarimento: str          # la domanda che il coordinatore fa alla persona
    su_pezzo: Any             # se c'e', il redattore scrive in streaming
    base: str                 # per firmare i collegamenti durante lo streaming
    utente: str
    resa: bool                # la risposta e' gia' resa (citazioni e fonti)
    righe: list
    verdetti: dict
    confermate: list
    etichette: dict           # numero di riga -> si | forse | non verificata
    risposta: str
    passi: int
    traccia: Annotated[list, operator.add]


def _dopo_mosse(stato) -> str:
    """Dopo le mosse si torna a decidere, ma se i dati hanno SMENTITO la
    previsione dell'analista si ripassa da lui.

    Difficolta' e rispondibilita' le aveva giudicate prima di vedere un solo
    dato. Se la ricerca torna vuota, o il critico non conferma niente, quel
    giudizio poggia su informazioni che non valgono piu'. Il codice non
    rigiudica: si accorge che e' comparsa una prova nuova e rimanda al
    giudice — come gia' succede a una riga appena guardata col VLM, che torna
    da verificare. Una volta sola per turno.
    """
    if stato.get("riviste", 0):
        return "coordinatore"
    vuoto = stato.get("eseguite", 0) > 0 and not (stato.get("righe") or [])
    bocciate = bool(stato.get("verdetti")) and not any(
        v == "si" for v in (stato.get("verdetti") or {}).values())
    muto = stato.get("respinte", 0) > 0 and stato.get("eseguite", 0) == 0
    return "analista" if (vuoto or bocciate or muto) else "coordinatore"


def _prossimo(stato) -> str:
    """L'UNICO bivio, e non decide niente: legge la mossa gia' scelta.

    Il tetto non e' piu' qui. Stava qui, e scartava in silenzio la mossa che
    il coordinatore aveva appena scelto. Adesso all'ultimo giro gli si offrono
    solo le mosse che chiudono: il limite si vede prima di scegliere, invece
    di cancellare la scelta dopo.
    """
    chiude = {"rispondi", "chiedi"}
    return "redattore" if any(n in chiude for n, _ in stato.get("mosse") or []) \
        else "mosse"


_grafo = StateGraph(Stato)
_grafo.add_node("analista", _nodo_analista)
_grafo.add_node("coordinatore", _nodo_coordinatore)
_grafo.add_node("mosse", _nodo_mosse)
_grafo.add_node("redattore", _nodo_redattore)
# L'analista gira una volta e basta, e NON e' un pezzo di catena: non ha un
# bivio, non instrada, aggiunge due righe allo stato. Un nodo senza rami non
# decide niente — e' un altro paio d'occhi, non un vigile.
_grafo.add_edge(START, "analista")
_grafo.add_edge("analista", "coordinatore")
_grafo.add_conditional_edges("coordinatore", _prossimo,
                             {"mosse": "mosse", "redattore": "redattore"})
_grafo.add_conditional_edges("mosse", _dopo_mosse,
                             {"analista": "analista",
                              "coordinatore": "coordinatore"})
_grafo.add_edge("redattore", END)
_compilato = _grafo.compile()


def cerca(conn, domanda: str, gruppi: list, storia: list = None,
          su_pezzo=None, base: str = "", utente: str = ""):
    """(confermate, risposta con [[n]], traccia).

    Stessa forma di `agente.cerca`, cosi' il chiamante non deve sapere quale
    flusso ha risposto. Il rendering delle citazioni resta fuori (`rendi`):
    serve la base URL e la persona, che qui non c'entrano.
    """
    # La domanda di ADESSO deve essere l'ultimo messaggio, sempre. Il
    # chiamante di solito passa la conversazione intera (che la contiene gia'
    # in fondo), ma non e' garantito: chi passa i soli turni precedenti lascia
    # la storia che finisce con la risposta dell'assistente, e gli agenti
    # analizzano la domanda di PRIMA senza che niente lo segnali. E' successo
    # in una simulazione (1/10/2026) e per un po' e' sembrato un difetto del
    # modello. Un contratto implicito che inganna un chiamante e' un difetto
    # del codice: qui si garantisce, invece di sperarci.
    conversazione = [m for m in (storia or [])
                     if m.get("role") in ("user", "assistant")]
    ultimo = conversazione[-1] if conversazione else None
    if not (ultimo and ultimo.get("role") == "user"
            and (ultimo.get("content") or "").strip() == domanda.strip()):
        conversazione.append({"role": "user", "content": domanda})
    stato = _compilato.invoke({
        "domanda": domanda,
        "storia": conversazione,
        "conn": conn, "dsn": os.environ["DATABASE_URL"],
        "gruppi": gruppi, "aziende": identita.aziende(gruppi),
        "query_fatte": [], "eseguite": 0, "respinte": 0,
        "riviste": 0, "impegnativa": True, "rispondibile": True,
        "ambito": "", "manca": [],
        "mosse": [], "esiti": [], "chiarimento": "",
        "su_pezzo": su_pezzo, "base": base, "utente": utente, "resa": False,
        "righe": [], "verdetti": {},
        "confermate": [], "etichette": {}, "risposta": "", "passi": 0,
        "traccia": [],
    }, {"recursion_limit": MAX_PASSI * 3 + 10})
    verdetti = stato.get("verdetti") or {}
    return stato.get("confermate") or [], (stato.get("risposta") or "").strip(), {
        "etichette": stato.get("etichette") or {},
        "resa": bool(stato.get("resa")),
        "strumenti": stato.get("traccia") or [],
        "ricerca": "ambito=%s | manca=%s | query=%dok/%dko | trovate=%d | confermate=%d | passi=%d" % (
            " ".join((stato.get("ambito") or "-").split())[:110],
            "; ".join(stato.get("manca") or []) or "-",
            stato.get("eseguite", 0), stato.get("respinte", 0),
            len(stato.get("righe") or []),
            sum(1 for v in verdetti.values() if v == "si"),
            stato.get("passi", 0)),
    }


def _prova():
    """Le regole che non chiamano ne' il database ne' i modelli."""
    # Il bivio LEGGE la scelta del coordinatore, non la prende.
    assert _prossimo({"mosse": [("cerca", {})], "passi": 1}) == "mosse"
    assert _prossimo({"mosse": [("rispondi", {})], "passi": 1}) == "redattore"
    # All'ultimo giro il tetto NON scarta la scelta: gli si offrono solo le
    # mosse che chiudono, quindi `cerca` non e' nemmeno sul tavolo.
    ultime = {s["function"]["name"] for s in _strumenti_del_coordinatore(True)}
    assert ultime == {"rispondi", "chiedi"}, ultime
    # Senza l'analista di mezzo, al coordinatore sono offerte TUTTE le
    # mosse: niente cancelli del codice, ne' su `rispondi` ne' su `chiedi`.
    # A dirgli che non ha ancora cercato ci pensa lo stato, a parole.
    libero = {s["function"]["name"] for s in _strumenti_del_coordinatore()}
    assert {"cerca", "verifica", "rispondi", "chiedi"} <= libero, libero
    # Non si chiede prima di aver guardato: senza, un verdetto «non
    # rispondibile» faceva chiedere al primo giro, con zero query.
    acerbo = {s["function"]["name"] for s in _strumenti_del_coordinatore(mai_eseguito=True)}
    assert "chiedi" not in acerbo and "cerca" in acerbo, acerbo
    assert "rispondi" in acerbo, "un saluto si chiude subito"
    # Ma se non puo' ne' rispondere ne' chiedere, all'ultima mossa deve
    # poter chiudere: altrimenti il turno resta appeso.
    stretta = {s["function"]["name"] for s in _strumenti_del_coordinatore(
        ultima=True, mai_eseguito=True, non_rispondibile=True)}
    assert stretta == {"chiedi"}, stretta
    # Ma chi ha PROVATO a cercare e non c'e' mai riuscito non puo' dire «non
    # ho trovato»: non ha guardato niente. Misurato togliendolo — senza, lo
    # diceva lo stesso. `chiedi` pero' resta, quindi non si blocca.
    cieco = {s["function"]["name"] for s in _strumenti_del_coordinatore(mai_cercato=True)}
    assert "rispondi" not in cieco, cieco
    assert {"chiedi", "cerca"} <= cieco, cieco
    # Domanda troppo vaga secondo l'analista: si puo' cercare e si puo'
    # chiedere, ma non si puo' chiudere con una risposta. All'ultima mossa
    # pero' il turno deve potersi chiudere lo stesso.
    # Domanda troppo generica: si toglie solo `rispondi` — non si AFFERMA
    # una risposta che l'analista ha giudicato impossibile. Cercare resta
    # permesso: la strategia la sceglie il coordinatore, informato dallo
    # stato, non il codice.
    vago = {s["function"]["name"] for s in _strumenti_del_coordinatore(
        non_rispondibile=True)}
    assert "rispondi" not in vago, vago
    assert {"cerca", "proponi", "chiedi"} <= vago, vago
    detto = _stato_a_parole({"domanda": "d", "righe": [], "verdetti": {},
                             "passi": 0, "rispondibile": False})
    assert "Non spendere una ricerca" in detto, detto
    # E il verdetto deve ARRIVARGLI a parole, o non puo' tenerne conto.
    detto = _stato_a_parole({"domanda": "d", "righe": [], "verdetti": {},
                             "passi": 0, "rispondibile": False})
    assert "non si puo' rispondere" in detto, detto
    # La nota di verifica la scrive il CODICE, perche' il redattore due volte
    # ha scritto «tutte verificate» sotto righe che non lo erano.
    assert _nota_verifica({1: "si", 2: "si"}, {1, 2}) == ""
    assert "2 delle 3" in _nota_verifica(
        {1: "si", 2: "forse", 3: "non verificata"}, {1, 2, 3})
    assert "Nessuna" in _nota_verifica({1: "non verificata"}, {1})
    # Solo le righe CITATE contano: una scartata che il redattore non nomina
    # non deve far comparire un avviso.
    assert _nota_verifica({1: "si", 2: "non verificata"}, {1}) == ""
    # Lo streaming: una citazione spezzata fra due pezzi non deve uscire a
    # metà, e il testo normale non deve restare bloccato nel buffer.
    passa = _a_pezzi(lambda seg: "<%s>" % seg.strip("[]"))
    assert passa("ecco a pag") == "ecco a pag"
    assert passa("ina [") == "ina "            # trattiene la parentesi
    assert passa("[3") == ""                   # citazione aperta: aspetta
    assert passa("]] e poi") == "<3> e poi"    # chiusa: esce tutta insieme
    assert passa("", fine=True) == ""
    # Una parentesi singola non e' una citazione: passa, non si trattiene.
    passa2 = _a_pezzi(lambda seg: "X")
    assert passa2("vedi [nota") == "vedi [nota"
    assert passa2(" a parte", fine=True) == " a parte"
    # Una citazione mai chiusa esce com'e', non sparisce.
    passa3 = _a_pezzi(lambda seg: "X")
    assert passa3("tronca [[7") == "tronca "
    assert passa3("", fine=True) == "[[7"
    # La stessa query due volte nello stesso turno: si dice, invece di
    # rieseguirla. Quattro SELECT identiche sono costate un turno intero.
    r, t, e = _mossa_cerca("dsn-finto", [], [], {"sql": "SELECT 1 FROM immagini;"},
                           "d", gia_viste={"select 1 from immagini"})
    assert e == "ripetuta" and "gia' eseguita" in t, (e, t)
    assert r == []
    assert "rispondi" in {s["function"]["name"] for s in _strumenti_del_coordinatore()}
    # Il motivo del critico deve arrivare al coordinatore, non solo il verdetto.
    con_motivo = _scheda([{"descrizione": "x", "_motivo": "e' un vaso"}],
                         verdetti={1: "no"})
    assert con_motivo == "1. [NO] (e' un vaso) x", con_motivo
    # Il redattore non riceve MAI righe promosse: quello che nessuno ha
    # guardato arriva come «non verificata», i `forse` restano `forse`, e solo
    # cio' che il critico ha scartato non passa. Prima il codice promuoveva a
    # confermate venti righe mai viste, e una risposta su tre usciva senza
    # controllo senza che si vedesse (1/10/2026).
    # La domanda di adesso deve finire in fondo alla conversazione anche se
    # il chiamante ha passato i soli turni precedenti: senza, gli agenti
    # analizzano la domanda del turno prima e nessuno se ne accorge.
    def _coda(storia, domanda):
        conv = [m for m in (storia or []) if m.get("role") in ("user", "assistant")]
        u = conv[-1] if conv else None
        if not (u and u.get("role") == "user"
                and (u.get("content") or "").strip() == domanda.strip()):
            conv.append({"role": "user", "content": domanda})
        return conv
    prec = [{"role": "user", "content": "prima"},
            {"role": "assistant", "content": "risposta"}]
    assert _coda(prec, "adesso")[-1] == {"role": "user", "content": "adesso"}
    gia = prec + [{"role": "user", "content": "adesso"}]
    assert len(_coda(gia, "adesso")) == 3, "non deve duplicarla se c'e' gia'"
    assert _coda([], "sola")[-1]["content"] == "sola"

    # Il critico si spezza in gruppi solo se il SERVER del modello serve piu'
    # richieste insieme, e in tanti gruppi quanti sono i suoi slot. Con uno
    # solo fa una chiamata sola: spezzare, li', costa di piu'.
    visti = []
    def _finto(stato, righe, motivo, gruppo):
        visti.append([n for n, _ in gruppo])
        return {n: "si" for n, _ in gruppo}
    vero_giudica, vero_slot = globals()["_giudica"], modello.slot
    globals()["_giudica"] = _finto
    try:
        tante = [{"descrizione": str(i)} for i in range(30)]
        modello.slot = lambda: 1
        _critico({"verdetti": {}}, tante, "")
        assert len(visti) == 1, "un solo slot: una chiamata sola"
        visti.clear()
        modello.slot = lambda: 3
        esiti = _critico({"verdetti": {}}, tante, "")
        assert len(visti) == 3, visti
        assert sorted(n for g in visti for n in g) == list(range(1, 31))
        assert len(esiti) == 30
        visti.clear()
        # Le righe gia' giudicate non si rigiudicano, qualunque sia lo slot.
        _critico({"verdetti": {1: "no", 2: "si"}},
                 [{"descrizione": str(i)} for i in range(5)], "")
        assert visti == [[3, 4, 5]], visti
    finally:
        globals()["_giudica"], modello.slot = vero_giudica, vero_slot

    quattro = [{"descrizione": c} for c in "abcd"]
    tenute, etich = _da_consegnare(quattro, {1: "si", 2: "no", 3: "forse"})
    assert [r["descrizione"] for r in tenute] == ["a", "c", "d"], tenute
    assert etich == {1: "si", 2: "forse", 3: "non verificata"}, etich
    # Nessuna verifica chiesta: le righe passano, ma NON come confermate.
    tenute, etich = _da_consegnare(quattro, {})
    assert len(tenute) == 4 and set(etich.values()) == {"non verificata"}, etich
    # Scartate tutte dal critico: al redattore non arriva niente da spacciare.
    tenute, etich = _da_consegnare(quattro, {n: "no" for n in range(1, 5)})
    assert tenute == [] and etich == {}
    # Piu' ricerche nello stesso giro sono ammesse: e' il caso del parallelo.
    assert _prossimo({"mosse": [("cerca", {}), ("cerca", {})], "passi": 1}) == "mosse"
    # Il dubbio chiude il giro come una risposta: la domanda alla persona e'
    # una mossa, non una resa.
    assert _prossimo({"mosse": [("chiedi", {})], "passi": 1}) == "redattore"
    # La valvola: quando i dati smentiscono la previsione dell'analista, si
    # ripassa da lui — una volta sola, e solo se c'e' davvero una smentita.
    assert _dopo_mosse({"eseguite": 1, "righe": [{"a": 1}],
                        "verdetti": {1: "si"}}) == "coordinatore"
    assert _dopo_mosse({"eseguite": 1, "righe": []}) == "analista"
    assert _dopo_mosse({"eseguite": 1, "righe": [{"a": 1}],
                        "verdetti": {1: "no"}}) == "analista"
    assert _dopo_mosse({"respinte": 3, "eseguite": 0}) == "analista"
    assert _dopo_mosse({"eseguite": 1, "righe": [], "riviste": 1}) == "coordinatore"
    # Niente verdetti ancora: non e' una smentita, e' solo presto.
    assert _dopo_mosse({"eseguite": 1, "righe": [{"a": 1}]}) == "coordinatore"
    # `guarda` si offre solo se l'osservatore c'e': non si offre uno strumento
    # che non esiste.
    nomi = {s["function"]["name"] for s in _strumenti_del_coordinatore()}
    assert ("guarda" in nomi) == OSSERVATORE, nomi
    assert {"cerca", "verifica", "rispondi", "chiedi"} <= nomi
    # Una citazione fuori intervallo o senza source_id sparisce, non diventa un
    # link rotto e non inventa una fonte.
    class _C:
        def execute(self, *a, **k):
            return []
    righe = [{"documento": "x.pdf", "page": 3, "source_id": ""}]
    assert rendi(_C(), "trovato [[1]] e [[9]]", righe, "https://b", "u") == "trovato  e "
    assert _apice(1) == "¹" and _apice(12) == "¹²"
    # La scheda: numerazione, verdetti, e le righe senza le colonne solite.
    assert _scheda([]) == "(nessuna riga)"
    assert _scheda([{"documento": "a.pdf", "page": 2, "descrizione": "ciao"}]) \
        == "1. [a.pdf, pagina 2] ciao"
    assert _scheda([{"count": 14}]) == "1. count: 14"
    assert _scheda([{"descrizione": "x"}], numeri=[7]) == "7. x"
    assert _scheda([{"descrizione": "x"}], verdetti={1: "si"}) == "1. [SI] x"
    # Lo stato a parole dice sempre quante mosse restano: il tetto e' noto al
    # coordinatore, non gli casca addosso.
    testo = _stato_a_parole({"domanda": "d", "righe": [], "verdetti": {}, "passi": 0})
    assert "Non hai ancora trovato niente" in testo and "mosse" in testo
    # Le spiegazioni dei rifiuti devono ARRIVARE al coordinatore: le
    # calcolavamo e le buttavamo via, e lui riscriveva la stessa query
    # respinta senza sapere perche'.
    con_esiti = _stato_a_parole({"domanda": "d", "righe": [], "verdetti": {},
                                 "passi": 0, "respinte": 1,
                                 "esiti": ["cerca [palline]: QUERY RESPINTA: "
                                           "scrivi UNA parola per condizione"]})
    assert "UNA parola per condizione" in con_esiti, con_esiti
    # Il giudizio dell'analista deve ARRIVARE al coordinatore, altrimenti e' un
    # nodo che gira per niente: e' l'unico modo che ha di sapere che la domanda
    # era mal posta, perche' lui vede solo i risultati.
    # Scartate tutte: lo stato deve dire che quelle righe sono vocabolario,
    # non un vicolo cieco. E' il caso in cui il coordinatore si arrendeva con
    # la parola giusta davanti agli occhi.
    tutte_no = _stato_a_parole({"domanda": "d", "passi": 0,
                                "righe": [{"descrizione": "Christmas ornament ball"}],
                                "verdetti": {1: "no"}})
    assert "vocabolario" in tutte_no, tutte_no
    # Ma se qualcosa e' confermato, quel paragrafo NON deve comparire.
    con_si = _stato_a_parole({"domanda": "d", "passi": 0,
                              "righe": [{"descrizione": "x"}], "verdetti": {1: "si"}})
    assert "vocabolario" not in con_si, con_si
    con_analisi = _stato_a_parole({"domanda": "d", "righe": [], "verdetti": {},
                                   "passi": 0, "ambito": "un prodotto",
                                   "manca": ["l'oggetto"]})
    assert "un prodotto" in con_analisi and "l'oggetto" in con_analisi
    assert "chiedi" in con_analisi, "il coordinatore deve sapere a cosa serve"
    ultimo = _stato_a_parole({"domanda": "d", "righe": [], "verdetti": {},
                              "passi": MAX_PASSI - 1})
    assert "ULTIMA MOSSA" in ultimo
    print("grafo: ok")


if __name__ == "__main__":
    _prova()
