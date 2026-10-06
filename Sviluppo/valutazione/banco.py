"""Il BANCO: la stessa batteria, N volte, e a giudicare e' un agente.

    D=/tmp/val-$(date +%s)
    docker compose cp valutazione orchestratore:$D
    docker compose exec -T orchestratore sh -c "cd /app && python $D/banco.py 3 > /var/tmp/banco.txt 2>&1"
    docker compose exec -T orchestratore cat /var/tmp/banco.txt

Una cartella NUOVA ogni volta (`cp` su una che esiste copia DENTRO, e si
rimisura la versione vecchia credendo di aver cambiato qualcosa). L'esito
si scrive in `/var/tmp`, non nella cartella copiata: quella arriva di root.

PERCHE' ESISTE. Il 2/10/2026 ho cambiato quattro prompt e dopo ognuno ho
concluso qualcosa da UNA esecuzione. Tre di quelle conclusioni erano
sbagliate, e me ne sono accorto solo rifacendo la prova per caso. Su un
sistema che decide, una esecuzione non e' una misura: e' un aneddoto.

PERCHE' GIUDICA UN AGENTE, e non il codice. La prima versione di questo
file giudicava con delle regex — «contiene [[n]]?», «contiene "non ho
trovato"?». In mezza giornata ha promosso TRE risposte sbagliate, sempre
per lo stesso motivo: misurava la meccanica invece dell'affermazione.

    «No, non ho trovato palline di Natale gialle. [[1]]»
        il [[n]] c'era -> passava. La riga citata era una pallina
        giallo-verde: la risposta era falsa e il banco diceva 4/5.
    «Non vedo articoli sportivi negli oggetti descritti.»
        aveva chiamato la guida -> passava. In archivio c'e' un nastro
        con i palloni da calcio.
    «Ho trovato nastri con cuori blu: - nastro con cuori ROSSI...»
        c'era una riga confermata -> passava. Il titolo prometteva una
        cosa che l'elenco smentiva due righe sotto.

La domanda vera — «quello che afferma corrisponde a quello che ha?» — non
e' meccanica, e un metro che la riduce a una regex si auto-assolve. Qui
l'attesa di ogni caso e' scritta in italiano, e a dire se e' rispettata e'
un agente che vede: la conversazione, la risposta, le righe che la risposta
cita, e un CAMPIONE di cosa l'archivio contiene davvero su quel tema
(`vicinato`, le righe piu' vicine per significato). Il campione serve per
l'unico giudizio che dalla sola risposta non si puo' dare: ha negato una
cosa che c'e'?

I numeri meccanici restano — secondi, giri del coordinatore, query
eseguite, righe citate — ma come DATI riportati accanto al verdetto, non
come il verdetto. Misurare quanto e' costato e' lecito; decidere se e'
giusto, no.

IL RISCHIO, detto subito: e' un modello che giudica un modello. Per questo
il giudice vede le prove invece di ricordare, risponde si'/no con mezza
riga di motivo, e i motivi si stampano tutti — la prima volta si leggono a
mano, e se non sono sensati il metro si butta. Un metro di cui non ci si
fida e' peggio di nessun metro.
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
from orchestratore import grafo, identita, modello, operatori      # noqa: E402

GRUPPI = os.environ.get("VAL_GRUPPI", "Responsabile Acquisti,acquisti,"
                                      "azienda-decobrands,tutti").split(",")
QUI = pathlib.Path(__file__).resolve().parent


# --------------------------------------------------------------------------
# LE ATTESE, in italiano. Sono proprieta' del COMPORTAMENTO, non del
# contenuto: «un saluto non si cerca», «non si nega cio' che c'e'». Se un
# giorno i cataloghi cambiano queste restano vere; una risposta attesa no.
#
# Lo SCOPO del sistema sui cataloghi, che il giudice deve avere in testa:
# chi lo usa cerca qualcosa e poi va a VERIFICARLO sulla pagina. Non gli
# servono codici ne' prezzi — quelli verranno dall'anagrafica articoli, che
# e' un'altra cosa e sta su tabelle. Qui il risultato e' la pagina giusta,
# raggiungibile, e la certezza che non gli sia stato nascosto niente.
# --------------------------------------------------------------------------
BATTERIA = [
    {"nome": "saluto", "domanda": "ciao",
     "attesa": "E' solo un saluto. Deve rispondere al saluto e basta, senza "
               "cercare niente, senza scusarsi e senza dire frasi che facciano "
               "pensare a un guasto."},
    {"nome": "archivio", "domanda": "quanti cataloghi ci sono?",
     "attesa": "Chiede un dato sull'archivio stesso. Deve DARE il numero. Un "
               "numero non si ricorda e non si stima: o l'ha letto, o non "
               "c'e' — ma se ce l'ha davanti e risponde «non so», e' un "
               "fallimento."},
    {"nome": "prodotto",
     "domanda": "ho bisogno di nastri bianchi con cuori rossi",
     "attesa": "Richiesta precisa di prodotto. Deve portare degli articoli "
               "che corrispondono davvero — bianchi E con cuori rossi, sullo "
               "stesso articolo — e ognuno deve essere accompagnato dal suo "
               "riferimento [[n]], che diventa il collegamento alla pagina: "
               "senza quello chi legge non puo' andare a verificare, ed e' "
               "tutto cio' per cui usa questo sistema."},
    {"nome": "generica", "domanda": "mi serve qualcosa di blu",
     "attesa": "Troppo generica per cercare: «blu» da solo tiene insieme un "
               "piatto, una molletta e un mappamondo. Deve CHIEDERE un "
               "chiarimento, e la domanda deve offrire strade concrete lette "
               "nell'archivio, non categorie inventate come «accessori» o "
               "«decorazioni». Mettersi a cercare e consegnare un elenco di "
               "cose blu scollegate e' il fallimento."},
    {"nome": "aperta-regalo",
     "domanda": "mi proponi qualcosa per il compleanno di mia mamma?",
     "attesa": "Un'occasione, non un oggetto: dice PERCHE' serve un regalo, "
               "non CHE COSA. Come un commesso, deve CHIEDERE invece di "
               "andare in magazzino — e la domanda deve offrire strade "
               "concrete lette nell'archivio (che tipo di oggetto, quali "
               "famiglie ci sono), non categorie inventate. Mettersi a "
               "cercare e consegnare un elenco di cose vagamente adatte a un "
               "compleanno e' il fallimento: chi ha chiesto preferisce "
               "rispondere a una domanda che scorrere quaranta articoli."},
    {"nome": "aperta-tema", "domanda": "cosa avete di sportivo?",
     "attesa": "Un tema, non un oggetto: «sportivo» non dice se cerca una "
               "palla, un nastro con i palloni o una decorazione. Come un "
               "commesso, deve CHIEDERE che tipo di oggetto serve, mostrando "
               "quali famiglie a tema sport ci sono davvero in archivio — nel "
               "campione qui sotto vedi cosa c'e'. Due fallimenti: dire che "
               "di sportivo non c'e' niente quando il campione dice il "
               "contrario, e consegnare un elenco lungo senza aver ristretto "
               "niente."},
    {"nome": "seguito", "domanda": "niente gialle?",
     "storia": [("user", "hai palline di Natale rosse?"),
                ("assistant", "Si', ne ho trovate: palline di Natale rosse "
                              "lucide nel catalogo Packara.")],
     "attesa": "E' un seguito: vuole palline di Natale GIALLE, non qualunque "
               "cosa gialla. Guarda il campione: se di palline gialle o di "
               "tonalita' vicine ce ne sono, rispondere «non ho trovato "
               "palline di Natale gialle» e' falso — e resta falso anche se "
               "sotto ne elenca una. Se invece ne mostra, devono avere il "
               "loro [[n]] ed essere descritte per come sono davvero (se e' "
               "giallo-verde lo dice)."},
    {"nome": "non-promettere", "domanda": "ci sono anche con i cuori blu?",
     "storia": [("user", "ho bisogno di nastri bianchi con cuori rossi"),
                ("assistant", "Ho trovato diversi nastri bianchi con cuori "
                              "rossi nel catalogo Packara, a pagina 39 e 5.")],
     "attesa": "Chiede nastri con i cuori BLU. Il fallimento da cercare e' "
               "l'opposto della reticenza: intestare la risposta con le "
               "parole della domanda e poi elencare un'altra cosa. «Ho "
               "trovato nastri con cuori blu» seguito da articoli con i cuori "
               "rossi e' falso, e il titolo e' la riga che uno legge. Deve "
               "descrivere quello che ha per come e'. Se di cuori blu non ne "
               "ha, lo dice — e va benissimo mostrare lo stesso quello che ha "
               "trovato, purche' chiamandolo col suo nome."},
    {"nome": "pappagallo", "domanda": "e di blu invece?",
     "storia": [("user", "hai palline di Natale rosse?"),
                ("assistant", "Si', ne ho trovate: palline di Natale rosse "
                              "lucide nel catalogo Packara.")],
     "attesa": "E' un seguito e chiede palline BLU. Deve rispondere a questa "
               "domanda, con quello che riguarda il blu. Se nella risposta "
               "compaiono cose che c'entrano con altri colori o con altre "
               "conversazioni — una pallina giallo-verde, un conteggio di "
               "cataloghi — non sta rispondendo: sta ripetendo frasi."},
    {"nome": "senza-dato", "domanda": "quanto costano?",
     "storia": [("user", "hai palline di Natale rosse?"),
                ("assistant", "Si', ne ho trovate: palline di Natale rosse "
                              "lucide nel catalogo Packara.")],
     "attesa": "Chiede un prezzo. In questo archivio i prezzi non ci sono "
               "(nessuna riga porta un importo), e i cataloghi non servono a "
               "quello: servono a trovare l'articolo e la pagina. Deve dire "
               "che il prezzo non ce l'ha. Inventare una cifra e' il "
               "fallimento; indicare dove guardare va bene."},
]


P_GIUDICE = """Giudichi il comportamento di un assistente aziendale che cerca nei cataloghi di un'azienda.

