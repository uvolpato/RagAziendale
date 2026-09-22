"""Regole dell'indicizzazione che non richiedono Docling ne' database.

    ./.venv/Scripts/python.exe ingestion/test_indicizza.py      (senza pytest, come gli altri test)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import indicizza  # noqa: E402
from indicizza import (MAX_PEZZO, PERCORSO_VALIDO, _gpu_piena, file_da_leggere,  # noqa: E402
                       in_finestra, motivo_prezzi, rimanda, unisci)


def test_file_da_leggere_salta_bozze_archivio_fogli_e_temporanei(tmp_path):
    for rel in ("procedure/emergenza.pdf", "schede/SDS-acetone.pdf", "note.txt",
                "_bozze/nuova.pdf", "_archivio/vecchia.pdf", ".nascosta/x.pdf",
                "procedure/~$emergenza.docx", "registro.xlsx", "vecchio.xls", "foto.heic", "_segreto.pdf"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    # I fogli di calcolo ci sono (decisione 72): i prezzi si controllano dopo;
    # il .xls compare per essere segnato come non leggibile, non per sparire.
    assert [r for r, _ in file_da_leggere(tmp_path)] == ["note.txt", "registro.xlsx", "vecchio.xls",
                                                         "procedure/emergenza.pdf", "schede/SDS-acetone.pdf"]


def test_unisci_accorpa_i_frammenti_della_stessa_pagina():
    assert unisci([("Titolo", 1), ("riga uno", 1), ("riga due", 2), ("  ", 2)]) == [("Titolo\nriga uno", 1), ("riga due", 2)]


def test_le_varianti_di_una_tabella_restano_pezzi_distinti():
    """Nei cataloghi il prodotto sta in cima alla pagina e i colori in tabella:
    unendoli si perde il soggetto, e il vettore di venti colori insieme non
    significa piu' nessun colore. «sassi rossi» non trovava «DST2001 rot red»
    (misurato: era il pezzo MENO pertinente di tutto il catalogo)."""
    titolo = "DEKOSTEINE deco rocks | pietre decorative 9 - 13 mm"
    pagina = [("\u00ae", 7), (titolo, 7), ("A rustic arrangement of rocks in white and grey.", 7),
              ("DST2090 blau blue", 7), ("DST2001 rot red", 7), ("DST2030 gelb yellow", 7)]
    pezzi = [testo for testo, _ in unisci(pagina)]
    varianti = [x for x in pezzi if "DST20" in x]
    assert len(varianti) == 3, f"le righe di colore vanno tenute separate: {varianti}"
    assert all(x.startswith(titolo) for x in varianti), "ogni variante porta con se' il prodotto"
    rosso = [x for x in varianti if "rot red" in x]
    assert len(rosso) == 1 and "pietre decorative" in rosso[0], rosso
    # La prosa continua a unirsi come prima: la regola vale solo per le tabelle.
    assert any("rustic arrangement" in x and "DST20" not in x for x in pezzi)


def test_il_titolo_lungo_resta_il_contesto_delle_varianti():
    """Sulla pagina vera il titolo NON arriva da solo: Docling gli attacca in
    coda la descrizione della figura, e il pezzo supera i 200 caratteri.
    Con il tetto rigido il contesto risultava vuoto e le righe finivano nude:
    in EUROSAND pagina 7 il pezzo era letteralmente «DST2001 rot red», senza
    una parola che dicesse «pietre». Ecco perche' «sassi rossi» non trovava
    nulla (misurato in banca dati il 21/09/2026)."""
    titolo = ("DEKOSTEINE deco rocks | pierres decoratives | pietre decorative 9 - 13 mm "
              "A rustic display of decorative stones arranged in a shallow wooden bowl, "
              "with warm beige and grey tones, photographed on a linen cloth in daylight.")
    assert len(titolo) > 200, "il caso da coprire e' proprio il titolo lungo"
    pezzi = [testo for testo, _ in unisci([(titolo, 7), ("DST2001 rot red", 7)])]
    rosso = [x for x in pezzi if "rot red" in x]
    assert len(rosso) == 1 and "pietre decorative" in rosso[0], rosso


def test_dopo_una_riga_di_tabella_non_ci_si_attacca_nulla():
    """La riga di tabella e' un pezzo CHIUSO. Senza questo, lo scarto dell'OCR
    che segue le finiva dentro: in EUROSAND pagina 7 il pezzo era
    «... | DST2001 rot red ai. p , oa mel i». Misurato il 21/09/2026: con la
    coda la distanza dalla domanda «sassi rossi» era 0,5030, senza 0,4651 —
    cioe' sopra al primo classificato di quel giorno (0,4758)."""
    pagina = [("DEKOSTEINE pietre decorative 9 - 13 mm", 7),
              ("DST2001 rot red", 7),
              ("ai.", 7), ("p", 7), ("oa", 7)]
    pezzi = [testo for testo, _ in unisci(pagina)]
    rosso = [x for x in pezzi if "rot red" in x]
    assert len(rosso) == 1, rosso
    assert rosso[0].endswith("DST2001 rot red"), f"la riga non deve raccogliere la coda: {rosso[0]!r}"


