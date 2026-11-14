# Churn-Spark worker: PySpark + Delta + JDBC drivers on Linux (no winutils needed).
# Secrets come from .env at runtime (compose env_file); data/ and models/ are mounted volumes.
FROM python:3.10.16-slim-bookworm

# Java 17 for Spark; procps because Spark's launch scripts call `ps`.
RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless procps \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Run as an unprivileged user.
RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app src ./src
COPY --chown=app:app config ./config
COPY --chown=app:app sql ./sql
USER app

# Resolve the Delta and JDBC jars at build time, so containers start without downloading them.
RUN python -c "from src.ingestion.spark_session import get_spark; get_spark().stop()"
# Keep them in /app/jars so Spark loads them directly at run time; Ivy would otherwise write to its
# cache on every start, which the read-only container filesystem (compose.yaml) does not allow.
USER root
RUN mkdir /app/jars && cp /home/app/.ivy2/jars/*.jar /app/jars/ && chown -R app:app /app/jars
USER app

CMD ["python", "-m", "src.integration.supabase_worker", "--poll", "60"]
