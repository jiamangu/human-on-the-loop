#!/usr/bin/env python3
"""
Agent Team -> Slack Thread notifier.
Called by Claude Code hooks (stdin = JSON, env = SLACK_BOT_TOKEN / SLACK_CHANNEL_ID).
State file keyed on cwd so Lead + all Teammates share one Slack thread.
"""

import fcntl
import hashlib
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SLACK_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "")
STATE_DIR = Path.home() / ".claude" / "agent-team-slack-state"
MAX_RETRIES = 3
# Built-in agent types that should display as "C.C." (not team members)
BUILTIN_AGENT_TYPES = {
    "general-purpose", "Explore", "Plan",
    "statusline-setup", "claude-code-guide",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_str() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%H:%M:%S")


def _cwd_hash(cwd: str) -> str:
    return hashlib.sha256(cwd.encode()).hexdigest()[:12]


def _project_name(cwd: str) -> str:
    return os.path.basename(cwd) or cwd


def _truncate(text: str, limit: int = 500) -> str:
    if not text:
        return ""
    s = str(text)
    return s[:limit] + "..." if len(s) > limit else s


# ---------------------------------------------------------------------------
# Slack API (with retry)
# ---------------------------------------------------------------------------

def _slack_api(method: str, payload: dict) -> dict:
    """Call Slack Web API. Returns parsed JSON response."""
    url = f"https://slack.com/api/{method}"
    data = json.dumps(payload).encode()
    headers = {
        "Authorization": f"Bearer {SLACK_TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                body = json.loads(resp.read().decode())
                if body.get("error") == "ratelimited":
                    retry_after = int(resp.headers.get("Retry-After", attempt + 1))
                    if attempt < MAX_RETRIES:
                        print(f"[slack] rate-limited, retry in {retry_after}s", file=sys.stderr)
                        time.sleep(retry_after)
                        continue
                return body
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < MAX_RETRIES:
                retry_after = int(exc.headers.get("Retry-After", attempt + 1))
                print(f"[slack] 429, retry in {retry_after}s", file=sys.stderr)
                time.sleep(retry_after)
                continue
            print(f"[slack] HTTP {exc.code}: {exc.read().decode()[:300]}", file=sys.stderr)
            return {}
        except Exception as exc:
            print(f"[slack] error: {exc}", file=sys.stderr)
            if attempt < MAX_RETRIES:
                time.sleep(attempt + 1)
                continue
            return {}
    return {}


def _post_message(text: str, thread_ts: str | None = None) -> dict:
    payload: dict = {"channel": CHANNEL_ID, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    return _slack_api("chat.postMessage", payload)


def _update_message(ts: str, text: str) -> dict:
    return _slack_api("chat.update", {"channel": CHANNEL_ID, "ts": ts, "text": text})


# ---------------------------------------------------------------------------
# State file (file-locked)
# ---------------------------------------------------------------------------

def _state_path(cwd: str) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / f"{_cwd_hash(cwd)}.json"


def _read_state(cwd: str) -> dict:
    """Read state inside a file lock. Returns (state, lock_fd)."""
    path = _state_path(cwd)
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _locked_read_modify_write(cwd: str, modifier):
    """
    Acquire flock, read state, call modifier(state) -> (state, pre_lock_result),
    write state, release lock, return pre_lock_result.
    modifier should NOT call Slack API (keep lock duration short).
    """
    path = _state_path(cwd)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        state = {}
        if path.exists():
            try:
                state = json.loads(path.read_text())
            except json.JSONDecodeError:
                state = {}
        state, result = modifier(state)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        return state, result
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

def handle_session_start(hook: dict):
    """Only record session_id. Thread creation is deferred to first PostToolUse(SendMessage)."""
    cwd = hook.get("cwd", os.getcwd())
    session_id = hook.get("session_id", "")

    def init(state):
        state["session_id"] = session_id
        state["members"] = []
        state["agent_id_map"] = {}
        state["task_count"] = 0
        state["start_time"] = time.time()
        state.pop("thread_ts", None)
        state.pop("top_msg_ts", None)
        state.pop("top_msg_text", None)
        state.pop("topic", None)
        return state, None

    _locked_read_modify_write(cwd, init)


def _ensure_thread(cwd: str) -> str | None:
    """Create Slack thread if not yet created. Returns thread_ts or None."""
    state = _read_state(cwd)
    if state.get("thread_ts"):
        return state["thread_ts"]

    project = _project_name(cwd)
    ts_str = _now_str()
    topic = state.get("topic", "")
    if topic:
        text = f"\U0001f916 TeamRoom\uff1a{project} | {topic} | {ts_str}"
    else:
        text = f"\U0001f916 TeamRoom\uff1a{project} | {ts_str}"

    resp = _post_message(text)
    thread_ts = resp.get("ts")
    if not thread_ts:
        print("[slack] _ensure_thread: failed to get thread_ts", file=sys.stderr)
        return None

    def set_thread(state):
        if state.get("thread_ts"):
            return state, state["thread_ts"]
        state["thread_ts"] = thread_ts
        state["top_msg_ts"] = thread_ts
        state["top_msg_text"] = text
        return state, thread_ts

    _, result_ts = _locked_read_modify_write(cwd, set_thread)
    return result_ts


def _check_session(hook: dict, state: dict) -> bool:
    """Return True if hook belongs to the current session (or session_id is unavailable)."""
    hook_sid = hook.get("session_id", "")
    state_sid = state.get("session_id", "")
    if hook_sid and state_sid and hook_sid != state_sid:
        return False
    return True


def handle_subagent_start(hook: dict):
    cwd = hook.get("cwd", os.getcwd())
    agent_type = hook.get("agent_type") or ""
    agent_id = hook.get("agent_id") or ""
    is_builtin = not agent_type or agent_type in BUILTIN_AGENT_TYPES
    agent = "C.C." if is_builtin else agent_type

    # Only team-member agents (non-builtin) can trigger thread creation.
    # Builtin agents (Explore, general-purpose, etc.) are ignored unless thread already exists.
    state = _read_state(cwd)
    if not _check_session(hook, state):
        return
    if is_builtin and not state.get("thread_ts"):
        # No thread yet and this is a builtin agent — skip silently
        if agent_id:
            def store_id(state):
                id_map = state.get("agent_id_map", {})
                id_map[agent_id] = agent
                state["agent_id_map"] = id_map
                return state, None
            _locked_read_modify_write(cwd, store_id)
        return

    # Team member detected (or thread already exists) — ensure thread is created
    if not state.get("thread_ts"):
        _ensure_thread(cwd)

    def check_and_add(state):
        if not _check_session(hook, state):
            return state, None
        # Store agent_id -> readable name mapping
        if agent_id:
            id_map = state.get("agent_id_map", {})
            id_map[agent_id] = agent
            state["agent_id_map"] = id_map
        members = state.get("members", [])
        if agent in members:
            return state, None  # already seen
        members.append(agent)
        state["members"] = members
        return state, state.get("thread_ts")

    state, thread_ts = _locked_read_modify_write(cwd, check_and_add)
    if thread_ts:
        members_str = "\u3001".join(state.get("members", []))
        _post_message(f"\U0001f464 {agent} \u52a0\u5165\u8ba8\u8bba\uff08\u5f53\u524d\u6210\u5458\uff1a{members_str}\uff09", thread_ts)


def handle_subagent_stop(hook: dict):
    cwd = hook.get("cwd", os.getcwd())
    last_msg = hook.get("last_assistant_message", "")

    state = _read_state(cwd)
    thread_ts = state.get("thread_ts")
    if not thread_ts:
        return
    if not _check_session(hook, state):
        return

    # Resolve agent name: prefer agent_type, then look up id_map, fallback to "C.C."
    agent_type = hook.get("agent_type") or ""
    agent_id = hook.get("agent_id") or ""
    if agent_type and agent_type != "unknown":
        agent = agent_type
    else:
        id_map = state.get("agent_id_map", {})
        agent = id_map.get(agent_id, "C.C.")

    ts = _now_str()
    text = f"\u2705 {agent} \u5b8c\u6210 \u2503 {ts}"
    if last_msg:
        text += f"\n{_truncate(last_msg)}"
    _post_message(text, thread_ts)


def handle_post_tool_use(hook: dict):
    """Capture SendMessage content — the actual discussion between team members."""
    tool_name = hook.get("tool_name", "")
    if tool_name != "SendMessage":
        return

    cwd = hook.get("cwd", os.getcwd())
    state = _read_state(cwd)
    if not _check_session(hook, state):
        return

    # Auto-create thread on first SendMessage if not yet created
    thread_ts = state.get("thread_ts")
    if not thread_ts:
        thread_ts = _ensure_thread(cwd)
        if not thread_ts:
            return

    tool_input = hook.get("tool_input", {})
    tool_response = hook.get("tool_response", "")
    recipient = tool_input.get("to", "unknown")
    message = tool_input.get("message", "")

    # Skip structured protocol messages (shutdown, plan approval, etc.)
    if isinstance(message, dict):
        return

    # Resolve sender: use agent_type from hook, fall back to agent_id_map
    agent_type = hook.get("agent_type") or ""
    agent_id = hook.get("agent_id") or ""
    if agent_type and agent_type not in BUILTIN_AGENT_TYPES:
        sender = agent_type
    else:
        id_map = state.get("agent_id_map", {})
        sender = id_map.get(agent_id, "C.C.")

    # Increment round counter
    def inc_round(state):
        state["round"] = state.get("round", 0) + 1
        return state, state["round"]

    _, round_num = _locked_read_modify_write(cwd, inc_round)

    ts = _now_str()
    text = f"\U0001f4ac {sender} \u2192 {recipient} \u2503 R{round_num} {ts}\n{_truncate(message, 800)}"
    _post_message(text, thread_ts)


def handle_task_completed(hook: dict):
    cwd = hook.get("cwd", os.getcwd())
    desc = hook.get("task_subject") or hook.get("task_description") or "unnamed"

    def inc_task(state):
        if not _check_session(hook, state):
            return state, None
        state["task_count"] = state.get("task_count", 0) + 1
        return state, state.get("thread_ts")

    state, thread_ts = _locked_read_modify_write(cwd, inc_task)
    if thread_ts:
        _post_message(f"\u2705 \u4efb\u52a1\u5b8c\u6210\uff1a{_truncate(desc, 300)}", thread_ts)


def handle_user_prompt_submit(hook: dict):
    cwd = hook.get("cwd", os.getcwd())

    state = _read_state(cwd)
    if state.get("topic"):
        return
    if not _check_session(hook, state):
        return

    prompt = hook.get("prompt", "")
    if not prompt:
        return

    # Save topic to state immediately (thread may not exist yet)
    topic = prompt.replace("\n", " ").replace("\r", " ").strip()
    topic = topic[:40] + ("..." if len(topic) > 40 else "")

    def set_topic(state):
        if state.get("topic"):
            return state, None
        state["topic"] = topic
        # If thread already exists, update top message
        top_ts = state.get("top_msg_ts")
        old_text = state.get("top_msg_text", "")
        if top_ts and old_text:
            parts = old_text.rsplit(" | ", 1)
            if len(parts) == 2:
                new_text = f"{parts[0]} | {topic} | {parts[1]}"
            else:
                new_text = f"{old_text} | {topic}"
            state["top_msg_text"] = new_text
            return state, (top_ts, new_text)
        return state, None

    state, result = _locked_read_modify_write(cwd, set_topic)
    if result:
        top_ts, new_text = result
        _update_message(top_ts, new_text)


def handle_session_end(hook: dict):
    cwd = hook.get("cwd", os.getcwd())
    state = _read_state(cwd)
    thread_ts = state.get("thread_ts")
    if not thread_ts:
        return
    if not _check_session(hook, state):
        return

    members = state.get("members", [])
    members_str = "\u3001".join(members) if members else "\u65e0"
    rounds = state.get("round", 0)
    tasks = state.get("task_count", 0)
    start = state.get("start_time", time.time())
    duration = int((time.time() - start) / 60)

    summary = (
        f"\U0001f3c1 \u56e2\u961f\u4f1a\u8bdd\u7ed3\u675f\n"
        f"\U0001f465 \u53c2\u4e0e\u6210\u5458\uff1a{members_str}\n"
        f"\U0001f4ac \u5bf9\u8bdd\u8f6e\u6b21\uff1a{rounds} \u8f6e\n"
        f"\u2705 \u5b8c\u6210\u4efb\u52a1\uff1a{tasks} \u4e2a\n"
        f"\u23f1\ufe0f \u6301\u7eed\u65f6\u95f4\uff1a{duration} \u5206\u949f"
    )
    _post_message(summary, thread_ts)

    # Update top-level message with checkmark
    top_text = state.get("top_msg_text", "")
    top_ts = state.get("top_msg_ts", thread_ts)
    if top_text and top_ts:
        _update_message(top_ts, f"{top_text} \u2705")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

HANDLERS = {
    "SessionStart": handle_session_start,
    "UserPromptSubmit": handle_user_prompt_submit,
    "SubagentStart": handle_subagent_start,
    "SubagentStop": handle_subagent_stop,
    "PostToolUse": handle_post_tool_use,
    "TaskCompleted": handle_task_completed,
    "SessionEnd": handle_session_end,
}


def main():
    if not SLACK_TOKEN or not CHANNEL_ID:
        print("[slack] SLACK_BOT_TOKEN or SLACK_CHANNEL_ID not set, skipping", file=sys.stderr)
        return

    raw = sys.stdin.read().strip()
    if not raw:
        return

    try:
        hook = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[slack] invalid JSON: {exc}", file=sys.stderr)
        return

    event = hook.get("hook_event_name", "")

    # Debug logging
    debug_path = STATE_DIR / "debug.log"
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(debug_path, "a") as f:
        ts = _now_str()
        f.write(f"[{ts}] event={event}")
        if event == "SubagentStart":
            f.write(f" agent_type={hook.get('agent_type')!r} agent_id={hook.get('agent_id')!r}")
        elif event == "PostToolUse":
            f.write(f" tool_name={hook.get('tool_name')!r}")
        f.write(f"\n")

    handler = HANDLERS.get(event)
    if handler:
        try:
            handler(hook)
        except Exception as exc:
            print(f"[slack] {event} handler error: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
