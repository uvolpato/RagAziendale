"""Gli operatori che mancano alla query del modello: il VETTORE e il GLOSSARIO.

`sql_agente` da' al modello un dialetto con un solo operatore, `~*`, che cerca
lettera per lettera. Ma meta' del database e' vettoriale — `immagini.embedding`
e `chunks.embedding` sono `vector(1024)` con indice HNSW — e il modello non lo
sa, perche' nessuno gliel'ha detto. Misurato il 30/09/2026 su «nastri bianchi
con cuori rossi», pagine giuste nelle prime 9 (su 7 esistenti):

    ~* letterale                                  5 su 7
    ORDER BY embedding <=>                        6 su 7
    WHERE ~* 'ribbon|tape' ORDER BY embedding <=> 7 su 7

L'embedding pero' sono 1024 numeri: il modello non lo puo' scrivere. Qui ci
sono due funzioni che scrive lui e che compila il codice:

    SIMILE('nastri bianchi con cuori rossi')  ->  (embedding <=> '[...]'::vector)
    TERMINI('nastri')                         ->  'nastri|ribbon|baender|tapes'

Non e' interpretare la domanda al posto suo: e' dargli una penna che scrive
vettori. La sostituzione e' testuale, su due funzioni che nel dialetto di
Postgres non esistono, quindi non puo' collidere con SQL vero.

Questo modulo NON tocca `sql_agente`: ne riusa i controlli (`controlla`) e
l'iniezione dei permessi (`_applica_acl`), che restano l'unica versione di
quelle regole.
"""
import re
import time

from orchestratore import recupero, sql_agente

# L'apostrofo dentro la stringa si raddoppia, come in SQL: «l''albero». Senza,
# `SIMILE('sfere per l'albero')` faceva finire la regex a meta' frase e il
# resto («albero')») restava nella query come SQL rotto — e in italiano
# l'apostrofo c'e' in una domanda su due.
_SIMILE = re.compile(r"\bSIMILE\s*\(\s*'((?:[^']|'')*)'\s*\)", re.I)
_TERMINI = re.compile(r"\bTERMINI\s*\(\s*'((?:[^']|'')*)'\s*\)", re.I)

# La colonna vettoriale di ogni tabella. `indice` ce l'ha ma e' vuota (0 righe
# il 30/09/2026): resta qui perche' quando si riempira' funzionera' senza
# toccare niente.
VETTORE = {"immagini": "embedding", "chunks": "embedding",
           "indice": "descrizione_vec"}


def _letterale(vettore) -> str:
    """Il vettore come letterale pgvector. Sono numeri che formattiamo noi,
    quindi non c'e' niente da sfuggire: nessun testo del modello entra qui."""
    return "'[" + ",".join(f"{x:.6f}" for x in vettore) + "]'::vector"


def _tabella(sql: str) -> str:
    m = re.search(r"\bfrom\s+([a-z_]+)", sql, re.I)
    return (m.group(1).lower() if m else "")


def _ripara_simile(sql: str) -> str:
    """Raddoppia gli apostrofi dentro SIMILE(), se il modello non l'ha fatto.

    `_SIMILE` pretende `''`, com'e' giusto in SQL. Ma la domanda e' in
    italiano e l'apostrofo ci sta una volta su due: il 4/10/2026, su «che
    modelli usiamo e perche'?», il coordinatore ha scritto
    `SIMILE('che modelli usiamo e perche'?')` — la regex non ha combaciato,
    il SIMILE non e' stato sostituito, e Postgres ha risposto «unterminated
    quoted string». Quel turno non ha cercato niente.

    Ricordare l'escaping SQL non e' un giudizio: e' la stessa ragione per cui
    i permessi li mette il codice e non il modello. Si cerca la parentesi che
    chiude la chiamata, si prendono i due apostrofi estremi, e si raddoppia
    quello che sta in mezzo.
    """
    fuori, i = [], 0
    for m in re.finditer(r"\bSIMILE\s*\(", sql or "", re.I):
        if m.start() < i:
            continue
        apri = sql.find("'", m.end())
        if apri < 0:
            continue
        chiudi = sql.find(")", m.end())
        while chiudi > 0 and sql.rfind("'", apri + 1, chiudi) < 0:
            chiudi = sql.find(")", chiudi + 1)
        ultimo = sql.rfind("'", apri + 1, chiudi) if chiudi > 0 else -1
        if ultimo <= apri:
            continue
        dentro = sql[apri + 1:ultimo].replace("''", "'").replace("'", "''")
        fuori.append(sql[i:apri + 1] + dentro)
        i = ultimo
    fuori.append(sql[i:])
    return "".join(fuori)


