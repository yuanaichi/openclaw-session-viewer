# openclaw-session-viewer

一个纯 Python 的 OpenClaw session（jsonl）聊天记录浏览器：读取 `sessions.json` 找到当前会话对应的 `sessionFile`，按 `id -> parentId` 链条还原成一个“聊天窗口”，并在文件追加/切换时实时刷新页面。

## 功能

- 按 `id/parentId` 还原对话链条（不是简单按时间排序）
- 实时刷新：jsonl 追加新记录会自动显示；`sessions.json` 指向的 `sessionFile` 变化也会自动切换
- 统一展示多种内容块：text / thinking / toolCall / toolResult / 原始 JSON
- LLM 记录增强显示：provider/model、token input/output、stopReason、错误信息
- Δ 耗时显示：记录间隔（下一条时间 - 当前条时间）并按快慢着色（超长间隔会隐藏，避免会话结束导致的误判）
- 支持局域网访问：`--host 0.0.0.0`

## 依赖

- Python 3.10+（使用了 `list[...]` 这类新语法）

## 运行

在本目录下执行：

```bash
python3 session_viewer.py
```

启动后会打印一个 URL（默认 `http://127.0.0.1:8765/`），用浏览器打开即可。

### 常用参数

```bash
python3 session_viewer.py \
  --sessions-json /Users/speedx/.openclaw/agents/main/sessions/sessions.json \
  --session-key agent:main:main \
  --count 500 \
  --poll 0.25 \
  --host 127.0.0.1 \
  --port 8765
```

- `--sessions-json`: OpenClaw 的 sessions 索引文件
- `--session-key`: sessions.json 里的 key（例如 `agent:main:main`）
- `--count`/`-n`: 页面最多显示多少条（沿链条截断）
- `--poll`: 轮询间隔（秒）
- `--host`/`--port`: HTTP 服务监听地址

### 局域网访问

```bash
python3 session_viewer.py --host 0.0.0.0 --port 8765
```

然后用同一局域网内设备访问：`http://<你的机器IP>:8765/`

## HTTP 接口

- `/`：页面
- `/state`：返回当前状态 JSON（包含 items）
- `/events`：SSE（Server-Sent Events），每次状态变化推送一次完整 state

## 文件说明

- [session_viewer.py](file:///Users/speedx/openclaw-sessions/openclaw-session-viewer/session_viewer.py)：服务端 + 前端页面（单文件）
