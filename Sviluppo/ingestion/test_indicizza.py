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


def test_le_immagini_di_un_documento_hanno_una_cartella_sola(tmp_path):
    from indicizza import _butta_immagini, _cartella_immagini
    a = _cartella_immagini("decobrands/acquisti", "catalogo.pdf")
    assert a.startswith("decobrands/acquisti/_immagini/")          # dentro la cartella della fonte
    assert a == _cartella_immagini("decobrands/acquisti", "catalogo.pdf")   # stabile fra un giro e l'altro
    assert a != _cartella_immagini("decobrands/acquisti", "listino.pdf")    # un documento, una cartella
    # '_' iniziale: file_da_leggere non ci rientra, l'indice non rilegge se stesso.
    (tmp_path / "decobrands/acquisti/_immagini").mkdir(parents=True)
    (tmp_path / "decobrands/acquisti/catalogo.pdf").write_bytes(b"x")
    (tmp_path / "decobrands/acquisti/_immagini/1_0.png").write_bytes(b"x")
    assert [r for r, _ in file_da_leggere(tmp_path)] == ["decobrands/acquisti/catalogo.pdf"]
    # E la cartella di un documento si butta via intera, residui compresi.
    indicizza.RADICE = tmp_path
    vecchia = tmp_path / _cartella_immagini("decobrands/acquisti", "catalogo.pdf")
    vecchia.mkdir(parents=True)
    (vecchia / "9_99.png").write_bytes(b"residuo")
    _butta_immagini("decobrands/acquisti", "catalogo.pdf")
    assert not vecchia.exists()


def test_errori_della_gpu_riconosciuti_per_ripiegare_in_cpu():
    assert _gpu_piena(RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB"))
    assert _gpu_piena(RuntimeError("CUDA error: no kernel image is available for execution"))
    assert not _gpu_piena(MemoryError("lettura troppo lenta (pagine 1-6): oltre 600 s"))
    assert not _gpu_piena(ValueError("file non valido"))


def test_percorsi_ammessi_solo_relativi():
    assert PERCORSO_VALIDO.match("luis/sicurezza")
    for no in ("\\\\server\\qualita", "C:\\dati", "/etc", ".."):
        assert not PERCORSO_VALIDO.match(no) or ".." in no


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
