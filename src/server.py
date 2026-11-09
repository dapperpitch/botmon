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

STATE = {"data": {}, "qr": b"", "err": "", "at": 0.0}
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

<div class="view" id="view-starmap">
<h1>知识星图</h1>
<div class="sub" id="smstat">加载中…</div>
<div style="display:flex;gap:14px;align-items:flex-start">
  <canvas id="smcanvas" width="1000" height="660"
          style="background:#0e1219;border:1px solid var(--line);border-radius:14px;flex:1;cursor:grab"></canvas>
  <div class="card" style="width:300px;min-height:200px">
    <h2>选中</h2>
    <div id="smdetail" class="sub" style="font-size:12.5px;line-height:1.7">点一个点看看</div>
  </div>
</div>
</div>

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
    if (b.dataset.p === 'starmap' && !SM.nodes.length) loadStar();
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

let SM = {nodes:[], edges:[], pos:{}};
let smView = {x:0, y:0, k:1, rot:0, drag:false, sx:0, sy:0};
let SM_HOVER = null;
let SM_LAYER = null, SM_WC = {x:0,y:0}, SM_BOUNDS = null, SM_AUTO = true, SM_RAF = 0;

async function loadStar(){
  const st = document.getElementById('smstat');
  try{
    const d = await (await fetch('/api/starmap',{cache:'no-store'})).json();
    if (d.error){ st.textContent = '解析失败：' + d.error; return; }
    SM = {...d, pos:{}};
    layoutStar();
    drawStar();
    st.textContent = '案例 ' + d.stats.cases + ' · 节点 ' + d.stats.nodes +
                     ' · 连线 ' + d.stats.edges + ' · ' + d.source;
  }catch(e){ st.textContent = '加载失败：' + e; }
}

// ── 星图：每个案例是一个星系，章节和句子是星星 ──
const PALETTE = ['#8ab4ff','#7fe0a8','#ffd166','#ff8fab','#a78bfa','#67e8f9',
                 '#fca5a5','#c4b5fd','#86efac','#fcd34d','#f9a8d4','#93c5fd',
                 '#6ee7b7','#fbbf24','#e879f9','#5eead4','#fda4af'];

function layoutStar(){
  const cv = document.getElementById('smcanvas');
  const W = cv.width, H = cv.height;
  const N = SM.nodes, P = SM.pos = {};

  SM.hue = SM.hue || {};
  const cases = N.filter(n=>n.kind==='case');
  const clusterOf = {}, clusters = [];
  cases.forEach((c,i)=>{
    clusters.push({id:c.id, x:0, y:0, r:0, sx:1, sy:1, rot:0,
                   color: PALETTE[i % PALETTE.length], members: []});
    clusterOf[c.id] = i;
  });

  const parentOf = {};
  N.forEach(n=>{ if(n.parent) parentOf[n.id] = n.parent; });
  function rootOf(id){
    let cur = id, guard = 0;
    while (parentOf[cur] && guard++ < 6) cur = parentOf[cur];
    return cur;
  }
  N.forEach(n=>{
    if (n.kind === 'case') return;
    const idx = clusterOf[rootOf(n.id)];
    if (idx !== undefined) clusters[idx].members.push(n.id);
  });

  // ① 星系中心：随机撒，再互相推开，避免排成一个规整的圆
  clusters.forEach(g=>{
    g.x = W*0.15 + Math.random()*W*0.70;
    g.y = H*0.15 + Math.random()*H*0.70;
  });
  for (let it=0; it<160; it++){
    for (let i=0;i<clusters.length;i++){
      for (let j=i+1;j<clusters.length;j++){
        const a = clusters[i], b = clusters[j];
        let dx=b.x-a.x, dy=b.y-a.y, d=Math.hypot(dx,dy)||1;
        const min = 210;
        if (d < min){
          const f=(min-d)/d*0.5;
          a.x-=dx*f; a.y-=dy*f; b.x+=dx*f; b.y+=dy*f;
        }
      }
    }
  }
  // 收回到画布内
  clusters.forEach(g=>{
    g.x = Math.max(W*0.10, Math.min(W*0.90, g.x));
    g.y = Math.max(H*0.12, Math.min(H*0.88, g.y));
  });

  // ② 星系形状：大小按星数，椭圆 + 随机旋角，成员按幂分布往中心堆
  clusters.forEach(g=>{
    const n = g.members.length || 1;
    g.r = 34 + Math.sqrt(n) * 11;
    g.sx = 0.75 + Math.random()*0.9;
    g.sy = 0.55 + Math.random()*0.8;
    g.rot = Math.random()*Math.PI;
    SM.hue[g.id] = g.color;

    g.members.forEach((id, k)=>{
      const t = (k + Math.random()) / n;
      const rr = g.r * Math.pow(Math.random(), 0.55) * (0.35 + t*0.9);
      const ang = Math.random()*Math.PI*2;
      const ex = Math.cos(ang)*rr*g.sx, ey = Math.sin(ang)*rr*g.sy;
      const x = ex*Math.cos(g.rot) - ey*Math.sin(g.rot);
      const y = ex*Math.sin(g.rot) + ey*Math.cos(g.rot);
      P[id] = {x: g.x + x, y: g.y + y};
    });

    const cn = N.find(x=>x.id===g.id);
    if (cn) P[g.id] = {x: g.x, y: g.y};
  });

  SM.clusters = clusters;
  fitView();
}

