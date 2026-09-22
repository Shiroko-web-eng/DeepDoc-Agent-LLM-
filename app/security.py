from __future__ import annotations

import re
from typing import Any

from app.config import Settings
from app.errors import AppError
from app.tenant import Principal


TENANT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class OIDCAuthenticator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: Any = None

    def authenticate(self, authorization: str | None) -> Principal:
        if not authorization or not authorization.startswith("Bearer "):
            raise AppError("AUTHENTICATION_REQUIRED", "需要 Bearer Token", 401)
        try:
            import jwt
        except ImportError as exc:
            raise RuntimeError("install the production extra to use OIDC") from exc
        if self._client is None:
            self._client = jwt.PyJWKClient(self.settings.oidc_jwks_url)
        token = authorization[7:].strip()
        try:
            key = self._client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token, key, algorithms=["RS256", "ES256"],
                audience=self.settings.oidc_audience,
                issuer=self.settings.oidc_issuer,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AppError("INVALID_ACCESS_TOKEN", "访问令牌无效", 401) from exc
        tenant = claims.get(self.settings.oidc_tenant_claim)
        if not isinstance(tenant, str) or not TENANT_PATTERN.fullmatch(tenant):
            raise AppError("INVALID_TENANT_CLAIM", "访问令牌缺少有效租户", 403)
        roles_value = claims.get(self.settings.oidc_roles_claim, [])
        if isinstance(roles_value, str):
            roles = (roles_value,)
        elif isinstance(roles_value, list) and all(isinstance(role, str) for role in roles_value):
            roles = tuple(roles_value)
        else:
            roles = ()
        return Principal(str(claims["sub"]), tenant, roles)
