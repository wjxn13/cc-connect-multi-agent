import subprocess, json, threading, time, sys, os
from collections import Counter

# 用法：D:/python/python.exe acp_filter_test.py "@claude，你好"
# 作用：通过 acp-thought-filter.js 代理连 DSH，验证思考块是否被成功丢弃。
# 期望：只出现 agent_message_chunk（内容恰好 NO_REPLY），不出现 agent_thought_chunk。
PROMPT = sys.argv[1] if len(sys.argv) > 1 else "@claude，你好"

NODE = r"C:/Users/<USER>/.workbuddy/binaries/node/versions/22.22.2-2/node.exe"
DSH = r"D:/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js"
FILTER = r"C:/Users/<USER>/.workbuddy/skills/cc-connect-weixin-bridge/scripts/acp-thought-filter.js"

env = dict(os.environ)
env["ACP_FILTER_LOG"] = "1"   # 让代理把丢弃记录打到 stderr

p = subprocess.Popen(
    [NODE, FILTER, NODE, DSH, "--profile", "acp"],
    cwd=r"C:/Users/<USER>",
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
)

def send(o):
    p.stdin.write(json.dumps(o) + "\n")
    p.stdin.flush()

seen = []
msgs = []
dropped = []
done = threading.Event()

def extract_text(u):
    """从 update 里安全取出文本。

    踩坑（2026-09-14）：`content` 不一定是 dict —— 真实任务里会出现
    `tool_call` / `tool_call_update`，它们的 content 常常是**列表**
    （每个元素形如 {"type":"content","content":{...}}）。旧版直接
    `u.get("content", {}).get("text")` 会在列表上抛 AttributeError，
    把 reader 线程打死 → 后面所有帧都收不到，结果误判成 FAIL。
    """
    c = u.get("content")
    parts = []
    if isinstance(c, dict):
        parts.append(c.get("text") or "")
    elif isinstance(c, list):
        for item in c:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("content"), dict):
                parts.append(item["content"].get("text") or "")
            parts.append(item.get("text") or "")
    return "".join(parts)


def tool_label(u):
    """给 tool_call 拼一个可读标签：标题 + 关键入参，便于看它调了什么工具。"""
    title = u.get("title") or u.get("kind") or u.get("toolName") or ""
    raw = u.get("rawInput")
    extra = ""
    if isinstance(raw, dict):
        # memorix 的工具名通常藏在 toolName / name / 命令里
        for k in ("toolName", "name", "command", "tool", "query", "prompt"):
            if raw.get(k):
                extra = f"{k}={str(raw[k])[:120]}"
                break
    return f"{title} {extra}".strip()


def reader():
    for line in p.stdout:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            m = json.loads(line)
        except Exception:
            continue
        msgs.append(m)
        if m.get("method") == "session/update":
            u = m.get("params", {}).get("update", {})
            kind = u.get("sessionUpdate")
            txt = extract_text(u)
            seen.append((kind, txt))
            if kind in ("tool_call", "tool_call_update"):
                print("[TOOL]", kind, "|", tool_label(u)[:200], flush=True)
            else:
                print("[UPDATE]", kind, "|", repr(txt[:200]), flush=True)
        if m.get("id") == 3:
            done.set()

def err_reader():
    for line in p.stderr:
        line = line.rstrip()
        if line:
            dropped.append(line)
            print("[STDERR]", line[:160], flush=True)

threading.Thread(target=reader, daemon=True).start()
threading.Thread(target=err_reader, daemon=True).start()

send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
      "params": {"protocolVersion": 1, "clientCapabilities": {}}})
time.sleep(2)
send({"jsonrpc": "2.0", "id": 2, "method": "session/new",
      "params": {"cwd": "C:/Users/<USER>", "mcpServers": []}})

sid = None
for _ in range(30):
    time.sleep(1)
    for m in msgs:
        if m.get("id") == 2 and "result" in m:
            sid = m["result"].get("sessionId") or m["result"].get("session_id")
    if sid:
        break

print("SESSION:", sid, flush=True)

if sid:
    send({"jsonrpc": "2.0", "id": 3, "method": "session/prompt",
          "params": {"sessionId": sid, "prompt": [{"type": "text", "text": PROMPT}]}})
    done.wait(timeout=150)

print("\n== 汇总：过滤后剩下的 sessionUpdate 类型 ==")
print(Counter(k for k, _ in seen))
print("\n== 被代理丢弃的帧数:", len(dropped), "==")
print("\n== 最终拼接结果（cc-connect 将看到的内容）==")
final = "".join(t for k, t in seen if k == "agent_message_chunk")
print(repr(final))
print("\n== 判定 ==")
# 只有「该被静默」的消息才期望恰好 NO_REPLY；真实任务当然会有正常正文。
should_silence = PROMPT.strip().startswith(("@claude", "@wb", "@workbuddy"))
if should_silence:
    print("PASS：恰好是 NO_REPLY，点位制可正常静默" if final.strip() == "NO_REPLY"
          else "FAIL：应静默却输出了正文 —— " + repr(final[:200]))
else:
    print("（真实任务，不适用 NO_REPLY 判定）正文长度 =", len(final.strip()))
    print("提示：往下看 [TOOL] 行里有没有 memorix_session_start / memorix_poll。")

p.kill()
