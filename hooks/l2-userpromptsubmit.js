#!/usr/bin/env node
// cc-connect L2 拦截钩子（多平台版）—— 2026-09-15 建，2026-09-16 加平台分支
//
// 目的：在 agent **调用模型之前**拦掉「点名了别的 agent」的消息，省掉那次 LLM 调用。
//
// 为什么必须按平台分支：三家的「阻断写法」实测交集 = 只有 stdout JSON 一条 ——
//   claudecode : 认 stdout 的 {"decision":"block"}（exit 2 无效）
//   workbuddy  : {"continue":false} / {"decision":"block"} / exit 2 三者都认
//   dsh        : 认 stdout 的 {"decision":"block"}；
//                ⚠️ exit 2 是官方 BLOCKING_EXIT_CODE，但在 Windows 上被 PowerShell
//                改写成 1（详见 dsh 档案），**不可用** —— 这是 2026-09-16 实测推翻的。
// 所以由环境变量 CC_BRIDGE_AGENT 或命令行 --agent 选定「我方是哪家」，据此挑正则与输出格式。
//
// 唯一不变量：拦截理由里必须写**实际**被点名的那个 agent。曾经硬编码 @dsh，
// 导致拦 @wb 时理由也说「点名了 @dsh」，排查时被带偏（2026-09-16 修）。
//
// 完整背景、三个陷阱的实测报文、以及引擎侧需要的两处补丁：
//   见本仓库 docs/06-L2前置拦截-三平台落地报告.md
// 回归测试（31 例，离线、不花 token）：
//   python scripts/l2_hook_tests.py

const fs = require('fs');
const os = require('os');
const path = require('path');

// 日志落到 cc-connect 的日志目录（与桥接主日志同处，便于对照时间线）。
// 用 os.homedir() 而不是写死用户名，方便换机器 / 别人复现；需要覆盖时设 CC_BRIDGE_L2_LOG。
const LOG = process.env.CC_BRIDGE_L2_LOG
  || path.join(os.homedir(), '.cc-connect', 'logs', 'l2-hook.jsonl');
const GATE_VAR = 'CC_BRIDGE_L2';

// ── 启动探针（2026-09-18 加）────────────────────────────────────────────────
// 目的：一刀切开「hook 根本没被调用」与「被调用了但卡在 stdin / 解析」两种可能。
//
// 背景：桥接链路的 claudecode 在正式日志 `l2-hook.jsonl` 里长期零记录，
//       而脚本本体已被证明是好的（手动喂 stdin 能正常 block + 写日志）。
//       于是只剩两种解释，且它们对排查方向的意义完全相反：
//         (a) Claude Code 压根没执行这条 hook  → 得从「配置/信任/启动方式」入手
//         (b) 执行了，但 stdin 迟迟不 end（或 JSON 结构不符）→ 得从「hook 协议」入手
//       下面这条记录写在**任何 stdin 操作之前**，所以它出现 = (b)，不出现 = (a)。
//
// 为什么单独一个文件：正式日志 `l2-hook.jsonl` 的行数一直被当作实验口径
//       （「215 → 215 零增长」），不能掺入噪音行，否则历史对比失效。
//
// 顺带价值：三个 agent 共用本脚本，这一行还能显示「谁真的调了 hook、谁没调」。
const START_LOG = process.env.CC_BRIDGE_L2_START_LOG
  || path.join(os.homedir(), '.cc-connect', 'logs', 'l2-hook-start.jsonl');
try {
  const argAgent = (() => {
    const i = process.argv.indexOf('--agent');
    if (i >= 0 && process.argv[i + 1]) return process.argv[i + 1];
    const eq = process.argv.find((a) => a.startsWith('--agent='));
    return eq ? eq.slice('--agent='.length) : '(未指定)';
  })();
  fs.appendFileSync(
    START_LOG,
    JSON.stringify({
      ts: new Date().toISOString(),
      phase: 'start',
      agent: argAgent,
      envAgent: process.env.CC_BRIDGE_AGENT || null,
      gated: process.env[GATE_VAR] === '1',
      pid: process.pid,
      cwd: process.cwd(),
      stdinIsTTY: !!process.stdin.isTTY,
      argvRaw: process.argv.slice(2).join(' '),
    }) + '\n',
    'utf8'
  );
} catch (_) {
  // 探针失败绝不能影响主流程
}

