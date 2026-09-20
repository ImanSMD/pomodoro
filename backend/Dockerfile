FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install from the lockfile, not from pyproject's floors. pyproject declares
# what we depend on; requirements.lock pins what actually gets installed, so a
# clean rebuild weeks later resolves the same stack. Regenerate with:
#   docker compose exec api pip freeze | grep -vE '^-e |pomodoro-api' | sort > backend/requirements.lock
# The -e filter matters: pip freeze emits `-e /app` for the editable install,
# and pip would then try to build /app from a context that has no source yet.
COPY requirements.lock ./
RUN pip install --upgrade pip && pip install -r requirements.lock

COPY . .

# Editable, deliberately: a non-editable install would bake a copy of app/ into
# site-packages that goes stale the moment the source is bind-mounted, and
# anything importing `app` from outside /app (alembic autogenerate, say) would
# silently read the snapshot instead of the live models. --no-deps keeps the
# locked dependency layer above authoritative.
RUN pip install --no-deps -e .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
