# Servizio azioni: l'unico con accesso al socket di Docker (riavvio/stop dei
# container, whitelist + registro). Contesto = azioni/.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY app.py /app/azioni/app.py
# Root: serve per parlare al socket di Docker (controllo dell'host). E' il
# motivo per cui questo servizio esiste separato e fa SOLO riavvio/stop.
EXPOSE 8000
CMD ["uvicorn", "azioni.app:app", "--host", "0.0.0.0", "--port", "8000"]
