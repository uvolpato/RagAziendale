"""Regole dell'indicizzazione che non richiedono Docling ne' database.

    ./.venv/Scripts/python.exe ingestion/test_indicizza.py      (senza pytest, come gli altri test)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from indicizza import MAX_PEZZO, PERCORSO_VALIDO, file_da_leggere, unisci  # noqa: E402


def test_file_da_leggere_salta_bozze_archivio_fogli_e_temporanei(tmp_path):
    for rel in ("procedure/emergenza.pdf", "schede/SDS-acetone.pdf", "note.txt",
                "_bozze/nuova.pdf", "_archivio/vecchia.pdf", ".nascosta/x.pdf",
                "procedure/~$emergenza.docx", "listino.xlsx", "prezzi.csv", "foto.heic", "_segreto.pdf"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    assert [r for r, _ in file_da_leggere(tmp_path)] == ["note.txt", "procedure/emergenza.pdf", "schede/SDS-acetone.pdf"]


def test_unisci_accorpa_i_frammenti_della_stessa_pagina():
    assert unisci([("Titolo", 1), ("riga uno", 1), ("riga due", 2), ("  ", 2)]) == [("Titolo\nriga uno", 1), ("riga due", 2)]


def test_unisci_taglia_i_pezzi_troppo_lunghi():
    lungo = "\n".join(["capoverso " * 20] * 20)
    pezzi = unisci([(lungo, 3)])
    assert len(pezzi) > 1 and all(len(t) <= MAX_PEZZO and p == 3 for t, p in pezzi)
    assert " ".join(t for t, _ in pezzi).split() == lungo.split()      # nessuna parola persa


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
