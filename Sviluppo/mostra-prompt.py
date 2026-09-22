"""Scrive PROMPT.md leggendo i prompt dal CODICE.

    ./.venv/Scripts/python.exe mostra-prompt.py

Ricopiarli a mano voleva dire due versioni che divergono al primo ritocco, e
il documento sbagliato e' peggio di nessun documento: chi lo legge crede di
sapere cosa chiede il sistema. Qui si estraggono dal sorgente con `ast`, senza
importarlo — `ingestion/indicizza.py` tira dentro Docling e non parte fuori
dal suo container.

Da rifare dopo ogni modifica a un prompt. Finche' stanno nel codice e' questo
il prezzo: vedi D18 in DECISIONI-APERTE.md.
"""
import ast
import datetime
import pathlib

QUI = pathlib.Path(__file__).parent

# (file, nome della costante, titolo, a cosa serve)
PROMPT = [
    ("orchestratore/prompt.py", "SYSTEM", "La risposta in chat",
     "Il prompt di sistema di OGNI risposta. Gli si accoda il CONTESTO (i brani "
     "recuperati, numerati) e poi la cronologia della conversazione. E' l'unico "
     "che l'utente sente parlare."),
    ("orchestratore/riformula.py", "ISTRUZIONI", "La riformulazione della domanda di seguito",
     "Serve SOLO quando la domanda non si regge da sola («ne ho bisogno in auto»): "
     "sotto i 200 caratteri e con riferimenti impliciti da sciogliere. Riscrive la "
     "domanda per la RICERCA, non per la risposta. Non e' la «riscrittura in termini "
     "di catalogo», che e' stata misurata quattro volte e bocciata quattro volte."),
    ("ingestion/indicizza.py", "ISTRUZIONI_PAGINA", "La lettura di una pagina (VLM)",
     "Il modello GUARDA l'immagine della pagina e la riscrive in Markdown. Si usa "
     "solo sulle fonti a griglia (LETTURA_PAGINA), dove Docling non vede le tabelle. "
     "ATTENZIONE: l'impronta di questo testo (IMPRONTA_PROMPT) e' il nome della "
     "cartella di cache del Markdown. Cambiare una virgola qui vuol dire rileggere "
     "tutte le pagine di tutti i cataloghi: mezz'ora per catalogo."),
    ("ingestion/indicizza.py", "ISTRUZIONI_FIGURA", "La descrizione di una figura (VLM)",
     "Una chiamata per ogni immagine estratta (891 su EUROSAND). Il `{titolo}` e' il "
     "titolo della pagina da cui viene il ritaglio: senza, un ritaglio di 221x149 px "
     "di sassi rossi diventa «possibly dried fruit or processed food». La descrizione "
     "finisce nel Markdown al posto del segnaposto `<!-- image -->`, quindi accanto "
     "al codice articolo della figura."),
]


def costante(percorso, nome):
    """Il valore della costante `nome`, dal sorgente. Senza importare."""
    albero = ast.parse((QUI / percorso).read_text(encoding="utf-8"))
    for n in albero.body:
        if isinstance(n, ast.Assign) and any(
                isinstance(b, ast.Name) and b.id == nome for b in n.targets):
            return ast.literal_eval(n.value)
    raise LookupError(f"{nome} non trovata in {percorso}")


def main():
    fuori = [
        "# I prompt del sistema",
        "",
        "**Generato da `mostra-prompt.py` leggendo il codice.** Non si modifica a",
        "mano: si cambia il prompt nel sorgente e si rigenera. Il documento e' qui",
        "perche' i prompt sono decisioni di progetto, non dettagli di",
        "implementazione, e finora si potevano leggere solo aprendo il Python.",
        "",
        f"Aggiornato il {datetime.date.today().strftime('%d/%m/%Y')}.",
        "",
        "Sono quattro. Nessuno e' configurabile: stanno nel codice, e cambiarne uno",
        "richiede di ricostruire l'immagine Docker (**D18** in",
        "`DECISIONI-APERTE.md`). L'unica manopola e' `SUFFISSO_SISTEMA`, una",
        "variabile d'ambiente che si **aggiunge in coda** al primo, oggi vuota.",
        "",
    ]
    for i, (f, nome, titolo, perche) in enumerate(PROMPT, 1):
        fuori += [f"## {i}. {titolo}", "",
                  f"`{f}` → `{nome}`", "", perche, "", "```", costante(f, nome).rstrip(), "```", ""]
    fuori += [
        "## Com'e' fatto il CONTESTO",
        "",
        "`orchestratore/prompt.py` → `contesto()`. Si accoda al prompt 1, e i numeri",
        "fra parentesi quadre sono quelli che la risposta deve citare:",
        "",
        "```",
        "CONTESTO:",
        "[1] (EUROSAND CATALOGO 2024 (1).pdf, pagina 7)",
        "DEKOSTEINE pietre decorative 9 - 13 mm | codice | colore | ...",
        "",
        "[2] (CATALOGO IPURO 2025.pdf, pagina 24)",
        "...",
        "```",
        "",
        "Senza nessun brano diventa una riga sola, `CONTESTO: nessun documento",
        "pertinente trovato.` — dichiarata, non omessa: il modello deve sapere che",
        "non ha materiale, non trovarsi il campo vuoto.",
        "",
        "## Cosa NON sta nei prompt",
        "",
        "- **I permessi.** Il filtro per gruppi, aziende e stato della fonte e' nella",
        "  query SQL (`recupero.py`). Un prompt si convince, una `WHERE` no.",
        "- **I nomi dei documenti visibili.** Stessa ragione.",
        "- **La riscrittura della domanda in termini di catalogo.** Misurata il 21 e il",
        "  22/09/2026, quattro volte: guadagno zero o negativo, e inventa attributi",
        "  («Marrakesch» → «sfere in resina», che sono di metallo). Il racconto e' in",
        "  `valutazione/RISULTATI.md`.",
        "",
    ]
    (QUI / "PROMPT.md").write_text("\n".join(fuori), encoding="utf-8")
    print(f"PROMPT.md: {len(PROMPT)} prompt, {len('\n'.join(fuori).splitlines())} righe")


if __name__ == "__main__":
    main()
