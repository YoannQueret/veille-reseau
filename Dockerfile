FROM python:3.13-slim
WORKDIR /app
COPY app.py enedis.py storage.py auth.py ./
COPY static ./static
RUN useradd --uid 10001 --create-home veille && mkdir /data && chown veille:veille /data
USER veille
ENV HOST=0.0.0.0 PORT=8000 DATABASE_PATH=/data/veille.sqlite3 PYTHONUNBUFFERED=1
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
CMD ["python", "app.py"]
