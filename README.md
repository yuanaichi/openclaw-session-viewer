# openclaw-session-viewer

Language: [English](README.md) | [中文](README.zh.md)

A pure-Python OpenClaw session (jsonl) chat viewer. It reads `sessions.json` to locate the active `sessionFile`, reconstructs a “chat window” by following the `id -> parentId` chain, and refreshes the page in real time when the jsonl file grows or when `sessions.json` switches to a different `sessionFile`.

## Features

- Reconstructs conversation by `id/parentId` chain (not just timestamp order)
- Live updates: appends in jsonl show up automatically; changes in `sessions.json` that point to a new `sessionFile` are auto-switched
- Unified rendering for blocks: text / thinking / toolCall / toolResult / raw JSON
- LLM metadata: provider/model, token input/output, stopReason, error message
- Δ timing: shows time gaps (next record time - current record time) with speed-based colors (very long gaps are hidden to avoid “session ended” false signals)
- LAN access: `--host 0.0.0.0`

## Requirements

- Python 3.10+ (uses newer syntax like `list[...]`)

## Run

Run from this directory:

```bash
python3 session_viewer.py
```

It prints a URL on startup (default `http://127.0.0.1:8765/`). Open it in your browser.

### Common options

```bash
python3 session_viewer.py \
  --sessions-json /Users/speedx/.openclaw/agents/main/sessions/sessions.json \
  --session-key agent:main:main \
  --count 500 \
  --poll 0.25 \
  --host 127.0.0.1 \
  --port 8765
```

- `--sessions-json`: OpenClaw sessions index file
- `--session-key`: key in sessions.json (e.g. `agent:main:main`)
- `--count`/`-n`: maximum number of items to render (truncates along the chain)
- `--poll`: polling interval (seconds)
- `--host`/`--port`: HTTP bind address

### LAN access

```bash
python3 session_viewer.py --host 0.0.0.0 --port 8765
```

Then access it from another device on the same network: `http://<your-ip>:8765/`

## HTTP endpoints

- `/`: page
- `/state`: current state as JSON (includes items)
- `/events`: SSE (Server-Sent Events), pushes a full state on each change

## Files

- [session_viewer.py](file:///Users/speedx/openclaw-sessions/openclaw-session-viewer/session_viewer.py): server + frontend (single file)
