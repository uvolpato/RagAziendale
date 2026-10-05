# Cognee: la PROVA del grafo di conoscenza (D13), su una cartella per volta.
#
# Immagine separata, non dentro `ingestion` e non dentro l'orchestratore: e'
# una prova, deve potersi buttare. Se il grafo entra in produzione, il posto
# giusto e' `ingestion` (scrive) piu' un lettore nell'orchestratore (legge),
# come abbiamo fatto per l'indice il 4/10/2026.
#
# NIENTE VERSO FUORI. La documentazione di Cognee dice che «configuring only
# one [provider] will cause the other to fall back to OpenAI»: una variabile
# sbagliata e i documenti aziendali partono verso un servizio esterno. Non ci
# si fida della configurazione: il proxy punta nel vuoto e solo i nostri host
# sono esclusi, quindi una chiamata esterna non puo' riuscire nemmeno per
# errore. E' la stessa regola del resto del progetto — fallire CHIUSO.
FROM python:3.12-slim

WORKDIR /app

# Kuzu (il grafo) e LanceDB (i vettori) sono incorporati: nessun servizio in
# piu' da sorvegliare, che era una delle obiezioni scritte in DECISIONI-APERTE.
# `[postgres]` perche' il relazionale e i vettori stanno sul NOSTRO
# Postgres (con pgvector), non su sqlite e LanceDB dentro un volume: un
# magazzino in meno da sorvegliare e da salvare. Porta `asyncpg`.
# `-binary` e non `[postgres]`: il secondo compila asyncpg, e in
# un'immagine slim non c'e' il compilatore — «Getting requirements to
# build wheel did not run successfully».
RUN pip install --no-cache-dir "cognee[postgres-binary]==1.6.2"

# IL RIGHELLO, dentro l'immagine. Cognee conta i token PRIMA di spedire,
# per non superare il limite d'ingresso di bge-m3 e per fare i lotti: il
# conto lo fa dal suo lato, e per farlo gli serve un tokenizer. Senza,
# esce verso huggingface.co (bloccato dal proxy qui sotto: 23 secondi di
# tentativi a vuoto a ogni avvio) e poi ripiega su una stima — che su un
# pezzo lungo fa spedire piu' di quanto il server accetti, l'errore
# «input (522 tokens) is too large» che a settembre ha lasciato 376 pezzi
# senza vettore.
#
# Si scarica QUI, dove la rete e' lecita perche' si sta costruendo. A
# valle il proxy e' chiuso e HF_HUB_OFFLINE dice di usare solo cio' che
# c'e' gia'.
ENV HF_HOME=/opt/hf
COPY righello.py /tmp/righello.py
RUN python /tmp/righello.py && du -sh /opt/hf

COPY prova.py t114.py permessi.py contesto.py confronto.py servizio.py ./
# La vista del grafo: `grafo_api.py` serve i nodi e gli archi del Kuzu vero in
# sola lettura, `grafo.html` li disegna e si aggiorna da solo. Copiare l'HTML
# serve perche' FastAPI lo restituisce con FileResponse, cosi' si puo'
# modificare la pagina senza riavviare il server.
COPY grafo_api.py grafo_live.py grafo.html ./

# Il proxy nel vuoto: qualunque host non escluso riceve un rifiuto immediato.
ENV HUGGINGFACE_TOKENIZER=BAAI/bge-m3 \
    HF_HUB_OFFLINE=1 \
    HTTP_PROXY=http://127.0.0.1:1 \
    HTTPS_PROXY=http://127.0.0.1:1 \
    NO_PROXY=host.docker.internal,postgres,localhost,127.0.0.1 \
    no_proxy=host.docker.internal,postgres,localhost,127.0.0.1

ENTRYPOINT ["python", "prova.py"]
