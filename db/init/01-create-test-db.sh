#!/bin/bash
# Runs ONLY on first initialisation of the pgdata volume. An existing volume
# from an earlier `up` will not have this database — recreate with
# `docker compose down -v` if the test suite reports it missing.
# No `-u`, and no `set` outside this script's own scope: if the executable bit
# is ever lost (Windows checkout, zip/tarball), postgres SOURCES this file
# instead of executing it, and -u would leak into the entrypoint's shell — which
# deliberately runs without it — aborting the rest of initialisation.
set -eo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
	CREATE DATABASE "${POSTGRES_TEST_DB:-${POSTGRES_DB}_test}";
SQL
