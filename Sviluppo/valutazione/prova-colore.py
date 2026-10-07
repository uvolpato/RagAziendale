"""Il 14b sa dire a COSA e' attaccato un colore, se glielo chiedi da solo?

    docker compose cp valutazione/prova-colore.py orchestratore:/tmp/pc.py
    docker compose exec -T orchestratore python /tmp/pc.py [volte]

PERCHE'. `non-promettere` e' 0-1/3 su dodici configurazioni: su «ci sono anche
con i cuori blu?» il sistema conferma una riga la cui didascalia dice «light
blue with white hearts» — nastro azzurro, cuori bianchi. Tre tentativi dentro
lo schema del critico (un campo per copiare l'attributo, il campo passato a
chi scrive, il motivo legato al campo) non hanno tenuto alla misura.

Prima di aggiungere un secondo modello va separato quello che il progetto
chiede sempre di separare: e' un difetto NOSTRO — il critico riceve
quarant'anni di contorno e la didascalia annega — o e' il 14b che non sa
leggere «<colore> with <colore> hearts»?

Qui la domanda e' nuda: una didascalia, l'attributo chiesto, si' o no. Niente
prompt del critico, niente stato, niente altre righe. Se in isolamento
risponde giusto, il difetto e' nel contorno e si ripara in casa. Se sbaglia
anche qui, il modello e' il collo di bottiglia e un giudice che legge domanda
e riga insieme (cross-encoder, o decisione tipizzata locale) e' giustificato.

Le quattro didascalie sono quelle vere, dal catalogo Packara CELEBRATION.
"""
import collections
import sys

sys.path.insert(0, "/app")

from orchestratore import modello  # noqa: E402

CHIESTO = "cuori blu"

RIGHE = [
    ("p.5  tre nastri", "no",
     "Object: three ribbons with heart motifs Material: fabric ribbon with "
     "stitched edges Shape/size: narrow rectangular strips, vertically "
     "oriented; hearts evenly spaced "
     "Colours: white with red hearts pink with silver hearts "
     "light blue with white hearts Code: 01, 50, 71"),
    ("p.6  due nastri glitter", "si",
     "object: two ribbons with heart motifs material: fabric with glittered "
     "outlines shape/size: rectangular strips, vertical orientation "
     "Colours: pink with pink glitter hearts light blue with blue glitter "
     "hearts Code: 50 Code: 71"),
    ("p.12 nastro ricamato", "si",
     "Object: embroidered ribbon with repeating bear and heart motifs "
     "Material: fabric with raised embroidery texture "
     "Colours: pink with light blue hearts light blue with light blue hearts"),
    ("p.30 nastro glitter", "no",
     "Object: ribbon with heart pattern Material: woven fabric with glitter "
     "texture Shape/size: vertical strip, uniform width "
     "Colours: white with red hearts Code: 71"),
]

NUDO = """Ti do la didascalia di una foto di catalogo e una caratteristica.
Dimmi se l'oggetto della foto ha QUELLA caratteristica.

Attenzione a cosa e' attaccato cosa: in queste didascalie i colori si scrivono
«<colore dell'oggetto> with <colore> <motivo>». «light blue with white hearts»
e' un nastro AZZURRO con cuori BIANCHI."""

SENZA_AVVISO = NUDO.split("\n\nAttenzione")[0]

STRUMENTO = [{"type": "function", "function": {
    "name": "giudizio",
    "description": "Se la caratteristica c'e' o no.",
    "parameters": {"type": "object", "properties": {
        "attributo": {"type": "string",
                      "description": "Il pezzo di didascalia che porta la "
                                     "caratteristica, copiato con la cosa a "
                                     "cui e' attaccato. «non c'e'» se manca."},
        "esito": {"type": "string", "enum": ["si", "no"],
                  "description": "`si` solo se l'oggetto ha quella "
                                 "caratteristica."}},
        "required": ["attributo", "esito"]}}}]