A che cosa serve quel sistema, perche' senza questo giudichi male: chi lo usa e' un venditore. Cerca un'idea o un articolo, e poi va a VERIFICARLO sulla pagina del catalogo. Non gli servono codici ne' prezzi — quelli stanno altrove. Gli serve trovare la cosa, sapere dov'e', e potersi fidare di quello che legge.

Ti do cinque cose:
- la CONVERSAZIONE, perche' quasi mai l'ultima riga si regge da sola;
- che cosa il sistema AVREBBE DOVUTO FARE, in italiano;
- la RISPOSTA che ha dato;
- le RIGHE CHE HA CITATO: sono quello che aveva davvero in mano;
- un CAMPIONE dell'archivio su quel tema, preso per significato. Non e' quello che il sistema ha trovato: e' quello che in archivio C'E'. Serve per l'unico giudizio che dalla sola risposta non si puo' dare — ha detto che non c'era niente mentre c'era?

Non dai un voto. Per ognuna delle due colpe qui sotto scrivi UN SOLO CAMPO: **la riga che la dimostra, copiata.** Se quella riga non c'e', lasci il campo vuoto, e vuoto vuol dire «nessuna colpa».

Non c'e' nessun altro modo di accusare: l'accusa E' la prova. Una colpa senza la riga copiata non e' una colpa, e' un sospetto — e un sospetto non si consegna.

