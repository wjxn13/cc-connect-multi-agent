# memorix 人机共聊 · 0.2 协作规约 + 「主动 poll」成败点验证报告

- 时间：2026-09-14 23:00 – 23:50
- 承接：`memorix-chat-plan.md`（v1.0）与 `memorix-chat-verify-report.md` 的建议第 1、2 步
- 结论：**两步都已完成，成败点验证通过**；第 3 步（建聊天页）等你实测确认后再开工

---

## 一、一句话结论

「agent 会不会主动看收件箱」这个**全案地基，已经打通并且实测过了**。
但过程里发现一个原方案和验证报告都没想到的坑（MCP 冷启动），
所以规约的真实写法与最初设想不同 —— 关键差别是**必须带一条不依赖 MCP 的兜底命令**。

---

## 二、做了三件事

### 1. 档位修正：`lite` → `team`（原方案完全没提到的前置）

这是本轮**最关键的发现**。memorix 的 `serve` 有四档工具配置，
而官方对 `lite` 的定义是 **"20 tools, without team tools"** ——
也就是说**「发消息 / 收件箱 / poll / 看板」这些协调工具在 lite 档根本不存在**。

> 换句话说：方案里「人留言 → agent 主动看」这个核心机制，
> 在原来的 `--mode lite` 配置下**从物理上就不可能实现**。

实测工具表对比（新脚本 `memorix_team_probe.py`）：

| 档位 | 工具数 | 含协调工具 |
|---|---|---|
| lite（改前） | 20 | ❌ |
| team（改后） | 28 | ✅ |

team 档比 lite 多出的 8 个：
`memorix_poll`、`team_message`、`team_manage`、`team_task`、
`memorix_handoff`、`team_file_lock`、`memorix_dashboard`、`memorix_knowledge`

**已验证**：team 档在 stdio 下 join / broadcast / poll / leave **全部真跑通**，不需要额外起 HTTP 服务。

已改动的四处配置：

| 文件 | 备注 |
|---|---|
| `~/.claude.json` | Claude Code |
| `~/.workbuddy/mcp.json` | WorkBuddy 桌面版（**需重启桌面版才生效**） |
| `~/.dsh/cordis.patch.yml` | DSH |
| `~/.cc-connect/config.toml` | 桥接的 WorkBuddy（`--mcp-config` JSON 串） |

### 2. 协作规约写进五个规则文件

| 文件 | 读者 |
|---|---|
| `C:\Users\<USER>\CLAUDE.md` | Claude Code（桥接） |
| `C:\Users\<USER>\AGENTS.md` | DSH（桥接） |
| `C:\Users\<USER>\wb-agent\AGENTS.md` | 桥接的 WorkBuddy |
| `~/.claude/CLAUDE.md` | Claude Code（全局） |
| `~/.dsh/AGENTS.md` | DSH（全局） |

规约内容（与原有「微信路由协议」并存，互不冲突）：

1. 先按路由协议判断**该不该由你作答**；
2. **只有该你答的消息**才「先看收件箱」——优先 `memorix_poll`，
   若工具还没加载出来就用**兜底命令**（见下）；
3. 有 `human-wjx` 的未读留言 → 先办它，并在回复里说明「已收到共享记忆留言」；
4. 没有未读 → 平静继续，**不要**提「我查过收件箱」；
5. **例外**：被判定要回 `NO_REPLY` 的消息，一律不调任何工具。

### 3. 真链路验证（A/B）+ 回归测试

用 `acp_filter_test.py` 走**真实 DSH 链路**（和微信桥接同一条通道），同一句 prompt 对比：

| | 旧规约 | 新规约 |
|---|---|---|
| 第 1 个动作 | 直接列目录 | **先查收件箱** |
| 回复开头 | 无 | **「已收到共享记忆留言 INBOX-TEST-0914，协作规约生效。」** |

回归测试 `@claude，你好`：仍然**恰好 `NO_REPLY`、且零工具调用** ——
点名制静默没被破坏。

---

## 三、意外发现：失败不是因为「模型不听话」，而是 MCP 冷启动

第一次测试**失败**了（agent 完全没看收件箱）。挖下去发现真正原因很反直觉。

我把 DSH 的会话记录解压出来（`.jsonl.zstd` 是**多帧拼接**的，Node 自带的解压只出第一帧，
写了个按魔数逐帧切的脚本才对），对比两次 LLM 请求的工具表：

| 轮次 | 工具总数 | memorix 工具 |
|---|---|---|
| 第 1 次请求 | 39 | **0** |
| 第 2 次请求 | 67 | **28** |

而模型自己的推理原文是：

> *"per workspace instructions I should first poll the memorix inbox ...
> but do I have memorix tools? The tool list shows no memorix\_\* tools."*