def compila(conn, sql: str) -> tuple[str, list[str]]:
    """(sql compilato, problemi). Sostituisce SIMILE() e TERMINI().

    TERMINI prima di SIMILE: e' solo testo, e chi legge la query compilata la
    vede come l'avrebbe scritta a mano.
    """
    problemi = []
    sql = _ripara_simile(sql)
    colonna = VETTORE.get(_tabella(sql), "embedding")

    def _termini(m):
        parola = (m.group(1) or "").replace("''", "'").strip()
        if not parola:
            problemi.append("TERMINI() vuoto: scrivi la parola, es. TERMINI('nastri')")
            return "''"
        # Il glossario qui espandeva la parola coi termini imparati dai
        # dati. Non si usa piu': `TERMINI` e' uscito dalla mappa degli
        # operatori, e i sinonimi li scrive il modello dentro il `|`.
        voci = [parola]
        # Un termine con lo spazio dentro un ~* non trova niente (le didascalie
        # sono etichette, le parole non sono attaccate): stessa ragione per cui
        # `sql_agente` respinge le frasi. Qui si scartano invece di respingere
        # la query, perche' il modello non li ha scritti lui.
        voci = [v for v in dict.fromkeys(voci) if v and not re.search(r"\s", v)]
        return "'" + "|".join(v.replace("'", "") for v in voci) + "'"

    def _simile(m):
        testo = (m.group(1) or "").replace("''", "'").strip()
        if not testo:
            problemi.append("SIMILE() vuoto: scrivici cosa cerchi, a parole tue")
            return "0"
        vettore = recupero.embedding(testo, query=True)
        if vettore is None:
            problemi.append("il modello di embedding non risponde: usa ~* per ora")
            return "0"
        return f"({colonna} <=> {_letterale(vettore)})"

    sql = _TERMINI.sub(_termini, sql)
    sql = _SIMILE.sub(_simile, sql)
    return sql, problemi


def usa_operatori(sql: str) -> bool:
    return bool(_SIMILE.search(sql or "") or _TERMINI.search(sql or ""))


def esegui(conn, sql: str, gruppi: list, aziende: list,
           t0: float | None = None) -> tuple[list, list[str]]:
    """(righe, problemi). Come `sql_agente.esegui`, ma con SIMILE e TERMINI.

    L'ordine conta: `controlla` gira sulla query ORIGINALE, quella che il
    modello ha scritto, cosi' gli errori parlano delle sue parole e non del
    vettore da dodicimila caratteri che nasce dopo.
    """
    problemi = sql_agente.controlla(sql)
    if problemi:
        return [], problemi
    sql, problemi = compila(conn, sql)
    if problemi:
        return [], problemi
    sql = sql_agente._applica_acl(sql, gruppi, aziende)
    if t0 is not None and time.monotonic() - t0 > sql_agente.SECONDI:
        return [], [f"hai impiegato troppo tempo a pensare la ricerca, semplifica"]
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET LOCAL statement_timeout = {int(sql_agente.SECONDI * 1000)}")
            cur.execute(sql, (aziende, gruppi))
            righe = cur.fetchmany(sql_agente.LIMITE_RIGHE + 1)
    except Exception as e:
        # Senza rollback la transazione resta abortita e TUTTE le query dopo
        # falliscono con lo stesso errore (la lezione e' di `sql_agente`).
        try:
            conn.rollback()
        except Exception:
            pass
        return [], [_perche(e)]
    return righe[:sql_agente.LIMITE_RIGHE], []


# Il testo di ogni tabella: serve a far vedere COME l'archivio scrive le cose.
TESTO = {"immagini": "descrizione", "chunks": "content"}


def colonne_chieste(sql: str) -> str:
    """Le colonne che il modello ha messo nella SELECT, o '*'."""
    m = re.search(r"\bselect\s+(.+?)\s+from\b", sql or "", re.I | re.S)
    scelte = " ".join((m.group(1) if m else "*").split())
    return "*" if not scelte or "(" in scelte else scelte


