"""Interrogazione del database scritta dal MODELLO (D22).

Il problema che risolve: fino a ora la domanda passava da quattro strumenti
scritti a mano, e ognuno aveva dentro una regola fissa che decideva al posto
del modello (il peso delle figure, il colore che diventa AND, `estrae` che
sceglie cosa e' un vincolo). Cinque interpreti della stessa domanda, e ognuno
poteva sbagliare: misurato il 30/09/2026, «nastri con palloni da calcio»
cercava «palloni da calcio» in un campo scritto in inglese, 0 riscontri, mentre
il prodotto vero era a p.22 con «white ribbon with printed soccer balls».

Qui non ci sono scorciatoie: il modello vede DOVE SONO I DATI e COME SONO
scritti (vedi `mappa.py`) e scrive la query come farebbe io. Se i cataloghi
cambiano lingua, cambia la mappa e lui si adatta: nessuna formula da
riparare quando arriva un attributo nuovo.

COSA NON E' LIBERO, e non lo sara' mai. Il modello scrive il SELECT, ma non
scrive i permessi: il filtro sui gruppi e sull'azienda lo mette SEMPRE questo
modulo, e non si puo' toglierlo ne' vederlo (vedi `_applica_acl`). Non puo'
scrivere, non pu' cancellare, non pu' usare tabelle che non sono di contenuto.
Un SELECT puo' sbagliare e restituire poco: non puo' uscire dai documenti che
l'utente puo' vedere, e non puo' fare full-scan silenzioso (c'e' un timeout e
un tetto di righe). La sicurezza e' in codice, non nel prompt: un prompt si
aggira, `WHERE azienda = %s` no.
"""
import re
import time

from psycopg import sql as S

# Le tabelle che il modello puo' interrogare, e cosa ci puo' chiedere.
# Non e' una scelta del modello: sono i dati del prodotto. Fuori da qui ci
# sono stato interno (gruppi, tasti, sincronizzazioni, anomalie): non sono
# contenuto dell'archivio e non si mostrano.
TABELLE = {
    "immagini": {
        "colonna": "descrizione",
        "descrizione": "La didascalia della foto di un prodotto. 15143 righe. "
                       "Un unico campo di testo `descrizione`, in INGLESE, con "
                       "etichette: «object: ... material: ... shape/size: ...», "
                       "a volte «Colours: ...».",
    },
    "chunks": {
        "content": "Il testo del documento, come nell'originale. 17582 righe. "
                   "Multilingue. Qui stanno prezzi, codici articolo, nomi di "
                   "prodotto, procedure, policy.",
    },
    "documenti": {
        "documento": "Il nome del file (tipo e anno). 14 righe.",
        "tipo": "catalogo | manuale | guida | listino.",
        "stato": "indicizzato | in_lettura | ...",
        "pezzi": "Quanti pezzi di testo ha il documento.",
        "figure_totali": "Quante foto contiene il catalogo.",
    },
    "indice": {
        "testo": "L'indice dei cataloghi: che cosa c'e' in ogni pagina. "
                 "ADESSO E' VUOTO (zero righe, il 30/09/2026): si puo' "
                 "interrogare, ma non torna niente finche' non si genera.",
    },
}

# Le colonne che il modello PUO' mostrare e su cui puo' filtrare. Puo'
# scegliere qualsiasi sottoinsieme: elencare tutto significa che ogni nuova
# colonna e' subito disponibile, senza toccare questo codice.
COLONNE = ("id", "source_id", "documento", "page", "descrizione", "content",
           "tipo", "stato", "pezzi", "figure_totali")

# Le tabelle hanno una colonna di riferimento che identifica il documento.
RIFERIMENTO = {"immagini": "documento", "chunks": "documento",
               "documenti": "documento"}

LIMITE_RIGHE = 40           # oltre, il modello riceve solo rumore
SECONDI = float(__import__("os").environ.get("SQL_AGENTE_TIMEOUT", "15"))
MAX_CARATTERI = 6000        # non svuotiamo il contesto con 40 righe enormi

VIETATE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|"
    r"copy|call|do|execute|vacuum|analyze|refresh|set|reset|begin|commit|"
    r"rollback|lock|listen|notify|dblink|pg_sleep|pg_read_file|"
    r"pg_ls_dir|current_setting|version)\b",
    re.I,
)
# Funzioni che possono costare un full-scan o eseguire codice.
VIETATE_FUNZIONI = re.compile(
    r"\b(pg_sleep|pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|"
    r"lo_import|lo_export|setseed|random|generate_series|unnest)\s*\(",
    re.I,
)