def chiedi(sistema, didascalia):
    messaggi = [{"role": "system", "content": sistema},
                {"role": "user", "content":
                 "Caratteristica chiesta: «%s»\n\nDidascalia:\n%s"
                 % (CHIESTO, didascalia)}]
    m = modello.messaggio(messaggi, max_tokens=400, tools=STRUMENTO,
                          tool_choice="required", ragiona=False)
    import json
    for tc in m.get("tool_calls") or []:
        try:
            a = json.loads(tc["function"].get("arguments") or "{}")
        except (TypeError, ValueError):
            continue
        return (str(a.get("esito") or "?").lower(),
                str(a.get("attributo") or "")[:60])
    return "?", ""


def main():
    volte = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    for nome, sistema in (("CON l'avviso sui colori", NUDO),
                          ("SENZA l'avviso", SENZA_AVVISO)):
        print("=== %s — attributo chiesto: «%s» ===" % (nome, CHIESTO))
        giuste = 0
        for etichetta, atteso, didascalia in RIGHE:
            c = collections.Counter()
            copiato = ""
            for _ in range(volte):
                esito, attributo = chiedi(sistema, didascalia)
                c[esito] += 1
                copiato = attributo or copiato
            ok = c[atteso]
            giuste += ok
            print("  %-24s atteso %-3s -> %-22s %d/%d   [%s]"
                  % (etichetta, atteso, dict(c), ok, volte, copiato))
        print("  TOTALE %d/%d\n" % (giuste, len(RIGHE) * volte))


def tutte_insieme(sistema, volte, conversazione=False):
    """Le quattro didascalie in UNA chiamata, come fa il critico vero."""
    import json
    strumento = [{"type": "function", "function": {
        "name": "giudizi", "description": "Un verdetto per ogni riga.",
        "parameters": {"type": "object", "properties": {
            "esiti": {"type": "array", "items": {"type": "object",
                "properties": {
                    "riga": {"type": "integer"},
                    "attributo": {"type": "string"},
                    "esito": {"type": "string", "enum": ["si", "no"]}},
                "required": ["riga", "attributo", "esito"]}}},
            "required": ["esiti"]}}}]
    righe = chr(10).join("%d. %s" % (i, d) for i, (_, _, d) in enumerate(RIGHE, 1))
    messaggi = [{"role": "system", "content": sistema}]
    if conversazione:
        messaggi += [
            {"role": "user", "content": "ho bisogno di nastri bianchi con cuori rossi"},
            {"role": "assistant", "content": "Ho trovato nastri bianchi con cuori rossi."},
            {"role": "user", "content": "ci sono anche con i cuori blu?"}]
    messaggi.append({"role": "user", "content":
                     "Caratteristica chiesta: «%s»" % CHIESTO + chr(10)*2
                     + "Righe:" + chr(10) + righe})
    conta = [collections.Counter() for _ in RIGHE]
    for _ in range(volte):
        m = modello.messaggio(messaggi, max_tokens=900, tools=strumento,
                              tool_choice="required", ragiona=False)
        for tc in m.get("tool_calls") or []:
            try:
                a = json.loads(tc["function"].get("arguments") or "{}")
            except (TypeError, ValueError):
                continue
            for e in a.get("esiti") or []:
                try:
                    i = int(e.get("riga")) - 1
                except (TypeError, ValueError):
                    continue
                if 0 <= i < len(RIGHE):
                    conta[i][str(e.get("esito") or "?").lower()] += 1
    giuste = 0
    for (etichetta, atteso, _), c in zip(RIGHE, conta):
        giuste += c[atteso]
        print("  %-24s atteso %-3s -> %-22s %d/%d"
              % (etichetta, atteso, dict(c), c[atteso], volte))
    print("  TOTALE %d/%d" % (giuste, len(RIGHE) * volte))


if __name__ == "__main__":
    main()
    volte = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print("=== TUTTE INSIEME in una chiamata, con l-avviso ===")
    tutte_insieme(NUDO, volte)
    print()
    print("=== TUTTE INSIEME + la conversazione dei cuori ROSSI ===")
    tutte_insieme(NUDO, volte, conversazione=True)
