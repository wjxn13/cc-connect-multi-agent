#!/usr/bin/env node
/**
 * DSH 会话记录解读器（多帧 zstd → JSONL → 工具表/推理摘要）
 *
 * 为什么需要这个脚本：
 *   DSH 把每轮会话写成
 *     ~/.dsh/sessions/--<cwd 转义>--/<sessionId>/session.v3.jsonl.zstd
 *   这是**多帧拼接的 zstd**（每轮 turn append 一帧）。Node 的
 *   zlib.zstdDecompressSync / createZstdDecompress 都**只解第一帧**，
 *   直接解只能拿到 161 字节的会话头 —— 排查时会误以为「记录是空的」。
 *   本脚本按 zstd 魔数 28 B5 2F FD 逐帧切分再逐帧解压。
 *
 * 排查价值（2026-09-14 用它定位了 memorix 冷启动问题）：
 *   对比不同轮次 `request/header` 里的工具表，能直接看出
 *   「MCP 工具是第几轮才注册进来的」。当时结论是：
 *     第 1 次 LLM 请求 39 个工具 / 0 个 memorix；
 *     第 2 次 LLM 请求 67 个工具 / 28 个 memorix。
 *   模型因此**第一轮根本看不到 memorix_poll**，规约写得再对也没用。
 *
 * 用法：
 *   node dsh_session_read.js <session.v3.jsonl.zstd>            # 概览
 *   node dsh_session_read.js <file> dump <outPath>              # 导出完整 JSONL
 *   node dsh_session_read.js <file> text                        # 打印各轮 assistant 推理文本
 */

const fs = require('fs');
const zlib = require('zlib');

const MAGIC = Buffer.from([0x28, 0xb5, 0x2f, 0xfd]);

/** 按 zstd 魔数切帧，逐帧解压，返回拼接后的 Buffer。 */
function decompressFrames(buf) {
  const pos = [];
  for (let i = 0; i + 4 <= buf.length; i++) {
    if (buf[i] === 0x28 && buf[i + 1] === 0xb5 && buf[i + 2] === 0x2f && buf[i + 3] === 0xfd) {
      pos.push(i);
    }
  }
  if (pos.length === 0 || pos[0] !== 0) pos.unshift(0);

  const out = [];
  let i = 0;
  while (i < pos.length) {
    let decoded = null;
    // 从 pos[i] 起，帧尾由「下一个魔数」界定；失败就扩大范围重试
    for (let j = i + 1; j <= pos.length; j++) {
      const end = j < pos.length ? pos[j] : buf.length;
      try {
        decoded = zlib.zstdDecompressSync(buf.subarray(pos[i], end));
        i = j;
        break;
      } catch (e) { /* 扩大范围 */ }
    }
    if (!decoded) break;
    out.push(decoded);
  }
  return { data: Buffer.concat(out), frames: out.length, candidates: pos.length };
}

function readJsonl(file) {
  const buf = fs.readFileSync(file);
  const { data, frames, candidates } = decompressFrames(buf);
  const lines = data.toString('utf8').split('\n').filter((l) => l.trim());
  const recs = [];
  for (const l of lines) {
    try { recs.push(JSON.parse(l)); } catch (e) { /* 忽略半截行 */ }
  }
  return { recs, frames, candidates, bytes: data.length };
}

/** 递归收集所有 name 字段（工具定义里工具名就在 name）。 */
function collectNames(o, out) {
  if (Array.isArray(o)) { for (const v of o) collectNames(v, out); return; }
  if (o && typeof o === 'object') {
    if (typeof o.name === 'string') out.push(o.name);
    for (const v of Object.values(o)) collectNames(v, out);
  }
}

/** 递归收集所有 text 字段（推理与正文都在 text 里）。 */
function collectTexts(o, out) {
  if (Array.isArray(o)) { for (const v of o) collectTexts(v, out); return; }
  if (o && typeof o === 'object') {
    if (typeof o.text === 'string') out.push(o.text);
    for (const v of Object.values(o)) collectTexts(v, out);
  }
}

function overview(recs) {
  console.log('=== 记录类型分布 ===');
  const counts = {};
  recs.forEach((r) => { counts[r.type] = (counts[r.type] || 0) + 1; });
  Object.entries(counts).forEach(([k, v]) => console.log(`  ${k.padEnd(24)} ${v}`));

  console.log('\n=== 各轮 request/header 的工具表（看 MCP 是第几轮才进来的）===');
  let n = 0;
  recs.forEach((r, idx) => {
    if (r.type !== 'request/header') return;
    n++;
    const names = [];
    collectNames(r.data, names);
    const uniq = [...new Set(names)];
    const mx = uniq.filter((x) => /memorix/i.test(x));
    console.log(`  #${n} (seq=${idx})  工具 ${uniq.length} 个  其中 memorix ${mx.length} 个`);
  });
  if (n === 0) console.log('  （没找到 request/header —— 可能是第一次请求之前的记录，或结构已变）');

  console.log('\n=== 各轮 assistant 推理/正文里是否提到关键规约 ===');
  recs.forEach((r, idx) => {
    if (r.type !== 'assistant/message') return;
    const ts = [];
    collectTexts(r.data, ts);
    const blob = ts.join('\n');
    const hits = ['memorix', 'poll', 'inbox', '收件箱', '协作规约'].filter((k) => blob.includes(k));
    console.log(`  seq=${idx}  文本 ${blob.length} 字  命中关键词: ${hits.join(', ') || '(无)'}`);
  });
}

function dumpText(recs) {
  recs.forEach((r, idx) => {
    if (r.type !== 'assistant/message') return;
    const ts = [];
    collectTexts(r.data, ts);
    console.log(`\n########## seq=${idx} assistant/message ##########`);
    console.log(ts.join('\n').slice(0, 4000));
  });
}

function main() {
  const [, , file, cmd, arg] = process.argv;
  if (!file) {
    console.error('用法: node dsh_session_read.js <session.v3.jsonl.zstd> [dump <outPath>|text]');
    process.exit(2);
  }
  const { recs, frames, candidates, bytes } = readJsonl(file);
  console.log(`文件: ${file}`);
  console.log(`zstd 帧数=${frames} / 魔数候选=${candidates} / 解出 ${bytes} 字节 / ${recs.length} 条记录\n`);

  if (cmd === 'dump') {
    const out = arg || file.replace(/\.zstd$/, '');
    fs.writeFileSync(out, recs.map((r) => JSON.stringify(r)).join('\n') + '\n');
    console.log('已导出 JSONL: ' + out);
    return;
  }
  if (cmd === 'text') { dumpText(recs); return; }
  overview(recs);
}

main();