La riga che copi deve parlare della stessa cosa di cui parla la risposta. Una riga qualsiasi non e' una prova: se la stai scegliendo perche' «qualcosa non torna» e non perche' smentisce quello che la risposta dice, allora il campo va lasciato vuoto.

1. NEGA QUELLO CHE AVEVA. Nella risposta c'e' una frase che dice che non ha trovato niente, che non c'e', che non vede o che non sa — e fra le righe citate o nel campione quella cosa c'e'. Copia quella riga.
   Conta anche se poi sotto la elenca: chi legge si ferma alla prima frase.
   Una risposta affermativa non e' una negazione: se nessuna frase dice che qualcosa manca, il campo resta vuoto.

2. PROMETTE QUELLO CHE NON AVEVA. L'apertura afferma di aver trovato quello che era stato chiesto, e NESSUNA delle righe citate lo sostiene. Copia la riga che dovrebbe sostenerlo e non lo fa.

   **Basta UNA riga che sostenga l'affermazione perche' questa colpa non ci sia.** Se la risposta cita tre articoli e il terzo e' quello giusto, la promessa e' mantenuta: il campo resta vuoto. Non fermarti alla prima riga che non c'entra — leggile tutte, e cerca se almeno una regge quello che la risposta dice.
   Su «ci sono anche con i cuori blu?» hai bocciato una risposta che citava due articoli, esibendo come prova il primo (cuori rossi) e ignorando il secondo, che diceva «light blue with blue glitter hearts» (2/10/2026). La risposta era corretta.
   **Per questa colpa la prova puo' venire SOLO dalle righe citate, mai dal campione.** Una promessa si smentisce con quello che chi scriveva aveva in mano, non con quello che esiste altrove nell'archivio: il campione contiene tutto, e pescarci dentro fa accusare chiunque. Se le righe citate sono zero, questo campo resta vuoto — sempre, senza eccezioni.
   Se la risposta descrive quello che ha per com'e', il campo resta vuoto, anche quando quello che ha non e' esattamente cio' che era stato chiesto.
   Una risposta che fa una domanda o che mostra delle scelte non promette niente: non sta proponendo articoli, sta orientando.

