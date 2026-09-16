#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
memorix team 档功能探针（stdio / newline-delimited JSON-RPC）

背景：
  memorix 的 serve 只有 stdio 一种传输，但工具档位有四档
  （micro / lite / team / full）。官方文档里 lite 明确写着
  "without team tools" —— 也就是说「发消息 / 收件箱 / poll / 看板」
  这些协调工具只在 team / full 档才存在。

  人机共聊方案（memorix-chat）的核心动作是：
    ① 人把话写进共享记忆的收件箱（human-wjx -> agent）
    ② agent 干活前 poll 一下，看到就回应
  这两步都依赖协调工具。所以「四个 agent 的 MCP 配置从 lite 改 team」
  是方案能否落地的前置条件。

  本脚本就是验证这个前置条件：
    A. team 档到底多出哪些工具（对比 lite）
    B. 这些工具在 stdio 下是否真的能调通（不需要额外起 HTTP 服务）
    C. 三个关键工具的入参 schema 长什么样（后续写规约要用）

用法：
  python memorix_team_probe.py            # 只做 A + C（只读，不改数据）
  python memorix_team_probe.py --call     # 额外做 B：真发一条测试消息 + poll
"""

import json
import os
import subprocess
import sys
import time

# 路径不写死用户名：默认取当前用户的家目录（本机解析结果与写死时完全一致），
# 换机器时用环境变量 CC_HOME / CC_NODE 覆盖即可。
HOME = (os.environ.get("CC_HOME") or os.path.expanduser("~")).replace("\\", "/")
NODE = os.environ.get("CC_NODE") or HOME + "/.workbuddy/binaries/node/versions/22.22.2-2/node.exe"
CLI = r"D:/npm-global/node_modules/memorix/dist/cli/index.js"
CWD = r"D:/memorix-shared"

KEY_TOOLS = ["team_manage", "team_message", "memorix_poll", "memorix_handoff", "team_task"]


class Mcp:
    def __init__(self, mode):
        self.p = subprocess.Popen(
            [NODE, CLI, "serve", "--mode", mode, "--cwd", CWD],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.nid = 0

    def send(self, method, params=None):
        self.nid += 1
        msg = {"jsonrpc": "2.0", "id": self.nid, "method": method}
        if params is not None:
            msg["params"] = params
        self.p.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.p.stdin.flush()
        return self.nid

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.p.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.p.stdin.flush()

    def wait(self, want, timeout=60):
        """读 stdout 直到收齐 want 里的 id（或超时）。返回 {id: msg}"""
        got = {}
        want = set(want)
        deadline = time.time() + timeout
        while time.time() < deadline and not want.issubset(set(got)):
            line = self.p.stdout.readline()
            if not line:
                break
            try:
                m = json.loads(line.decode("utf-8", "replace"))
            except Exception:
                continue
            if isinstance(m.get("id"), int):
                got[m["id"]] = m
        return got

    def init(self):
        i = self.send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                     "clientInfo": {"name": "memorix-team-probe", "version": "1"}})
        self.notify("notifications/initialized", {})
        self.wait([i], timeout=60)
        return i

    def tools(self):
        i = self.send("tools/list", {})
        return (self.wait([i], timeout=60).get(i) or {}).get("result", {}).get("tools", []) or []

    def call(self, name, args, timeout=60):
        i = self.send("tools/call", {"name": name, "arguments": args})
        return self.wait([i], timeout=timeout).get(i) or {"error": "timeout"}

    def close(self):
        try:
            self.p.kill()
        except Exception:
            pass


def text_of(msg):
    """把 tools/call 的结果压成一行文本，便于打印。"""
    r = (msg.get("result") or {})
    parts = []
    for c in (r.get("content") or []):
        if c.get("type") == "text":
            parts.append(c.get("text") or "")
    if not parts and msg.get("error"):
        return "ERROR " + json.dumps(msg["error"], ensure_ascii=False)
    return "\n".join(parts).strip()


def main():
    do_call = "--call" in sys.argv

    # ---------- A. team 档工具清单 ----------
    print("== A. 各档位工具数量对比 ==")
    counts = {}
    for mode in ("lite", "team"):
        m = Mcp(mode)
        m.init()
        t = m.tools()
        counts[mode] = [x.get("name") for x in t]
        m.close()
        print(f"  --mode {mode:<5} -> {len(counts[mode])} 个工具")

    lite, team = set(counts.get("lite") or []), set(counts.get("team") or [])
    extra = sorted(team - lite)
    print(f"\n  team 档比 lite 档多出 {len(extra)} 个工具：")
    for n in extra:
        print("   +", n)
    missing = sorted(lite - team)
    if missing:
        print(f"  (lite 有而 team 没有的：{missing})")

    # ---------- C. 关键工具入参 schema ----------
    print("\n== C. 关键工具入参 schema（写规约时要照这个填）==")
    m = Mcp("team")
    m.init()
    tools = m.tools()
    schemas = {x.get("name"): (x.get("inputSchema") or {}) for x in tools}
    for name in KEY_TOOLS:
        if name not in schemas:
            print(f"\n[{name}] 不存在于 team 档！")
            continue
        sch = schemas[name]
        props = sch.get("properties") or {}
        req = sch.get("required") or []
        print(f"\n[{name}]  required={req}")
        for pname, pv in props.items():
            t = pv.get("type")
            enum = pv.get("enum")
            desc = (pv.get("description") or "").replace("\n", " ")[:110]
            line = f"    - {pname}: {t}"
            if enum:
                line += f" enum={enum}"
            line += f"   {desc}"
            print(line)

    # ---------- B. 真调用 ----------
    if do_call:
        print("\n== B. 真调用（会往共享记忆写一条测试消息）==")

        # B1. 先看当前协调状态
        r = m.call("team_manage", {"action": "status"})
        print("\n[B1 team_manage action=status]")
        print(text_of(r)[:1500] or "(空)")

        # B2. 本会话必须显式 join，否则 team_message 会报
        #     "active session identity ... required for send"（实测踩到）
        r = m.call("team_manage", {
            "action": "join",
            "name": "team-probe",
            "agentType": "probe",
            "instanceId": "team-probe-20260914",
        })
        print("\n[B2 team_manage action=join name=team-probe]")
        joined = text_of(r)
        print(joined[:900] or json.dumps(r, ensure_ascii=False)[:900])

        # join 返回的是完整 UUID（形如 "Joined ... (ID: xxxxxxxx-xxxx-...)"），
        # 而 team status 里显示的是 8 位短 ID —— 两者不是一回事，别按短 ID 去猜。
        import re
        mo = re.search(r"ID:\s*([0-9a-fA-F-]{36})", joined)
        aid = mo.group(1) if mo else None
        print(f"  -> 解析到的 agentId = {aid}")

        # B3. 广播一条消息。
        #     踩坑：`from` 要「整个省略」才走本会话身份；显式传 None 会被
        #     schema 拒掉（data/from must be string）。type 是 send/broadcast 的必填项。
        args = {"action": "broadcast", "type": "announcement",
                "content": "[探针] team 档通路测试 @ 2026-09-14"}
        if aid:
            args["from"] = aid
        r = m.call("team_message", args)
        print("\n[B3 team_message action=broadcast type=announcement]")
        print(text_of(r)[:900] or json.dumps(r, ensure_ascii=False)[:900])

        # B4. poll 一次，看能不能把协调状态读回来（这是 agent 开工前的动作）
        pargs = {"markInboxRead": False}
        if aid:
            pargs["agentId"] = aid
        r = m.call("memorix_poll", pargs)
        print("\n[B4 memorix_poll markInboxRead=false]")
        print(text_of(r)[:2500] or json.dumps(r, ensure_ascii=False)[:2500])

        # B5. 收尾：把探针身份退掉，别在团队列表里留垃圾
        if aid:
            r = m.call("team_manage", {"action": "leave", "agentId": aid})
            print("\n[B5 team_manage action=leave]")
            print(text_of(r)[:600] or json.dumps(r, ensure_ascii=False)[:600])

    m.close()
    print("\n完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
