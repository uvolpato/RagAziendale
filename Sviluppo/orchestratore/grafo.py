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
P_ANALISTA = """<ruolo>
Leggi una domanda fatta a un archivio aziendale e la giudichi. Non cerchi e non rispondi: dici che domanda e'.
</ruolo>

<archivio>
- `immagini`: le foto dei cataloghi, una riga per foto, con descritto oggetto, materiale, forma, colori. Qui stanno i PRODOTTI.
- `chunks`: il testo dei documenti nelle lingue originali. Nomi commerciali, codici, procedure, manuali.
- `documenti`: l'elenco delle fonti. Quanti documenti, di che tipo, quante pagine.
</archivio>

<ambito>
Scrivi per esteso cosa vuole la persona ADESSO, e dove sta la risposta. Una o due frasi.

Scrivi la RICHIESTA, non la sua categoria: chi legge dopo di te deve poter scrivere la ricerca avendo solo la tua frase. «Un prodotto con due attributi, sta nelle foto» non gli serve a niente.

Se e' un seguito, la richiesta e' la SOMMA di quello che e' stato detto. Ogni turno aggiunge un pezzo, e un pezzo nuovo sostituisce solo quello dello STESSO TIPO: un colore sostituisce il colore, un oggetto sostituisce l'oggetto, tutto il resto resta dov'e'.

- dopo «hai palline di Natale rosse?», «niente gialle?» e' palline di Natale GIALLE: cambia il colore, l'oggetto resta.
- dopo «hai qualcosa di blu?», «dei nastri» e' nastri BLU: arriva l'oggetto, il colore resta.

Nessuno ripete quello che ha gia' detto. Un pezzo che e' nel discorso c'e' anche se la frase di adesso non lo nomina — e non e' un pezzo che manca.
I pezzi fissati sono quelli che ha detto LA PERSONA. Quello che c'era nella risposta di prima — in che catalogo stava, com'era fatto l'articolo — descriveva quello che si era trovato, non quello che lei vuole, e non e' un pezzo fissato. Dopo «hai palline di Natale rosse?» e una risposta che diceva «rosse lucide nel catalogo Packara», un «e di blu invece?» e' palline di Natale BLU: non lucide, e non solo in quel catalogo.
</ambito>

<manca>
Quello che la domanda non dice e che cambierebbe la risposta per trovare l'oggetto. Al massimo due cose, scritte come le diresti a voce.

Se nel discorso quel pezzo non c'e', manca: su una domanda che non dice nemmeno che oggetto e' — «hai qualcosa di blu» — l'oggetto manca.

La prova per riempire questo campo: potrebbe rispondertelo la persona? Se no, non manca alla domanda e il campo resta vuoto. 
ATTENZIONE: Non inventare mancanze commerciali come "budget", "prezzo", "destinatario" o "gusti". L'archivio contiene solo caratteristiche fisiche. Se manca l'oggetto (es. "regalo per mamma"), l'unica cosa che manca davvero e' "il tipo di oggetto desiderato".
</manca>

<rispondibile>
Chiedi a te stesso una cosa sola: **con quello che so adesso, posso scrivere una ricerca che torni un insieme coerente di articoli o chiudere la conversazione?**

Rispondi SI nei seguenti casi (la ricerca o la risposta si possono fare subito):
- Un saluto o una chiacchiera: SI, non c'e' niente da cercare, si puo' rispondere direttamente.
- DOCUMENTO o TESTO: «cosa dice la procedura sui resi». Sempre SI.
- DATI o ARCHIVIO: un conteggio, un totale, l'elenco dei documenti. Sempre SI.
- CATALOGO con OGGETTO NOTO: Serve sapere CHE TIPO DI OGGETTO (un nastro, un vaso, una pallina, una candela). «Ho bisogno di sassi rossi» e' SI.
- Un seguito che eredita l'oggetto dal discorso di prima: SI. L'oggetto c'e' gia'.

Rispondi NO esclusivamente in questo caso (la mossa e' mostrare delle scelte, senza cercare):
- CATALOGO SENZA OGGETTO: Quando non si nomina il tipo di oggetto ma solo un colore isolato («qualcosa di blu»), un tema («cosa avete di sportivo») o un'occasione/regalo («un regalo per il compleanno di mia mamma», «qualcosa per San Valentino»). L'occasione lascia dentro tutto l'archivio, quindi e' NO.

Queste domande NON sono impegnative: hai gia' capito tutto, e la mossa — mostrare le scelte — non richiede di pensarci su.
</rispondibile>

<impegnativa>
Serve a chi cerca dopo di te per decidere la strategia di ricerca.

Se hai appena giudicato la domanda come NON rispondibile (rispondibile: NO), allora impegnativa e' tassativamente FALSE. Hai gia' capito tutto: la mossa e' mostrare delle scelte e non richiede di pensarci su.

In caso di rispondibile: SI, valuta invece cosi':
- IMPEGNATIVA: quando una parola va indovinata (il catalogo la chiama in un altro modo, es. «Kit per l'albero di Natale») o quando la risposta sta in due posti e va messa insieme. Nel dubbio, se e' rispondibile SI, considerala impegnativa.
- NON impegnativa: un saluto, un dato sull'archivio, o un oggetto con i suoi attributi detto chiaro («nastri bianchi con cuori rossi»).
</impegnativa>

<i_pezzi_della_richiesta>
Oltre alla frase, consegni la richiesta SCOMPOSTA: `oggetto`, `attributi`, `dove`. La frase serve a chi legge, i pezzi servono a chi lavora.

Perche' esistono: dopo di te, tre agenti diversi devono sapere che cosa si cerca — chi scrive la query, chi controlla le righe una per una, chi costruisce le scelte da offrire. Se glielo lasci dentro una frase, ognuno la rilegge e ne ricava una cosa un po' diversa. Scomponila tu, una volta, e sono d'accordo tutti e tre.

`oggetto` e' IL TIPO DI COSA, al singolare e nudo: «nastro», non «nastri bianchi». Se la domanda non nomina una cosa — un colore da solo, un'occasione, un saluto, una domanda su un testo — resta vuoto, ed e' la stessa cosa che stai dicendo con `rispondibile`.

`attributi` sono le caratteristiche che quell'oggetto deve avere, una per voce, con il loro genere: «bianco» e' un colore, «cuori rossi» e' un motivo, «lucido» e' altro. Il genere conta perche' a valle non tutte le caratteristiche si cercano allo stesso modo.

Ci va solo quello che ha chiesto LEI. Se una risposta di prima diceva «rosse lucide nel catalogo Packara», «lucide» non e' un attributo: era la descrizione di quello che si era trovato.

`dove` e' la tabella: `immagini` per i prodotti (inclusi i casi NON rispondibili come occasioni, regali o colori isolati che mirano al catalogo), `chunks` per il testo dei documenti, `documenti` per le domande sull'archivio. Usa `nessuno` RIGOROSAMENTE solo per saluti, insulti o chiacchiere che non hanno alcuna attinenza con i prodotti o l'azienda.
</i_pezzi_della_richiesta>

<consegna>
Con lo strumento `analisi`.
</consegna>"""


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
                                 "risposta. Vuoto se la domanda e' completa."},
        "oggetto": {"type": "string",
                    "description": "IL TIPO DI COSA cercata, al singolare e "
                                   "senza aggettivi: «nastro», «vaso», "
                                   "«pallina di Natale». Vuoto se la domanda "
                                   "non nomina un oggetto (un saluto, un "
                                   "colore da solo, un'occasione, una "
                                   "domanda su un testo o sull'archivio)."},
        "attributi": {"type": "array", "items": {"type": "object", "properties": {
            "valore": {"type": "string",
                       "description": "La caratteristica come l'ha detta lei: "
                                      "«bianco», «cuori rossi», «lucido»."},
            "tipo": {"type": "string",
                     "enum": ["colore", "motivo", "materiale", "forma",
                              "misura", "altro"],
                     "description": "Che genere di caratteristica e'."}},
            "required": ["valore", "tipo"]},
            "description": "Le caratteristiche che l'oggetto deve avere, una "
                           "per voce. Solo quelle chieste DA LEI, nel "
                           "discorso: non quelle che comparivano in una "
                           "risposta di prima. Vuoto se non ne ha chieste."},
        "dove": {"type": "string",
                 "enum": ["immagini", "chunks", "documenti", "nessuno"],
                 "description": "La tabella dove sta la risposta. «nessuno» "
                                "per un saluto o una chiacchiera."}},
        "required": ["ambito", "manca", "impegnativa", "rispondibile",
                     "oggetto", "attributi", "dove"]}}}]


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
    oggetto, attributi, dove = "", [], ""
    for nome, arg in _decide(messaggi, I_ANALISI):
        ambito = str(arg.get("ambito") or "").strip()
        manca = [str(m).strip() for m in (arg.get("manca") or []) if str(m).strip()][:2]
        impegnativa = bool(arg.get("impegnativa", True))
        rispondibile = bool(arg.get("rispondibile", True))
        # I PEZZI. Li produceva gia' e li buttavamo: la scomposizione tornava
        # dentro la tool-call e poi restava fuori dallo stato, cosi' il
        # coordinatore, il critico e la guida se la rifacevano ognuno per
        # conto suo leggendo la frase.
        oggetto = str(arg.get("oggetto") or "").strip()
        attributi = [{"valore": str(a.get("valore") or "").strip(),
                      "tipo": str(a.get("tipo") or "altro").strip()}
                     for a in (arg.get("attributi") or [])
                     if isinstance(a, dict) and str(a.get("valore") or "").strip()]
        dove = str(arg.get("dove") or "").strip()
    return {"ambito": ambito, "manca": manca, "impegnativa": impegnativa,
            "rispondibile": rispondibile,
            "oggetto": oggetto, "attributi": attributi, "dove": dove,
            "riviste": stato.get("riviste", 0) + 1,
            "traccia": [_nota("analista", {"manca": manca,
                                           "impegnativa": impegnativa,
                                           "rispondibile": rispondibile}, 0, t0)]}


