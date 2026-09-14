# cc-connect 微信桥接运维（本机 Windows）

## 本机路径

| 项 | 路径 |
|---|---|
| CLI 包装 | `D:\npm-global\cc-connect`（sh wrapper） |
| 真实二进制 | `D:\npm-global\node_modules\cc-connect\bin\cc-connect.exe`（Go，v1.3.4+） |
| 配置 | `C:\Users\<USER>\.cc-connect\config.toml` |
| 日志 | `C:\Users\<USER>\.cc-connect\logs\cc-connect.log`（daemon）/ 手动启动输出到自定文件 |
| 会话 | `C:\Users\<USER>\.cc-connect\sessions\` |
| 桌面控制台 | `cc-connect-控制台.bat` → 调 `.ps1`（交互菜单） |
| 内部 API socket | `C:\Users\<USER>\.cc-connect\run\api.sock` |

## 能力边界（2026-09-15 实测，排障/选型必读）

**入站消息是"扇出"的，不是"路由"的。** 一条微信消息会被**原样投给配置里的每一个 project** —— 日志表现为同一 `msg_id` 出现 N 次 `message received`（N = project 数），随后 N 次 `turn complete`。cc-connect **没有**按内容 / 关键词 / @ 对象的入站路由能力。

**`NO_REPLY` 不是桥接的内置开关。** 该字面量在 `cc-connect.exe` 里搜不到 —— 它是**提示词约定**：agent 的规则文件（`CLAUDE.md` / `AGENTS.md`）要求它在「不该自己答」时整段只输出 `NO_REPLY`，桥接识别后把该次回复标记 `silent=true` 并不投递。
→ 所以「其他 agent 不回复」靠的是 **agent 自判**，**仍会消耗一次完整 LLM 调用**，只是不占微信消息位。

**`banned_words` 做不到按 project 定向拦截（实测失败）。** 同一测试词分别写在 ① 项目级（`[[projects]]` 下、与 `name` 同级）② 平台级（`[projects.platforms.options]` 下）—— **两处都不生效**：消息照常进入全部 agent，三条回复全部投递（3× `turn complete` 全部 `silent=false`）。
→ 推断它是**全局**配置（写在 project 内被 TOML 库静默忽略，pelletier 不报未知字段），而全局＝拦所有 project，无法实现「只让某个 agent 收到」。

**微信私聊不传「@ 了谁」。** 会话键为 `weixin:dm:...` 时，`@dsh` 在桥接眼里只是普通文本，没有结构化 @ 数据。原生 @ 路由是**飞书侧**能力（exe 里 `require_mention`、`<at id=%s></at>`、`receive_id_type` 等字段紧邻出现；微信侧无对应实现）。
→ 微信平台只有 `allow_from`，它按**发送者**过滤，不是按 @ 对象过滤。

**`[[hooks]]` 是只读观察者**（`CC_HOOK_*` 环境变量传上下文），**不能拦截或改写消息**，别指望它做路由。

## 配置格式坑（2026-09-13 实际踩过，导致服务近 3 个月起不来）

`admin_from` 必须是**字符串**，不能写成 TOML 表。错误写法（旧版本格式）：

```toml
[projects.admin_from]
allow = ["xxx@im.wechat"]
```

正确写法（v1.3.4，必须放在 `[[projects]]` 表头下方、`[projects.agent]` 之前，否则键会错挂到 agent.options 下）：

```toml
[[projects]]
name = "my-project"
admin_from = "xxx@im.wechat"
```

症状识别：启动瞬间报 `Error loading config: parse config: toml: line N (last key "projects.admin_from"): incompatible types`，或日志里反复刷 `acquired instance lock` 后无任何平台日志。

## 启动方式

```bash
# 手动前台/后台（推荐排障时用，输出直接可见）
D:/npm-global/node_modules/cc-connect/bin/cc-connect.exe --force

# daemon 方式（schtasks 计划任务；注意任务可能被禁用，需先 Enable-ScheduledTask）
cc-connect daemon start / stop / status / logs -f
```

## 常驻策略（2026-09-13 用户决定）

**按需启动，不开机自启**：计划任务的 LogonTrigger 已禁用（任务本身保留，`daemon start/stop` 可管理）。改触发器的方法：

```powershell
$t = Get-ScheduledTask -TaskName 'cc-connect'
$t.Triggers[0].Enabled = $false
$t | Set-ScheduledTask
```

日常启动：桌面 `cc-connect-控制台.bat` 选 2；停止选 3。

注意：沙箱环境可能拦截 schtasks.exe；此时直接跑 exe。启动成功标志（日志）：

```
config loaded → session: loaded from disk → platform recovery loop started
→ api server started → weixin: ilink ready-for-poll → platform ready
```

## 关键机制（排障必读）

1. **微信 token 靠入站消息刷新**：bot 长期没收到用户消息后再主动发送，可能 ret=-2 报错——需要用户先给 bot 发一条新消息刷新 token。
2. **claudecode 引擎懒加载**：启动时 `agent=claudecode ready=0 pending=1` 是正常的，首条入站消息才拉起 Claude 进程。此时 `cc-connect send` 会报 `no active session found`——属预期，不是故障。
3. **send 的前提**：`cc-connect send -p my-project -m "..."` 只要求该 project 收到过入站消息（有会话），之后出站随时可用。这是"agent 互拨"通道：任何能执行命令的程序/agent 都可以 curl 或调 send 发消息到微信。
4. **旧 session id 过期告警**：`session ID does not belong to this project, clearing it` ——cc-connect 会自动清理重建，无需处理。
5. 一轮对话约 10-15 秒（`turn complete` 的 turn_duration）。

## 端到端验证序列

```bash
# 1. 看平台是否 ready
grep -E "platform ready|ready-for-poll" <日志文件>

# 2. 用户手机发微信后，看入站与回复
#    应出现: message received → session spawned → turn complete