function fitView(){
  let minx=1e9,miny=1e9,maxx=-1e9,maxy=-1e9;
  Object.values(SM.pos).forEach(p=>{
    if(p.x<minx)minx=p.x; if(p.x>maxx)maxx=p.x;
    if(p.y<miny)miny=p.y; if(p.y>maxy)maxy=p.y;
  });
  SM_BOUNDS = {minx, miny, maxx, maxy, w: maxx-minx, h: maxy-miny};
  SM_WC = {x:(minx+maxx)/2, y:(miny+maxy)/2};

  const cv = document.getElementById('smcanvas');
  const pad = 70;
  const k = Math.min((cv.width-pad*2)/(SM_BOUNDS.w||1),
                     (cv.height-pad*2)/(SM_BOUNDS.h||1));
  smView.k = Math.max(0.3, Math.min(2.2, k));
  smView.x = cv.width/2;
  smView.y = cv.height/2;
  smView.rot = 0;
  SM_LAYER = null;     // 布局变了，静态层要重画
}

// 把星星和连线画到一张离屏画布上，之后只做旋转贴图（不然每帧 400 个渐变会卡）
function buildLayer(){
  const cv = document.getElementById('smcanvas');
  const pad = 120;
  const bw = Math.ceil(SM_BOUNDS.w + pad*2);
  const bh = Math.ceil(SM_BOUNDS.h + pad*2);
  const c = document.createElement('canvas');
  c.width = bw; c.height = bh;
  const g = c.getContext('2d');
  g.translate(-SM_BOUNDS.minx + pad, -SM_BOUNDS.miny + pad);

  // 连线
  g.globalCompositeOperation = 'lighter';
  SM.edges.forEach(e=>{
    const a = SM.pos[e.a], b = SM.pos[e.b];
    if(!a||!b) return;
    g.strokeStyle = 'rgba(140,170,220,.10)';
    g.lineWidth = 0.6;
    g.beginPath(); g.moveTo(a.x,a.y); g.lineTo(b.x,b.y); g.stroke();
  });
  g.globalCompositeOperation = 'source-over';

  // 星星
  const order = [...SM.nodes].sort((p,q)=>p.size-q.size);
  order.forEach(n=>{
    const p = SM.pos[n.id];
    if(!p) return;
    const col = n.kind==='case' ? '#ffffff' : (SM.hue[rootCase(n.id)] || n.color || '#9fb3d1');
    const base = n.kind==='case' ? 5.5 : (n.kind==='section' ? 3.2 : 1.7);
    const glow = base*4;
    const grd = g.createRadialGradient(p.x, p.y, 0, p.x, p.y, glow);
    grd.addColorStop(0, hexA(col, 0.9));
    grd.addColorStop(0.22, hexA(col, 0.35));
    grd.addColorStop(1, hexA(col, 0));
    g.fillStyle = grd;
    g.beginPath(); g.arc(p.x, p.y, glow, 0, Math.PI*2); g.fill();
    g.fillStyle = hexA('#ffffff', n.kind==='point' ? 0.5 : 0.9);
    g.beginPath(); g.arc(p.x, p.y, Math.max(0.7, base*0.32), 0, Math.PI*2); g.fill();
  });

  // 星系名（跟着转，所以画在层里）
  g.textAlign = 'center';
  (SM.clusters||[]).forEach(cl=>{
    const node = SM.nodes.find(n=>n.id===cl.id);
    if(!node) return;
    g.fillStyle = hexA(cl.color, 0.9);
    g.font = '600 10.5px system-ui';
    g.fillText(node.label.slice(0,14), cl.x, cl.y - 14);
  });

  SM_LAYER = {canvas: c, ox: -SM_BOUNDS.minx + pad, oy: -SM_BOUNDS.miny + pad};
}