# ==========================================================================
# IL COORDINATORE — l'unico che decide
# ==========================================================================
P_COORDINATORE = """<ruolo>
Conduci la ricerca in un archivio di cataloghi e documenti. Non scrivi la risposta: decidi la prossima mossa e la fai fare a chi sa farla. A ogni giro hai davanti la conversazione intera e lo stato di quello che hai fatto finora.
</ruolo>

<usa_i_pezzi_dell_analista>
Nello stato trovi l'analisi della richiesta gia' pronta, calcolata dall'analista. Non devi ricostruire la storia dei turni precedenti: fidati dei campi estratti.

- `rispondibile`: se e' FALSE, salta immediatamente al blocco <se_la_domanda_non_e_rispondibile>.
- `dove`: e' la tabella di destinazione indicata dall'analista. Cerca solo li'.
- `oggetto` e `attributi`: sono la tua bussola per costruire la query, ma rispettando RIGOROSAMENTE le regole di MAPPA_OPERATORI per non svuotare la ricerca:
  * l'oggetto e i colori vanno nel WHERE (con `~*`);
  * le finiture, i motivi o le occasioni NON vanno nel WHERE: si passano solo dentro `ORDER BY SIMILE()`, per portare in cima i candidati migliori senza escludere i dati parziali.
</usa_i_pezzi_dell_analista>

<dove_si_cerca>
- `immagini`: le foto dei cataloghi. Qui sta il PRODOTTO.
- `chunks`: il testo dei documenti, nelle lingue originali. Nomi commerciali, codici, prezzi, procedure.
- `documenti`: l'elenco delle fonti, per le domande sull'archivio stesso.
</dove_si_cerca>

<le_tue_mosse>
- `cerca` esegue una query che scrivi tu. Puoi chiamarla piu' volte nello stesso giro e partono tutte insieme: se ci sono due strade indipendenti, aprile subito entrambe. Una ricerca costa cinquanta millisecondi, un giro in piu' ne costa quattordicimila.
  **Ordina sempre per pertinenza e metti un limite**: `ORDER BY SIMILE('la richiesta in italiano') LIMIT 20`. Senza, torni quaranta righe in ordine qualsiasi: chi le verifica le legge tutte una per una, e quelle che arrivano a chi ha chiesto non sono le piu' vicine a quello che voleva. Un `~*` coi confini di parola che taglia il grosso, piu' `SIMILE` che ordina il resto: e' la combinazione che rende di piu'.
- `verifica` fa leggere le righe trovate una per una e dice quali rispondono davvero.
- `guarda` riapre la foto vera, per le righe su cui la descrizione non basta a decidere.
- `proponi` costruisce le scelte da mostrare alla persona, leggendole nell'archivio. Gli dici TU cosa cercare, con le parole con cui la chiamerebbe lei — «oggetti sportivi», «regali per una mamma» — come dentro `SIMILE()`. Si chiama una volta per giro, e il risultato resta nello stato.
- `chiedi` si ferma e mostra quelle scelte alla persona. E' una mossa come le altre, non una resa.
- `rispondi` chiude e manda a chi scrive tutte le righe raccolte, con l'etichetta che hanno adesso.
</le_tue_mosse>

<verifica_prima_di_rispondere>
Trovare e controllare sono due lavori, e `cerca` fa solo il primo: venti righe che contengono quelle parole non sono venti righe che rispondono.

Prima di `rispondi`, guarda lo stato: se ci sono righe senza verdetto, la mossa e' `verifica`. Le righe non verificate arrivano a chi scrive marcate cosi', e chi legge se lo vede scritto.

L'unico caso in cui si salta la verifica e' quando non c'e' niente da verificare: un saluto, o un dato secco che la query ha gia' dato per intero.
</verifica_prima_di_rispondere>

<se_la_domanda_non_e_rispondibile>
Quando te lo dicono, non cercare: cercare darebbe un elenco tenuto insieme da una caratteristica sola, e due minuti dopo saresti comunque a chiedere.

Le mosse sono due, in quest'ordine: `proponi`, poi `chiedi`. Niente `cerca`.

`chiedi` non si chiama a mani vuote: senza righe e senza scelte da mostrare, la domanda te la inventi, e ti esce una domanda generica che manda la persona a cercare qualcosa che non c'e'. Prima `proponi`.
</se_la_domanda_non_e_rispondibile>

<sul_chiedere>
Una domanda buona si distingue da una cattiva per una cosa: hai guardato prima di farla?
- Chiedere senza aver guardato fa fare il lavoro alla persona.
- Chiedere avendo davanti quello che hai trovato mette lei a scegliere fra cose vere.
- Se le opzioni sono poche, non chiedere: mostrale.
Una cosa per volta, concreta.
</sul_chiedere>

<come_si_conduce>
- Una domanda di prodotto finisce quando hai le pagine in cui l'articolo ha davvero tutto quello che e' stato chiesto, non una parte.
- Una domanda su un documento sta nel testo, e finisce quando hai le pagine da riassumere.
- Una domanda sull'archivio e' finita appena hai il dato.
- Un saluto o una chiacchiera: `rispondi` subito.
</come_si_conduce>

<quando_ti_fermi>
Appena hai righe confermate che rispondono, `rispondi`. Non inseguire varianti.

Se hai cercato in piu' modi e non c'e' niente, `rispondi` lo stesso: una risposta onesta vale piu' di un'altra ricerca.
</quando_ti_fermi>"""


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
                "cerca": {"type": "string",
                          "description": "LA COSA da cercare nell'archivio, in "
                                         "italiano, con le parole con cui la "
                                         "chiamerebbe la persona: «oggetti "
                                         "sportivi», «regali per una mamma», "
                                         "«nastri blu». Due o tre parole, come "
                                         "dentro SIMILE(). Non una frase su di "
                                         "lei: il campione lo trova la "
                                         "somiglianza, e «la persona sta "
                                         "cercando...» somiglia alle foto "
                                         "delle persone."},
                "motivo": {"type": "string",
                           "description": "Cosa c'e' da restringere."}},
                "required": ["cerca"]}}},
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