def test_una_riga_qualsiasi_non_e_una_variante():
    """Il riconoscimento deve essere stretto: un codice a inizio riga e riga
    corta. Una frase che cita un codice resta prosa."""
    from indicizza import _e_variante
    assert _e_variante("DST2001 rot red")
    assert _e_variante("GRA1040 weiss white")
    assert not _e_variante("Il prodotto DST2001 va ordinato entro il 30 del mese")
    assert not _e_variante("ISO 27001 richiede un riesame annuale della politica")
    assert not _e_variante("A rustic arrangement of decorative rocks in white and grey")


def test_unisci_taglia_i_pezzi_troppo_lunghi():
    lungo = "\n".join(["capoverso " * 20] * 20)
    pezzi = unisci([(lungo, 3)])
    assert len(pezzi) > 1 and all(len(t) <= MAX_PEZZO and p == 3 for t, p in pezzi)
    assert " ".join(t for t, _ in pezzi).split() == lungo.split()      # nessuna parola persa


def _xlsx(percorso, righe, formato=None):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Dati"
    for r in righe:
        ws.append(r)
    if formato:
        ws["B2"].number_format = formato
    wb.save(percorso)
    return percorso


def test_prezzi_riconosciuti_nei_fogli(tmp_path):
    listino = _xlsx(tmp_path / "listino.xlsx", [["Codice", "Descrizione", "Prezzo netto"], ["ART-1", "Guanti", 12.5],
                                                ["ART-2", "Occhiali", 8]])
    assert "Prezzo netto" in motivo_prezzi(listino)
    valuta = _xlsx(tmp_path / "offerta.xlsx", [["Voce", "Valore"], ["Corso antincendio", 450]], '#,##0.00 "€"')
    assert "valuta" in motivo_prezzi(valuta)
    csv = tmp_path / "dpi.csv"
    csv.write_text("codice;descrizione;prezzo\nD1;casco;35,90\n", encoding="utf-8")
    assert motivo_prezzi(csv)


def test_registro_senza_prezzi_entra(tmp_path):
    # "Costo stimato" c'e', ma senza numeri sotto: e' un registro, non un listino.
    registro = _xlsx(tmp_path / "rischi.xlsx", [["Rischio", "Probabilita'", "Impatto", "Costo stimato"],
                                                ["Phishing", 4, 3, "alto"], ["Furto portatile", 2, 4, "medio"]])
    assert motivo_prezzi(registro) is None
    csv = tmp_path / "trattamenti.csv"
    csv.write_text("trattamento,finalita,base giuridica,conservazione anni\npaghe,stipendi,contratto,10\n", encoding="utf-8")
    assert motivo_prezzi(csv) is None


def _alle(ore, minuti=0):
    from datetime import datetime
    return datetime(2026, 9, 20, ore, minuti)


def test_finestra_a_cavallo_di_mezzanotte():
    notte = "22:00-06:00"
    assert in_finestra(notte, adesso=_alle(23)) and in_finestra(notte, adesso=_alle(2))
    assert in_finestra(notte, adesso=_alle(22)) and not in_finestra(notte, adesso=_alle(6))
    assert not in_finestra(notte, adesso=_alle(15)) and not in_finestra(notte, adesso=_alle(21, 59))


def test_finestra_dentro_la_giornata_e_casi_storti():
    assert in_finestra("13:00-14:00", adesso=_alle(13, 30))
    assert not in_finestra("13:00-14:00", adesso=_alle(14, 1))
    assert not in_finestra("", adesso=_alle(3))                  # nessuna finestra configurata
    assert not in_finestra("dalle 22 alle 6", adesso=_alle(3))   # scritta male: non si indovina


