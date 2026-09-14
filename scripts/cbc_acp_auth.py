import subprocess, json, threading, time, sys, os

# 用法：python cbc_acp_auth.py [methodId]
#   methodId: iOA | external | internal | selfhosted   （默认 internal = 微信登录）
# 作用：按 ACP 协议对 WorkBuddy/CodeBuddy CLI 发起 authenticate，
#       把 agent 返回的所有消息（含可能出现的登录 URL / 设备码）原样打印出来。
NODE = r"C:/Users/<USER>/.workbuddy/binaries/node/versions/22.22.2-2/node.exe"
CBC  = os.environ.get("CBC_CLI") or r"D:/npm-global/node_modules/@tencent-ai/codebuddy-code/bin/codebuddy"
METHOD = sys.argv[1] if len(sys.argv) > 1 else "internal"

env = dict(os.environ)
p = subprocess.Popen([NODE, CBC, "--acp", "--acp-transport", "stdio"],
                     cwd=r"C:/Users/<USER>", env=env,
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     text=True, encoding="utf-8", errors="replace", bufsize=1)

def send(obj):
    try:
        p.stdin.write(json.dumps(obj) + "\n"); p.stdin.flush()
    except Exception as e:
        print("SEND FAIL:", e)

msgs, errs = [], []

def reader(stream, sink, is_err=False):
    for line in stream:
        line = line.rstrip()
        if not line:
            continue
        if is_err:
            errs.append(line); print("[stderr]", line[:400]); continue
        sink.append(line)
        try:
            m = json.loads(line)
            print("[recv]", json.dumps(m, ensure_ascii=False)[:600])
        except Exception:
            print("[raw]", line[:400])

threading.Thread(target=reader, args=(p.stdout, msgs), daemon=True).start()
threading.Thread(target=reader, args=(p.stderr, errs, True), daemon=True).start()

send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{}}})
time.sleep(3)
print("=== 发起 authenticate:", METHOD, "===")
send({"jsonrpc":"2.0","id":2,"method":"authenticate","params":{"methodId":METHOD}})
time.sleep(18)
print("=== 尝试 session/new（看认证是否已通过）===")
send({"jsonrpc":"2.0","id":3,"method":"session/new","params":{"cwd":"C:/Users/<USER>","mcpServers":[]}})
time.sleep(8)
print("=== 总结 ===")
print("总消息数:", len(msgs))
p.kill()
