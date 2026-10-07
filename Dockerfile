# The gateway, in a container.
#
# docker compose up
#
# The React dashboard builds to static files in a second stage once it exists.

FROM python:3.12-slim

# Not root. This process holds a provider key and accepts traffic from the
# network; if something goes wrong in it, the blast radius should not include
# the whole container.
RUN useradd --create-home --uid 10001 gateway

WORKDIR /app

# Dependencies before code, so editing a Python file does not reinstall
# SQLAlchemy. Docker caches each layer, and this ordering is the difference
# between a two-second rebuild and a two-minute one.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gateway/ ./gateway/
COPY migrations/ ./migrations/
COPY alembic.ini docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

# The database goes on a volume, not in the image. A container is disposable;
# the record of who sent what is not.
RUN mkdir -p /data && chown gateway:gateway /data

ENV GATEWAY_DATABASE_URL=sqlite+aiosqlite:////data/gateway.db \
    GATEWAY_HOST=0.0.0.0 \
    GATEWAY_PORT=8080 \
    PYTHONUNBUFFERED=1
# GATEWAY_HOST is 0.0.0.0 here and 127.0.0.1 everywhere else. The default is
# right outside a container -- accept connections from this machine only -- and
# useless inside one, where nothing outside could ever reach it.
#
# PYTHONUNBUFFERED because Python buffers stdout when it is not a terminal, and
# a container whose logs appear four minutes late is a container nobody can
# debug.

USER gateway
EXPOSE 8080

# Compose and orchestrators use this to tell "running" from "working".
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request as r; r.urlopen('http://127.0.0.1:8080/health')"

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["serve"]