def _pezzi_a_parole(stato, con_tabella: bool = False) -> str:
    """La richiesta SCOMPOSTA, detta in prosa coi dati dentro.

    Non un dict crudo: una frase che dice dove stanno i dati veri, perche' il
    modello ragiona sulla prosa e poi va a prendere il valore. Una funzione
    sola perche' la leggono in due — il coordinatore nello stato e il critico
    nel suo messaggio — e due versioni della stessa frase sarebbero due
    versioni della stessa richiesta: e' il difetto da cui veniamo.
    """
    pezzi = []
    if stato.get("oggetto"):
        pezzi.append("l'oggetto cercato e' «%s»" % stato["oggetto"])
    attributi = [a for a in (stato.get("attributi") or [])
                 if isinstance(a, dict) and a.get("valore")]
    if attributi:
        pezzi.append("gli attributi chiesti sono %s" % ", ".join(
            "«%s» (%s)" % (a["valore"], a.get("tipo") or "altro") for a in attributi))
    if con_tabella and stato.get("dove") and stato["dove"] != "nessuno":
        pezzi.append("la tabella e' `%s`" % stato["dove"])
    return "; ".join(pezzi)


def _stato_a_parole(stato) -> str:
    """Lo stato come lo leggerebbe una persona. E' quello che il coordinatore
    ha davanti a ogni giro: non un riassunto scelto dal codice, ma tutto.

    LA REGOLA: qui dentro ci stanno i FATTI, la strategia sta nel prompt.

    Era mescolata. Accanto a ogni fatto il codice aggiungeva la mossa da
    fare — «non spendere una ricerca: `proponi` poi `chiedi`», «e' la
    materia prima di `chiedi`», «la mossa che resta e' `chiedi`». Sono
    decisioni, scritte da chi non ha letto la conversazione, e il
    coordinatore le subisce: su «quanto costano?» il verdetto sbagliato
    dell'analista («manca: il prezzo») arrivava con attaccato l'ordine di
    chiedere, e dopo 73 secondi la risposta era «posso cercare nel testo
    dei documenti» (2/10/2026).

    Dove la strategia serviva davvero sta nel prompt del coordinatore, che
    la legge una volta per tutte: il `proponi` che si chiama una volta
    sola, il «non cercare se la domanda non e' rispondibile». Li' e'
    un'istruzione che lui puo' pesare insieme a tutto il resto; qui era un
    cartello appeso a un dato."""
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
            "utile.")
    pezzi = _pezzi_a_parole(stato, con_tabella=True)
    if pezzi:
        parti.append("Chi ha letto la domanda l'ha anche SCOMPOSTA, mettendo "
                     "insieme tutti i turni: " + pezzi
                     + ". Sono questi i pezzi su cui si cerca.")
    if stato.get("manca"):
        parti.append("Chi ha letto la domanda dice che NON dice: "
                     + "; ".join(stato["manca"]) + ".")
    if stato.get("chiarimento"):
        # Il ventaglio e' un pezzo di stato DUREVOLE, come le righe e i
        # verdetti, e come loro va detto a ogni giro. Finiva solo negli
        # `esiti`, che raccontano l'ultima mossa e basta: al giro dopo era
        # sparito dalla vista, e il coordinatore rifaceva `proponi` credendo
        # di non averlo. Tre volte di fila sul compleanno (1/10/2026),
        # trentotto secondi. Stesso difetto di `esiti` e di `rispondibile`:
        # un dato che lo stato ha e non pronuncia.
        parti.append("IL VENTAGLIO E' GIA' PRONTO — l'hai costruito tu, e "
                     "non e' cambiato niente da allora:\n" + stato["chiarimento"])
    if not righe:
        parti.append("Non hai ancora trovato niente.")
    else:
        confermate = [n for n in verdetti if verdetti[n] == "si"]

        parti.append(
            f"Righe trovate: {len(righe)}. Controllate: {len(verdetti)} "
            f"(buone {len(confermate)}, "
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


def _mostra(stato, testo: str) -> None:
    """Fa vedere a chi aspetta cosa sta succedendo, mentre succede.

    Il ragionamento del coordinatore esce gia' in streaming, ma solo quando
    ragiona — e da quando le domande generiche sono «non impegnative» quello
    e' il caso raro. Risultato: venti o trenta secondi di schermo fermo, che
    e' peggio di un turno lento perche' sembra rotto.

    Non si inventa niente: si manda il `motivo` che l'agente ha scritto da
    se' quando ha scelto la mossa. Chi legge vede le sue parole, non le
    nostre, e nello stesso blocco pieghevole dove finisce il ragionamento.
    """
    su_pezzo = stato.get("su_pezzo")
    if callable(su_pezzo) and testo:
        su_pezzo(("pensiero", testo.rstrip() + "\n"))


def _nodo_coordinatore(stato):
    t0 = time.monotonic()
    messaggi = [
        {"role": "system", "content": _prompt("coordinatore", P_COORDINATORE)},
        {"role": "system", "content": mappa.mappa_dati() + "\n" + operatori.MAPPA_OPERATORI},
    ]
    messaggi += stato.get("storia") or [{"role": "user", "content": stato["domanda"]}]
    messaggi.append({"role": "user", "content":
                     "<stato>\n" + _stato_a_parole(stato) + "\n</stato>"})
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

    for nome, arg in mosse:
        motivo = str(arg.get("motivo") or "").strip()
        _mostra(stato, "**%s** — %s" % (nome, motivo) if motivo else "**%s**" % nome)

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
            # Solo le query che sono ANDATE DAVVERO al database entrano fra le
            # «gia' viste». Ci entravano tutte, respinte comprese, e il
            # risultato era un messaggio falso al momento peggiore: il modello
            # correggeva la query appena respinta, la chiave minuscola la
            # faceva coincidere con quella di prima («christmas balls» /
            # «Christmas balls»), e invece del motivo del rifiuto — l'unica
            # cosa azionabile che avesse — si sentiva dire «l'hai gia'
            # eseguita, il risultato e' lo stesso di prima». Non l'aveva
            # eseguita, e un risultato non c'era. Misurato il 2/10/2026 su
            # «niente gialle?»: 3 giri su 3, falso negativo su palline che
            # esistono.
            if esito in ("eseguita", "vuota"):
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
            esiti.append("verifica: %d buone, %d scartate" % (
                sum(1 for v in nuovi.values() if v == "si"),
                sum(1 for v in nuovi.values() if v == "no")))
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
            proposta = _guida(stato, str(arg.get("motivo") or ""),
                              str(arg.get("cerca") or ""))
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
P_GUIDA = """<ruolo>
Una persona cerca qualcosa in un archivio di cataloghi, e quello che ha detto non basta ancora per cercare. Il tuo lavoro e' guidarla: le mostri fra cosa puo' scegliere, lei scegliera', e a ogni scelta si restringe.
</ruolo>

<cosa_ti_do>
Quello che la persona vuole, la conversazione fatta finora, e un campione vero di righe dell'archivio vicine a quello che cerca.
</cosa_ti_do>

<un_passo_dell_imbuto>
Non stai dando la risposta: stai facendo UN PASSO. Guarda nella conversazione cosa e' gia' stabilito e non riproporlo. Se ha gia' detto «blu», il blu e' deciso: le scelte sono i tipi di oggetto blu che vedi nel campione. Se ha gia' detto «blu» e «nastri», sono deciso tutti e due: le scelte sono i motivi o le varianti dei nastri blu.

Le scelte sono la distinzione che DIVIDE MEGLIO quello che resta. In ordine, di solito: prima il tipo di oggetto, poi il motivo o la decorazione, poi la variante.

Ti fermi quando con le scelte in mano si potrebbe scrivere una ricerca: cioe' quando si sa che oggetto e' e qual e' la caratteristica che conta. Non serve arrivare a un singolo articolo.
</un_passo_dell_imbuto>

<ogni_scelta_porta_le_sue_righe>
Il campione e' numerato. Ogni scelta che scrivi porta i numeri delle righe che SONO quella scelta: almeno uno.

Non e' una formalita', e' il cuore del tuo lavoro: se per una strada non trovi nemmeno una riga, quella strada non esiste nell'archivio e chi la sceglie trovera' il vuoto. Cancellala e proponi quello che c'e' davvero, anche se sono due cose invece di cinque.

Quindi si parte dal campione e si arriva alle scelte, mai il contrario: non pensare a come si divide quello che la persona ha chiesto, guarda cosa c'e' scritto nelle righe e raggruppalo.
</ogni_scelta_porta_le_sue_righe>

<ogni_scelta_passa_una_prova>
Prima di scrivere una scelta, fatti questa domanda: **un cliente puo' ordinarla?**

Il campione viene dalle foto dei cataloghi, e un catalogo non e' fatto solo di articoli: ci sono pagine di listino, tabelle di misure, foto ambientate con modelle, loghi, sfondi. Tu li vedi descritti come tutto il resto, con le stesse parole, e non passano la prova: una pagina non si ordina, una tabella non si ordina, la foto di una modella non si ordina.

Passano la prova le famiglie di oggetti: nastri, vasi, candele, palline, confezioni, sassi, fiori finti.

Un oggetto si ordina. Il modo in cui una pagina e' fatta, no: quello che descrive la pagina invece della merce non e' una scelta.

Se dopo la prova ti restano due scelte, proponi due scelte. Meglio due ordinabili che cinque di cui tre non esistono come merce.
</ogni_scelta_passa_una_prova>

<forma>
Da due a cinque scelte, brevi, con parole che una persona capisce — non le etichette inglesi del catalogo. Niente pagine e niente codici: qui non si risponde, si orienta.

L'introduzione nomina quello che hai visto nel campione, e comincia da cosa c'e'. Se dice che non c'e' niente, chi legge smette di cercare.

La domanda finale chiede di scegliere fra le strade che hai elencato.
</forma>

<consegna>
Con lo strumento `percorso`.
</consegna>"""


I_PERCORSO = [{"type": "function", "function": {
    "name": "percorso",
    "description": "Le strade fra cui la persona puo' scegliere.",
    "parameters": {"type": "object", "properties": {
        "introduzione": {"type": "string",
                         "description": "Una riga che NOMINA quello che hai "
                                        "visto nel campione: «ci sono dei "
                                        "diffusori, delle confezioni regalo e "
                                        "qualche decorazione». Comincia da "
                                        "cosa c'e', mai da cosa manca — questa "
                                        "riga e' la prima che la persona "
                                        "legge, e se dice «non c'e' niente» "
                                        "smette li', anche se sotto hai "
                                        "elencato tre cose."},
        "scelte": {"type": "array", "items": {"type": "object", "properties": {
            "strada": {"type": "string",
                       "description": "La strada, in due o tre parole, come la "
                                      "direbbe una persona."},
            "righe": {"type": "array", "items": {"type": "integer"},
                      "description": "I numeri delle righe del campione che "
                                     "sono questa strada. Almeno uno: se non "
                                     "lo trovi, questa strada non c'e'."}},
            "required": ["strada", "righe"]},
                   "description": "Da due a cinque strade, ognuna con le "
                                  "righe del campione che la contengono."},
        "domanda": {"type": "string",
                    "description": "La domanda con cui chiudere, una riga."}},
        "required": ["introduzione", "scelte", "domanda"]}}}]


def _guida(stato, motivo: str, cerca: str = "") -> str:
    """Il ventaglio di strade, costruito sulle righe vere. "" se non riesce.

    Il campione si cercava con `ambito`, che e' la frase dell'analista — e una
    frase che comincia con «la persona sta cercando» somiglia, per un modello
    di embedding, alle FOTO DELLE PERSONE. Misurato il 3/10/2026 su «cosa
    avete di sportivo»: 7 righe su 8 erano ritratti fotografici, e la guida
    offriva «foto di persone in abiti formali» — leggeva bene il suo
    campione, era il campione a non avere niente a che fare con la domanda.
    Con «oggetti sportivi» le stesse 8 righe sono scarpe da corsa, palloni da
    basket, un ciclista e un Babbo Natale sul tapis roulant.

    Quindi la cosa da cercare la scrive il coordinatore, con le parole con cui
    la chiamerebbe chi ha chiesto: e' la stessa decisione che prende quando
    scrive `SIMILE('nastri blu')`, e la prende lui, non questo codice.
    """
    campione = operatori.vicinato(
        stato["conn"], "immagini",
        cerca.strip() or stato.get("ambito") or stato["domanda"],
        stato["gruppi"], stato["aziende"], quanti=GUIDA_CAMPIONE,
        colonne="id, documento, page, descrizione")
    if not campione:
        return ""
    messaggi = [{"role": "system", "content": _prompt("guida", P_GUIDA)}]
    messaggi += stato.get("storia") or []
    messaggi.append({"role": "user", "content":
                     f"<cosa_ha_chiesto>{stato.get('ambito') or stato['domanda']}"
                     f"</cosa_ha_chiesto>\n"
                     + (f"<cosa_serve_restringere>{motivo}</cosa_serve_restringere>\n"
                        if motivo else "")
                     + "\n<campione_dall_archivio>\n" + _scheda(campione)
                     + "\n</campione_dall_archivio>"})
    for nome, arg in _decide(messaggi, I_PERCORSO):
        scelte = []
        for voce in (arg.get("scelte") or []):
            if isinstance(voce, dict):
                testo, quali = str(voce.get("strada") or "").strip(), voce.get("righe")
                # Una strada senza righe e' una strada inventata: lo strumento
                # chiedeva da quali righe viene e non e' arrivato niente.
                if testo and quali:
                    scelte.append(testo)
            elif str(voce).strip():
                scelte.append(str(voce).strip())
        parti = [str(arg.get("introduzione") or "").strip()]
        parti += [f"- {s}" for s in scelte[:5]]
        parti.append(str(arg.get("domanda") or "").strip())
        return "\n".join(p for p in parti if p)
    return ""


# ==========================================================================
# IL CRITICO — verifica, non cerca e non scrive
# ==========================================================================
P_CRITICO = """<ruolo>
Verifichi. Non cerchi e non scrivi la risposta: ti do la conversazione e delle righe trovate in un archivio di cataloghi, e per ognuna dici se quello che e' stato chiesto c'e'.
</ruolo>

<cosa_sta_cercando>
Nello stato hai l'analisi definitiva della richiesta, passata dall'analista. Non devi reinterpretare la conversazione per estrarre l'intento: fidati dei dati indicati.
Usa la conversazione solo come contesto di sfondo. La tua guida per la verifica sono i dati espliciti: l'oggetto cercato e l'elenco degli attributi richiesti (con il loro tipo: colore, motivo, materiale, forma, misura).
</cosa_sta_cercando>

<il_verdetto_e_binario>
Due valori, `si` e `no`. Non ce n'e' un terzo, e non ti serve: quello che non sai lo scrivi nel motivo.
</il_verdetto_e_binario>

<due_domande_in_ordine>
Per ogni riga dell'archivio, poniti tassativamente due domande in sequenza:

1. **La riga descrive l'OGGETTO richiesto?** Confronta la didascalia con l'oggetto cercato. Un vaso non e' un sasso; una lanterna non e' un nastro. Se l'oggetto non corrisponde, il verdetto e' NO. Smetti di leggere e passa alla riga successiva: non guardare gli attributi.
   *Nota linguistica*: le didascalie sono in inglese (es. «pebbles», «rocks», «gravel» per sassi). Ma la COSA deve essere quella: un ciondolo a forma di sasso non e' un sasso.

2. **Se e solo se l'oggetto e' quello giusto: ha gli ATTRIBUTI chiesti?** Controlla gli attributi nell'elenco (colori, motivi, ecc.). Se l'oggetto e' corretto ma manca un attributo vincolante, o c'e' un attributo diverso (es. cuori rosa invece di rossi), il verdetto e' NO.
</due_domande_in_ordine>

<quando_e_si>
Quando l'oggetto chiesto c'e' e ha quello che e' stato chiesto. Quattro casi in cui c'e' e si sbaglia a dire no:
- una riga e' UNA FOTO, e una foto di catalogo mostra spesso piu' articoli insieme: basta che UNO sia quello chiesto — uno che la riga NOMINA, non uno che potrebbe esserci;
- un articolo che ha quello che serve e in piu' qualcos'altro va bene: il di piu' non toglie;
- l'attributo chiesto c'e' scritto in un altro modo, o in una sfumatura vicina: «yellow-green» per giallo, «crimson» per rosso, «ivory» per bianco. Nel motivo scrivi com'e' scritto davvero;
- il colore chiesto sta nell'ELENCO DEI COLORI della riga. Quell'elenco e' il catalogo delle varianti ordinabili, non la descrizione della foto: e' spesso lungo, in due lingue e con i codici in mezzo («...rot red... blau blue...»), e il colore si cerca dentro tutto l'elenco, fino in fondo. Se c'e', l'articolo e' si', e nel motivo scrivi che e' una variante fra tante.

Due articoli con codice diverso sono due articoli, anche se la descrizione si somiglia: non scartarne uno come doppione.

Se la domanda chiedeva un numero, un totale o l'elenco dei documenti, la riga che porta quel dato e' si', anche se non e' un articolo e non ha una pagina.
</quando_e_si>

<quando_e_no>
Quando l'oggetto chiesto non c'e', o non ha quello che e' stato chiesto. Quattro casi in cui manca e si sbaglia a dire si':
- la riga descrive un altro oggetto: cercavano un nastro, questa e' una lanterna;
- l'attributo chiesto non c'e' affatto: «nastri bianchi con cuori rossi», un nastro bianco senza cuori;
- c'e' un attributo diverso, non una sfumatura: cuori rosa dove si chiedevano cuori rossi;
- le cose chieste stanno su due oggetti diversi nella stessa foto, non sullo stesso: un nastro bianco accanto a un fiocco rosso non e' un nastro bianco con cuori rossi.

Una riga si scarta perche' non risponde, mai perche' non e' un prodotto.
</quando_e_no>

<il_motivo>
**Al massimo otto parole.** Genera un'etichetta in italiano naturale, secca e telegrafica. Non scrivere frasi intere e non ripetere l'intera descrizione.

Il motivo deve essere lo specchio logico del tuo verdetto:
- se il verdetto e' NO perche' l'oggetto e' errato, dichiara il rifiuto e nomina cosa c'e': «non un nastro: un vaso», «non un sasso: una lanterna»;
- se il verdetto e' NO perche' l'oggetto e' giusto ma manca un attributo o e' diverso, nominalo chiaramente: «blu non confermato», «senza cuori», «cuori rosa anziche' rossi»;
- se il verdetto e' SI, descrivi sinteticamente cosa c'e' davvero, per informare chi scrive: «bianco con cuori rossi», «sassi rosso-marroni». Se e' una variante dall'elenco colori, dillo: «variante blu disponibile».
</il_motivo>

<consegna>
Con lo strumento `verdetti`, uno per ogni riga che ti ho dato. Non saltarne nessuna.
</consegna>"""


I_VERDETTI = [{"type": "function", "function": {
    "name": "verdetti",
    "description": "I tuoi verdetti, uno per ogni riga.",
    "parameters": {"type": "object", "properties": {
        "esiti": {"type": "array", "items": {"type": "object", "properties": {
            "riga": {"type": "integer", "description": "Il numero della riga."},
            "esito": {"type": "string", "enum": ["si", "no"]},
            "motivo": {"type": "string",
                       "description": "Al massimo otto parole. Se un attributo chiesto non e' confermato, nominalo."}},
            "required": ["riga", "esito"]}}},
        "required": ["esiti"]}}}]


def _critico(stato, righe, motivo: str) -> dict:
    """{numero riga: si|no} per le righe non ancora verificate.

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
                     f"<ultima_cosa_che_ha_scritto>{stato['domanda']}"
                     f"</ultima_cosa_che_ha_scritto>\n"
                     + (f"<di_cosa_si_parla>{stato['ambito']}</di_cosa_si_parla>\n"
                        if stato.get("ambito") else "")
                     # I PEZZI. Fin qui il critico riceveva la sola frase
                     # dell'analista e il `motivo` scritto a mano dal
                     # coordinatore: la stessa richiesta in tre versioni, e
                     # lui scegliva a quale credere.
                     + (("<la_richiesta_scomposta>" + _pezzi_a_parole(stato)
                         + "</la_richiesta_scomposta>\n")
                        if _pezzi_a_parole(stato) else "")
                     + (f"<cosa_deve_avere_una_riga>{motivo}"
                        f"</cosa_deve_avere_una_riga>\n" if motivo else "")
                     + "\n<righe_da_verificare>\n"
                     + _scheda([r for _, r in da_fare], numeri=numeri,
                                   visto=True, intera=True)
                     + "\n</righe_da_verificare>"})
    fuori = {}
    for nome, arg in _decide(messaggi, I_VERDETTI):
        for e in arg.get("esiti") or []:
            try:
                n = int(e.get("riga"))
            except (TypeError, ValueError):
                continue
            esito = str(e.get("esito", "")).lower()
            if n in numeri and esito in ("si", "no"):
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
P_REDATTORE = """<ruolo>
Scrivi la risposta per la persona, in italiano. Usi solo gli articoli che ti do: non aggiungere articoli, pagine, codici o prezzi che non sono li' dentro.
</ruolo>

<cosa_era_stato_chiesto>
Nello stato hai i dati esatti estratti a monte dall'analista. Usali come termine di paragone per misurare la distanza fra quello che la persona voleva e quello che gli articoli offrono davvero:
- `oggetto`: il tipo di cosa cercata (es. «nastro»).
- `attributi`: le caratteristiche chieste (es. «colore: blu», «motivo: cuori»).
</cosa_era_stato_chiesto>

<di_cosa_puoi_parlare>
Di quello che la persona puo' vedere: gli articoli, i cataloghi, le pagine. Niente altro.

Come ci sei arrivato, per chi legge, non esiste: non ha un nome, non si spiega, non si cita — nemmeno per scusarsi di non averne. Se stai per nominare un pezzo del sistema, quella frase va cancellata e non riscritta: sotto non c'e' una versione migliore, c'e' che quella frase non serve.
</di_cosa_puoi_parlare>

<quando_non_hai_articoli>
Guarda prima se in fondo ai dati c'e' un testo da consegnare com'e' (vedi sotto: `<da_consegnare_come_e>` comanda). Se non c'e':

- era un SALUTO o una chiacchiera: **rispondi e fermati.** Una parola, due. Qualunque cosa aggiungi dopo sara' una scusa per qualcosa che la persona non ti ha chiesto.
- si e' cercato davvero: di' in una frase sola che l'articolo cercato non c'e', NOMINANDOLO con l'oggetto chiesto: «non ho trovato nessun nastro con queste caratteristiche». Quello che manca e' l'ARTICOLO, mai qualcosa di nostro.
</quando_non_hai_articoli>

<di_quello_che_hai>
Se hai degli articoli, qualcosa hai trovato. Nessuna etichetta ti autorizza a dire che non hai trovato niente: l'etichetta dice quanto fidarsi di un articolo, non se esiste.

Descrivi ogni articolo con le parole di `<descrivi>`, e accanto, in mezza riga, cosa non torna. Se `<descrivi>` dice «avorio» e ti avevano chiesto bianco, scrivi che c'e' ed e' avorio: decide chi legge.
</di_quello_che_hai>

<non_promettere_cio_che_non_hai>
Confronta gli articoli che hai ricevuto con l'oggetto e gli attributi chiesti.

La tua prima frase, o l'intestazione dell'elenco, non deve MAI promettere l'oggetto perfetto se i dati dicono altro. Se la persona ha chiesto un regalo o un oggetto con certi attributi — il colore blu — e gli articoli mostrano caratteristiche diverse o parziali — il colore avorio — la tua introduzione deve essere onesta e letterale su cio' che c'e'.

Esempio: se gli articoli parlano di cuori rossi, la tua frase dice che sono disponibili cuori rossi — e poi dichiari esplicitamente che di blu non ne sono venuti fuori. Non intitolare mai un elenco basandoti sulla richiesta della persona se gli articoli sotto non la coprono interamente.

Promettere nel titolo e smentirsi nell'elenco e' il modo piu' veloce di perdere chi legge, perche' si fida della prima frase.
</non_promettere_cio_che_non_hai>

<citazioni>
Ogni cosa che dici porta il numero dell'articolo da cui viene, scritto `[[3]]`, subito dopo la cosa che stai dicendo. Il sistema lo trasforma nel collegamento alla pagina.

Non scrivere tu il numero di pagina: al suo posto metti il riferimento. Non scrivere una sezione «Fonti»: la aggiunge il sistema. Non scrivere mai un indirizzo web: quello vero lo mette il sistema, uno scritto da te e' inventato per definizione.
</citazioni>

<come_ti_arrivano_gli_articoli>
Ogni articolo e' una struttura, e ogni tag vuole una cosa diversa da te:

- `<scrivi_questo>` lo ricopi esattamente come sta, dentro la frase in cui parli di quell'articolo. E' quello che diventa il collegamento alla pagina.
- `<descrivi>` e' la scheda, in inglese. Non si ricopia: si racconta in italiano, con le sue parole ma dette a una persona.
- `<controllo>` dice se qualcuno l'ha guardato. «controllato»: presenti l'articolo e basta. «non controllato»: lo dai lo stesso, con un avviso — una riga sola lo dice per tutti.
- `<di_anche>` e' quello che ha visto chi ha controllato, e **vince sulla descrizione**. Se dice che un attributo chiesto non e' confermato — «cuori si, blu non detto» — quella cosa deve arrivare a chi legge: nomini l'articolo per quello che e' e dici che il blu non e' specificato. Ignorarlo e scrivere che l'articolo e' come lo voleva la domanda e' il modo piu' grave di sbagliare, perche' chi legge non ha modo di accorgersene.
- `<catalogo>` e' il nome del catalogo. Serve a te, per raggruppare, se aiuta.

I tag sono la forma in cui ti arrivano i dati: nella risposta non ne compare nessuno, e nemmeno i loro nomi.

Non scrivere mai che le informazioni sono state verificate o i dati controllati: il controllo e' di ogni singolo articolo, non dell'insieme.
</come_ti_arrivano_gli_articoli>

<se_ti_do_delle_scelte_da_mostrare>
A volte in fondo ai dati trovi un testo gia' scritto: una riga che dice cosa c'e', delle scelte, una domanda. L'ha scritto chi aveva davanti l'archivio.

Riportalo dalla sua prima riga, quella compresa, senza riscriverla e senza mettere niente prima. Gli articoli, se ce ne sono, vanno prima col loro riferimento, e quel testo chiude.

Ti sembrera' che manchi un'apertura: non manca. Se la tua prima frase comincia con «non», e' sbagliata.
</se_ti_do_delle_scelte_da_mostrare>

<forma>
Gli articoli che hai davanti compaiono tutti: non sceglierne un sottoinsieme.

Come le presenti lo decidi tu: elenco per gli articoli, prosa per un documento di testo, raggruppate per catalogo se aiuta.

Traduci in italiano i nomi dei prodotti; i codici articolo restano come sono.
</forma>"""


def _da_consegnare(righe, verdetti):
    """(righe per il redattore, etichetta vera di ognuna).

    Il codice non promuove e non scarta: riporta. Qui stavano tre decisioni
    prese dal programma, e la peggiore mentiva:

    - `if righe and not verdetti: confermate = righe` promuoveva a «verificate»
      venti righe che nessuno aveva guardato. Misurato l'1/10/2026: una
      risposta su tre usciva senza controllo, e chi la leggeva non poteva
      saperlo.
    - `== "si"` buttava via i verdetti diversi da «si», e un verdetto che il
      codice non riconosce non e' un «no»: e' una riga che passa con la sua
      etichetta.
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


P_REVISORE = """<ruolo>
Rileggi una risposta prima che venga consegnata. Non la riscrivi: dici se c'e' qualcosa che non torna, e se vale la pena rifarla.
</ruolo>

<cosa_ti_do>
La conversazione, la risposta, e le righe che chi l'ha scritta aveva davanti, con il loro numero di riferimento. Le righe sono la verita': quello che non e' li' dentro, chi scrive non lo aveva.
</cosa_ti_do>

<come_si_accusa>
Cerchi tre cose, una per volta. Per ognuna la risposta onesta e' «no» quasi sempre.

Per dire «si'» ti servono due cose: le parole esatte della risposta che lo mostrano, e la riga che le smentisce, copiata. Se non riesci a copiare quella riga perche' non c'e', la colpa non c'e'.

La riga che copi deve parlare della stessa cosa di cui parla la risposta. Una riga qualsiasi non e' una prova.
</come_si_accusa>

<nega>
C'e' una frase che dice che non ha trovato niente, che non c'e', che non vede o che non sa, mentre fra le righe quella cosa c'e'?

Conta anche se poi sotto la elenca: chi legge si ferma alla prima frase.

Una risposta affermativa non e' una negazione. Se nessuna frase dice che qualcosa manca, questa e' no.
</nega>

<promette>
L'apertura afferma di aver trovato quello che era stato chiesto, e le righe dicono un'altra cosa?

Se descrive quello che ha per com'e', questa e' no — anche quando quello che ha non e' esattamente cio' che era stato chiesto.

Una risposta che mostra delle scelte invece di proporre articoli non cita niente, e non e' una promessa: e' un orientamento. Zero righe citate non e' mai una prova di colpa.
</promette>

<non_verificabile>
Un articolo e' nominato senza nessun riferimento accanto, oppure al suo posto c'e' una pagina scritta a mano nel testo?

Un riferimento presente e' la prova che si puo' verificare: se ogni articolo ne ha uno, questa e' no.

Vale solo quando elenca articoli. Un saluto, un dato secco o delle scelte da mostrare non hanno niente da verificare.
</non_verificabile>

<rifare>
Trovare qualcosa che non torna e decidere che valga la pena rifare sono due giudizi, e il secondo e' tuo.

Si rifa' quando quello che hai trovato porta chi legge a una conclusione sbagliata. Non si rifa' per un difetto che non cambia quello che chi legge capisce: in quel caso la risposta esce com'e' e il rilievo resta scritto.
</rifare>

<non_giudicare>
Lo stile, la lunghezza, la gentilezza. E non chiedere piu' articoli di quelli che ci sono: codici e prezzi non sono il mestiere dei cataloghi.
</non_giudicare>

<consegna>
Con lo strumento `revisione`.
</consegna>"""


I_REVISIONE = [{"type": "function", "function": {
    "name": "revisione",
    "description": "Le tre cose, una per una.",
    "parameters": {"type": "object", "properties": {
        "nega": {"type": "boolean"},
        "nega_dove": {"type": "string",
                      "description": "Le parole esatte, o «» se nega e' false."},
        "promette": {"type": "boolean"},
        "promette_dove": {"type": "string"},
        "non_verificabile": {"type": "boolean"},
        "non_verificabile_dove": {"type": "string"},
        # LA MOSSA, e la sceglie lui. Prima la deducevo io dal fatto che ci
        # fosse un rilievo: il codice decideva «si riscrive» al posto
        # dell'agente che aveva letto la risposta. Trovare qualcosa che non
        # torna e ritenere che valga la pena rifare sono due giudizi
        # diversi, e il secondo lo puo' dare solo chi ha visto quanto e'
        # grave — una pagina scritta a mano in fondo a una risposta per
        # altro giusta non vale dieci secondi di riscrittura, «non ho
        # trovato» con la riga in mano si'.
        "rifare": {"type": "boolean",
                   "description": "Vale la pena farla riscrivere? Si' se "
                                  "quello che hai trovato porta chi legge "
                                  "a una conclusione sbagliata. No se e' un "
                                  "difetto che non cambia cosa capisce — "
                                  "allora la risposta esce com'e', e il "
                                  "rilievo resta scritto."}},
        "required": ["nega", "nega_dove", "promette", "promette_dove",
                     "non_verificabile", "non_verificabile_dove", "rifare"]}}}]

RILIEVI = (
    ("nega", "dici che qualcosa non c'e' mentre fra le righe che hai c'e'. "
             "Non e' una sfumatura: chi legge la prima frase e se ne va. "
             "Riscrivi cominciando da quello che hai"),
    ("promette", "intesti la risposta con le parole della domanda e sotto "
                 "metti un'altra cosa. Descrivi gli articoli per come sono "
                 "scritti nelle righe, e se quello che era stato chiesto non "
                 "ce l'hai, dillo dopo averli mostrati"),
    ("non_verificabile", "un articolo e' senza il suo [[n]], o hai scritto a "
                         "mano una pagina al suo posto. Chi legge deve poter "
                         "aprire la pagina: ogni articolo porta il suo [[n]], "
                         "e le pagine non si scrivono"),
)


def _revisore(stato, bozza: str, confermate, etichette) -> list:
    """I rilievi sulla bozza, o [] se va bene.

    PERCHE' ESISTE. Il critico verifica le RIGHE; la risposta non la
    rileggeva nessuno, e l'ultima parola arrivava a chi chiede senza che
    nessun agente l'avesse confrontata con le righe. Tutti i difetti
    peggiori del 2/10/2026 stanno li': «ho trovato nastri con cuori blu»
    sopra un elenco di cuori rossi, «non so quanti siano i cataloghi» con
    in mano la riga che diceva 13, «non vedo articoli sportivi» con un
    gatto coi manubri nel campione.

    E lo si sapeva: questo agente esisteva gia' fuori dal sistema, nel
    banco di prova, dove trovava quei difetti in modo affidabile — tarato
    7/7 su risposte di cui si conosceva il verdetto. Stava a MISURARE
    invece che a CORREGGERE.

    Non riscrive niente e non decide la risposta: dice cosa non torna, e a
    riscrivere e' il redattore, che e' quello che sa farlo. Una revisione
    sola per turno: se la seconda bozza ha ancora un rilievo, si consegna
    com'e' — non si fa aspettare chi chiede per una terza passata, e il
    rilievo resta nella traccia.
    """
    if not (bozza or "").strip():
        return []
    messaggi = [{"role": "system", "content": _prompt("revisore", P_REVISORE)}]
    messaggi += stato.get("storia") or [{"role": "user", "content": stato["domanda"]}]
    messaggi.append({"role": "user", "content":
                     "<righe_che_aveva_davanti>\n"
                     + _scheda(confermate, verdetti=etichette, visto=True,
                               per_chi_scrive=True)
                     + "\n</righe_che_aveva_davanti>\n\n"
                     + "<risposta_da_rileggere>\n" + bozza
                     + "\n</risposta_da_rileggere>"})
    for nome, arg in _decide(messaggi, I_REVISIONE):
        trovati = [(c, str(arg.get(c + "_dove") or "").strip(), come)
                   for c, come in RILIEVI if arg.get(c)]
        # Si rifa' se lo dice LUI. Il codice non deduce «c'e' un rilievo,
        # quindi si riscrive»: sono due giudizi diversi, e il secondo lo
        # da' chi ha letto la risposta. Qui si trasporta la sua scelta.
        return trovati if arg.get("rifare") else []
    return []


def _nodo_redattore(stato):
    t0 = time.monotonic()
    righe, verdetti = stato.get("righe") or [], stato.get("verdetti") or {}
    confermate, etichette = _da_consegnare(righe, verdetti)
    messaggi = [{"role": "system", "content": _prompt("redattore", P_REDATTORE)}]
    messaggi += stato.get("storia") or [{"role": "user", "content": stato["domanda"]}]
    chiarimento = stato.get("chiarimento") or ""
    messaggi.append({"role": "user", "content":
                     # Il tag si chiamava `<righe>` e il redattore scriveva
                     # «ho trovato queste righe che parlano di nastri blu»:
                     # il nome dell'etichetta gli insegna la parola, come un
                     # divieto citato insegna la frase che vieta. Ora si
                     # chiama come la cosa che deve consegnare, cosi' se lo
                     # ricopia non fa danno (2/10/2026, in chat).
                     # COSA ERA STATO CHIESTO. Tutto il suo prompt parlava di
                     # quello che ha TROVATO: la domanda gli arrivava solo in
                     # prosa, dentro la conversazione, mentre gli articoli gli
                     # arrivano strutturati. Non sapeva quale attributo doveva
                     # essere verificato, e su `aperta-regalo` prometteva.
                     (("<cosa_era_stato_chiesto>" + _pezzi_a_parole(stato)
                       + "</cosa_era_stato_chiesto>\n\n")
                      if _pezzi_a_parole(stato) else "")
                     + "<articoli_trovati>\n"
                     + _scheda(confermate, verdetti=etichette,
                               visto=True, per_chi_scrive=True)
                     + "\n</articoli_trovati>"
                     # L'etichetta diceva «DOMANDA DA FARE alla persona», e
                     # quello che arriva non e' una domanda: e' un testo
                     # intero — una riga che dice cosa c'e', le strade, e in
                     # fondo la domanda. Il redattore leggeva l'etichetta
                     # alla lettera, concludeva che l'apertura mancasse, e se
                     # la scriveva: «non ho trovato informazioni specifiche su
                     # prodotti sportivi nell'archivio. Tuttavia...» davanti a
                     # un ventaglio che cominciava con «ci sono delle figurine
                     # sportive». Teneva le scelte e buttava via la prima
                     # riga, cioe' l'unica che diceva che la roba c'e'
                     # (2/10/2026, 3 giri su 3). Un'etichetta che descrive
                     # male il suo contenuto e' un'istruzione sbagliata.
                     + (f"\n\n<da_consegnare_come_e>\n"
                        f"Questo testo e' gia' scritto. Riportalo dalla sua "
                        f"prima riga, senza riscriverla e senza premetterci "
                        f"parole tue — le righe qui sopra, se ce ne sono, "
                        f"vanno prima di esso.\n{chiarimento}"
                        f"\n</da_consegnare_come_e>"
                        if chiarimento else "")})
    # La BOZZA, e poi chi la rilegge. In questo ordine, e non per scelta
    # estetica: una risposta non si puo' richiamare dopo averla consegnata,
    # quindi il controllo deve stare PRIMA della consegna. Il prezzo e' che
    # non si scrive piu' in streaming mentre il modello compone — chi
    # aspetta vede il ragionamento del coordinatore, non la risposta che
    # cresce. Fra far aspettare qualche secondo in piu' e consegnare «non
    # ho trovato» con la riga in mano, la scelta non e' dubbia.
    bozza = modello.chiedi(messaggi, max_tokens=2500)
    # Il revisore restituisce i rilievi SOLO se ha anche deciso che vale la
    # pena rifare: la lista vuota vuol dire «consegna», e non e' il codice a
    # stabilirlo. Una revisione per turno — quello si', e' un tetto scritto
    # qui, come MAX_PASSI: non si fa aspettare chi chiede per una terza
    # passata, e quello che resta fuori finisce nella traccia.
    _mostra(stato, "**rileggo la risposta prima di darla**")
    rilievi = _revisore(stato, bozza, confermate, etichette)
    if rilievi:
        _mostra(stato, "**la riscrivo** — " + "; ".join(come for _, _, come in rilievi))
        messaggi += [
            {"role": "assistant", "content": bozza},
            {"role": "user", "content":
             "Chi rilegge ha trovato questo, e ha ragione:\n"
             + "\n".join("- %s. Le tue parole: «%s»" % (come, dove)
                         for _, dove, come in rilievi)
             + "\nRiscrivi la risposta per intero tenendo conto di questo. "
               "Niente scuse e niente commenti sul fatto che la stai "
               "riscrivendo: esce solo la risposta."}]
        bozza = modello.chiedi(messaggi, max_tokens=2500)
    nota = _nota("redattore", {"righe": len(confermate),
                               "rilievi": [c for c, _, _ in rilievi]}, 0, t0)

    # La consegna. I «[[3]]» vanno risolti qui, altrimenti si vedrebbero i
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

        _manda(passa(bozza))
        _manda(passa("", fine=True))
        _manda(coda(etichette))
        return {"risposta": "".join(resi), "resa": True,
                "confermate": confermate, "etichette": etichette,
                "traccia": [nota]}
    return {"risposta": bozza,
            "confermate": confermate, "etichette": etichette,
            "traccia": [nota]}


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
# Le etichette come le direbbe una persona, non come codici: il redattore
# le ricopia dentro la risposta, e stampava «(verificata)» fra parentesi in
# mezzo alla prosa (2/10/2026, in chat). Se se le ricopia cosi', almeno non
# sembrano il referto di una macchina.
PAROLA = {"si": "controllato"}


def _scheda(righe, numeri=None, verdetti=None, visto: bool = False,
            per_chi_scrive: bool = False, intera: bool = False) -> str:
    """Le righe numerate, come le vedono coordinatore, critico e redattore.

    `per_chi_scrive` cambia la FORMA, non il contenuto, e serve a una cosa
    sola: il redattore ricopia il formato che gli mostri. Gli davamo righe
    fatte cosi'

        1. [SI] (motivo) [Catalogo X.pdf, pagina 142] object: glass...

    e lui scriveva «1. **[SI]** Kit con decorazioni in vetro (pagina 142 del
    file Catalogo X.pdf)» — l'etichetta interna in grassetto e la pagina a
    mano, cioe' le due cose che il prompt gli vieta da sempre (2/10/2026, in
    chat: tre risposte su tre con **[SI]**, e una senza nessun
    collegamento). Non e' disobbedienza: `[[n]]` nell'input non compariva
    mai, `[SI]` e la pagina erano in ogni riga.

    Quindi per lui il numero si presenta gia' come citazione, il verdetto e'
    una parola e non un'etichetta, e la pagina non c'e' — al suo posto c'e'
    il riferimento, che e' quello che deve scrivere. Il nome del catalogo
    resta, perche' raggruppare per catalogo aiuta chi legge.

    Le colonne le sceglie il coordinatore, quindi non si puo' dare per
    scontato che ci siano `documento`, `page` e `descrizione`. Una riga di
    conteggio (`SELECT count(*) FROM documenti`) non ne ha nessuna: mostrata
    come le altre diventava «1. [?, pagina ?]» seguito dal vuoto, e il critico
    la scartava — non perche' non rispondesse, ma perche' non la VEDEVA
    («quanti cataloghi ci sono» restava senza risposta, il 30/09/2026)."""
    if per_chi_scrive:
        return _schede_xml(righe, numeri, verdetti, visto)
    fuori = []
    for i, r in enumerate(righe):
        n = numeri[i] if numeri else i + 1
        capo = f"[[{n}]]" if per_chi_scrive else f"{n}."
        if verdetti and n in verdetti:
            capo += (" (%s)" % PAROLA.get(verdetti[n], "non controllato")
                     if per_chi_scrive else f" [{verdetti[n].upper()}]")
            # Il MOTIVO, non solo il verdetto. «0 confermate su 40» dice
            # «vicolo cieco»; «e' una pallina singola, non un kit» dice che il
            # problema e' il criterio, non le parole — informazione opposta, e
            # il critico la scriveva gia' senza che nessuno la leggesse.
            if r.get("_motivo"):
                capo += f" ({r['_motivo']})"
        if r.get("documento"):
            # La pagina NON si mostra a chi scrive: e' esattamente quello che
            # non deve scrivere, e mostrargliela e' invitarlo a ricopiarla.
            capo += (f" (dal catalogo {r['documento']})" if per_chi_scrive
                     else f" [{r['documento']}, pagina {r.get('page', '?')}]")
        if visto and r.get("_visto"):
            capo += f" (guardata: {r['_visto']})"
        testo = r.get("descrizione") or r.get("content")
        if not testo:
            testo = "; ".join(f"{c}: {v}" for c, v in r.items()
                              if not str(c).startswith("_")
                              and c not in ("id", "source_id", "documento", "page")
                              and v is not None)
        # Il taglio a `ASSAGGIO` serve a chi guarda il MUCCHIO di righe per
        # decidere la mossa. Chi deve giudicare o descrivere UNA riga la vede
        # intera, perche' le didascalie di questo corpus mettono in fondo
        # proprio quello su cui si giudica: «Colours: ... rot red ... blau
        # blue», «Code: ...». Misurato il 3/10/2026 su «sassi rossi»: la
        # pagina dei sassi decorativi ha 26 colori in due lingue, «rot red»
        # sta al carattere 450, e il critico la scartava — non perche' non
        # fosse rossa, ma perche' la parola «red» era oltre il taglio. Meta'
        # delle righe sono piu' corte di 300 caratteri: il taglio risparmiava
        # quasi niente e cancellava l'attributo chiesto.
        corpo = ' '.join(str(testo).split())
        if not (intera or per_chi_scrive):
            corpo = corpo[:ASSAGGIO]
        fuori.append(f"{capo} {corpo}".strip())
    return "\n".join(fuori) or "(nessuna riga)"



def _schede_xml(righe, numeri=None, verdetti=None, visto: bool = False) -> str:
    """Gli articoli per CHI SCRIVE, uno per struttura invece che per riga.

    Il foglio di prima metteva cinque cose di natura diversa tutte fra
    parentesi sulla stessa riga: `[[1]] (controllato) (cuori si, blu non
    detto) (dal catalogo P.pdf) ribbon with heart motifs`. Ma `[[1]]` va
    ricopiato tale e quale, la descrizione va RACCONTATA, «controllato» va
    trasmesso, il motivo e' un'istruzione di onesta' e il catalogo e'
    contesto: una sintassi sola per cinque ruoli. E una riga di frammenti fra
    parentesi E' prosa annotata, quindi il redattore la incollava come prosa
    — misurato in chat il 3/10/2026: «[[1]] (controllato) (nastri con disegni
    di foglie): tre nastri sottili...», con l'etichetta e il motivo dentro la
    risposta.

    I nomi dei tag sono VERBI, non nomi di cose: il redattore ricopia quello
    che vede, e se ricopiasse «scrivi_questo» o «di_anche» sarebbe una frase
    storta, mentre «riga», «verdetto» o «etichetta» sarebbero il nostro gergo
    in faccia a chi legge.
    """
    fuori = []
    for i, r in enumerate(righe):
        n = numeri[i] if numeri else i + 1
        pezzi = ["  <scrivi_questo>[[%d]]</scrivi_questo>" % n]
        testo = r.get("descrizione") or r.get("content")
        if not testo:
            testo = "; ".join(f"{c}: {v}" for c, v in r.items()
                              if not str(c).startswith("_")
                              and c not in ("id", "source_id", "documento", "page")
                              and v is not None)
        pezzi.append("  <descrivi>%s</descrivi>" % " ".join(str(testo).split()))
        if verdetti and n in verdetti:
            pezzi.append("  <controllo>%s</controllo>"
                         % PAROLA.get(verdetti[n], "non controllato"))
            if r.get("_motivo"):
                pezzi.append("  <di_anche>%s</di_anche>" % r["_motivo"])
        if visto and r.get("_visto"):
            pezzi.append("  <di_anche>%s</di_anche>" % r["_visto"])
        if r.get("documento"):
            pezzi.append("  <catalogo>%s</catalogo>" % r["documento"])
        fuori.append("<articolo>\n%s\n</articolo>" % "\n".join(pezzi))
    return "\n".join(fuori) or "(nessun articolo)"

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
    oggetto: str              # il tipo di cosa cercata, dall'analista
    attributi: list           # [{valore, tipo}], dall'analista
    dove: str                 # la tabella, dall'analista
    righe: list
    verdetti: dict
    confermate: list
    etichette: dict           # numero di riga -> si | non verificata
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
    # E il verdetto deve ARRIVARGLI a parole, o non puo' tenerne conto.
    detto = _stato_a_parole({"domanda": "d", "righe": [], "verdetti": {},
                             "passi": 0, "rispondibile": False})
    assert "non si puo' rispondere" in detto, detto
    # La nota di verifica la scrive il CODICE, perche' il redattore due volte
    # ha scritto «tutte verificate» sotto righe che non lo erano.
    assert _nota_verifica({1: "si", 2: "si"}, {1, 2}) == ""
    assert "2 delle 3" in _nota_verifica(
        {1: "si", 2: "non verificata", 3: "non verificata"}, {1, 2, 3})
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
    tenute, etich = _da_consegnare(quattro, {1: "si", 2: "no", 3: "si"})
    assert [r["descrizione"] for r in tenute] == ["a", "c", "d"], tenute
    assert etich == {1: "si", 2: "si", 3: "non verificata"}, etich
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
    ultimo = _stato_a_parole({"domanda": "d", "righe": [], "verdetti": {},
                              "passi": MAX_PASSI - 1})
    assert "ULTIMA MOSSA" in ultimo
    print("grafo: ok")


if __name__ == "__main__":
    _prova()
