import subprocess, json, threading, time, sys

# 用法：D:/python/python.exe acp_probe.py "@claude，1+1是多少"
# 作用：以真实 ACP 协议握手 DSH，发一条 prompt，收集 agent_message_chunk 输出。
# 用途：本地验证点名制（@claude 应输出恰好 NO_REPLY）与推理泄漏是否已消除。
PROMPT = sys.argv[1] if len(sys.argv) > 1 else "@claude，1+1是多少"

NODE = r"C:/Users/<USER>/.workbuddy/binaries/node/versions/22.22.2-2/node.exe"
DSH  = r"D:/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js"
p = subprocess.Popen([NODE, DSH, "--profile", "acp"], cwd=r"C:/Users/<USER>",
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                     text=True, encoding="utf-8", errors="replace", bufsize=1)

def send(obj):
    p.stdin.write(json.dumps(obj) + "\n"); p.stdin.flush()

out_lines, chunks, done = [], [], threading.Event()

def reader():
    for line in p.stdout:
        line = line.strip()
        if not line.startswith("{"): continue
        try: msg = json.loads(line)
        except Exception: continue
        out_lines.append(msg)
        if msg.get("method") == "session/update":
            u = msg.get("params", {}).get("update", {})
            if u.get("sessionUpdate") == "agent_message_chunk":
                t = u.get("content", {}).get("text")
                if t: chunks.append(t)
        if msg.get("id") == 3 and ("result" in msg or "error" in msg):
            done.set()

threading.Thread(target=reader, daemon=True).start()
send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{}}})
time.sleep(2)
sid = None
for attempt in range(20):
    for m in out_lines:
        if m.get("id") == 2 and "result" in m:
            sid = m["result"].get("sessionId") or m["result"].get("session_id")
    if sid: break
    if attempt == 0:
        send({"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"C:/Users/<USER>","mcpServers":[]}})
    time.sleep(1)
print("SESSION:", sid)
if sid:
    send({"jsonrpc":"2.0","id":3,"method":"session/prompt","params":{"sessionId":sid,"prompt":[{"type":"text","text":PROMPT}]}})
    done.wait(timeout=120)
    print("== 输出块 ==")
    print(repr("".join(chunks))[:400])
p.kill()
