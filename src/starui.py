"""知识星图：三维银河（参考「拾光」的视觉方法，独立实现）。

学到的关键（来自参考项目文档）：
  · 每颗装饰粒子都是真三维坐标，逐帧投影，靠透视产生厚度
  · 景深：远处糊成大光斑、近处是硬点 —— 最出效果的一条
  · 底色压到接近纯黑，星点要亮，对比就是"深空"
  · 核心有呼吸；默认不画线；点击才出详情
  · 尺寸必须乘 p.s / REF，别把世界单位和像素混用
形态可换：旋涡 / 椭圆星云 / 不规则。
"""

VIEW = """
<div class="view" id="view-starmap">
<style>
 .smstage{position:relative;height:calc(100vh - 186px);min-height:440px;
   border:1px solid var(--line);border-radius:14px;overflow:hidden;background:#010206}
 .smstage canvas{display:block;width:100%;height:100%}
 .smtop{position:absolute;left:12px;top:12px;right:12px;display:flex;gap:8px;align-items:flex-start}
 .smsearch{position:relative;flex:1;max-width:340px}
 .smsearch input{width:100%;background:rgba(10,14,20,.82);border:1px solid var(--line);
   border-radius:9px;padding:8px 11px;color:var(--txt);font-size:13px;outline:none}
 .smsearch input:focus{border-color:#35507a}
 .smres{display:none;position:absolute;left:0;right:0;top:38px;background:rgba(12,17,26,.97);
   border:1px solid var(--line);border-radius:10px;max-height:300px;overflow:auto;
   box-shadow:0 12px 34px rgba(0,0,0,.65);z-index:30}
 .smitem{padding:7px 11px;font-size:12.5px;color:#c6d2e4;cursor:pointer;
   border-bottom:1px solid rgba(40,52,70,.6)}
 .smitem:hover{background:#182130}
 .smbtns{display:flex;gap:6px;margin-left:auto}
 .smbtns button{background:rgba(16,22,32,.82);border:1px solid var(--line);color:var(--txt);
   padding:7px 11px;border-radius:9px;cursor:pointer;font-size:12.5px}
 .smbtns button:hover{background:#1d2736}
 .smload{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
   background:rgba(2,4,9,.92);z-index:40;transition:opacity .25s;opacity:1}
 .smload.off{opacity:0;pointer-events:none}
 .smload .box{width:320px;text-align:center}
 .smload .t{font-size:13px;color:#c8d6ea;margin-bottom:10px}
 .smload .b{height:4px;background:#161d29;border-radius:3px;overflow:hidden}
 .smload .b i{display:block;height:100%;width:0;background:linear-gradient(90deg,#4d8dff,#7fe0a8);
   transition:width .18s}
 .smload .s{margin-top:8px;font-size:11.5px;color:#5f6b80}
 .smload.err .t{color:#ff8080}
 .smload.err .b i{background:#ff5c5c}
 .smdetail{display:none;position:absolute;right:12px;bottom:12px;width:370px;max-height:64%;
   overflow:auto;background:rgba(14,19,28,.96);border:1px solid var(--line);
   border-radius:12px;padding:14px 16px;box-shadow:0 16px 44px rgba(0,0,0,.6)}
 .smdetail.on{display:block}
 .smdetail h3{margin:0 0 4px;font-size:14px;color:#e9f0fb}
 .smdetail .smm{font-size:11.5px;color:#6f7c92;margin-bottom:10px}
 .smsec{margin:10px 0 4px;font-size:12px;color:#9fb0c9;cursor:pointer;padding:3px 6px;border-radius:6px}
 .smsec:hover{background:#1a2230}
 .smpt{font-size:12.5px;line-height:1.7;color:#ccd7e9;padding:3px 8px;
   border-left:2px solid rgba(120,150,200,.25);margin:4px 0}
 .smclose{position:absolute;right:10px;top:8px;cursor:pointer;color:#7f8ca3;font-size:16px}
 .smhint{position:absolute;left:14px;bottom:10px;font-size:11.5px;color:#55617a}
</style>

<div class="smstage" id="smstage">
  <canvas id="smcanvas"></canvas>
  <div class="smtop">
    <div class="smsearch">
      <input id="smq" placeholder="搜索案例 / 章节 / 知识点…" autocomplete="off">
      <div id="smres" class="smres"></div>
    </div>
    <div class="smbtns">
      <button onclick="smCycleShape()" id="smshape">形态：旋涡</button>
      <button onclick="smReset()">重置视角</button>
      <button id="smspin" onclick="smToggleSpin()">暂停自转</button>
    </div>
  </div>
  <div class="smdetail" id="smdetail">
    <div class="smclose" onclick="smClose()">×</div>
    <h3 id="smdTitle"></h3>
    <div class="smm" id="smdMeta"></div>
    <div id="smdBody"></div>
  </div>
  <div class="smload" id="smload">
    <div class="box">
      <div class="t" id="smltxt">准备加载…</div>
      <div class="b"><i id="smlbar"></i></div>
      <div class="s" id="smlsub">0.0s</div>
    </div>
  </div>
  <div class="smhint" id="smhint">拖动绕银河观察 · 滚轮拉近拉远</div>
</div>
</div>
"""

