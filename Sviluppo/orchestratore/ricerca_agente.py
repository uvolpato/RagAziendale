"""Cercare PIU' VOLTE, guardando cosa torna (decisione D17, solo la ricerca).

Una ricerca sola sbaglia quando le parole della domanda non sono le parole del
documento. Misurato il 22/09/2026: «ciottoli neri lucidi» non trova niente,
mentre la stessa domanda scritta per esteso — «effetto molto lucido, quasi a
specchio» — trova SPIEGELSAND, *mirror sand*. Il ponte fra le due lingue non
lo costruisce una riscrittura alla cieca: quella e' stata bocciata quattro
volte e inventa attributi («Marrakesch» → «sfere in resina», che sono di
metallo). Lo costruisce chi GUARDA i risultati e riprova con altre parole.

E' quello che fa una persona davanti a un motore di ricerca, ed e' l'unica
cosa che questo modulo aggiunge: un giro di cerca-guarda-riprova.

## Cosa NON puo' fare

**Non sceglie i permessi.** I gruppi arrivano dal chiamante e finiscono nella
WHERE della query, come sempre (`recupero.py`). L'agente sceglie le PAROLE.
Se un giorno potesse scegliere anche dove cercare, il filtro ACL passerebbe da
una `WHERE` a una convinzione del modello, ed e' esattamente la riga che il
progetto non attraversa.

**Non decide se rispondere.** Restituisce pezzi, non risposte. Il gate, la
contaminazione e il prompt restano dove sono.

**Non puo' costare tempo illimitato.** Al massimo GIRI ricerche; se il modello
non risponde o risponde male si tiene quello che si ha, che nel caso peggiore
e' esattamente il risultato di oggi.

## Perche' non LangGraph

C'e' gia' fra le dipendenze, e sarebbe un grafo con due nodi e un arco. Il
giro e' un `for`: aggiungere una libreria per scriverlo diversamente non lo
renderebbe piu' chiaro, e renderebbe piu' difficile leggere cosa succede
quando una risposta e' sbagliata.
"""
import json
import os

from orchestratore import egress, recupero

LITELLM = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000").rstrip("/")
MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
ROTTA = os.environ.get("LLM_RAGIONAMENTO", "ragionamento")
SECONDI = float(os.environ.get("AGENTE_TIMEOUT", "30"))

# SPENTO, e con un numero dietro (22/09/2026, confronto appaiato sulle 20
# domande corte):
#
#     ricerca sola      14/20    1,1 s a domanda
#     agente, 3 giri    14/20   11,7 s a domanda
#
# Dieci volte il costo, zero guadagno. Ha riprovato su cinque domande e
# nessuna seconda ricerca ha portato dentro il pezzo giusto — nemmeno
# «ciottoli neri brillanti», che sembrava azzeccata perche' BRILLANT e' il
# nome di una linea del catalogo. Su tre delle cinque ha riprovato dove non
# serviva: «diamanti di vetro misure miste», poi «misure diverse», per
# ritrovare quello che il primo giro aveva gia' in posizione 1.
#
# Cosa questo NON dimostra: la D17 nasceva per le domande a PIU' PASSI
# («confronta i prezzi di EUROSAND e FLEURAMI»), e fra le 24 domande d'oro non
# ce n'e' nessuna cosi'. Qui e' stato provato sul ponte di vocabolario, che e'
# un altro problema. Per il suo scopo dichiarato resta non misurato, perche'
# manca il metro — non perche' abbia funzionato.
ACCESO = os.environ.get("RICERCA_AGENTE", "") == "si"
# Quante ricerche in tutto, compresa la prima. Tre perche' la prima e' la
# domanda com'e', e restano due tentativi con altre parole: il quarto giro
# costa quanto i primi tre e, sulle 20 domande d'oro, non cambiava niente.
GIRI = int(os.environ.get("AGENTE_GIRI", "3"))
# Quanti pezzi si fanno vedere al modello fra un giro e l'altro. Non il testo
# intero: serve a capire SE ha trovato la cosa giusta, non a rispondere.
ASSAGGIO = int(os.environ.get("AGENTE_ASSAGGIO", "5"))
CARATTERI = 160

ISTRUZIONI = (
    "Stai cercando dentro un archivio di documenti aziendali per conto di una persona.\n"
    "Hai fatto una ricerca e vedi i titoli di cio' che e' tornato. Decidi:\n"
    "\n"
    "- se fra i risultati c'e' cio' che la persona ha chiesto, rispondi BASTA\n"
    "- altrimenti proponi UNA nuova ricerca, con parole DIVERSE da quelle gia' usate\n"
    "\n"
    "Per la nuova ricerca guarda cosa e' tornato: i documenti usano parole loro, "
    "spesso in un'altra lingua o piu' tecniche di quelle della persona. Se i risultati "
    "parlano di una cosa vicina ma sbagliata, cerca il termine che distingue le due.\n"
    "\n"
    "NON inventare caratteristiche del prodotto che la persona non ha detto: materiali, "
    "misure, prezzi, marche. Puoi usare solo le parole della domanda e le parole che "
    "vedi nei risultati.\n"
    "\n"
    "Rispondi con UNA riga: «BASTA» oppure le parole da cercare. Niente spiegazioni.\n"
    # Qui il modello RAGIONA, ed e' l'unica cosa che lo rende utile.
    #
    # Prima versione con «/no_think» e 60 token: il ragionamento di Qwen3
    # finisce in `reasoning_content`, quindi `content` tornava VUOTO e l'agente
    # si fermava sempre al primo giro. Ha misurato se stesso per venti domande.
    # Aggiunto «/no_think» come si deve, rispondeva — ma rispondeva BASTA
    # anche davanti a risultati sbagliati: non si accorgeva di aver fallito.
    # Lasciandolo ragionare, sulle stesse quattro domande, propone «ciottoli
    # neri brillanti» dove aveva sbagliato e dice BASTA dove aveva trovato.
    #
    # Si paga: circa undici secondi per decisione, su ogni domanda, anche su
    # quelle che andavano bene al primo colpo. E' il numero che decide se
    # questo modulo si accende (vedi ACCESO).
)


