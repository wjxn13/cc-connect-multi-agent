# -*- coding: utf-8 -*-
"""
CodeBuddy / WorkBuddy 独立 CLI 登录器（带日志，便于排障）

原理：以 ACP 协议启动 codebuddy CLI，调用 authenticate 方法，
      CLI 会推一条 `_codebuddy.ai/authUrl` 通知，内含设备授权链接。
      自动用默认浏览器打开 → 你在浏览器完成登录 → CLI 拿到授权 →
      凭证写入配置目录。

日志：<配置目录>/login.log（全量记录协议消息，便于事后核验）

环境变量（可选）：
  CBC_CLI           指定 CLI 入口，默认用 WorkBuddy 内嵌版（连 workbuddy.cn 账号体系）
  CBC_CONFIG_DIR    指定配置目录，默认 ~/.codebuddy
  CBC_AUTH_METHOD   iOA | external | internal(微信) | selfhosted，默认 internal
  CBC_WAIT_SECONDS  等待登录的秒数，默认 900
"""
import subprocess, json, threading, time, os, sys, webbrowser

NODE = r"C:/Users/<USER>/.workbuddy/binaries/node/versions/22.22.2-2/node.exe"
# 默认用 WorkBuddy 内嵌版 CLI：它对应 www.workbuddy.cn 账号体系
DEFAULT_CBC = r"D:/workbudy/WorkBuddy/resources/app.asar.unpacked/cli/bin/codebuddy"
CBC = os.environ.get("CBC_CLI") or DEFAULT_CBC
CONFIG_DIR = os.environ.get("CBC_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".codebuddy")
METHOD = os.environ.get("CBC_AUTH_METHOD", "internal")
WAIT_SECONDS = int(os.environ.get("CBC_WAIT_SECONDS", "900"))
LOG_PATH = os.path.join(CONFIG_DIR, "login.log")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def log(msg):
    line = str(msg)
    print(line, flush=True)
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


log("=" * 64)
log("  CodeBuddy CLI 登录器   开始时间 " + time.strftime("%Y-%m-%d %H:%M:%S"))
log("  CLI 入口   : " + CBC)
log("  配置目录   : " + CONFIG_DIR)
log("  日志文件   : " + LOG_PATH)
log("=" * 64)

if not os.path.isfile(CBC):
    log("[错误] 找不到 CLI 入口，请检查路径。")
    sys.exit(1)

env = dict(os.environ)
env.pop("NODE_OPTIONS", None)
env.pop("ELECTRON_RUN_AS_NODE", None)
env["CODEBUDDY_CONFIG_DIR"] = CONFIG_DIR      # 显式锁定配置目录
os.makedirs(CONFIG_DIR, exist_ok=True)

log("[1/4] 启动 CLI（ACP 模式）...")
proc = subprocess.Popen(
    [NODE, CBC, "--acp", "--acp-transport", "stdio"],
    cwd=os.path.expanduser("~"), env=env,
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding="utf-8", errors="replace", bufsize=1,
)

state = {"url": None, "auth_err": None, "session_ok": False, "session_err": None}
authed = threading.Event()


def send(obj):
    try:
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()
    except Exception as e:
        log("  [发送失败] %s" % e)


def watch(stream, is_err=False):
    for line in stream:
        line = line.rstrip()
        if not line:
            continue
        if is_err:
            low = line.lower()
            if "error" in low or "fail" in low or "denied" in low:
                log("  [CLI stderr] " + line[:300])
            continue
        try:
            msg = json.loads(line)
        except Exception:
            log("  [非JSON] " + line[:200])
            continue
        log("  [recv] " + json.dumps(msg, ensure_ascii=False)[:500])
        method = msg.get("method")
        mid = msg.get("id")
        if method == "_codebuddy.ai/authUrl":
            url = (msg.get("params") or {}).get("authUrl")
            if url and not state["url"]:
                state["url"] = url
                log("")
                log("[2/4] 已获取登录链接：")
                log("      " + url)
                log("")
                try:
                    webbrowser.open(url)
                    log("      已自动打开浏览器；若未打开，请手动复制上面链接访问。")
                except Exception:
                    log("      浏览器未能自动打开，请手动复制上面链接访问。")
        elif mid == 2:
            if "error" in msg:
                state["auth_err"] = json.dumps(msg["error"], ensure_ascii=False)
            log("      >> authenticate 响应：" + json.dumps(msg, ensure_ascii=False)[:300])
            authed.set()
        elif mid == 3:
            if "error" in msg:
                state["session_err"] = json.dumps(msg["error"], ensure_ascii=False)
            else:
                state["session_ok"] = True
            log("      >> session/new 响应：" + json.dumps(msg, ensure_ascii=False)[:300])


threading.Thread(target=watch, args=(proc.stdout,), daemon=True).start()
threading.Thread(target=watch, args=(proc.stderr, True), daemon=True).start()

send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
      "params": {"protocolVersion": 1, "clientCapabilities": {}}})
time.sleep(4)
log("[2/4] 发起认证（方式：%s，微信登录即 internal）..." % METHOD)
send({"jsonrpc": "2.0", "id": 2, "method": "authenticate", "params": {"methodId": METHOD}})
log("      等待你在浏览器中完成登录（最多 %d 秒）..." % WAIT_SECONDS)

deadline = time.time() + WAIT_SECONDS
while time.time() < deadline and not authed.is_set():
    time.sleep(1)

log("")
if state["auth_err"]:
    log("[失败] 认证被拒绝：" + state["auth_err"])
elif authed.is_set():
    log("[3/4] 认证流程已返回。正在验证会话可用性 ...")
    send({"jsonrpc": "2.0", "id": 3, "method": "session/new",
          "params": {"cwd": os.path.expanduser("~"), "mcpServers": []}})
    for _ in range(20):
        if state["session_ok"] or state["session_err"]:
            break
        time.sleep(1)
    log("")
    if state["session_ok"]:
        log("[4/4] 成功！ACP 会话已建立，凭证可用。")
        log("      >>> 可以关闭本窗口，回去告诉 AI『登录完成』。")
    else:
        log("[4/4] 会话仍未建立：" + str(state["session_err"]))
        log("      （说明浏览器登录未真正完成，或该链接未走完授权流程）")
else:
    log("[超时] 未在 %d 秒内完成登录，请重新运行本脚本。" % WAIT_SECONDS)

time.sleep(2)
try:
    proc.terminate()
except Exception:
    pass
log("")
log("日志已写入：" + LOG_PATH)
log("窗口可关闭。")
