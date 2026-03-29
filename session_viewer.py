import argparse
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional


@dataclass(frozen=True)
class ParsedItem:
    order: int
    id: str
    parent_id: Optional[str]
    type: str
    ts: str
    ts_ms: Optional[int]
    role: str
    provider: str
    model: str
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    duration_ms: Optional[int]
    stop_reason: str
    error_message: str
    blocks: list[dict[str, Any]]
    raw: dict[str, Any]


def _parse_iso8601_z(s: str) -> Optional[datetime]:
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _format_ts(dt: Optional[datetime], fallback: str) -> str:
    if dt is None:
        return fallback or "-"
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "Z"


def _first_int(*vals: Any) -> Optional[int]:
    for v in vals:
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(v)
    return None


def _collect_blocks(node: Any, out: list[dict[str, Any]]) -> None:
    if node is None:
        return
    if isinstance(node, str):
        s = node.strip()
        if s:
            out.append({"kind": "text", "text": s})
        return
    if isinstance(node, list):
        for item in node:
            _collect_blocks(item, out)
        return
    if not isinstance(node, dict):
        return

    t = node.get("type")
    if t == "text":
        txt = node.get("text")
        if isinstance(txt, str) and txt.strip():
            out.append({"kind": "text", "text": txt.strip()})
        return
    if t == "thinking":
        txt = node.get("thinking")
        if isinstance(txt, str) and txt.strip():
            out.append({"kind": "thinking", "text": txt.strip()})
        return
    if t == "toolCall":
        name = node.get("name")
        args = node.get("arguments")
        if isinstance(name, str) and name.strip():
            out.append({"kind": "tool_call", "name": name.strip(), "arguments": args})
        return
    if t == "toolResult":
        tool_name = node.get("toolName")
        is_error = node.get("isError")
        details = node.get("details")
        out.append(
            {
                "kind": "tool_result",
                "toolName": tool_name if isinstance(tool_name, str) else "",
                "isError": bool(is_error) if isinstance(is_error, bool) else False,
                "details": details if isinstance(details, dict) else None,
            }
        )
        _collect_blocks(node.get("content"), out)
        return

    txt = node.get("text")
    if isinstance(txt, str) and txt.strip():
        out.append({"kind": "text", "text": txt.strip()})
    txt2 = node.get("thinking")
    if isinstance(txt2, str) and txt2.strip():
        out.append({"kind": "thinking", "text": txt2.strip()})
    _collect_blocks(node.get("content"), out)


def _extract_ts(obj: dict[str, Any]) -> tuple[Optional[datetime], str]:
    ts = obj.get("timestamp")
    if isinstance(ts, str):
        dt = _parse_iso8601_z(ts)
        return dt, ts
    if isinstance(ts, (int, float)):
        dt = datetime.fromtimestamp(float(ts) / 1000.0, tz=timezone.utc)
        return dt, str(int(ts))
    msg = obj.get("message")
    if isinstance(msg, dict):
        mts = msg.get("timestamp")
        if isinstance(mts, (int, float)):
            dt = datetime.fromtimestamp(float(mts) / 1000.0, tz=timezone.utc)
            return dt, str(int(mts))
    data = obj.get("data")
    if isinstance(data, dict):
        dts = data.get("timestamp")
        if isinstance(dts, (int, float)):
            dt = datetime.fromtimestamp(float(dts) / 1000.0, tz=timezone.utc)
            return dt, str(int(dts))
    return None, ""


