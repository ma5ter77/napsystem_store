FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg postgresql-client && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.11.32
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN useradd -r -u 10001 nap && mkdir -p /app/var/storage /app/var/jobs && chown -R nap /app/var
USER nap
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
