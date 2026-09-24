"""Settings guards.

Each of these refusals was a finding in section 1.1's reviews; the tests exist
so the guards cannot quietly regress when Settings grows.
"""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from app.config import DEFAULT_CORS_ORIGINS, DEV_JWT_SECRET, Settings

REAL_SECRET = "0" * 64


def make(**overrides) -> Settings:
    # Every field the guards read is passed explicitly: init kwargs outrank the
    # environment, so the container's own env cannot make these tests flaky.
    base = {
        "database_url": "postgresql+asyncpg://u:p@db:5432/x",
        "jwt_secret": DEV_JWT_SECRET,
        "cookie_secure": False,
        "cors_origins": "https://app.example.com",
    }
    return Settings(**{**base, **overrides})


def test_dev_secret_with_secure_cookie_is_refused():
    with pytest.raises(ValidationError, match="development placeholder"):
        make(jwt_secret=DEV_JWT_SECRET, cookie_secure=True)


def test_real_secret_with_secure_cookie_is_fine():
    assert make(jwt_secret=REAL_SECRET, cookie_secure=True).cookie_secure


@pytest.mark.parametrize("value", ["*", "https://a.example,*", " * "])
def test_wildcard_origin_is_refused(value: str):
    with pytest.raises(ValidationError, match="not allowed"):
        make(cors_origins=value)


@pytest.mark.parametrize("value", ["", "   ", ",", ",,", " , , "])
def test_empty_origins_fall_back_on_a_dev_config(value: str, caplog):
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        settings = make(cors_origins=value)
    assert settings.allowed_origins == [DEFAULT_CORS_ORIGINS]
    # The fallback must be loud: silently allowing only a dev origin is the
    # failure mode this guard exists to make visible.
    assert "CORS_ORIGINS is empty" in caplog.text


@pytest.mark.parametrize(
    "overrides",
    [
        {"jwt_secret": REAL_SECRET},
        {"jwt_secret": REAL_SECRET, "cookie_secure": True},
    ],
    ids=["real-secret", "real-secret-and-secure-cookie"],
)
def test_empty_origins_are_refused_on_a_deployed_config(overrides):
    with pytest.raises(ValidationError, match="looks like a deployed"):
        make(cors_origins="", **overrides)


def test_explicit_origins_are_preserved_and_trimmed():
    settings = make(cors_origins=" https://a.example , https://b.example ")
    assert settings.allowed_origins == ["https://a.example", "https://b.example"]


def test_an_allowed_origins_env_var_cannot_break_startup(monkeypatch):
    # allowed_origins is derived, not configured. As a declared field it would
    # be read from the environment and JSON-parsed, so a stray ALLOWED_ORIGINS
    # would crash the app before it served a request.
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://evil.example")
    assert make().allowed_origins == ["https://app.example.com"]


@pytest.mark.parametrize(
    "value",
    [
        "app.example.com",
        "https://app.example.com/",
        "https://app.example.com/path",
        "ftp://app.example.com",
        "https://",
        "https://app.example.com?x=1",
    ],
)
def test_malformed_origin_is_refused(value: str):
    # Starlette matches allow_origins against the Origin header as an exact
    # string, and that header has no path and no trailing slash — so these
    # would match nothing and reject every request silently.
    with pytest.raises(ValidationError, match="not a bare origin"):
        make(cors_origins=value)


@pytest.mark.parametrize(
    "value",
    ["http://localhost:5173", "https://app.example.com", "http://127.0.0.1:8000"],
)
def test_well_formed_origins_are_accepted(value: str):
    assert make(cors_origins=value).allowed_origins == [value]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://App.Example.com", "https://app.example.com"),
        ("HTTPS://app.example.com", "https://app.example.com"),
        ("http://LOCALHOST:5173", "http://localhost:5173"),
    ],
)
def test_origins_are_lowercased(value: str, expected: str):
    # Starlette compares allow_origins to the Origin header with a plain `in`,
    # and browsers always send scheme and host lowercased.
    assert make(cors_origins=value).allowed_origins == [expected]


def test_origin_with_credentials_is_refused():
    # Userinfo never appears in an Origin header, so this could only ever be a
    # misconfiguration — and a silent one.
    with pytest.raises(ValidationError, match="not a bare origin"):
        make(cors_origins="https://user:pass@app.example.com")


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5173",
        "https://127.0.0.1",
        "http://app.localhost:3000",
        "https://app.example.com,http://localhost:5173",
    ],
)
def test_loopback_origins_are_refused_on_a_deployed_config(origin: str):
    """The guard must key off the resolved origins, not off emptiness.

    compose ships CORS_ORIGINS=http://localhost:5173 as a default, so the value
    is never empty in a compose deployment — an emptiness-only check could
    never fire, and a TLS deploy that forgot CORS_ORIGINS would serve
    production with a loopback allowlist.
    """
    with pytest.raises(ValidationError, match="loopback origin"):
        make(jwt_secret=REAL_SECRET, cookie_secure=True, cors_origins=origin)