def _tabella_permessa(tabella: str) -> bool:
    return tabella in TABELLE


def controlla(sql: str) -> list[str]:
    """Gli errori di una query del modello, in chiaro. Vuoto = accettata."""
    problemi = []
    if not sql or not sql.strip():
        return ["query vuota"]
    testo = sql.strip().rstrip(";")
    if ";" in testo:
        problemi.append("una sola query per volta: togli il ';' in mezzo")
    if VIETATE.search(testo):
        parole = sorted(set(m.group(0).lower() for m in VIETATE.finditer(testo)))
        problemi.append("puoi solo LEGGERE (SELECT): niente " + ", ".join(parole))
    if VIETATE_FUNZIONI.search(testo):
        problemi.append("funzione non ammessa")
    if not re.match(r"^\s*(select|with)\b", testo, re.I):
        problemi.append("la query deve cominciare con SELECT (o WITH)")
    # Le tabelle citate devono essere tutte di contenuto: se il modello ne
    # scrive una che non conosciamo, non la eseguiamo. Il caso piu' freqente e'
    # «immagini.descrizione»: crede che `object:` e `Colours:` siano colonne
    # (la mappa gliele ha mostrate come etichette dentro il testo). Si risponde
    # dicendo qual e' la colonna vera, altrimenti riscrive la stessa query.
    citate = []
    for t in re.findall(r"\b(?:from|join|update|into)\s+([a-z_][a-z0-9_.]*)", testo, re.I):
        if "." in t:
            tab, colonna = t.split(".", 1)
            if tab.lower() in TABELLE:
                problemi.append(
                    f"«{t}» non esiste: in {tab} la colonna vera e' "
                    f"`{TABELLE[tab].get('colonna') or TABELLE[tab].get('content') or list(TABELLE[tab])[0]}`, "
                    f"e `{colonna}` e' solo un'etichetta dentro quel testo")
            else:
                problemi.append(f"tabella non consultabile: {tab}")
            citate.append(tab.lower())
        else:
            if not _tabella_permessa(t):
                problemi.append(f"tabella non consultabile: {t}")
            citate.append(t.lower())
    # UNA tabella per query. Non e' una limitazione arbitraria: i permessi
    # vengono iniettati sotto il PRIMO `FROM` (vedi `_applica_acl`), quindi con
    # JOIN, UNION, subquery o CTE la seconda tabella verrebbe letta SENZA il
    # filtro dei gruppi — una fuga di dati, non un difetto di ricerca. Il
    # modello che vuole due fonti fa due chiamate, e gliele vede entrambe.
    # Il modello ci arriva da solo (misurato il 30/09/2026 su «vasi con fiori
    # blu»: ha scritto `immagini ... UNION ... chunks`).
    if len(citate) > 1:
        problemi.append(
            "una tabella sola, una volta sola, perché i permessi si applicano "
            "al PRIMO FROM e tutto quello che viene dopo (JOIN, UNION, "
            "subquery, CTE) leggerebbe senza filtro. Hai citato "
            + ", ".join(citate) + ": fai due chiamate, una per tabella")
    # Le colonne citate devono esistere: evita che il peso vada a spese del
    # permesso (una colonna inesistente fa fallire tutta la query).
    #
    # Si guarda il testo SENZA le stringhe, e gli operatori-parola vogliono i
    # confini. Prima si scandiva tutto, `IS` compreso e senza `\b`: dentro
    # 'chr-is-tmas' la regex leggeva la colonna «chr» seguita da IS, e
    # respingeva la query con «colonna non conosciuta: chr». Qualunque termine
    # di ricerca che contenesse «is» era irricercabile — «christmas», «lista»,
    # «misura», «artistico» — con un errore che parlava di una colonna che il
    # modello non aveva scritto, quindi non correggibile: riscriveva, veniva
    # respinto di nuovo, e finiva per dire «non ho trovato nulla» su roba che
    # c'era (1/10/2026, «palline rosse o gialle»: 53 righe in archivio).
    senza_stringhe = re.sub(r"'(?:[^']|'')*'", "''", testo)
    for c in re.findall(r"\b([a-z_][a-z0-9_]*)\s*(?:~~\*?|~\*|~|=|>=|<=|>|<|"
                        r"\bILIKE\b|\bLIKE\b|\bIS\b|\bANY\b)", senza_stringhe, re.I):
        if c.lower() in ("and", "or", "not", "where", "like", "ilike", "is",
                         "notnull", "null", "any", "all", "case", "when",
                         "then", "else", "end", "distinct", "as", "on", "in",
                         "between", "order", "by", "limit", "offset", "desc",
                         "asc", "count", "sum", "avg", "min", "max"):
            continue
        if c.lower() not in COLONNE:
            problemi.append(f"colonna non conosciuta: {c}")
    # Filtrare per source_id non e' pericoloso (i permessi restano applicati),
    # ma e' inutile: i documenti visibili sono gia' decisi dal sistema. Il
    # messaggio e' sul testo grezzo perche' la verifica sulle colonne non lo
    # vede: li precede un nome di tabella o parola chiave.
    if re.search(r"\bsource_id\s*(?:=|>|<|~|IN\b)", testo, re.I):
        problemi.append("non filtrare per source_id: i documenti che quest'utente "
                        "puo' vedere sono gia' decisi dal sistema, tu scrivi "
                        "cosa cercare")
    # Un filtro ~* con piu' parole e' una trappola: 'white ribbon' cerca QUELLA
    # coppia lettera per lettera, e nelle didascalie «white» e «ribbon» non
    # stanno vicine (misurato il 30/09/2026: 'white ribbon' -> 0 righe contro le
    # 21 di 'white' AND 'ribbon'). Non e' un caso specifico: e' la classe di
    # ogni frase scritta dove il dato ha parole separate. Si respinge e si dice
    # di spezzare, come per la tabella sola.
    # Si guarda ogni ALTERNATIVA dentro i `|`, non il filtro intero.
    #
    # Prima bastava uno spazio in un punto qualsiasi per respingere tutta la
    # condizione, e il messaggio diceva che «'star|star motif|stellar' non
    # trova niente» — falso, `star` da sola trova quaranta righe. Il modello
    # leggeva una critica all'intero filtro, non sapeva quale pezzo togliere,
    # e riscriveva la stessa cosa: misurato l'1/10/2026, quattro query di fila
    # respinte su «nastri con le stelle» e zero ricerche eseguite. Un errore
    # che non dice QUALE pezzo e' sbagliato non e' correggibile.
    for m in re.finditer(r"(?:~~\*|~\*|~~|ilike|like)\s*'([^']+)'", testo, re.I):
        frasi = [a for a in m.group(1).split("|") if re.search(r"\s", a.strip())]
        if not frasi:
            continue
        buone = [a for a in m.group(1).split("|") if a.strip() and not re.search(r"\s", a.strip())]
        problemi.append(
            "dentro il filtro '%s' c'e' un'alternativa con uno spazio: %s. "
            "Le didascalie sono etichette e le parole non stanno attaccate, "
            "quindi quella coppia non la trova mai. %s"
            % (m.group(1), ", ".join(f"'{f.strip()}'" for f in frasi),
               ("Togli quella e tieni il resto: '%s'." % "|".join(buone))
               if buone else
               "Spezzala: una parola per condizione, unite con AND "
               "(es. descrizione ~* 'white' AND descrizione ~* 'ribbon')."))
        break
    return problemi


