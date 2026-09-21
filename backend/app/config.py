"""Application settings.

This module is the single place environment configuration is read and
validated. The guards below were all carried over from section 1.1's reviews —
they exist here rather than in main.py because they need to reason about
several settings together.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, PrivateAttr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("uvicorn.error")

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_ROOT.parent

# The value compose ships so a fresh clone runs with no .env. Anything that
# looks like a real deployment must not still be using it.
DEV_JWT_SECRET = "dev-only-insecure-secret-change-me"
# localhost only, deliberately. To a browser, 127.0.0.1 and localhost are
# different *sites*, so a page served from http://127.0.0.1:5173 makes
# cross-site calls to http://localhost:8000 and the SameSite=Lax refresh cookie
# is withheld: login appears to work, then the session dies at the first
# refresh. A loud CORS error on the wrong hostname beats a silent logout, so do
# not "fix" this by adding the 127.0.0.1 spelling.
DEFAULT_CORS_ORIGINS = "http://localhost:5173"


def _is_loopback(origin: str) -> bool:
    """Loopback or unspecified, by value rather than by spelling.

    A literal set of strings missed 0:0:0:0:0:0:0:1, 127.0.0.2 and 127.1, all
    of which are loopback and all of which a deployed config must refuse.
    """
    host = (urlparse(origin).hostname or "").lower()
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            # inet_aton, because ipaddress rejects the shorthand forms the
            # resolver accepts: "127.1" is 127.0.0.1 to a browser but a
            # ValueError to ipaddress. A real hostname raises OSError here.
            address = ipaddress.ip_address(socket.inet_aton(host))
        except OSError:
            return False
    return address.is_loopback or address.is_unspecified


class Settings(BaseSettings):
    # Absolute, never ".", which resolves against whatever cwd the process
    # happens to have — pytest from the repo root and the alembic subprocess
    # from backend/ would otherwise read different files. Both locations are
    # listed because the layout differs by context: on the host the .env sits at
    # the repo root, while the container mounts only backend/ (so the repo root
    # is "/" there and compose supplies the environment directly instead).
    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", _BACKEND_ROOT / ".env"), extra="ignore"
    )

    database_url: str
    test_database_url: str | None = None

    # min_length: refusing only the exact placeholder still accepted
    # JWT_SECRET=x, which signs 30-day refresh tokens and, worse, reads as a
    # deliberately configured deployment to looks_deployed below.
    jwt_secret: str = Field(default=DEV_JWT_SECRET, min_length=32)
    # gt=0: a typo minting already-expired access tokens, or a refresh TTL of 0
    # logging every user out at the first refresh, should not start.
    access_token_ttl_minutes: int = Field(default=15, gt=0)
    refresh_token_ttl_days: int = Field(default=30, gt=0)
    cookie_secure: bool = False

    cors_origins: str = ""

    # A private attribute, not a field: a declared field would make
    # pydantic-settings look for ALLOWED_ORIGINS in the environment and try to
    # JSON-parse whatever it finds, so an unrelated variable of that name would
    # crash startup.
    _allowed_origins: list[str] = PrivateAttr(default_factory=list)

    @property
    def allowed_origins(self) -> list[str]:
        return self._allowed_origins

    @property
    def uses_dev_secret(self) -> bool:
        return self.jwt_secret == DEV_JWT_SECRET

    @property
    def looks_deployed(self) -> bool:
        """True when this config is not obviously a local dev box.

        Either signal alone is enough: a real secret means someone configured
        this deliberately, and Secure cookies only work over HTTPS.
        """
        return self.cookie_secure or not self.uses_dev_secret

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        if self.uses_dev_secret and self.cookie_secure:
            raise ValueError(
                "JWT_SECRET is still the development placeholder while "
                "COOKIE_SECURE is on. Refresh tokens live for "
                f"{self.refresh_token_ttl_days} days and would be signed with a "
                "value committed to the repo. Generate one: openssl rand -hex 32"
            )

        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]

        normalised: list[str] = []
        for origin in origins:
            if origin == "*":
                normalised.append(origin)
                continue
            parsed = urlparse(origin)
            host = parsed.hostname or ""
            try:
                port = parsed.port
            except ValueError:
                # urlparse defers port validation to attribute access, so a
                # non-numeric or out-of-range port raises here rather than
                # failing the shape check below.
                port = -1
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or "@" in parsed.netloc  # userinfo never appears in an Origin header
                or "*" in parsed.netloc  # no wildcard matching: Starlette compares exact strings
                or not host  # "http://:5173" has a truthy netloc but no host
                or not host.isascii()  # browsers send punycode, not unicode
                # -1 is the sentinel above; 0 parses cleanly but would then be
                # dropped by the falsy check below, widening "host:0" to "host"
                or port in {-1, 0}
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                # Starlette compares allow_origins to the Origin header as an
                # exact string, and that header never carries a path or a
                # trailing slash. "app.example.com" or "https://app.example.com/"
                # would therefore match nothing, rejecting every request with
                # nothing in the log.
                raise ValueError(
                    f"CORS_ORIGINS entry {origin!r} is not a bare origin. "
                    "Expected scheme://host[:port] with no trailing slash, path, "
                    "credentials or wildcard, and an IDNA/punycode host. "
                    "Starlette matches this against the Origin header as an "
                    "exact string, so anything else silently matches nothing."
                )

            scheme = parsed.scheme.lower()
            # A browser omits the default port from Origin, so keeping it here
            # would mean the entry never matches.
            if (scheme, port) in {("http", 80), ("https", 443)}:
                port = None
            # Lowercased because the comparison is case-sensitive and browsers
            # always send scheme and host in lower case. urlparse strips the
            # brackets from an IPv6 literal, so they have to be put back — a
            # browser sends "https://[::1]:8000", and the unbracketed form
            # would match nothing (and would slip past the loopback guard).
            display_host = f"[{host}]" if ":" in host else host.lower()
            netloc = display_host + (f":{port}" if port else "")
            normalised.append(f"{scheme}://{netloc}")

        origins = normalised

        if "*" in origins:
            # Starlette echoes the caller's Origin rather than sending a literal
            # "*" when credentials are enabled, so a wildcard hands any website
            # a credentialed read of this API.
            raise ValueError(
                "CORS_ORIGINS='*' is not allowed while credentials are enabled. "
                "List explicit origins, e.g. https://app.example.com"
            )

        if not origins:
            if self.looks_deployed:
                raise ValueError(
                    "CORS_ORIGINS is empty on what looks like a deployed "
                    "configuration. Refusing to fall back to "
                    f"{DEFAULT_CORS_ORIGINS} — that would reject all real "
                    "traffic while granting credentialed access to any page on "
                    "a victim's own localhost."
                )
            logger.warning(
                "CORS_ORIGINS is empty; falling back to %s. Set it explicitly.",
                DEFAULT_CORS_ORIGINS,
            )
            origins = [DEFAULT_CORS_ORIGINS]

        # Checked against the RESOLVED origins, not against emptiness. compose
        # ships CORS_ORIGINS=http://localhost:5173 as a default, so cors_origins
        # is never empty in a compose deployment and an emptiness-only check
        # could never fire — a TLS deploy that set JWT_SECRET and COOKIE_SECURE
        # but forgot CORS_ORIGINS would serve production with a loopback
        # allowlist: real traffic rejected silently, and any page on a victim's
        # own localhost holding credentialed access.
        if self.looks_deployed:
            loopback = sorted(o for o in origins if _is_loopback(o))
            if loopback:
                raise ValueError(
                    f"CORS_ORIGINS contains loopback origin(s) {loopback} on what "
                    "looks like a deployed configuration (a real JWT_SECRET or "
                    "COOKIE_SECURE is set). Set CORS_ORIGINS to the real "
                    "front-end origin."
                )

        self._allowed_origins = origins
        return self


def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
