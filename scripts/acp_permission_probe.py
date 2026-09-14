#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ACP 权限请求探针 —— 判断某个 ACP agent 在调用工具时会不会「向客户端要权限」。

背景（2026-09-14 实链路踩坑）：
  桥接的 WorkBuddy agent 收到 @wb 消息后要 grep 工作区外的目录，于是按 ACP 协议发出
  session/request_permission 给 cc-connect。桥接是无值守的（没人点「允许」），
  这个回合就永久挂起 —— 会话记录里只剩 [user]、没有 assistant，微信侧表现为「完全没回」。

用法：
  python acp_permission_probe.py "运行 pwd 告诉我当前目录"          # 探 DSH
  python acp_permission_probe.py --cb "运行 pwd 告诉我当前目录"     # 探桥接用的 CodeBuddy CLI

输出：
  - 收到的所有 JSON-RPC method 统计（重点看有没有 session/request_permission）
  - 权限请求次数与判定结论

注意：脚本会把权限请求自动应答为「允许」，才能观察到后续行为；
      仅用于本地诊断，不要把这份应答逻辑搬进生产桥接。
"""

import json
import subprocess
import sys
import threading
import time

NODE = r"C:/Users/<USER>/.workbuddy/binaries/node/versions/22.22.2-2/node.exe"
DSH = r"D:/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js"
CODEBUDDY = r"D:/workbudy/WorkBuddy/resources/app.asar.unpacked/cli/bin/codebuddy"
CWD_DSH = r"C:/Users/<USER>"
CWD_CB = r"C:/Users/<USER>/wb-agent"


def main():
    argv = sys.argv[1:]
    use_cb = "--cb" in argv
    argv = [a for a in argv if a != "--cb"]
    prompt = argv[0] if argv else "运行 pwd 命令，告诉我当前工作目录是什么"

    mcp_json = ('{"mcpServers":{"memorix":{"command":"' + NODE +
                '","args":["D:/npm-global/node_modules/memorix/dist/cli/index.js",'
                '"serve","--mode","lite","--cwd","D:/memorix-shared"]}}}')

    if use_cb:
        cmd = [NODE, CODEBUDDY, "--acp", "--acp-transport", "stdio",
               "--permission-mode", "bypassPermissions", "--mcp-config", mcp_json]
        cwd = CWD_CB
        label = "CodeBuddy CLI（桥接用）"
    else:
        cmd = [NODE, DSH, "--profile", "acp"]
        cwd = CWD_DSH
        label = "DSH"

    print(f"== ACP 权限探针：{label} ==")
    print(f"   prompt: {prompt}\n")

    p = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, encoding="utf-8",
                         errors="replace", bufsize=1)

    def send(o):
        try:
            p.stdin.write(json.dumps(o) + "\n")
            p.stdin.flush()
        except Exception:
            pass

    methods = {}
    permission_events = []
    text_parts = []
    session_id = {"v": None}
    prompt_done = threading.Event()

    def handle(m):
        meth = m.get("method")
        if meth:
            methods[meth] = methods.get(meth, 0) + 1
            if "permission" in meth:
                permission_events.append(m)
                rid = m.get("id")
                if rid is not None:
                    send({"jsonrpc": "2.0", "id": rid,
                          "result": {"outcome": {"outcome": "selected",
                                                 "optionId": "allow_once"}}})
            if meth == "session/update":
                u = m.get("params", {}).get("update", {})
                if u.get("sessionUpdate") == "agent_message_chunk":
                    c = u.get("content") or {}
                    text_parts.append(str(c.get("text", "")))
            return
        # 响应
        rid = m.get("id")
        if rid == 2:
            r = m.get("result") or {}
            session_id["v"] = r.get("sessionId")
            if session_id["v"]:
                threading.Thread(target=fire_prompt, daemon=True).start()
        elif rid == 100:
            prompt_done.set()

    def fire_prompt():
        print(f"   sessionId = {session_id['v']}，20 秒后发 prompt（等 MCP 就绪）...")
        time.sleep(20)
        send({"jsonrpc": "2.0", "id": 100, "method": "session/prompt",
              "params": {"sessionId": session_id["v"],
                         "prompt": [{"type": "text", "text": prompt}]}})

    def reader():
        for line in p.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                handle(json.loads(line))
            except Exception:
                continue
        prompt_done.set()

    threading.Thread(target=reader, daemon=True).start()

    send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                     "clientInfo": {"name": "perm-probe", "version": "1"}}})
    send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    send({"jsonrpc": "2.0", "id": 2, "method": "session/new",
          "params": {"cwd": cwd, "mcpServers": []}})

    prompt_done.wait(timeout=180)
    time.sleep(2)
    try:
        p.kill()
    except Exception:
        pass

    print("\n收到的 JSON-RPC method 统计：")
    if not methods:
        print("  （没收到任何 method —— 握手可能失败）")
    for k, v in sorted(methods.items(), key=lambda x: -x[1]):
        mark = "   <<< 权限请求" if "permission" in k else ""
        print(f"  {v:>3}x {k}{mark}")

    body = "".join(text_parts).strip()
    print(f"\n正文回复（{len(body)} 字符）：{body[:400]}")

    print(f"\n权限请求次数：{len(permission_events)}")
    if permission_events:
        print("  判定：该 agent 【会】向客户端要权限 → 无值守桥接必须设全自动模式")
    else:
        print("  判定：本次未观察到权限请求（该操作可能已被默认允许，"
              "或已由 --permission-mode 拦截）")


if __name__ == "__main__":
    sys.exit(main())
