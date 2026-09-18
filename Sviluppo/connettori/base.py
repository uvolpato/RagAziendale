"""Motore di importazione, uguale per ogni connettore (SPECIFICA-CONNETTORI.md §7).

Il connettore traduce e basta: legge il gestionale e restituisce righe gia'
nelle colonne del modello canonico. Questo modulo fa tutto il resto, e le
garanzie stanno qui, una volta sola:

- storico SCD2 (MODELLO-DATI-GESTIONALE.md §4): una versione nuova nasce solo
  se l'impronta dei campi cambia; una versione modificata chiude la precedente
  e ne apre una nuova; una riga sparita in una lettura completa diventa
  `cancellato`. Il database rifiuta da solo due versioni sovrapposte (vincolo
  di esclusione) e due versioni correnti (indice unico parziale);
- una esecuzione alla volta per azienda x entita (blocco);
- controlli di qualita' PRIMA di toccare lo storico: un calo sospetto delle
  righe ferma l'importazione con un'anomalia critica, lasciando i dati com'erano;
- anomalie deduplicate via `segnala_anomalia()` / `chiudi_anomalia()` (migrazione
  003), e stato in `erp.sincronizzazioni` per l'assistente ("dati aggiornati al ...").

ponytail: l'estrazione avviene in memoria (una lista di dict per tabella). Va
bene per anagrafiche, articoli, listini, codici e scadenze; quando i documenti
(che possono essere milioni di righe) entreranno, si passera' all'area di sosta
`erp_sosta.*` descritta nella specifica, con la fusione in un solo passaggio SQL.
"""
import hashlib
import importlib
import json
import os
import pathlib
from datetime import date, datetime, timezone

from psycopg import sql

# Colonne gestite dallo storicizzatore (_versione + SCD2): NON fanno parte dei
# "campi significativi" di una riga e non vanno nel calcolo dell'impronta.
VERSIONAMENTO = {
    "versione", "id", "azienda", "hash_riga", "data_modifica_origine",
    "registrato_dal", "registrato_al", "cancellato",
}

# Sotto questa quota di righe rispetto all'ultima lettura completa si ferma
# tutto (SPECIFICA-CONNETTORI.md §7.4): quasi sempre e' un collegamento rotto,
# non dati davvero spariti.
SOGLIA_CROLLO = 0.5


class ImportazioneBloccata(RuntimeError):
    """Un controllo bloccante ha fermato l'importazione: lo storico resta com'era."""


class InCorso(RuntimeError):
    """C'e' gia' un'esecuzione per la stessa azienda x entita."""


class EntitaNonDisponibile(ValueError):
    """Il connettore non fornisce questa entita (stato normale, non un errore)."""


