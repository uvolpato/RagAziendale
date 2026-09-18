# Servizio connettori: custode dei segreti dei collegamenti (decisione 70).
# Contesto di build = Sviluppo/ (vedi docker-compose.yml): qui si copia solo il
# package connettori (catalogo + motore + servizio), niente segreti ne .env.
FROM python:3.12-slim
WORKDIR /app
COPY servizio-connettori/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY connettori /app/connettori
USER nobody
EXPOSE 8000
CMD ["uvicorn", "connettori.servizio:app", "--host", "0.0.0.0", "--port", "8000"]