# 3. 验证出站（agent 互拨）
cc-connect send -p my-project -m "测试消息"
#    成功输出: Message sent successfully.
```

## 本机接入现状（2026-09-14 更新）

- weixin 平台：已配置（`ilinkai.weixin.qq.com` 微信官方接口），白名单=用户微信 ID，端到端验证通过。claudecode 懒加载正常。
- **多 project 同账号 = 竞争消费 = 多 agent 扇出（已转正为功能）**：两个 project 绑同一微信 token 时，一条消息两个 project 都回复。用户要的就是「一条消息多个 AI 分管回答」，故 my-project（Claude Code）与 my-dsh（DeepSeek Harness，`type="acp"`）同时启用。
- **点名制（@前缀路由）已实现，无需换工具**——靠 cc-connect 的 `NO_REPLY` 静默机制：
  - 机制：cc-connect 检测回复内容整体匹配 `(?i)^\s*NO_REPLY\s*$`（允许 `*` 包裹）时**不发送该条回复**。
  - **规则注入点（实测结论，很重要）**：`[projects.agent.options] append_system_prompt` 对 cc-connect 的 claudecode 会话**不生效**（实测该会话转录里查不到规则文本）；正确做法是写 **work_dir 下的 `CLAUDE.md`**（Claude Code 原生项目记忆，实测生效）。
  - DSH（`type="acp"`）**没有提示词选项**，规则写 **work_dir 下的 `AGENTS.md`**（实测生效）。
  - **措辞必须"硬"**：早期温和措辞（"若以 @dsh 开头则只输出 NO_REPLY"）Claude 会无视并继续作答；必须写成**协议级硬规则**：明确"禁止作答、禁止调用任何工具、完整回复必须恰好是这 7 个字符 NO_REPLY、多写一个字就会真的发出造成刷屏"。改硬后 Claude 与 DSH 均 100% 遵从。
  - 语义：`@dsh xxx` → 只有 DSH 答；`@claude xxx` → 只有 Claude 答；**无前缀 → 两个都答**。
  - 本地验证（不必经微信）：`cd <work_dir> && claude -p "@dsh，今天是几号"` 应输出 `NO_REPLY`；`dsh --profile headless "@claude，今天是几号"`（cwd=work_dir）应输出 `NO_REPLY`。
- **改规则后必须开新会话**：被 resume 的旧会话**不会重新加载** CLAUDE.md / AGENTS.md（实测：改完规则后微信里仍按旧行为作答）。做法：`daemon stop` → 强杀残留 → **把 `~/.cc-connect/sessions/*.json` 挪到备份目录**（不要删）→ `daemon start`。之后首条消息即建新会话。
- 常驻方式：按需启动（计划任务 LogonTrigger 已禁用），桌面 `cc-connect-控制台.bat` 选 2 启动 / 3 停止。
- 出站发送：`cc-connect send -p <project> -m "..."`，前提是该 project 收到过入站消息（重启后需用户先发一条）。
- **DSH 已装 memorix MCP**（2026-09-14）：`memorix serve --mode lite` 在 `~/.dsh/cordis.patch.yml`。WorkBuddy 侧在 `~/.workbuddy/mcp.json`。

## WorkBuddy 作为被桥接 agent（2026-09-14 实测，**推翻"接不进来"的旧结论**）

**核心事实：WorkBuddy 桌面版本体就是一个 CodeBuddy CLI 进程，天生是 ACP agent。**

- 桌面版 CLI 路径：`D:\workbudy\WorkBuddy\resources\app.asar.unpacked\cli\`，入口 `bin/codebuddy`（node 脚本），bundle `dist/codebuddy.js`（22MB）、`dist/codebuddy-headless.js`。
- 内部包名 `@genie/agent-cli`（npm 上 404，属内部包）；**对外公开包 `@tencent-ai/codebuddy-code`**（`npm view @tencent-ai/codebuddy-code version`，实测 2026-09-14 为 `2.150.0`，比桌面版内嵌的 `2.132.0` 更新）——**推荐用公开包装独立实例做桥接**，避免与桌面版抢资源。
- 验证方法：`tasklist`/`Get-CimInstance Win32_Process` 看进程命令行，会看到
  `WorkBuddy.exe ...\cli\bin\codebuddy --serve --session-id <uuid> --permission-mode bypassPermissions ...`
  ——即**每个 WorkBuddy 会话 = 一个 `codebuddy --serve` 子进程**（由 `~/.workbuddy/logs/daemon.log` 的 `sidecar-manager` 创建）。

### 三种外部接入形态（`--help` 实证）

| 形态 | 参数 | 适配的桥 |
|---|---|---|
| ACP stdio | `--acp --acp-transport stdio` | cc-connect（`type="acp"`）、AgentBridge |
| ACP HTTP | `--acp --acp-transport streamable-http` | WeiClaw（`acp://host:port`） |
| CLI 非交互 | `-p/--print`（+ `-c/--continue`、`-r/--resume`） | WeiClaw / AgentBridge 的 CLI 驱动 |
| HTTP 服务 | `--serve --port <n>` | 自带 Web UI + API |

次要能力：`--system-prompt-file`、`--append-system-prompt`、`--permission-mode`、`--model`（含 `deepseek-v4.1-flash`/`glm-5.3`/`kimi-k3-1` 等）、`--mcp-config`、`--strict-mcp-config`、`--allowedTools`/`--disallowedTools`。

### ACP 握手实测（脚本 `scripts/cbc_acp_probe.py`，**实测通过**）

`initialize` 返回标准结果：

```json
{"protocolVersion":1,
 "agentCapabilities":{"promptCapabilities":{"image":true,"embeddedContext":true},
   "mcpCapabilities":{"http":true,"sse":true},
   "loadSession":true,"delegateToolsSupport":true},
 "authMethods":[{"id":"iOA","name":"Login with iOA"},
                {"id":"external","name":"Login with Google/Github"},
                {"id":"internal","name":"Login with WeChat"},
                {"id":"selfhosted","name":"Login with Enterprise Domain"}]}
```

**唯一障碍 = 认证**：独立启动的 CLI 不共享桌面版登录态，需先登录一次（四选一，微信登录对内网外用户最方便）。未认证时 `session/new` 不返回，表现为"卡住"。

### 两个独立启动时的坑

1. **固定端口 62117 被占**：桌面版当前会话进程会 `LISTEN 127.0.0.1:62117`（即 `--serve` 的 HTTP 端口）。外部再起一个 CLI 走 `-p`/`--serve` 路径时报 `EADDRINUSE ... 127.0.0.1:62117` 并且**进程不退出（永久挂起）**。`--acp` 路径不绑该端口，可正常握手。
2. **`config list` 等子命令静默无输出**（同一挂起问题的表现），不要误判为"命令不存在"。

**规避办法（实测修正）**：装独立包 `@tencent-ai/codebuddy-code` 能得到独立的 `~/.codebuddy` 数据目录，但**仍会撞 62117**——该端口是**按机器/用户固定**的（搜索过内嵌包与独立包的 bundle，均无硬编码 `62117`，是运行时算出来的），设计上假定「同一用户只跑一个实例」。
→ 因此**桥接一律走 `--acp`**（不绑该端口，实测正常），避开 `-p`/`--serve` 路径。

### 独立安装 + 登录（2026-09-14 已落地）

```bash
# 独立安装（本机前缀 D:\npm-global，装前务必 unset NODE_OPTIONS ELECTRON_RUN_AS_NODE）
npm install -g @tencent-ai/codebuddy-code          # 实测装到 2.150.0，added 4 packages in 1m
```

- **真实 JS 入口（Windows 下必须用这个）**：`D:\npm-global\node_modules\@tencent-ai\codebuddy-code\bin\codebuddy`
  ⚠️ 不能指向 `D:\npm-global\bin\codebuddy`（npm 生成的 **POSIX sh 包装脚本**，Windows 下 subprocess 无法执行，会 `OSError: [Errno 22] Invalid argument`）。`.cmd`/`.ps1` 同理不适合被 JSON 配置直接调用。
- 独立实例 ACP 握手实测通过，能力声明与内嵌版一致并多出 `multitaskSupport: true`。
- **登录机制（实测关键）**：ACP 协议流程 `initialize` → `authenticate` → `session/new`。
  - `authenticate` 时 CLI 不发响应，而是先推一条**自定义通知**：
    ```json
    {"method":"_codebuddy.ai/authUrl",
     "params":{"authUrl":"https://www.workbuddy.cn/login?platform=workbuddy&state=<uuid>&version=5.4.7",
               "provider":"external"}}
    ```
  - 未完成授权时 `session/new` 返回：`{"error":{"code":-32000,"message":"Authentication required","data":{"category":"auth"}}}`。
  - 所以流程是：**拿 authUrl → 用户浏览器登录 → CLI 侧授权完成 → 凭证落 `~/.codebuddy/`**。
- **登录器（已固化）**：`scripts/cbc_login.py`（ACP 拉起 CLI、抓 authUrl、自动 `webbrowser.open`、等待授权、超时 600s）
  配套桌面双击启动器：`C:\Users\<USER>\Desktop\codebuddy-登录.bat`
  （bat 内容**必须纯 ASCII + CRLF**；用 Python 以 `chr(92)` 拼反斜杠生成，避免转义把路径写成 `D://python//` 的错误形态。）

### 接入 cc-connect（2026-09-14 **已落地，三 agent 并存**）

**已生效配置**（`~/.cc-connect/config.toml`）——三个 project 共用同一微信 token，靠 NO_REPLY 点名：

| project | agent type | work_dir | 规则文件 |
|---|---|---|---|
| my-project | `claudecode` | `C:\Users\<USER>` | 该目录 `CLAUDE.md` |
| my-dsh | `acp` | `C:/Users/<USER>` | 该目录 `AGENTS.md` |
| **my-workbuddy** | `acp` | `C:/Users/<USER>/wb-agent` | 该目录 `CLAUDE.md` + `AGENTS.md`（双份保险） |

my-workbuddy 关键字段：

```toml
[projects.agent]
type = "acp"
[projects.agent.options]
work_dir = "C:/Users/<USER>/wb-agent"
command = "C:/Users/<USER>/.workbuddy/binaries/node/versions/22.22.2-2/node.exe"
args = ["D:/workbudy/WorkBuddy/resources/app.asar.unpacked/cli/bin/codebuddy", "--acp", "--acp-transport", "stdio"]
display_name = "WorkBuddy"
```

**为什么必须给它独立 work_dir**：codebuddy CLI 是 Claude Code 风格、会读 work_dir 下的 `CLAUDE.md`。若沿用 `C:\Users\<USER>`，它会读到**为 Claude Code 写的点名规则**（只认 @dsh/@claude），导致 `@wb` 时三方抢答。

**点名口令**：`@claude` / `@dsh` / `@wb`（或 `@workbuddy`）；**无前缀 = 全部应答**。
**验证通过标志**：日志里三个 project 都出现 `platform ready`，且 `cc-connect is running projects=3`。

### ⚠️ 沙箱陷阱：绝不要在 WorkBuddy 会话的 Bash 里手工跑 codebuddy CLI（2026-09-14 踩到）

实测沙箱会**拦截** `C:\Users\<USER>\AppData\Local\CodeBuddyExtension\Data\Public\auth\*`（**这就是 CLI 凭证的真实位置**，按 host 命名，如 `workbuddy-desktop.info`）以及 `reg.exe`。
后果：CLI 读不到凭证 → 行为异常（`session/new` 无限无响应）、并可能反复触发重新授权。
**结论**：在 WorkBuddy 沙箱里得到的 CLI 行为**全部不可信**（本机排查 `session/new` 无响应耗费多轮皆因此）；验证必须交给**沙箱外的进程**（cc-connect / WeiClaw）。若确实要在沙箱内跑，需 `dangerouslyDisableSandbox` 并经用户批准。

### 重启 cc-connect 的正确流程（2026-09-14 复核）

```bash
# 1) 停 —— 会打印 "daemon stopped" 但【旧进程不会死】，必须复核
cc-connect daemon stop
ps -W | grep -i cc-connect                 # 仍有进程 → 强杀
#   强杀只能用 PowerShell：Stop-Process -Id <pid> -Force
#   （git bash 下 taskkill //F //PID 报"无效参数"；cmd //c 会被吞；reg.exe 在程序黑名单）
# 2) 启
cc-connect daemon start
# 3) 验证：三个 project 都要出现 platform ready
grep "platform ready" ~/.cc-connect/logs/cc-connect.log | tail
```

补充：`cc-connect --force` 可在启动时自动杀掉同配置旧实例；`cc-connect daemon status` 查状态（本机平台 **schtasks**，WorkDir `C:/Users/<USER>/.cc-connect`）。规则文件改动后需**开新会话**才生效；`~/.cc-connect/sessions/` 为空时无需清理。

**⚠️ 杀进程顺序（2026-09-14 22:56 补，两条规则会打架，按下表权衡）**：

| 目标 | 顺序 | 原因 |
|---|---|---|
| 避免微信收到假错误 | **先杀 cc-connect，再清残留子进程** | stderr 在「杀 agent」那一刻才被刷出；cc-connect 已死就没人转发（见坑 5） |
| 避免堆积孤儿进程 | 先子后父 | parent 死后 children 在 Windows 上不会跟着死 |

两者兼顾的写法：**先杀 cc-connect，隔 2 秒再杀残留后代**。
若在**没有 agent 在运行时**重启（刚起完、还没来过消息），两种顺序都无所谓 —— 优先挑这个时机。

### 🚨 严重教训：不要反复跑 ACP 认证探针（2026-09-14 实际踩到）

**现象**：连续跑了几次 `cbc_acp_probe.py` / `cbc_acp_auth.py` 后，用户报告「不断弹出浏览器要求登录」、且「**WorkBuddy 桌面版不断回到登录界面**」。

**根因**：ACP 下每调用一次 `authenticate`，CLI 就会**自己打开系统浏览器**走一遍设备授权，并**刷新账号令牌**。反复调用 = 反复弹窗 + 反复刷令牌 → **把 WorkBuddy 桌面版已登录的令牌挤失效**，桌面版被迫重新登录。
（注意：打开浏览器的是 **CLI 自身**，不是调用方脚本 —— `cbc_acp_probe.py` 里根本没有 `webbrowser`。）

**规程（必须遵守）**：
1. **认证探针一次只跑一次**，失败先改代码 + 复核日志，**不要连续重试**。
2. 跑之前**先告知用户**"可能会弹出一次浏览器授权"，让用户有预期。
3. 不要在用户正在使用 WorkBuddy 桌面版时做这类测试 —— 会打断他的会话。
4. 出现桌面版被登出后：**先停手**，让用户重新登录桌面版恢复，再评估。
5. 每轮开始前用 `Get-CimInstance Win32_Process` 确认没有残留的 `--acp` 进程。

**另一条重要线索**：清空宿主注入的 `CODEBUDDY_*` 环境变量后，CLI 反而走**完整账号登录流程（弹窗）**；而 WorkBuddy 桌面版自身的 CLI 子进程靠 `CODEBUDDY_GATEWAY_AUTH` / `CODEBUDDY_GATEWAY_PASSWORD` / `CODEBUDDY_SERVICE_PROXY_URL` 这几个网关凭证**静默认证，从不弹窗**。→ 若要实现"不弹窗的桥接"，方向应是**带上网关凭证**（或用桌面版配置目录 `~/.workbuddy`），而不是清干净环境。此点尚未验证成功，勿当成结论。

**待澄清**（下次继续时优先验证）：`authenticate` 后 id=2 返回了 userinfo（认证成功）但 `session/new` 始终无响应（40s / 120s 均无），已排除：MCP 干扰（加 `--strict-mcp-config` 无效）、工作目录扫描（换空目录无效）、stderr 报错（仅 Git Bash 提示）。**下一步应换方向验证**，不要再重复同一路径。

### 附：CLI 原生带微信/企业微信接入（环境变量实证）

`CODEBUDDY_WECHAT_AUTO_CONNECT`、`CODEBUDDY_WECOM_AUTO_CONNECT`、`CODEBUDDY_WECOM_BOT_ID`、`CODEBUDDY_WECOM_BOT_SECRET`、`CODEBUDDY_WECOM_BOT_WS_URL`，对应参数 `--remote-control [client]`（帮助里 client 例子就是 `wecom`）。
→ 即 **WorkBuddy 自己就能直连微信**（用户此前的 wecom 连接器底层即此）。但它是"WorkBuddy 独占入口"，无法让 Claude/DSH 同时进同一入口，**只能作为多 agent 扇出方案的备选**。
（另注：`CODEBUDDY_SAFE_DELETE_*` 系列变量印证本机 safe-delete shim 确由 WorkBuddy 注入。）

## 「对方正在输入」卡死 = 权限请求 + token 过期 死锁（2026-09-14 实测）

微信侧表现：一直显示「对方正在输入」但永远收不到消息。日志特征：

```
acp: cached tool_call input ...        ← agent 正在调工具
weixin: sendMessage ret=-2 (expired context_token) ... preview="⚠️ **权限请求**"
```

成因链：① 多 project 共用一个 ilink bot，`context_token` 会互相竞争/过期 → ② agent 触发的**权限请求卡片发不出去** → ③ agent 在等审批、用户看不到卡片 → 死锁，后续消息排队。

处置：
1. 先让用户**发一条新消息**刷新 token（token 只认最新的入站消息）。
2. 治本：让被点名的 agent **不调用任何工具**（点名制硬规则里已写明"不要调用任何工具"），从源头减少权限卡片。
3. 重启服务可解除卡死状态（旧进程被杀的中间态会让微信侧输入提示自然超时消失）。

## 思考过程（reasoning/thinking）泄漏 → 点名制失效（2026-09-14 两轮实测，最终修法）

症状：点名制下，非目标 agent 不输出干净的 `NO_REPLY`，而是把**推理独白**一起发到微信。例如：

```
The message starts with "@claude1+2是多少" — this starts with "@claude" prefix. Per the routing protocol, message...
```

### 定位方法（最快，优先用这个）

**读 `~/.cc-connect/sessions/<project>_<hash>.json` 的 `history` 数组** —— 它直接记录「cc-connect 认为 agent 回复了什么」，一眼看清是哪个 agent 泄漏、泄漏成什么样。比逐个猜日志里的 `agent_session` 快得多。

```bash
# 三个 project 各一份（sessions/ 下按 project 名 + 随机 hash 命名）
python -c "import json;d=json.load(open(r'C:\Users\<USER>\.cc-connect\sessions\my-dsh_44fd3b43.json',encoding='utf-8'));print(json.dumps(d['sessions'],ensure_ascii=False,indent=1))"
```

对照实测（2026-09-14 21:35 那条 `@wb，你是什么模型？`）：

| project | agent 类型 | history 里记录的回复 |
|---|---|---|
| my-project | claudecode | `NO_REPLY` ✅ 干净 |
| my-dsh | acp | `The message starts with @wb → I must reply exactly NO_REPLY.NO_REPLY` ❌ 独白混入正文 |
| my-workbuddy | acp | `用户以 @wb 开头，根据规则应该去掉前缀正常作答。…我应该简洁回答。我是 CodeBuddy Code…` ❌ 独白混入正文 |

> ⚠️ **别被表象带偏**：用户投诉「claude 输出思考过程」，实际 Claude 的**最终回复是干净的**（history 里就是 `NO_REPLY`，log 里 `silent=true` + `silent reply suppressed`）。用户看到的英文独白是 Claude 的 thinking 被 cc-connect 当作**独立消息**发出去的（不进 history）。真正把独白**混进正文**的是两个 acp agent（DSH / WorkBuddy）。

### 真正的根因：cc-connect 的 ACP 适配器不区分「思考块」与「正文块」

**类型 1 —— 思考被当作「独立消息」发出**（Claude，走 `claudecode` 适配器）
→ 由**全局 `[display] thinking_messages = false`** 解决。cc-connect 默认 `thinking_messages = true`，会把思考过程单独发到聊天。

**类型 2 —— 思考被【拼接】进回复正文**（DSH / WorkBuddy，走 `acp` 适配器）
→ **`clean_reply = true` 治不了它**（实测证伪，见下）。真正原因是：

> cc-connect 的 ACP 适配器**不认识 `agent_thought_chunk` 这个标准消息类型** —— 二进制里搜 `agent_thought_chunk` **命中 0 次**，只有 `agent_message_chunk`。它于是把思考块和正文块**都当成正文拼成一条回复**。
>
> 而 **agent 侧其实是完全规范的**：用 `scripts/acp_dump.py` 实测 DSH，思考走 `agent_thought_chunk`、正文走 `agent_message_chunk`，两者干净分离：
> ```
> [UPDATE] agent_thought_chunk | 'The message starts with "@claude，你好"...'
> [UPDATE] agent_message_chunk | 'NO_REPLY'
> ```
> 所以问题只在中间通道，**不需要改动 agent 本身**。

### ✅ 最终修法：在通道上加一层「思考块过滤器」

`scripts/acp-thought-filter.js` —— 插在 cc-connect 与 agent 之间的 ACP 代理。按行解析 newline-delimited JSON-RPC，丢弃 `sessionUpdate` 匹配 `/thought|reason|thinking/i` 的帧，其余原样透传（解析失败一律不丢，绝不影响通信）。

配置写法（把过滤器放在 `args` 最前，其后接真实 agent 的启动命令）：

```toml
[projects.agent.options]
command = "<node.exe 绝对路径>"
args = [
  "<skill目录>/scripts/acp-thought-filter.js",
  "<node.exe>",          # 真实 agent 的解释器
  "<真实 agent 启动脚本>",  # 例：D:/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js --profile acp
  "参数..."
]
```

实测效果（`scripts/acp_filter_test.py`，走真实 DSH + 真实过滤器）：

| | cc-connect 收到 | 结果 |
|---|---|---|
| 过滤前 | `...独白...NO_REPLY` | 静默判定失效，独白被发到微信 |
| 过滤后 | `NO_REPLY` | ✅ 静默正常 |

**微信实链路实测通过（2026-09-14 22:06）** —— 三条点名消息，每条恰好只有一个 agent 说话：

| 消息 | Claude | DSH | WorkBuddy |
|---|---|---|---|
| `@wb，你好` | `NO_REPLY` ✅ | `NO_REPLY` ✅ | **「你好！有什么我可以帮你的？」** |
| `@dsh，在吗` | `NO_REPLY` ✅ | **「在的，有什么可以帮你？」** | `NO_REPLY` ✅ |
| `@claude，你好吗` | **作答** ✅ | `NO_REPLY` ✅ | `NO_REPLY` ✅ |

日志侧对应：非目标 agent 全部 `response_len=8` + `silent=true` + `silent reply suppressed`（`response_len=8` 正好是 `NO_REPLY` 的字节数，说明独白确实被拦在门外）。
**WorkBuddy 侧一并生效**（它同样走 ACP 通道，同样被过滤器拦住）—— 此前担心的"WorkBuddy 无法本地验证"由实链路补齐。

补充：`[display]` 那段配置仍要保留（它解决的是 Claude 的「独立思考消息」）。

```toml
# 全局：放在【第一个 [[projects]] 之前】（否则会被误解析成项目子表！）
[display]
thinking_messages = false
tool_messages = false
```

改完**必须重启**（杀 cc-connect.exe，守护脚本 10 秒后自动拉起，见下文「重启正确流程」）。

### ❌ 两个已被证伪的修法（别再走弯路）

1. **`clean_reply = true`**（平台级选项，描述为 *Strip thinking/tool progress lines from replies*）
   **2026-09-14 实测无效**：加上之后 history 里独白照旧、`response_len` 依旧 60~900 字节。
   它大概只处理特定格式的「进度行」，对 ACP 直接拼进正文的自由文本独白没有作用。（该配置无害，可保留。）

2. **改 DSH 的 `reasoningEffort: off`**
   该文件 20:01 就已设成 `off`，但 21:35、21:56 两轮实测 DSH 仍把独白发出去 —— 它**并不阻止 agent 发送思考块**，只是调低了推理强度。
   **2026-09-14 22:56 已移除该设置**（恢复为 `high`，与 `settings.yaml` 一致）：既然 `acp-thought-filter.js`
   已在通道层兜住，就没必要再牺牲推理质量。移除前实测确认过滤器仍然有效 ——
   走真实「DSH + 过滤器」链路（`scripts/acp_filter_test.py`）：
   **18 帧思考块被丢弃**，而 cc-connect 最终看到的仍恰好是 `NO_REPLY`，判定 PASS。

**推理档位的正确写法**（仅 acp profile 生效；cordis patch 是**整行替换 config、不合并**，所以 provider/model 必须重述。行 id 与 name 可在 `D:/dsh/node_modules/@deepseek-ai/dsh-base/cordis.patch.yml` 查到，`id: agent-default-model` → `name: '@deepseek-ai/dsh-agent-default-model'`）：

```yaml
- id: agent-default-model
  config:
    provider: deepseek-official
    model: deepseek-flash
    reasoningEffort: high       # 可选值：off / low / high / max
```

### 本地验证工具（不用等微信，2026-09-14 固化）

`scripts/acp_probe.py`（本技能目录内）——以真实 ACP 协议握手 DSH、发一条 prompt、收集 `agent_message_chunk`：

```bash
D:/python/python.exe "<skill目录>/scripts/acp_probe.py" "@claude，1+1是多少"   # 期望输出恰好 'NO_REPLY'
D:/python/python.exe "<skill目录>/scripts/acp_probe.py" "@dsh，1+1是多少"      # 期望输出正常的答案
```

Claude 侧对应验证：`cd C:/Users/<USER> && claude -p "@dsh，今天是几号"` → 期望恰好 `NO_REPLY`。

`scripts/acp_dump.py` —— 把 agent 发出的**所有** `session/update` 类型原样打印，用来判断「思考」到底走哪种消息类型：

```bash
D:/python/python.exe "<skill目录>/scripts/acp_dump.py" "@claude，你好"
# 期望看到 agent_thought_chunk（思考）与 agent_message_chunk（正文）分开出现
```

`scripts/acp_filter_test.py` —— 经 `acp-thought-filter.js` 代理连 DSH，验证思考块是否被成功丢弃：

```bash
D:/python/python.exe "<skill目录>/scripts/acp_filter_test.py" "@claude，你好"
# 期望：汇总里没有 agent_thought_chunk；最终拼接结果恰好是 'NO_REPLY'；判定 PASS
```

> ⚠️ 涉及 **CodeBuddy CLI（WorkBuddy）** 时**不要**在 WorkBuddy 会话里跑探针 —— 会触发认证并弹浏览器（见下文「严重教训」）。WorkBuddy 侧的验证只能交给 cc-connect 实链路（用户发微信消息）或沙箱外进程。


## cc-connect 两个致命坑（2026-09-14 踩过，务必先查）

### 1. `daemon.ps1` 硬编码 PATH → claude 找不到

`C:\Users\<USER>\.cc-connect\cc-connect-daemon.ps1` 第 4 行**写死了整条 PATH**（不是继承系统/用户环境）。里面漏 `D:\npm-global` 时，schtasks 启动的实例会在日志刷：

```
failed to create agent project=my-project error="claudecode: \"claude\" CLI not found in PATH"
```

排查要素：用户在 bash 里 `which claude` 有结果 ≠ daemon 能找到。**修法是编辑该 ps1 的 PATH 字符串**，不要试图改系统 PATH（本机工具进程无管理员权限，写 HKLM 静默失败，且与症状无关）。

### 2. `daemon restart` / `daemon stop` 都不杀旧进程 → 配置不生效

实测**两次**：`cc-connect daemon restart` 与 `daemon stop` 都会打印成功，但旧 `cc-connect.exe` 仍存活（`tasklist` 能查到 PID），**新配置根本没加载**（日志 mtime 不更新）。所以 `daemon stop` 之后**必须再确认一次进程是否真的没了**，有残留就强杀。

正确的重载序列：

```bash
cc-connect daemon stop
tasklist | grep -i cc-connect          # 关键：不能省，本机实测常有残留
# 用 PowerShell: Get-Process cc-connect | Stop-Process -Force
cc-connect daemon start
sleep 15 && ls -la ~/.cc-connect/logs/cc-connect.log   # 看 mtime 是否为当前时间
grep "cc-connect is running" ~/.cc-connect/logs/cc-connect.log | tail -1   # projects=N
```

验证配置真的生效，还可直接解析 TOML 比对：

```bash
D:/python/python.exe -c "import tomllib;d=tomllib.load(open(r'C:\Users\<USER>\.cc-connect\config.toml','rb'));print([(p['name'],p['agent']['type'],list(p['agent'].get('options',{}))) for p in d['projects']])"
```

## memorix MCP 接入（2026-09-14，四端配置 + 三个关键坑）

### 各 agent 的配置位置（本机实况）

| Agent | 配置位置 | 备注 |
|---|---|---|
| WorkBuddy 桌面版 | `~/.workbuddy/mcp.json` | `~/.workbuddy` 是指向 `D:/workbuddy/.workbuddy` 的**符号链接** |
| Claude Code | `~/.claude.json` 顶层 `mcpServers` | 原为裸命令 `memorix` + 无 `--mode`（默认 `micro`，工具最少），已改 node 绝对路径 + `--mode lite` |
| DSH | `~/.dsh/cordis.patch.yml`（**全局**，非 profile 专属） | insert 条目 id = `memory-memorix` |
| **桥接的 WorkBuddy**（CodeBuddy CLI） | **上面三份都不读** → 必须用 `--mcp-config` 显式传 | 见坑 2 |

### ⚠️ 坑 1（最关键）：memorix 拒绝把 $HOME 及其子目录当项目根

```
Error: Refusing to bind $HOME as a project root (C:\Users\<USER>).
Pass a git project via --cwd, MEMORIX_PROJECT_ROOT, or memorix_session_start({ projectRoot }).
```

而三个 agent 的 work_dir **全在 `$HOME` 下**（`C:/Users/<USER>`、`C:/Users/<USER>/wb-agent`）→ 一个都绑不上项目，这才是"memorix 层没跑通"的主因。

**修法**：四端的 memorix 全部加 `--cwd D:/memorix-shared`（真实 git 仓库 `wjxn13/memorix-shared`，正是本机设计中的共享记忆载体）。实测生效：

```
[memorix] Project root: D:\memorix-shared
[memorix] MCP Server running on stdio (profile: lite, project: wjxn13/memorix-shared)
[memorix] Prepared search index for 276 observations in project: wjxn13/memorix-shared
[memorix] Tool profile: lite (core memory + sessions, 20 tools)
```

### ⚠️ 坑 2：桥接的 WorkBuddy 加载到的 MCP 数为 0

实测日志（`~/.codebuddy/logs/<日期>/wb-agent__*.log`）：

```
[Startup] McpConfigManager: total MCP servers from plugins: 0
[MCP] reconcile revision=0..5 projected (retained=0, pending=0, evicted=0)
```

它虽然被识别为 WorkBuddy 产品（日志 `productName=WorkBuddy, isWorkBuddy=true`），但 user 作用域**没有读到**桌面版那份 `~/.workbuddy/mcp.json`。

**为什么确定 `--mcp-config` 这条路可行**（实链路日志实证）：`wb-agent__*.log` 里存在一次
`mcpConfig={"mcpServers":{}} strictMcpConfig=true` 的 ACP 启动（argv 带 `--acp --acp-transport stdio --strict-mcp-config --mcp-config {"mcpServers":{}}`），
说明 **ACP 模式下 CLI 确实会解析 `--mcp-config` 并把条目投进 dynamic 作用域**（该次因配置为空，
后面紧跟 `projected (retained=0, pending=0, evicted=0)`）。传非空配置即可保留。
另注意：`--strict-mcp-config` 会把 plugin/user/project/local 四个作用域全部 block 掉，只留 dynamic ——
我们**不需要**加这个开关（不加则 user/project 作用域照常加载，只会更全不会更少）。

**修法**：在 cc-connect 的 `args` 里显式传 `--mcp-config`（**JSON 字符串**形式，探针脚本 `cbc_acp_probe.py` 已验证此用法；TOML 用单引号包裹 = 字面量，无需转义）：

```toml
args = ["...acp-thought-filter.js", "<node.exe>", "...codebuddy", "--acp", "--acp-transport", "stdio",
        "--mcp-config", '{"mcpServers":{"memorix":{"command":"<node.exe>","args":["D:/npm-global/node_modules/memorix/dist/cli/index.js","serve","--mode","lite","--cwd","D:/memorix-shared"]}}}']
```

### ⚠️ 坑 3：MCP server 是异步启动的（探针极易误判）

memorix 启动时要建索引（本项目 276 条 observations），实测需十几秒。若在 `session/new` 之后**立刻**发 prompt，工具列表里看不到 memorix / argo，很容易误判成"配置没生效"（本次就先踩了一次，差点走错方向）。
`acp_dump.py` 已内置 20 秒预热等待。cc-connect 实链路的 agent 是常驻的，第二条消息起就不存在该问题。

### 验证方法（2026-09-14 实测结果）

| 端 | 命令 | 实测结果 |
|---|---|---|
| memorix 本体 | `memorix_probe.py 1`（冒烟握手） | ✅ `serverInfo name=memorix version=1.9.2`，`--mode lite` **固定 20 个工具** |
| DSH | `acp_dump.py "你现在挂了哪些 MCP 工具"` | ✅ 挂上了 memorix（含 argo，同一份 lite 档） |
| Claude Code | `claude -p "列出你挂载的 MCP 工具名称"` | ✅ **20 个 memorix 工具** |
| 并发 | `memorix_probe.py 3`（3 实例同开一个 `memorix.db`） | ✅ **3/3 PASS**，各 20 工具，7.8s |
| 桥接 WorkBuddy | 只能看实链路日志 `~/.codebuddy/logs/` | ⏳ 待实链路确认 |

> `--mode lite` 的 20 个工具（实例名）：`memorix_store` / `suggest_topic_key` / `search` / `graph_context` /
> `project_context` / `codegraph_status` / `context_pack` / `resolve` / `store_reasoning` / `search_reasoning` /
> `timeline` / `detail` / `retention` / `session_start` / `session_end` / `session_context` / `transfer` /
> `evidence` / `feedback` / `media`。

**关于并发（已实测排除的风险）**：四个 agent 可能同时被 @，各自拉起一个 memorix 子进程，全都指向同一个
`D:/memorix-shared/data/memorix.db`。担心 SQLite 多进程抢锁会像 `--cwd` 那样再埋一个坑，故写
`scripts/memorix_probe.py` 实测：3 个实例并发 `initialize + tools/list` **全部通过、零报错**。
结论：memorix 的并发读写不是阻塞项。（该探针是只读的，不会写入任何记忆。）

### 附：memorix 官方 setup 命令（仍可用，但有副作用）

```bash
memorix setup --list                      # 查看支持清单（含 WorkBuddy / DeepSeek Harness）
memorix setup --agent workbuddy --global --dryRun   # 先 dry-run
memorix setup --agent workbuddy --global  # → 写 ~/.workbuddy/mcp.json
memorix setup --agent dsh --global        # → 写 ~/.dsh/cordis.patch.yml + ~/.dsh/AGENTS.md
```

注意：`memorix setup` 写 DSH 的 `cordis.patch.yml` 时会**重排 YAML 并丢掉全部注释**（本机该文件注释里存着 argo/video-evidence 的排错知识）→ 装完务必用备份 diff 补回注释。

它写入的是裸命令 `command: memorix`，而 **dsh 会清理子进程环境（PATH 不可依赖）**，裸命令在 dsh 里起不来。必须改成绝对路径：

```yaml
command: C:/Users/<USER>/.workbuddy/binaries/node/versions/22.22.2-2/node.exe
args: [D:/npm-global/node_modules/memorix/dist/cli/index.js, serve, --mode, lite]
```

冒烟测试（不用等 agent 跑起来就能确认能握手）：

```bash
D:/python/python.exe -c "
import subprocess,json
p=subprocess.Popen([r'C:/Users/<USER>/.workbuddy/binaries/node/versions/22.22.2-2/node.exe',r'D:/npm-global/node_modules/memorix/dist/cli/index.js','serve','--mode','lite'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,encoding='utf-8')
p.stdin.write(json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'smoke','version':'1'}}})+chr(10)); p.stdin.flush()
print(p.stdout.readline()[:200]); p.kill()"
# 期望看到 serverInfo name=memorix
```

## ⚠️ 坑 4（最隐蔽）：agent 要调工具时会「权限挂起」，微信侧表现为「完全没回」

### 症状

用户 @ 某个 agent，微信里**一条回复都没有**（不是报错、不是 NO_REPLY 静默，就是空的）。

诊断入口 —— 读会话记录，会看到 `history` **停在 `[user]`、没有对应的 `assistant`**：

```bash
python -c "
import json
d=json.load(open(r'C:/Users/<USER>/.cc-connect/sessions/my-workbuddy_e5d412fe.json',encoding='utf-8'))
h=(d.get('sessions') or {}).get('s1',{}).get('history',[])
for m in h[-3:]: print(m.get('role'),'::',str(m.get('content'))[:100])
"
```

对照三个 project 的会话记录：正常静默的 agent 会留下 `assistant :: NO_REPLY`，
挂起的 agent 则**什么都没有**。

`cc-connect.log` 里的特征行：

```
level=INFO msg="acp: permission request" request_id=0 tool=permission input="{...}"
level=INFO msg="permission request" request_id=0 tool=permission
```
（此后不再有该 project 的 `turn complete`。）

### 根因

ACP 协议下，agent 执行工具前会向**客户端**发 `session/request_permission`。
cc-connect 虽然是客户端，但微信桥接是**无值守**的 —— 没人点「允许」，回合就永久挂起。
（cc-connect 二进制里确实有「允许/允许所有/拒绝」等提示文案，但这次实链路并没有把审批卡发到微信，
所以不能指望人工批准。）

### 修法 A：CodeBuddy CLI（桥接的 WorkBuddy）

CLI 自带权限开关（`codebuddy --help` 可查）：

| 参数 | 含义 |
|---|---|
| `-y, --dangerously-skip-permissions` | 跳过所有权限检查 |
| `--permission-mode <mode>` | `acceptEdits` / `bypassPermissions` / `default` / `plan` / `dontAsk` / `auto` |

在 cc-connect 的 `args` 里加 `"--permission-mode", "bypassPermissions"` 即可
（位置：`"--acp-transport","stdio"` 之后、`"--mcp-config"` 之前）。

**注意**：`default` 模式下任何工具调用都会挂起；`acceptEdits` 只自动过编辑类，命令类仍会挂（治不了本）。
本例挂起的那次是「grep 工作区外的目录」，属于路径越界审批。

### 修法 B：DSH（依赖 profile 补丁，两层）

DSH 的沙箱与审批**由一个开关派生**（见 `@deepseek-ai/dsh-base/cordis.patch.yml`）：

```yaml
sandbox-policy.mode : process.env.DSH_PERMISSION_MODE ?? 'workspace-write'
user-approval.policy: (DSH_PERMISSION_MODE ?? 'workspace-write') === 'danger-full-access' ? 'never' : 'ask'
```

由于 dsh 会清理子进程环境、且不依赖 cc-connect 是否支持传 env，**直接把两者写死**在
`~/.dsh/profiles/acp/cordis.patch.yml`（**只作用于桥接 profile**，web/tui 不受影响）：

```yaml
- id: sandbox-policy
  config:
    mode: danger-full-access
    workspaceRoot: !!js process.cwd()   # patch 是整行替换，workspaceRoot 必须重述
- id: user-approval
  config:
    policy: never
- id: permission                          # ← 少了这段会起不来！
  config:
    presets:
      read-only:        { sandbox: read-only,        approval: ask }
      workspace-write:  { sandbox: workspace-write,  approval: ask }
      danger-full-access: { sandbox: danger-full-access, approval: never }
    defaultPreset: danger-full-access
```

**最容易踩的连环坑**：只改前两项、不写第三项，dsh 直接起不来，报：

```
Error: dsh: plugin tree failed to load: failed to apply loader entry permission
(@deepseek-ai/dsh-permission-presets):
permission: composed sandbox and approval defaults match no preset; configure defaultPreset explicitly
```

即：组合出来的值必须能在预设表里**显式对上某个具名预设**，服务拒绝「推导出的 custom 状态」。
表现上是 agent 握手成功、但 prompt 后**收不到任何消息**（探针会显示"没收到任何 method"）。

**顺带修掉的无关 bug**：原先 `workspace-write` 沙箱在本机启动就失败
（`Windows ACL temp root must be outside the workspace: workspace=C:\Users\<USER>;
temp=C:\Users\<USER>\AppData\Local\Temp`），导致 DSH 的 shell 工具根本跑不起来。关沙箱后该报错消失。

### 修法 C：claudecode project（Claude Code）—— 最容易漏的一个

上面两种修法都只覆盖 ACP 类（WorkBuddy / DSH）。**`type = "claudecode"` 的 project
有自己一套权限开关，很容易被漏掉**，而它的症状和 ACP 完全一样：回合永久挂起、微信侧「完全没回」。
（2026-09-14 深夜实际踩到：三个 project 里只漏了这一个。）

改法（`~/.cc-connect/config.toml`，该 project 的 `[projects.agent.options]`）：

```toml
[projects.agent.options]
work_dir = "C:\\Users\\<USER>"
mode = "bypassPermissions"   # 原为 "default"
```

`mode` 的可选值与语义（摘自 config.toml 自带注释）：

| 值 | 语义 |
|---|---|
| `default` | **每次工具调用都要人工确认** ← 桥接场景必卡 |
| `acceptEdits` | 只自动通过文件编辑，其他仍要确认 |
| `plan` | 只规划不执行 |
| `auto` | 由 Claude 自己判断何时需要确认 |
| `bypassPermissions` | 全部自动通过（桥接要用这个） |
| `dontAsk` | 未预授权的工具**自动拒绝**（更安全，但会让未列出的工具静默失效） |

静态自证（不用真跑）：

```bash
# cc-connect 二进制里确实有这些字符串 → mode 会映射到 claude 的 --permission-mode
grep -ao "permission-mode\|bypassPermissions\|allowed_tools" \
  D:/npm-global/node_modules/cc-connect/bin/cc-connect.exe | sort | uniq -c
# claude CLI 也确实接受这些值
"D:/npm-global/node_modules/@anthropic-ai/claude-code/bin/claude.exe" --help | grep -A3 permission-mode
#   choices: "acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan"
```

**日志特征（和 ACP 那套不同，认这两个关键字）**：

```
level=INFO msg="claudeSession: permission request" request_id=... tool=mcp__memorix__memorix_poll
level=INFO msg="permission request"                request_id=... tool=mcp__memorix__memorix_poll
```

**怎么确认是它卡了**：看 `~/.cc-connect/sessions/my-project_*.json` 的 history ——
会**停在 `[user]` 而没有对应的 `[assistant]`**；同时 `ps` 里能看到
`claude.exe --permission-prompt-tool stdio` 一直挂着（它还带着自己的 MCP 子进程）。

**一个很有用的旁证**：`~/.claude/settings.local.json` 的 `permissions.allow` 里列了
`mcp__memorix__memorix_project_context` / `memorix_store` / `memorix_search` /
`memorix_session_start` / `memorix_detail` / `memorix_codegraph_status`，
**却没有 `memorix_poll`** —— 因为 poll 是 team 档才有的工具，之前的 allow 列表
不可能包含它。所以第一条「需要真调工具」的 `@claude` 消息必然卡在 poll 上。
（此前所有 `@claude` 测试都是纯闲聊、不需要工具，因此一直没暴露。）

> 顺带说明：这个 project 用的 claude CLI 是**走本机代理**的
> （`~/.claude/settings.json` 里 `ANTHROPIC_BASE_URL=http://127.0.0.1:15721`），
> 所以它的行为与 WorkBuddy / DSH 相互独立，权限问题也要单独修。

### 本机触发 agent（不想等微信时）：`cron add` + `cron exec`

cc-connect 内置定时任务，可以**不经过微信**直接把一个 prompt 喂给某个 project 的 agent：

```bash
CC=D:/npm-global/node_modules/cc-connect/bin/cc-connect.exe
# 建一个「几乎不会自动触发」的任务（9/14 23:59 只在某年才命中一次），然后手动触发
"$CC" cron add -p my-project -c "59 23 14 9 *" --prompt "<你的验证 prompt>" --silent --desc "本机自测"
"$CC" cron exec <id>          # 立即触发
"$CC" cron list               # 看 id / 状态
"$CC" cron del <id>           # 用完删掉
```

⚠️ **最大的坑：cron 的回复会照常发到平台（微信）**。所以要避免打扰用户，
prompt 必须让 agent 最终**只输出 `NO_REPLY`**（cc-connect 会静默丢弃）；否则用户会收到
一条自己没问过的消息 —— 这正是用户明确反感的行为，用之前先想清楚。

好处是：验证完可以直接在 `cc-connect.log` 里看 `turn complete ... tools=N silent=true`，
`tools>=1` 就证明「工具调用没被权限卡住」。**注意别用 `* * * * *` 这类高频表达式**
（会在你删掉之前反复触发）。

### 验证工具：`scripts/acp_permission_probe.py`

直接以 ACP 握手 agent，统计收到的所有 method，**重点看有没有 `session/request_permission`**；
并把权限请求自动应答为「允许」以便观察后续（仅诊断用，别把这段逻辑搬进生产）。

```bash
python acp_permission_probe.py "运行 pwd 命令，告诉我当前工作目录是什么"        # 探 DSH
python acp_permission_probe.py --cb "运行 pwd 命令，告诉我当前工作目录是什么"   # 探 CodeBuddy CLI
```

实测对照（2026-09-14）：

| 对象 | 权限请求次数 | 正文 | 判定 |
|---|---|---|---|
| DSH（改前） | **1** | 「我没能真正执行 pwd：沙箱启动就失败了…按规则我申请了一次 danger-full-access 升级重试，被你驳回了」 | 会挂起 |
| DSH（改后） | **0** | 「当前工作目录是：`C:\Users\<USER>`」 | ✅ 工具真的能跑 |

> 探针里有个反面教材值得记：把权限应答写成 `optionId: "allow_once"` 时，DSH 判定为
> **「被你驳回了」** —— 说明它认的 optionId 不是这个名字。所以探针的用途只是「探测是否要权限」，
> 不要指望它替你做正确的批准。

## ⚠️ 坑 5：微信里突然收到一大段「错误: [vdb-node] server ready …」（假报警）

### 症状

用户明明没提问，微信却收到一条以「错误」开头的长消息，内容是 MCP 子进程的启动横幅：

```
错误: [vdb-node] server ready
[vdb-node] FIRST stdin data: bytes=164 raw="{...}"
[argo-mcp] starting (lazy imports enabled)
[memorix] MCP Server running on stdio (profile: lite, project: wjxn13/memorix-shared)
(node:27264) ExperimentalWarning: SQLite is an experimental feature ...
[memorix] Tool profile: lite (core memory + sessions, 20 tools)
```

### 根因：不是 bug，是「杀进程」的副作用 + stderr 透传

`cc-connect.log` 里的三行连起来看就明白了：

```
level=ERROR msg="acp: process exited" error="exit status 0xffffffff" stderr="[vdb-node] ..."
level=INFO  msg="unsolicited events detected, relaying to platform"
level=ERROR msg="unsolicited agent error" error="<同一大段 stderr>"
```

- `exit status 0xffffffff` = 进程被**强制结束**（本次是我的重启杀掉了 agent）。
- agent 的 MCP 子进程在启动时把横幅打到 **stderr**；而 `acp-thought-filter.js` 原本是
  `child.stderr.pipe(process.stderr)` **原样透传**。
- cc-connect 收到这些 stderr 后判定为 `unsolicited agent error`（未被请求的 agent 错误），
  **直接转发到微信**。

stderr 平时是「静默攒着」的，**杀进程那一刻才被刷出去** —— 所以表现得很突兀，
用户会觉得「我没问啊，怎么突然报错」。

### 修法：噪音只落档、不转发

在 `acp-thought-filter.js` 里把 stderr 从「透传」改成「判别后转发」：

```js
const NOISE_RE = new RegExp('^\\s*(' +
  '\\[vdb-node\\]|\\[argo-mcp\\]|\\[memorix\\]|\\[acp-thought-filter\\]' +
  '|\\(node:\\d+\\)' +                    // Node 的 (node:1234) xxxWarning
  '|\\(Use `node --trace-warnings|\\[UNDICI-EHPA\\]|EnvHttpProxyAgent|ExperimentalWarning' +
  ')');
// 命中 → 只 append 到 ~/.cc-connect/logs/acp-agent-stderr.log，不写 process.stderr
// 未命中 → 照常转发（真正的报错不能被吞掉）
```

**为什么不是直接丢掉 stderr**：真报错（认证失败、插件加载失败等）还得能让 cc-connect 报出来，
所以是**白名单式抑制噪音**而非全量静默；被抑制的行也**留档**，排查时仍能看到。
落档路径可用 `ACP_FILTER_STDERR_LOG` 覆盖。

### 重启顺序（这次踩的）—— 必须「先父后子」

既然 stderr 是在**杀进程时**刷出去的，那么：

| 顺序 | 结果 |
|---|---|
| ❌ 先杀 agent，再杀 cc-connect | 旧过滤器把 stderr 转发给还活着的 cc-connect → **用户在微信收到一条假错误** |
| ✅ **先杀 cc-connect**，再清残留子进程 | cc-connect 已经不在了，没人转发 → 微信静默 |

这与「回收孤儿进程」的顺序要求**正好相反**（那边要先子后父以免留孤儿）。
务实做法：**先杀 cc-connect，再杀残留后代**；两者兼顾。当然，在**没有 agent 在运行**时
重启（刚启动完、还没来过消息）就完全没有这个问题。

### 验证

跑 `scripts/acp_filter_test.py "@claude，你好"`，对比改前改后：

| 指标 | 改前 | 改后 |
|---|---|---|
| 透出的 stderr 噪音行 | 十几行（`[vdb-node]` / `[argo-mcp]` / `[memorix]` / warning） | **0 行** |
| 思考块过滤判定 | PASS | **PASS（仍然是恰好 `NO_REPLY`）** |
| 噪音去向 | 直接进 cc-connect | `~/.cc-connect/logs/acp-agent-stderr.log`（带时间戳） |

## 回收重启后的孤儿 agent 进程

cc-connect 重启（或被杀）后，Windows 不会连带杀掉子进程 → agent 及其 MCP 子进程全部变成孤儿，
每个几十 MB、还会继续占着 memorix 的 SQLite 句柄。**判定孤儿要按「祖先链是否可达活进程」，别按时间猜**：

```powershell
# 用 WMIC 的 Bash 调用不可靠；改用「落盘再读」的 PowerShell 套路
$all = Get-CimInstance Win32_Process
$ids = @{}
foreach ($p in $all) { $ids[[int]$p.ProcessId] = $p }
# 对每个 node.exe：看 ParentProcessId 是否还在 $ids 里，不在就是 DEAD → 孤儿
```

注意两点：

1. **一定不要碰 `WorkBuddy.exe` 的后代** —— 桌面版自己的 MCP（sheetagent / weixinpay）就挂在其下。
2. 父进程显示 `cmd.exe` 的那批（weixinpay MCP 等）也是桌面版经由 cmd 拉起的，不要动。

本次实测：清掉 5 组共 12 个孤儿（21:35 / 21:56 / 22:06 三次旧会话的 codebuddy + dsh + argo + 过滤器），
`node.exe` 进程数 34 → 22 → 8。**更稳的做法是重启 cc-connect 前先杀它的子进程**，就不会产生新孤儿。

## memorix 档位：lite 不含「协调工具」→ 人机共聊必须用 team（2026-09-14 实测）

`memorix serve` 只有 stdio 一种传输，但工具档位有四档：
`micro`（9）/ `lite`（20）/ `team`（28）/ `full`（40+）。
官方对 lite 的说明是 **"without team tools"** —— 也就是「发消息 / 收件箱 / poll / 看板」
这一类**协调工具只在 team 及以上档才存在**。

实测 `scripts/memorix_team_probe.py`（对比 lite / team 两档的工具表）：

| 档位 | 工具数 |
|---|---|
| lite | 20 |
| team | 28 |

team 档比 lite 多出的 8 个：

```
memorix_dashboard  memorix_handoff  memorix_knowledge  memorix_poll
team_file_lock     team_manage      team_message       team_task
```

**结论**：任何「人给 agent 留言 → agent 主动看收件箱」的方案，四端配置都必须把
`--mode lite` 改成 `--mode team`（`.claude.json`、`.workbuddy/mcp.json`、
`.dsh/cordis.patch.yml`、`.cc-connect/config.toml` 的 `--mcp-config` 字符串）。
team 档在 **stdio 下工作正常，不需要额外起 HTTP 服务**（join/broadcast/poll 全部真跑通）。

### 四个必踩的 API 细节（都验过）

1. **`team_message` 的 `type` 是必填**，漏了报
   `active session identity, type, and content required for send`。
2. **`from` 要「整个省略」**才走本会话身份；显式传 `from: null` 会被 schema 拒掉
   （`data/from must be string`）。
3. **`memorix_session_start` 只传 `joinTeam: true` 不够**，会被**静默跳过**：
   ```
   Coordination join skipped: pass `agent` or `agentType` to create a coordination identity.
   ```
   必须 `joinTeam: true` + `agent` + `agentType`（再加固定 `instanceId` 让身份跨重启稳定）。
   join 成功后 `memorix_poll` 才会返回 `[AGENT] You: <id> (active)` 与 `[INBOX] N unread message(s)`。
4. `team_manage action=leave` 认的是 **agentId（UUID）**，给 `instanceId` 会报 `Agent not found`。

## ⚠️ 坑 6（决定成败）：MCP 冷启动 → 规约写对了也没工具可调

### 症状

规约（「干活前先 poll 收件箱」）明明写进了 `AGENTS.md`，agent 却完全不理。

### 定位方法：读 DSH 会话记录，对比两次 LLM 请求的工具表

DSH 把每轮会话写成 `~/.dsh/sessions/--<cwd 转义>--/<sessionId>/session.v3.jsonl.zstd`。
它是**多帧拼接的 zstd**（每轮 append 一帧），Node 的同步/流式 API **只解第一帧**，
要按魔数 `28 B5 2F FD` 逐帧切（脚本 `scripts/dsh_session_read.js`）。

解出来后找 `type == "request/header"` 的行（那里面是完整的工具定义），数一数工具：

| 轮次 | 工具总数 | memorix 工具 |
|---|---|---|
| 第 1 次 LLM 请求 | 39 | **0** |
| 第 2 次 LLM 请求 | 67 | **28** |

而模型自己的推理（`assistant/message` 里）已经写明：

> "per workspace instructions I should first poll the memorix inbox ...
> but do I have memorix tools? The tool list shows no memorix_* tools."

**= 规约被读懂了，只是那一刻工具还没注册进来。**

### 根因

memorix 的 MCP 冷启动要建/加载索引，比 argo 那类 MCP 慢（实测 connect ~6s 起步）。
第一条消息进来时**第一次 LLM 请求的工具表里一个 memorix 工具都没有**，
要等它连上、**第二轮请求**才出现。所以「开工前调 memorix_poll」这种写法在第一轮注定失败。

### 修法：规约里必须给一条**不依赖 MCP** 的兜底命令

```bash
# 任何 shell 都能跑；直接走 memorix CLI，不等 MCP
C:/Users/<USER>/.workbuddy/binaries/node/versions/22.22.2-2/node.exe \
  D:/npm-global/node_modules/memorix/dist/cli/index.js \
  message inbox --mark-read --cwd D:/memorix-shared
```

要点：
- 用 **node 绝对路径 + CLI 入口**，不要写裸 `memorix`（DSH 会清理 PATH，裸命令解析不到 npm shim）。
- 加 `--mark-read` 表示「看完即已读」，否则下一条消息会让 agent 又重复一遍「已收到留言」。
- `--cwd D:/memorix-shared` 不能省（memorix 拒绝 `$HOME`，见坑 1）。

### 修约还要删掉「让模型自己拿捏」的措辞

旧版写「开工前先 poll（在你要动手干活时）」—— 模型会纠结「列个目录算不算干活」。
改成 **「先按路由协议判断该不该你答；只有该你答的消息才走这步」**，判断标准唯一，不再犹豫。

### A/B 实测（同一句 prompt，`scripts/acp_filter_test.py` 走真实 DSH 链路）

| | 旧规约 | 新规约（加兜底命令） |
|---|---|---|
| 第 1 个动作 | 直接 `pwsh` 列目录 | **先跑 CLI 兜底查收件箱** |
| 回复开头 | 无 | **「已收到共享记忆留言 INBOX-TEST-0914，协作规约生效。」** |

回归测试（`@claude，你好`）：仍然是**恰好 `NO_REPLY`、且没有任何 `tool_call`** ——
例外条款生效，点名制静默没被破坏。

## ⚠️ `acp_filter_test.py` 的坑：`content` 可能是列表

旧版 `reader()` 里写死 `u.get("content", {}).get("text", "")`。真实任务里会出现
`tool_call` / `tool_call_update`，它们的 `content` 常常是**列表**，于是在列表上抛
`AttributeError` → **把 reader 线程打死** → 后面所有帧都收不到 → 结果误判成 FAIL。
已修成 `extract_text()`（dict / list 都兼容），并把 `tool_call` 的标题与关键入参打出来
（`[TOOL]` 行），这样一眼能看出它到底调了哪些工具。

另外判定逻辑也改了：只有「本该静默」的 prompt（以 `@claude` / `@wb` 开头）才期望恰好
`NO_REPLY`；真实任务当然会有正文，不该按 NO_REPLY 判 FAIL。

## 本机 PowerShell 输出坑（用于以上验证）

本机 PowerShell 工具常出现「exit 0 但无 stdout」，且 HKLM 写入需管理员（当前工具进程非管理员）。可靠做法：命令内 `Set-Content` 落盘 → 用 Read 工具读文件；或用 Bash 只读命令（ls / du / grep）验证。`reg.exe` 在程序黑名单里，不可调用。
