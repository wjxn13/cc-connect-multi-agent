# -*- coding: utf-8 -*-
"""公开前的仓库审计：扫 HEAD 全部文件 + 全部历史 blob。

扫描目标是「一旦公开就收不回来」的那类信息：
凭证、真实标识、内网地址、本机用户名路径。

用法:
    python tools/audit-before-public.py            # 审计当前仓库
    python tools/audit-before-public.py D:/some/repo

退出码: 0 = 干净；1 = 有命中（需人工过一遍再决定是否公开）。

注意：本脚本必须在 git 仓库内运行。它读的是 git 对象（`git show HEAD:<file>`
与 `git cat-file`），不是工作区文件 —— 这样连「删过但还在历史里」的内容也能查到，
这正是强推之后旧提交仍按 SHA 可达这类问题的检出口。
"""
import os
import re
import subprocess
import sys

REPO = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.getcwd()

PATTERNS = [
    ("微信 open_id（完整）",      re.compile(r"o9cq[0-9A-Za-z_\-]{8,}")),
    ("会话键含完整 open_id",       re.compile(r"weixin:dm:(?!<)[0-9A-Za-z_\-]{20,}")),
    ("OpenAI 风格 key",            re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("GitHub token",               re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")),
    ("私钥",                       re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("赋值型凭证",                 re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|app_?secret|corp_?secret|encoding_?aes_?key)\b\s*[:=]\s*[\"'][^\"'<>{}\s]{8,}")),
    ("手机号",                     re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("邮箱（排除占位/im.wechat）",  re.compile(r"[A-Za-z0-9._%+\-]+@(?!im\.wechat|example\.com|users\.noreply\.github\.com)[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("UUID（memorix 身份等）",     re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")),
    ("内网/本机 IP",               re.compile(r"\b(?:10\.\d{1,3}|192\.168\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3})\.\d{1,3}\b")),
    ("公网 IP",                    re.compile(r"\b(?!(?:10|127|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.)(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("含 token 的 URL",            re.compile(r"https?://[^\s\"'<>]*[?&](?:access_token|token|key|secret)=", re.I)),
    ("本机用户名路径",             re.compile(r"(?i)[A-Z]:[\\/]Users[\\/][^\\/\s\"']+|/Users/[^/\s\"']+")),
    ("GitHub PAT 前缀",            re.compile(r"github_pat_")),
]

SENSITIVE_FILENAMES = re.compile(
    r"(?i)(^|/)(config\.toml|config\.json|\.env|id_rsa|.*\.pem|.*\.pfx|.*\.key|.*\.log|.*\.token|.*\.secret)$"
)


def run(args, binary=False):
    return subprocess.run(args, cwd=REPO, capture_output=True, text=not binary).stdout


def check(text, label, hits):
    for name, rx in PATTERNS:
        for m in rx.finditer(text):
            frag = m.group(0)
            # 截断长片段，避免把敏感原文再打印一遍
            if len(frag) > 40:
                frag = frag[:40] + "…"
            hits.append((name, label, frag))


def main():
    hits = []
    # ── 1) HEAD 工作树文件 ──
    files = [f for f in run(["git", "ls-files"]).splitlines() if f.strip()]
    for f in files:
        try:
            blob = run(["git", "show", "HEAD:%s" % f], binary=True)
        except Exception:                                    # noqa: BLE001
            continue
        text = blob.decode("utf-8", "replace")
        check(text, "HEAD:" + f, hits)
        if SENSITIVE_FILENAMES.search(f):
            hits.append(("可疑文件名", "HEAD:" + f, f))

    # ── 2) 全部历史 blob（含被删过的）──
    objs = run(["git", "rev-list", "--all", "--objects"]).splitlines()
    seen = set()
    for line in objs:
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        sha, path = parts
        if sha in seen:
            continue
        seen.add(sha)
        if SENSITIVE_FILENAMES.search(path):
            hits.append(("可疑文件名", "历史 blob", path))
        try:
            blob = run(["git", "cat-file", "-p", sha], binary=True)
            text = blob.decode("utf-8", "replace")
        except Exception:                                    # noqa: BLE001
            continue
        check(text, "历史:%s" % path, hits)

    print("仓库：%s" % REPO)
    print("扫描文件数（HEAD）：%d" % len(files))
    print("扫描对象数（历史）：%d" % len(seen))
    print("=" * 88)
    if not hits:
        print("未发现任何命中项。")
        return 0

    # 同类归并，只展示前几条
    by_kind = {}
    for name, label, frag in hits:
        by_kind.setdefault(name, []).append((label, frag))
    for name, items in sorted(by_kind.items(), key=lambda kv: -len(kv[1])):
        print("\n【%s】命中 %d 处" % (name, len(items)))
        for label, frag in items[:12]:
            print("   %-58s %s" % (label[:58], frag))
        if len(items) > 12:
            print("   … 另有 %d 处" % (len(items) - 12))
    return 1


if __name__ == "__main__":
    sys.exit(main())
