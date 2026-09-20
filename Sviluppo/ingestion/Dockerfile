# Indicizzazione delle cartelle (indicizza.py). Immagine pesante per via di
# Docling (layout, tabelle, OCR) e di torch con CUDA: ~4 GB in piu' del solo
# CPU, il prezzo per leggere i documenti sulla GPU invece che sul processore.
# Senza GPU l'immagine funziona lo stesso: si legge in CPU (GPU=off, oppure
# nvidia-smi che non risponde).
FROM python:3.12-slim
WORKDIR /app
# OCR con Tesseract dal pacchetto Debian, lingua italiana compresa: niente
# modelli da scaricare da server esterni (RapidOCR li prende da modelscope.cn).
# tzdata: la finestra notturna e' in ora locale (TZ), non UTC.
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 tzdata \
        tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
# cu128: e' la serie che parla con le schede Blackwell (RTX 50xx) oltre alle
# precedenti. Le librerie CUDA arrivano dentro le ruote: niente immagine nvidia.
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cu128
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# I modelli (layout e tabelle, da Hugging Face) si scaricano QUI, una volta:
# a regime il servizio non esce su internet (HF_HUB_OFFLINE), come ogni altro
# componente (egress).
# Solo layout e tabelle. Le immagini le descrive un VLM servito altrove
# (VLM_DESCRIZIONI=api): SmolVLM qui dentro pesava 3,3 GB e diceva "a few
# bottles" dove glm-ocr su LM Studio trascrive nomi e quantita' dei prodotti.
RUN docling-tools models download layout tableformer -o /opt/modelli
# HOME scrivibile per nobody: torch tiene li' le sue cache.
ENV DOCLING_MODELLI=/opt/modelli HF_HUB_OFFLINE=1 PYTHONUNBUFFERED=1 HOME=/tmp
# Le immagini estratte le scrive `nobody`: la cartella deve essere sua. Docker
# copia proprietario e permessi da qui dentro il volume quando lo crea vuoto;
# su un volume gia' esistente va sistemato a mano una volta sola:
#   docker run --rm -v assistente_immagini-data:/i alpine chown -R 65534:65534 /i
RUN mkdir -p /immagini && chown nobody:nogroup /immagini
COPY indicizza.py .
USER nobody
CMD ["python", "indicizza.py"]