def _parse_jsonl_line(line: str, order: int) -> Optional[ParsedItem]:
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None

    dt, ts_raw = _extract_ts(obj)
    ts_ms = None
    if dt is not None:
        try:
            ts_ms = int(dt.timestamp() * 1000)
        except Exception:
            ts_ms = None
    item_id = obj.get("id")
    if not isinstance(item_id, str) or not item_id.strip():
        item_id = f"@{order}"

    parent = obj.get("parentId")
    if not isinstance(parent, str) or not parent.strip():
        parent = None

    item_type = obj.get("type")
    if not isinstance(item_type, str):
        item_type = "-"

    role = "-"
    provider = ""
    model = ""
    input_tokens = None
    output_tokens = None
    duration_ms = None
    stop_reason = ""
    error_message = ""
    blocks: list[dict[str, Any]] = []

    if item_type == "message":
        msg = obj.get("message")
        if isinstance(msg, dict):
            r = msg.get("role")
            if isinstance(r, str) and r.strip():
                role = r.strip()

            p = msg.get("provider")
            if isinstance(p, str) and p.strip():
                provider = p.strip()
            m = msg.get("model")
            if isinstance(m, str) and m.strip():
                model = m.strip()

            usage = msg.get("usage")
            if isinstance(usage, dict):
                input_tokens = _first_int(
                    usage.get("input"),
                    usage.get("inputTokens"),
                    usage.get("prompt"),
                    usage.get("promptTokens"),
                )
                output_tokens = _first_int(
                    usage.get("output"),
                    usage.get("outputTokens"),
                    usage.get("completion"),
                    usage.get("completionTokens"),
                )

            stop = msg.get("stopReason")
            if isinstance(stop, str):
                stop_reason = stop

            err = msg.get("errorMessage")
            if isinstance(err, str):
                error_message = err

            details = msg.get("details")
            if isinstance(details, dict):
                duration_ms = _first_int(details.get("durationMs"))

            _collect_blocks(msg.get("content"), blocks)

    return ParsedItem(
        order=order,
        id=item_id,
        parent_id=parent,
        type=item_type,
        ts=_format_ts(dt, ts_raw),
        ts_ms=ts_ms,
        role=role,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        duration_ms=duration_ms,
        stop_reason=stop_reason,
        error_message=error_message,
        blocks=blocks,
        raw=obj,
    )


def _resolve_session_file(sessions_json_path: str, session_key: str) -> str:
    with open(sessions_json_path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict) or session_key not in obj:
        raise KeyError(f"missing session key: {session_key}")
    entry = obj[session_key]
    if not isinstance(entry, dict):
        raise TypeError(f"invalid session entry for key: {session_key}")
    session_file = entry.get("sessionFile")
    if not isinstance(session_file, str) or not session_file.strip():
        raise KeyError(f"missing sessionFile for key: {session_key}")
    return session_file