def test_i_file_grossi_aspettano_solo_se_la_gpu_e_occupata():
    indicizza.MB_MAX_DI_GIORNO = 5
    grosso, piccolo = 6 * 1024 * 1024, 4 * 1024 * 1024
    # Di giorno, GPU occupata: il file grosso aspetta, il piccolo passa.
    assert rimanda(grosso, finestra=False, gpu=False)
    assert not rimanda(piccolo, finestra=False, gpu=False)
    # GPU libera: si legge subito, a qualsiasi ora e dimensione. L'indicizzazione
    # e' continua finche' c'e' posto in VRAM, che e' il punto di tutto.
    assert not rimanda(grosso, finestra=False, gpu=True)
    assert not rimanda(grosso, finestra=True, gpu=False)   # di notte (o --forza) passa tutto
    indicizza.MB_MAX_DI_GIORNO = 0                         # default: nessun limite, come prima
    assert not rimanda(grosso, finestra=False, gpu=False)


def test_il_lavorato_di_un_documento_sta_in_una_cartella_sola(tmp_path):
    from indicizza import _butta_sorgenti, _cartella_sorgenti
    a = _cartella_sorgenti("decobrands/acquisti", "catalogo.pdf")
    assert a.startswith("decobrands/acquisti/_sorgenti/")          # dentro la cartella della fonte
    assert a == _cartella_sorgenti("decobrands/acquisti", "catalogo.pdf")   # stabile fra un giro e l'altro
    assert a != _cartella_sorgenti("decobrands/acquisti", "listino.pdf")    # un documento, una cartella
    # '_' iniziale: file_da_leggere non ci rientra, l'indice non rilegge se stesso.
    (tmp_path / "decobrands/acquisti/_sorgenti").mkdir(parents=True)
    (tmp_path / "decobrands/acquisti/catalogo.pdf").write_bytes(b"x")
    (tmp_path / "decobrands/acquisti/_sorgenti/1_0.png").write_bytes(b"x")
    assert [r for r, _ in file_da_leggere(tmp_path)] == ["decobrands/acquisti/catalogo.pdf"]

    indicizza.RADICE = tmp_path
    base = tmp_path / a
    (base / "immagini").mkdir(parents=True)
    (base / f"markdown-{indicizza.IMPRONTA_PROMPT}").mkdir(parents=True)
    (base / "immagini/9_99.png").write_bytes(b"residuo")
    (base / f"markdown-{indicizza.IMPRONTA_PROMPT}/0007.md").write_text("# DEKOSTEINE", encoding="utf-8")

    # Rispezzare senza rileggere: le immagini si rifanno, il Markdown resta.
    # Trenta minuti di VLM per catalogo contro pochi secondi.
    _butta_sorgenti("decobrands/acquisti", "catalogo.pdf", tieni_markdown=True)
    assert not (base / "immagini").exists()
    assert (base / f"markdown-{indicizza.IMPRONTA_PROMPT}/0007.md").is_file()

    # Il documento esce dall'indice: via tutto.
    _butta_sorgenti("decobrands/acquisti", "catalogo.pdf")
    assert not base.exists()


