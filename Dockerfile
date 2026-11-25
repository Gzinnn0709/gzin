FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY gabecuts ./gabecuts
RUN pip install --no-cache-dir ".[whisper]"
VOLUME /app/data
EXPOSE 8000
ENTRYPOINT ["gabecuts"]
CMD ["live"]
