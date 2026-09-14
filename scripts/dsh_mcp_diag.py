import subprocess, json, time, threading

# 目的：抓取 DSH 启动 MCP server 时的 stderr，定位 memorix / argo 为何没有挂载。
NODE = r'C:/Users/<USER>/.workbuddy/binaries/node/versions/22.22.2-2/node.exe'
DSH = r'D:/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js'

p = subprocess.Popen(
    [NODE, DSH, '--profile', 'acp'],
    cwd=r'C:/Users/<USER>',
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding='utf-8', errors='replace', bufsize=1,
)

def err_reader():
    for line in p.stderr:
        s = line.rstrip()
        if s:
            print('[DSH-STDERR]', s[:260], flush=True)

threading.Thread(target=err_reader, daemon=True).start()

def send(o):
    p.stdin.write(json.dumps(o) + chr(10))
    p.stdin.flush()

send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
      'params': {'protocolVersion': 1, 'clientCapabilities': {}}})
time.sleep(3)
send({'jsonrpc': '2.0', 'id': 2, 'method': 'session/new',
      'params': {'cwd': 'C:/Users/<USER>', 'mcpServers': []}})

time.sleep(35)
print('--- 采集结束 ---', flush=True)
p.kill()