function drawStar(){
  const cv = document.getElementById('smcanvas');
  const ctx = cv.getContext('2d');
  ctx.setTransform(1,0,0,1,0,0);

  ensureBg(cv);
  ctx.drawImage(SM_BG, 0, 0);

  if (!SM_LAYER) buildLayer();

  ctx.save();
  ctx.translate(smView.x, smView.y);
  ctx.scale(smView.k, smView.k);
  ctx.rotate(smView.rot);
  ctx.translate(-SM_WC.x, -SM_WC.y);

  ctx.globalCompositeOperation = 'lighter';
  ctx.drawImage(SM_LAYER.canvas, SM_BOUNDS.minx - 120, SM_BOUNDS.miny - 120);
  ctx.globalCompositeOperation = 'source-over';
  ctx.restore();

  // 悬停高亮（在变换空间里画一个亮点）
  if (SM_HOVER && SM.pos[SM_HOVER.id]){
    const p = SM.pos[SM_HOVER.id];
    ctx.save();
    ctx.translate(smView.x, smView.y);
    ctx.scale(smView.k, smView.k);
    ctx.rotate(smView.rot);
    ctx.translate(-SM_WC.x, -SM_WC.y);
    const col = SM.hue[rootCase(SM_HOVER.id)] || '#ffffff';
    const grd = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, 26);
    grd.addColorStop(0, hexA(col, 0.95));
    grd.addColorStop(1, hexA(col, 0));
    ctx.fillStyle = grd;
    ctx.beginPath(); ctx.arc(p.x, p.y, 26, 0, Math.PI*2); ctx.fill();
    ctx.restore();
  }
}

function rootCase(id){
  let cur = id, guard = 0;
  while (guard++ < 6){
    const n = SM.nodes.find(x=>x.id===cur);
    if (!n || !n.parent) return cur;
    cur = n.parent;
  }
  return cur;
}

function hexA(hex, a){
  if (!hex || hex[0] !== '#') return 'rgba(150,170,200,'+a+')';
  const r = parseInt(hex.slice(1,3),16), g = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
  return 'rgba('+r+','+g+','+b+','+a+')';
}

let SM_BG = null, SM_BG_KEY = '';

function ensureBg(cv){
  const key = cv.width + 'x' + cv.height;
  if (SM_BG && SM_BG_KEY === key) return;
  const c = document.createElement('canvas');
  c.width = cv.width; c.height = cv.height;
  const g = c.getContext('2d');
  const W = c.width, H = c.height;

  const base = g.createRadialGradient(W*0.5, H*0.5, 30, W*0.5, H*0.5, Math.max(W,H)*0.78);
  base.addColorStop(0,   '#121a30');
  base.addColorStop(0.45,'#0a1020');
  base.addColorStop(1,   '#03050a');
  g.fillStyle = base;
  g.fillRect(0,0,W,H);

  g.globalCompositeOperation = 'lighter';
  const clouds = [
    [W*0.22, H*0.30, Math.max(W,H)*0.42, '90,70,190'],
    [W*0.78, H*0.24, Math.max(W,H)*0.36, '40,130,180'],
    [W*0.62, H*0.78, Math.max(W,H)*0.40, '180,60,140'],
    [W*0.30, H*0.80, Math.max(W,H)*0.34, '50,160,140'],
  ];
  clouds.forEach(([x,y,r,rgb])=>{
    const cg = g.createRadialGradient(x, y, 0, x, y, r);
    cg.addColorStop(0,   'rgba('+rgb+',0.16)');
    cg.addColorStop(0.55,'rgba('+rgb+',0.05)');
    cg.addColorStop(1,   'rgba('+rgb+',0)');
    g.fillStyle = cg;
    g.beginPath(); g.arc(x, y, r, 0, Math.PI*2); g.fill();
  });

  for (let i=0; i<520; i++){
    const x = Math.random()*W, y = Math.random()*H;
    const r = Math.random()*1.1 + 0.15;
    const a = Math.random()*0.55 + 0.08;
    g.fillStyle = 'rgba(255,255,255,' + a.toFixed(2) + ')';
    g.beginPath(); g.arc(x, y, r, 0, Math.PI*2); g.fill();
    if (Math.random() < 0.02){
      g.strokeStyle = 'rgba(200,220,255,' + (a*0.8).toFixed(2) + ')';
      g.lineWidth = 0.6;
      g.beginPath();
      g.moveTo(x-4, y); g.lineTo(x+4, y);
      g.moveTo(x, y-4); g.lineTo(x, y+4);
      g.stroke();
    }
  }

  const vig = g.createRadialGradient(W/2, H/2, Math.min(W,H)*0.35, W/2, H/2, Math.max(W,H)*0.72);
  vig.addColorStop(0, 'rgba(0,0,0,0)');
  vig.addColorStop(1, 'rgba(0,0,0,0.62)');
  g.globalCompositeOperation = 'source-over';
  g.fillStyle = vig;
  g.fillRect(0,0,W,H);

  SM_BG = c; SM_BG_KEY = key;
}