# --------------------------------------------------------------------- hash
def canonico(v):
    """Una rappresentazione stabile di un valore, per costruire l'impronta.

    Il connettore deve restituire tipi semplici (str, numeri, bool, list, dict,
    date/datetime). Questa funzione li normalizza: l'impronta e' calcolata dallo
    stesso motore a ogni esecuzione, quindi deve solo essere identica a ogni giro
    dello stesso codice, non fra linguaggi diversi.
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        return v
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [canonico(x) for x in v]
    if isinstance(v, dict):
        return {str(k): canonico(x) for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))}
    return str(v)


def hash_riga(riga, colonne):
    """Impronta dei campi significativi: se non cambia, non nasce una versione.

    Si considerano le colonne di business (non quelle di storicizzazione),
    compreso `extra`: un campo che il gestionale ha e il modello non prevede e'
    comunque contenuto, e una sua modifica e' una modifica.
    """
    d = {k: canonico(riga.get(k)) for k in colonne}
    testo = json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(testo.encode("utf-8")).hexdigest()


# -------------------------------------------------------- schema (introspezione)
_colonne_cache: dict[str, list[str]] = {}


def colonne_business(conn, tabella):
    """Le colonne di business di una tabella erp_storico (escluso il versionamento).

    Si leggono dal database, non da un elenco scritto a mano: cosi' una colonna
    nuova nel modello e' subito parte dell'impronta e dell'inserimento.
    """
    if tabella not in _colonne_cache:
        righe = conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = 'erp_storico' AND table_name = %s", (tabella,))
        _colonne_cache[tabella] = sorted({r["column_name"] for r in righe} - VERSIONAMENTO)
    return _colonne_cache[tabella]


def _ident(tabella):
    return sql.Identifier(tabella)


# ------------------------------------------------------------------- fusione
def _adesso():
    # L'ora della sincronizzazione, non la data di modifica del gestionale:
    # quella non sempre esiste ed e' a volte inaffidabile (modello dati §4.4).
    return datetime.now(timezone.utc)


def _chiudi(conn, tabella, azienda, id_, adesso):
    conn.execute(
        sql.SQL("UPDATE erp_storico.{} SET registrato_al = %s"
                " WHERE azienda = %s AND id = %s AND registrato_al IS NULL")
        .format(_ident(tabella)),
        (adesso, azienda, id_))


def _inserisci(conn, tabella, riga, h, azienda, adesso, cancellato=False):
    colonne = colonne_business(conn, tabella)
    campi = {k: riga[k] for k in colonne if k in riga}
    nomi = ["id", "azienda", "hash_riga", "registrato_dal"]
    valori = [riga["id"], azienda, h, adesso]
    if riga.get("data_modifica_origine") is not None:
        nomi.append("data_modifica_origine")
        valori.append(riga["data_modifica_origine"])
    if cancellato:
        nomi.append("cancellato")
        valori.append(True)
    nomi += list(campi)
    valori += [campi[k] for k in campi]
    conn.execute(
        sql.SQL("INSERT INTO erp_storico.{} ({}) VALUES ({})")
        .format(_ident(tabella),
                sql.SQL(", ").join(map(sql.Identifier, nomi)),
                sql.SQL(", ").join([sql.Placeholder()] * len(valori))),
        valori)


def fusione(conn, tabella, azienda, righe, rileva_cancellazioni):
    """SCD2 su una tabella, dentro la transazione del chiamante.

    `righe` sono dict con almeno `id` e le colonne di business. Piu' righe con
    lo stesso id: vale l'ultima. Restituisce il numero di versioni nuove.
    """
    per_id = {}
    for r in righe:
        per_id[r["id"]] = r

    colonne = colonne_business(conn, tabella)
    correnti = {
        r["id"]: r["hash_riga"]
        for r in conn.execute(
            sql.SQL("SELECT id, hash_riga FROM erp_storico.{}"
                    " WHERE azienda = %s AND registrato_al IS NULL AND NOT cancellato")
            .format(_ident(tabella)), (azienda,))
    }

    adesso = _adesso()
    versioni_nuove = 0

    # 1. Le versioni correnti: chiuse se cambiate, chiuse+segnate se sparite.
    for id_, vecchia in list(correnti.items()):
        nuova = per_id.get(id_)
        if nuova is None:
            if rileva_cancellazioni:
                _chiudi(conn, tabella, azienda, id_, adesso)
                # La versione "cancellato = true" resta quella corrente: sparisce
                # dalle viste erp.* ma la cronologia e' completa.
                _inserisci(conn, tabella, {"id": id_}, hash_riga({"id": id_}, colonne),
                           azienda, adesso, cancellato=True)
                versioni_nuove += 1
            continue
        if hash_riga(nuova, colonne) == vecchia:
            continue                       # il 95% dei casi: niente da fare
        _chiudi(conn, tabella, azienda, id_, adesso)
        _inserisci(conn, tabella, nuova, hash_riga(nuova, colonne), azienda, adesso)
        versioni_nuove += 1

    # 2. Le righe nuove, mai viste.
    for id_, r in per_id.items():
        if id_ not in correnti:
            _inserisci(conn, tabella, r, hash_riga(r, colonne), azienda, adesso)
            versioni_nuove += 1

    return versioni_nuove


# ------------------------------------------------------------------- blocco
def _chiave_blocco(azienda, entita):
    return "connettore:" + azienda + ":" + entita


def blocco(conn, azienda, entita):
    """Un'esecuzione alla volta per azienda x entita (advisory lock).

    ponytail: chiave da hashtextextended, due stringhe diverse potrebbero in
    teoria collidere. Ininfluente a questa scala; con piu' esecuzioni si passa
    alla coppia di interi da una chiave intera nel database.
    """
    r = conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS preso",
                     (_chiave_blocco(azienda, entita),)).fetchone()
    return bool(r["preso"])


def sblocco(conn, azienda, entita):
    conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                 (_chiave_blocco(azienda, entita),))


# ---------------------------------------------------------------- stato (sync)
def _segna(conn, azienda, entita, esito, righe=None, versioni=None, errore=None):
    conn.execute(
        """INSERT INTO erp.sincronizzazioni (azienda, entita, iniziata_il, completata_il,
                                              esito, righe_lette, versioni_nuove, errore)
           VALUES (%s, %s, now(), now(), %s, %s, %s, %s)
           ON CONFLICT (azienda, entita) DO UPDATE
              SET iniziata_il = now(), completata_il = now(), esito = EXCLUDED.esito,
                  righe_lette = COALESCE(EXCLUDED.righe_lette, erp.sincronizzazioni.righe_lette),
                  versioni_nuove = COALESCE(EXCLUDED.versioni_nuove, erp.sincronizzazioni.versioni_nuove),
                  errore = EXCLUDED.errore""",
        (azienda, entita, esito, righe, versioni, errore))


def cursore_di(conn, azienda, entita):
    r = conn.execute("SELECT cursore FROM erp.sincronizzazioni WHERE azienda = %s AND entita = %s",
                     (azienda, entita)).fetchone()
    return r["cursore"] if r else None


def ultima_lettura(conn, azienda, entita):
    r = conn.execute("SELECT righe_lette FROM erp.sincronizzazioni WHERE azienda = %s AND entita = %s",
                     (azienda, entita)).fetchone()
    return r["righe_lette"] if r else None


# ------------------------------------------------------------------ anomalie
def _impronta(azienda, entita, controllo):
    return f"importazione:{azienda}/{entita}:{controllo}"


def _anomalia(conn, impronta, gravita, sistema, titolo, cosa, azienda, oggetto, dettaglio=None):
    conn.execute("SELECT segnala_anomalia(%s,%s,%s,%s,%s,%s,%s,%s)",
                 (impronta, gravita, sistema, titolo, cosa, azienda, oggetto,
                  json.dumps(dettaglio or {}, ensure_ascii=False)))


def chiudi_anomalie(conn, azienda, entita):
    """A esecuzione riuscita: i controlli sono tornati a passare, si chiudono
    le anomalie 'errore' e 'crollo' precedenti (SPECIFICA-CONNETTORI.md §7.2)."""
    for controllo in ("errore", "crollo"):
        conn.execute("SELECT chiudi_anomalia(%s)", (_impronta(azienda, entita, controllo),))


# --------------------------------------------------------------- controlli §7.4
def controlli(conn, azienda, entita, righe_lette, strategia):
    """Controlli PRIMA della fusione. Solleva ImportazioneBloccata se uno e'
    bloccante: l'anomalia resta (committata a parte), lo storico no."""
    precedente = ultima_lettura(conn, azienda, entita)
    # Calo sospetto: solo su lettura completa, e solo con un termine di paragone
    # non banale. Su un'importazione incrementale il numero e' quello delle righe
    # CAMBIATE, non del totale: il confronto non avrebbe senso.
    if (strategia == "completa" and precedente and precedente >= 2
            and righe_lette < SOGLIA_CROLLO * precedente):
        conn.rollback()          # via la transazione di sola lettura
        _anomalia(conn, _impronta(azienda, entita, "crollo"), "critico", "qualita",
                  f"Righe calate del {int((1 - righe_lette / precedente) * 100)}% nell'importazione di {entita}",
                  "Di solito indica un problema del collegamento, non righe davvero sparite. "
                  "Verifica il connettore prima di rileggere tutto.",
                  azienda, f"importazione:{azienda}/{entita}",
                  {"prima": precedente, "ora": righe_lette})
        conn.commit()
        raise ImportazioneBloccata(
            f"{azienda}/{entita}: {righe_lette} righe contro {precedente} (calo sospetto)")


