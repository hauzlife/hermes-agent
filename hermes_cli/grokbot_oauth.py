"""Grok Bot PKCE for `hermes auth add grokbot`.

Cursor sand loginDeepControl poller. Tokens stay in ~/.grokbot/session.json
(mode 0600). The credential pool stores a listable copy so `hermes auth list`
shows grokbot as oauth, not a dummy yaml api_key.
"""

from __future__ import annotations

from typing import Any

from agent.grokbot import login as grokbot_login


def existing_session() -> dict[str, Any] | None:
    try:
        sess = grokbot_login._load()
    except Exception:
        return None
    if not isinstance(sess, dict):
        return None
    if not sess.get("accessToken") or not sess.get("refreshToken"):
        return None
    return sess


def run_pkce_login(*, open_browser: bool = True, timeout: float = 300.0) -> dict[str, Any]:
    return grokbot_login.login_session(open_browser=open_browser, timeout=timeout)


def session_label(sess: dict[str, Any]) -> str:
    auth_id = str(sess.get("authId") or "")
    email = str(sess.get("email") or "")
    if email:
        return email
    if auth_id:
        return auth_id.split("|", 1)[0] + "|…"
    return "grokbot-pkce"


def resolve_grokbot_runtime_credentials() -> dict:
    """Return api_key/base_url from ``~/.grokbot/session.json`` for runtime resolve.

    The ConnectRPC facade also re-reads the session file; this only satisfies
    ``resolve_runtime_provider`` so Hermes can select provider=grokbot without
    an API-key env var.
    """
    sess = existing_session()
    if sess is None:
        raise RuntimeError(
            "No Grok Bot session at ~/.grokbot/session.json. "
            "Import from Grok Bot Safe Storage or run: hermes auth add grokbot --type oauth"
        )
    return {
        "api_key": sess.get("accessToken") or "",
        "base_url": "https://api2.cursor.sh",
        "source": "grokbot-session",
        "auth_id": sess.get("authId") or "",
        "email": sess.get("email") or "",
    }
