#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
memorix MCP 探针（stdio / newline-delimited JSON-RPC）

用途：
  1) 单实例冒烟：确认 memorix serve 能起来、tools/list 返回哪些工具。
  2) 并发测试：同时起 N 个实例指向同一个 --cwd（同一个 memorix.db），
     验证 SQLite 多进程读写不会互相抢锁失败。
     —— 这一点很重要：WorkBuddy / Claude Code / DSH / 桥接 WorkBuddy 四个 agent
        在微信里可能同时被 @，会各自拉起一个 memorix 子进程。

用法：
  python memorix_probe.py                # 单实例，默认 3 个并发
  python memorix_probe.py 5              # 5 个并发
  python memorix_probe.py 1              # 只跑 1 个（冒烟）

说明：本脚本是「只读探针」，只做 initialize + tools/list，不写任何记忆。
"""

import json
import os
import subprocess
import sys
import threading
import time

# 路径不写死用户名：默认取当前用户的家目录（本机解析结果与写死时完全一致），
# 换机器时用环境变量 CC_HOME / CC_NODE 覆盖即可。
HOME = (os.environ.get("CC_HOME") or os.path.expanduser("~")).replace("\\", "/")
NODE = os.environ.get("CC_NODE") or HOME + "/.workbuddy/binaries/node/versions/22.22.2-2/node.exe"
CLI = r"D:/npm-global/node_modules/memorix/dist/cli/index.js"
CWD = r"D:/memorix-shared"


def send(proc, obj):
    proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
    proc.stdin.flush()


def one_round(tag, results):
    """一个 memorix 实例：initialize -> tools/list -> 退出。"""
    try:
        p = subprocess.Popen(
            [NODE, CLI, "serve", "--mode", "lite", "--cwd", CWD],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except Exception as e:
        results[tag] = {"ok": False, "err": f"spawn failed: {e}"}
        return

    send(p, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "memorix-probe", "version": "1"}}})
    send(p, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    send(p, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})

    info, tools, bad = None, None, []
    deadline = time.time() + 45          # memorix 建索引可能十几秒
    while time.time() < deadline and (info is None or tools is None):
        line = p.stdout.readline()
        if not line:
            break
        try:
            m = json.loads(line.decode("utf-8", "replace"))
        except Exception:
            continue
        if m.get("id") == 1:
            r = m.get("result") or {}
            info = (r.get("serverInfo") or {}).get("version")
            if m.get("error"):
                bad.append("initialize: " + json.dumps(m["error"], ensure_ascii=False))
        elif m.get("id") == 2:
            if m.get("error"):
                bad.append("tools/list: " + json.dumps(m["error"], ensure_ascii=False))
            else:
                tools = [t.get("name") for t in (m.get("result") or {}).get("tools", [])]

    try:
        p.kill()
    except Exception:
        pass

    results[tag] = {
        "ok": info is not None and tools is not None and not bad,
        "version": info,
        "tool_count": len(tools) if tools is not None else None,
        "tools": tools or [],
        "errors": bad,
    }


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(f"== memorix 并发探针：{n} 个实例 / 同一个 --cwd {CWD} ==\n")
    results = {}
    threads = []
    t0 = time.time()
    for i in range(n):
        t = threading.Thread(target=one_round, args=(f"#{i+1}", results))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    elapsed = time.time() - t0

    ok_cnt = 0
    for tag in sorted(results):
        r = results[tag]
        if r.get("ok"):
            ok_cnt += 1
            print(f"[{tag}] PASS  version={r['version']}  tools={r['tool_count']}")
        else:
            print(f"[{tag}] FAIL  version={r.get('version')} tools={r.get('tool_count')}")
            for e in r.get("errors", []):
                print(f"        {e}")

    # 打印第一个实例的工具名，便于核对
    first = results.get("#1") or {}
    if first.get("tools"):
        print("\n工具清单（实例 #1）：")
        for name in first["tools"]:
            print("  -", name)

    print(f"\n结果：{ok_cnt}/{n} 个实例正常，总耗时 {elapsed:.1f}s")
    return 0 if ok_cnt == n else 1


if __name__ == "__main__":
    sys.exit(main())
