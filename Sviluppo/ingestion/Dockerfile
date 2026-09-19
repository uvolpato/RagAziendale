# Indicizzazione delle cartelle (indicizza.py). Immagine pesante per via di
# Docling (layout, tabelle, OCR): torch solo CPU per non scaricare CUDA.
FROM python:3.12-slim
WORKDIR /app
# OCR con Tesseract dal pacchetto Debian, lingua italiana compresa: niente
# modelli da scaricare da server esterni (RapidOCR li prende da modelscope.cn).
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
        tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# I modelli (layout e tabelle, da Hugging Face) si scaricano QUI, una volta:
# a regime il servizio non esce su internet (HF_HUB_OFFLINE), come ogni altro
# componente (egress).
RUN docling-tools models download layout tableformer -o /opt/modelli
# HOME scrivibile per nobody: torch tiene li' le sue cache.
ENV DOCLING_MODELLI=/opt/modelli HF_HUB_OFFLINE=1 PYTHONUNBUFFERED=1 HOME=/tmp
COPY indicizza.py .
USER nobody
CMD ["python", "indicizza.py"]