def test_loopback_origins_are_fine_on_a_dev_config():
    assert make(cors_origins="http://localhost:5173").allowed_origins == [
        "http://localhost:5173"
    ]


@pytest.mark.parametrize(
    "value",
    [
        "https://*.example.com",
        "https://例え.jp",
    ],
)
def test_origins_starlette_can_never_match_are_refused(value: str):
    # Starlette compares exact strings, so a wildcard matches nothing; browsers
    # send punycode rather than unicode for an IDN host.
    with pytest.raises(ValidationError, match="not a bare origin"):
        make(cors_origins=value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://app.example.com:443", "https://app.example.com"),
        ("http://app.example.com:80", "http://app.example.com"),
        ("https://app.example.com:8443", "https://app.example.com:8443"),
    ],
)
def test_default_ports_are_stripped(value: str, expected: str):
    # A browser omits the default port from Origin, so keeping it would mean
    # the entry never matches.
    assert make(cors_origins=value).allowed_origins == [expected]


def test_short_jwt_secret_is_refused():
    # Refusing only the exact placeholder still accepted JWT_SECRET=x, which
    # signs 30-day refresh tokens and reads as a deliberate deployment.
    with pytest.raises(ValidationError):
        make(jwt_secret="x", cors_origins="https://app.example.com")


@pytest.mark.parametrize(
    "overrides",
    [
        {"access_token_ttl_minutes": 0},
        {"access_token_ttl_minutes": -15},
        {"refresh_token_ttl_days": 0},
        {"refresh_token_ttl_days": -1},
    ],
)
def test_non_positive_token_ttls_are_refused(overrides):
    with pytest.raises(ValidationError):
        make(**overrides)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://[::1]:8000", "https://[::1]:8000"),
        ("http://[2001:db8::1]", "http://[2001:db8::1]"),
        ("https://[2001:DB8::1]:443", "https://[2001:db8::1]"),
    ],
)
def test_ipv6_origins_keep_their_brackets(value: str, expected: str):
    # urlparse strips the brackets from .hostname, and a browser sends them —
    # so the unbracketed form matches nothing AND slips past the loopback guard.
    assert make(cors_origins=value).allowed_origins == [expected]


def test_ipv6_loopback_is_caught_on_a_deployed_config():
    with pytest.raises(ValidationError, match="loopback origin"):
        make(jwt_secret=REAL_SECRET, cors_origins="https://[::1]:8000")


@pytest.mark.parametrize("value", ["https://h:abc", "https://h:99999"])
def test_unparseable_port_is_refused_with_the_shape_error(value: str):
    # urlparse defers port validation to attribute access, so this would
    # otherwise surface as a raw "Port could not be cast to integer".
    with pytest.raises(ValidationError, match="not a bare origin"):
        make(cors_origins=value)


@pytest.mark.parametrize("value", ["http://:5173", "https://:443"])
def test_origin_with_no_host_is_refused(value: str):
    # urlparse gives a truthy netloc but hostname None, and "".isascii() is
    # True, so every shape clause missed this.
    with pytest.raises(ValidationError, match="not a bare origin"):
        make(cors_origins=value)


def test_explicit_port_zero_is_refused():
    # Port 0 parses cleanly, and the falsy check that strips default ports
    # would then drop it — widening "http://example.com:0" to the whole origin.
    with pytest.raises(ValidationError, match="not a bare origin"):
        make(cors_origins="http://example.com:0")


@pytest.mark.parametrize(
    "value",
    [
        "http://127.1:8000",
        "http://127.0.0.2:8000",
        "https://[0:0:0:0:0:0:0:1]:8000",
        "http://0.0.0.0:5173",
    ],
)
def test_non_canonical_loopback_spellings_are_caught(value: str):
    # A literal string set only caught the canonical spellings.
    with pytest.raises(ValidationError, match="loopback origin"):
        make(jwt_secret=REAL_SECRET, cors_origins=value)


def test_a_deployed_config_must_use_secure_cookies():
    """Symmetric with the dev-secret guard.

    That one refuses a placeholder secret WITH Secure cookies; this refuses a
    real secret WITHOUT them. The refresh cookie is a 30-day credential, and
    without Secure the browser attaches it to plain http:// requests, where
    anyone on the network path can take it.
    """
    with pytest.raises(ValidationError, match="COOKIE_SECURE must be on"):
        make(jwt_secret=REAL_SECRET, cookie_secure=False)


def test_a_deployed_config_with_secure_cookies_is_accepted():
    settings = make(jwt_secret=REAL_SECRET, cookie_secure=True)
    assert settings.cookie_secure is True


def test_a_dev_config_does_not_require_secure_cookies():
    # Local development is served over http, where Secure cookies would never
    # be sent at all.
    assert make(cookie_secure=False).allowed_origins == ["https://app.example.com"]
