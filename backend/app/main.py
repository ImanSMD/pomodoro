import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Pomodoro API")

# Moves onto Settings in 1.2. The browser needs this from the first frontend
# request in 1.6 — curl and the tests pass without it, so a missing header here
# would only surface during hand-verification.
# localhost only, deliberately. 127.0.0.1 and localhost are different *sites*,
# so a page served from http://127.0.0.1:5173 would make cross-site calls to
# http://localhost:8000 and the browser would withhold the SameSite=Lax refresh
# cookie: login appears to work, then the session dies at the first refresh.
# A loud CORS error on the wrong hostname beats a silent logout 15 minutes in.
_TRUTHY = {"1", "true", "t", "y", "yes", "on"}

_DEFAULT_ORIGINS = "http://localhost:5173"
# Fall back on the POST-SPLIT value, not the raw string: "," and ",," are
# truthy but filter down to [], which is the silent reject-every-browser-request
# state this guard exists to prevent.
def _parse_origins(raw: str) -> list[str]:
    return [o.strip() for o in raw.split(",") if o.strip()]


_origins = _parse_origins(os.getenv("CORS_ORIGINS", ""))

if not _origins:
    # Substituting silently would be its own trap: in production this variable
    # decides the whole allowlist, and falling back to a developer's Vite
    # origin means every real request is rejected with nothing in the log —
    # while a page on a victim's own localhost:5173 keeps credentialed access.
    # pydantic-settings' own truthy set, since this guard moves onto Settings
    # in 1.2 — COOKIE_SECURE=on must not slip past it.
    if os.getenv("COOKIE_SECURE", "").strip().lower() in _TRUTHY:
        raise RuntimeError(
            "CORS_ORIGINS is empty but COOKIE_SECURE is on. Refusing to fall "
            "back to a localhost development origin in a production config."
        )
    logging.getLogger("uvicorn.error").warning(
        "CORS_ORIGINS is empty; falling back to %s. Set it explicitly.",
        _DEFAULT_ORIGINS,
    )
    _origins = _parse_origins(_DEFAULT_ORIGINS)

if "*" in _origins:
    # Starlette special-cases allow_origins=["*"] under allow_credentials by
    # echoing the caller's Origin instead of sending a literal "*". With the
    # refresh cookie and bearer token riding on these requests, that hands any
    # website a credentialed read of this API. Refuse at boot rather than serve
    # it: "*" is the obvious thing to write when first deploying.
    raise RuntimeError(
        "CORS_ORIGINS='*' is not allowed while credentials are enabled. "
        "List explicit origins, e.g. https://app.example.com"
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,  # the refresh-token cookie rides on these requests
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
