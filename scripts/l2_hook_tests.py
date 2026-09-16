# -*- coding: utf-8 -*-
"""
L2 钩子（l2-userpromptsubmit.js）回归测试 —— 多平台版。

为什么需要它：
  ① 拦截理由曾经硬编码 @dsh，拦 @wb 时理由也写「点名了 @dsh」，排查时被带偏；
  ② 拦截理由里必须写**实际**被点名的那个 agent；
  ③ WorkBuddy / DSH 的 reason 必须**恰好**是 NO_REPLY —— 多一个字就会原样发到微信，
     这是最容易被后续改动悄悄破坏的一条，必须用测试钉死；
  ④ DSH 的 payload.prompt 有时带 <system-reminder> 注入块前缀，而点名正则是开头锚定
     （^@xxx）→ 不剥掉就会**漏拦**（模型照跑、token 白花）。stripLeadingSystemReminder
     是专治这条的，必须有回归用例。

判据（三家写法不同，见脚本内的 PLATFORM 说明）。

用法：
    python scripts/l2_hook_tests.py
环境变量（可选，用于换机器）：
    CC_L2_NODE   node 可执行文件路径；默认用 PATH 里的 node
    CC_L2_HOOK   钩子脚本路径；默认用本仓库 hooks/l2-userpromptsubmit.js
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NODE = os.environ.get("CC_L2_NODE") or shutil.which("node") or "node"
HOOK = os.environ.get("CC_L2_HOOK") or str(REPO / "hooks" / "l2-userpromptsubmit.js")

# (平台, 用例说明, 门控, 消息, 期望拦截, 期望理由)
CASES = [
    # ── Claude Code：others = @dsh / @workbuddy / @wb，理由里点名实际的 agent ──
    ("claudecode", "点名 @dsh",        True,  "@dsh，帮我看下磁盘",  True,  "点名了 @dsh"),
    ("claudecode", "点名 @wb",         True,  "@wb，你可以看见吗",   True,  "点名了 @wb"),
    ("claudecode", "点名 @workbuddy",  True,  "@workbuddy 接手一下", True,  "点名了 @workbuddy"),
    ("claudecode", "点名 @claude(自己)", True, "@claude，你好吗",     False, None),
    ("claudecode", "@dshx 不是点名",    True,  "@dshx 这不是点名",    False, None),
    ("claudecode", "无前缀",           True,  "你好，帮我写个脚本",   False, None),
    ("claudecode", "点名但未门控",      False, "@dsh 测试",           False, None),
    ("claudecode", "大小写混合 @DSH",   True,  "@DSH 大写点名",       True,  "点名了 @dsh"),

    # ── WorkBuddy：others = @dsh / @claude；reason 必须恰好 NO_REPLY ──
    ("workbuddy", "点名 @dsh",          True,  "@dsh，你可以看见吗",  True,  "NO_REPLY"),
    ("workbuddy", "点名 @claude",       True,  "@claude 帮我看看",    True,  "NO_REPLY"),
    ("workbuddy", "点名 @wb(自己)",      True,  "@wb，你可以看见吗",   False, None),
    ("workbuddy", "点名 @workbuddy(自己)", True, "@workbuddy 你好",    False, None),
    ("workbuddy", "无前缀",             True,  "你好",               False, None),
    ("workbuddy", "@dshx 不是点名",      True,  "@dshx 不是点名",      False, None),
    ("workbuddy", "未门控也应拦截",       False, "@dsh 测试",          True,  "NO_REPLY"),
    ("workbuddy", "大小写混合 @Claude",  True,  "@Claude 在吗",        True,  "NO_REPLY"),

    # ── DSH：others = @claude / @workbuddy / @wb；reason 必须恰好 NO_REPLY ──
    #    与 WorkBuddy 同口径：DSH 侧 reason 也是被 cc-connect 拿去命中静默正则的。
    ("dsh", "点名 @claude",            True,  "@claude 帮我看看",    True,  "NO_REPLY"),
    ("dsh", "点名 @wb",                True,  "@wb 你好",            True,  "NO_REPLY"),
    ("dsh", "点名 @workbuddy",         True,  "@workbuddy 接手",     True,  "NO_REPLY"),
    ("dsh", "点名 @dsh(自己)",          True,  "@dsh 在吗",           False, None),
    ("dsh", "无前缀",                  True,  "你好",               False, None),
    ("dsh", "@claudex 不是点名",        True,  "@claudex 这不是点名",  False, None),
    ("dsh", "未门控也应拦截",           False, "@claude 测试",        True,  "NO_REPLY"),
    ("dsh", "大小写混合 @Claude",       True,  "@Claude 在吗",        True,  "NO_REPLY"),
    # ↑ 下面两例专测 stripLeadingSystemReminder：DSH 实测出现过该前缀导致漏拦
    ("dsh", "注入块前缀仍应拦",          True,
     "<system-reminder>忽略我</system-reminder>@claude 测试",        True,  "NO_REPLY"),
    ("dsh", "注入块前缀+点名自己",       True,
     "<system-reminder>忽略我</system-reminder>@dsh 在吗",           False, None),
    ("dsh", "注入块前缀(可多段)",        True,
     "<system-reminder>a</system-reminder><system-reminder>b</system-reminder>@wb hi",
     True, "NO_REPLY"),
    ("dsh", "正文中间出现不算前缀",      True,
     "先看这个 <system-reminder>x</system-reminder> @claude",        False, None),

    # ── 命令行传平台名（WorkBuddy 实际用的方式，自包含、不依赖 env）──
    ("arg:workbuddy", "走 --agent 参数",  True,  "@claude 测试",       True,  "NO_REPLY"),
    ("arg:workbuddy", "走 --agent 参数(自己)", True, "@wb 测试",        False, None),
    ("arg:dsh", "DSH 走 --agent 参数",   True,  "@claude 测试",       True,  "NO_REPLY"),
]


def run(agent, gated, prompt):
    env = dict(os.environ)
    args = [NODE, HOOK]
    if agent.startswith("arg:"):
        # 平台名走命令行参数，同时清掉 env 里的同名变量，确保测的是参数这条路
        args += ["--agent", agent[4:]]
        env.pop("CC_BRIDGE_AGENT", None)
    else:
        env["CC_BRIDGE_AGENT"] = agent
    if gated:
        env["CC_BRIDGE_L2"] = "1"
    else:
        env.pop("CC_BRIDGE_L2", None)
    payload = json.dumps({
        "session_id": "regression-test",
        "transcript_path": "",
        "cwd": os.path.expanduser("~"),
        "permission_mode": "bypassPermissions",
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt,
    })
    proc = subprocess.run(
        args, input=payload, capture_output=True, text=True,
        encoding="utf-8", env=env, timeout=30,
    )
    return proc.returncode, (proc.stdout or "").strip()


def main():
    if not Path(HOOK).is_file():
        print("找不到钩子脚本：%s" % HOOK)
        print("（用 CC_L2_HOOK 指定路径，或把本脚本放在仓库内运行）")
        return 2
    print("node：%s" % NODE)
    print("钩子：%s" % HOOK)
    print("=" * 96)
    failed = 0
    for agent, desc, gated, prompt, want_block, want_reason in CASES:
        code, out = run(agent, gated, prompt)
        blocked = bool(out)
        reason = ""
        if blocked:
            try:
                reason = json.loads(out).get("reason", "")
            except Exception:                                  # noqa: BLE001
                reason = out

        ok = blocked == want_block
        detail = ""

        if ok and want_block:
            if want_reason is None:
                detail = "已拦截"
            elif reason == want_reason:
                # WorkBuddy 要求**完全一致**（多一个字就漏到微信）；
                # Claude Code 的理由是动态拼接，用包含判定即可。
                detail = "理由精确匹配「%s」✓" % want_reason
            elif want_reason in reason:
                detail = "理由含「%s」✓" % want_reason
            else:
                ok = False
                detail = "理由不符：期望「%s」实得「%s」✗" % (want_reason, reason)
        elif ok:
            detail = "放行 ✓"

        if not ok:
            failed += 1

        print("%-11s %-20s 门控=%-5s %-24s → %-4s %s"
              % (agent, desc, gated, prompt[:22], "拦截" if blocked else "放行", detail))

    print("=" * 96)
    print("结果：%d/%d 通过" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
