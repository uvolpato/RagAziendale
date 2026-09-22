"""Test del prompt: logica pura, senza Docker ne' modelli.

    ./.venv/Scripts/python.exe -m orchestratore.test_prompt   (da Sviluppo/)

Verificano le regole non negoziabili del prompt: la citazione [n], il fatto che
il contesto vuoto sia dichiarato (mai "inventa"), e che il filtro di sicurezza
NON viva nel prompt (non c'e' niente di ACL qui).
"""
from orchestratore import prompt

esiti = []


def prova(nome, descrizione):
    def deco(f):
        try:
            f()
            esiti.append(True)
            print(f"  PASS  {nome}  {descrizione}")
        except AssertionError as e:
            esiti.append(False)
            print(f"  FAIL  {nome}  {descrizione}\n        {e}")
        except Exception as e:
            esiti.append(False)
            print(f"  ERR   {nome}  {descrizione}\n        {type(e).__name__}: {e}")
        return f
    return deco


def main():
    @prova("P1", "il prompt chiede un numero VERO, non il segnaposto [n]")
    def _():
        # Fino al 22/09/2026 diceva «riporta la citazione [n]» e il modello
        # copiava il segnaposto: «il natur [n], il creme [n]» diciannove volte,
        # zero citazioni vere. Il segnaposto non deve stare nelle regole.
        regole = prompt.SYSTEM[prompt.SYSTEM.index("Regole:"):]
        assert "[n]" not in regole
        assert "[1]" in regole
        assert "non inventare" in prompt.SYSTEM.lower()
        assert "CONTESTO" in prompt.SYSTEM.upper()

    @prova("P2", "contesto numerato con documento e pagina")
    def _():
        c = prompt.contesto([
            {"documento": "manuale.pdf", "page": 12, "content": "La garanzia dura 24 mesi."},
            {"documento": "listino.pdf", "page": None, "content": "Sconto 18%."},
        ])
        assert "[1] (manuale.pdf, pagina 12)" in c
        assert "[2] (listino.pdf)" in c
        assert "La garanzia dura 24 mesi." in c

    @prova("P3", "nessun chunk -> contesto dichiarato vuoto, non omesso")
    def _():
        c = prompt.contesto([])
        assert "nessun documento pertinente" in c.lower()

    @prova("P4", "il prompt non e' un controllo di sicurezza: niente ACL ne' gruppi")
    def _():
        assert "acl" not in prompt.SYSTEM.lower()
        assert "gruppo" not in prompt.SYSTEM.lower()

    print("\n" + "=" * 72)
    falliti = [e for e in esiti if not e]
    print(f"{len(esiti) - len(falliti)}/{len(esiti)} passati")
    if falliti:
        raise SystemExit(1)
    print("Prompt: verdi.")


if __name__ == "__main__":
    main()
