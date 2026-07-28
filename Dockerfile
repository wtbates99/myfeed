FROM python:3.13-slim
RUN pip install --no-cache-dir feedparser
WORKDIR /app
COPY digest.py serve.py feeds.toml tech_pool.json econ_2026.json entrypoint.sh ./
ENV MYFEED_DATA=/data MYFEED_BIND=0.0.0.0 TZ=America/New_York
VOLUME /data
EXPOSE 8484
CMD ["./entrypoint.sh"]
