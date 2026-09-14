#!/usr/bin/env node
/*
 * acp-thought-filter.js —— ACP 中间层代理（cc-connect 与 agent 之间）
 *
 * 职责一：丢弃「思考块」（2026-09-14 实测定位）
 *   DSH / WorkBuddy 走 ACP 协议时，思考块用标准的 `agent_thought_chunk` 发送，
 *   正文用 `agent_message_chunk` 发送 —— 两者是【分开】的，agent 侧完全规范。
 *   但 cc-connect 的 ACP 适配器不区分 sessionUpdate 类型，把两者【拼接】成一条回复，
 *   于是 `NO_REPLY` 变成 `...独白...NO_REPLY`，点名制的静默判定（整段必须等于 NO_REPLY）
 *   失效，独白被真的发到微信。
 *
 * 职责二：抑制 stderr 噪音（2026-09-14 22:56 追加）
 *   agent 的 MCP 子进程（vdb-node / argo-mcp / memorix）启动时会打一堆横幅到 stderr。
 *   若原样转发给 cc-connect，它会把这段当 `unsolicited agent error`【直接转发到微信】，
 *   用户就会莫名收到一大段「错误: [vdb-node] server ready...」。
 *   实测触发场景：重启时杀掉 agent，积压的 stderr 被整段刷出去。
 *   故：噪音行【只落档、不转发】；非噪音行照常转发（真正的报错不能被吞掉）。
 *   落档位置默认 ~/.cc-connect/logs/acp-agent-stderr.log（可用 ACP_FILTER_STDERR_LOG 覆盖）。
 *
 * 本代理插在 cc-connect 与 agent 中间：
 *   cc-connect ──stdin──> 代理 ──stdin──> 真实 agent
 *   cc-connect <─stdout── 代理 <─stdout── 真实 agent        （丢弃思考块）
 *   cc-connect <─stderr── 代理 <─stderr── 真实 agent        （只放行非噪音）
 *
 * 用法：
 *   node acp-thought-filter.js <agent命令> [参数...]
 * 例：
 *   node acp-thought-filter.js <node.exe> <dsh的bin.js> --profile acp
 *
 * 环境变量：
 *   ACP_FILTER_LOG=1          把被丢弃的思考块打到 stderr（调试用）
 *   ACP_FILTER_STDERR_LOG=<路径>  覆盖 stderr 落档位置
 *
 * 设计原则：只丢「明确是思考」的帧与「明确是噪音」的 stderr 行，其余一律原样转发；
 *          解析失败也转发（绝不影响正常通信）。
 */

const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const argv = process.argv.slice(2);
if (argv.length === 0) {
  console.error('usage: node acp-thought-filter.js <agent-command> [args...]');
  process.exit(2);
}

const [cmd, ...args] = argv;

const child = spawn(cmd, args, {
  stdio: ['pipe', 'pipe', 'pipe'],
  env: process.env,
});

// ---------- cc-connect -> agent：原样透传 ----------
process.stdin.pipe(child.stdin);
process.stdin.on('error', () => {});
child.stdin.on('error', () => {});

// ---------- agent stderr -> 落档 + 只放行「非噪音」----------
// 这些是 MCP 子进程与 Node 运行时的正常启动横幅，不是错误。
const NOISE_RE = new RegExp(
  '^\\s*(' +
  '\\[vdb-node\\]' +                    // 视频取证 MCP
  '|\\[argo-mcp\\]' +                   // argo MCP
  '|\\[memorix\\]' +                    // memorix MCP
  '|\\[acp-thought-filter\\]' +
  '|\\(node:\\d+\\)' +                  // Node 的 (node:1234) xxxWarning
  '|\\(Use `node --trace-warnings' +
  '|\\[UNDICI-EHPA\\]' +
  '|EnvHttpProxyAgent' +
  '|ExperimentalWarning' +
  ')'
);

const STDERR_LOG = process.env.ACP_FILTER_STDERR_LOG
  || path.join(os.homedir(), '.cc-connect', 'logs', 'acp-agent-stderr.log');

let stderrLogStream = null;
function logStderr(line) {
  try {
    if (!stderrLogStream) {
      fs.mkdirSync(path.dirname(STDERR_LOG), { recursive: true });
      stderrLogStream = fs.createWriteStream(STDERR_LOG, { flags: 'a' });
    }
    stderrLogStream.write('[' + new Date().toISOString() + '] ' + line + '\n');
  } catch (e) {
    /* 落档失败也不能影响主流程 */
  }
}

let ebuf = '';
child.stderr.on('data', (chunk) => {
  ebuf += chunk.toString('utf8');
  let i;
  while ((i = ebuf.indexOf('\n')) >= 0) {
    const line = ebuf.slice(0, i).replace(/\r$/, '');
    ebuf = ebuf.slice(i + 1);
    if (line.trim() === '') continue;
    if (NOISE_RE.test(line)) {
      logStderr(line);          // 噪音：只落档，不进 cc-connect（否则会被转发到微信）
      continue;
    }
    if (process.env.ACP_FILTER_LOG) logStderr(line);
    process.stderr.write(line + '\n');   // 非噪音：照常转发，真报错不能被吞
  }
});
child.stderr.on('end', () => {
  if (ebuf.trim() === '') return;
  if (NOISE_RE.test(ebuf)) {
    logStderr(ebuf);
  } else {
    logStderr(ebuf);
    process.stderr.write(ebuf + '\n');
  }
});
child.stderr.on('error', () => {});

// ---------- 判断某一行是否是「思考块」 ----------
function isThoughtFrame(line) {
  // 快速短路：非 session/update 的帧一定不是
  if (line.indexOf('session/update') === -1) return false;
  try {
    const m = JSON.parse(line);
    if (m.method !== 'session/update') return false;
    const update = (m.params || {}).update || {};
    const kind = String(update.sessionUpdate || '');
    // 标准名 agent_thought_chunk，兼顾客端可能的变体（reasoning / thinking）
    return /thought|reason|thinking/i.test(kind);
  } catch (e) {
    return false; // 解析失败一律不丢，避免破坏协议
  }
}

// ---------- agent -> cc-connect：按行过滤 ----------
// ACP over stdio 是 newline-delimited JSON，每帧一行（JSON 内的换行会被转义，故安全）。
let buf = '';

child.stdout.on('data', (chunk) => {
  buf += chunk.toString('utf8');
  let idx;
  while ((idx = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, idx);
    buf = buf.slice(idx + 1);

    if (line.trim() === '') {
      process.stdout.write('\n');
      continue;
    }
    if (isThoughtFrame(line)) {
      if (process.env.ACP_FILTER_LOG) {
        console.error('[acp-thought-filter] dropped ' + line.length + ' bytes: ' + line.slice(0, 120));
      }
      continue; // 丢弃思考块
    }
    process.stdout.write(line + '\n');
  }
});

child.stdout.on('error', () => {});

// ---------- 退出处理 ----------
child.on('exit', (code) => {
  if (buf.trim() !== '') process.stdout.write(buf + '\n');
  process.exit(code === null ? 0 : code);
});

child.on('error', (err) => {
  console.error('[acp-thought-filter] failed to start agent: ' + err.message);
  process.exit(1);
});

function shutdown() {
  try { child.kill(); } catch (e) { /* ignore */ }
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
process.on('exit', shutdown);