def vicinato(conn, tabella: str, domanda: str, gruppi: list, aziende: list,
             quanti: int = 8, colonne: str = "") -> list:
    """Le righe piu' vicine alla domanda PER SIGNIFICATO, senza nessun filtro.

    Serve a rispondere alla domanda che un «0 righe» lascia in sospeso: non
    c'e' niente, o ho usato parole che qui non si usano? Il 30/09/2026 il
    sistema ha detto «non ho trovato nulla» su «palline rosse o gialle»
    cercando `~* 'ball'`, mentre l'archivio scrive `Kugel` (84 righe) e
    `bauble` (40). Il vocabolario giusto era a una query di distanza, e
    nessuno la faceva.

    NON e' un risultato e non va trattato come tale: e' il modo in cui questo
    archivio parla della cosa piu' vicina. Chi chiama lo mostra come
    vocabolario, non lo mette fra le righe trovate.
    """
    colonna = TESTO.get(tabella)
    if not colonna or tabella not in VETTORE:
        # Tabella senza vettore: non si puo' ordinare per significato, ma la
        # domanda «non c'e' niente, o ho sbagliato le parole?» resta la
        # stessa. Queste tabelle sono piccole per natura (`documenti` ha 14
        # righe), quindi la risposta e' mostrare cosa contengono davvero.
        # Senza, su «quante foto ha il catalogo Gasper» il modello ha scritto
        # quattro volte `WHERE documento = 'Catalogo Gasper Primavera Estate
        # 2026'` — senza «.pdf» — e non ha mai saputo com'erano scritti i nomi
        # veri (1/10/2026).
        if tabella not in sql_agente.TABELLE:
            return []
        # Le colonne che aveva chiesto LUI, non tutte: con `SELECT *` la
        # tabella `documenti` riversa impronte da 64 caratteri e timestamp, e
        # l'unico dato utile finiva troncato a meta' numero dal limite di
        # lunghezza. L'aiuto c'era e non si poteva leggere (1/10/2026).
        righe, _ = esegui(conn, "SELECT %s FROM %s LIMIT %d"
                          % (colonne or "*", tabella, int(quanti)),
                          gruppi, aziende)
        return righe
    sql = (f"SELECT id, documento, page, {colonna} FROM {tabella} "
           f"ORDER BY SIMILE('{domanda.replace(chr(39), chr(39) * 2)}') "
           f"LIMIT {int(quanti)}")
    righe, _ = esegui(conn, sql, gruppi, aziende)
    return righe


def _perche(e: Exception) -> str:
    """L'errore in parole che il modello puo' usare per correggersi. Le stesse
    famiglie di `sql_agente.esegui`: se un giorno i due esecutori diventano uno,
    questa funzione e' il pezzo da tenere."""
    testo = str(e).strip()
    tipo = type(e).__name__
    if "only '%s', '%b', '%t' are allow" in testo:
        return ("LIKE con i % non si puo' usare: al posto di "
                "descrizione ILIKE '%white%' scrivi descrizione ~* 'white'")
    if "does not exist" in testo or "Undefined" in tipo:
        return f"colonna o tabella inesistente: {testo[:200]}"
    if "syntax error" in testo:
        return f"errore di sintassi: {testo[:200]}"
    if "must appear" in testo or "GROUP BY" in testo:
        return f"GROUP BY incompleto: {testo[:200]}"
    if "statement timeout" in testo or "canceling" in testo:
        return "query troppo lenta: semplifica"
    return f"query non eseguita ({tipo}): {testo[:200]}"


