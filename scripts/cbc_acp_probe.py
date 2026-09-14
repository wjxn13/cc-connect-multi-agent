import subprocess, json, threading, time, sys, os

# 用法：python cbc_acp_probe.py "问题内容"
# 作用：以正确的 ACP 协议顺序与 codebuddy CLI（WorkBuddy）交互并取回回答。
#
# ⚠️ 关键时序（踩过的坑）：
#   ACP 下必须显式走完 authenticate 才能 session/new，否则 session/new 会返回
#   {"code":-32000,"message":"Authentication required"}。
#   而且必须【等到 authenticate 的响应】再发 session/new —— 二者并发时，
#   session/new 会先于 authenticate 完成而失败。
#   正确顺序：initialize → authenticate(等响应) → session/new → session/prompt
PROMPT = sys.argv[1] if len(sys.argv) > 1 else "只回复两个字：可用"

NODE = r"C:/Users/<USER>/.workbuddy/binaries/node/versions/22.22.2-2/node.exe"
# 默认用 WorkBuddy 内嵌版（连 www.workbuddy.cn 账号体系）；可用 CBC_CLI 覆盖。
CBC = os.environ.get("CBC_CLI") or r"D:/workbudy/WorkBuddy/resources/app.asar.unpacked/cli/bin/codebuddy"
METHOD = os.environ.get("CBC_AUTH_METHOD", "internal")

# ⚠️ 第二个坑：必须【清掉宿主会话注入的 CODEBUDDY_* 环境变量】再启动 CLI。
# 若从 WorkBuddy 会话内启动，会继承 50+ 个变量（CODEBUDDY_SESSION_ID /
# CODEBUDDY_CONVERSATION_REQUEST_ID / CODEBUDDY_MCP_CONFIG / CODEBUDDY_CONFIG_DIR /
# 网关凭证等），会让 CLI 误以为自己身处宿主会话，session/new 直接无响应。
CONFIG_DIR = os.environ.get("CBC_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".codebuddy")
# 工作目录：用一个独立空目录，避免在家目录下建会话时触发大规模扫描而卡住，
# 同时也避免读到其他 agent 的 CLAUDE.md 规则文件。
CWD = os.environ.get("CBC_CWD") or os.path.join(os.path.expanduser("~"), "wb-agent")
env = {k: v for k, v in os.environ.items()
       if not k.upper().startswith(("CODEBUDDY_", "CLAUDE_", "GENIE_"))}
env["CODEBUDDY_CONFIG_DIR"] = CONFIG_DIR
env["PATH"] = os.environ.get("PATH", "")

p = subprocess.Popen([NODE, CBC, "--acp", "--acp-transport", "stdio",
                      "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
                     cwd=CWD, env=env,
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     text=True, encoding="utf-8", errors="replace", bufsize=1)

def drain_stderr():
    for line in p.stderr:
        line = line.rstrip()
        if line:
            print("[stderr]", line[:300], flush=True)

threading.Thread(target=drain_stderr, daemon=True).start()

def send(obj):
    try:
        p.stdin.write(json.dumps(obj) + "\n"); p.stdin.flush()
    except Exception as e:
        print("SEND FAIL:", e)

msgs, chunks = [], []
auth_done, sess_done, prompt_done = threading.Event(), threading.Event(), threading.Event()
sid_holder = {}

def reader():
    for line in p.stdout:
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        msgs.append(msg)
        if msg.get("method") == "session/update":
            u = msg.get("params", {}).get("update", {})
            if u.get("sessionUpdate") == "agent_message_chunk":
                t = u.get("content", {}).get("text")
                if t:
                    chunks.append(t)
        mid = msg.get("id")
        if mid == 2:
            auth_done.set()
        elif mid == 3:
            if "result" in msg:
                sid_holder["sid"] = msg["result"].get("sessionId") or msg["result"].get("session_id")
            sess_done.set()
        elif mid == 4:
            prompt_done.set()

threading.Thread(target=reader, daemon=True).start()

send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{}}})
time.sleep(3)

send({"jsonrpc":"2.0","id":2,"method":"authenticate","params":{"methodId":METHOD}})
auth_done.wait(timeout=40)
auth_msg = next((m for m in msgs if m.get("id") == 2), None)
info = ((auth_msg or {}).get("result") or {}).get("_meta", {}).get("codebuddy.ai/userinfo")
if info:
    print("认证: 成功  账号=%s  类型=%s" % (info.get("userName"), info.get("authType")))
elif auth_msg and "error" in auth_msg:
    print("认证: 失败", json.dumps(auth_msg["error"], ensure_ascii=False))
else:
    url = next((m["params"].get("authUrl") for m in msgs if m.get("method") == "_codebuddy.ai/authUrl"), None)
    print("认证: 未完成，需登录 ->", url)

send({"jsonrpc":"2.0","id":3,"method":"session/new","params":{"cwd":CWD,"mcpServers":[]}})
sess_done.wait(timeout=120)
print("SESSION:", sid_holder.get("sid"))

if not sid_holder.get("sid"):
    err = next((m for m in msgs if m.get("id") == 3), None)
    print("session/new 响应:", json.dumps(err, ensure_ascii=False)[:300] if err else "无响应")
    print("== 已收到的全部消息 ==")
    for m in msgs:
        print("   ", json.dumps(m, ensure_ascii=False)[:250])
else:
    send({"jsonrpc":"2.0","id":4,"method":"session/prompt",
          "params":{"sessionId":sid_holder["sid"],"prompt":[{"type":"text","text":PROMPT}]}})
    prompt_done.wait(timeout=180)
    print("== 回答 ==")
    print("".join(chunks)[:800])

p.kill()