// ── 平台档案 ────────────────────────────────────────────────────────────────
// others       : 「点名了别人」的前缀正则。负向先行断言 (?![a-z0-9_]) 避免把
//                "@dshx" 之类误判成点名。
// labels       : 别名 → 规范标签（理由里要写规范的那个）。
// requireGate  : 是否要求 CC_BRIDGE_L2=1 才生效。
//                claudecode 的钩子装在**全局** ~/.claude/settings.json 里，
//                必须靠 env 门控，否则用户自己开的会话也会被拦；
//                workbuddy 的钩子是用 --settings 只注入给桥接进程的，
//                天然隔离，再加门控反而会因 env 漏配而静默失效 → 不要求。
const PROFILES = {
  claudecode: {
    others: /^@(dsh|workbuddy|wb)(?![a-z0-9_])/i,
    labels: { dsh: '@dsh', workbuddy: '@workbuddy', wb: '@wb' },
    requireGate: true,
    respond: (who) => ({
      decision: 'block',
      reason: 'L2: 这条消息点名了 ' + who + '，不由本 agent 应答',
    }),
  },

  workbuddy: {
    // 本 agent 是 @wb / @workbuddy，所以「别人」是 @dsh / @claude。
    others: /^@(dsh|claude)(?![a-z0-9_])/i,
    labels: { dsh: '@dsh', claude: '@claude' },
    requireGate: false,
    // ⚠️ reason 必须**恰好**是 NO_REPLY，不能多一个字。
    //    CodeBuddy 的 HookBlockedError 会把 reason 原文当阻断文本记进事件流，
    //    cc-connect 据此走 ACP 投递；写 NO_REPLY 才能命中它的静默正则
    //    (?i)^\s*NO_REPLY\s*$。写别的（哪怕更详细）就会原样发到微信。
    respond: () => ({ continue: false, reason: 'NO_REPLY' }),
  },

  dsh: {
    // 本 agent 是 @dsh，所以「别人」是 @claude / @wb / @workbuddy。
    others: /^@(claude|workbuddy|wb)(?![a-z0-9_])/i,
    labels: { claude: '@claude', workbuddy: '@workbuddy', wb: '@wb' },
    requireGate: false,
    // ⚠️ DSH 的阻断协议 —— 2026-09-16 读源码确认（比 README 更硬）：
    //   @deepseek-ai/dsh-hook-protocol/lib/index.js
    //     const BLOCKING_EXIT_CODE = 2
    //     if (exitCode === BLOCKING_EXIT_CODE) { output.decision = "block";
    //                                            if (stderr) output.reason = stderr.trim() }
    //     rank("block") = 3  →  mergeHookOutputs()  →  decisionForRank(3) = "deny"
    //   插件 @deepseek-ai/dsh-hooks-claude-code 的 agent/pre-step：
    //     if (merged.decision === "deny") return { kind: "reject" };   // 模型不跑
    //   两条推论：
    //     ① （已作废，见下方 🔴）当时以为「必须用 --block-style exit2」；
    //     ② reason 写 NO_REPLY，与 WorkBuddy 对齐，好让 cc-connect 侧复用同一套
    //        静默/剥离逻辑。
    //
    //    🔴 2026-09-16 实测：**不能用 exit 2** —— 尽管它是官方的 BLOCKING_EXIT_CODE。
    //       原因：DSH 的 ctx.shell 在 Windows 上走 PowerShell（源码注释：
    //       "the win32 layer swaps the POSIX rows for the pwsh ones"），而 PowerShell
    //       执行原生命令时只要子进程往 stderr 写了东西，就会当成 NativeCommandError
    //       → **把退出码改写成 1**（不是 2）。实测（读 DSH 会话的 hook/result 事件）：
    //         {"exitCode":1,"decision":"pass","stderrSummary":"NO_REPLY"}
    //       → 非 2 即「非阻断」，模型照跑（同轮 request/header 有记录为证）。
    //       改用 **stdout JSON**（走 exit 0）后 PowerShell 不介入退出码，链路就通了。
    //
    //    ⚠️ 另注：DSH 执行 hook 命令时会加前缀，且不做 shell 层面的引号转义 ——
    //       命令里**不能写引号**，否则 PowerShell 把它们当字符串字面量、报「意外的标记」。
    //       本机两条路径都无空格，所以裸写即可（见 dsh-hooks.json）。
    //
    //    ⚠️ 再注：**不能用 {continue:false}**。插件 agent/pre-step 只检查
    //       merged.decision === "deny"，`continue:false` 在那条路径上不被处理
    //       （它只影响 Stop 事件），所以写了也不会阻断。
    respond: () => ({ decision: 'block', reason: 'NO_REPLY' }),
  },
};

