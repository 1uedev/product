"""OpenID Connect relying party (authorization code flow with PKCE, state and nonce).

Issuer, signature, audience, expiry and nonce are always verified. ``oidc_issuer`` is the public issuer that appears in
tokens; backchannel calls (token endpoint, JWKS) use ``oidc_internal_issuer`` so Docker networking works without ever
relaxing the issuer check.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWK

from decision_evidence.config import Settings, get_settings

ALLOWED_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]


class OidcError(Exception):
    """Authentication failed. The message is safe to show, details stay in logs."""


@dataclass(frozen=True)
class IdTokenClaims:
    issuer: str
    subject: str
    email: str | None
    email_verified: bool
    name: str | None
    nonce: str | None
    raw: dict[str, Any]


def new_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def safe_redirect_path(value: str | None) -> str:
    """Only same-origin absolute paths are allowed as post-login target (no open redirect)."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value or "\n" in value or "\r" in value:
        return "/"
    return value


class OidcClient:
    def __init__(self, settings: Settings | None = None, http: httpx.Client | None = None) -> None:
        self.s = settings or get_settings()
        self.http = http or httpx.Client(timeout=10.0)
        self._lock = threading.Lock()
        self._discovery: dict[str, Any] | None = None
        self._discovery_at = 0.0
        self._jwks: dict[str, Any] = {}
        self._jwks_at = 0.0

    # ---------------------------------------------------------------- discovery
    @property
    def redirect_uri(self) -> str:
        return f"{self.s.public_origin}/api/v1/auth/callback"

    def _to_backchannel(self, url: str) -> str:
        public, internal = self.s.oidc_issuer, self.s.oidc_backchannel_base
        return internal + url[len(public):] if url.startswith(public) else url

    def discovery(self) -> dict[str, Any]:
        with self._lock:
            if self._discovery and time.time() - self._discovery_at < 600:
                return self._discovery
            url = f"{self.s.oidc_backchannel_base}/.well-known/openid-configuration"
            try:
                doc = self.http.get(url).raise_for_status().json()
            except (httpx.HTTPError, ValueError) as exc:
                raise OidcError("Der Anmeldedienst ist nicht erreichbar.") from exc
            if doc.get("issuer") != self.s.oidc_issuer:
                raise OidcError("Der Anmeldedienst meldet einen unerwarteten Aussteller.")
            self._discovery, self._discovery_at = doc, time.time()
            return doc

    def _signing_key(self, kid: str | None) -> Any:
        with self._lock:
            fresh = time.time() - self._jwks_at < 600
            keys = self._jwks if fresh else {}
        if kid not in keys:
            uri = self._to_backchannel(self.discovery()["jwks_uri"])
            try:
                data = self.http.get(uri).raise_for_status().json()
            except (httpx.HTTPError, ValueError) as exc:
                raise OidcError("Der Anmeldedienst ist nicht erreichbar.") from exc
            keys = {k.get("kid"): k for k in data.get("keys", []) if k.get("use", "sig") == "sig"}
            with self._lock:
                self._jwks, self._jwks_at = keys, time.time()
        if kid not in keys:
            raise OidcError("Unbekannter Signaturschlüssel.")
        return PyJWK(keys[kid]).key

    # ---------------------------------------------------------------- flow
    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.s.oidc_client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.s.oidc_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.discovery()['authorization_endpoint']}?{urlencode(params)}"

    def exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        secret = self.s.oidc_client_secret.get_secret_value() if self.s.oidc_client_secret else None
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.s.oidc_client_id,
            "code_verifier": code_verifier,
        }
        if secret:
            data["client_secret"] = secret
        try:
            resp = self.http.post(self._to_backchannel(self.discovery()["token_endpoint"]), data=data)
        except httpx.HTTPError as exc:
            raise OidcError("Der Anmeldedienst ist nicht erreichbar.") from exc
        if resp.status_code != 200:
            raise OidcError("Der Anmeldecode wurde abgelehnt.")
        return resp.json()

    def verify_id_token(self, token: str, *, nonce: str) -> IdTokenClaims:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in ALLOWED_ALGORITHMS:
                raise OidcError("Nicht erlaubter Signaturalgorithmus.")
            claims = jwt.decode(
                token,
                self._signing_key(header.get("kid")),
                algorithms=ALLOWED_ALGORITHMS,
                audience=self.s.oidc_client_id,
                issuer=self.s.oidc_issuer,
                leeway=self.s.oidc_leeway_seconds,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise OidcError("Das Anmeldetoken ist ungültig.") from exc
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise OidcError("Die Anmeldeantwort passt nicht zur Anfrage.")
        return IdTokenClaims(
            issuer=claims["iss"], subject=str(claims["sub"]), email=claims.get("email"),
            email_verified=bool(claims.get("email_verified", False)),
            name=claims.get("name") or claims.get("preferred_username"), nonce=claims.get("nonce"), raw=claims,
        )

    def end_session_url(self, id_token_hint: str | None) -> str | None:
        endpoint = self.discovery().get("end_session_endpoint")
        if not endpoint:
            return None
        params = {"post_logout_redirect_uri": f"{self.s.public_origin}/", "client_id": self.s.oidc_client_id}
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        return f"{endpoint}?{urlencode(params)}"