# ----------------------------------------------------------- caricamento tipo
def connettore_di(tipo, parametri=None):
    """Istanzia il connettore dal catalogo (Sviluppo/connettori/<tipo>/).

    `parametri` espliciti (es. quelli decifrati di un collegamento) vincono
    sulle credenziali in .env; se None, si leggono da .env.
    """
    modulo = importlib.import_module(f"connettori.{tipo}.connettore")
    return modulo.Connettore(parametri if parametri is not None else parametri_da_env(tipo))


def catalogo():
    """I connettori installati (tipo -> manifesto), letti dal codice.

    E' il catalogo della specifica §2.2: il pannello non lo configura, lo
    legge. Aggiungere un gestionale = aggiungere una cartella con manifesto.json.
    """
    radice = pathlib.Path(__file__).parent
    out = {}
    for cartella in radice.iterdir():
        m = cartella / "manifesto.json"
        if cartella.is_dir() and m.exists() and not cartella.name.startswith("_"):
            try:
                man = json.loads(m.read_text(encoding="utf-8"))
                out[man["tipo"]] = man
            except (ValueError, KeyError):
                continue
    return out


def parametri_da_env(tipo):
    """Credenziali del collegamento da .env: CONNETTORE_<TIPO>_<CHIAVE>.

    SPECIFICA-CONNETTORI.md §4.1: oggi le credenziali stanno in .env (l'opzione
    "solo .env" della proposta 70). Il passaggio alla cifratura nel database
    avverra' con i collegamenti configurati dal pannello (fase 3 della specifica):
    a quel punto questo metodo sparira' dal motore e la provenienza sara' il
    servizio `connettori`, non l'ambiente.
    """
    prefisso = f"CONNETTORE_{tipo.upper()}_"
    return {k[len(prefisso):].lower(): v for k, v in os.environ.items()
            if k.startswith(prefisso) and v}


