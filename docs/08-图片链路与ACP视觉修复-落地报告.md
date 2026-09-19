# 图片链路：为什么「发过去的图」agent 收不到

> 测试时间：2026-09-19
> 测试目的：查清「微信发了图片、DSH 没有任何反应」的根因
> 结论：**图片一次都没有丢，它只是发错了家**；顺着链路查下去，还挖出一个与图片无关的模型路由错配
> 关联：`docs/07-点名路由与三层防线-落地报告.md`（媒体归属由 L1 的粘性点名决定）、
> `patches/0007`（媒体路由改动）、`config/config.example.toml`（`media_default`）

---

## 一、现象与第一层误解

现象：微信里发了图片，DSH（`my-dsh`）毫无反应。

第一反应是「图片没送到」。**这个判断是错的。** 把桥接日志按时间线排开之后，真相是：

| 环节 | 实际发生的事 |
|---|---|
| 附件落盘 | 两张 jpg **都正常落盘**（`~/.cc-connect/attachments/`，各约 871 KB） |
| `my-workbuddy` | 收到后按规则丢弃 |
| `my-dsh` | 收到后按规则丢弃 |
| `my-project`（默认接图方） | **接走了，并且完整处理**（一次 `tools=7 response_len=1882`，耗时 1m48s） |

→ 图片被处理了，只是**被另一个 agent 处理了**。用户的视角里「DSH 没反应」，
系统的视角里「任务完成了，只是执行者不是你想的那个」。

**教训**：排查「消息丢了」之前，先看**每个 project 各自的 default 归属**，
而不是只盯着你以为的那个 project 的日志。多 agent 共用一个入口时，
「没人处理」和「别人处理了」在单个 agent 的视角下长得一模一样。

> 归属问题由 `docs/07` 的 L1 粘性点名解决；本文余下部分处理另一个独立问题：
> **图片到了 agent 手里之后，agent 能不能真的看懂它。**

---

## 二、ACP 通道「只传路径，不传像素」

这是排查中被代码证实的第一件事，也是理解后续所有现象的前提。

cc-connect 的 ACP 适配器在 `Send()` 里构造 prompt 时：

```go
prompt = s.appendImageRefs(prompt, images)   // 图片先落盘到 attachments/
promptBlocks := []any{
    map[string]any{"type": "text", "text": prompt},   // ← 永远只有一个「文本」块
}
```

`appendImageRefs` 追加的内容形如：

```
(Image files saved locally: <path>)
```

⇒ **走 ACP 的 agent 从不接收图片像素**，它只拿到一个文件路径，需要自己按路径把文件读出来。

这一点直接决定了后面的判据：ACP agent 要「看见」图，必须同时满足

1. 它**愿意并且会**去读那个路径（工具/技能层）；
2. 它**当前使用的模型**声明支持图片输入（模型层）。

---

## 三、根因：模型路由写死在 bundle 里

### 3.1 报错长这样

DSH 尝试读图时被自己的工具层拒绝，错误信息指向**模型能力**而不是文件：

```
cannot read "<path>" as an image: model "<model>" does not declare image input;
switch to an image-capable model to read images
```

拒绝逻辑（`dsh-tool-fs`）大意是：

```js
const routed = exec.agent?.session.requestHeader()?.config;   // 取 ACP 会话路由
const model  = routed?.model ?? exec.agent?.options.model;
const active = await llm.resolveModelInfo(provider, model, exec.signal);
if (active.inputModalities === void 0 || !active.inputModalities.includes("image"))
    throw new Error("... does not declare image input ...");
```

关键：**`model` 取自 ACP 会话的路由配置**，而不是「agent 的默认模型」。

### 3.2 两处配置，只有一处管用

| 配置 | 作用 | 能否决定读图 |
|---|---|---|
| `~/.dsh/profiles/acp/cordis.patch.yml` 里的 `agent-default-model` | agent 的默认模型 | **不能** |
| `node_modules/@deepseek-ai/dsh-acp-app/cordis.patch.yml` 里的 `- id: acp → config.model` | **ACP 会话实际路由** | **能** ← 就是这里 |

第二处随 bundle 安装，内容是写死的：

```yaml
- id: acp
  config:
    provider: deepseek-official
    model: deepseek-v4-flash      # ← 目录里「没声明 image」的那一条
```

而模型目录（`dsh-llm-deepseek` 的 `DEFAULT_MODELS`）里，`inputModalities` 是**声明式**的：