Il CAMPIONE serve a UNA cosa sola: capire se una NEGAZIONE era falsa. Non si usa per la seconda colpa. Non e' l'elenco di quello che la risposta avrebbe dovuto contenere, e non si confronta col numero di righe citate. Una risposta che cita tre articoli giusti va benissimo anche se il campione ne mostra dieci.

Non giudicare lo stile, la lunghezza o la gentilezza. Non pretendere codici o prezzi: non e' il mestiere dei cataloghi. Non punire una risposta perche' e' secca, e non premiarla perche' e' scritta bene.

Consegna con lo strumento `verdetto`."""

I_VERDETTO = [{"type": "function", "function": {
    "name": "verdetto",
    "description": "Le tre colpe, una per una.",
    "parameters": {"type": "object", "properties": {
        "nega": {"type": "boolean",
                 "description": "true SOLO se nella risposta c'e' una frase "
                                "che dice che qualcosa non c'e' o non si sa, "
                                "e invece c'era."},
        "nega_dove": {"type": "string",
                      "description": "Le parole esatte della risposta che lo "
                                     "dimostrano, o «» se nega e' false."},
        "nega_prova": {"type": "string",
                       "description": "La riga — citata o del campione — che "
                                      "contiene la cosa che la risposta dice "
                                      "di non avere, copiata. «» se false."},
        "promette": {"type": "boolean",
                     "description": "true SOLO se l'apertura afferma di aver "
                                    "trovato una cosa che le righe citate "
                                    "smentiscono."},
        "promette_dove": {"type": "string",
                          "description": "Le parole esatte, o «»."},
        "promette_prova": {"type": "string",
                           "description": "La riga che descrive una cosa "
                                          "DIVERSA da quella promessa, "
                                          "copiata. «» se false."}},
        "required": ["nega", "nega_dove", "nega_prova",
                     "promette", "promette_dove", "promette_prova"]}}}]

# Le colpe sono due, e sono tutte e due SEMANTICHE: «quello che dice e'
# vero?». La terza — «c'e' il [[n]]?» — era meccanica, ed era l'unica che
# il modello sbagliava, in modo instabile: passava la taratura e poi
# bocciava «Palline di Natale blu con sfumature d'argento [[1]]» con la
# citazione dentro la frase che riportava come prova (2/10/2026, su quasi
# tutta la batteria). Adesso quel controllo sta dentro il flusso, nel
# revisore, che oltre a vederlo lo puo' far correggere.
COLPE = (("nega", "nega avendo"), ("promette", "promette senza avere"))

# Da quale tabella si pesca il CAMPIONE dell'archivio. I cataloghi
# stanno nelle didascalie delle foto, i documenti nel testo: un giudice
# che per giudicare una risposta di progetto guarda un campione di
# didascalie inglesi non ha la prova che gli serve.
CAMPIONE_DA = "immagini"


def _righe_a_testo(righe, quante=10):
    return "\n".join(
        "- [%s, pagina %s] %s" % (
            r.get("documento", "?"), r.get("page", "?"),
            " ".join(str(r.get("descrizione") or r.get("content") or "").split())[:200])
        for r in righe[:quante]) or "(nessuna)"


def giudica(caso, conversazione, risposta, citate, campione):
    """si'/no + motivo, o None se il giudice non decide."""
    testo = "\n".join("%s: %s" % ("PERSONA" if m["role"] == "user" else "SISTEMA",
                                  " ".join(m["content"].split())[:300])
                      for m in conversazione)
    messaggi = [{"role": "system", "content": P_GIUDICE},
                {"role": "user", "content":
                    f"CONVERSAZIONE:\n{testo}\n\n"
                    f"COSA AVREBBE DOVUTO FARE:\n{caso['attesa']}\n\n"
                    f"RISPOSTA:\n{risposta}\n\n"
                    f"RIGHE CHE HA CITATO:\n{_righe_a_testo(citate)}\n\n"
                    f"CAMPIONE DELL'ARCHIVIO su questo tema (cosa c'e' "
                    f"davvero):\n{_righe_a_testo(campione)}"}]
    for tentativo in range(2):
        # Il giudice RAGIONA. Gli si chiede un controllo a piu' passi —
        # trova la frase nella risposta, poi la riga che la smentisce, poi
        # valuta se parlano della stessa cosa — e glielo si chiedeva di
        # getto. Da li' le accuse senza prova e le prove a caso: un ramo di
        # limoni esibito per smentire delle confezioni regalo (2/10/2026).
        # Un 500 del server non e' un verdetto: il 2/10/2026 ne e' arrivato
        # uno a meta' batteria e si e' portato via le quattro domande che
        # restavano. Qui si conta come «il giudice non ha deciso».
        try:
            m = modello.messaggio(messaggi, max_tokens=2500, tools=I_VERDETTO,
                                  tool_choice="required", ragiona=True)
        except Exception as e:
            print("   (il giudice e' esploso: %s: %s%s)"
                  % (type(e).__name__, str(e)[:90],
                     ", riprovo" if tentativo == 0 else ""))
            if tentativo == 0:
                continue
            return None
        m.pop("reasoning_content", None)
        for tc in m.get("tool_calls") or []:
            if tc.get("function", {}).get("name") == "verdetto":
                try:
                    return json.loads(tc["function"].get("arguments") or "{}")
                except (TypeError, ValueError):
                    break
        messaggi += [m, {"role": "user",
                         "content": "Rispondi chiamando lo strumento `verdetto`."}]
    return None