# ------------------------------------------------------------------ importa
def importa_fotografia(conn, connettore, azienda, entita, codice_origine):
    """Fotografia (giacenze): upsert nella tabella snapshot, non SCD2.

    Una riga al giorno per (data, azienda, articolo, magazzino). La giacenza
    "di adesso" si legge in diretta dal gestionale (decisione 47); qui si tiene
    la serie storica per l'analisi. Il connettore emette `articolo_id_origine`.
    """
    if not blocco(conn, azienda, entita):
        raise InCorso(f"{azienda}/{entita} e' gia' in corso")
    _segna(conn, azienda, entita, "in_corso")
    conn.commit()
    try:
        oggi = date.today()
        totale = 0
        for tabella in connettore.entita()[entita]["tabelle"]:
            for r in connettore.estrai(tabella, codice_origine):
                if "articolo_id_origine" not in r:
                    raise ValueError(f"il connettore non ha fornito articolo_id_origine per {tabella}")
                conn.execute(
                    """INSERT INTO erp.giacenze_istantanee
                       (data, azienda, articolo_id, magazzino, esistenza, impegnato, ordinato, disponibile, valore)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (data, azienda, articolo_id, magazzino) DO UPDATE SET
                         esistenza=EXCLUDED.esistenza, impegnato=EXCLUDED.impegnato,
                         ordinato=EXCLUDED.ordinato, disponibile=EXCLUDED.disponibile,
                         valore=EXCLUDED.valore""",
                    (oggi, azienda, f"{azienda}:{r['articolo_id_origine']}", r.get("magazzino") or "",
                     r.get("esistenza"), r.get("impegnato"), r.get("ordinato"),
                     r.get("disponibile"), r.get("valore")))
                totale += 1
        _segna(conn, azienda, entita, "ok", righe=totale, versioni=totale)
        chiudi_anomalie(conn, azienda, entita)
        conn.commit()
        return {"azienda": azienda, "entita": entita, "righe": totale, "versioni": totale}
    except Exception as e:
        conn.rollback()
        try:
            _segna(conn, azienda, entita, "errore", errore=str(e)[:2000])
            if not isinstance(e, (EntitaNonDisponibile, InCorso)):
                _anomalia(conn, _impronta(azienda, entita, "errore"), "errore", "importazioni",
                          f"Importazione {entita} fallita: {e}",
                          "Controlla il collegamento e lo stato del gestionale.",
                          azienda, f"importazione:{azienda}/{entita}")
            conn.commit()
        except Exception:
            conn.rollback()
        raise
    finally:
        try:
            sblocco(conn, azienda, entita)
            conn.commit()
        except Exception:
            conn.rollback()


