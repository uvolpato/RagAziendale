# Immagine per i processi host di Dagster (webserver + daemon) e la code location.
# La code location e' caricata in-process via workspace.yaml (python_file): per un
# singolo nodo e' la forma piu' semplice. Le run girano in-process (esecutore
# predefinito) e fanno solo una POST al servizio `connettori`.
FROM python:3.12-slim
WORKDIR /opt/dagster
RUN pip install --no-cache-dir \
    dagster==1.10.21 dagster-graphql==1.10.21 dagster-webserver==1.10.21
COPY definitions.py workspace.yaml ./
ENV DAGSTER_HOME=/opt/dagster/dagster_home
EXPOSE 3000