MAPPA_OPERATORI = (
    "PRIMA DI SCRIVERE LA QUERY, GUARDA DOVE STAI CERCANDO: in questo archivio ci sono due cose diverse, e si cercano in due modi diversi. Non e' una preferenza, e' misurato.\n"
    "- le FOTO dei cataloghi (`immagini`): didascalie in INGLESE, scritte da chi guardava la foto, piene di parole precise — object, material, colours. Li' un `~*` sulla parola giusta taglia via il grosso e rende. Capitolo 1.\n"
    "- il TESTO dei documenti (`chunks`): prosa in ITALIANO — progetti, procedure, manuali, relazioni. Li' il `~*` su un argomento fa danno, e la ricerca che porta le righe giuste e' quella semantica. Capitolo 2.\n"
    "La stessa cartella puo' contenere tutti e due. Guarda la domanda: se chiede un ARTICOLO sei nel capitolo 1, se chiede cosa DICE un testo sei nel capitolo 2. Se la prima strada torna a vuoto, l'altra e' li'.\n"
    "\n"
    "=== CAPITOLO 1 — LE FOTO DEI CATALOGHI ===\n"
    "COME SI CERCA: hai DUE operatori, e la scelta e' tua.\n"
    "1. `~*` cerca le LETTERE, e le cerca anche DENTRO le altre parole: "
    "`~* 'red'` trova «textured» e «covered», `~* 'stone'` trova "
    "«polystone». Per questo ogni parola si scrive col confine davanti: "
    "`\\m` vuol dire «qui comincia una parola», e `~* '\\mred'` prende «red», "
    "«reds» e «reddish» ma non «textured». La coda resta libera di proposito: "
    "cosi' `'\\mribbon'` trova anche «ribbons», mentre `'ribbons'` non "
    "troverebbe «ribbon». Scrivi sempre la forma corta col confine davanti: "
    "`'\\mribbon'`, non `'ribbon'` e non `'ribbons'`. Misurato: `'red'` prende "
    "5627 righe, `'\\mred'` ne prende 1846, e le 3781 che cadono sono parole "
    "come «textured». Una `\\m` per parola, attaccata alla sua prima "
    "lettera. "
    "Il `|` dentro lo stesso ~* e' una O (`'\\msoccer|\\mfootball'`); per la E metti "
    "due condizioni con AND. MAI piu' parole dentro un solo ~*, in NESSUNA "
    "tabella: `~*` cerca la sequenza esatta di lettere, spazi compresi. Nelle "
    "didascalie delle foto le parole non stanno nemmeno vicine (`~* '\\mwhite "
    "\\mribbon'` trova zero righe, dove '\\mwhite' AND '\\mribbon' ne "
    "trova ventuno); "
    "nel testo dei documenti stanno vicine ma non in quell'ordine e non senza "
    "niente in mezzo — '\\mred christmas balls' non trova «Christmas balls, red» "
    "ne' «red glitter Christmas balls». Una parola per condizione, unite con "
    "AND: cosi' vale in tutte e due. Non usare LIKE con '%'.\n"
    "   Per il NON, l'operatore e' `!~*`: `descrizione !~* '\\myellow'`. "
    "`NOT ~*` NON ESISTE e da' errore di sintassi. Ma pensaci due volte prima "
    "di escludere qualcosa: un `!~*` butta via una riga per una parola che "
    "magari descriveva un altro articolo nella stessa foto.\n"
    "2. `SIMILE('...')` cerca il SIGNIFICATO, e ci scrivi la domanda "
    "in ITALIANO, com'e': non serve tradurla. Restituisce una DISTANZA, e "
    "piu' e' piccola piu' la riga e' pertinente. Quindi va in ORDER BY, "
    "crescente, che e' il modo normale:\n"
    "     ORDER BY SIMILE('nastri bianchi con cuori rossi') LIMIT 20\n"
    "   Attenzione a due errori che sembrano innocui e rovesciano tutto: "
    "`ORDER BY SIMILE(...) DESC` ti da' le righe PIU' LONTANE, cioe' le meno "
    "pertinenti; e `WHERE SIMILE(...) > 0` non filtra niente, perche' una "
    "distanza e' sempre maggiore di zero. SIMILE serve a ORDINARE, non a "
    "filtrare: se vuoi meno righe usa LIMIT.\n"
    "   Usalo quando non sai con che parola il catalogo chiama una cosa — cioe' "
    "quasi sempre.\n"
    "\n"
    "IL MODO MIGLIORE E' METTERLI INSIEME: un ~* coi confini che taglia "
    "via il grosso, e SIMILE che ordina il resto.\n"
    "     SELECT id, documento, page, descrizione FROM immagini\n"
    "     WHERE descrizione ~* '\\mribbon|\\mtape'\n"
    "     ORDER BY SIMILE('nastri bianchi con cuori rossi') LIMIT 20\n"
    "Misurato su questa domanda: il solo ~* trova 5 pagine giuste su 7, il solo "
    "SIMILE 6 su 7, i due insieme 7 su 7.\n"
    "Il LIMIT taglia DOPO il WHERE, e li' il confine di parola decide tutto: "
    "se il ~* prende la parola dentro le altre parole, le venti righe che "
    "restano sono le sue, e quelle giuste non arrivano. Misurato su «sassi "
    "rossi»: con `'red'` e `'stone'` le venti righe erano vasi in polystone e "
    "superfici textured, e di sassi rossi ne arrivavano DUE; con `'\\mred'` e "
    "`'\\mstone|\\mrock|\\mpebble|\\mgravel'`, otto.\n"
    "Nel WHERE va sempre l'OGGETTO. Ogni attributo che aggiungi e' un "
    "filtro DURO: se la didascalia non usa quella parola, la riga sparisce "
    "— e la didascalia e' scritta da chi guardava la foto, non da chi fa la "
    "domanda. I COLORI nelle didascalie ci sono quasi sempre, quindi un "
    "colore con un AND rende: su «nastri blu», `'\\mribbon'` da solo porta "
    "4 nastri blu, `'\\mblue'` AND `'\\mribbon'` ne porta 7. Le "
    "finiture, i motivi e gli stili — lucido, a cuori, elegante — spesso non "
    "sono scritti: quelli NON si mettono nel WHERE, si lasciano a SIMILE, "
    "che li usa per ordinare senza buttare via niente. Il solo colore senza "
    "l'oggetto e' l'errore opposto: i candidati diventano tutto quello che "
    "e' blu, fiori e lanterne comprese, e due righe su venti erano nastri.\n"
    "I SINONIMI li scrivi tu, dentro il `|`: le didascalie sono in inglese e "
    "ogni catalogo ha il suo vocabolario, quindi di una cosa metti tutti i "
    "nomi che le daresti — `'\\mstone|\\mrock|\\mpebble|\\mgravel'` per i "
    "sassi. Il `|` non costa niente: una parola in piu' nell'elenco e' una "
    "pagina che altrimenti non vedevi.\n"
    "Se non sai nemmeno da che parte cominciare, usa SIMILE da solo, senza "
    "WHERE: e' sempre meglio di una parola indovinata.\n"
    "\n"
    "=== CAPITOLO 2 — IL TESTO DEI DOCUMENTI ===\n"
    "Qui la PRIMA ricerca e' semantica, e si scrive cosi':\n"
    "     SELECT id, documento, page, content FROM chunks\n"
    "     ORDER BY SIMILE('la domanda in italiano, come te l''ha fatta') LIMIT 20\n"
    "Senza WHERE. `SIMILE` cerca il SIGNIFICATO: porta il pezzo che parla di quella cosa anche quando non contiene nessuna delle parole che avresti scelto tu. Misurato il 4/10/2026 su cinque domande vere di progetto: il solo SIMILE ha portato 30 pezzi giusti su 100, le query scritte con un `~*` su una parola indovinata ne hanno portati 13.\n"
    "Il `~*` qui serve a UNA cosa: un TERMINE ESATTO che deve comparire nel testo — un codice (`SPG3001`), un prezzo, il nome di un file, il nome di una persona o di un'azienda. Quello e' il suo mestiere, e li' e' prezioso.\n"
    "Non e' il mestiere di un ARGOMENTO. «progetto», «fase», «modelli», «problemi», «struttura», «stato» sono argomenti, e cercarli col `~*` fa due danni insieme: butta via i pezzi che dicono la stessa cosa con altre parole, e tiene i pezzi che contengono la parola per caso. Misurato: su «mi parli del progetto RAG aziendale?» `~* 'RAG'` ha portato una guida doganale sull'export di animali vivi; su «che modelli usiamo e perche'?» `~* 'model|modell|models'` ha portato zero pezzi utili su venti.\n"
    "I documenti sono in ITALIANO e la domanda arriva in italiano: qui non si traduce niente, e dentro `SIMILE()` ci va la domanda com'e'.\n"
    "`documenti` NON e' il testo: e' l'ELENCO DEI FILE — nome, stato, quanti pezzi, quante figure. Serve solo per le domande sull'archivio stesso («quanti cataloghi ci sono»). Una domanda sul CONTENUTO si cerca sempre in `chunks`: su «quali problemi sono ancora aperti?» la query era `SELECT * FROM documenti WHERE stato ~* 'aperto'`, cioe' la parola «aperto» cercata nella colonna di stato di un elenco di nomi di file. Zero righe, e nessuna possibilita' di trovarne.\n"
    "Una domanda che chiede un GIUDIZIO — «a che punto siamo», «come e' strutturato», «cosa manca» — non ha la risposta in una riga: si forma leggendo PIU' pezzi insieme. Non cercare la riga giusta: prendine venti col SIMILE, e passa a verificarle. Una ricerca sola, poi si risponde.\n"
)