**结论：规约它读懂了、也打算照做，只是那一刻 memorix 的工具还没注册进工具表。**
memorix 启动要建索引，比 argo 那类 MCP 慢（实测 6 秒起步），
而第一条消息进来时是**第一次** LLM 请求 —— 正好赶在它连上之前。

所以修法不是「把规约写得更凶」，而是给一条**不等 MCP 的兜底命令**
（任何 shell 都能跑，直接走 CLI）：

```
C:/Users/<USER>/.workbuddy/binaries/node/versions/22.22.2-2/node.exe D:/npm-global/node_modules/memorix/dist/cli/index.js message inbox --mark-read --cwd D:/memorix-shared
```

> `--mark-read` 是「看完即标记已读」，少了它下一条消息会让 agent 又重复一遍「已收到留言」。

另外把规约里「**干活前**先 poll（在你要动手干活时）」这种措辞删掉了 ——
模型会纠结「列个目录算不算干活」。改成「**该你答的消息**就做这步」，判断标准唯一。

---

## 四、你现在要做的（一步）

**发一条微信给任意一个 agent**，内容是它需要动手才能回答的，例如：

```
@claude 帮我列一下 D:\memorix-shared 目录下的文件
```

**看回复里有没有出现「已收到共享记忆留言 INBOX-TEST-0914」。**

- ⚠️ **只有一条留言是真的**：收件箱里现在留着 **1 条未读**（human-wjx 投的 `INBOX-TEST-0914` 靶子）。
  第一次实测谁都会读到它，**读一次就会被标记已读、之后不会再出现** —— 这是正常的，不是坏了。
- 想再测一轮，就让我再投一条靶子。

---

## 五、后续（阶段 1 开工前的两点修正）

1. **agent 是事件驱动的**：它只在你发消息时才运行。聊天页**不需要**做推送/唤醒，
   但 UI 文案要写清「写完留言后，到微信 @ 一下对应 agent」。
2. **桌面版 WorkBuddy 需重启**：它的 memorix 还是旧的 lite 实例
   （父进程是 `WorkBuddy.exe`，按规矩我没动它）。桥接的三个 agent 已随 cc-connect 重启生效。

---

## 六、附件：文件与备份清单

**新增脚本**（技能 `cc-connect-weixin-bridge`）

| 脚本 | 用途 |
|---|---|
| `scripts/memorix_team_probe.py` | 对比 lite/team 工具表 + 真调协调链路 |
| `scripts/memorix_session_probe.py` | 验证 agent 如何拿到可被寻址的身份 |
| `scripts/dsh_session_read.js` | 解压并解读 DSH 会话记录（多帧 zstd + 工具表对比） |

**修复**

- `scripts/acp_filter_test.py`：旧版遇到真实任务的 `tool_call`（其 `content` 是**列表**）
  会抛异常把读线程打死、误判 FAIL。已兼容 dict/list，并新增 `[TOOL]` 行打印工具名与入参。

**技能文档**

- `SKILL.md` 新增：memorix 档位章节、**坑 6（MCP 冷启动决定成败）**、测试脚本的坑。

**备份**