SCRIPT = r"""
// ================= 三维银河星图 =================
const GALAXY = {
  shape: 'spiral',            // spiral / elliptical / irregular
  arms: 2, twist: 1.22, radius: 5.8, thickness: 0.62,
  coreSize: 1.0, palette: 'cool-violet',
  n: {arms: 16000, bulge: 4200, halo: 3000, far: 2400, giant: 380}
};
const SHAPES = ['spiral', 'elliptical', 'irregular'];
const SHAPE_NAME = {spiral: '旋涡', elliptical: '椭圆星云', irregular: '不规则'};

const SM = {
  nodes: [], edges: [], cases: [], byParent: {},
  cam: {yaw: 0, pitch: 0.55, dist: 11.5},
  spin: 0.55,   // 模型自转角（不再动相机）
  auto: true, drag: null, hover: null, sel: null, t0: 0, intro: 0,
  css: {w:0,h:0,dpr:1, cx:0, cy:0}, raf: 0, sprite: null,
  litRatio: 1, lastMs: 0
};

const PALETTE = ['#8ab4ff','#7fe0a8','#ffd166','#ff8fab','#a78bfa','#67e8f9',
  '#fca5a5','#c4b5fd','#86efac','#fcd34d','#f9a8d4','#93c5fd','#6ee7b7','#fbbf24',
  '#e879f9','#5eead4','#fda4af'];

function smHash(s){ let h=2166136261; for(let i=0;i<s.length;i++){h^=s.charCodeAt(i);h=Math.imul(h,16777619);} return h>>>0; }
function smRng(seed){ let a=seed>>>0; return ()=>{ a|=0; a=a+0x6D2B79F5|0; let t=Math.imul(a^a>>>15,1|a); t=t+Math.imul(t^t>>>7,61|t)^t; return ((t^t>>>14)>>>0)/4294967296; }; }
function hexA(h,a){ if(!h||h[0]!=='#') return 'rgba(160,180,220,'+a+')';
  const r=parseInt(h.slice(1,3),16),g=parseInt(h.slice(3,5),16),b=parseInt(h.slice(5,7),16);
  return 'rgba('+r+','+g+','+b+','+a+')'; }

/* ---------- 数据 ---------- */
let SM_LOAD_T0 = 0, SM_LOAD_TIMER = 0;

function smProgress(txt, pct){
  const box = document.getElementById('smload');
  if (!box) return;
  box.classList.remove('off', 'err');
  if (!SM_LOAD_T0){
    SM_LOAD_T0 = performance.now();
    SM_LOAD_TIMER = setInterval(()=>{
      const s = document.getElementById('smlsub');
      if (s) s.textContent = '已用 ' + ((performance.now()-SM_LOAD_T0)/1000).toFixed(1) + 's'
                              + (performance.now()-SM_LOAD_T0 > 6000 ? ' · 还在算，没有卡死' : '');
    }, 100);
  }
  const t = document.getElementById('smltxt');
  const b = document.getElementById('smlbar');
  if (t) t.textContent = txt;
  if (b) b.style.width = Math.max(0, Math.min(100, pct)) + '%';
}

function smProgressDone(){
  const box = document.getElementById('smload');
  if (SM_LOAD_TIMER){ clearInterval(SM_LOAD_TIMER); SM_LOAD_TIMER = 0; }
  if (!box) return;
  box.classList.add('off');
  SM_LOAD_T0 = 0;
}

function smProgressErr(msg){
  const box = document.getElementById('smload');
  if (SM_LOAD_TIMER){ clearInterval(SM_LOAD_TIMER); SM_LOAD_TIMER = 0; }
  if (!box) return;
  box.classList.remove('off');
  box.classList.add('err');
  document.getElementById('smltxt').textContent = msg;
  const s = document.getElementById('smlsub');
  if (s) s.textContent = '点右上角「重置视角」可以重试';
}

async function smLoad(){
  const hint = document.getElementById('smhint');
  smProgress('读取知识库…', 8);
  try{
    const resp = await fetch('/api/starmap', {cache:'no-store'});
    smProgress('解析知识库…', 35);
    const d = await resp.json();
    if (d.error){ smProgressErr('解析失败：' + d.error); hint.textContent = d.error; return; }

    smProgress('整理 ' + d.stats.cases + ' 个案例…', 52);
    SM.nodes = d.nodes; SM.edges = d.edges;
    SM.cases = d.nodes.filter(n=>n.kind==='case');
    SM.byParent = {};
    d.nodes.forEach(n=>{ if(n.parent) (SM.byParent[n.parent]=SM.byParent[n.parent]||[]).push(n); });

    smProgress('布置知识星…', 64);
    smBuildStars();

    smProgress('生成 ' + smParticleTotal().toLocaleString() + ' 颗粒子…', 78);
    await new Promise(r=>setTimeout(r, 30));     // 让进度条先刷出来
    smBuildParticles();

    smProgress('渲染第一帧…', 92);
    await new Promise(r=>setTimeout(r, 20));
    smResize();
    smDraw();

    smProgress('完成', 100);
    hint.textContent = '案例 ' + d.stats.cases + ' · 知识点 ' + (d.stats.nodes-d.stats.cases) +
                       ' · 粒子 ' + smParticleTotal().toLocaleString();
    setTimeout(smProgressDone, 260);
  }catch(e){
    smProgressErr('加载失败：' + e);
    hint.textContent = '加载失败：' + e;
  }
}

function smBuildStars(){
  const arms = GALAXY.arms;
  SM.cases.forEach((c,i)=>{
    const rng = smRng(smHash(c.stable_key || String(c.id)));
    const r = GALAXY.radius * (0.22 + rng()*0.66);
    // 落在旋臂上：角度用和粒子同一套螺旋公式，再带一点抖动
    const arm = i % arms;
    const wob = Math.sin(r*3.7 + arm*1.7) * 0.14;
    const ang = arm*(Math.PI*2/arms) + GALAXY.twist*r + wob + (rng()-0.5)*0.10;
    c._p = {x: Math.cos(ang)*r,
            y: (rng()-0.5)*GALAXY.thickness*0.35,
            z: Math.sin(ang)*r};
    c._c = PALETTE[i % PALETTE.length];
    c._ph = rng()*6.28;
  });
}

/* ---------- 粒子 ---------- */
function smParticleTotal(){
  const n = GALAXY.n;
  return n.arms + n.bulge + n.halo + n.far + n.giant;
}

let PT = null;
function smBuildParticles(){
  if (PT) return;                       // 只建一次！否则每帧重建近万颗粒子，直接卡死
  const n = GALAXY.n, N = smParticleTotal();
  const rng = smRng(smHash(GALAXY.shape + '|' + GALAXY.arms + '|' + GALAXY.twist));
  const pos = new Float32Array(N*3), size = new Float32Array(N), ph = new Float32Array(N);
  const col = new Uint8Array(N*3), kind = new Uint8Array(N);
  const R = GALAXY.radius, th = GALAXY.thickness, arms = GALAXY.arms, tw = GALAXY.twist;
  let i = 0;

  function put(x,y,z,k,r,g,b,s){
    pos[i*3]=x; pos[i*3+1]=y; pos[i*3+2]=z;
    col[i*3]=r; col[i*3+1]=g; col[i*3+2]=b;
    size[i]=s; ph[i]=rng()*6.28; kind[i]=k; i++;
  }

  if (GALAXY.shape === 'spiral' || GALAXY.shape === 'irregular'){
    // 每条臂自己的相位和拧度，避免两条臂长得一模一样
    const armPhase = [], armTwist = [], armWidth = [];
    for (let a=0;a<arms;a++){
      armPhase[a] = (rng()-0.5) * 1.1;
      armTwist[a] = tw * (0.86 + rng()*0.30);
      armWidth[a] = 0.09 + rng()*0.12;
    }
    for (let k=0;k<arms;k++){
      const arm = k % arms;
      for (let j=0;j<n.arms/arms;j++){
        const t = Math.pow(rng(), 0.55);
        const r = (0.05 + t*0.93) * R;

        // 断续：有的地方根本没有星，像被掏空
        const gaps = Math.sin(r*5.1 + armPhase[arm]*3) * Math.sin(r*2.3 - armPhase[arm]);
        if (gaps < -0.45 && rng() < 0.72) continue;

        // 角度：基础旋 + 相位 + 两级噪声 + 臂宽随机
        const wob = Math.sin(r*3.7 + armPhase[arm]*2.0) * 0.14
                  + Math.sin(r*8.3 - armPhase[arm]) * 0.05;
        const jitter = (armWidth[arm] + 0.20*(1 - r/R)) * (rng()-0.5) * 2;
        let ang = arm*(Math.PI*2/arms) + armTwist[arm]*r + wob + jitter + armPhase[arm];
        if (GALAXY.shape === 'irregular') ang += Math.sin(r*2.3)*0.55;

        const y = (rng()-0.5) * th * (1 - 0.75*(r/R)) * (GALAXY.shape==='irregular'?1.7:1);
        const x = Math.cos(ang)*r + (rng()-0.5)*0.06;
        const z = Math.sin(ang)*r + (rng()-0.5)*0.06;

        const fade = 1 - 0.5*(r/R);
        const blueish = rng() < 0.25;
        put(x,y,z,0, (blueish?148:198)*fade, (blueish?188:214)*fade, 255,
            0.42 + rng()*0.8);
      }
    }
  } else {
    // 椭圆星云：核球为主 + 扁平盘
    for (let j=0;j<n.arms;j++){
      const rr = Math.pow(rng(), 0.85)*R;
      const a = rng()*Math.PI*2;
      put(Math.cos(a)*rr*(1.15), (rng()-0.5)*th*0.35, Math.sin(a)*rr*0.8, 0,
          205, 200, 255, 0.5+rng()*0.9);
    }
  }

  for (let j=0;j<n.bulge;j++){
    const rr = Math.pow(rng(),2.1)*R*0.30;
    const a = rng()*Math.PI*2, p = Math.acos(2*rng()-1);
    put(rr*Math.sin(p)*Math.cos(a), rr*Math.cos(p)*1.35, rr*Math.sin(p)*Math.sin(a),
        1, 255, 240, 214, 1.3+rng()*1.8);
  }
  for (let j=0;j<n.halo;j++){
    const rr = (0.42+rng()*0.85)*R;
    const a = rng()*Math.PI*2, p = Math.acos(2*rng()-1);
    put(rr*Math.sin(p)*Math.cos(a), rr*Math.cos(p)*0.95, rr*Math.sin(p)*Math.sin(a),
        2, 150, 168, 226, 0.7+rng()*0.7);
  }
  for (let j=0;j<n.far;j++){
    put((rng()-0.5)*R*4.2, (rng()-0.5)*R*2.6, (rng()-0.5)*R*4.2,
        3, 132, 146, 182, 0.5+rng()*0.5);
  }
  for (let j=0;j<n.giant;j++){
    const rr = Math.pow(rng(),0.8)*R*0.95, a = rng()*Math.PI*2;
    put(Math.cos(a)*rr,(rng()-0.5)*th*0.5, Math.sin(a)*rr, 4, 255, 244, 220, 1.8+rng()*1.6);
  }
  // 尘埃带：沿旋臂内侧、略偏角，颜色接近背景黑，盖住亮星形成吸收带
  const ND = 900;
  const dpos = new Float32Array(ND*3), dsize = new Float32Array(ND);
  for (let j=0;j<ND;j++){
    const t = Math.pow(rng(), 0.55);
    const r = (0.30 + t*0.68) * R;
    const arm = Math.floor(rng()*arms);
    const ang = arm*(Math.PI*2/arms) + tw*r + 0.30 + (rng()-0.5)*0.22;
    dpos[j*3]   = Math.cos(ang)*r + (rng()-0.5)*0.28;
    dpos[j*3+1] = (rng()-0.5)*th*0.34;
    dpos[j*3+2] = Math.sin(ang)*r + (rng()-0.5)*0.28;
    dsize[j] = 7 + rng()*16;
  }
  PT = {pos, size, col, kind, ph, n:i, dust:{pos:dpos, size:dsize, n:ND}};
}

/* ---------- 相机 ---------- */
function smProject(x,y,z){
  const c = SM.cam;
  const cy=Math.cos(SM.spin), sy=Math.sin(SM.spin);
  const rx = x*cy - z*sy;
  const rz0 = x*sy + z*cy;
  const cp=Math.cos(c.pitch), sp=Math.sin(c.pitch);
  const ry = y*cp - rz0*sp;
  const rz = y*sp + rz0*cp;
  const depth = c.dist - rz;
  if (depth < 0.5) return null;
  const focal = Math.min(SM.css.w, SM.css.h) * 1.05;
  const s = focal / depth;
  return {x: SM.css.w*0.5 + rx*s, y: SM.css.h*0.54 - ry*s, s, depth};
}


function smBuildBg(){
  const c = SM.css;
  if (!c.w) return;
  const cv = document.createElement('canvas');
  cv.width = Math.max(1, Math.round(c.w*c.dpr));
  cv.height = Math.max(1, Math.round(c.h*c.dpr));
  const g = cv.getContext('2d');
  g.scale(c.dpr, c.dpr);
  const W = c.w, H = c.h;
  const bg = g.createRadialGradient(W*0.5,H*0.5,10, W*0.5,H*0.5, Math.max(W,H)*0.8);
  bg.addColorStop(0,'#0b1220'); bg.addColorStop(0.55,'#06090f'); bg.addColorStop(1,'#02030a');
  g.fillStyle = bg; g.fillRect(0,0,W,H);

  // 远景背景星：固定不动，烘进背景图里，不参与绘制循环
  const rs = smRng(20261110);
  for (let i=0;i<260;i++){
    const x = rs()*W, y = rs()*H;
    const r = 0.25 + rs()*0.85;
    const a = 0.025 + rs()*0.14;
    g.fillStyle = 'rgba(' + (200 + Math.floor(rs()*55)) + ',' +
                  (210 + Math.floor(rs()*45)) + ',255,' + a.toFixed(2) + ')';
    g.beginPath(); g.arc(x, y, r, 0, Math.PI*2); g.fill();
  }

  const vig = g.createRadialGradient(W/2,H/2, Math.min(W,H)*0.34, W/2,H/2, Math.max(W,H)*0.78);
  vig.addColorStop(0,'rgba(0,0,0,0)'); vig.addColorStop(1,'rgba(0,0,0,0.62)');
  g.fillStyle = vig; g.fillRect(0,0,W,H);
  SM.bgCache = cv;
}

function smBuildGlow(){
  const cv = document.createElement('canvas');
  cv.width = cv.height = 512;
  const g = cv.getContext('2d');
  const gr = g.createRadialGradient(256,256,0, 256,256,256);
  gr.addColorStop(0,'rgba(255,240,214,0.34)');
  gr.addColorStop(0.30,'rgba(226,206,255,0.14)');
  gr.addColorStop(0.65,'rgba(150,160,255,0.055)');
  gr.addColorStop(1,'rgba(90,110,255,0)');
  g.fillStyle = gr; g.fillRect(0,0,512,512);
  SM.glowCache = cv;
}


function smDotSprite(){
  if (SM.dot) return SM.dot;
  const c = document.createElement('canvas');
  c.width = c.height = 16;
  const g = c.getContext('2d');
  const gr = g.createRadialGradient(8,8,0, 8,8,8);
  gr.addColorStop(0,'rgba(255,255,255,1)');
  gr.addColorStop(0.30,'rgba(255,255,255,0.55)');
  gr.addColorStop(0.62,'rgba(255,255,255,0.14)');
  gr.addColorStop(1,'rgba(255,255,255,0)');
  g.fillStyle = gr; g.fillRect(0,0,16,16);
  SM.dot = c;
  return c;
}

function smSprite(){
  if (SM.sprite) return SM.sprite;
  const c = document.createElement('canvas'); c.width = c.height = 32;
  const g = c.getContext('2d');
  const gr = g.createRadialGradient(16,16,0,16,16,16);
  gr.addColorStop(0,'rgba(255,255,255,1)');
  gr.addColorStop(0.28,'rgba(255,255,255,.62)');
  gr.addColorStop(0.65,'rgba(255,255,255,.12)');
  gr.addColorStop(1,'rgba(255,255,255,0)');
  g.fillStyle = gr; g.fillRect(0,0,32,32);
  SM.sprite = c; return c;
}

/* ---------- 绘制 ---------- */
function smDraw(ts){
  const cv = document.getElementById('smcanvas');
  if (!cv || !SM.css.w) return;
  const g = cv.getContext('2d');
  const {w,h,dpr} = SM.css;
  const now = (ts || performance.now()) / 1000;
  if (!SM.t0) SM.t0 = now;
  const t = now - SM.t0;

  g.setTransform(dpr,0,0,dpr,0,0);
  if (!SM.bgCache) smBuildBg();
  if (SM.bgCache) g.drawImage(SM.bgCache, 0, 0, w, h);
  else { g.fillStyle = '#010206'; g.fillRect(0,0,w,h); }

  // 开场：0.0-0.6 黑场，之后粒子渐亮
  const intro = Math.min(1, Math.max(0, (t-0.55)/1.1));
  if (intro <= 0) return;   // 黑场期间不重绘，交给外层循环

  // 核心呼吸

  smBuildParticles();
  const sprite = smSprite();
  const focus = SM.cam.dist;
  const REF = (Math.min(w,h) * 1.05) / 11.5;   // 世界单位 → 像素的基准
  const lit = Math.ceil(PT.n * intro * SM.litRatio);

  // 分桶绘制：同色同透明度档的粒子合并成一次 fillStyle 设置
  // 32 个桶足够（3 色 × 3 档 + 贴图路径）
  if (!SM.bk) SM.bk = Array.from({length: 32}, ()=>[]);
  const bk = SM.bk;
  for (let b=0;b<SM.bk.length;b++) bk[b].length = 0;

  g.globalCompositeOperation = 'lighter';
  for (let i=0;i<lit;i++){
    const pr = smProject(PT.pos[i*3], PT.pos[i*3+1], PT.pos[i*3+2]);
    if (!pr) continue;
    if (pr.x < -24 || pr.x > w+24 || pr.y < -24 || pr.y > h+24) continue;

    const dz = Math.abs(pr.depth - focus);
    const k = pr.s / REF;
    const kind = PT.kind[i];

    // 核球和巨星用发光贴图（数量少，值得）
    if (kind === 1 || kind === 4){
      const size = Math.max(1.2, PT.size[i] * 1.15 * k * (1 + dz*0.16));
      const a = Math.min(0.9, (0.35 - dz*0.02)) * intro;
      if (a <= 0.02) continue;
      g.globalAlpha = a;
      g.drawImage(sprite, pr.x-size*2, pr.y-size*2, size*4, size*4);
      continue;
    }

    // 其余：算好位置塞进桶里，最后统一 fillRect
    const size = Math.max(0.8, PT.size[i] * 0.95 * k * (1 + dz*0.16));
    const a = (0.72 - dz*0.030) * intro;
    if (a <= 0.02) continue;
    const band = a > 0.20 ? 0 : a > 0.10 ? 1 : 2;
    const col = kind === 0 ? 0 : kind === 2 ? 1 : 2;   // 旋臂 / 星晕 / 远景
    const bucket = bk[col*3 + band];
    bucket.push(pr.x, pr.y, size);
  }
  g.globalAlpha = 1;

  const COLORS = ['rgba(198,214,255,', 'rgba(150,168,226,', 'rgba(132,146,182,'];
  const ALPHAS = [0.85, 0.45, 0.20];
  for (let c=0;c<3;c++){
    for (let b=0;b<3;b++){
      const arr = bk[c*3 + b];
      if (!arr.length) continue;
      g.fillStyle = COLORS[c] + ALPHAS[b] + ')';
      for (let j2=0;j2<arr.length;j2+=3){
        const sz = arr[j2+2];
        g.fillRect(arr[j2], arr[j2+1], sz, sz);
      }
    }
  }
  g.globalAlpha = 1;

  g.globalAlpha = 1;

  // 尘埃带：正常混合 + 接近背景的深色，压暗经过的星光
  if (false){
    g.globalCompositeOperation = 'source-over';
    const D = PT.dust;
    for (let i=0;i<D.n;i++){
      const pr = smProject(D.pos[i*3], D.pos[i*3+1], D.pos[i*3+2]);
      if (!pr) continue;
      if (pr.x < -40 || pr.x > w+40 || pr.y < -40 || pr.y > h+40) continue;
      if (!intro) continue;
      const size = Math.max(2, D.size[i] * (pr.s / REF) * 0.9);
      const a = Math.min(0.30, 0.16 * (1 - Math.abs(pr.depth - focus)/40)) * intro;
      if (a <= 0.01) continue;
      g.fillStyle = 'rgba(2,4,10,' + a.toFixed(3) + ')';
      g.beginPath(); g.arc(pr.x, pr.y, size, 0, Math.PI*2); g.fill();
    }
    g.globalCompositeOperation = 'lighter';
  }

  // 核心光：放在尘埃之后，保证中心是亮的
  const core = smProject(0,0,0);
  if (core){
    const breathe = 1 + Math.sin(t*0.9)*0.07;
    if (!SM.glowCache) smBuildGlow();
    const cr1 = Math.min(w,h)*0.46*breathe;
    const cr2 = Math.min(w,h)*0.11*breathe;
    g.globalCompositeOperation='lighter';
    g.globalAlpha = 0.75*intro;
    g.drawImage(SM.glowCache, core.x-cr1, core.y-cr1, cr1*2, cr1*2);
    g.globalAlpha = 0.85*intro;
    g.drawImage(SM.glowCache, core.x-cr2, core.y-cr2, cr2*2, cr2*2);
    g.globalAlpha = 1;
    g.globalCompositeOperation='source-over';
  }

  // 知识星
  SM.cases.forEach(c=>{
    const pr = smProject(c._p.x, c._p.y, c._p.z);
    if (!pr) { c._s = null; return; }
    const hot = (SM.hover && SM.hover.id===c.id) || (SM.sel && SM.sel.id===c.id);
    const kids = (SM.byParent[c.id]||[]).length;
    const k = pr.s / REF;
    const base = (2.6 + Math.min(2.2, kids*0.28)) * Math.max(0.6, k);
    const r = base * (hot ? 2.0 : 1);
    // 彩色柔光（一直有，但很轻）+ 纯白核心
    g.globalAlpha = (hot ? 0.85 : 0.42) * intro;
    g.drawImage(sprite, pr.x-r*3.4, pr.y-r*3.4, r*6.8, r*6.8);
    g.globalAlpha = intro;
    g.fillStyle = '#ffffff';
    g.beginPath(); g.arc(pr.x, pr.y, Math.max(1.1, r*0.42), 0, Math.PI*2); g.fill();
    if (hot){
      g.globalAlpha = 0.9*intro;
      g.fillStyle = c._c;
      g.beginPath(); g.arc(pr.x, pr.y, Math.max(1.8, r*0.8), 0, Math.PI*2); g.fill();
    }
    g.globalAlpha = 1;
    c._s = {x:pr.x, y:pr.y, r};
  });
  g.globalCompositeOperation = 'source-over';

  // 暗角已在背景贴图里，不再每帧重画

  // 悬停标签（水平、屏幕空间）
  const lab = SM.hover;
  if (lab && lab._s){
    const txt = (lab.label||'').slice(0,20);
    g.font = '600 12px system-ui, "Microsoft YaHei"';
    const tw2 = g.measureText(txt).width;
    const x = Math.min(Math.max(lab._s.x, tw2/2+10), w-tw2/2-10);
    const y = Math.max(lab._s.y - 16, 18);
    g.textAlign='center'; g.lineWidth=3.5; g.strokeStyle='rgba(2,4,10,.92)';
    g.strokeText(txt,x,y);
    g.fillStyle='#eaf2ff'; g.fillText(txt,x,y);
  }
}

/* ---------- 尺寸 ---------- */
function smResize(){
  const cv = document.getElementById('smcanvas');
  if (!cv) return;
  const box = cv.parentElement.getBoundingClientRect();
  if (box.width < 20 || box.height < 20){ setTimeout(smResize, 260); return; }
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  SM.css = {w:box.width, h:box.height, dpr};
  SM.bgCache = null;
  cv.width = Math.round(box.width*dpr);
  cv.height = Math.round(box.height*dpr);
  smDraw();
}

/* ---------- 循环 ---------- */
function smLoop(ts){
  if (SM.auto && !SM.drag && document.getElementById('view-starmap')?.classList.contains('on')){
    if (!SM.last || ts - SM.last >= 32){          // 自转限 ~30fps
      SM.last = ts;
      const t0 = performance.now();
      SM.spin += 0.0035 * 0.5;
      smDraw(ts);
      SM.lastMs = performance.now() - t0;
      // 按真实绘制耗时调粒子比例（高刷屏帧间隔只有 6ms，按间隔判断永远失灵）
      if (SM.lastMs > 26) SM.litRatio = Math.max(0.15, SM.litRatio * 0.82);
      else if (SM.lastMs < 12) SM.litRatio = Math.min(1, SM.litRatio + 0.02);
    }
  }
  SM.frames = (SM.frames || 0) + 1;
  if (!SM.fpsT) SM.fpsT = ts;
  if (ts - SM.fpsT > 5000){
    const fps = SM.frames * 1000 / (ts - SM.fpsT);
    SM.frames = 0; SM.fpsT = ts;
    try{
      fetch('/api/clientlog', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({msg: 'fps=' + fps.toFixed(1) + ' draw=' + (SM.lastMs||0).toFixed(1) +
                                   'ms lit=' + SM.litRatio.toFixed(2) +
                                   ' n=' + Math.round(PT.n * SM.litRatio)})});
    }catch(e){}
  }
  SM.raf = requestAnimationFrame(smLoop);
}

/* ---------- 交互 ---------- */
function smHitCase(cx, cy){
  const cv = document.getElementById('smcanvas');
  const r = cv.getBoundingClientRect();
  const x = cx - r.left, y = cy - r.top;
  let best = null, bd = 20;
  SM.cases.forEach(c=>{
    if (!c._s) return;
    const d = Math.hypot(c._s.x-x, c._s.y-y);
    if (d < bd){ bd = d; best = c; }
  });
  return best;
}

function smReset(){ SM.spin = 0.55; SM.cam.pitch = 0.55; SM.cam.dist = 11.5; smDraw(); }
function smToggleSpin(){ SM.auto = !SM.auto;
  document.getElementById('smspin').textContent = SM.auto ? '暂停自转' : '开始自转'; }
function smCycleShape(){
  const i = SHAPES.indexOf(GALAXY.shape);
  GALAXY.shape = SHAPES[(i+1) % SHAPES.length];
  document.getElementById('smshape').textContent = '形态：' + SHAPE_NAME[GALAXY.shape];
  PT = null; smDraw();
}

function smOpen(c){
  document.getElementById('smdTitle').textContent = c.label;
  document.getElementById('smdMeta').textContent =
    (c.date ? c.date + ' · ' : '') + ((SM.byParent[c.id]||[]).length) + ' 个章节';
  const body = document.getElementById('smdBody');
  body.textContent = '';
  (SM.byParent[c.id]||[]).forEach(sec=>{
    const h = document.createElement('div');
    h.className = 'smsec';
    h.textContent = '▸ ' + (sec.label||'');
    body.appendChild(h);
    const wrap = document.createElement('div');
    (SM.byParent[sec.id]||[]).forEach(pt=>{
      const d = document.createElement('div');
      d.className = 'smpt';
      d.textContent = pt.text || pt.label || '';
      wrap.appendChild(d);
    });
    wrap.style.display='none';
    h.onclick = ()=>{ wrap.style.display = wrap.style.display==='none' ? 'block' : 'none'; };
    body.appendChild(wrap);
  });
  document.getElementById('smdetail').classList.add('on');
  SM.sel = c; smDraw();
}
function smClose(){
  document.getElementById('smdetail').classList.remove('on');
  SM.sel = null; smDraw();
}

function smSearch(){
  const box = document.getElementById('smres');
  const q = (document.getElementById('smq').value||'').trim().toLowerCase();
  box.textContent = '';
  if (!q){ box.style.display='none'; return; }
  const hits = SM.nodes.filter(n=>{
    const hay = ((n.text||'')+' '+(n.label||'')+' '+(n.case_title||'')).toLowerCase();
    return hay.includes(q);
  }).slice(0,24);
  if (!hits.length){ box.textContent='没有匹配'; box.style.display='block'; return; }
  hits.forEach(n=>{
    const it = document.createElement('div');
    it.className = 'smitem';
    it.textContent = (n.kind==='case'?'【案例】':'【'+(n.section_type||'知识点')+'】')
                     + (n.label||'').slice(0,36);
    it.onclick = ()=>{
      const owner = SM.cases.find(c=>c.id === (n.kind==='case'?n.id:n.case_id));
      if (owner){ smOpen(owner); smFocus(owner); }
      box.style.display='none';
    };
    box.appendChild(it);
  });
  box.style.display='block';
}

function smFocus(c){
  const p = c._p;
  SM.spin = Math.atan2(p.x, p.z);
  SM.cam.pitch = 0.42;
  SM.cam.dist = Math.max(7.5, Math.min(11, 9));
  smDraw();
}

function smInit(){
  const cv = document.getElementById('smcanvas');
  if (!cv || cv.dataset.ready) return;
  cv.dataset.ready = '1';

  cv.addEventListener('mousedown', e=>{
    SM.drag = {x:e.clientX, y:e.clientY, spin:SM.spin, pitch:SM.cam.pitch, moved:0};
    cv.style.cursor='grabbing';
  });
  cv.addEventListener('mousemove', e=>{
    if (SM.drag){
      const dx = e.clientX-SM.drag.x, dy = e.clientY-SM.drag.y;
      SM.drag.moved = Math.max(SM.drag.moved, Math.abs(dx)+Math.abs(dy));
      SM.spin = SM.drag.spin + dx*0.006;
      SM.cam.pitch = Math.max(-0.35, Math.min(1.25, SM.drag.pitch - dy*0.004));
      smDraw(); return;
    }
    const c = smHitCase(e.clientX, e.clientY);
    const changed = (c?c.id:null) !== (SM.hover?SM.hover.id:null);
    SM.hover = c; cv.style.cursor = c ? 'pointer' : 'grab';
    if (changed) smDraw();
  });
  window.addEventListener('mouseup', e=>{
    if (SM.drag && SM.drag.moved < 4){
      const c = smHitCase(e.clientX, e.clientY);
      if (c) smOpen(c); else smClose();
    }
    SM.drag = null; cv.style.cursor='grab';
  });
  cv.addEventListener('wheel', e=>{
    e.preventDefault();
    SM.cam.dist = Math.max(4.5, Math.min(22, SM.cam.dist * (e.deltaY>0?1.08:0.93)));
    smDraw();
  }, {passive:false});

  const q = document.getElementById('smq');
  if (q) q.addEventListener('input', smSearch);
  if (window.ResizeObserver) new ResizeObserver(smResize).observe(cv.parentElement);
  else window.addEventListener('resize', smResize);
  if (!SM.raf) SM.raf = requestAnimationFrame(smLoop);
}
"""
