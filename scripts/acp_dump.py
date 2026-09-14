import subprocess, json, threading, time, sys
from collections import Counter

# 用法：D:/python/python.exe acp_dump.py "@claude，你好"
# 作用：以真实 ACP 协议握手 DSH，把 agent 发出的【所有 session/update 类型】原样打印，
#       用来判断「推理独白」是以哪种 sessionUpdate 类型发出的：
#         - agent_thought_chunk  → 标准思考块（可用中间代理过滤）
#         - agent_message_chunk  → 与正文同类型（cc-connect 无法区分，必须从 agent 侧解决）
PROMPT = sys.argv[1] if len(sys.argv) > 1 else "@claude，你好"

NODE = r"C:/Users/<USER>/.workbuddy/binaries/node/versions/22.22.2-2/node.exe"
DSH = r"D:/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js"

p = subprocess.Popen(
    [NODE, DSH, "--profile", "acp"],
    cwd=r"C:/Users/<USER>",
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    text=True, encoding="utf-8", errors="replace", bufsize=1,
)

def send(o):
    p.stdin.write(json.dumps(o) + "\n")
    p.stdin.flush()

seen = []
msgs = []
done = threading.Event()

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
            txt = u.get("content", {}).get("text", "") or ""
            seen.append((kind, txt))
            print("[UPDATE]", kind, "|", repr(txt[:250]), flush=True)
        if m.get("id") == 3:
            print("[PROMPT-RESULT]", json.dumps(m, ensure_ascii=False)[:300], flush=True)
            done.set()

threading.Thread(target=reader, daemon=True).start()

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
    # MCP server（memorix / argo 等）是异步启动的：memorix 要建索引（本项目 276 条
    # observations），实测需要十几秒。若立刻发 prompt，工具列表里会看不到这些 MCP 工具。
    # cc-connect 实链路的 agent 是常驻的，第二条消息起就不存在这个问题；
    # 本地探针每次都是冷启动，所以这里主动等一段时间。
    time.sleep(20)
    send({"jsonrpc": "2.0", "id": 3, "method": "session/prompt",
          "params": {"sessionId": sid, "prompt": [{"type": "text", "text": PROMPT}]}})
    done.wait(timeout=150)

print("\n== 汇总：出现过的 sessionUpdate 类型（含次数）==")
print(Counter(k for k, _ in seen))
print("\n== agent_message_chunk 拼接结果 ==")
print(repr("".join(t for k, t in seen if k == "agent_message_chunk"))[:800])
for k in sorted(set(k for k, _ in seen)):
    if k != "agent_message_chunk":
        print("\n== 其他类型:", k, "==")
        print(repr("".join(t for kk, t in seen if kk == k))[:500])

p.kill()
