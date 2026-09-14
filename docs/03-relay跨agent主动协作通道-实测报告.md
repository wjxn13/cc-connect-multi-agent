# cc-connect `relay`（跨 agent 主动协作通道）实测报告 · 终版

- 时间：2026-09-15 00:07 – 00:20
- 目的：验证「信使 = 电话交换机」——agent 能否主动拨号叫醒另一个 agent
- **最终结论：通路已打通，两跳接力实测成功；但发现两个真实缺陷，都有可行的补救办法**

---

## 一、一句话结论

**`relay` 是可用的，agent 之间「拨号 → 叫醒 → 取回结果」这条链真的跑通了**，
而且**目标 agent 自己再拨号给第三个 agent 也成功了**（两跳接力）。

但有两个不期而遇的问题：

1. **`CC_SESSION_KEY` 环境变量没有注入**（只有 `CC_PROJECT` 注入了），
   导致 agent **无法零参数拨号** —— 与官方注入提示里「环境变量已经替你设好了」的说法不符。
   补救：在命令里显式带上 `-f` 和 `-s`，**已验证可行**。
2. **relay 的返回内容没有清洗**，会混进目标 agent 的**流式工具调用碎片**
   （`📤 **call_xxx**` 前缀 + 十几段被切开的内容）。
   微信通道上我们有 `acp-thought-filter.js` 做过滤，**relay 通道上这道过滤不存在**。

---

## 二、实测数据（全部真实执行）

| # | 时间 | 路径 | 发起的 | 结果 | 耗时 |
|---|---|---|---|---|---|
| 1 | 00:09 | my-project → my-dsh | 我（外部 CLI） | ❌ `relay: no binding for this chat` | 0s |
| 2 | 00:13 | — | **用户**在微信发 `/bind my-dsh` | ✅ 三个 project 全部登记进同一聊天绑定 | — |
| 3 | 00:15:11 | my-project → my-dsh | 我 | ✅ 返回 `RELAY-OK-0915`（13 字符，干净） | **9s** |
| 4 | 00:16:18 | my-project → my-dsh | 我（探测环境变量） | ✅ 返回：只有 `CC_LOG_FILE` / `CC_LOG_MAX_SIZE` / `CC_PROJECT`，**无 `CC_SESSION_KEY`** | 18s |
| 5 | 00:16:46 | my-project → my-dsh → my-workbuddy | 第 1 跳我，**第 2 跳 DSH 自己** | ✅ 两跳打通，末尾拿到 `CHAIN-LINK-OK` | 46s |

关键日志（第 5 次，两跳）：

```
00:17:28  relay: turn complete  from=my-dsh        to=my-workbuddy  response_len=1008
00:17:32  relay: turn complete  from=my-project    to=my-dsh        response_len=2500
```

→ **中间那一跳是 DSH 自己发起的**（`from=my-dsh`），说明「agent 主动找同伴」不是理论，是真的。

---

## 三、机制细节（这次才搞清楚的）

### 3.1 绑定是「聊天级」的，且一次绑定会带上整个聊天里的所有 bot

用户只发了 `/bind my-dsh`，日志显示三个 project **逐个被加入同一个绑定**：

```
00:13:00  relay: project added to binding  chat_id=dm  project=my-workbuddy  bots=map[my-workbuddy:my-workbuddy]
00:13:00  relay: project added to binding  chat_id=dm  project=my-dsh        bots=map[my-dsh my-workbuddy]
00:13:00  relay: project added to binding  chat_id=dm  project=my-project    bots=map[my-dsh my-project my-workbuddy]
```

落盘为 `~/.cc-connect/relay_bindings.json`：

```json
{ "dm": { "platform": "weixin", "chat_id": "dm",
  "bots": { "my-dsh": "my-dsh", "my-project": "my-project", "my-workbuddy": "my-workbuddy" } } }
```

**含义**：一次 `/bind` 就把三个 agent 互相拨号的资格全开了，不需要逐对绑定。

### 3.2 relay 会为目标侧**另开独立会话**，不污染人和 agent 的主对话

`active_session` 映射（每个 project 的会话文件里）：

| project | 用户主会话 | relay 会话 |
|---|---|---|
| my-project | `weixin:dm:o9cq…@im.wechat` → `s1` | （它是发起方，无） |
| my-dsh | 同上 → `s1` | **`relay:my-project:weixin:dm` → `s2`** |
| my-workbuddy | 同上 → `s1` | **`relay:my-dsh:weixin:dm` → `s2`** |

会话键格式：**`relay:<发起方项目>:<平台>:<聊天id>`**。

**好处**：agent 之间的对话和历史跟你的人类对话**天然隔离**，不会把你和 Claude 的聊天记录搅乱。
**代价**：见 3.3。