| 目录 | 内容 |
|---|---|
| `D:\backups\memorix-team-mode-20260914\` | 四处配置改前原件 |
| `D:\backups\memorix-poll-protocol-20260914\` | 五个规则文件改前原件 |

**环境状态**

- cc-connect：23:21:52 重启完成，三个 project 均 `platform ready`（PID 25944）

---

## 七、补记（23:40–23:55）：你实测第一轮 —— `@claude` 卡死，**已修**

你按建议发了 `@claude 帮我列一下 D:\memorix-shared 目录下的文件`。结果：

| project | agent | 反应 |
|---|---|---|
| my-dsh | DSH | `NO_REPLY` ✅ |
| my-workbuddy | WorkBuddy | `NO_REPLY` ✅ |
| **my-project** | **Claude Code** | **卡死 —— 会话记录停在 `[user]`，没有 assistant 回复** |

### 好消息：规约生效了

日志里抓到这一行：

```
23:37:29  claudeSession: permission request  tool=mcp__memorix__memorix_poll
```

**Claude Code 读到了「先看收件箱」的规约，并且真的去调 `memorix_poll` 了。** 规约本身没问题。

### 坏消息：它撞在权限门上

它按协议发出**权限请求**（等人工点「允许」），而桥接是无值守的 —— 没人应答，回合永久挂起。

三个 agent 里，**只有 claudecode 这个 project 漏了权限放行**：之前只修了
WorkBuddy（`--permission-mode bypassPermissions`）和 DSH（profile 补丁），
**Claude Code 走的是另一套开关**。

一条很精确的旁证：`~/.claude/settings.local.json` 的 allow 列表里已有
`memorix_project_context` / `memorix_store` / `memorix_search` / `memorix_session_start` /
`memorix_detail` / `memorix_codegraph_status`，**唯独没有 `memorix_poll`** ——
因为 poll 是 team 档才有的工具，旧的 allow 列表不可能包含它。
此前所有 `@claude` 测试都是纯闲聊、不需要工具，所以这个洞一直没暴露。

### 已修

`~/.cc-connect/config.toml` 里 `my-project` 的 `mode = "default"` → **`"bypassPermissions"`**
（`default` 的语义就是「每次工具调用都要人工确认」，桥接场景必卡）。

未采用更安全的 `dontAsk` + `allowed_tools`：桥接要执行的任务类型不定（这次列目录就要 Bash），
逐个预授权等于还是要放开一大片，反而更容易出现「未列出的工具静默失效」这种更难查的问题。
风险边界与另两个 agent 一致：只有 `allow_from` 白名单内你自己的微信号能触发。

- 备份：`D:\backups\cc-connect-claude-perm-20260914\`
- 重启：23:43:21 完成，三个 project `platform ready`
- 顺带清掉卡死的 `claude.exe`（`--permission-prompt-tool stdio`）等 9 个残留进程，未碰桌面版
- 静态自证：cc-connect 二进制含 `permission-mode` / `bypassPermissions` 映射字符串；
  claude CLI 的 `--permission-mode` 可选值里确有 `bypassPermissions`

### 请你再发一次

同样的那条：

```
@claude 帮我列一下 D:\memorix-shared 目录下的文件
```

这次应该能正常回复，并且开头带「已收到共享记忆留言 INBOX-TEST-0914」
（收件箱里那条靶子仍是未读，所以它还能被读到）。

---

## 八、你实测第二轮（23:48–23:49）：**全部通过，成败点正式打通** ✅

你按上面的话又发了一次同一条消息。三条链路逐条核对：

| project | agent | 判定 | 证据 |
|---|---|---|---|
| `my-dsh` | DSH | ✅ 正确静默 | `turn complete ... tools=0 response_len=8 silent=true` |
| `my-workbuddy` | WorkBuddy | ✅ 正确静默 | 同上（`tools=0 / len=8 / silent=true`） |
| **`my-project`** | **Claude Code** | ✅ **先查收件箱 → 再干活 → 正常回复** | `tools=2 response_len=482 silent=false`，耗时 28.9 秒 |

那条决定性日志（23:49:02）：

```
turn complete session=s1 agent_session=e065d124-… tools=2 response_len=482 silent=false
```

- `tools=2`：这轮**真的调用了 2 个工具**。修复前就是卡在这步（权限请求无人应答）→ 反证已修好。
- `silent=false`：不是静默，正文确实发出去了。
- 对照前两条 `tools=0 / len=8 / silent=true`：那 8 个字符就是 `NO_REPLY`，
  被桥接按点名制吞掉（日志另有一行 `silent reply suppressed` = 静默回复已抑制，未发微信）。

Claude Code 的回复原文（开头即证据）：

> 已收到共享记忆留言 INBOX-TEST-0914。D:\memorix-shared 目录下的文件如下：……

收件箱现状：

```
Inbox: 0 unread / 3 total
```

→ 靶子被读走（`--mark-read` 生效）。说明这次不是「看到了但没消费」，是真的把未读清了。

### 结论：地基三件套全部成立

1. **agent 会主动看收件箱**（MCP 未就绪时靠 CLI 兜底命令）—— 全案成败点，通过。
2. **权限不再卡死**—— claudecode / acp(WorkBuddy) / acp(DSH) 三类开关现在都是放行状态。
3. **点名制静默未被破坏**—— 不该它答的一律 `NO_REPLY`，零打扰。

### 一个必须写进阶段 1 的约束（原方案 §1.4 验收标准要改）

差距核查 §10.4 那条现在升级为**硬约束**：**agent 是事件驱动的，只在被触发运行时才读收件箱。**
所以「聊天页写消息 → agent 自动回复」在纯投递模式下**不成立** ——
必须另外给它一个触发源（微信 @ 一下，或调 `cc-connect cron exec` 主动喂 prompt）。
阶段 1 的验收标准需据此改写，详见下方待拍板项。

### 环境状态（本轮复核时实测）

- cc-connect：PID 13480，23:43:21 启动，三个 project 全 `platform ready`。
- `claude.exe` 子进程：PID 25360（23:48:34 起，正是成功那一轮）。
- 协调团队名册：`1 active agent / 4 historical`，活跃的是 `human-wjx [human] engineer (871f1f30…)`。
  ⚠ 三个 agent（claude/dsh/workbuddy）显示为 historical —— 它们只在运行时才 active，
  聊天页的成员列表要以 `--all` 为准，不能只看 active。