def _prova():
    """Le regole che non chiamano ne' il database ne' i modelli."""
    assert usa_operatori("SELECT 1 FROM immagini ORDER BY SIMILE('x')")
    # L'apostrofo raddoppiato non deve spezzare la lettura a meta' frase.
    assert _SIMILE.search("ORDER BY SIMILE('sfere per l''albero')").group(1)         == "sfere per l''albero"
    assert usa_operatori("SELECT 1 FROM immagini WHERE descrizione ~* TERMINI('x')")
    assert not usa_operatori("SELECT 1 FROM immagini WHERE descrizione ~* 'x'")
    assert _letterale([0.5, -0.25]) == "'[0.500000,-0.250000]'::vector"
    assert _tabella("select a from Chunks where b") == "chunks"
    assert _tabella("select 1") == ""
    # La query che il modello scrive deve passare i controlli di sql_agente
    # PRIMA della compilazione: se SIMILE facesse scattare un controllo, il
    # modello riceverebbe un errore su una cosa che gli abbiamo detto di usare.
    for buona in ("SELECT id, documento, page, descrizione FROM immagini "
                  "WHERE descrizione ~* 'ribbon|tape' "
                  "ORDER BY SIMILE('nastri bianchi con cuori rossi') LIMIT 20",
                  "SELECT id, documento, page, descrizione FROM immagini "
                  "ORDER BY SIMILE('un regalo per una trentenne') LIMIT 20",
                  "SELECT id, documento, page, content FROM chunks "
                  "WHERE content ~* TERMINI('nastri') LIMIT 20"):
        assert not sql_agente.controlla(buona), \
            f"sql_agente respinge una query lecita: {sql_agente.controlla(buona)}"
    # E i permessi devono restare dove sono: l'ACL entra anche quando la query
    # ha solo ORDER BY e nessuna WHERE.
    senza_where = sql_agente._applica_acl(
        "SELECT id FROM immagini ORDER BY (embedding <=> '[0]'::vector) LIMIT 5",
        ["g"], ["a"])
    assert "WHERE immagini.source_id IN" in senza_where, senza_where
    assert senza_where.index("WHERE") < senza_where.index("ORDER BY"), \
        "la WHERE dei permessi deve stare prima dell'ORDER BY"
    # Il vicinato su una tabella SENZA vettore (`documenti`) deve mostrare
    # cosa contiene. Il ramo non lo toccava nessuna prova perche' vuole il
    # database, e ci e' finito dentro un NameError che ha fatto esplodere un
    # turno intero (1/10/2026). Qui si finge l'esecuzione: serve solo a
    # verificare che il ramo stia in piedi e scelga la query giusta.
    global esegui
    vero, visto = esegui, {}
    try:
        esegui = lambda c, sql, g, a: (visto.setdefault("sql", sql) and None) or ([], [])
        vicinato(None, "documenti", "quante foto", [], [])
        assert "FROM documenti" in visto.get("sql", ""), visto
        assert "SIMILE" not in visto["sql"], "documenti non ha un vettore"
        # Le colonne sono quelle che aveva chiesto il modello, non tutte:
        # con `SELECT *` l'unico dato utile usciva troncato fra le impronte.
        visto.clear()
        vicinato(None, "documenti", "x", [], [], colonne="documento, figure_totali")
        assert "SELECT documento, figure_totali FROM" in visto["sql"], visto
        assert colonne_chieste("SELECT a, b FROM immagini WHERE c") == "a, b"
        assert colonne_chieste("select count(*) from documenti") == "*"
        visto.clear()
        assert vicinato(None, "tabella_che_non_esiste", "x", [], []) == []
        assert not visto, "una tabella sconosciuta non si interroga"
    finally:
        esegui = vero
    # L'apostrofo italiano dentro SIMILE(): riparato, e la regex lo trova.
    for sql, dentro in (
            ("ORDER BY SIMILE(Qche modelli usiamo e percheQ?Q) LIMIT 20",
             "percheQQ?"),
            ("ORDER BY SIMILE(Qsfere per lQalberoQ)", "lQQalbero"),
            ("ORDER BY SIMILE(QlQQalberoQ)", "lQQalbero"),
            ("ORDER BY SIMILE(Qnastri bluQ)", "nastri blu")):
        sql, dentro = sql.replace("Q", chr(39)), dentro.replace("Q", chr(39))
        riparato = _ripara_simile(sql)
        assert dentro in riparato, (sql, riparato)
        assert _SIMILE.search(riparato), (sql, riparato)
    print("operatori: ok")


if __name__ == "__main__":
    _prova()
