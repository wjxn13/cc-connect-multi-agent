# hooks/ —— L2 前置拦截钩子（多平台）

在 **agent 调用模型之前** 拦掉「这条消息点名的是别人」的情况，省掉那次 LLM 调用。

```
homedir/
├── cc-connect/hooks/
│   ├── l2-userpromptsubmit.js      ← 钩子本体（本目录的同名文件）
│   ├── workbuddy-settings.json     ← 给 WorkBuddy 用
│   └── dsh-hooks.json              ← 给 DSH 用
```

## 替换占位符

两个 JSON 里的 `command` 各含两个占位符，装之前必须替换（**注意引号规则，见下**）：

| 占位符 | 换成 |
|---|---|
| `<NODE_EXE>` | node 可执行文件的绝对路径 |
| `<HOOK_JS>` | `l2-userpromptsubmit.js` 的绝对路径 |

## 挂载方式（三家不一样）

### Claude Code（`claudecode` project）

钩子装在自己的 settings 里，用 **环境变量门控**（因为这份 settings 是全局的，不加门控会连你自己开的会话一起拦）：

```json
{ "hooks": { "UserPromptSubmit": [ { "hooks": [ {
  "type": "command",
  "command": "\"<NODE_EXE>\" \"<HOOK_JS>\" --agent claudecode",
  "timeout": 10
} ] } ] } }
```

再给桥接侧注入 `CC_BRIDGE_L2=1`（在 `[[projects.agent.options.env]]` 里）。
`claudecode` 档案的 `requireGate = true`，没有这个变量就一律放行。

### WorkBuddy（`my-workbuddy` project）

用 **`--settings` 附加设置文件**（只作用于桥接进程，天然隔离，不需要门控）：

在 cc-connect 的 `args` 末尾追加两项：

```
"--settings", "<homedir>/.cc-connect/hooks/workbuddy-settings.json"
```

见本目录 `workbuddy-settings.json`。

### DSH（`my-dsh` project）

在 `~/.dsh/profiles/acp/cordis.patch.yml` 末尾追加（**`--profile acp` 专属，不影响 web / tui**）：

```yaml
- insert:
    - id: cc-bridge-l2
      name: '@deepseek-ai/dsh-hooks-claude-code'
      config:
        configPath: <homedir>/.cc-connect/hooks/dsh-hooks.json
```

见本目录 `dsh-hooks.json`。

> 新增插件必须用 `- insert:` 语法；`- id:` 只能修改**已有**条目。
> `configPath` 在进程启动时解析一次（`patchReload: "startup"`），**改完必须重启**。

## ⚠️ 唯一的格式差异：DSH 那份**不能带引号**

| 文件 | `command` 里能不能有引号 |
|---|---|
| `workbuddy-settings.json` | **必须带** —— hook 命令经 shell 执行，路径含空格时必须引起来 |
| `dsh-hooks.json` | **绝对不能带** —— DSH 执行 hook 时会自行加前缀且不做 shell 引号转义，引号会被 PowerShell 当字符串字面量，报「表达式或语句中包含意外的标记」 |

所以 DSH 那条要求 `<NODE_EXE>` 与 `<HOOK_JS>` 的路径**都不含空格**（Windows 默认安装位置通常满足）。

## 验证

```bash
# 离线回归测试（31 例，不启动任何 agent、不花 token）
python scripts/l2_hook_tests.py
```

想验证「真的拦住了」，判据不是钩子自己的日志，而是**读 agent 侧的会话记录**：
DSH 看 `hook/result` 事件（`{exitCode, decision}`）+ 同轮 `request/header` 是否为 0。

## 排查用：启动探针

脚本在**读 stdin 之前**会往 `l2-hook-start.jsonl` 追加一行（与正式日志分开，
免得污染「`l2-hook.jsonl` 行数」这个实验口径）。它把两种可能一刀切开：

| 探针出现了吗 | 含义 | 该往哪查 |
|---|---|---|
| **没有** | 平台**压根没执行**这条 hook | 配置 / 信任 / 启动方式 |
| **出现了** | 执行了，但卡在 stdin 或 JSON 解析 | hook 协议 |

行里同时记了 `agent`（`--agent` 或 `CC_BRIDGE_AGENT` 报出的平台）、`gated`
（`CC_BRIDGE_L2` 是否 =1）、`pid`、`stdinIsTTY`。三个 agent 共用这份脚本时，
这一行还能直接回答「谁真的调了 hook、谁没调」。

> ⚠️ `gated=False` **不代表配错了**：`workbuddy` / `dsh` 两家的 `requireGate` 本来就是 `false`
> ——它们的钩子只注入给桥接进程，天然隔离，再加门控反而会因 env 漏配而**静默失效**。
> 只有挂在**全局** settings 里的 `claudecode` 需要 `true`。详见 `docs/07` 第五节。

日志默认落在 `~/.cc-connect/logs/`（用 `os.homedir()` 取，**不写死用户名**，方便换机器复现），
可用 `CC_BRIDGE_L2_LOG` / `CC_BRIDGE_L2_START_LOG` 覆盖。

## 平台分支速查

| 平台 | 有效阻断写法 | 备注 |
|---|---|---|
| Claude Code | stdout `{"decision":"block"}` / `{"continue":false}` | `exit 2` 无效 |
| WorkBuddy | stdout JSON / `{"continue":false}` / `exit 2` 三者都认 | |
| DSH | stdout `{"decision":"block"}` | `exit 2` 在 Windows 上被 PowerShell 改写成 1；`{"continue":false}` 完全不阻断 |

完整机理与实测报文见 `docs/06-L2前置拦截-三平台落地报告.md`。

> **本层（L2）在整体中的位置**：现在共有三道防线，L2 是第二道。比它更早的是**引擎层点名关卡（L1）**，
> 比它更晚的是**输出静默约定（L3）**。三者是**叠加**关系，L1 落地后 L2 并未被取代——
> 详见 `docs/07-点名路由与三层防线-落地报告.md` 第五节。
