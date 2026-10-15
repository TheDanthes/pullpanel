FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/thedanthes/pullpanel" \
      org.opencontainers.image.title="PullPanel" \
      org.opencontainers.image.description="Panel web para actualizar imágenes Docker desde ZimaOS con un botón"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PULLPANEL_DATA=/data

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

VOLUME ["/data"]
EXPOSE 8790

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8790/healthz', timeout=4)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8790", "--no-access-log"]
