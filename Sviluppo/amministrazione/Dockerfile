FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY . /app/amministrazione
# Non root: il servizio non ha bisogno di scrivere sul filesystem.
USER nobody
EXPOSE 8000
CMD ["uvicorn", "amministrazione.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
