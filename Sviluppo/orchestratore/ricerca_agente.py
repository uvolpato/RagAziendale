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

from orchestratore import egress, modello, recupero

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


def _chiedi(messaggi, max_tokens=800):
    return modello.chiedi(messaggi, max_tokens)


# La classificazione della domanda: QUALE via usare. E' il cervello dell'agente
# (D22): non cerca, decide COME cercare. Quattro vie, ognuna con lo strumento
# giusto gia' esistente. La classificazione e' una scelta a basso rischio:
# sbagliare via significa usare lo strumento meno adatto, non invertire il
# senso della domanda (che e' il difetto del vecchio estrae D19).
VIA_ISTRUZIONI = (
    "Classifica la domanda di una persona che cerca nei cataloghi aziendali "
    "(decorazioni, vasi, sassi, candele, profumatori, ghirlande, etichette).\n"
    "Rispondi con UNA sola parola fra queste quattro:\n"
    "\n"
    "- IDENTITA: la domanda nomina un codice o un nome di prodotto preciso "
    "(es. «quanto costa il DST2040», «il FLK2004»)\n"
    "- ATTRIBUTO: la domanda cerca prodotti con un attributo enumerabile "
    "(colore, misura, prezzo) detto esplicitamente (es. «palline viola», "
    "«etichette da 60 cm», «sotto i 2 euro»)\n"
    "- CONCRETA: la domanda nomina un oggetto o una categoria di oggetti "
    "(es. «sassi rossi», «vasi», «candele profumate»)\n"
    "- ASTRATTA: la domanda esprime un bisogno o un'occasione senza nominare "
    "un oggetto (es. «un regalo per una ragazza di 30 anni», «idee per un "
    "matrimonio», «qualcosa di elegante»)\n"
    "\n"
    "Niente spiegazioni, solo la parola.\n"
    "/no_think"
)


def _classifica(domanda) -> str:
    """La via della domanda, una delle quattro. Degrada a CONCRETA (il
    comportamento di oggi) se il modello non risponde o risponde male."""
    try:
        testo = _chiedi([{"role": "system", "content": VIA_ISTRUZIONI},
                         {"role": "user", "content": domanda}],
                        max_tokens=10).strip().upper()
    except Exception:
        return "CONCRETA"
    for via in ("IDENTITA", "ATTRIBUTO", "CONCRETA", "ASTRATTA"):
        if testo.startswith(via):
            return via
    return "CONCRETA"


# Per le domande ASTRATTE: tradurre il bisogno in CATEGORIE che il catalogo
# contiene DAVVERO. Il modello riceve le descrizioni dei documenti e deve
# scegliere le categorie FRA quelle: «regalo per una trentenne» -> candele,
# vasi, profumatori (perche' il catalogo li contiene), NON «zaini e cappelli»
# (che un catalogo di vasi non ha). Senza questo ancoraggio l'agente ragiona
# fuori dominio — misurato il 23/09/2026: «uomo che ama la montagna» dava
# tazze, zaini, cappelli da un catalogo di decorazioni.
INTENTO_ISTRUZIONI = (
    "La persona esprime un bisogno senza nominare un oggetto. Sotto trovi "
    "l'elenco di cio' che i cataloghi aziendali contengono DAVVERO.\n"
    "Scegli le CATEGORIE, prese dall'elenco, che rispondono a quel bisogno.\n"
    "Esempio: «un regalo per una ragazza di 30 anni» -> candele, vasi, "
    "profumatori, oggetti decorativi\n"
    "Solo categorie che compaiono nell'elenco: NON inventare categorie che il "
    "catalogo non ha (niente zaini, cappelli, attrezzi se non sono elencati).\n"
    "Massimo 6 categorie, separate da virgola. Se nessuna categoria dell'elenco "
    "risponde, rispondi con le categorie piu' vicine.\n"
    "Rispondi solo con l'elenco, niente spiegazioni.\n"
    "/no_think"
)


def _intento(domanda, descrizioni=None) -> str:
    """Le categorie concrete che rispondono a un bisogno astratto, o ''."""
    elenco = "\n".join(f"- {d} | {t}" for d, t in (descrizioni or []))
    corpo = domanda
    if elenco:
        corpo += f"\n\nCatalogo:\n{elenco}"
    try:
        testo = _chiedi([{"role": "system", "content": INTENTO_ISTRUZIONI},
                         {"role": "user", "content": corpo}],
                        max_tokens=120).strip()
    except Exception:
        return ""
    testo = testo.split("```")[-2] if "```" in testo else testo
    riga = next((r.strip() for r in testo.splitlines() if r.strip()), "")
    return riga[:200]


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


# ------------------------------------------------------------------ i 4 tool
#
# Ogni tool e' una VIA, col suo strumento. L'agente classifica la domanda e
# instrada al tool giusto. I tool non scelgono i permessi (i gruppi restano
# nella WHERE di recupero) e non decidono se rispondere (restituiscono pezzi):
# sono mani diverse sullo stesso filtro, non teste diverse.
import re as _re


def _tool_identita(conn, domanda, gruppi, limite):
    """IDENTITA: un codice articolo (DST2040, FLK2004...). Il ramo lessicale
    IDF gia' lo trova, ma `cerca_esatta` e' il colpo esatto: o la parola c'e'
    o non c'e'. Torna None se nella domanda non si vede nessun codice."""
    codici = _re.findall(r"\b[A-Z]{2,}[0-9]+\b", domanda)
    if not codici:
        return None
    righe = recupero.cerca_esatta(conn, codici[0], gruppi, limite)
    return righe or None


