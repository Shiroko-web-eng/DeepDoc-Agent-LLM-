from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant_id: str
    roles: tuple[str, ...] = ()


_principal: ContextVar[Principal] = ContextVar(
    "deepdoc_principal", default=Principal("local-user", "local", ("admin",))
)


def current_principal() -> Principal:
    return _principal.get()


def set_principal(principal: Principal) -> Token[Principal]:
    return _principal.set(principal)


def reset_principal(token: Token[Principal]) -> None:
    _principal.reset(token)