# Una prova che non contiene parole non e' una prova. Il giudice, messo
# davanti all'obbligo di copiare la riga, ha imparato a scrivere «» e «[»:
# non vuoti, quindi contavano come accusa, e su un «Ciao!» ne sono arrivate
# due (2/10/2026). Qui non si giudica niente: si guarda se c'e' del testo.
_PAROLE = re.compile(r"[0-9A-Za-zÀ-ɏ]{3,}")


def prova_vera(testo) -> str:
    """Il testo della prova se contiene almeno due parole, altrimenti «»."""
    t = str(testo or "").strip()
    return t if len(_PAROLE.findall(t)) >= 2 else ""


def citate_davvero(risposta, righe):
    """Le righe che la risposta nomina con «[[n]]». Il giudice deve vedere
    QUELLE: una riga recuperata e non citata non e' stata proposta, e
    giudicarla falserebbe il voto in meglio."""
    numeri = {int(n) for n in re.findall(r"\[\[\s*(\d+)\s*\]\]", risposta or "")}
    return [righe[n - 1] for n in sorted(numeri) if 1 <= n <= len(righe)]


def un_giro(conn, caso, campione):
    conversazione = [{"role": r, "content": c} for r, c in caso.get("storia", [])]
    conversazione.append({"role": "user", "content": caso["domanda"]})
    t0 = time.monotonic()
    try:
        righe, risposta, meta = grafo.cerca(conn, caso["domanda"], GRUPPI,
                                            storia=list(conversazione))
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"esploso": "%s: %s" % (type(e).__name__, e), "secondi": 0,
                "coordinatore": 0, "giri": 0, "mosse": [], "eseguite": 0,
                "citate": 0, "risposta": "", "verdetto": None}
    secondi = time.monotonic() - t0
    ric = meta.get("ricerca") or ""
    passi = meta.get("strumenti") or []
    g = giudica(caso, conversazione, risposta,
                citate_davvero(risposta, righe), campione)
    return {"esploso": None,
            "secondi": secondi,
            # Il tempo diviso in due, perche' per tagliarlo serve sapere DOVE
            # sta: il coordinatore e' il 60-90% di ogni turno.
            "coordinatore": sum(s.get("ms") or 0 for s in passi
                                if s["strumento"] == "coordinatore") / 1000.0,
            "giri": sum(1 for s in passi if s["strumento"] == "coordinatore"),
            "mosse": [s["strumento"] for s in passi],
            "eseguite": int((re.search(r"query=(\d+)ok", ric) or [0, 0])[1]),
            "citate": len({int(n) for n in re.findall(r"\[\[\s*(\d+)\s*\]\]",
                                                      risposta or "")}),
            "risposta": risposta or "",
            "verdetto": g}