### 3.3 relay 的对话内容**不落地**

`my-dsh` 的 `s2` 会话里 `history: null`，`updated_at` 等于创建时间 —— 也就是说
**转发的内容和回复都不写进会话历史**，只在日志里留一行 `relay: turn complete … response_len=N`。

这直接修正你方案里的一条：你写的「消息和任务都进库，天然有审计记录」——
**对 memorix 成立，对 relay 不成立**。relay 是通话，说完就散；要留痕必须显式写进 memorix。

---

## 四、两个缺陷与补救

### 缺陷 1：`CC_SESSION_KEY` 未注入 → agent 不能零参数拨号

实测 agent 直接执行 `cc-connect relay send --to my-workbuddy "..."` 会失败：

```
Error: session key is required (set CC_SESSION_KEY or use --session-key)
```
中文：缺少会话标识，请设置 `CC_SESSION_KEY` 或使用 `--session-key`。

但注入给 agent 的系统提示原文是 *"Environment variables CC_PROJECT and CC_SESSION_KEY
are already set"* —— **只对了一半**：`CC_PROJECT=my-dsh` 确实注入了，`CC_SESSION_KEY` 没有。

**补救（已验证可行）**：命令里显式带上会话标识。已确认可用的完整形式：

```bash
D:/npm-global/node_modules/cc-connect/bin/cc-connect.exe relay send \
  -f <你自己的项目名> \
  -s "weixin:dm:<YOUR_OPENID>@im.wechat" \
  --to <目标项目名> "消息"
```

→ 第 5 次实测就是靠这个形式跑通两跳的。

### 缺陷 2：relay 返回未清洗，混入工具调用碎片

第 5 次实测的返回长这样（节选）：

```
📤 **call_00_6bupCByBRofEx2VUGNiU7425**
---
📤 **call_f191859a0acc4801aa21041f**
---
{"command": "C:/Users
📤 **call_f191859a0acc4801aa21041f**
---
/<USER>/.
📤 **call_f191859a0acc4801aa21041f**
---
workbuddy/bin
...（十几段，把一条命令切得七零八落）
```

拼起来其实是目标 agent 执行**协作规约里的收件箱检查**时的工具调用，
最后一行才是真正的答案 `CHAIN-LINK-OK`。

两个含义：
- ✅ **好消息**：协作规约在 relay 会话里**也生效**了（agent 确实先查了收件箱）。
- ❌ **坏消息**：微信通道有 `acp-thought-filter.js` 清洗思考块，**relay 通道没有这道工序**，
  工具调用痕迹会原样漏进返回值。要用 relay 就必须自己加一层清洗（取最后一段 / 按标记切分）。

---

## 五、这次实测对方案的修正

| 原判断 | 修正后 |
|---|---|
| 「不需要自建 3213 服务，cc-connect 已内置」 | ✅ 成立 |
| 「agent 拨号零门槛，环境变量已就绪」 | ⚠️ **半错**：`CC_PROJECT` 有，`CC_SESSION_KEY` 没有 → 命令里必须显式 `-f/-s` |
| 「relay 是零配置的」 | ❌ **错**：需要一道一次性人工闸门（聊天里发 `/bind <项目名>`） |
| 「消息天然有审计记录」 | ⚠️ **对 memorix 成立，对 relay 不成立**（relay 不留痕） |
| 「阶段 1 聊天页复用 relay 做唤醒」 | ✅ 方向对，但页面无法自己建绑定，且要处理返回清洗 |

**阶段 1 的额外设计约束**（新增）：
1. 启动时用一次 relay 试拨来**探测绑定是否就绪**，未就绪则引导用户去微信 `/bind`。
2. 唤醒按钮要**过滤 relay 返回**（只取最终正文），否则界面会被工具碎片刷满。
3. 若要「接力全流程可见」，光靠 relay 不够 —— **relay 不留痕**，
   想让你的手机/页面看到完整链条，得让 agent 在每一步额外 `message send` 到 memorix
   （或上游给 relay 加落地）。

---

## 六、环境改动与回滚

| 项 | 内容 |
|---|---|
| 备份 | `D:\backups\cc-connect-relay-test-20260915\config.toml.bak` |
| 已改 | `config.toml` 第 292-294 行：启用 `[relay]` 段，`timeout_secs=120`、**`visibility="none"`** |
| 已产生 | `~/.cc-connect/relay_bindings.json`（你 `/bind` 产生的，**建议保留** —— 这是 relay 的通行证） |
| 已重启 | cc-connect 新 PID 11060（旧 13480 已强杀，无残留） |
| 微信影响 | **零**。`visibility="none"` 生效，整个测试期间没有一条测试消息发到你微信 |
| 回滚 | 还原 config.toml 备份 → `daemon stop` → 强杀 → `daemon start` |