def _read_tail_lines(session_file: str, max_lines: int) -> tuple[list[str], int, str]:
    try:
        with open(session_file, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            if end <= 0:
                return [], 0, ""
            pos = end
            buf = b""
            need_nl = max(0, max_lines) + 1
            while pos > 0 and buf.count(b"\n") < need_nl:
                step = 65536 if pos >= 65536 else pos
                pos -= step
                f.seek(pos)
                chunk = f.read(step)
                buf = chunk + buf
            raw_lines = buf.splitlines()
            if max_lines > 0 and len(raw_lines) > max_lines:
                raw_lines = raw_lines[-max_lines:]
            lines = [b.decode("utf-8", errors="replace") for b in raw_lines]
            return lines, end, ""
    except FileNotFoundError:
        return [], 0, f"sessionFile not found: {session_file}"
    except Exception as e:
        return [], 0, f"failed to read sessionFile: {session_file} ({e})"


def _index_jsonl(session_file: str, max_lines: int) -> tuple[dict[str, ParsedItem], Optional[ParsedItem], int]:
    by_id: dict[str, ParsedItem] = {}
    tail: Optional[ParsedItem] = None
    lines, file_pos, _err = _read_tail_lines(session_file, max(1, max_lines))
    for i, line in enumerate(lines, start=1):
        item = _parse_jsonl_line(line, order=i)
        if item is None:
            continue
        by_id[item.id] = item
        tail = item
    return by_id, tail, file_pos


def _build_chain(tail: Optional[ParsedItem], by_id: dict[str, ParsedItem], max_count: int) -> list[ParsedItem]:
    if tail is None:
        return []
    chain: list[ParsedItem] = []
    seen: set[str] = set()
    cur: Optional[ParsedItem] = tail
    while cur is not None and cur.id not in seen:
        chain.append(cur)
        seen.add(cur.id)
        if not cur.parent_id:
            break
        cur = by_id.get(cur.parent_id)
    chain.reverse()
    if max_count > 0 and len(chain) > max_count:
        chain = chain[-max_count:]
    return chain


def _item_to_view(item: ParsedItem, span_ms_to_next: Optional[int]) -> dict[str, Any]:
    side = "system"
    if item.type == "message" and item.role == "user":
        side = "user"

    duration_ms = item.duration_ms
    if item.type == "message" and item.role == "toolResult":
        for b in item.blocks:
            if b.get("kind") == "tool_result" and isinstance(b.get("details"), dict):
                duration_ms = _first_int(duration_ms, b["details"].get("durationMs"))

    return {
        "order": item.order,
        "id": item.id,
        "parentId": item.parent_id,
        "type": item.type,
        "role": item.role,
        "provider": item.provider,
        "model": item.model,
        "inputTokens": item.input_tokens,
        "outputTokens": item.output_tokens,
        "ts": item.ts,
        "durationMs": duration_ms,
        "spanMs": span_ms_to_next,
        "stopReason": item.stop_reason,
        "errorMessage": item.error_message,
        "blocks": item.blocks,
        "side": side,
        "raw": item.raw,
    }


def _build_state(sessions_json_path: str, session_key: str, max_count: int) -> dict[str, Any]:
    session_file = ""
    state_err = ""
    try:
        session_file = _resolve_session_file(sessions_json_path, session_key)
    except Exception as e:
        state_err = f"failed to resolve sessionFile: {e}"
        return {
            "sessionKey": session_key,
            "sessionsJson": sessions_json_path,
            "sessionFile": "",
            "filePos": 0,
            "tailId": None,
            "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "items": [],
            "error": state_err,
        }

    lines, file_pos, err = _read_tail_lines(session_file, max(1, max_count))
    if err:
        state_err = err
    by_id: dict[str, ParsedItem] = {}
    tail: Optional[ParsedItem] = None
    for i, line in enumerate(lines, start=1):
        item = _parse_jsonl_line(line, order=i)
        if item is None:
            continue
        by_id[item.id] = item
        tail = item
    chain = _build_chain(tail=tail, by_id=by_id, max_count=max_count)
    spans: list[Optional[int]] = []
    for i, it in enumerate(chain):
        if i == 0:
            spans.append(None)
            continue
        a = chain[i - 1].ts_ms
        b = it.ts_ms
        if a is None or b is None:
            spans.append(None)
            continue
        spans.append(max(0, b - a))
    return {
        "sessionKey": session_key,
        "sessionsJson": sessions_json_path,
        "sessionFile": session_file,
        "filePos": file_pos,
        "tailId": tail.id if tail else None,
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "items": [_item_to_view(it, spans[i]) for i, it in enumerate(chain)],
        "error": state_err,
    }


HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>OpenClaw Session Viewer</title>
  <style>
    :root{
      --bg:#f5f5f7;
      --panel:#ffffff;
      --line:#e6e6ea;
      --muted:#6b7280;
      --sys:#ffffff;
      --usr:#95ec69;
      --tool:#0ea5e9;
      --err:#ef4444;
      --shadow:0 6px 20px rgba(0,0,0,.06);
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono","Courier New", monospace;
      --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", "PingFang SC","Hiragino Sans GB","Microsoft YaHei", sans-serif;
    }
    html,body{height:100%;}
    body{margin:0;background:var(--bg);font-family:var(--sans);color:#111827;}
    .topbar{
      height:48px;display:flex;align-items:center;gap:10px;
      padding:0 14px;border-bottom:1px solid var(--line);background:rgba(255,255,255,.85);
      position:sticky;top:0;backdrop-filter:saturate(180%) blur(10px);z-index:5;
    }
    .badge{font-size:12px;color:var(--muted);}
    .wrap{height:calc(100% - 48px);padding:12px;box-sizing:border-box;display:flex;justify-content:center;min-height:0;}
    .chat{
      width:min(980px, 100%);
      background:var(--panel);
      border:1px solid var(--line);
      border-radius:14px;
      box-shadow:var(--shadow);
      overflow:hidden;
      display:flex;
      flex-direction:column;
      height:100%;
      min-height:0;
    }
    .chatbar{
      padding:10px 12px;border-bottom:1px solid var(--line);font-size:13px;color:var(--muted);
      display:flex;align-items:center;justify-content:space-between;
    }
    .scroll{flex:1;overflow-y:auto;overflow-x:hidden;padding:12px 12px 18px;min-height:0;}
    .row{display:flex;margin:8px 0;}
    .left{justify-content:flex-start;}
    .right{justify-content:flex-end;}
    .centerrow{justify-content:center;}
    .bubble{
      max-width:min(680px, 92%);
      border-radius:14px;padding:10px 12px;
      border:1px solid var(--line);
      background:var(--sys);
      position:relative;
    }
    .bubble.user{background:var(--usr);border-color:rgba(0,0,0,.06);}
    .bubble.llm{
      border-color:rgba(99,102,241,.40);
      box-shadow:0 10px 24px rgba(99,102,241,.10);
      background:
        linear-gradient(180deg, rgba(99,102,241,.08), rgba(255,255,255,0) 46%),
        var(--sys);
    }
    .bubble.user.llm{
      border-color:rgba(99,102,241,.35);
      background:
        linear-gradient(180deg, rgba(99,102,241,.10), rgba(255,255,255,0) 46%),
        var(--usr);
    }
    .bubble.llm::after{
      content:"LLM";
      position:absolute;
      top:-8px;
      right:12px;
      font-size:10px;
      padding:1px 8px;
      border-radius:999px;
      border:1px solid rgba(99,102,241,.30);
      background:rgba(99,102,241,.14);
      color:#3730a3;
      letter-spacing:.3px;
    }
    .bubble.event{
      max-width:min(820px, 96%);
      background:#f3f4f6;
      border-color:rgba(107,114,128,.28);
      border-style:dashed;
      box-shadow:none;
    }
    .bubble.event::after{
      content:attr(data-evt);
      position:absolute;
      top:-8px;
      right:12px;
      font-size:10px;
      padding:1px 8px;
      border-radius:999px;
      border:1px solid rgba(107,114,128,.28);
      background:rgba(107,114,128,.12);
      color:#374151;
      letter-spacing:.2px;
      text-transform:none;
    }
    .meta{
      display:flex;gap:10px;flex-wrap:wrap;
      font-size:11px;color:var(--muted);margin-bottom:6px;
    }
    .meta code{font-family:var(--mono);font-size:11px;color:#374151;background:#f3f4f6;padding:1px 6px;border-radius:10px;}
    .meta code.provider{background:rgba(99,102,241,.14);color:#3730a3;border:1px solid rgba(99,102,241,.26);}
    .meta code.tokens{background:rgba(14,165,233,.12);color:#075985;border:1px solid rgba(14,165,233,.25);}
    .bubble.user .meta code{background:rgba(255,255,255,.55);}
    .bubble.user .meta code.provider{background:rgba(255,255,255,.55);border-color:rgba(99,102,241,.28);color:#312e81;}
    .bubble.user .meta code.tokens{background:rgba(255,255,255,.55);border-color:rgba(14,165,233,.25);color:#0c4a6e;}
    .content{font-size:14px;line-height:1.45;white-space:pre-wrap;word-break:break-word;}
    .block{margin:8px 0 0;}
    .pill{
      display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;
      background:#f3f4f6;color:#111827;border:1px solid var(--line);
    }
    .pill.tool{background:rgba(14,165,233,.10);border-color:rgba(14,165,233,.25);color:#075985;}
    .pill.err{background:rgba(239,68,68,.10);border-color:rgba(239,68,68,.25);color:#7f1d1d;}
    .pill.fast{background:rgba(16,185,129,.12);border-color:rgba(16,185,129,.25);color:#065f46;}
    .pill.slow{background:rgba(234,179,8,.12);border-color:rgba(234,179,8,.25);color:#78350f;}
    .pill.very{background:rgba(239,68,68,.12);border-color:rgba(239,68,68,.25);color:#7f1d1d;}
    .pill.thing{background:rgba(107,114,128,.10);border-color:rgba(107,114,128,.22);color:#374151;}
    .thinking{
      background:rgba(107,114,128,.08);
      border:1px solid rgba(107,114,128,.18);
      border-radius:12px;
      padding:8px 10px;
      font-size:13px;
      color:#374151;
      white-space:pre-wrap;
      word-break:break-word;
      font-family:var(--mono);
    }
    .kv{
      margin-top:6px;font-family:var(--mono);font-size:12px;background:#0b1220;color:#e5e7eb;
      border-radius:12px;padding:10px 10px;overflow:auto;border:1px solid rgba(255,255,255,.08);
    }
    .bubble.user .kv{background:rgba(17,24,39,.9);}
    .center{
      display:flex;justify-content:center;margin:12px 0;
    }
    .center .sysline{
      font-size:12px;color:var(--muted);background:#f3f4f6;border:1px solid var(--line);
      padding:6px 10px;border-radius:999px;
    }
    .hint{font-size:12px;color:var(--muted);}
    .sep{height:1px;background:var(--line);margin:10px 0;}
  </style>
</head>
<body>
  <div class="topbar">
    <div style="font-weight:600;">Session Viewer</div>
    <div class="badge" id="badge"></div>
    <div style="flex:1;"></div>
    <div class="badge" id="status">connecting…</div>
  </div>
  <div class="wrap">
    <div class="chat">
      <div class="chatbar"><span>Chat</span><span class="hint" id="count"></span></div>
      <div class="scroll" id="chat"></div>
    </div>
  </div>
<script>
  const els = {
    badge: document.getElementById('badge'),
    status: document.getElementById('status'),
    chat: document.getElementById('chat'),
    count: document.getElementById('count'),
  };

  function nearBottom(el) {
    return (el.scrollHeight - el.scrollTop - el.clientHeight) < 120;
  }

  function escapeHtml(s) {
    return String(s)
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function prettyJson(v) {
    try { return JSON.stringify(v, null, 2); } catch { return String(v); }
  }

  function fmtDelta(ms) {
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60_000) return `${(ms/1000).toFixed(ms < 10_000 ? 2 : 1)}s`;
    const s = Math.floor(ms/1000);
    const m = Math.floor(s/60);
    const r = s % 60;
    return `${m}m${r.toString().padStart(2,'0')}s`;
  }

  function deltaBadge(ms) {
    if (typeof ms !== 'number') return '';
    if (ms > 300_000) return '';
    const txt = `Δ${fmtDelta(ms)}`;
    let cls = 'fast';
    if (ms >= 10_000) cls = 'very';
    else if (ms >= 1_000) cls = 'slow';
    return `<span class="pill ${cls}">${txt}</span>`;
  }

  function renderBlock(block) {
    const kind = block.kind;
    if (kind === 'text') {
      return `<div class="block"><div class="content">${escapeHtml(block.text || '')}</div></div>`;
    }
    if (kind === 'thinking') {
      return `<div class="block"><span class="pill thing">thinking</span><div class="thinking">${escapeHtml(block.text || '')}</div></div>`;
    }
    if (kind === 'tool_call') {
      const args = block.arguments === undefined ? null : block.arguments;
      return `
        <div class="block">
          <span class="pill tool">toolCall</span>
          <span class="pill">${escapeHtml(block.name || '')}</span>
          ${args ? `<div class="kv">${escapeHtml(prettyJson(args))}</div>` : ''}
        </div>
      `;
    }
    if (kind === 'tool_result') {
      const d = block.details || null;
      const dms = d && typeof d.durationMs === 'number' ? d.durationMs : null;
      const tag = block.isError ? 'err' : 'tool';
      const label = block.isError ? 'toolError' : 'toolResult';
      return `
        <div class="block">
          <span class="pill ${tag}">${label}</span>
          <span class="pill">${escapeHtml(block.toolName || '')}</span>
          ${dms !== null ? `<span class="pill">${dms}ms</span>` : ''}
          ${d ? `<div class="kv">${escapeHtml(prettyJson(d))}</div>` : ''}
        </div>
      `;
    }
    return `<div class="block"><div class="kv">${escapeHtml(prettyJson(block))}</div></div>`;
  }

  function renderItem(item) {
    const isUser = item.side === 'user';
    const isEvent = item.type === 'custom' || item.type === 'thinking_level_change' || item.type === 'model_change';
    const rowClass = isEvent ? 'row centerrow' : (isUser ? 'row right' : 'row left');
    const isLLM = !!(item.provider && item.model);
    const bubbleClass = (isUser ? 'bubble user' : 'bubble') + (isLLM ? ' llm' : '') + (isEvent ? ' event' : '');
    const evtAttr = isEvent ? ` data-evt="${escapeHtml(item.type)}"` : '';
    const metaParts = [
      `<span>${escapeHtml(item.ts)}</span>`,
      `<code>${escapeHtml(item.type)}</code>`,
      `<code>${escapeHtml(item.role || '-')}</code>`,
      `<code>id:${escapeHtml(item.id)}</code>`,
    ];
    if (item.parentId) metaParts.push(`<code>p:${escapeHtml(item.parentId)}</code>`);
    if (item.provider && item.model) metaParts.push(`<code class="provider">${escapeHtml(item.provider)}/${escapeHtml(item.model)}</code>`);
    if (isLLM && (typeof item.inputTokens === 'number' || typeof item.outputTokens === 'number')) {
      const ins = typeof item.inputTokens === 'number' ? String(item.inputTokens) : '-';
      const outs = typeof item.outputTokens === 'number' ? String(item.outputTokens) : '-';
      metaParts.push(`<code class="tokens">tok:${escapeHtml(ins)}/${escapeHtml(outs)}</code>`);
    }
    if (typeof item.durationMs === 'number') metaParts.push(`<code>${item.durationMs}ms</code>`);
    if (!isUser && typeof item.spanMs === 'number') {
      const b = deltaBadge(item.spanMs);
      if (b) metaParts.push(b);
    }
    if (item.stopReason) metaParts.push(`<code>stop:${escapeHtml(item.stopReason)}</code>`);
    if (item.errorMessage) metaParts.push(`<code class="err">err</code>`);

    const blocks = Array.isArray(item.blocks) ? item.blocks : [];
    const body = blocks.length ? blocks.map(renderBlock).join('') : `<div class="block"><div class="kv">${escapeHtml(prettyJson(item.raw))}</div></div>`;

    const err = item.errorMessage ? `<div class="block"><span class="pill err">error</span><div class="kv">${escapeHtml(String(item.errorMessage))}</div></div>` : '';
    return `<div class="${rowClass}"><div class="${bubbleClass}"${evtAttr}><div class="meta">${metaParts.join('')}</div>${body}${err}</div></div>`;
  }

  function render(state) {
    els.badge.textContent = `${state.sessionKey} · ${state.sessionFile}`;
    const itemsAll = Array.isArray(state.items) ? state.items : [];
    const items = itemsAll.length > 50 ? itemsAll.slice(-50) : itemsAll;
    const auto = nearBottom(els.chat);
    const emptyText = state && state.error ? `Error: ${state.error}` : 'No messages';
    els.chat.innerHTML = items.map(renderItem).join('') || `<div class="center"><div class="sysline">${escapeHtml(emptyText)}</div></div>`;
    els.count.textContent = `${items.length} items`;
    if (auto) els.chat.scrollTop = els.chat.scrollHeight;
  }

  function connect() {
    const ev = new EventSource('/events');
    els.status.textContent = 'connected';
    ev.onmessage = (e) => {
      try {
        const state = JSON.parse(e.data);
        render(state);
      } catch {
      }
    };
    ev.onerror = () => {
      els.status.textContent = 'reconnecting…';
      try { ev.close(); } catch {}
      setTimeout(connect, 800);
    };
  }

  connect();
</script>
</body>
</html>
"""


class App:
    def __init__(self, sessions_json: str, session_key: str, max_count: int, poll_s: float):
        self.sessions_json = sessions_json
        self.session_key = session_key
        self.max_count = max_count
        self.poll_s = poll_s
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {}
        self._sessions_mtime: Optional[float] = None
        self._session_file: Optional[str] = None
        self._jsonl_mtime: Optional[float] = None
        self._load_state()

    def _load_state(self) -> None:
        state = _build_state(self.sessions_json, self.session_key, self.max_count)
        sessions_mtime = None
        jsonl_mtime = None
        try:
            sessions_mtime = os.stat(self.sessions_json).st_mtime
        except Exception:
            sessions_mtime = None
        sf = state.get("sessionFile")
        if isinstance(sf, str) and sf:
            try:
                jsonl_mtime = os.stat(sf).st_mtime
            except Exception:
                jsonl_mtime = None

        with self._lock:
            self._state = state
            self._sessions_mtime = sessions_mtime
            self._session_file = sf if isinstance(sf, str) and sf else None
            self._jsonl_mtime = jsonl_mtime

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            return self._state

    def wait_for_change_and_refresh(self) -> dict[str, Any]:
        while True:
            sessions_mtime = None
            try:
                sessions_mtime = os.stat(self.sessions_json).st_mtime
            except Exception:
                sessions_mtime = None

            session_file = None
            with self._lock:
                session_file = self._session_file
                prev_sessions_mtime = self._sessions_mtime
                prev_jsonl_mtime = self._jsonl_mtime

            sessions_changed = (sessions_mtime != prev_sessions_mtime) and (sessions_mtime is not None or prev_sessions_mtime is not None)
            if sessions_changed:
                self._load_state()
                return self.get_state()

            resolved_session_file = None
            try:
                resolved_session_file = _resolve_session_file(self.sessions_json, self.session_key)
            except Exception:
                resolved_session_file = None
            if resolved_session_file and resolved_session_file != session_file:
                self._load_state()
                return self.get_state()

            jsonl_mtime = None
            if session_file:
                try:
                    jsonl_mtime = os.stat(session_file).st_mtime
                except Exception:
                    jsonl_mtime = None

            jsonl_changed = (jsonl_mtime != prev_jsonl_mtime) and (jsonl_mtime is not None or prev_jsonl_mtime is not None)
            if jsonl_changed:
                self._load_state()
                return self.get_state()

            time.sleep(max(0.05, self.poll_s))


def _write_json(handler: BaseHTTPRequestHandler, status: int, obj: Any) -> None:
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _write_text(handler: BaseHTTPRequestHandler, status: int, text: str, content_type: str) -> None:
    data = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions-json", default="/Users/speedx/.openclaw/agents/main/sessions/sessions.json")
    ap.add_argument("--session-key", default="agent:main:main")
    ap.add_argument("-n", "--max", "--count", dest="max", type=int, default=50)
    ap.add_argument("--poll", type=float, default=0.25)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    app = App(
        sessions_json=args.sessions_json,
        session_key=args.session_key,
        max_count=max(1, args.max),
        poll_s=args.poll,
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            if self.path == "/" or self.path.startswith("/?"):
                _write_text(self, 200, HTML, "text/html; charset=utf-8")
                return
            if self.path.startswith("/state"):
                _write_json(self, 200, app.get_state())
                return
            if self.path.startswith("/events"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()

                def send_state(st: dict[str, Any]) -> None:
                    payload = json.dumps(st, ensure_ascii=False)
                    msg = f"data: {payload}\n\n"
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()

                send_state(app.get_state())
                while True:
                    st = app.wait_for_change_and_refresh()
                    send_state(st)
                return

            _write_text(self, 404, "not found", "text/plain; charset=utf-8")

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"http://{args.host}:{args.port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
