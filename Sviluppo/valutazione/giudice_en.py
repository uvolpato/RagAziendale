"""Il giudice in INGLESE, senza think: la lingua del prompt e' il problema?

    docker compose exec -T orchestratore sh -c "cd /app && python $D/giudice_en.py"

L'ipotesi e' dell'utente: Qwen3 ha visto molto piu' inglese che italiano, e
l'effetto si vede dove il compito e' articolato — seguire istruzioni a piu'
passi e tenerle insieme. E' esattamente dove il giudice sbagliava.

PERCHE' SI PROVA COSI'. In italiano il giudice fa 6/6 col ragionamento
acceso e 5/6 spento (accusa i ventagli esibendo prove che non c'entrano:
un ramo di limoni contro delle confezioni regalo). Con il punteggio pieno
non c'e' spazio per vedere un miglioramento, quindi il confronto utile e'
a think SPENTO: se l'inglese arriva a 6/6 senza ragionare, la lingua era
il collo di bottiglia e il ragionamento si risparmia.

Il contenuto e' lo stesso dell'italiano, tag per tag: si cambia la lingua,
non le regole. Se cambiassi anche quelle non saprei a cosa attribuire la
differenza — e oggi l'ho gia' fatto una volta, misurando sistema e metro
nello stesso passo.
"""
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, __file__.rsplit("/", 1)[0])

import banco                                                     # noqa: E402
from orchestratore import modello                                # noqa: E402

P_EN = """<role>
You judge the answer of a company assistant that searches the firm's product catalogues. You do not rewrite it: you only report what is wrong.
</role>

<who_reads_it>
A salesperson. They look for an article and then go and check it on the catalogue page. Item codes and prices are not what catalogues are for — do not ask for them.
</who_reads_it>

<what_you_get>
The conversation, what the system was supposed to do, the answer it gave, the ROWS THE ANSWER CITED, and a SAMPLE of what the archive really holds on that topic. The sample is not what the system found: it is what exists. It serves one purpose only — telling whether a denial was false.
</what_you_get>

<how_to_accuse>
For each of the two faults below you fill ONE FIELD: **the row that proves it, copied.** If that row does not exist, you leave the field empty, and empty means «no fault».

There is no other way to accuse: the accusation IS the evidence. A fault without the copied row is a suspicion, and suspicions are not delivered.

The row you copy must talk about the same thing the answer talks about. Any row at all is not evidence: if you are picking it because «something feels off» rather than because it contradicts what the answer says, leave the field empty.

Two empty fields is the normal outcome. Most answers have neither fault.
</how_to_accuse>

<denies_what_it_had>
Is there a sentence in the answer saying it found nothing, that there is none, that it cannot see or does not know — while that thing IS among the cited rows or in the sample? Copy that row.

It counts even if the answer lists the thing further down: the reader stops at the first sentence.

An affirmative answer is not a denial. «There are 13 catalogues» and «I found white ribbons with red hearts» deny nothing. If no sentence says something is missing, the field stays empty.
</denies_what_it_had>

<promises_what_it_lacked>
Does the opening claim to have found what was asked for, while a CITED row describes something else? Copy that row.

**For this fault the evidence may come ONLY from the cited rows, never from the sample.** A promise is disproved by what the writer had in hand, not by what exists elsewhere in the archive: the sample holds everything, and fishing in it convicts anyone. If there are no cited rows, this field stays empty — always.

If the answer describes what it has as it is, the field stays empty, even when what it has is not exactly what was asked for.

An answer that ASKS A QUESTION, or that offers choices to pick from, promises nothing: it is not proposing articles, it is narrowing down. Empty.
</promises_what_it_lacked>

<do_not_judge>
Style, length, politeness. Do not demand more articles than there are.
</do_not_judge>

<deliver>
With the `verdetto` tool.
</deliver>"""

banco.P_GIUDICE = P_EN

_vero = modello.messaggio


def _senza_think(messaggi, **k):
    k["ragiona"] = False
    return _vero(messaggi, **k)


modello.messaggio = _senza_think

if __name__ == "__main__":
    print("GIUDICE IN INGLESE, think spento\n")
    import calibra                                               # noqa: F401