**待你决定的**：`visibility` 要不要从 `none` 改成 `full` / `summary`？
- `none`（当前）：微信干净，但你看不到 agent 之间的对话内容
- `summary`：只回声摘要
- `full`：全程可见 —— 你说的「手机依次收到三条结果」需要这个（或让 agent 显式发消息给你）

---

## 七、上游可提的两个 issue（素材已齐）

1. **`CC_SESSION_KEY` 未注入**，但注入提示里声称已设置 → agent 按提示直接调 relay 必然失败。
   附：实测报错原文 + 环境变量清单 + 复现命令。
2. **relay 返回未做思考/工具痕迹过滤**，与微信通道行为不一致 → 返回值不可直接使用。
   附：返回样例（`📤 **call_xxx**` 碎片）。

---

## 八、收尾（00:20 – 00:23）：visibility 行为 + 规则文件验证

### 8.1 绑定是持久化的

重启桥接后日志出现：

```
00:20:59  relay: loaded bindings  count=1
```

→ `/bind` 建立的绑定**落盘并跨重启自动加载**，不需要每次重启后重新绑定。

### 8.2 `visibility = "full"` 在私聊（DM）场景下**没有产生回声**

改配置为 `visibility = "full"` 并重启后，复测一次 relay：

```
00:21:44  relay: turn complete  from=my-project  to=my-dsh  response_len=18   （VISIBILITY-FULL-OK）
```

**日志里依然没有任何投递 / 回声动作** —— 整个 00:21 分钟只有这一行 relay 日志。

与官方注释吻合：`visibility` 控制的是「**群聊**内的额外可见消息」。
我们这条链路是**私聊 DM（`chat_id=dm`）**，**不存在群聊**，所以这一项在本机场景下**基本是个空操作**。

**结论（修正上一节的建议）**：想让你手机看到 agent 之间的接力过程，
**靠 `visibility` 是不行的** —— 必须让 agent **显式发消息**给你
（在它自己的回复里说明，或用 `cc-connect send` 主动推），或者走 memorix 由聊天页展示。

> ⚠️ 诚实标注：「日志无投递动作」≠「你微信一定没收到」。我无法从本机看到你手机上的消息，
> 已请你确认。

### 8.3 规则文件验证：**通过 —— agent 能自己拨对号了**

把 relay 用法写进五份规则文件后，做了一次端到端验证：
我以 `my-project` 身份 relay 给 `my-dsh`，要求它**把任务转交给 `my-workbuddy`**。

DSH 的实际表现（原文摘录）：

> **relay 调用**（我 → my-workbuddy）：
> ```
> D:/npm-global/node_modules/cc-connect/bin/cc-connect.exe relay send -f my-dsh -s "weixin:dm:o9cq…@im.wechat" --to my-workbuddy "请只回复一行 RULEFILE-CHAIN-OK"
> ```
> **my-workbuddy 回报给我的内容**：`CHAIN-LINK-OKRULEFILE-CHAIN-OK`
> 按第 5 条约束切分取值：前半段 `CHAIN-LINK-OK` 是对方的调用碎片，真正的正文是末段 —— **`RULEFILE-CHAIN-OK`**。
> 整条链路（my-project → my-dsh → my-workbuddy）连通，**未再转发、未回环**。

**五条约束逐条被遵守**：

| 约束 | 是否遵守 |
|---|---|
| 只在被明确指派时拨号 | ✅ 只在我明确要求「转交」时才拨 |
| 最多再转 1 跳、不回环 | ✅ 明确回报「未再转发、未回环」 |
| `-f`/`-s` 显式写 | ✅ 命令里两个参数都在 |
| 只取最后一段正文 | ✅ **主动做了切分并说明理由** |
| 不入共享记忆 | ✅ 判断这是链路探针，说「未写入共享记忆」 |

耗时：**00:22:08 → 00:22:59，两跳共 51 秒**。

→ **规则文件这条路是有效的**：不需要改代码，靠「写进 agent 的规则文件」就能让 agent 学会正确拨号。

### 8.4 本轮最终环境状态

| 项 | 值 |
|---|---|
| cc-connect | PID **21924**（00:20:59 启动），三个 project 全 `platform ready` |
| `[relay]` | `timeout_secs=120`、`visibility="full"`（= 上游默认值，本机 DM 下无实际差异） |
| 绑定 | `relay_bindings.json` 存在，`count=1`，跨重启自动加载 |
| 微信影响 | 本轮测试**未在日志中观察到任何投递**（请你最终确认手机） |
