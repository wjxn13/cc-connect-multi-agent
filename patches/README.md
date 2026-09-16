# patches/ —— cc-connect 引擎侧补丁

本目录是**在 [cc-connect](https://github.com/chenhg5/cc-connect) 上做的本地补丁**，用来让
「agent 被前置钩子拦掉」的回合**不要被当成正常回复发给用户**。

没有这些补丁，L2 前置拦截（见 `hooks/`）**看起来能用、实际会漏内容进聊天窗口**。

---

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

## 怎么用

```bash
git clone https://github.com/chenhg5/cc-connect.git
cd cc-connect
git checkout v1.3.4          # 本补丁的基线

git am /path/to/patches/*.patch
```

只想编一个能跑的二进制、不需要前端改动时，跳过 `0003`：

```bash
git am /path/to/patches/0001-*.patch /path/to/patches/0002-*.patch \
       /path/to/patches/0004-*.patch /path/to/patches/0005-*.patch \
       /path/to/patches/0006-*.patch
```

编译（**不要加 `-tags no_web`**，否则 Web 管理后台整个丢失）：

```bash
go build -ldflags "-s -w -X main.version=v1.3.4" -o cc-connect.exe ./cmd/cc-connect
```

---

## 已验证

- ✅ **对基线 `v1.3.4` 完整 `git am` 通过**（6/6 干净应用）。
- ✅ **对上游 `main` 也 6/6 干净应用** —— 核对时的上游 tip：
  `757b4df fix(cron): cap timer span to 30s to recover from system sleep (#1808)`（2026-09-10）。
  → 也就是说**不需要 rebase**，要往上提随时可以。
- ✅ 相关单测通过：`go test ./core/ -run 'TestProcessInteractiveEvents_(HookBlocked|EmptyResponse)'`。

## 上游现状（2026-09-16 现查，未采信记忆）

- 上游最新 release：**v1.5.0**（2026-08-16）；`main` 最新提交 2026-09-10。
  → **本机桥接基座是 v1.3.4，落后上游两个小版本**。本目录的补丁是打在旧基线之上的。
- 上游 `main` **至今没有**处理 hook-block 文本。核对命令与结果（`FETCH_HEAD` = 757b4df）：

  ```bash
  git grep -n "MsgEmptyResponse" FETCH_HEAD -- '*.go'
  # → core/engine.go:5767   fullResponse = e.i18n.T(MsgEmptyResponse)
  #   全仓仅此一处调用，且是**无条件赋值**（没有任何条件静默）

  git grep -niE "hookBlocked|prevent_continuation|blocked by hook" FETCH_HEAD -- '*.go'
  # → 空。上游完全不认这类文本

  git grep -niE "stopReason|blocked|refusal" FETCH_HEAD -- 'agent/acp/*'
  # → 空。ACP 适配器也不解析 turn/end 的 blocked 状态
  ```

- 本人在该仓库**没有任何** PR / Issue（`gh api search/issues?q=repo:chenhg5/cc-connect+is:pr+author:wjxn13` 现查，`total_count = 0`）。

→ 结论：**补丁依然有效、且尚未上游化。** 是否要提 PR 由你决定；
提的话建议把 `0001` 与 `0005` 合并、并去掉 `0003`。
