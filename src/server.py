#!/usr/bin/env python3
"""双 Bot 监测台 —— 本地跑，通过 SSH 读服务器状态。

用法： python tools/botmon/server.py  [--port 8791]
打开： http://127.0.0.1:8791
"""
import argparse
import base64
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import starui
except Exception:
    starui = None

try:
    import starmap
except Exception:
    starmap = None

try:
    import updater
except Exception:  # 单文件运行时可能没有同目录模块
    updater = None

CFG_PATH = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                        "BotMonitor", "config.json")


def _cfg():
    """配置只从本机文件/环境变量读，仓库里不出现服务器地址和私钥路径。"""
    d = {}
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        pass
    return d


_C = _cfg()
KEY = os.environ.get("BOTMON_KEY") or _C.get("ssh_key", "")
HOST = os.environ.get("BOTMON_HOST") or _C.get("ssh_host", "")
REMOTE_PROBE = _C.get("remote_probe", "/opt/botmon/probe.py")
KB_PATH = _C.get("kb_path", os.path.join(os.path.expanduser("~"), "OneDrive", "文档",
                                        "deepseek-harness", "default-workspace", "运维知识库.md"))

STATE = {"data": {}, "qr": b"", "err": "", "at": 0.0, "client": []}
LOCK = threading.Lock()


# exe 是 --windowed 的（没有控制台），每次起 ssh 子进程 Windows 都会给它新开一个
# 控制台窗口——轮询 5 秒一次，屏幕上就一直闪黑框。必须显式禁止创建窗口。
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _hidden_si():
    if os.name != "nt":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0          # SW_HIDE
    return si


def ssh(cmd, timeout=40):
    # 必须显式 utf-8：Windows 默认按 GBK 解码，中文会炸成 UnicodeDecodeError 并把 stdout 变成 None
    return subprocess.run(
        ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no",
         "-o", "ConnectTimeout=12", "-o", "BatchMode=yes", HOST, cmd],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=_CREATE_NO_WINDOW, startupinfo=_hidden_si())


STREAM_CMD = (
    "while true; do python3 %s; sleep 15; done"
)


def stream_loop():
    """一条长连接流式取数据。

    2026-11-09 两次 SSH 断连的教训：
      · 客户端重连间隔固定 5 秒 = 反复握手，短时间几百次登录，
        既像暴力破解（会被云安全中心 / 登录防护盯上），也会把自己堵死
      · 所以这里改成指数退避，失败一次等 30 秒，最长 5 分钟，
        并且「任何情况下都不许 30 秒内重连」
    """
    fail = 0
    while True:
        got_data = False
        try:
            proc = subprocess.Popen(
                ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no",
                 "-o", "ConnectTimeout=12", "-o", "BatchMode=yes",
                 "-o", "ServerAliveInterval=20", "-o", "ServerAliveCountMax=3",
                 HOST, STREAM_CMD % REMOTE_PROBE],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1,
                creationflags=_CREATE_NO_WINDOW)
            for line in proc.stdout:
                line = (line or "").strip()
                if not line.startswith("{"):
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                got_data = True
                mi = data.get("mi") or {}
                b64 = mi.pop("qr_b64", "") or ""
                png = b""
                if b64:
                    try:
                        png = base64.b64decode(b64)
                    except Exception:
                        png = b""
                with LOCK:
                    STATE["data"] = data
                    STATE["at"] = time.time()
                    STATE["err"] = ""
                    if png:
                        STATE["qr"] = png
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        except Exception as e:  # noqa: BLE001
            with LOCK:
                STATE["err"] = str(e)[:300]

        if got_data:
            fail = 0
            wait = 10
        else:
            fail += 1
            wait = min(300, 30 * (2 ** min(fail - 1, 4)))
            with LOCK:
                if not STATE["err"]:
                    STATE["err"] = "连接中断，%d 秒后重试（第 %d 次）" % (wait, fail)
        time.sleep(wait)