def _applica_acl(sql: str, gruppi: list[str], aziende: list[str]) -> str:
    """I permessi, imposti dal CODICE e non dal modello.

    Il modello sceglie la sua WHERE, questa c'e' comunque e non la puo'
    togliere: nasce qui, dentro questa funzione. Non usa l'alias che il modello
    ha scritto (o non ha scritto: se scrive `FROM immagini` senza alias,
    riferirsi a `i.source_id` faceva fallire la query — misurato il
    30/09/2026), ma il nome della tabella, che e' sempre quello.

    Le ACL stanno in `sources`, lo stesso predicato di `documento.visible`.
    Se `sources` e' vuota in questo ambiente il filtro non taglia nulla, ma
    resta scritto: e' la condizione che il resto del sistema usa, e quando si
    riempie vale senza toccare nient'altro.
    """
    tabella = re.search(r"\bfrom\s+([a-z_]+)", sql, re.I)
    if not tabella or tabella.group(1).lower() not in TABELLE:
        return sql
    nome = tabella.group(1)
    # `aziende` NON e' la lista dei gruppi: viene dai gruppi Keycloak con il
    # prefisso «azienda-» tolto (identita.aziende). Passare li' i gruppi
    # («azienda-decobrands» dove ci si aspetta «decobrands») faceva venire
    # zero righe senza alcun errore: la query era lecita, il filtro tagliava
    # tutto (misurato il 30/09/2026).
    dove = (f"{nome}.source_id IN (SELECT id FROM sources WHERE stato = 'attiva' "
            f"AND aziende && %s::text[] AND acl_groups && %s::text[])")
    corpo = sql.rstrip().rstrip(";")
    if re.search(r"\bwhere\b", corpo, re.I):
        return re.sub(r"\bwhere\b", f"WHERE {dove} AND", corpo, count=1, flags=re.I)
    # Nessuna WHERE: la accodiamo PRIMA di ORDER BY / LIMIT / GROUP BY, o
    # finisce dentro una clausola e la query non gira.
    for clausola in (r"\border\s+by\b", r"\blimit\b", r"\bgroup\s+by\b",
                     r"\boffset\b", r"\bhaving\b"):
        m = re.search(clausola, corpo, re.I)
        if m:
            return corpo[:m.start()] + f" WHERE {dove} " + corpo[m.start():]
    return corpo + f" WHERE {dove}"


