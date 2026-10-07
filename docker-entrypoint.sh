#!/bin/sh
# Create or update the database, then hand over to the gateway.
#
# `exec` matters: it replaces this shell with the Python process, so the
# gateway becomes PID 1 and receives the signals Docker sends. Without it,
# `docker stop` would reach the shell, the shell would ignore it, and ten
# seconds later Docker would kill the container -- dropping whatever requests
# were in flight.
set -e

if [ "${GATEWAY_MIGRATE:-true}" = "true" ]; then
    # Safe to run every start: a database already at the latest revision is a
    # no-op. Set GATEWAY_MIGRATE=false where several instances start at once
    # and you would rather migrate deliberately, from one place, than have
    # them race each other.
    python -m alembic upgrade head
fi

exec python -m gateway "$@"