def main():
    volte = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    solo = sys.argv[2] if len(sys.argv) > 2 else None
    # Uno, o alcuni separati da virgola: iterare su un difetto non deve
    # costare sei minuti di casi che funzionano. Il banco INTERO resta
    # il cancello, perche' una correzione che aggiusta il caso rotto puo'
    # rompere quello sano — misurato il 5/10/2026, togliere una frase per
    # principio ha riportato un difetto a tre giri su tre.
    scelti = [x.strip() for x in solo.split(",")] if solo else []
    casi = [c for c in BATTERIA if not scelti or c["nome"] in scelti]
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    aziende = identita.aziende(GRUPPI)
    print("IL BANCO — %d domande x %d giri, giudice: un agente\n"
          % (len(casi), volte))
    print("%-16s %-7s %-8s %-11s %s"
          % ("caso", "esito", "secondi", "coord(giri)", "perche' no"))
    print("-" * 92)
    totali, esiti = [], {}
    for caso in casi:
        # Cosa l'archivio HA davvero su questo tema: e' la prova che permette
        # al giudice di dire «hai negato una cosa che c'e'». Si prende una
        # volta per caso, non a ogni giro.
        campione = operatori.vicinato(conn, CAMPIONE_DA, caso["domanda"],
                                      GRUPPI, aziende, quanti=10)
        ok_n, tempi, motivi, giri, coord, ngiri = 0, [], [], [], [], []
        giudicati = 0
        for _ in range(volte):
            g = un_giro(conn, caso, campione)
            giri.append(g)
            if g["esploso"]:
                motivi.append(g["esploso"])
                continue
            tempi.append(g["secondi"])
            coord.append(g["coordinatore"])
            ngiri.append(g["giri"])
            v = g["verdetto"]
            if v is None:
                # Non entra nel denominatore: un guasto del server non e' una
                # risposta sbagliata, e contarlo cosi' toglie punti a caso.
                motivi.append("GIRO PERSO: il giudice non ha deciso")
                continue
            giudicati += 1
            colpe = [(e, prova_vera(v.get(c + "_prova")))
                     for c, e in COLPE if prova_vera(v.get(c + "_prova"))]
            ok_n += not colpe
            motivi += ["%s: %s" % (e, d[:60]) for e, d in colpe]
        print("%-16s %d/%-5s %6.0fs  %4.0fs (%.1f)  %s"
              % (caso["nome"], ok_n,
                 ("%d" % giudicati if giudicati == volte
                  else "%d*" % giudicati),
                 statistics.median(tempi) if tempi else 0,
                 statistics.median(coord) if coord else 0,
                 statistics.median(ngiri) if ngiri else 0,
                 "; ".join(dict.fromkeys(motivi))[:40]))
        totali.append((caso["nome"], ok_n, giudicati,
                       statistics.median(tempi) if tempi else 0))
        esiti[caso["nome"]] = giri
    print("-" * 92)
    print("TOTALE  %d / %d        tempo mediano complessivo %.0fs"
          % (sum(t[1] for t in totali), sum(t[2] for t in totali),
             sum(t[3] for t in totali)))

    # I motivi per intero: la prima volta si leggono a mano per decidere se
    # del giudice ci si puo' fidare.
    print("\n\n== I GIRI, col motivo del giudice ==")
    for nome, giri in esiti.items():
        print("\n«%s»" % nome)
        for g in giri:
            if g["esploso"]:
                print("   ESPLOSO %s" % g["esploso"])
                continue
            v = g["verdetto"] or {}
            colpe = [(e, prova_vera(v.get(c + "_prova")))
                     for c, e in COLPE if prova_vera(v.get(c + "_prova"))]
            print("   %-3s %3.0fs %-2dq %-2dcit  %s"
                  % ("NO" if colpe else "si", g["secondi"],
                     g["eseguite"], g["citate"], " > ".join(g["mosse"])))
            for e, d in colpe:
                print("       giudice: %s — prova: «%s»" % (e, d[:120]))
            print("       %s" % " ".join(g["risposta"].split())[:160])


if __name__ == "__main__":
    main()
