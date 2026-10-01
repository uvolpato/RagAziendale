"""Il metro delle domande APERTE: un giudice che legge le righe citate.

    D=/tmp/val-$(date +%s)
    docker compose cp valutazione orchestratore:$D
    docker compose exec -T orchestratore sh -c "cd /app && python $D/aperte.py > /var/tmp/aperte.txt 2>&1"
    docker compose exec -T orchestratore cat /var/tmp/aperte.txt

Una cartella NUOVA ogni volta (vedi misura.py: `cp` su una cartella che
esiste copia DENTRO, e si rimisura la versione vecchia credendo di aver
cambiato qualcosa).

L'esito si scrive in `/var/tmp`, non nella cartella copiata: quella arriva
di root e il container gira come utente, quindi non ci si puo' scrivere
(«Permission denied», preso in pieno al primo giro). E si parte da `/app`,
o `import orchestratore` non trova niente.

PERCHE' ESISTE. Sulle domande chiuse il metro c'e' da sempre: `misura.py`
conta se la pagina giusta esce, e due volte l'1/10/2026 ha smentito
modifiche che «sembravano» migliori (il critico in parallelo, il prompt sul
parallelismo). Sulle domande aperte quella rete non c'e', e si e' visto:
«da 223 a 57 secondi e la risposta e' migliore» — il tempo misurato, il
«migliore» giudicato a occhio su una esecuzione sola.

Una domanda aperta non ha una pagina giusta, quindi non si puo' contare un
`riscontro`. Si puo' pero' chiedere a un modello di giudicare cose
VERIFICABILI, dandogli la domanda, la risposta e **le righe che la risposta
ha davvero citato**. Ancorato cosi', non puo' inventarsi che una cosa c'e':
ce l'ha davanti.

COSA GIUDICA. Quattro cose concrete, non «e' una bella risposta»:

    esistono      ogni cosa proposta corrisponde a una riga citata, e la
                  riga la descrive davvero cosi'. Il caso vero: «una
                  confezione regalo elegante» dove la riga dice «Christmas
                  gift bag» — esiste, ma non e' quello che la risposta dice.
    servono       le proposte c'entrano col bisogno. Una confezione
                  NATALIZIA per un compleanno di ottobre esiste e non serve.
    dove          si capisce dove guardare: documento e pagina.
    onesta        se in archivio non c'e' niente di adatto, lo dice invece
                  di riempire. Su «articoli per il giardinaggio» proporre
                  qualcosa E' il fallimento, non il successo.

IL RISCHIO, detto subito: e' un modello che giudica un modello. Per questo
giudica poche cose concrete invece di dare un voto, e stampa il MOTIVO di
ogni verdetto — la prima volta si leggono a mano, e se non sono sensati il
metro si butta. Un metro di cui non ci si fida e' peggio di nessun metro.
"""
import json
import os
import pathlib
import sys
import time

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, "/app")
from orchestratore import grafo, modello                      # noqa: E402

QUI = pathlib.Path(__file__).resolve().parent
GRUPPI = os.environ.get("VAL_GRUPPI", "Responsabile Acquisti,acquisti,"
                                      "azienda-decobrands,tutti").split(",")

P_GIUDICE = """Giudichi la risposta di un assistente aziendale a una domanda APERTA — una di quelle che non hanno una pagina giusta, ma un ventaglio di cose utili.

Ti do: cosa ha chiesto la persona, a cosa le serve, la risposta dell'assistente, e LE RIGHE DELL'ARCHIVIO che la risposta ha citato. Le righe sono la verita': quello che non e' li' dentro, non esiste.

Rispondi a quattro domande, ognuna si' o no, ognuna con mezza riga di motivo.

1. ESISTONO. Ogni cosa che la risposta propone corrisponde a una riga citata, E la riga la descrive davvero cosi'? Attento al caso insidioso: la risposta dice «una confezione regalo elegante» e la riga dice «Christmas gift bag» — la cosa esiste, ma la risposta la racconta diversa da com'e'. Quello e' NO.

2. SERVONO. Le proposte c'entrano col bisogno dichiarato?

   Attenzione, perche' qui e' facile essere severi nel modo sbagliato. Chi usa questo sistema e' un VENDITORE che deve farsi un'idea di cosa proporre a un cliente: **meglio un articolo in piu' che uno che poteva andare bene e non si e' visto**. Un vaso mostrato a chi chiede «giardinaggio» e' un suggerimento legittimo, non un errore — un commesso farebbe lo stesso. Quindi una proposta ADIACENTE, utile da mostrare, e' SI.

   E' NO solo quando la proposta non serve davvero a quel bisogno: una confezione natalizia per un compleanno di ottobre esiste, e' pure carina, ma a quel cliente non la vendi.

3. DOVE. Chi legge capisce dove andare a guardare — il documento e la pagina di ogni cosa proposta?

4. ONESTA. La risposta descrive le cose per quello che sono, senza far credere che l'archivio contenga categorie che non ha?

   NON si chiede di essere avari: proporre articoli vicini e' il mestiere. Si chiede di non spacciarli. «Si', abbiamo articoli per il giardinaggio» seguito da vasi decorativi e' NO, perche' afferma un reparto che non esiste; «attrezzi da giardino non ne abbiamo, ma ci sono vasi e piante» e' SI, e propone esattamente le stesse cose. La differenza e' tutta nel come lo dice.

   E' NO anche quando inventa un codice, un prezzo o una pagina.

Una risposta che invece di proporre FA UNA DOMANDA alla persona non e' automaticamente sbagliata: se la domanda era troppo vaga per rispondere, chiedere e' la cosa giusta — ma la domanda deve essere utile e ancorata a cosa c'e' in archivio, non generica. Giudicala con lo stesso metro: «esistono» e «servono» riguardano le strade che propone.

Sii severo e concreto. Non premiare una risposta perche' e' scritta bene."""