def esegui(conn, sql: str, gruppi: list[str], aziende: list[str],
           t0: float | None = None) -> tuple[list, list[str]]:
    """(righe, problemi). Se i problemi sono non vuoti la query NON gira.

    Il tetto e' sui secondi e sulle righe, non sulla fiducia: una query
    sbagliata costa al massimo qualche secondo e non porta via il servizio.
    """
    problemi = controlla(sql)
    if problemi:
        return [], problemi
    sql = _applica_acl(sql, gruppi, aziende)
    if t0 is not None and time.monotonic() - t0 > SECONDI:
        return [], [f"hai impiegato troppo tempo a pensare la ricerca ({SECONDI:.0f}s), semplifica"]
    try:
        with conn.cursor() as cur:
            # SET LOCAL non accetta parametri bind: il valore e' un intero calcolato qui
            # (SECONDI), mai roba che viene dal modello, quindi va bene
            # formattarlo nella stringa.
            cur.execute(f"SET LOCAL statement_timeout = {int(SECONDI * 1000)}")
            cur.execute(sql, (aziende, gruppi))
            righe = cur.fetchmany(LIMITE_RIGHE + 1)
    except Exception as e:
        # Una query sbagliata lascia la transazione in stato abortito: senza
        # rollback, TUTTE le query dopo falliscono con lo stesso errore e il
        # modello riceve solo rumore per il resto del turno (misurato il
        # 30/09/2026: la prima sintassi sbagliata faceva fallire anche le
        # tre successive, che erano giuste).
        try:
            conn.rollback()
        except Exception:
            pass
        tipo = type(e).__name__
        # Gli errori piu' frequenti si dicono per intero: altrimenti il modello
        # non sa cosa correggere e riscrive la stessa query.
        testo = str(e).strip()
        if "only '%s', '%b', '%t' are allow" in testo:
            return [], [
                "LIKE con i % non si puo' usare cosi'. Usa ~* che cerca la "
                "parola senza '%': al posto di "
                "descrizione ILIKE '%white%' scrivi descrizione ~* 'white'"]
        if "does not exist" in testo or "Undefined" in tipo:
            return [], [f"colonna o tabella inesistente: {testo[:200]}"]
        if "syntax error" in testo:
            return [], [f"errore di sintassi: {testo[:200]}"]
        if "must appear" in testo or "GROUP BY" in testo:
            return [], [f"GROUP BY incompleto: {testo[:200]}"]
        if "statement timeout" in testo or "canceling" in testo:
            return [], [f"query troppo lenta: semplifica, non scandisca 15143 righe"]
        return [], [f"query non eseguita ({tipo}): {testo[:200]}"]
    if len(righe) > LIMITE_RIGHE:
        righe = righe[:LIMITE_RIGHE]
    return righe, []


