"""Il gate: decide se e come si puo' rispondere.

Regola high-water-mark: se QUALUNQUE chunk recuperato appartiene a una
sorgente `interno`, tutto il turno va sul modello interno. Nessuna eccezione,
nessuna euristica.

La contaminazione e' della CONVERSAZIONE, non del turno: lo storico torna al
modello a ogni messaggio, quindi ripulirla sarebbe illusorio. Una volta
contaminata resta interna fino alla fine.

Fail closed: se la rotta interna non e' configurata, si RIFIUTA di rispondere.
Uno stub permissivo sarebbe peggio di niente — insegnerebbe a ignorare il gate,
e il giorno in cui si accende il modello interno si scoprirebbe che non ha mai
funzionato.
"""
import os

from . import egress, recupero

RIFIUTO = (
    "Questa domanda richiede fonti che non lasciano l'azienda. "
    "Il modello interno non e' ancora attivo, quindi non posso rispondere."
)


class RispostaRifiutata(Exception):
    """Turno interno senza rotta interna configurata."""


def leggi(conn, conversation_id: str | None) -> bool:
    if not conversation_id:
        return False
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM conversation_taint WHERE conversation_id = %s",
                    (conversation_id,))
        return cur.fetchone() is not None


def contamina(conn, conversation_id: str | None, source_id: str | None):
    if not conversation_id:
        return
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO conversation_taint (conversation_id, source_id)
               VALUES (%s, %s) ON CONFLICT (conversation_id) DO NOTHING""",
            (conversation_id, source_id),
        )
    conn.commit()


def rotta_interna_configurata() -> bool:
    return bool(os.environ.get("LLM_RAGIONAMENTO_INTERNO"))


def applica(conn, conversation_id: str | None, righe) -> dict:
    """Valuta la contaminazione e imposta il contesto di egress.

    Restituisce la decisione: rotta da usare, se il turno e' interno, e la
    sorgente che ha contaminato.
    """
    gia_interno = leggi(conn, conversation_id)
    fonte = recupero.contiene_interno(righe)

    interno = gia_interno or fonte is not None
    if fonte is not None and not gia_interno:
        contamina(conn, conversation_id, fonte)

    # Da qui in avanti la guardia di egress sa che siamo in un turno interno:
    # qualsiasi chiamata verso EGRESS_EXTERNAL solleva EgressVietato.
    egress.turno_interno.set(interno)

    if interno and not rotta_interna_configurata():
        raise RispostaRifiutata(RIFIUTO)

    return {
        "interno": interno,
        "fonte_contaminante": fonte,
        "gia_contaminata": gia_interno,
        "rotta": os.environ.get("LLM_RAGIONAMENTO_INTERNO") if interno
                 else os.environ.get("LLM_RAGIONAMENTO", "ragionamento"),
    }
