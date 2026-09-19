# patches/ —— cc-connect 引擎侧补丁

本目录是**在 [cc-connect](https://github.com/chenhg5/cc-connect) 上做的本地补丁**，分两组，
**解决两个完全独立的问题**：

| 组 | 补丁 | 解决什么 | 位置 |
|---|---|---|---|
| **A. 点名路由** | `0007` – `0008` | 让「@ 了谁」决定消息进哪个 agent | **L1**，消息进 agent **之前** |
| **B. hook-block 静默** | `0001` – `0006` | 让「被前置钩子拦掉」的回合不要漏出空响应/包装文本 | **L2 的配套**，模型调用**之前**拦、拦完**输出**要干净 |

两组**互不依赖，可单独套用**：

- 只套 B → 「纯扇出 + 前置拦截」：谁都被投递，但点名别人的那条不进模型；
- 只套 A → 「点名路由 + 提示词静默」：消息就进对被点名的那一个，静默靠提示词约定；
- 都套 → `docs/07` 描述的三层防线（效果最好，也是本机在跑的形态）。

> 三层防线（L1 关卡 / L2 前置钩子 / L3 输出静默）为什么是**叠加**而不是互相替代，
> 见 `docs/07-点名路由与三层防线-落地报告.md` 第五节。

---

# A 组：点名路由（0007 – 0008）

## 为什么需要

`docs/05` 测清楚了原生 cc-connect 的真实行为：**入站是扇出，不是路由**。
一条微信消息会被原样投给配置里的每一个 project，于是

> 每条用户消息 = **N 次完整 LLM 调用**（N = 挂微信的 project 数）

`docs/06` 的 B 组补丁把「点名了别人」的那条拦在了模型之前，但有三类漏网（详见 `docs/07` 第一节）：

1. 没点名任何人的消息（它不属于任何人，于是被投给所有人）；
2. 斜杠命令（关卡与钩子都对其放行）；
3. **图片** —— 微信没法在一条消息里同时发文字和图片，所以「@dsh」和图片**必然分两条到达**，
   第二条不含点名信息，看起来就是「没点人」。

外加一个纯粹的可用性问题：三个 agent 共用一个微信窗口时，**回复里看不出是谁答的**，
报错时尤其致命（说「会话启动失败」，但不说**谁的**）。

## 补丁清单

| # | 提交 | 作用 |
|---|---|---|
| `0007` | `feat(core): add cross-agent mention gate, agent labels and sticky media routing` | **主补丁（+984 行）**：引擎层点名关卡、粘性媒体路由、出站身份前缀、以及三处「共用账号时才暴露」的文案修正 |
| `0008` | `test(core): cover mention routing, agent labels and queued messages` | 离线测试（+1176 行），内容全部是新增的 `_test.go` |

### `0007` 具体改了什么

| 关注点 | 落地物 |
|---|---|
| 配置 | `[[mention_agents]]`（`project` / `aliases` / `default` / `media_default` / `enabled`）、`[display].agent_label`、`[[projects]].label`，以及加载期校验 |
| 判定 | `mentionGateBlocked` / `mentionGateDrop` / `mentionsSelf`（读**第一个** @ 记号） |
| 别名 | `NormalizeMentionAlias`（折叠大小写 / 空白 / 前导 `@`）+ **最长别名前缀**匹配 |
| 粘性 | `core/mention_sticky.go`（TTL 3 分钟，只对**无文字**消息生效） |
| webhook | `executePrompt` 入口补一次关卡判定（否则 webhook 是关卡的旁路） |
| 出站 | `stampAgentLabel` 施加在引擎的**两个发送咽喉** |
| 文案 | 排队提示补「第几位 / 谁在占用 / 将由谁执行 / 怎么改派」；会话启动失败补「是哪个 project 失败」 |

### 一个容易踩的坑：`default` 决定「没点名时怎么办」

`default = "silent"` 的 project 收到「没点名任何人」的消息时不会回答。
这正是「图片正确投递之后仍然没人理」的常见原因之一：
投递对了，但这个 project 的默认动作是闭嘴，而带图那条消息**没有可被点名的文字**。
所以 `media_default` 认领媒体、和 `default` 决定是否作答，是**两件独立的事**。

## 已验证（A 组）

- ✅ 对本机基线（`v1.3.4` + `0001`–`0006`）**依次应用通过**，应用结果与主线提交
  **逐字节一致**（复核方式：`git diff <主线提交> --stat` 为空）。
- ✅ 应用后 `go build ./core/ ./config/ ./cmd/cc-connect/` 通过；
  `go test ./core/ ./config/` 通过。
- ✅ 端到端（三 agent 桥接实测，2026-09-19）：

  ```
  mention gate: media message kept    project=my-dsh        owner=my-dsh
  mention gate: media message dropped project=my-workbuddy token=(media)
  mention gate: media message dropped project=my-project   token=(media)
  ```

- ⚠️ **对上游 `main` 不能直接应用**（`0007` 在 `core/engine.go` 首个 hunk 冲突）。
  `0001`–`0006` 在上游 `main` 上仍然 6/6 干净，只有 A 组需要 rebase。
  根因推测：本机基座 `v1.3.4` 落后上游两个小版本，`engine.go` 的结构体定义区已有差异。
  要往上提的话，先 rebase 再走 PR。

---

# B 组：hook-block 静默（0001 – 0006）

## 为什么需要

当 `UserPromptSubmit` 钩子阻断一条消息时，**模型根本没被调用**，agent 侧不会产生正常的回复文本。
但 cc-connect 的适配器并不知道这一点，于是有两种「无正文」形态各自漏给了用户：

| 形态 | 现象 | 由哪个补丁治 |
|---|---|---|
| agent **吐了文本**，但文本是钩子阻断的包装 | 包装文本被当回复投递；更糟的是它**会被携带到下一轮**、与真回复直接粘连 | `0005` |
| agent **一个字都没吐** | 落到 `MsgEmptyResponse` 占位符 `(空响应)` 并被投递 | `0006` |

> ⚠️ **只修一半是最容易犯的错** —— 两种形态成因不同、出现在不同平台上：
> 前者是 Claude Code 的行为（它会留下 `HookBlockedError` 文本），
> 后者是 ACP agent 的行为（DSH 被 `{kind:"reject"}` 拦掉时真·零输出）。

## 补丁清单（按应用顺序）

| # | 提交 | 作用 | 是否通用 |
|---|---|---|---|
| 0001 | `feat(core): silence Claude Code hook-block result text` | 新增 `isHookBlockedResponse()`（**前缀锚定**，避免误吞正文里提到该短语的正常回复），并在静默判定里用上 | 通用 |
| 0002 | `test(core): add engine-path and reverse-control coverage for hook-block silence` | 补引擎路径与反向对照测试 | 通用 |
| 0003 | `build(web): make frontend build work on this machine` | pnpm workspace 改 `nodeLinker: hoisted` + 放行 esbuild postinstall | **仅本机**（Windows 上编前端才需要，可跳过） |
| 0004 | `test(core): pin the necessity of the patch with real-run evidence` | 用现场取证到的真实阻断文本钉住前提（断言它不被原有 `NO_REPLY` 正则命中） | 通用 |
| 0005 | `fix(core): strip ACP hook-block wrapper instead of silencing the whole reply` | **把策略从「整条静默」改成「剥离包装、投递剩余真回复」** | 通用 |
| 0006 | `fix(core): suppress the empty-response placeholder for hook-blocked turns` | agent 零输出时静默掉 `(空响应)` 占位符 | 通用 |

### 关于 0001 → 0005 的策略变化（重要，别被提交历史绕晕）

`0001` 的第一版策略是：**只要检测到阻断文本，整条静默**。

这个策略有致命缺陷：阻断文本**会被携带到下一轮**、和下一轮的真回复粘连成一条。
于是「整条静默」会把**那一轮的真回复一起吞掉** ——
用户看到的现象是「**每拦一次，我下一条消息就没回**」。

`0005` 把它改成「**先剥离包装、只投递剩下的真回复**」，并用三场景实测 3/3 钉死：

| 场景 | 回复长度 | 期望 |
|---|---|---|
| A 纯阻断（没有真回复） | 35 字节 | 静默 |
| B 包装 + 真回复粘连 | 46 字节（= 真回复长度） | **投递**（若未剥离会是 81） |
| C 干净轮 | 37 字节 | 正常投递 |

所以这两个提交**不是互相矛盾的重复**，而是同一处策略的修正过程；如果要往上提，`0001 + 0005`
应当合并成一个提交来讲这个故事。

---

# 怎么用

```bash
git clone https://github.com/chenhg5/cc-connect.git
cd cc-connect
git checkout v1.3.4          # 本套补丁的基线

git am /path/to/patches/*.patch
```

只用其中一组也可以（两组互不依赖）：

```bash
# 只要点名路由
git am /path/to/patches/0007-*.patch /path/to/patches/0008-*.patch

# 只要 hook-block 静默（跳过仅本机需要的 0003）
git am /path/to/patches/0001-*.patch /path/to/patches/0002-*.patch \
       /path/to/patches/0004-*.patch /path/to/patches/0005-*.patch \
       /path/to/patches/0006-*.patch
```

编译（**不要加 `-tags no_web`**，否则 Web 管理后台整个丢失）：

```bash
go build -ldflags "-s -w -X main.version=v1.3.4" -o cc-connect.exe ./cmd/cc-connect
```

> Windows + git bash 注意：`git am` 的**参数路径别写成 `/d/...`**。
> git 是原生 Windows 程序，会把 `/d/xxx` 解析成 `D:\d\xxx`（少一个反斜杠就差一个目录），
> 表现为「文件明明在、git 说找不到」或补丁被写到诡异位置。用 `D:/xxx`。

# 已验证（总览）

| 项 | 结果 |
|---|---|
| 对基线 `v1.3.4` 完整 `git am` | ✅ `0001`–`0008` 全部干净应用 |
| 对上游 `main`（tip `757b4df`，2026-09-10） | ⚠️ `0001`–`0006` **6/6 干净**；`0007`–`0008` **需 rebase** |
| A 组应用结果的正确性 | ✅ 与主线提交逐字节一致；编译 + 单测通过 |
| B 组相关单测 | ✅ `go test ./core/ -run 'TestProcessInteractiveEvents_(HookBlocked|EmptyResponse)'` |

# 上游现状（2026-09-19 现查，未采信记忆）

- 上游 `main` 最新提交仍是 `757b4df fix(cron): cap timer span to 30s to recover from system sleep (#1808)`
  （2026-09-10）；最新 release 仍是 **v1.5.0**（2026-08-16）。
  → 与 2026-09-16 核对时**完全一致**，即上游这三周没有动过相关区域。
- 上游 `main` **至今没有**处理 hook-block 文本，也**没有**点名路由。
  当时的核对命令与结果（`FETCH_HEAD` = 757b4df）：

  ```bash
  git grep -n "MsgEmptyResponse" FETCH_HEAD -- '*.go'
  # → core/engine.go:5767   fullResponse = e.i18n.T(MsgEmptyResponse)
  #   全仓仅此一处调用，且是**无条件赋值**（没有任何条件静默）

  git grep -niE "hookBlocked|prevent_continuation|blocked by hook" FETCH_HEAD -- '*.go'
  # → 空。上游完全不认这类文本
  ```

- 本人在该仓库**没有任何** PR / Issue。

→ 结论：**两组补丁都仍然有效、且都尚未上游化。** 是否要提 PR 由你决定；
提的话建议按 `docs/06` / `docs/07` 的叙事拆成两个 PR（点名前移、hook-block 静默），
其中 B 组的 `0001` 与 `0005` 应合并、并去掉仅本机需要的 `0003`。