def importa(conn, connettore, azienda, entita, rileva_cancellazioni=None):
    """Un'esecuzione completa di una entita per un'azienda (§7.2).

    `connettore` e' un'istanza gia' configurata (con le sue credenziali, che
    arrivano da .env finche' non esiste il pannello — §4.1). Ordine: blocco ->
    estrazione -> controlli (prima dello storico) -> fusione in una transazione
    -> stato. Se un controllo bloccante fallisce, lo storico resta com'era.
    """
    a = conn.execute("SELECT codice_origine FROM aziende WHERE codice = %s", (azienda,)).fetchone()
    if not a:
        raise ValueError(f"azienda sconosciuta: {azienda}")
    codice_origine = a["codice_origine"]

    entita_info = connettore.entita().get(entita)
    if not entita_info:
        raise EntitaNonDisponibile(f"il connettore non fornisce l'entita {entita}")
    strategia = entita_info["strategia"]

    # La fotografia (giacenze) non e' SCD2: va nella tabella snapshot.
    if strategia == "fotografia":
        return importa_fotografia(conn, connettore, azienda, entita, codice_origine)

    rileva = strategia == "completa" if rileva_cancellazioni is None else rileva_cancellazioni

    if not blocco(conn, azienda, entita):
        raise InCorso(f"{azienda}/{entita} e' gia' in corso")

    _segna(conn, azienda, entita, "in_corso")
    conn.commit()

    try:
        # Estrazione in memoria: i controlli devono poter girare PRIMA della
        # fusione, e una lettura rotta non deve toccare lo storico.
        dal = cursore_di(conn, azienda, entita) if strategia == "incrementale" else None
        per_tabella = {}
        for tabella in entita_info["tabelle"]:
            # Il connettore conosce solo il gestionale, non le nostre aziende:
            # emette `id_origine` (l'id nel gestionale) e qui si compone la chiave
            # canonica `<azienda nostra>:<id_origine>` (decisione 69, MODELLO §3).
            righe = []
            for r in connettore.estrai(tabella, codice_origine, dal=dal):
                if "id_origine" not in r:
                    raise ValueError(f"il connettore non ha fornito id_origine per {tabella}")
                righe.append({**r, "id": f"{azienda}:{r.pop('id_origine')}"})
            per_tabella[tabella] = righe
        righe_lette = sum(len(r) for r in per_tabella.values())

        controlli(conn, azienda, entita, righe_lette, strategia)

        versioni = 0
        for tabella, righe in per_tabella.items():
            versioni += fusione(conn, tabella, azienda, righe, rileva)

        # Cursore = l'ultima data di modifica dichiarata dal gestionale fra le
        # righe lette: la prossima lettura incrementale riparte da li'.
        if strategia == "incrementale":
            date_righe = [r["data_modifica_origine"] for righe in per_tabella.values()
                          for r in righe if r.get("data_modifica_origine")]
            if date_righe:
                conn.execute("UPDATE erp.sincronizzazioni SET cursore = %s WHERE azienda = %s AND entita = %s",
                             (max(date_righe), azienda, entita))

        _segna(conn, azienda, entita, "ok", righe=righe_lette, versioni=versioni)
        chiudi_anomalie(conn, azienda, entita)
        conn.commit()
        return {"azienda": azienda, "entita": entita, "righe": righe_lette, "versioni": versioni}
    except Exception as e:
        conn.rollback()
        try:
            _segna(conn, azienda, entita, "errore", errore=str(e)[:2000])
            if not isinstance(e, (ImportazioneBloccata, EntitaNonDisponibile, InCorso)):
                _anomalia(conn, _impronta(azienda, entita, "errore"), "errore", "importazioni",
                          f"Importazione {entita} fallita: {e}",
                          "Controlla il collegamento e lo stato del gestionale.",
                          azienda, f"importazione:{azienda}/{entita}")
            conn.commit()
        except Exception:
            conn.rollback()
        raise
    finally:
        try:
            sblocco(conn, azienda, entita)
            conn.commit()
        except Exception:
            conn.rollback()
