"""Il contesto del grafo, servito all'orchestratore. SOLO LETTURA.

    docker compose --profile cognee up -d cognee-lettore
    curl -s 'http://cognee-lettore:8800/contesto?cerca=a+che+punto+siamo&dataset=sviluppo'

PERCHE' UN SERVIZIO E NON UNA LIBRERIA. Il pacchetto Cognee pesa 1,3 GB e si
porta dentro litellm: metterlo nell'orchestratore vorrebbe dire quel peso e
quello strato dentro il servizio che risponde alle domande. Qui resta dov'e'
gia', e l'orchestratore fa una chiamata HTTP sulla rete interna — millisecondi.

PERCHE' SOLO LETTURA. La stessa regola che abbiamo applicato all'indice il
4/10/2026: chi scrive sta nell'ingestione, chi legge nell'orchestratore. Qui
non c'e' nessun `add` e nessun `cognify`: solo `search`.

PERCHE' `only_context`. Misurato il 5/10/2026 col grafo pieno:
`GRAPH_COMPLETION` scrive la risposta col modello, carica 37.257 token nel
prompt — piu' del contesto che il nostro 14b ha per slot — e dopo 307 secondi
muore con `ContextWindowExceededError`. `only_context` restituisce i pezzi
collegati in 1-3 secondi, e la risposta la scrive il nostro redattore, che
quel lavoro lo fa gia'.

I PERMESSI NON SI DECIDONO QUI. Il chiamante passa i dataset; questo servizio
li gira a Cognee, che applica le sue ACL (dimostrate il 5/10: isolamento,
ereditarieta' dal ruolo, revoca). Chi traduce i gruppi Keycloak in dataset e'
l'orchestratore, che quei gruppi li ha nel token.
"""
import asyncio
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, "/app")

from prova import configura

PORTA = int(os.environ.get("COGNEE_PORTA", "8800"))
QUANTI = int(os.environ.get("COGNEE_TOP_K", "10"))

_cognee = None
_loop = None


def _avvia():
    """Una volta sola: la configurazione e il ciclo asincrono."""
    global _cognee, _loop
    _cognee = configura()
    _loop = asyncio.new_event_loop()
    print("lettore del grafo pronto sulla porta %d" % PORTA, flush=True)


async def _dataset(nomi):
    """I dataset per nome, fra quelli che l'utente di servizio possiede."""
    from cognee.modules.users.methods import get_user_by_email
    from cognee.modules.users.permissions.methods import get_readable_datasets
    fuori = []
    for nome in nomi:
        servizio = await get_user_by_email(
            "ingestione-%s@assistente.locale" % nome)
        if not servizio:
            continue
        for d in await get_readable_datasets(servizio.id):
            if d.name == nome:
                fuori.append((servizio, d))
    return fuori


async def _contesto(cerca, nomi, quanti):
    pezzi = []
    for servizio, d in await _dataset(nomi):
        fuori = await _cognee.search(
            query_text=cerca, user=servizio,
            query_type=_cognee.SearchType.GRAPH_COMPLETION,
            dataset_ids=[d.id], only_context=True, top_k=quanti,
            # La memoria di sessione SPENTA: senza, il contesto comincia con
            # «Previous conversation:» e si porta dietro le domande di
            # un'altra conversazione.
            session_id=None)
        for r in fuori or []:
            testo = r.get("search_result") if isinstance(r, dict) else r
            pezzi.append({"dataset": d.name, "contesto": str(testo)})
    return pezzi


class Lettore(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):           # niente rumore per ogni richiesta
        pass

    def _rispondi(self, codice, corpo):
        dati = json.dumps(corpo, ensure_ascii=False).encode()
        self.send_response(codice)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(dati)))
        self.end_headers()
        self.wfile.write(dati)

    def do_GET(self):
        parti = urlparse(self.path)
        if parti.path == "/salute":
            return self._rispondi(200, {"stato": "su"})
        if parti.path != "/contesto":
            return self._rispondi(404, {"errore": "si chiede /contesto"})
        q = parse_qs(parti.query)
        cerca = (q.get("cerca") or [""])[0].strip()
        nomi = [n for n in (q.get("dataset") or []) if n.strip()]
        quanti = int((q.get("quanti") or [QUANTI])[0])
        if not cerca or not nomi:
            return self._rispondi(400, {"errore": "servono `cerca` e `dataset`"})
        try:
            pezzi = _loop.run_until_complete(_contesto(cerca, nomi, quanti))
        except Exception as e:
            # Il grafo che non risponde non deve rompere il turno: chi chiama
            # tratta la lista vuota come «il grafo non ha niente».
            print("contesto non riuscito (%s: %s)" % (type(e).__name__, e),
                  flush=True)
            return self._rispondi(200, {"pezzi": [], "errore": type(e).__name__})
        return self._rispondi(200, {"pezzi": pezzi})


if __name__ == "__main__":
    _avvia()
    ThreadingHTTPServer(("0.0.0.0", PORTA), Lettore).serve_forever()
