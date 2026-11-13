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

try:
    import apply_update
except Exception:
    apply_update = None

# 更新进度（下载线程写，面板轮询读）
UPDATE_STATE = {"state": "idle", "got": 0, "total": 0, "err": "",
                "version": "", "path": ""}
UPDATE_LOCK = threading.Lock()


def _j(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode()


def _update_snapshot() -> dict:
    with UPDATE_LOCK:
        return dict(UPDATE_STATE)


def _download_worker():
    def on_progress(got, total):
        with UPDATE_LOCK:
            UPDATE_STATE["got"] = got
            if total:
                UPDATE_STATE["total"] = total

    r = updater.download(on_progress=on_progress)
    with UPDATE_LOCK:
        if r.get("ok"):
            UPDATE_STATE.update(state="done", got=r.get("size") or 0,
                                total=r.get("size") or 0,
                                version=r.get("version") or "",
                                path=r.get("path") or "", err="")
        else:
            UPDATE_STATE.update(state="error", err=r.get("err") or "下载失败")


def _exit_soon(delay=1.2):
    """先把 HTTP 响应发完，再退出进程，让外部 cmd 接手换文件。"""
    time.sleep(delay)
    os._exit(0)

BRAIN_PROBE = """
<script>
(function(){
  function post(m){
    try{ fetch('/api/clientlog',{method:'POST',headers:{'Content-Type':'application/json'},
      body: JSON.stringify({msg: ('[brain] ' + m).slice(0,480)})}); }catch(e){}
  }
  window.addEventListener('error', function(e){
    post('ERROR ' + (e.message||'') + ' @ ' + (e.filename||'').split('/').pop() + ':' + e.lineno);
  }, true);
  window.addEventListener('unhandledrejection', function(e){
    post('REJECT ' + ((e.reason && (e.reason.message||e.reason)) || ''));
  });
  setTimeout(function(){
    var c = document.querySelector('canvas');
    var gl = c && (c.getContext && (c.getContext('webgl2') || c.getContext('webgl')));
    post('canvas=' + (c ? c.width + 'x' + c.height : 'none') + ' webgl=' + (gl ? 'yes' : 'no'));
  }, 2500);
})();
</script>
"""

try:
    import sys as _sys
    _BASE = getattr(_sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

    BRAIN_DIR = os.path.join(_BASE, "brain")
except Exception:
    BRAIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brain")

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
    """每 15 秒跑一次远端探针。失败走指数退避（30s→300s），绝不低于 15s。"""
    fail = 0
    while True:
        try:
            r = ssh("python3 " + REMOTE_PROBE, timeout=45)
            raw = r.stdout if isinstance(r.stdout, str) else ""
            data = None
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith("{"):
                    try:
                        data = json.loads(line)
                    except Exception:
                        data = None
            if data:
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
                fail = 0
                time.sleep(15)
            else:
                fail += 1
                with LOCK:
                    STATE["err"] = "探针没有返回数据（%s）" % (raw[:120] or "空输出")
                wait = min(300, 30 * (2 ** min(fail - 1, 4)))
                time.sleep(wait)
        except Exception as e:
            fail += 1
            with LOCK:
                STATE["err"] = ("连接异常：%s" % e)[:200]
            time.sleep(min(300, 30 * (2 ** min(fail - 1, 4))))


ACTIONS = {
    "restart-napcat": "docker restart napcat",
    "restart-astrbot": "docker restart astrbot",
    "watchdog": "python3 /opt/napcat-watch/watchdog.py; tail -1 /var/log/napcat-watch.log",
    "logs-napcat": "docker logs --since 5m napcat 2>&1 | grep -avE '^[0-9a-f]{64}' | tail -25",
    "logs-astrbot": "docker logs --since 5m astrbot 2>&1 | sed 's/\\x1b\\[[0-9;]*m//g' | grep -avE 'DBUG' | tail -25",
}

PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>夏07 / 贾维斯 监测台</title>
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
 .btab{background:#151a23;border:1px solid var(--line);color:#9fb0c9;
   padding:5px 14px;border-radius:99px;cursor:pointer;font-size:12.5px}
 .btab.on{background:#2a3a52;border-color:#3d5675;color:#cfe0f5}
 .bk-head{display:flex;gap:10px;margin:12px 0}
 .bk-bot{flex:1;background:#151a23;border:1px solid var(--line);border-radius:12px;padding:12px 14px}
 .bk-bot .bn{font-size:14px;font-weight:600;margin-bottom:6px}
 .bk-row{display:flex;justify-content:space-between;font-size:12px;color:#8b95a8}
 .bk-row .v{color:#d6e0ee}
 .bk-card{background:#151a23;border:1px solid var(--line);border-radius:14px;
   padding:14px 16px;margin-bottom:12px}
 .bk-card h3{font-size:12px;letter-spacing:.05em;text-transform:uppercase;color:#8b95a8;margin:0 0 10px}
 .bk-item{display:flex;gap:10px;padding:8px 0;border-bottom:1px solid rgba(35,43,56,.7)}
 .bk-item:last-child{border-bottom:0}
 .bk-d{width:7px;height:7px;border-radius:50%;margin-top:6px;flex:0 0 7px}
 .bk-bad{background:#ff5c5c}.bk-warn{background:#f0b429}.bk-ok{background:#39d98a}
 .bk-t{font-size:12.5px;color:#dce6f4}
 .bk-m{font-size:11.5px;color:#6f7c92;margin-top:2px}
 .bk-tag{font-size:10.5px;border:1px solid var(--line);border-radius:99px;
   padding:1px 7px;color:#9fb0c9;margin-left:6px}
 .bk-f{display:flex;font-size:12px;padding:4px 0;color:#c6d3e5}
 .bk-f .k{color:#7f8ea6;width:56px;flex:0 0 56px}
 .bk-f .v{flex:1}
 .bk-f .c{color:#8fa2bb}
 .bk-bar{height:7px;background:#1a2130;border-radius:4px;overflow:hidden;display:flex;margin:8px 0 6px}
 .bk-bar i{display:block;height:100%}
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
    <button class="rbtn" data-p="brain" title="脑图">◍</button>
    <button class="rbtn" data-p="settings" title="设置">⚙</button>
  </div>
  <div class="rver" id="rver">v—</div>
</div>
<div class="wrap">
<div class="view on" id="view-status">
<h1>夏07 / 贾维斯 监测台</h1>
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
    <h2>贾维斯 · NapCat 协议端</h2>
    <div class="row"><span class="name"><span class="dot" id="midot"></span>贾维斯</span>
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

<div class="view" id="view-audit"><h1>审计</h1><div class="card"><h2>贾维斯 · 群聊自检</h2><div id="aud-more" class="sub"></div></div></div>

<div class="view" id="view-brain">
<h1>记忆大脑</h1>
<div class="sub" style="display:flex;gap:8px;align-items:center">
  <button class="btab on" data-b="3d" onclick="brainTab('3d')">3D 脑图</button>
  <button class="btab" data-b="check" onclick="brainTab('check')">体检</button>
</div>
<iframe id="brainframe" style="width:100%;height:calc(100vh - 250px);min-height:420px;border:1px solid var(--line);border-radius:14px;background:#05070c"></iframe>
<div id="braincheck" style="display:none;height:calc(100vh - 250px);overflow:auto"></div>

<div class="view" id="view-settings"><h1>设置</h1><div class="card" style="max-width:520px"><h2>版本</h2><div class="row"><span class="k">当前版本</span><span class="v" id="set-ver">—</span></div><div class="row"><span class="k">最新版本</span><span class="v" id="set-remote">—</span></div><div class="row"><span class="k">更新状态</span><span class="v" id="upd-msg">—</span></div><div id="upd-bar-wrap" style="display:none;margin:8px 0;height:6px;background:#2a2f3a;border-radius:3px;overflow:hidden"><div id="upd-bar" style="height:6px;width:0%;background:#5b8def"></div></div><div class="btns"><button onclick="checkUpdate()">检查更新</button><button id="btn-dl" style="display:none" onclick="downloadUpdate()">下载更新</button><button id="btn-apply" style="display:none" onclick="applyUpdate()">重启安装</button><button id="btn-rb" style="display:none" onclick="rollbackNow()">回滚到上一版</button><button onclick="openRepo()">打开仓库</button></div><pre id="set-notes" style="display:none"></pre></div></div>

<div class="btns">
  <button class="danger" onclick="act('restart-napcat','确定重启贾维斯？会掉线约 20 秒，登录态保留。')">重启贾维斯</button>
  <button class="danger" onclick="act('restart-astrbot','确定重启 AstrBot？所有插件会重载约 30 秒。')">重启 AstrBot</button>
  <button onclick="act('watchdog','跑一次看门狗（会按判定结果告警/重启）')">跑一次看门狗</button>
  <button onclick="act('logs-napcat','看贾维斯最近日志')">贾维斯日志</button>
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

    // 贾维斯
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
      (mi.online ? ' · 贾维斯在线' : ' · 贾维斯不在线');

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
    if (b.dataset.p === 'brain'){
      const f = document.getElementById('brainframe');
      if (f && !f.src) f.src = '/astrbot-xia07/brain';
    }
    // 进设置页就刷新一次更新状态：否则「已下载好/可回滚」要等你点过“检查更新”才显形
    if (b.dataset.p === 'settings') refreshUpdateState();
    if (el) el.classList.add('on');
  };
});


function brainTab(k){
  document.querySelectorAll('.btab').forEach(function(b){ b.classList.toggle('on', b.dataset.b === k); });
  document.getElementById('brainframe').style.display = (k === '3d') ? 'block' : 'none';
  document.getElementById('braincheck').style.display = (k === 'check') ? 'block' : 'none';
  if (k === '3d'){
    var f = document.getElementById('brainframe');
    if (!f.src) f.src = '/astrbot-xia07/brain';
  } else {
    brainCheck();
  }
}

var BRAIN_CACHE = null;
async function brainCheck(force){
  var box = document.getElementById('braincheck');
  if (BRAIN_CACHE && !force) return renderBrain(BRAIN_CACHE);
  box.textContent = '正在读取两个大脑…';
  try{
    var d = await (await fetch('/api/brain-check', {cache:'no-store'})).json();
    BRAIN_CACHE = d;
    renderBrain(d);
  }catch(e){ box.textContent = '读取失败：' + e; }
}

function renderBrain(d){
  var box = document.getElementById('braincheck');
  box.textContent = '';
  var bots = (d && d.bots) || {};

  var head = document.createElement('div'); head.className = 'bk-head';
  ['xia07','mike'].forEach(function(k){
    var b = bots[k]; if (!b) return;
    var card = document.createElement('div'); card.className = 'bk-bot';
    var nm = document.createElement('div'); nm.className = 'bn'; nm.textContent = b.label || k;
    card.appendChild(nm);
    [['记忆', b.mem_total + ' 条'], ['实体', b.ent_total + ' 个'], ['事实卡片', b.facts_count + ' 张']]
      .forEach(function(pair){
        var r = document.createElement('div'); r.className = 'bk-row';
        var a = document.createElement('span'); a.textContent = pair[0];
        var v = document.createElement('span'); v.className = 'v'; v.textContent = pair[1];
        r.appendChild(a); r.appendChild(v); card.appendChild(r);
      });
    head.appendChild(card);
  });
  box.appendChild(head);

  ['xia07','mike'].forEach(function(k){
    var b = bots[k]; if (!b) return;
    var card = document.createElement('div'); card.className = 'bk-card';
    var h = document.createElement('h3'); h.textContent = (b.label||k) + ' · 需要看的 ' + (b.issues||[]).length + ' 件事';
    card.appendChild(h);
    (b.issues||[]).forEach(function(it){
      var row = document.createElement('div'); row.className = 'bk-item';
      var dot = document.createElement('div'); dot.className = 'bk-d bk-' + (it.sev||'warn');
      var wrap = document.createElement('div');
      var t = document.createElement('div'); t.className = 'bk-t';
      t.textContent = it.title || '';
      if (it.tag){ var tg = document.createElement('span'); tg.className = 'bk-tag'; tg.textContent = it.tag; t.appendChild(tg); }
      var m = document.createElement('div'); m.className = 'bk-m'; m.textContent = it.why || '';
      wrap.appendChild(t); wrap.appendChild(m);
      row.appendChild(dot); row.appendChild(wrap); card.appendChild(row);
    });
    box.appendChild(card);

    if ((b.facts||[]).length){
      var fc = document.createElement('div'); fc.className = 'bk-card';
      var fh = document.createElement('h3'); fh.textContent = '事实卡片 · ' + b.facts_count + ' 张';
      fc.appendChild(fh);
      var bySubject = {};
      b.facts.forEach(function(f){ (bySubject[f.subject] = bySubject[f.subject] || []).push(f); });
      Object.keys(bySubject).forEach(function(subj){
        var sh = document.createElement('div'); sh.className = 'bk-row';
        sh.style.marginTop = '8px'; sh.style.color = '#b9c8dd';
        var s1 = document.createElement('span'); s1.textContent = subj;
        var s2 = document.createElement('span'); s2.textContent = bySubject[subj].length + ' 张';
        sh.appendChild(s1); sh.appendChild(s2); fc.appendChild(sh);
        bySubject[subj].forEach(function(f){
          var row = document.createElement('div'); row.className = 'bk-f';
          var k1 = document.createElement('span'); k1.className = 'k'; k1.textContent = f.key;
          var v1 = document.createElement('span'); v1.className = 'v'; v1.textContent = f.value;
          var c1 = document.createElement('span'); c1.className = 'c';
          c1.textContent = f.conf.toFixed(2);
          if (f.conf < 0.6) c1.style.color = '#f0b429';
          row.appendChild(k1); row.appendChild(v1); row.appendChild(c1); fc.appendChild(row);
        });
      });
      box.appendChild(fc);
    }

    if (b.by_kind && Object.keys(b.by_kind).length){
      var mc = document.createElement('div'); mc.className = 'bk-card';
      var mh = document.createElement('h3'); mh.textContent = '记忆构成 · ' + b.mem_total + ' 条';
      mc.appendChild(mh);
      var bar = document.createElement('div'); bar.className = 'bk-bar';
      var total = b.mem_total || 1;
      var colors = {episode:'#4d8dff', fact:'#f0b429', reflection:'#a78bfa'};
      Object.keys(b.by_kind).forEach(function(kd){
        var i2 = document.createElement('i');
        i2.style.width = Math.round(b.by_kind[kd] / total * 100) + '%';
        i2.style.background = colors[kd] || '#7fe0a8';
        bar.appendChild(i2);
      });
      mc.appendChild(bar);
      var lg = document.createElement('div'); lg.className = 'bk-m';
      lg.textContent = Object.keys(b.by_kind).map(function(kd){ return kd + ' ' + b.by_kind[kd]; }).join(' · ');
      mc.appendChild(lg);
      var rec = document.createElement('div'); rec.className = 'bk-m';
      rec.textContent = '近 7 天新增 ' + (b.recent7||[]).reduce(function(a,c){return a+c;},0) +
                        ' 条 · 最长未更新 ' + (b.max_stale_days||0) + ' 天';
      mc.appendChild(rec);
      box.appendChild(mc);
    }
  });
}

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
    const dl = document.getElementById('btn-dl');
    dl.style.display = d.newer ? '' : 'none';
    if (d.newer) updMsg('可以更新到 v' + d.remote);
  }catch(e){ document.getElementById('set-remote').textContent = '检查失败'; }
  refreshUpdateState();
}

function updMsg(t){ document.getElementById('upd-msg').textContent = t || '—'; }
function updBar(pct){
  const w = document.getElementById('upd-bar-wrap'), b = document.getElementById('upd-bar');
  if (pct == null){ w.style.display='none'; b.style.width='0%'; return; }
  w.style.display='block'; b.style.width = Math.max(0, Math.min(100, pct)).toFixed(1)+'%';
}

async function refreshUpdateState(){
  try{
    const s = await (await fetch('/api/update/status',{cache:'no-store'})).json();
    document.getElementById('btn-apply').style.display = s.staged ? '' : 'none';
    document.getElementById('btn-rb').style.display = s.can_rollback ? '' : 'none';
    if (s.pending && s.pending.to && s.pending.to !== s.local)
      updMsg('上次更新到 v' + s.pending.to + ' 未生效，可以回滚');
    else if (s.staged)
      updMsg('已下载 v' + s.staged_ver + '，点「重启安装」生效');
    else if (s.pending && s.pending.to)
      updMsg('更新到 v' + s.pending.to + ' 待确认');
    return s;
  }catch(e){ return null; }
}

let _pollTimer = null;

async function downloadUpdate(){
  document.getElementById('btn-dl').style.display='none';
  updMsg('正在下载…'); updBar(0);
  try{
    const r = await (await fetch('/api/update/download',{method:'POST'})).json();
    if (!r.ok){ updMsg('下载失败：' + (r.err||'未知')); updBar(null); return; }
  }catch(e){ updMsg('下载请求失败'); updBar(null); return; }
  if (_pollTimer) clearInterval(_pollTimer);
  _pollTimer = setInterval(async () => {
    try{
      const p = await (await fetch('/api/update/progress',{cache:'no-store'})).json();
      if (p.state === 'downloading'){
        const pct = p.total ? (p.got / p.total * 100) : 0;
        updBar(pct);
        updMsg('下载中 ' + (p.got/1048576).toFixed(1) + ' / ' +
               (p.total ? (p.total/1048576).toFixed(1) : '?') + ' MB');
      } else if (p.state === 'done'){
        clearInterval(_pollTimer); _pollTimer = null;
        updBar(100); updMsg('下载完成 v' + (p.version||'') + '，点「重启安装」');
        refreshUpdateState();
      } else if (p.state === 'error'){
        clearInterval(_pollTimer); _pollTimer = null;
        updBar(null); updMsg('下载失败：' + (p.err||'未知'));
      }
    }catch(e){}
  }, 600);
}

async function applyUpdate(){
  if (!confirm('现在重启程序并安装新版本？\n\n会先退出当前窗口，替换完成后自动重新打开。\n旧版本会保留，万一新版本起不来可以在设置里回滚。')) return;
  updMsg('正在退出并安装…');
  try{ await fetch('/api/update/apply',{method:'POST'}); }catch(e){}
}

async function rollbackNow(){
  if (!confirm('回滚到上一版本？\n\n会先退出当前窗口，然后换回旧版并重新打开。')) return;
  updMsg('正在回滚…');
  try{ await fetch('/api/update/rollback',{method:'POST'}); }catch(e){}
}

function openRepo(){ window.open('https://github.com/dapperpitch/botmon', '_blank'); }

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
        if path == "/api/brain":
            which = "mike"
            if "which=mike" in self.path:
                which = "mike"
            elif "which=xia07" in self.path:
                which = "xia07"
            try:
                r = ssh("python3 /opt/astrbot/data/brain_export.py " + which, timeout=60)
                g = json.loads(r.stdout if isinstance(r.stdout, str) else "{}")
                body = json.dumps({"data": {"graph": g}}, ensure_ascii=False).encode()
            except Exception as e:
                body = json.dumps({"data": {"graph": {"nodes": [], "edges": [], "memories": []}},
                                   "error": str(e)[:200]}).encode()
            self._send(200, "application/json", body)
            return

        if path == "/api/brain-check":
            try:
                r = ssh("python3 /opt/botmon/brain_check.py", timeout=60)
                body = r.stdout if isinstance(r.stdout, str) else "{}"
            except Exception as e:
                body = json.dumps({"error": str(e)[:200]})
            self._send(200, "application/json", body.encode("utf-8"))
            return

        if path == "/astrbot-xia07/brain-view.html":
            fp2 = os.path.join(BRAIN_DIR, "brain-view.html")
            if os.path.isfile(fp2):
                h2 = open(fp2, encoding="utf-8", errors="replace").read()
                h2 = h2.replace("<head>", "<head>" + BRAIN_PROBE, 1)
                self._send(200, "text/html; charset=utf-8", h2.encode())
            else:
                self._send(404, "text/plain", b"brain-view missing")
            return

        if path.startswith("/astrbot-xia07/brain"):
            # 数据接口：和 DSH 同一个脚本、同一个 URL 形状
            if path == "/astrbot-xia07/brain/data":
                which = "xia07" if "which=xia07" in self.path else "mike"
                try:
                    r = ssh("python3 /opt/astrbot/data/brain_export.py " + which, timeout=60)
                    body = r.stdout if isinstance(r.stdout, str) else "{}"
                except Exception as e:
                    body = json.dumps({"ok": False, "error": str(e)[:200]})
                self._send(200, "application/json", body.encode("utf-8"))
                return

            # 静态资源：/brain/assets/xxx 与 /brain/xxx（module.js、brain-view.html）
            rel = path[len("/astrbot-xia07/brain"):].lstrip("/")
            if rel.startswith("assets/"):
                rel = rel[len("assets/"):]
            if not rel:
                rel = "brain.html"
            fp = os.path.normpath(os.path.join(BRAIN_DIR, rel.replace("/", os.sep)))
            base = os.path.normpath(BRAIN_DIR)
            if not fp.startswith(base) or not os.path.isfile(fp):
                self._send(404, "text/plain", ("not found: " + rel).encode())
                return

            low = fp.lower()
            if low.endswith(".html"):
                html = open(fp, encoding="utf-8", errors="replace").read()
                # 页内相对/绝对路径都指向本项目；探针帮我们在没有控制台时看到错误
                html = html.replace('href="/astrbot-xia07/panel"', 'href="#"')
                html = html.replace("<head>", "<head>" + BRAIN_PROBE, 1)
                self._send(200, "text/html; charset=utf-8", html.encode())
                return

            ctype = "application/octet-stream"
            if low.endswith(".js"):
                ctype = "text/javascript; charset=utf-8"
            elif low.endswith(".json"):
                ctype = "application/json"
            for ext, ct in ((".bin", "application/octet-stream"), (".md", "text/plain; charset=utf-8")):
                if low.endswith(ext):
                    ctype = ct
            with open(fp, "rb") as f:
                self._send(200, ctype, f.read())
            return

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
        elif path == "/api/update/status":
            data = apply_update.status() if apply_update else {"err": "apply_update 未加载"}
            self._send(200, "application/json",
                       json.dumps(data, ensure_ascii=False).encode())
        elif path == "/api/update/progress":
            self._send(200, "application/json",
                       json.dumps(_update_snapshot(), ensure_ascii=False).encode())
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
        if path == "/api/update/download":
            if not (updater and apply_update):
                self._send(200, "application/json", _j({"ok": False, "err": "更新模块未加载"}))
                return
            with UPDATE_LOCK:
                busy = UPDATE_STATE.get("state") == "downloading"
            if busy:
                self._send(200, "application/json", _j({"ok": False, "err": "正在下载中"}))
                return
            with UPDATE_LOCK:
                UPDATE_STATE.update(state="downloading", got=0, total=0,
                                    err="", version="", path="")
            threading.Thread(target=_download_worker, daemon=True).start()
            self._send(200, "application/json", b'{"ok":true}')
            return
        if path == "/api/update/apply":
            if not apply_update:
                self._send(200, "application/json", _j({"ok": False, "err": "apply_update 未加载"}))
                return
            st = apply_update.status()
            if not st.get("staged"):
                self._send(200, "application/json",
                           _j({"ok": False, "err": "还没有下载好的新版本"}))
                return
            r = apply_update.apply(st["staged"], st.get("staged_ver") or "")
            self._send(200, "application/json", _j(r))
            if r.get("ok"):
                threading.Thread(target=_exit_soon, daemon=True).start()
            return
        if path == "/api/update/rollback":
            if not apply_update:
                self._send(200, "application/json", _j({"ok": False, "err": "apply_update 未加载"}))
                return
            r = apply_update.rollback()
            self._send(200, "application/json", _j(r))
            if r.get("ok"):
                threading.Thread(target=_exit_soon, daemon=True).start()
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
