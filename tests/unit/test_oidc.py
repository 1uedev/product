"""OIDC relying party: every claim that matters is verified; backchannel and public issuer are kept apart."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from decision_evidence.config import Settings
from decision_evidence.identity.oidc import OidcClient, OidcError, new_pkce_pair, safe_redirect_path

PUBLIC = "https://id.example.org/auth/realms/x"
INTERNAL = "http://keycloak:8080/auth/realms/x"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = json.loads(RSAAlgorithm.to_jwk(KEY.public_key()))
JWK.update({"kid": "k1", "use": "sig", "alg": "RS256"})
seen_urls: list[str] = []


def handler(request: httpx.Request) -> httpx.Response:
    seen_urls.append(str(request.url))
    if request.url.path.endswith("/.well-known/openid-configuration"):
        return httpx.Response(200, json={
            "issuer": PUBLIC, "authorization_endpoint": f"{PUBLIC}/protocol/openid-connect/auth",
            "token_endpoint": f"{PUBLIC}/protocol/openid-connect/token", "jwks_uri": f"{PUBLIC}/protocol/openid-connect/certs",
            "end_session_endpoint": f"{PUBLIC}/protocol/openid-connect/logout"})
    if request.url.path.endswith("/certs"):
        return httpx.Response(200, json={"keys": [JWK]})
    if request.url.path.endswith("/token"):
        return httpx.Response(200, json={"id_token": "x"})
    return httpx.Response(404)


def client() -> OidcClient:
    s = Settings(oidc_issuer=PUBLIC, oidc_internal_issuer=INTERNAL, oidc_client_id="web", oidc_client_secret="s", public_origin="https://app.example.org")
    return OidcClient(s, httpx.Client(transport=httpx.MockTransport(handler)))


def token(key: Any = KEY, alg: str = "RS256", **over: Any) -> str:
    now = int(time.time())
    claims = {"iss": PUBLIC, "aud": "web", "sub": "user-1", "iat": now, "exp": now + 300, "nonce": "n-1", "email": "a@x.org", "email_verified": True, "name": "A"}
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": "k1"})


def test_valid_token_is_accepted_and_backchannel_is_used_for_jwks() -> None:
    seen_urls.clear()
    c = client()
    claims = c.verify_id_token(token(), nonce="n-1")
    assert claims.subject == "user-1" and claims.issuer == PUBLIC and claims.email == "a@x.org" and claims.email_verified
    assert any(u.startswith(INTERNAL) for u in seen_urls)                       # discovery + JWKS via the internal address
    assert not any(u.startswith("https://id.example.org") for u in seen_urls)   # never via the public address (docker networking)


@pytest.mark.parametrize("over", [
    {"iss": "https://evil.example.org/realms/x"}, {"aud": "other-client"}, {"exp": int(time.time()) - 600}, {"nonce": "other"}, {"sub": None}, {"exp": None},
])
def test_invalid_claims_are_rejected(over: dict) -> None:
    with pytest.raises(OidcError):
        client().verify_id_token(token(**over), nonce="n-1")


def test_signature_by_another_key_and_algorithm_confusion_are_rejected() -> None:
    with pytest.raises(OidcError):
        client().verify_id_token(token(key=OTHER_KEY), nonce="n-1")
    forged_hs = jwt.encode({"iss": PUBLIC, "aud": "web", "sub": "u", "iat": 1, "exp": int(time.time()) + 100, "nonce": "n-1"}, "secret-secret-secret-secret-1234", algorithm="HS256", headers={"kid": "k1"})
    with pytest.raises(OidcError):
        client().verify_id_token(forged_hs, nonce="n-1")
    none_alg = base64.urlsafe_b64encode(b'{"alg":"none","kid":"k1"}').rstrip(b"=").decode() + "." + base64.urlsafe_b64encode(b'{"iss":"' + PUBLIC.encode() + b'","aud":"web","sub":"u","nonce":"n-1","exp":9999999999,"iat":1}').rstrip(b"=").decode() + "."
    with pytest.raises(OidcError):
        client().verify_id_token(none_alg, nonce="n-1")


def test_unknown_key_id_is_rejected() -> None:
    t = jwt.encode({"iss": PUBLIC, "aud": "web", "sub": "u", "iat": 1, "exp": int(time.time()) + 100, "nonce": "n-1"}, KEY, algorithm="RS256", headers={"kid": "unknown"})
    with pytest.raises(OidcError):
        client().verify_id_token(t, nonce="n-1")


def test_discovery_with_unexpected_issuer_is_refused() -> None:
    def evil(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"issuer": "https://evil.example.org/realms/x"})
    s = Settings(oidc_issuer=PUBLIC, oidc_internal_issuer=INTERNAL, oidc_client_id="web", public_origin="https://app.example.org")
    with pytest.raises(OidcError):
        OidcClient(s, httpx.Client(transport=httpx.MockTransport(evil))).discovery()


def test_authorization_url_has_pkce_state_nonce_and_the_fixed_redirect_uri() -> None:
    c = client()
    verifier, challenge = new_pkce_pair()
    assert challenge == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode() and len(verifier) >= 43
    url = c.authorization_url(state="st", nonce="no", code_challenge=challenge)
    assert url.startswith(f"{PUBLIC}/protocol/openid-connect/auth?")
    for part in ("response_type=code", "code_challenge_method=S256", "state=st", "nonce=no", "client_id=web",
                 "redirect_uri=https%3A%2F%2Fapp.example.org%2Fapi%2Fv1%2Fauth%2Fcallback"):
        assert part in url
    assert c.end_session_url("tok").startswith(f"{PUBLIC}/protocol/openid-connect/logout?")


def test_code_exchange_uses_the_internal_token_endpoint() -> None:
    seen_urls.clear()
    assert client().exchange_code("code", "verifier") == {"id_token": "x"}
    assert any(u == f"{INTERNAL}/protocol/openid-connect/token" for u in seen_urls)


@pytest.mark.parametrize(("value", "expected"), [
    ("/w/abc/problems?x=1", "/w/abc/problems?x=1"), ("/", "/"), (None, "/"), ("", "/"),
    ("//evil.example.org", "/"), ("https://evil.example.org", "/"), ("/\\evil.example.org", "/"), ("javascript:alert(1)", "/"), ("/ok\r\nSet-Cookie: x=1", "/"),
])
def test_post_login_redirect_cannot_leave_the_origin(value: str | None, expected: str) -> None:
    assert safe_redirect_path(value) == expected