def test_errori_della_gpu_riconosciuti_per_ripiegare_in_cpu():
    assert _gpu_piena(RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB"))
    assert _gpu_piena(RuntimeError("CUDA error: no kernel image is available for execution"))
    assert not _gpu_piena(MemoryError("lettura troppo lenta (pagine 1-6): oltre 600 s"))
    assert not _gpu_piena(ValueError("file non valido"))


def test_percorsi_ammessi_solo_relativi():
    assert PERCORSO_VALIDO.match("luis/sicurezza")
    for no in ("\\\\server\\qualita", "C:\\dati", "/etc", ".."):
        assert not PERCORSO_VALIDO.match(no) or ".." in no


# ---- lettura della PAGINA col VLM (percorso alternativo a Docling) ----------

PAGINA_VERA = """```markdown
# DEKOSTEINE
deco rocks | pierres decoratives | pierre decorative
9 - 13 mm

| E2500 | E5000 |
| :--- | :--- |
| 2,5 l<br>6<br>EUR 8,00 | 5 l<br>6<br>EUR 12,20 |
| E3500 | E5500 |
| 3,5 l<br>6<br>EUR 11,05 | 5,5 l<br>6<br>EUR 13,80 |

---

**Colore:**

- DST2040 weiss
- DST2058 olive
- DST2001 rot
```"""


def test_ogni_articolo_dell_elenco_porta_con_se_il_prodotto():
    """Il difetto di tutta la giornata del 21/09/2026, risolto dalla struttura
    invece che da un'espressione regolare: «DST2001 rot» da solo non dice che
    e' una pietra. Qui il titolo arriva dall'intestazione Markdown che il VLM
    ha messo guardando la pagina."""
    from indicizza import _pezzi_da_markdown
    pezzi = [t for t, _ in _pezzi_da_markdown(PAGINA_VERA, 7)]
    rosso = [x for x in pezzi if "DST2001" in x]
    assert len(rosso) == 1, f"un pezzo per articolo: {rosso}"
    assert "DEKOSTEINE" in rosso[0] and "decorative" in rosso[0].lower(), rosso[0]
    assert "9 - 13 mm" in rosso[0], "le misure fanno parte del prodotto"
    # Il controllo e' su «decorative» e non su «pietre» di proposito. Il
    # catalogo ha le tre lingue — «deco rocks | pierres decoratives | pietre
    # decorative» — ma la TRASCRIZIONE varia fra una lettura e l'altra: il
    # 21/09/2026 la stessa pagina e' uscita una volta «pietre» e una volta
    # «pierre». Una lettera, e la ricerca esatta non combacia piu'.
    # Un test che pretendesse la grafia esatta fallirebbe a giorni alterni per
    # un difetto che sta altrove: questo verifica che il prodotto arrivi
    # insieme all'articolo, che e' quello che deve garantire.
    # Gli altri colori restano pezzi separati: venti colori in un vettore solo
    # non significano nessun colore.
    assert len([x for x in pezzi if "DST20" in x]) == 3


def test_la_tabella_dei_formati_resta_intera_se_ci_sta():
    """«Quali formati offrite?» vuole vedere tutti i formati insieme: una
    tabella corta non si spezza. Le domande 1, 2 e 4 dell'azienda chiedono
    esattamente questo, ed erano 0 su 5."""
    from indicizza import _pezzi_da_markdown
    pezzi = [t for t, _ in _pezzi_da_markdown(PAGINA_VERA, 7)]
    formati = [x for x in pezzi if "E5500" in x]
    assert len(formati) == 1, formati
    assert "E2500" in formati[0] and "5,5 l" in formati[0] and "EUR 13,80" in formati[0]
    assert "DEKOSTEINE" in formati[0], "anche la tabella porta con se' il prodotto"


def test_una_tabella_lunga_si_spezza_per_riga():
    from indicizza import MAX_PEZZO, _pezzi_da_markdown
    righe = "\n".join(f"| ART{i:04d} | descrizione lunga numero {i} " + "x" * 60 + " |"
                      for i in range(60))
    md = f"# VASI\n\n| codice | descrizione |\n| --- | --- |\n{righe}\n"
    pezzi = [t for t, _ in _pezzi_da_markdown(md, 3)]
    assert len(pezzi) > 10, "una tabella lunga non resta un pezzo solo"
    uno = [x for x in pezzi if "ART0042" in x]
    assert len(uno) == 1 and "VASI" in uno[0] and "codice" in uno[0], uno
    assert all(len(t) <= MAX_PEZZO * 2 for t in pezzi)


def test_la_prosa_resta_prosa_e_i_recinti_spariscono():
    from indicizza import _pezzi_da_markdown
    md = "```markdown\n# NOTE\n\nQuesta pagina descrive la lavorazione.\nSecondo capoverso.\n```"
    pezzi = [t for t, _ in _pezzi_da_markdown(md, 1)]
    assert len(pezzi) == 1, pezzi
    assert "```" not in pezzi[0] and "NOTE" in pezzi[0] and "lavorazione" in pezzi[0]


def test_pagina_vuota_non_produce_pezzi():
    from indicizza import _pezzi_da_markdown
    assert _pezzi_da_markdown("", 5) == []
    assert _pezzi_da_markdown("```\n\n---\n\n```", 5) == []



def test_si_riconosce_quando_il_modello_non_rispetta_la_forma():
    """Il VLM non e' coerente: sulla STESSA pagina, a temperatura zero, il
    21/09/2026 ha prodotto una tabella in una chiamata e righe nude con `<br>`
    nella successiva. Righe nude significano ventisei articoli in un pezzo
    solo, cioe' il difetto di partenza.

    Non si indovina cosa intendeva: si controlla il CONTRATTO, che e'
    oggettivo (`<br>` e' vietato dalle istruzioni) e non dipende dal
    contenuto della pagina ne' da soglie."""
    from indicizza import rispetta_la_forma
    buona = "# DEKOSTEINE\n\n| codice | colore |\n| --- | --- |\n| DST2001 | rot / red |"
    assert rispetta_la_forma(buona)
    cattiva = "# DEKOSTEINE\n\nDST2001 rot<br>red\nDST2050 gruen<br>green"
    assert not rispetta_la_forma(cattiva)
    assert rispetta_la_forma("")      # pagina vuota: niente da richiedere


def test_il_markdown_salvato_dipende_dalle_istruzioni():
    """Cambiando il prompt cambia la FORMA del Markdown: rileggere i file
    vecchi darebbe pezzi incoerenti con i nuovi. L'impronta delle istruzioni
    sta nel nome della cartella, cosi' un prompt diverso rilegge da solo."""
    from indicizza import IMPRONTA_PROMPT
    assert len(IMPRONTA_PROMPT) == 8 and IMPRONTA_PROMPT.isalnum()



def test_il_modo_di_leggere_lo_decide_la_fonte():
    """Sui cataloghi il percorso a due sguardi porta il recupero da 10/20 a
    17/20; sui documenti di prosa Docling da solo fa gia' 4/4, e il prompt
    «catalogo» li' imporrebbe una griglia che non c'e'. Quindi non e' una
    scelta globale: la dichiara chi sa cosa contiene la cartella."""
    indicizza.FONTI_A_PAGINA = {"acquisti-decobrands"}
    indicizza.LETTURA = "docling"
    assert indicizza.come_leggere("acquisti-decobrands") == "pagina"
    assert indicizza.come_leggere("sicurezza-luis") == "docling"
    # LETTURA resta la rete di sicurezza per le fonti non dichiarate.
    indicizza.LETTURA = "pagina"
    assert indicizza.come_leggere("sicurezza-luis") == "pagina"
    indicizza.LETTURA = "docling"
    indicizza.FONTI_A_PAGINA = set()



def test_con_scarica_chat_no_il_modello_di_chat_non_si_tocca():
    """Da quando i modelli stanno tutti in VRAM insieme, scaricare quello di
    chat faceva pagare 34 secondi di ricarica a OGNI messaggio, perche'
    l'indicizzazione lo rifaceva a ogni file (misurato il 22/09/2026)."""
    lettore = indicizza.Lettore(conn=None, finestra=True)
    indicizza.SCARICA_CHAT = False
    chiamate = []
    vero = indicizza.scarica_llm
    indicizza.scarica_llm = lambda: chiamate.append(1) or True
    try:
        lettore._fai_spazio()
        assert chiamate == [], "con SCARICA_CHAT=no non si scarica niente"
        # E con l'impostazione attiva si comporta come prima: decide la VRAM.
        indicizza.SCARICA_CHAT = True
        lettore.spazio_fatto = False
        indicizza.vram_libera_mb = lambda: 100          # molto poca
        lettore._fai_spazio()
        assert chiamate == [1], "con SCARICA_CHAT=si e poca VRAM si scarica"
    finally:
        indicizza.scarica_llm = vero
        indicizza.SCARICA_CHAT = True



def test_il_titolo_di_pagina_arriva_a_chi_descrive_le_figure(tmp_path):
    """Un ritaglio di 221x149 px senza contesto inganna: il 22/09/2026 un
    primo piano di sassi rossi e' stato descritto come «possibly dried fruit or
    processed food». Con il titolo della pagina davanti lo stesso ritaglio
    diventa «reddish-brown decorative stones, approximately 9-13 mm».

    Il titolo si prende dal Markdown che il VLM ha gia' scritto: non costa una
    chiamata in piu'."""
    indicizza.RADICE = tmp_path
    dentro = "fonte/_sorgenti/abc123"
    md = tmp_path / dentro / f"markdown-{indicizza.IMPRONTA_PROMPT}"
    md.mkdir(parents=True)
    (md / "0007.md").write_text(
        "```markdown\n# DEKOSTEINE\ndeco rocks | pietre decorative\n9 - 13 mm\n\n| a | b |\n",
        encoding="utf-8")
    (md / "0012.md").write_text("# FARBSAND\nsabbia colorata\n", encoding="utf-8")

    titoli = indicizza._titoli_di_pagina(dentro)
    assert set(titoli) == {7, 12}, titoli
    assert "DEKOSTEINE" in titoli[7] and "9 - 13 mm" in titoli[7], titoli[7]
    assert "```" not in titoli[7], "i recinti del modello non fanno parte del titolo"
    assert "FARBSAND" in titoli[12]

    # Una pagina senza Markdown non ha titolo: la figura si descrive come prima.
    assert 99 not in titoli
    # E una cartella che non esiste non fa saltare niente.
    assert indicizza._titoli_di_pagina("fonte/_sorgenti/mai-vista") == {}


def test_senza_vlm_le_figure_restano_come_sono():
    """Se il VLM non e' configurato non si chiama nessuno e le descrizioni di
    Docling restano quelle: il documento entra lo stesso."""
    vero = indicizza.VLM_MODELLO
    indicizza.VLM_MODELLO = ""
    try:
        immagini = [("finta-immagine", 7, "descrizione di Docling")]
        assert indicizza._descrivi_col_titolo(immagini, {7: "# DEKOSTEINE"}) == immagini
    finally:
        indicizza.VLM_MODELLO = vero



def test_le_descrizioni_delle_figure_entrano_nel_testo_col_prodotto():
    """La prosa che descrive le figure e' quella che fa funzionare le domande
    descrittive — «ciottoli neri con effetto specchio» non combacia con nessun
    codice articolo. Finora ce la metteva il chunker di Docling; da quando le
    descrizioni le scriviamo noi, dobbiamo metterla noi, altrimenti sparisce
    dall'indice del testo.

    Con il titolo davanti: «Immagine: pietre rosse» da sola non dice di che
    prodotto si parla."""
    immagini = [("f/7_67.png", 7, "red decorative stones, 9-13 mm"),
                ("f/7_99.png", 7, ""),                     # senza descrizione: si salta
                ("f/9_10.png", 9, "una scatola di cartone")]
    titoli = {7: "# DEKOSTEINE pietre decorative 9 - 13 mm"}
    pezzi = indicizza._pezzi_dalle_figure(immagini, titoli)
    assert len(pezzi) == 2, pezzi
    testo, pagina = pezzi[0]
    assert pagina == 7
    assert "DEKOSTEINE" in testo and "red decorative stones" in testo, testo
    # Una pagina senza titolo non inventa: la descrizione entra da sola.
    assert pezzi[1][0].startswith("Immagine:") and "cartone" in pezzi[1][0]



def test_un_id_con_la_barra_si_scarica_lo_stesso():
    """L'id del VLM contiene una barra («qwen/qwen3-vl-4b»): senza protezione
    diventa un altro pezzo di percorso e il server risponde 404 — cioe' il
    modello resterebbe caricato e Docling non avrebbe la VRAM, senza che
    niente si lamenti."""
    import urllib.parse
    chiamate = []

    class FintaRisposta:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import urllib.request
    vera = urllib.request.urlopen
    urllib.request.urlopen = lambda req, timeout=0: chiamate.append(req.full_url) or FintaRisposta()
    vecchio = indicizza.MODELLI_HOST
    indicizza.MODELLI_HOST = "host:1235"
    try:
        assert indicizza._scarica_modello("qwen/qwen3-vl-4b")
        assert chiamate and chiamate[0].endswith("/api/models/unload/qwen%2Fqwen3-vl-4b"), chiamate
        # Senza host o senza modello non si chiama nessuno.
        indicizza.MODELLI_HOST = ""
        assert indicizza._scarica_modello("qualsiasi") is False
        indicizza.MODELLI_HOST = "host:1235"
        assert indicizza._scarica_modello("") is False
    finally:
        urllib.request.urlopen = vera
        indicizza.MODELLI_HOST = vecchio


if __name__ == "__main__":
    import tempfile
    esiti = []
    for nome, f in list(globals().items()):
        if nome.startswith("test_"):
            try:
                if f.__code__.co_argcount:
                    with tempfile.TemporaryDirectory() as d:
                        f(pathlib.Path(d))
                else:
                    f()
                esiti.append(True)
                print(f"  PASS  {nome}")
            except AssertionError as e:
                esiti.append(False)
                print(f"  FAIL  {nome}: {e}")
    print(f"{sum(esiti)}/{len(esiti)} passati")
    sys.exit(0 if all(esiti) else 1)
