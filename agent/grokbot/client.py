#!/usr/bin/env python3
"""Grok Bot chat client — same path the desktop app uses.

Route:  POST https://api2.cursor.sh/aiserver.v1.GrokBotService/SendGrokBotUserMessage
        POST .../GetGrokBotSendStatus
        POST .../ListGrokBotTranscriptEntries

NOT InferenceService/Stream (that path returns ERROR_NOT_LOGGED_IN for sand
session JWTs). Auth: ~/.grokbot/session.json (sand-secrets import). Never print tokens.

Ephemeral mode (kanban/oneshot): CreateGrokBotTemporalAgent →
SendGrokBotUserMessage → DeleteGrokBotAgent in a finally path. Never reuse
Artur's main agent 67f75405 for those jobs. Pin with GROKBOT_AGENT_ID or set
GROKBOT_EPHEMERAL_AGENT=0 to disable. machineId comes from session.json.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid

from agent.grokbot import login as _g

API_BASE = _g.API_BASE
SEND_URL = f"{API_BASE}/aiserver.v1.GrokBotService/SendGrokBotUserMessage"
STATUS_URL = f"{API_BASE}/aiserver.v1.GrokBotService/GetGrokBotSendStatus"
LIST_AGENTS_URL = f"{API_BASE}/aiserver.v1.GrokBotService/ListGrokBotAgents"
LIST_ENTRIES_URL = f"{API_BASE}/aiserver.v1.GrokBotService/ListGrokBotTranscriptEntries"
CREATE_URL = f"{API_BASE}/aiserver.v1.GrokBotService/CreateGrokBotTemporalAgent"
DELETE_URL = f"{API_BASE}/aiserver.v1.GrokBotService/DeleteGrokBotAgent"

DEFAULT_MODEL = "grok-4.6"
KNOWN_MODELS = ["grok-4.6", "grok-4.5", "default", "composer-2.5",
                "cursor-grok-4.6-medium", "cursor-grok-4.6-high", "grok-4.7"]

# Artur's main Grok Bot agent — NEVER the target for ephemeral kanban/msg jobs.
_MAIN_AGENT_UUID = "67f75405-9844-4fad-9b0f-b60f39412723"
_DEFAULT_AGENT_UUID = _MAIN_AGENT_UUID

# Last agentId used by infer() (for proofs / worker logs). Not a token.
last_agent_id: str = ""
last_ephemeral: bool = False

ROLE_USER = 1
ROLE_ASSISTANT = 2
ROLE_TOOL = 3
ROLE_SYSTEM = 4

_cached_agent_id: str | None = None


def _content_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        bits = []
        for p in content:
            if isinstance(p, dict):
                bits.append(p.get("text") or p.get("content") or "")
            else:
                bits.append(str(p))
        return "".join(bits)
    return str(content)


def openai_messages_to_history(msgs: list[dict]) -> tuple[str, list[tuple[int, str]]]:
    """Flatten OpenAI chat messages into (last_user_prompt, prior history).

    Kept for native.py compatibility. Tool results become extra user text.
    """
    history: list[tuple[int, str]] = []
    pending_user = ""
    for m in msgs or []:
        role = (m.get("role") or "user").lower()
        text = _content_text(m.get("content"))
        if role == "system":
            history.append((ROLE_USER, f"[SYSTEM]\n{text}"))
        elif role == "user":
            if pending_user:
                history.append((ROLE_USER, pending_user))
            pending_user = text
        elif role == "assistant":
            if pending_user:
                history.append((ROLE_USER, pending_user))
                pending_user = ""
            if text:
                history.append((ROLE_ASSISTANT, text))
        elif role == "tool":
            name = m.get("name") or "tool"
            pending_user = (
                (pending_user + "\n" if pending_user else "")
                + f"TOOL_RESULT {name}: {text}"
            )
    return pending_user, history


def _compose_send_text(prompt: str, history: list[tuple[int, str]] | None) -> str:
    """Text for SendGrokBotUserMessage — latest user turn only.

    Do NOT dump Hermes system prompts / local history into the remote agent
    transcript (that contaminated oneshot proofs). The Grok Bot agent already
    keeps its own conversation via ListGrokBotTranscriptEntries.
    """
    del history  # remote harness owns memory; Hermes history stays local
    prompt = (prompt or "").strip()
    return prompt or "hello"

def _json_rpc(url: str, payload: dict, token: str, timeout: int = 120) -> dict:
    body = json.dumps(payload).encode()
    h = _g.headers(token)
    h["Content-Type"] = "application/json"
    h["Accept"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        raw = resp.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        raise RuntimeError(f"HTTP {e.code}: {raw[:500]!r}") from None
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"non-JSON response from {url}: {raw[:300]!r}") from e



def _env_flag(name: str) -> str | None:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return None
    if raw in {"1", "true", "yes", "on"}:
        return "1"
    if raw in {"0", "false", "no", "off"}:
        return "0"
    return raw


def want_ephemeral(explicit: bool | None = None) -> bool:
    """Whether this infer() should mint a fresh agent and delete it after.

    Priority:
      1. explicit kwarg
      2. GROKBOT_AGENT_ID pin → False (reuse pinned)
      3. GROKBOT_EPHEMERAL_AGENT=0/1
      4. Hermes kanban worker (HERMES_KANBAN_TASK) or oneshot marker → True
      5. default False (interactive / main agent path)
    """
    if explicit is not None:
        return bool(explicit)
    if (os.environ.get("GROKBOT_AGENT_ID") or "").strip():
        return False
    flag = _env_flag("GROKBOT_EPHEMERAL_AGENT")
    if flag == "0":
        return False
    if flag == "1":
        return True
    if (os.environ.get("HERMES_KANBAN_TASK") or "").strip():
        return True
    if (os.environ.get("HERMES_ONESHOT") or "").strip() in {"1", "true", "yes"}:
        return True
    platform = (os.environ.get("HERMES_PLATFORM") or "").strip().lower()
    if platform in {"kanban", "oneshot", "cli-oneshot"}:
        return True
    return False


def create_agent(
    token: str | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    title: str | None = None,
    agent_id: str | None = None,
) -> str:
    """CreateGrokBotTemporalAgent → UUID agentId. Never prints tokens."""
    sess = _g._load() or {}
    tok = token or sess.get("accessToken")
    if not tok:
        raise RuntimeError("No Grok Bot session. Session should be at ~/.grokbot/session.json")
    aid = (agent_id or "").strip() or str(uuid.uuid4())
    if aid == _MAIN_AGENT_UUID:
        raise RuntimeError("Refusing to create with Artur main agent id 67f75405")
    short = aid.split("-")[0]
    payload = {
        "agentId": aid,
        "name": (name or f"hermes-ephemeral-{short}")[:80],
        "description": description or "Hermes ephemeral worker agent (auto-deleted after job)",
        "title": title or "Hermes Ephemeral",
        "avatarShape": "",
        "avatarColor": "",
        "harness": "TEMPORAL",
        "kickstartRequested": False,
        "introductionSuppressed": True,
        "createdAutomatically": True,
        "createIntent": "FRESH",
        "createCaller": "MINT",
    }
    resp = _json_rpc(CREATE_URL, payload, tok, timeout=45)
    agent = resp.get("agent") or {}
    out = agent.get("agentId") or agent.get("id") or aid
    if not isinstance(out, str) or "-" not in out:
        raise RuntimeError(f"CreateGrokBotTemporalAgent returned no agentId: {resp!r}"[:400])
    if out == _MAIN_AGENT_UUID:
        raise RuntimeError("Server returned main agent id; aborting ephemeral create")
    harness = str(agent.get("harness") or "").lower()
    if harness and harness not in {"temporal", "2"}:
        raise RuntimeError(f"Expected temporal harness, got {harness!r}")
    return out


def delete_agent(agent_id: str, token: str | None = None) -> None:
    """DeleteGrokBotAgent. Idempotent-ish: ignores already-gone agents."""
    aid = (agent_id or "").strip()
    if not aid:
        return
    if aid == _MAIN_AGENT_UUID:
        # Never delete Artur's main agent.
        return
    sess = _g._load() or {}
    tok = token or sess.get("accessToken")
    if not tok:
        return
    try:
        _json_rpc(DELETE_URL, {"id": aid}, tok, timeout=30)
    except RuntimeError as e:
        msg = str(e).lower()
        if "not found" in msg or "404" in msg or "already" in msg:
            return
        # best-effort cleanup — surface but do not mask the original job error
        raise


def resolve_agent_id(token: str | None = None) -> str:
    """UUID agentId for SendGrokBotUserMessage (not the numeric legacy id)."""
    global _cached_agent_id
    env = (os.environ.get("GROKBOT_AGENT_ID") or "").strip()
    if env:
        return env
    if _cached_agent_id:
        return _cached_agent_id
    tok = token or (_g._load() or {}).get("accessToken")
    if not tok:
        return _DEFAULT_AGENT_UUID
    try:
        data = _json_rpc(LIST_AGENTS_URL, {}, tok, timeout=30)
        agents = data.get("agents") or []
        for a in agents:
            aid = a.get("agentId") or a.get("legacyAgentId")
            if isinstance(aid, str) and "-" in aid:
                _cached_agent_id = aid
                return aid
            # numeric id alone was previously rejected as "agent not found"
        if agents:
            # last resort: prefer id if it looks like uuid
            for a in agents:
                i = str(a.get("id") or "")
                if "-" in i:
                    _cached_agent_id = i
                    return i
    except Exception:
        pass
    _cached_agent_id = _DEFAULT_AGENT_UUID
    return _DEFAULT_AGENT_UUID


def _decode_entry_body(body) -> dict | None:
    if body is None:
        return None
    if isinstance(body, dict):
        return body
    if isinstance(body, str):
        raw = body
        try:
            raw_b = base64.b64decode(body)
            return json.loads(raw_b)
        except Exception:
            try:
                return json.loads(body)
            except Exception:
                return None
    if isinstance(body, (bytes, bytearray)):
        try:
            return json.loads(bytes(body))
        except Exception:
            try:
                return json.loads(base64.b64decode(body))
            except Exception:
                return None
    return None


def _assistant_text_from_entry(decoded: dict) -> str | None:
    if not isinstance(decoded, dict):
        return None
    kind = decoded.get("kind") or ""
    # App assistant turns are entryKind send-message with message.content
    if kind in ("send-message", "message"):
        msg = decoded.get("message")
        if isinstance(msg, dict):
            c = msg.get("content")
            if isinstance(c, str) and c.strip():
                # user role messages also use kind message — skip those
                if decoded.get("role") == "user":
                    return None
                return c
        # some message entries put content at top level
        if kind == "message" and decoded.get("role") in (None, "assistant", "agent", "bot"):
            c = decoded.get("content")
            if isinstance(c, str) and c.strip() and decoded.get("role") != "user":
                return c
    return None


def _wait_for_reply(token: str, agent_id: str, message_id: str,
                    session_id: str | None, after_seq: int,
                    timeout_s: float = 180.0) -> str:
    """Poll send-status then transcript until an assistant send-message appears."""
    deadline = time.time() + timeout_s
    # 1) acceptance
    status_payload = {"agentId": agent_id, "messageId": message_id}
    if session_id:
        status_payload["sessionId"] = session_id
    accepted = False
    echo_id = None
    while time.time() < deadline:
        st = _json_rpc(STATUS_URL, status_payload, token, timeout=30)
        status = str(st.get("status") or "")
        if "ACCEPTED" in status or "DUPLICATE" in status:
            accepted = True
            echo_id = st.get("echoEntryId")
            break
        if "REJECT" in status or "FAIL" in status or st.get("rejectionCode"):
            raise RuntimeError(f"Send rejected: {st}")
        if "NOT_FOUND" in status or status.endswith("_UNSPECIFIED") or not status:
            time.sleep(0.4)
            continue
        time.sleep(0.4)
    if not accepted:
        # still try transcript — Temporal sometimes accepts before status flips
        pass

    # 2) transcript poll
    matched_user = False
    collected: list[str] = []
    while time.time() < deadline:
        listing = _json_rpc(
            LIST_ENTRIES_URL,
            {"agentId": agent_id, "limit": 40},
            token,
            timeout=30,
        )
        entries = listing.get("entries") or []
        # entries are newest-first
        for e in reversed(entries):
            try:
                seq = int(e.get("seq") or 0)
            except (TypeError, ValueError):
                seq = 0
            if seq <= after_seq:
                continue
            decoded = _decode_entry_body(e.get("body"))
            if not decoded:
                continue
            # detect our user echo via clientNonce == message_id
            if decoded.get("clientNonce") == message_id or (
                echo_id and e.get("entryId") == echo_id
            ):
                matched_user = True
                collected = []  # reset: only take assistant turns after our echo
                continue
            if not matched_user and after_seq > 0:
                # if we never saw echo but seq advanced, still accept assistant texts
                pass
            text = _assistant_text_from_entry(decoded)
            if text is not None:
                # Only keep assistant texts that appear after we matched the user
                # echo, or — if echo never shows — after after_seq.
                if matched_user or echo_id is None:
                    if matched_user or seq > after_seq:
                        collected.append(text)
        if collected and matched_user:
            return "\n\n".join(collected).strip()
        # If we have assistant text after our after_seq and enough time passed
        if collected and (time.time() + 2 > deadline or matched_user):
            return "\n\n".join(collected).strip()
        time.sleep(0.7)

    if collected:
        return "\n\n".join(collected).strip()
    raise RuntimeError(
        f"GrokBotService: no assistant reply within {timeout_s:.0f}s "
        f"(agent={agent_id} message_id={message_id} accepted={accepted})"
    )


def _latest_seq(token: str, agent_id: str) -> int:
    listing = _json_rpc(
        LIST_ENTRIES_URL, {"agentId": agent_id, "limit": 5}, token, timeout=30
    )
    seqs = []
    for e in listing.get("entries") or []:
        try:
            seqs.append(int(e.get("seq") or 0))
        except (TypeError, ValueError):
            pass
    return max(seqs) if seqs else 0


def infer(prompt: str, model: str = DEFAULT_MODEL, token: str | None = None,
          conversation_id: str | None = None, history=None,
          tools: list[dict] | None = None, on_chunk=None,
          agent_id: str | None = None, ephemeral: bool | None = None) -> dict:
    """Send via GrokBotService and wait for the harness reply.

    ``tools`` are ignored: the temporal agent harness owns its own tool surface.
    When ephemeral (see ``want_ephemeral``), mints a fresh agent, sends to THAT
    agentId (never ``_MAIN_AGENT_UUID``), and deletes it in a finally path.
    Returns {text, tool_calls, model, agent_id, ephemeral}.
    """
    global last_agent_id, last_ephemeral
    del tools  # harness path — Hermes tools are not mapped onto SendGrokBotUserMessage
    sess = _g._load() or {}
    tok = token or sess.get("accessToken")
    if not tok:
        raise RuntimeError("No Grok Bot session. Session should be at ~/.grokbot/session.json")

    use_ephemeral = want_ephemeral(ephemeral)
    created_id: str | None = None
    target_id = (agent_id or "").strip() or None

    try:
        if use_ephemeral:
            if target_id == _MAIN_AGENT_UUID:
                target_id = None
            created_id = create_agent(tok, agent_id=target_id)
            target_id = created_id
        else:
            target_id = target_id or resolve_agent_id(tok)

        last_agent_id = target_id
        last_ephemeral = bool(created_id)

        machine = sess.get("machineId") or _g.machine_id()
        message_id = str(uuid.uuid4())
        session_id = (conversation_id or os.environ.get("GROKBOT_SESSION_ID") or "").strip() or None
        text = _compose_send_text(prompt, history)

        after_seq = _latest_seq(tok, target_id)

        payload = {
            "agentId": target_id,
            "messageId": message_id,
            "text": text,
            "sentAtMs": str(int(time.time() * 1000)),
            "isFork": (os.environ.get("GROKBOT_IS_FORK", "").strip().lower() in {"1", "true", "yes"}),
        }
        if machine:
            payload["machineId"] = machine
        if session_id:
            payload["sessionId"] = session_id

        def _send(t: str) -> dict:
            return _json_rpc(SEND_URL, payload, t, timeout=60)

        try:
            resp = _send(tok)
        except RuntimeError as e:
            if token is None and str(e).startswith("HTTP 401"):
                sess = _g.refresh_session()
                tok = sess["accessToken"]
                resp = _send(tok)
            else:
                raise

        delivery = str(resp.get("delivery") or "")
        if resp.get("refusal"):
            raise RuntimeError(f"GrokBotService refused: {resp.get('refusal')}")
        if "REFUSED" in delivery:
            raise RuntimeError(f"GrokBotService delivery refused: {resp}")
        if not (resp.get("dispatched") or "ACCEPTED" in delivery or "DUPLICATE" in delivery):
            # still attempt wait — some modes omit dispatched
            if "UNSPECIFIED" in delivery or not delivery:
                raise RuntimeError(f"GrokBotService unexpected send response: {resp}")

        result_text = _wait_for_reply(
            tok, target_id, message_id, session_id, after_seq,
            timeout_s=float(os.environ.get("GROKBOT_REPLY_TIMEOUT", "180")),
        )
        if on_chunk and result_text:
            on_chunk(result_text)
        routed = model or DEFAULT_MODEL
        stream.last_model = routed
        return {
            "text": result_text,
            "tool_calls": [],
            "model": routed,
            "agent_id": target_id,
            "ephemeral": bool(created_id),
        }
    finally:
        if created_id:
            try:
                delete_agent(created_id, tok)
            except Exception:
                # Cleanup must not hide the job result/error; log-ish via stderr.
                import sys
                print(
                    f"[grokbot] warning: failed to delete ephemeral agent {created_id}",
                    file=sys.stderr,
                )


def stream(prompt: str, model: str = DEFAULT_MODEL, token: str | None = None,
           conversation_id: str | None = None, history=None,
           on_chunk=None, tools=None, agent_id: str | None = None,
           ephemeral: bool | None = None) -> str:
    return infer(
        prompt, model, token, conversation_id, history, tools, on_chunk,
        agent_id=agent_id, ephemeral=ephemeral,
    )["text"]


stream.last_model = ""