function writeLog(rec) {
  try {
    const dir = path.dirname(LOG);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    fs.appendFileSync(LOG, JSON.stringify(rec) + '\n', 'utf8');
  } catch (_) {
    // 记日志失败绝不能影响主流程：钩子出错的代价是整个会话不可用。
  }
}

// 剥掉**开头**的 <system-reminder>…</system-reminder> 块（可连续多段）。
//
// 为什么需要（2026-09-16 实测 DSH）：同一会话的后续消息，payload.prompt 有时会带上
// 工作区指令注入块，实测到的形态：
//   "<system-reminder>\nThe following workspace instructions may b…"
// 而点名判定是**开头锚定**（^@xxx）—— 一旦被这类块挡在前面就会**漏拦**：
// 钩子放行 → 模型真的跑 → token 白花。（不会刷屏：agent 侧的 NO_REPLY 约定仍兜底，
// 但「拦在模型调用之前」这个目的就落空了。）
//
// 只剥开头的块：正文中间出现 <system-reminder> 时不动，避免误伤正常内容。
function stripLeadingSystemReminder(text) {
  let out = text;
  for (;;) {
    const m = out.match(/^\s*<system-reminder>[\s\S]*?<\/system-reminder>\s*/);
    if (!m) return out;
    out = out.slice(m[0].length);
  }
}

// 我方平台：优先取命令行参数 --agent <名>（自包含、不依赖 env 传递），
// 其次取环境变量 CC_BRIDGE_AGENT，都缺则按 claudecode 处理。
// 为什么优先命令行：WorkBuddy 的钩子是通过 --settings 注入的，把平台名写在同一行
// 命令里最不容易失效（不用指望 cc-connect 的 env 一定传到 hook 子进程）。
const argvAgent = (() => {
  const i = process.argv.indexOf('--agent');
  if (i >= 0 && process.argv[i + 1]) return process.argv[i + 1];
  const eq = process.argv.find((a) => a.startsWith('--agent='));
  return eq ? eq.slice('--agent='.length) : '';
})();
const agentName = String(argvAgent || process.env.CC_BRIDGE_AGENT || 'claudecode').toLowerCase();
const profile = PROFILES[agentName] || PROFILES.claudecode;

// 阻断输出方式：默认 json；--block-style exit2 改为「退出码 2 + stderr 写理由」。
const styleIdx = process.argv.indexOf('--block-style');
const blockStyle = String(
  (styleIdx >= 0 && process.argv[styleIdx + 1]) || process.env.CC_BRIDGE_BLOCK_STYLE || 'json'
).toLowerCase();

let raw = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => { raw += chunk; });
process.stdin.on('end', () => {
  let payload = {};
  try { payload = JSON.parse(raw); } catch (_) { /* 拿不到就按放行处理 */ }

  const prompt = String(payload.prompt || '').trim();
  // 匹配用「剥掉开头注入块」之后的文本；日志里同时留原始头与被匹配的头，便于排查。
  const matchText = stripLeadingSystemReminder(prompt);
  const gated = process.env[GATE_VAR] === '1';
  const gateOk = profile.requireGate ? gated : true;
  const named = profile.others.exec(matchText);
  const targetOther = named !== null;
  const block = gateOk && targetOther;

  const who = named ? profile.labels[named[1].toLowerCase()] : null;

  writeLog({
    ts: new Date().toISOString(),
    agent: agentName,
    gated,
    required: profile.requireGate,
    targetOther,
    block,
    namedAgent: who,
    promptHead: prompt.slice(0, 60),
    // 与 promptHead 不同的唯一情形 = 开头确实有注入块被剥掉了。排查「为什么没拦住」时先看这里。
    matchHead: matchText.slice(0, 60),
    cwd: payload.cwd || null,
    sessionId: payload.session_id || null,
  });

  if (block) {
    // 阻断方式：
    //  json  = 往 stdout 写 JSON（默认）
    //  exit2 = 把理由写 stderr 并以退出码 2 结束（DSH 只认这种；此处用于实验）
    if (blockStyle === 'exit2') {
      process.stderr.write(String(profile.respond(who).reason || 'NO_REPLY') + '\n');
      process.exit(2);
    }
    process.stdout.write(JSON.stringify(profile.respond(who)) + '\n');
  }
  process.exit(0);
});