def _prova():
    """Le regole che non chiamano il database."""
    assert controlla("") == ["query vuota"], "una query vuota va rifiutata"
    assert not controlla("SELECT 1"), "una SELECT secca e' lecita"
    assert controlla("SELECT documento FROM immagini; SELECT 1"), \
        "due query in una vanno rifiutate"
    for cattiva in ("DELETE FROM immagini",
                    "DROP TABLE immagini",
                    "SELECT * FROM tabelle_utenti",
                    "SELECT segreti FROM collegamenti",
                    "SELECT pg_sleep(10) FROM immagini",
                    "UPDATE immagini SET descrizione = 'x'",
                    # Due tabelle in una: la seconda leggerebbe i documenti di
                    # altri gruppi, perche' i permessi si applicano a una
                    # tabella sola. Rifiutata per questo, non per sintassi.
                    "SELECT documento, page FROM immagini WHERE page=1 "
                    "UNION SELECT documento, page FROM chunks WHERE page=1",
                    "SELECT i.page, c.content FROM immagini i JOIN chunks c "
                    "ON i.page = c.page",
                    "WITH a AS (SELECT page FROM immagini) SELECT * FROM a "
                    "UNION SELECT page FROM chunks"):
        assert controlla(cattiva), f"doveva essere rifiutata: {cattiva}"
    assert "una tabella sola" in " ".join(controlla(
        "SELECT page FROM immagini UNION SELECT page FROM chunks")), \
        "la query su due tabelle deve dirlo perche'"
    # Un termine di ricerca con «is» dentro non e' una colonna. Erano
    # irricercabili «christmas», «lista», «misura», «artistico»: il controllo
    # sulle colonne leggeva dentro le stringhe e IS senza confini di parola.
    for parola in ("christmas", "lista", "misura", "artistico", "is"):
        q = f"SELECT id FROM immagini WHERE descrizione ~* '{parola}'"
        assert not controlla(q), f"'{parola}' deve essere cercabile: {controlla(q)}"
    # Ma una colonna che non esiste DAVVERO va ancora respinta.
    assert controlla("SELECT id FROM immagini WHERE colore = 'rosso'"), \
        "una colonna inesistente fuori dalle stringhe va respinta"
    for buona in ("SELECT documento, page, descrizione FROM immagini "
                  "WHERE descrizione ~* 'soccer|football' LIMIT 20",
                  "SELECT documento, page FROM immagini "
                  "WHERE descrizione ~* 'heart' ORDER BY page",
                  "SELECT d.documento, count(*) FROM immagini i "
                  "GROUP BY d.documento"):
        assert not controlla(buona), f"doveva passare: {buona} -> {controlla(buona)}"
    # Una frase in un ~* non trova niente: va respinta, e l'errore deve dire
    # QUALE pezzo e' sbagliato.
    for frase in ("WHERE descrizione ~* 'white ribbon'",
                  "WHERE descrizione ~* 'red hearts' AND descrizione ~* 'white'"):
        assert "una parola per condizione" in " ".join(controlla(frase)).lower(), \
            f"la frase in ~* va respinta: {frase}"
    # Con piu' alternative, si nomina SOLO quella con lo spazio e si suggerisce
    # il filtro ripulito: prima si respingeva tutto dicendo che «star|star
    # motif|stellar non trova niente», che e' falso e irreparabile.
    detto = " ".join(controlla("SELECT id FROM immagini WHERE descrizione "
                               "~* 'star|star motif|stellar'"))
    assert "'star motif'" in detto, detto
    assert "'star|stellar'" in detto, "deve suggerire il filtro senza la frase"
    # Un filtro di sole parole singole non si tocca.
    assert not controlla("SELECT id FROM immagini WHERE descrizione "
                         "~* 'star|stellar|sterne'")
    # I permessi non si tolgono, qualunque sia la query.
    for q in ("SELECT documento FROM immagini WHERE page = 3",
              "SELECT documento FROM immagini WHERE page = 3 ORDER BY page",
              "SELECT documento FROM immagini",
              "SELECT documento FROM immagini LIMIT 5"):
        per = _applica_acl(q, ["acquisti"], ["decobrands"])
        assert "sources" in per and "acl_groups" in per, (q, per)
        assert per.upper().count("ORDER BY") == q.upper().count("ORDER BY"), per
    # Aziende e gruppi sono due cose diverse: l'azienda arriva gia' priva del
    # prefisso «azienda-».
    per = _applica_acl("SELECT documento FROM immagini", ["azienda-decobrands"],
                       ["decobrands"])
    assert "acl_groups" in per, per
    print("sql_agente: ACL e controlli ok")


if __name__ == "__main__":
    _prova()