I_GIUDIZIO = [{"type": "function", "function": {
    "name": "giudizio",
    "description": "Il tuo verdetto sulle quattro cose.",
    "parameters": {"type": "object", "properties": {
        "esistono": {"type": "boolean"},
        "esistono_perche": {"type": "string"},
        "servono": {"type": "boolean"},
        "servono_perche": {"type": "string"},
        "dove": {"type": "boolean"},
        "dove_perche": {"type": "string"},
        "onesta": {"type": "boolean"},
        "onesta_perche": {"type": "string"}},
        "required": ["esistono", "esistono_perche", "servono", "servono_perche",
                     "dove", "dove_perche", "onesta", "onesta_perche"]}}}]

VOCI = ("esistono", "servono", "dove", "onesta")


def giudica(domanda, bisogno, risposta, citate):
    """Il verdetto sulle quattro voci, o None se il giudice non decide."""
    righe = "\n".join(
        "- [%s, pagina %s] %s" % (r.get("documento", "?"), r.get("page", "?"),
                                  " ".join(str(r.get("descrizione")
                                               or r.get("content") or "").split())[:260])
        for r in citate) or "(la risposta non ha citato nessuna riga)"
    messaggi = [{"role": "system", "content": P_GIUDICE},
                {"role": "user", "content":
                    f"DOMANDA: {domanda}\nA COSA SERVE: {bisogno}\n"
                    f"\nRISPOSTA DELL'ASSISTENTE:\n{risposta}\n\n"
                    f"RIGHE CITATE (la verita'):\n{righe}"}]
    for _ in range(2):
        m = modello.messaggio(messaggi, max_tokens=1200, tools=I_GIUDIZIO,
                              tool_choice="required", ragiona=False)
        m.pop("reasoning_content", None)
        for tc in m.get("tool_calls") or []:
            if tc.get("function", {}).get("name") == "giudizio":
                try:
                    return json.loads(tc["function"].get("arguments") or "{}")
                except (TypeError, ValueError):
                    break
        messaggi += [m, {"role": "user",
                         "content": "Rispondi chiamando lo strumento `giudizio`."}]
    return None


def citate_davvero(risposta, confermate):
    """Le righe che la risposta nomina con «[[n]]». Il giudice deve vedere
    QUELLE, non tutte quelle trovate: una riga recuperata e non citata non
    e' una proposta, e giudicarla falserebbe il voto in meglio."""
    import re
    numeri = {int(n) for n in re.findall(r"\[\[\s*(\d+)\s*\]\]", risposta or "")}
    return [confermate[n - 1] for n in sorted(numeri)
            if 1 <= n <= len(confermate)]


def main():
    dati = json.loads((QUI / "domande-aperte.json").read_text(encoding="utf-8"))
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    print("Il metro delle domande aperte — %d domande\n" % len(dati["domande"]))
    print("%-44s %-5s %-5s %-5s %-5s %-5s %s"
          % ("domanda", "esist", "serv", "dove", "onest", "tot", "sec"))
    print("-" * 86)
    totale, massimo, dettagli = 0, 0, []
    for d in dati["domande"]:
        t0 = time.monotonic()
        try:
            righe, risposta, _ = grafo.cerca(conn, d["domanda"], GRUPPI)
        except Exception as e:
            print("%-44s  ESPLOSO: %s: %s" % (d["domanda"][:44], type(e).__name__, e))
            try:
                conn.rollback()
            except Exception:
                pass
            continue
        secondi = time.monotonic() - t0
        g = giudica(d["domanda"], d.get("bisogno", ""), risposta,
                    citate_davvero(risposta, righe))
        if g is None:
            print("%-44s  il giudice non ha deciso" % d["domanda"][:44])
            continue
        voti = [bool(g.get(v)) for v in VOCI]
        totale += sum(voti)
        massimo += len(VOCI)
        print("%-44s %-5s %-5s %-5s %-5s %d/4   %3.0f"
              % (d["domanda"][:44],
                 *["si" if v else "NO" for v in voti], sum(voti), secondi))
        dettagli.append((d["domanda"], g, risposta))
    print("-" * 86)
    print("TOTALE  %d / %d" % (totale, massimo))

    # I motivi, per intero: la prima volta si leggono a mano per decidere se
    # del giudice ci si puo' fidare. Un metro di cui non ci si fida e' peggio
    # di nessun metro.
    print("\n\n== PERCHE', voce per voce ==")
    for domanda, g, risposta in dettagli:
        print("\n«%s»" % domanda)
        for v in VOCI:
            print("   %-9s %-3s %s" % (v, "si" if g.get(v) else "NO",
                                       str(g.get(v + "_perche") or "")[:150]))
        print("   risposta: %s" % " ".join(str(risposta).split())[:180])


if __name__ == "__main__":
    main()