function smHit(mx, my){
  const cv = document.getElementById('smcanvas');
  const r = cv.getBoundingClientRect();
  const sx = mx - r.left, sy = my - r.top;
  // 逆变换：canvas → 世界坐标
  let dx = (sx - smView.x)/smView.k, dy = (sy - smView.y)/smView.k;
  const c = Math.cos(-smView.rot), sn = Math.sin(-smView.rot);
  const rx = dx*c - dy*sn, ry = dx*sn + dy*c;
  const x = rx + SM_WC.x, y = ry + SM_WC.y;

  let best = null, bd = 1e9;
  SM.nodes.forEach(n=>{
    const p = SM.pos[n.id]; if(!p) return;
    const d = (p.x-x)**2 + (p.y-y)**2;
    if (d < 400 && d < bd){ bd = d; best = n; }
  });
  return best;
}

function initStar(){
  const cv = document.getElementById('smcanvas');
  if (!cv) return;
  cv.addEventListener('mousedown', e=>{
    smView.drag = true;
    smView.spin = (e.button === 2 || e.shiftKey);
    smView.sx = e.clientX - smView.x;
    smView.sy = e.clientY - smView.y;
    smView.rot0 = smView.rot;
    smView.mx0 = e.clientX;
  });
  cv.addEventListener('mousemove', e=>{
    if(smView.drag){
      if (smView.spin){ smView.rot = smView.rot0 + (e.clientX - smView.mx0) * 0.008; }
      else { smView.x = e.clientX - smView.sx; smView.y = e.clientY - smView.sy; }
      drawStar(); return;
    }
    const n = smHit(e.clientX, e.clientY);
    const cur = SM_HOVER ? SM_HOVER.id : null;
    SM_HOVER = n;
    cv.style.cursor = n ? 'pointer' : 'grab';
    cv.title = n ? n.label : '';
    if ((n?n.id:null) !== cur) drawStar();
  });
  window.addEventListener('mouseup', ()=>{ smView.drag=false; });
  cv.addEventListener('wheel', e=>{
    e.preventDefault();
    smView.k = Math.max(0.25, Math.min(4, smView.k * (e.deltaY>0?0.9:1.1)));
    drawStar();
  }, {passive:false});
  cv.addEventListener('dblclick', ()=>{ SM_AUTO = !SM_AUTO; });
  cv.addEventListener('contextmenu', e=>{ e.preventDefault(); SM_AUTO = !SM_AUTO; });
  if (!SM_RAF){
    const loop = ()=>{
      if (SM_AUTO && !smView.drag && SM.nodes.length){
        smView.rot += 0.0012;
        if (smView.rot > Math.PI*2) smView.rot -= Math.PI*2;
        drawStar();
      }
      SM_RAF = requestAnimationFrame(loop);
    };
    SM_RAF = requestAnimationFrame(loop);
  }

  cv.addEventListener('click', e=>{
    const n = smHit(e.clientX, e.clientY);
    const el = document.getElementById('smdetail');
    if(!n){ el.textContent = '点一个点看看'; return; }
    el.innerHTML = '<b style="color:'+(n.kind==='case'?'#8ab4ff':(n.color||'#8b95a8'))+'">' +
      (n.kind==='case'?'案例':'条目') + '</b><br>' +
      (n.date? '<span style="color:#5d6879">'+n.date+'</span><br>' : '') +
      n.label;
  });
};

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
initStar();
tick();
</script></div></body></html>"""


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
