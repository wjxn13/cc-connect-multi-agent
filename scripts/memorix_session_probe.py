#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
memorix 会话身份探针：验证 agent 怎么「拿到一个可被寻址的 ID」。

为什么需要：
  人机共聊方案里，人要能把话投给「某个 agent」。但 agent 默认在协调系统里
  没有身份 —— team_manage status 里只会出现显式 join 过的成员。
  官方文档提过 `session_start with joinTeam=true`，本脚本就是验证这条路。

  只有身份存在，人才能定向留言；agent 也只有 join 过，poll 才会返回
  「[AGENT] You: xxx (active)」这一段。

用法：
  python memorix_session_probe.py
"""

import json
import sys
import time

from memorix_team_probe import Mcp, text_of

MODE = "team"


def main():
    m = Mcp(MODE)
    m.init()
    tools = {t.get("name"): (t.get("inputSchema") or {}) for t in m.tools()}

    print("== memorix_session_start 入参 schema ==")
    sch = tools.get("memorix_session_start")
    if not sch:
        print("  该工具不存在！")
    else:
        print(f"  required={sch.get('required') or []}")
        for pn, pv in (sch.get("properties") or {}).items():
            enum = pv.get("enum")
            desc = (pv.get("description") or "").replace("\n", " ")[:100]
            line = f"    - {pn}: {pv.get('type')}"
            if enum:
                line += f" enum={enum}"
            print(line + f"   {desc}")

    # joinTeam 是否被支持？
    props = (sch or {}).get("properties") or {}
    if "joinTeam" in props:
        print("\n  -> 支持 joinTeam，正是要验证的开关")
    else:
        print("\n  -> 没有 joinTeam 参数；改看是否 session_start 自带 join 行为")

    # 踩坑（实测）：只传 joinTeam=true 会被静默跳过，返回
    #   "Coordination join skipped: pass `agent` or `agentType` to create a coordination identity."
    # 必须同时给 agent / agentType（建议再给 instanceId 让身份跨重启稳定）。
    print("\n== 调用 memorix_session_start（joinTeam=true + agent/agentType）==")
    args = {}
    if "joinTeam" in props:
        args["joinTeam"] = True
    args["agent"] = "memorix-session-probe"
    args["agentType"] = "probe"
    args["instanceId"] = "memorix-session-probe-20260914"
    r = m.call("memorix_session_start", args)
    print(text_of(r)[:1200] or json.dumps(r, ensure_ascii=False)[:1200])

    print("\n== 随后 poll（不带 agentId）看是否出现 [AGENT] You ==")
    r = m.call("memorix_poll", {"markInboxRead": False})
    print(text_of(r)[:2000] or json.dumps(r, ensure_ascii=False)[:2000])

    print("\n== 收尾：退出协调身份，别在团队列表留垃圾 ==")
    r = m.call("team_manage", {"action": "status"})
    st = text_of(r)
    print(st[:600])
    r = m.call("team_manage", {"action": "leave", "agentId": "memorix-session-probe-20260914"})
    print("\n[leave by instanceId] " + (text_of(r)[:300] or "(空)"))

    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