| 模型 id | 声明 | 实际能力 |
|---|---|---|
| `deepseek-flash`（V4.1-Flash） | `["text","image"]` | **有视觉**（下文实测证实） |
| `deepseek-v4-flash` | 未声明 → 按 schema 退化为 `["text"]` | 被工具层判为「不能读图」 |
| `deepseek-v4-pro` | 未声明 | 同上 |
| `deepseek-v4-flash-vision-exp` | `["text","image"]` | 有视觉 |

> schema 里 `inputModalities` 的默认值是 `["text"]`，所以**判断依据是「有没有显式声明」，
> 而不是「模型真实能力」**。这就制造了一个很难从现象反推的错配：
> **能力更强的模型，因为没写声明，反而被当成不能看图。**

### 3.3 修复

在 profile 的 patch 层追加一条覆写（不动 bundle 里的原文件）：

```yaml
- id: acp
  config:
    provider: deepseek-official
    model: deepseek-flash
```

两条硬注意：

1. **patch 是整行替换 `config`，不合并**。所以要先把原本的 `config` 有哪些键查清楚再改，
   否则会把别的键悄悄删掉。改前务必用
   `dsh --profile <名字> --dump-config`（只打印合成后的配置树，不启动服务、不花 token）
   留一份基线，改完再 dump 一次做 diff。
2. **这类配置的生效方式是重启**（该 patch 的 `patchReload` 为 `startup`），热改不生效。

---

## 四、怎么判断一个模型到底有没有视觉

**不要用「有没有报错」当判据**——没有视觉的模型通常也返回 200，只是内容为空、
或者说一句「我无法查看图片」，看起来像「调用方式不对」。

**可靠判据是：出的题必须「可验对错」，然后看答案跟图对不对得上。**

做法要点：

- 造一张**有确定答案**的图：例如一个红圆、一个蓝方，加一串随机字符（如 `7Q3`）。
  **别用纯色图或 1×1 的退化样本**——那种图无论模型看不看得见都能蒙对，等于没测。
- 用 data URI 把图塞进 `image_url` 块，`max_tokens` 给足（≥300），避免答案被截断。
- 判据：**答案是否与图一致**。

实测结果（同一张测试图）：

| 模型 | 结果 | 判定 |
|---|---|---|
| `deepseek-flash`（V4.1-Flash） | **答对**（正确说出图形与字符） | 有视觉 |
| `deepseek-v4-pro` | 说图片无法显示，思维链里出现 `[Unsupported Image]` | 无视觉 |

→ 这条实测同时纠正了一个**从行为反推出来的错误结论**。
一开始曾据「DSH 读图被拒」推断「DSH 没有原生视觉」，
实测证明**模型有视觉，是路由指错了模型**。**从现象反推能力，很容易把配置问题误判成能力问题。**

---

## 五、顺手验证：中间多一层代理会不会吃掉图片

该链路里还有一个 HTTP 代理（headroom，本机 8787 端口）。图片经过它时会不会被降级？

结论：**原样透传**。判据有两条且互相印证：

- 走代理时模型**答对了**测试图；
- `prompt_tokens` 与直连一致（241），说明请求体没有被改写或丢弃内容块。

---

## 六、附：一个排查手法——怎么确认「日志里那条 tool_call 是谁发的」

多 agent 共用一个入口时，日志里会出现不属于当前排查对象的记录，很容易把结论带偏。
本机用过的一个可靠办法是**唯一标记法**：

1. 构造一条内容里带唯一字符串的消息（如 `ZZMQ_MARKER_9F3A_probe` 这类不可能自然出现的串），
2. 发出去，然后在日志里搜这个串。

**出现 = 这条确实由该路径处理；不出现 = 与它无关。** 比按时间戳猜、按 project 名猜都可靠。

同一类交叉验证还包括：确认配置文件在整个排查过程中**未被改写**
（比对 mtime，排除「结论是并发写配置导致的」）、确认某工具在某批调用里**根本没被使用**
（统计出现次数为 0）。这些都为「排除法」提供了硬依据，而不是「我觉得」。

---

## 七、已知边界

- **`media_default` 只解决归属，不解决理解**。图片正确投递到某个 agent 之后，
  能不能看懂仍取决于该 agent 的读图工具与其模型路由，两者互相独立。
- **模型目录的声明与实际能力可能不一致**（见 3.2）。判断一个路由能否读图，
  以**实测**为准，不要只看目录里的声明。
- **本次修复改动了 agent 实际使用的模型**（`deepseek-v4-flash` → `deepseek-flash`）。
  由于静默机制（`docs/07` 的 L3）靠提示词约定实现，**换模型后必须复跑一次静默复查**：
  故意发一条只 @ 了别人的消息，确认仍会闭嘴。