def _tool_attributo(conn, domanda, gruppi, qvec, limite):
    """ATTRIBUTO: colore/misura/prezzo espliciti. Si riusa D19 (vincoli): il
    must-match testuale. La classificazione a monte fa da guardia: qui si
    arriva solo se la domanda nomina davvero un attributo, quindi il rischio
    dell'estrazione (che in D19 invertiva il senso) e' molto piu' basso."""
    from orchestratore import vincoli as v
    _, _, trovati = v.estrae(domanda)
    if not trovati:
        return None
    regex = v.regex(trovati)
    righe, _ = recupero.cerca(conn, domanda, gruppi, qvec, limite, vincolo=regex)
    return righe or None


def _tool_astratta(conn, domanda, gruppi, limite, documenti=None):
    """ASTRATTA: un bisogno senza oggetto. Si traduce l'intento in categorie
    ANCORATE alle descrizioni dei documenti (indice), e si cerca su TUTTI i
    documenti — non ristretti, perche' l'indice su una domanda astratta sceglie
    documenti che non assomigliano a nessun catalogo specifico. Se il modello
    non sa tradurlo, si torna alla ricerca diretta."""
    from orchestratore import indice
    descrizioni = indice.descrizioni_visibili(conn, gruppi)
    categorie = _intento(domanda, descrizioni)
    if not categorie:
        return None
    qv = recupero.embedding(categorie)
    righe, _ = recupero.cerca(conn, categorie, gruppi, qv, limite)
    return righe or None


def _tool_concreta(conn, domanda, gruppi, qvec, limite, vincolo, documenti):
    """CONCRETA (e fallback): la ricerca diretta, con l'eventuale giro
    cerca-guarda-riprova. E' il comportamento di oggi."""
    righe, degradato = recupero.cerca(conn, domanda, gruppi, qvec, limite,
                                      vincolo=vincolo, documenti=documenti)
    if degradato:
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
            print(f"agente di ricerca fermo ({type(e).__name__}: {e})", flush=True)
            break
        if not nuova:
            break
        cercate.append(nuova)
        righe, _ = recupero.cerca(conn, nuova, gruppi, recupero.embedding(nuova),
                                  limite, vincolo=vincolo, documenti=documenti)
        for r in righe:
            viste.setdefault(r["id"], r)
    if len(cercate) > 1:
        print(f"ricerche: {json.dumps(cercate, ensure_ascii=False)}", flush=True)
    # L'unione di piu' ricerche non ha un ordine suo: il cross-encoder ordina
    # sulla domanda VERA, mai sulle riformulazioni.
    return recupero.riordina(domanda, list(viste.values()), limite), False


def _comprensione(conn, domanda, gruppi, qvec, limite, documenti):
    """La via UNICA: capire insieme cosa cercare e quali attributi deve avere.

    E' cio' che fa una persona che legge «sassi rossi»: non separa «sassi» da
    «rossi», capisce una cosa sola con una proprieta'. `vincoli.estrae` gia'
    produce tutto insieme (intento + termini multilingue + attributi); qui si
    applica nello stesso atto di ricerca: query arricchita + must-match.

    Torna None se l'estrazione non capisce niente o la ricerca non trova: il
    chiamante degrada sul routing a 4 vie, che resta come rete di sicurezza.
    """
    from orchestratore import vincoli as v
    _, intent_termini, trovati = v.estrae(domanda)
    if not intent_termini and not trovati:
        return None
    regex = v.regex(trovati)
    query = v.query_di_ricerca(domanda, intent_termini, trovati)
    qv = recupero.embedding(query) if qvec is None else qvec
    righe, _ = recupero.cerca(conn, query, gruppi, qv, limite,
                              vincolo=regex, documenti=documenti)
    return righe or None


def cerca(conn, domanda: str, gruppi: list[str], qvec=None, limite: int = 8,
          vincolo: str = "", documenti: list[str] | None = None,
          domanda_vera: str | None = None):
    """L'agente: capisce la domanda (via unica) e, se non basta, instrada.

    Stessa firma di `recupero.cerca` (con un parametro in piu'), cosi' il
    chiamante non sa quale dei due usa. `domanda` e' la query da CERCARE (puo'
    essere arricchita coi sinonimi); `domanda_vera` e' cio' che la persona ha
    scritto, e serve SOLO a capire.
    """
    if not ACCESO:
        return recupero.cerca(conn, domanda, gruppi, qvec, limite,
                              vincolo=vincolo, documenti=documenti)

    vera = domanda_vera or domanda

    # 1. Via UNICA: capire tutto insieme (oggetto + attributi). Prioritaria.
    righe = _comprensione(conn, vera, gruppi, qvec, limite, documenti)
    print(f"[agente] vera={vera!r} via_unica={bool(righe)} "
          f"documenti={documenti!r}", flush=True)
    if righe:
        return recupero.riordina(domanda, righe, limite), False

    # 2. Rete di sicurezza: il routing a 4 vie, per cio' che la via unica non
    #    copre (identita' e astratte senza attributi).
    via = _classifica(vera)
    if via == "IDENTITA":
        righe = _tool_identita(conn, vera, gruppi, limite)
        if righe:
            return recupero.riordina(domanda, righe, limite), False
    elif via == "ASTRATTA":
        righe = _tool_astratta(conn, vera, gruppi, limite, documenti)
        if righe:
            return recupero.riordina(domanda, righe, limite), False

    return _tool_concreta(conn, domanda, gruppi, qvec, limite, vincolo, documenti)


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