ACTIONS = {
    "restart-napcat": "docker restart napcat",
    "restart-astrbot": "docker restart astrbot",
    "watchdog": "python3 /opt/napcat-watch/watchdog.py; tail -1 /var/log/napcat-watch.log",
    "logs-napcat": "docker logs --since 5m napcat 2>&1 | grep -avE '^[0-9a-f]{64}' | tail -25",
    "logs-astrbot": "docker logs --since 5m astrbot 2>&1 | sed 's/\\x1b\\[[0-9;]*m//g' | grep -avE 'DBUG' | tail -25",
}

PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>夏07 / 小米 监测台</title>
<style>
 :root{--bg:#0b0d12;--card:#151a23;--line:#232b38;--txt:#e8ecf4;--dim:#8b95a8;
       --ok:#39d98a;--warn:#f0b429;--bad:#ff5c5c}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--txt);
      font:14px/1.5 system-ui,"Segoe UI","Microsoft YaHei",sans-serif}
 .wrap{max-width:1080px;margin:0 auto;padding:22px 20px 40px;padding-left:88px}
 .rail{position:fixed;left:0;top:0;bottom:0;width:68px;background:#0e1219;
       border-right:1px solid var(--line);display:flex;flex-direction:column;
       align-items:center;justify-content:space-between;padding:16px 0 14px}
 .rbtns{display:flex;flex-direction:column;gap:8px}
 .rbtn{width:42px;height:42px;border-radius:12px;background:transparent;border:1px solid transparent;
       color:var(--dim);font-size:17px;cursor:pointer}
 .rbtn:hover{background:#1a2130;color:var(--txt)}
 .rbtn.on{background:#1f2a3d;border-color:#2f4467;color:#8ab4ff}
 .rver{font-size:10.5px;color:#5d6879;writing-mode:horizontal-tb}

 .smbar{display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
 .smsearch{position:relative;flex:1;min-width:200px}
 .smsearch input{width:100%;background:#0e1219;border:1px solid var(--line);
   border-radius:9px;padding:8px 11px;color:var(--txt);font-size:13px;outline:none}
 .smsearch input:focus{border-color:#35507a}
 .smres{display:none;position:absolute;z-index:20;left:0;right:0;top:38px;
   background:#111722;border:1px solid var(--line);border-radius:10px;
   max-height:280px;overflow:auto;box-shadow:0 10px 30px rgba(0,0,0,.5)}
 .smitem{padding:7px 11px;font-size:12.5px;color:#c6d2e4;cursor:pointer;
   border-bottom:1px solid rgba(40,52,70,.6)}
 .smitem:hover{background:#1a2331}
 .smwrap{display:flex;gap:12px;align-items:stretch;min-width:0}
 .smwrap canvas{border:1px solid var(--line);border-radius:14px;flex:1;
   min-width:0;display:block}
 .smdetail{width:300px;flex:0 0 300px;overflow:auto;max-height:640px}
 .smhead{font-size:12px;color:var(--dim);margin-bottom:8px}
 .smbody{font-size:13px;line-height:1.75;color:#dbe4f2;white-space:pre-wrap}
 .smmeta{margin-top:10px;font-size:11.5px;color:#68748a}
 @media(max-width:1000px){.smdetail{display:none}}
 .view{display:none}
 .view.on{display:block}
 h1{font-size:17px;font-weight:600;margin:0 0 4px}
 .sub{color:var(--dim);font-size:12px;margin-bottom:18px}
 .grid{display:grid;grid-template-columns:1fr 1fr 300px;gap:14px}
 @media(max-width:900px){.grid{grid-template-columns:1fr}}
 .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}
 .card h2{font-size:13px;font-weight:600;color:var(--dim);margin:0 0 12px;
          letter-spacing:.04em;text-transform:uppercase}
 .row{display:flex;justify-content:space-between;align-items:baseline;margin:7px 0}
 .k{color:var(--dim);font-size:12.5px}
 .v{font-variant-numeric:tabular-nums}
 .dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:7px}
 .dot.on{background:var(--ok);box-shadow:0 0 10px var(--ok)}
 .dot.off{background:var(--bad);box-shadow:0 0 10px var(--bad)}
 .dot.wait{background:var(--warn);box-shadow:0 0 10px var(--warn)}
 .name{font-size:16px;font-weight:600}
 .big{font-size:26px;font-weight:600;font-variant-numeric:tabular-nums}
 .bar{height:6px;background:#1e2530;border-radius:4px;overflow:hidden;margin-top:6px}
 .bar i{display:block;height:100%;background:linear-gradient(90deg,#4d8dff,#7fe0a8)}
 .qrbox{background:#fff;padding:10px;border-radius:12px;display:inline-block}
 .qrbox img{display:block;width:230px;height:230px;image-rendering:pixelated}
 .btns{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
 button{background:#1d2431;color:var(--txt);border:1px solid var(--line);
        padding:8px 13px;border-radius:9px;cursor:pointer;font-size:13px}
 button:hover{background:#26303f}
 button.danger{border-color:#5b2a2a}
 button.danger:hover{background:#3a1d1d}
 pre{background:#0e1219;border:1px solid var(--line);border-radius:10px;
     padding:10px;font-size:11.5px;color:#b9c4d4;overflow:auto;max-height:230px;
     white-space:pre-wrap;margin:10px 0 0}
 .warnline{color:var(--warn)}
 .err{color:var(--bad);font-size:12px;margin-top:8px}
 .chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
 .chip{background:#1b2230;border:1px solid var(--line);border-radius:999px;
       padding:2px 9px;font-size:11.5px;color:#b9c4d4}
</style></head><body><div class="rail">
  <div class="rbtns">
    <button class="rbtn on" data-p="status" title="状态">▦</button>
    <button class="rbtn" data-p="audit" title="审计">◉</button>
    <button class="rbtn" data-p="starmap" title="星图">✦</button>
    <button class="rbtn" data-p="settings" title="设置">⚙</button>
  </div>
  <div class="rver" id="rver">v—</div>
</div>
<div class="wrap">
<div class="view on" id="view-status">
<h1>夏07 / 小米 监测台</h1>
<div class="sub" id="sub">连接中…</div>

<div class="grid">
  <div class="card">
    <h2>夏07 · QQ 官方 Bot</h2>
    <div class="row"><span class="name"><span class="dot" id="x7dot"></span>夏07</span>
      <span id="x7state" class="v">—</span></div>
    <div class="row"><span class="k">近 10 分钟事件</span><span class="v" id="x7msg">—</span></div>
    <div class="row"><span class="k">容器</span><span class="v" id="abox">—</span></div>
    <div class="row"><span class="k">私聊审计</span><span class="v" id="x7audit">—</span></div>
    <div class="chips" id="x7chips"></div>
  </div>

  <div class="card">
    <h2>小米 · NapCat 协议端</h2>
    <div class="row"><span class="name"><span class="dot" id="midot"></span>小米</span>
      <span id="mistate" class="v">—</span></div>
    <div class="row"><span class="k">API</span><span class="v" id="miapi">—</span></div>
    <div class="row"><span class="k">二维码</span><span class="v" id="miqr">—</span></div>
    <div class="row"><span class="k">近 10 分钟事件</span><span class="v" id="mimsg">—</span></div>
    <div class="row"><span class="k">群审计</span><span class="v" id="miaudit">—</span></div>
    <div class="chips" id="michips"></div>
  </div>

  <div class="card">
    <h2>扫码登录</h2>
    <div class="qrbox"><img id="qr" alt="QR"></div>
    <div class="sub" style="margin:8px 0 0;font-size:11.5px">
      每 2 分钟自动换新；点下面按钮可手动拉一次
    </div>
  </div>

  <div class="card">
    <h2>服务器</h2>
    <div class="row"><span class="k">内存</span><span class="v" id="mem">—</span></div>
    <div class="bar"><i id="membar" style="width:0"></i></div>
    <div class="row" style="margin-top:12px"><span class="k">磁盘</span><span class="v" id="disk">—</span></div>
    <div class="bar"><i id="diskbar" style="width:0"></i></div>
    <div class="row" style="margin-top:12px"><span class="k">负载</span><span class="v" id="load">—</span></div>
    <div class="row"><span class="k">运行</span><span class="v" id="uptime">—</span></div>
  </div>

  <div class="card" style="grid-column:span 2">
    <h2>看门狗最近判定</h2>
    <pre id="watch">—</pre>
    <div class="err" id="err"></div>
  </div>
</div>

</div>

<!--STARMAP_VIEW-->

<div class="view" id="view-audit"><h1>审计</h1><div class="card"><h2>小米 · 群聊自检</h2><div id="aud-more" class="sub"></div></div></div>

<div class="view" id="view-settings"><h1>设置</h1><div class="card" style="max-width:520px"><h2>版本</h2><div class="row"><span class="k">当前版本</span><span class="v" id="set-ver">—</span></div><div class="row"><span class="k">最新版本</span><span class="v" id="set-remote">—</span></div><div class="btns"><button onclick="checkUpdate()">检查更新</button><button onclick="openRepo()">打开仓库</button></div><pre id="set-notes" style="display:none"></pre></div></div>

<div class="btns">
  <button class="danger" onclick="act('restart-napcat','确定重启小米？会掉线约 20 秒，登录态保留。')">重启小米</button>
  <button class="danger" onclick="act('restart-astrbot','确定重启 AstrBot？所有插件会重载约 30 秒。')">重启 AstrBot</button>
  <button onclick="act('watchdog','跑一次看门狗（会按判定结果告警/重启）')">跑一次看门狗</button>
  <button onclick="act('logs-napcat','看小米最近日志')">小米日志</button>
  <button onclick="act('logs-astrbot','看 AstrBot 最近日志')">AstrBot 日志</button>
</div>
<pre id="out" style="display:none"></pre>

<script>
// ===== 启动自检：任何脚本错误都要看得见，而不是变成"连接中…" =====
window.__bootErr = [];
window.__firstOk = false;
function __showBoot(msg){
  let el = document.getElementById('booterr');
  if (!el){
    el = document.createElement('div');
    el.id = 'booterr';
    el.style.cssText = 'position:fixed;left:0;right:0;top:0;z-index:99;background:#7f1d1d;' +
      'color:#fff;font:12px/1.5 system-ui;padding:8px 14px;white-space:pre-wrap';
    document.body.appendChild(el);
  }
  el.textContent = '面板脚本异常：\n' + window.__bootErr.slice(-4).join('\n');
  try{
    fetch('/api/clientlog', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({msg: window.__bootErr[window.__bootErr.length-1]})});
  }catch(e){}
}
window.addEventListener('error', e=>{
  window.__bootErr.push((e.message||'error') + ' @' + (e.filename||'').split('/').pop() + ':' + e.lineno);
  __showBoot();
});
window.addEventListener('unhandledrejection', e=>{
  window.__bootErr.push('rejection: ' + (e.reason && (e.reason.message||e.reason)));
  __showBoot();
});
setTimeout(()=>{
  if (!window.__firstOk){
    window.__bootErr.push('启动 3 秒内没拿到状态数据（可能是某个初始化函数抛错）');
    __showBoot();
  }
}, 3000);
</script>
<script>
const $ = id => document.getElementById(id);
function setDot(el, cls){ el.className = 'dot ' + cls; }
function ago(s){ if(s===null||s===undefined) return '—';
  if(s<90) return s+' 秒前'; if(s<5400) return Math.round(s/60)+' 分钟前';
  return (s/3600).toFixed(1)+' 小时前'; }

async function tick(){
  try{
    const r = await fetch('/api/status?t='+Date.now(), {cache:'no-store'});
    const d = await r.json();
    if (d.err) $('err').textContent = '采集出错：' + d.err;
    else $('err').textContent = '';
    const s = d.data || {};
    const mi = s.mi || {};

    // 小米
    let cls = 'off', txt = '掉线';
    if (mi.online) { cls='on'; txt='在线'; }
    else if ((mi.qr_age ?? 999) < 240) { cls='wait'; txt='等扫码'; }
    setDot($('midot'), cls);
    $('mistate').textContent = txt;
    $('miapi').textContent = mi.api_ok ? 'OK' : '不通';
    $('miqr').textContent = mi.qr_age==null ? '—' : ago(mi.qr_age);
    $('mimsg').textContent = (s.msgs?.mi ?? 0) + ' 条';

    // 夏07
    const ab = s.containers?.astrbot || '';
    const x7ok = ab.includes('Up');
    setDot($('x7dot'), x7ok ? 'on' : 'off');
    $('x7state').textContent = x7ok ? '运行中' : '容器未运行';
    $('x7msg').textContent = (s.msgs?.x07 ?? 0) + ' 条';
    $('abox').textContent = ab || '—';

    // 审计
    const a = s.audit || {};
    const fmt = o => o ? Object.entries(o.counts||{}).slice(0,3).map(([k,v])=>k+v).join(' ') +
                       ' · 待采纳 ' + o.pending : '—';
    $('miaudit').textContent = fmt(a.mi);
    $('x7audit').textContent = fmt(a.x07);

    // 系统
    if (s.mem){ $('mem').textContent = `${(s.mem.used/1024).toFixed(1)} / ${(s.mem.total/1024).toFixed(1)} G`;
      $('membar').style.width = (s.mem.used/s.mem.total*100).toFixed(0)+'%'; }
    if (s.disk){ $('disk').textContent = `${s.disk.used/1024|0} / ${s.disk.size/1024|0} G · ${s.disk.pct}`;
      $('diskbar').style.width = s.disk.pct; }
    $('load').textContent = s.load || '—';
    $('uptime').textContent = s.uptime || '—';

    $('watch').textContent = (s.watch||[]).join('\\n') || '—';
    window.__firstOk = true;
    $('sub').textContent = '数据时间 ' + new Date(d.at*1000).toLocaleTimeString() +
      (mi.online ? ' · 小米在线' : ' · 小米不在线');

    const q = await fetch('/qr.png?t='+Date.now(), {cache:'no-store'});
    if (q.ok){ const b = await q.blob(); const u = URL.createObjectURL(b);
      const img = $('qr'); if (img.dataset.u) URL.revokeObjectURL(img.dataset.u);
      img.dataset.u = u; img.src = u; }
  }catch(e){ $('err').textContent = '页面出错：' + e; }
  setTimeout(tick, 3000);
}

document.querySelectorAll('.rbtn').forEach(b=>{
  b.onclick = () => {
    document.querySelectorAll('.rbtn').forEach(x=>x.classList.remove('on'));
    b.classList.add('on');
    document.querySelectorAll('.view').forEach(v=>v.classList.remove('on'));
    const el = document.getElementById('view-' + b.dataset.p);
    if (b.dataset.p === 'starmap' && !SM.nodes.length) smLoad();
    if (el) el.classList.add('on');
  };
});

async function loadVersion(){
  try{
    const d = await (await fetch('/api/version',{cache:'no-store'})).json();
    document.getElementById('rver').textContent = 'v' + d.version;
    document.getElementById('set-ver').textContent = 'v' + d.version;
  }catch(e){}
}

async function checkUpdate(){
  const n = document.getElementById('set-notes');
  document.getElementById('set-remote').textContent = '检查中…';
  try{
    const d = await (await fetch('/api/check-update',{cache:'no-store'})).json();
    if (d.err){ document.getElementById('set-remote').textContent = d.err; n.style.display='none'; return; }
    document.getElementById('set-remote').textContent = d.newer ? ('v' + d.remote + '（有新版）') : ('v' + d.remote + '（已是最新）');
    n.style.display = d.notes ? 'block' : 'none';
    n.textContent = d.notes || '';
  }catch(e){ document.getElementById('set-remote').textContent = '检查失败'; }
}

function openRepo(){ window.open('https://github.com/lavie0421/botmon', '_blank'); }

//<!--STARMAP_JS-->

async function act(doit, why){
  if (why && !confirm(why)) return;
  $('out').style.display='block';
  $('out').textContent = '执行中…';
  try{
    const r = await fetch('/api/action?do='+doit, {method:'POST'});
    const j = await r.json();
    $('out').textContent = j.out || j.err || '(无输出)';
  }catch(e){ $('out').textContent = '失败：'+e; }
  tick();
}
loadVersion();
smInit();
tick();
</script></div></body></html>"""

if starui:
    PAGE = (PAGE.replace("<!--STARMAP_VIEW-->", starui.VIEW)
                .replace("//<!--STARMAP_JS-->", starui.SCRIPT))



class H(BaseHTTPRequestHandler):
    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/api/status":
            with LOCK:
                body = json.dumps({"data": STATE["data"], "err": STATE["err"],
                                   "at": STATE["at"] or time.time()},
                                  ensure_ascii=False).encode()
            self._send(200, "application/json", body)
        elif path == "/api/clientlog":
            with LOCK:
                body = json.dumps({"log": STATE.get("client") or []},
                                  ensure_ascii=False).encode()
            self._send(200, "application/json", body)
        elif path == "/api/starmap":
            try:
                data = starmap.parse(KB_PATH) if starmap else {"error": "starmap 未加载"}
            except Exception as e:
                data = {"error": str(e)[:200]}
            self._send(200, "application/json",
                       json.dumps(data, ensure_ascii=False).encode())
        elif path == "/api/version":
            v = updater.local_version() if updater else "0.0.0"
            body = json.dumps({"version": v}, ensure_ascii=False).encode()
            self._send(200, "application/json", body)
        elif path == "/api/check-update":
            body = json.dumps(updater.check() if updater else {"err": "updater 未加载"},
                              ensure_ascii=False).encode()
            self._send(200, "application/json", body)
        elif path == "/qr.png":
            with LOCK:
                png = STATE["qr"]
            if png:
                self._send(200, "image/png", png)
            else:
                self._send(503, "text/plain", b"no qr")
        else:
            self._send(200, "text/html; charset=utf-8", PAGE.encode())

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/api/clientlog":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b"{}"
                d = json.loads(raw.decode("utf-8", "replace"))
                msg = str(d.get("msg") or "")[:500]
                if msg:
                    with LOCK:
                        log = STATE.setdefault("client", [])
                        log.append({"at": time.time(), "msg": msg})
                        del log[:-20]
                    print("[client]", msg, flush=True)
            except Exception:
                pass
            self._send(200, "application/json", b'{"ok":true}')
            return
        if path != "/api/action":
            self._send(404, "application/json", b'{"err":"not found"}')
            return
        qs = dict(p.split("=", 1) for p in (self.path.split("?", 1) + [""])[1].split("&") if "=" in p)
        cmd = ACTIONS.get(qs.get("do", ""))
        if not cmd:
            self._send(400, "application/json", b'{"err":"unknown action"}')
            return
        try:
            r = ssh(cmd, timeout=120)
            body = json.dumps({"out": (r.stdout or "")[-4000:], "err": (r.stderr or "")[-1200:]},
                              ensure_ascii=False).encode()
        except Exception as e:  # noqa: BLE001
            body = json.dumps({"err": str(e)[:400]}, ensure_ascii=False).encode()
        self._send(200, "application/json", body)

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("BOTMON_PORT", "8791")))
    args = ap.parse_args()
    threading.Thread(target=stream_loop, daemon=True).start()
    print("http://127.0.0.1:%d" % args.port, flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), H).serve_forever()


if __name__ == "__main__":
    main()
