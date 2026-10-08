#!/usr/bin/env python3
"""
Two-way Telegram chat with buffet bot.

Long-polls the existing bot (TELEGRAM_BOT_TOKEN) for messages. Each message
from Andre's chat (TELEGRAM_CHAT_ID) goes to `claude -p` in this repo, which
gets the same tool grants as a scheduled cycle and chat/CHAT.md as extra system
prompt. The answer comes back as the reply. Messages from any other chat are
dropped without a reply, so a stranger who finds the bot gets nothing.

The conversation carries over between messages (--resume). /new starts a
fresh one, and so does six hours of silence.

Run as systemd/buffet-bot-chat.service. Nothing else may call getUpdates on
this bot token: Telegram hands each update to only one poller.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAT_DIR = os.path.join(ROOT, "chat")
STATE_PATH = os.path.join(CHAT_DIR, "state.json")
LOG_DIR = os.path.join(ROOT, "logs", "chat")
SYSTEM_PROMPT = os.path.join(CHAT_DIR, "CHAT.md")

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"].strip()
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"].strip())
API = f"https://api.telegram.org/bot{TOKEN}"
ET = ZoneInfo("America/New_York")

SESSION_IDLE_RESET_S = 6 * 3600
CLAUDE_TIMEOUT_S = 20 * 60
TG_MAX = 4000  # Telegram caps a message at 4096 characters

# Same grants as run-cycle.sh, minus mcp__telegram__*: the listener sends the
# reply itself, and a second sender would just produce duplicate messages.
ALLOWED_TOOLS = ",".join([
    "Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch",
    "Agent", "ToolSearch", "mcp__tradingview__*", "mcp__schwab__*",
    "mcp__claude_ai_Webull__*", "mcp__webull__*",
    "mcp__claude_ai_Google_Drive__*", "mcp__claude_ai_Google_Calendar__*",
    "mcp__claude_ai_Alpha_Vantage_MCP_Server__*",
])

http = httpx.Client(timeout=70)


def log(msg):
    print(f"{datetime.now(ET):%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def tg(method, **params):
    r = http.post(f"{API}/{method}", data=params)
    body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"telegram {method}: {body.get('description')}")
    return body["result"]


def send_text(text):
    text = text.strip() or "(empty reply)"
    while text:
        chunk, text = text[:TG_MAX], text[TG_MAX:]
        if text:  # split on a paragraph or line break when there is one
            cut = max(chunk.rfind("\n\n"), chunk.rfind("\n"))
            if cut > TG_MAX // 2:
                chunk, text = chunk[:cut], chunk[cut:] + text
        # Plain text on purpose: the model's markdown would trip Telegram's parser.
        tg("sendMessage", chat_id=CHAT_ID, text=chunk.strip(),
           disable_web_page_preview="true")


def send_photo(path):
    with open(path, "rb") as f:
        r = http.post(f"{API}/sendPhoto", data={"chat_id": CHAT_ID},
                      files={"photo": (os.path.basename(path), f, "image/png")})
    if not r.json().get("ok"):
        send_text(f"(couldn't send chart {path}: {r.json().get('description')})")


def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)


def keep_typing(stop):
    while not stop.is_set():
        try:
            tg("sendChatAction", chat_id=CHAT_ID, action="typing")
        except Exception:
            pass
        stop.wait(5)


def ask_claude(prompt, session_id):
    cmd = ["claude", "-p",
           "--mcp-config", ".mcp.json",
           "--permission-mode", "acceptEdits",
           "--permission-prompts", "none",
           "--allowedTools", ALLOWED_TOOLS,
           "--append-system-prompt-file", SYSTEM_PROMPT,
           "--output-format", "json"]
    if session_id:
        cmd += ["--resume", session_id]
    # The prompt goes in on stdin so a message starting with "-" can't be
    # read as a flag.
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                          cwd=ROOT, timeout=CLAUDE_TIMEOUT_S)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, f"{datetime.now(ET):%Y-%m-%d}.jsonl"), "a") as f:
        f.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(),
                            "prompt": prompt, "rc": proc.returncode,
                            "stdout": proc.stdout[-20000:],
                            "stderr": proc.stderr[-5000:]}) + "\n")
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        raise RuntimeError(f"claude exited {proc.returncode}: {(proc.stderr or proc.stdout)[-500:]}")
    return out.get("result") or "", out.get("session_id"), out.get("is_error", False)


PHOTO_LINE = re.compile(r"^\s*\[\[photo:(\S+?)\]\]\s*$", re.M)


def deliver(reply):
    photos = PHOTO_LINE.findall(reply)
    text = PHOTO_LINE.sub("", reply)
    send_text(text)
    for p in photos:
        if os.path.isfile(p):
            send_photo(p)
        else:
            send_text(f"(chart not found: {p})")


def handle(msg, state):
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if not text:
        send_text("I can only read text messages for now.")
        return
    sent = datetime.fromtimestamp(msg["date"], tz=timezone.utc)
    age_min = int((datetime.now(timezone.utc) - sent).total_seconds() // 60)

    if text.lower() in ("/new", "/reset"):
        state.pop("session_id", None)
        save_state(state)
        send_text("Fresh conversation started.")
        return
    if text.lower() == "/start":
        send_text("buffet bot here. Ask me anything about the market or the "
                  "account, or text an order. /new starts a fresh conversation.")
        return

    session_id = state.get("session_id")
    if session_id and time.time() - state.get("last_at", 0) > SESSION_IDLE_RESET_S:
        session_id = None

    prompt = f"[sent {sent.astimezone(ET):%H:%M} ET, {age_min} min ago]\n{text}"
    stop = threading.Event()
    threading.Thread(target=keep_typing, args=(stop,), daemon=True).start()
    try:
        reply, new_sid, is_error = ask_claude(prompt, session_id)
        if is_error:
            reply = f"(error) {reply}"
    except subprocess.TimeoutExpired:
        new_sid = session_id
        reply = (f"Timed out after {CLAUDE_TIMEOUT_S // 60} min. Check logs/chat/ "
                 "before resending anything order-related; it may have gone through.")
    except Exception as e:
        new_sid, reply = session_id, f"Error talking to Claude: {e}"
    finally:
        stop.set()

    if new_sid:
        state["session_id"] = new_sid
    state["last_at"] = time.time()
    save_state(state)
    deliver(reply)


def main():
    state = load_state()
    log(f"listening for chat {CHAT_ID}")
    while True:
        try:
            updates = tg("getUpdates", offset=state.get("offset", 0), timeout=60,
                         allowed_updates='["message"]')
        except Exception as e:
            log(f"poll failed: {e}")
            time.sleep(10)
            continue
        for u in updates:
            # Advance the offset before handling, so a crash mid-order can't
            # replay the same message (and the same order) on restart.
            state["offset"] = u["update_id"] + 1
            save_state(state)
            msg = u.get("message")
            if not msg:
                continue
            if msg["chat"]["id"] != CHAT_ID or msg.get("from", {}).get("id") != CHAT_ID:
                log(f"ignored message from chat {msg['chat']['id']}")
                continue
            log(f"msg: {(msg.get('text') or '')[:80]!r}")
            try:
                handle(msg, state)
            except Exception as e:
                log(f"handle failed: {e}")
                try:
                    send_text(f"Something broke handling that: {e}")
                except Exception:
                    pass


if __name__ == "__main__":
    sys.exit(main())