def _chiedi(messaggi):
    testa = {"Authorization": f"Bearer {MASTER_KEY}"} if MASTER_KEY else {}
    corpo = {"model": ROTTA, "messages": messaggi, "temperature": 0, "max_tokens": 800}
    with egress.client(timeout=SECONDI, verify=False) as c:
        r = c.post(f"{LITELLM}/v1/chat/completions", json=corpo, headers=testa)
        r.raise_for_status()
        return (r.json()["choices"][0]["message"].get("content") or "").strip()


def _assaggio(righe):
    """Cosa si fa vedere al modello: da che documento e che pagina, e l'inizio
    del testo. Non tutto il pezzo — deve decidere se riprovare, non rispondere,
    e una tabella di prezzi intera gli riempirebbe il contesto per niente."""
    fuori = []
    for i, r in enumerate(righe[:ASSAGGIO], 1):
        pag = f", p. {r['page']}" if r.get("page") is not None else ""
        testo = " ".join((r.get("content") or "").split())[:CARATTERI]
        fuori.append(f"{i}. ({r.get('documento', '?')}{pag}) {testo}")
    return "\n".join(fuori) or "(nessun risultato)"


def _pulisci(testo, gia_cercate):
    """La riga del modello -> parole da cercare, oppure None per fermarsi."""
    # Le scorie si tolgono PRIMA di scegliere la riga: «/no_think» e' la
    # direttiva che mandiamo NOI, e a volte il modello la ricopia in cima alla
    # risposta. Prendendo la prima riga non vuota si leggerebbe quella.
    pulito = testo or ""
    for scoria in ("/no_think", "/think", "Ricerca:", "Cerca:"):
        pulito = pulito.replace(scoria, "")
    riga = next((r.strip() for r in pulito.splitlines() if r.strip()), "")
    riga = riga.strip('"').strip()
    if not riga or riga.upper().startswith("BASTA") or len(riga) > 200:
        return None
    # Gia' cercata: ripeterla costa un giro e non cambia niente. Succede
    # quando il modello e' convinto della prima formulazione.
    if riga.lower() in {g.lower() for g in gia_cercate}:
        return None
    return riga


def cerca(conn, domanda: str, gruppi: list[str], qvec=None, limite: int = 8):
    """Come `recupero.cerca`, ma cercando piu' volte: (righe, degradato).

    Stessa firma e stesso contratto della ricerca normale, cosi' il chiamante
    non deve sapere quale delle due sta usando — e spegnere l'agente e' una
    variabile d'ambiente, non una modifica.
    """
    righe, degradato = recupero.cerca(conn, domanda, gruppi, qvec, limite)
    if not ACCESO or degradato:
        return righe, degradato

    viste = {r["id"]: r for r in righe}
    cercate = [domanda]
    for _ in range(GIRI - 1):
        try:
            nuova = _pulisci(_chiedi([
                {"role": "system", "content": ISTRUZIONI},
                {"role": "user", "content":
                    f"La persona ha chiesto: {domanda}\n\n"
                    f"Ricerche gia' fatte: {'; '.join(cercate)}\n\n"
                    f"Risultati dell'ultima:\n{_assaggio(righe)}"},
            ]), cercate)
        except Exception as e:
            # Il modello non risponde: si tiene quello che si ha, che e'
            # esattamente il risultato della ricerca di sempre.
            print(f"agente di ricerca fermo ({type(e).__name__}: {e})", flush=True)
            break
        if not nuova:
            break
        cercate.append(nuova)
        righe, _ = recupero.cerca(conn, nuova, gruppi, recupero.embedding(nuova), limite)
        for r in righe:
            viste.setdefault(r["id"], r)

    if len(cercate) > 1:
        print(f"ricerche: {json.dumps(cercate, ensure_ascii=False)}", flush=True)
    # L'unione di piu' ricerche non ha un ordine suo: i punteggi di ricerche
    # diverse non si sommano e non si confrontano. A ordinarla e' il
    # cross-encoder, sulla domanda VERA — mai sulle riformulazioni, che sono
    # un mezzo e non cio' che la persona ha chiesto.
    return recupero.riordina(domanda, list(viste.values()), limite), False


def _prova():
    """Le regole che non chiamano il modello."""
    assert _pulisci("BASTA", ["x"]) is None
    assert _pulisci("  basta, ci sono  ", ["x"]) is None
    assert _pulisci("", ["x"]) is None
    assert _pulisci("pietre nere", ["pietre nere"]) is None, "ripete una ricerca gia' fatta"
    assert _pulisci('"sabbia a specchio"', []) == "sabbia a specchio"
    assert _pulisci("/no_think\nsabbia specchio", []) == "sabbia specchio"
    assert _pulisci("x" * 300, []) is None, "riga assurda"
    assert _assaggio([]) == "(nessun risultato)"
    assert "p. 7" in _assaggio([{"documento": "c.pdf", "page": 7, "content": "a  b"}])
    print("ricerca_agente: regole verdi")


if __name__ == "__main__":
    _prova()
