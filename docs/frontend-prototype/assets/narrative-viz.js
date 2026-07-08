/* ============================================================
   Arrow Lake · Mission Control · 12 个 signature 可视化
   依赖: d3 (KG 力导向; 可选, 失败该卡留空) · $/$$ 来自 narrative.js
   所有内容均为硬编码 mock 常量 · 无不可信输入
   ============================================================ */

// 颜色常量（与 tokens.css 对齐，SVG 内不能用 CSS var）
const C = {
  teal: '#2DD4BF', tealD: '#14B8A6', amber: '#F59E0B', amberD: '#D97706',
  info: '#38BDF8', violet: '#A78BFA', pink: '#F472B6', lime: '#A3E635',
  ok: '#22C55E', danger: '#EF4444',
  fgHi: '#EDF1F8', fgMd: '#A7B2C8', fgLo: '#8593AC',
  ink900: '#0D1320', ink800: '#182236', ink700: '#26344F', line: '#243149',
  mono: "'JetBrains Mono',ui-monospace,monospace",
};

// 一次性注入 viz 内部组件样式（textContent · 安全）
(function injectStyle() {
  const st = document.createElement('style');
  st.textContent = `
.vf{position:absolute;inset:0;padding:46px 22px 18px;display:flex;flex-direction:column;gap:8px;overflow:auto}
.vf::-webkit-scrollbar{width:5px}.vf::-webkit-scrollbar-thumb{background:#324460;border-radius:3px}
.vhero-num{font-family:${C.mono};font-size:clamp(3rem,8vw,5rem);font-weight:800;color:#2DD4BF;line-height:1;text-shadow:0 0 28px rgba(20,184,166,.45);letter-spacing:-.04em}
.vcode{background:#090D16;border:1px solid #243149;border-radius:8px;padding:12px 14px;font-family:${C.mono};font-size:.74rem;color:#A7B2C8;white-space:pre;overflow:auto;margin:0;line-height:1.55}
.vcode .clang{color:#8593AC;display:block;margin-bottom:6px;font-size:.66rem}
.vtabs{display:flex;gap:4px;align-items:center}
.vtab{background:#182236;border:1px solid #243149;color:#8593AC;padding:5px 12px;border-radius:6px;cursor:pointer;font-family:${C.mono};font-size:.72rem}
.vtab.on{background:rgba(20,184,166,.16);color:#2DD4BF;border-color:rgba(20,184,166,.5)}
.vtab:hover{border-color:#324460}
.mgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:4px}
.mcard{background:#0D1320;border:1px solid #243149;border-radius:8px;padding:10px;display:flex;flex-direction:column;gap:3px}
.mci{font-size:1.4rem}.mct{color:#EDF1F8;font-size:.74rem;font-weight:600}.mcm{color:#8593AC;font-size:.6rem;font-family:${C.mono};text-transform:uppercase}
.mbar{height:4px;border-radius:2px;margin-top:2px}.mcs{color:#A7B2C8;font-family:${C.mono};font-size:.66rem}
.audit{display:flex;flex-direction:column;gap:4px;margin-top:2px}
.arow{display:grid;grid-template-columns:auto auto 1fr auto auto;gap:8px;align-items:center;padding:6px 10px;background:#0D1320;border:1px solid #243149;border-radius:6px;font-size:.7rem}
.arow.bad{border-color:rgba(239,68,68,.5);background:rgba(239,68,68,.06)}
.alamp{width:8px;height:8px;border-radius:50%;background:#22C55E;box-shadow:0 0 6px #22C55E}
.arow.bad .alamp{background:#EF4444;box-shadow:0 0 6px #EF4444}
.aevt{color:#2DD4BF;font-family:${C.mono};font-weight:600}.ads{color:#8593AC;font-family:${C.mono}}
.ahash{color:#8593AC;font-size:.64rem}.aver{color:#22C55E;font-family:${C.mono};font-size:.64rem}
.arow.bad .aver{color:#EF4444}
.toggles{display:flex;flex-direction:column;gap:4px;margin-top:2px}
.tog{display:grid;grid-template-columns:60px 1fr auto auto;gap:8px;align-items:center;padding:6px 10px;background:#0D1320;border:1px solid #243149;border-radius:6px;font-size:.7rem}
.tog-n{color:#EDF1F8;font-weight:600;font-family:${C.mono}}.tog-d{color:#8593AC}
.tog-btn{width:36px;height:18px;border-radius:10px;background:#26344F;border:1px solid #324460;cursor:pointer;padding:0;position:relative;transition:background .2s}
.tog-btn.on{background:rgba(34,197,94,.3);border-color:#22C55E}
.tog-k{position:absolute;top:1px;left:1px;width:14px;height:14px;border-radius:50%;background:#8593AC;transition:transform .2s,background .2s}
.tog-btn.on .tog-k{transform:translateX(18px);background:#22C55E;box-shadow:0 0 6px #22C55E}
.tog-st{color:#22C55E;font-family:${C.mono};font-size:.64rem}
.vbox{background:#0D1320;border:1px solid #243149;border-radius:8px;padding:10px 12px;margin-top:4px}
.vbox-h{color:#F59E0B;font-family:${C.mono};font-size:.72rem;font-weight:600;margin-bottom:6px}
.diffrow{display:flex;justify-content:space-between;padding:4px 0;font-family:${C.mono};font-size:.7rem;color:#A7B2C8;border-bottom:1px dashed #1B2640}
.diffrow:last-child{border-bottom:0}
.diffrow.add{color:#EDF1F8}.diffrow .k{color:#8593AC}.diffrow .v{color:#A7B2C8}
.diffrow.add .v{color:#2DD4BF}.diffrow .v i{color:#F59E0B;font-style:normal;font-size:.64rem;margin-left:4px}
.ans{color:#EDF1F8;font-size:.82rem;line-height:1.7;background:#0D1320;border:1px solid #243149;border-radius:8px;padding:12px 14px}
.cit{color:#2DD4BF;font-weight:700;cursor:help;font-size:.66rem}
.cites{display:flex;flex-direction:column;gap:4px;margin-top:4px}
.cite-card{background:#0D1320;border:1px solid #243149;border-radius:6px;padding:6px 10px;transition:border-color .2s,background .2s}
.cite-card.hl{border-color:#2DD4BF;background:rgba(20,184,166,.08)}
.cc-h{font-family:${C.mono};font-size:.7rem}.cc-mode{float:right;color:#F59E0B;font-size:.6rem}
@keyframes bob{0%,100%{transform:translateY(0);opacity:.6}50%{transform:translateY(4px);opacity:1}}
`;
  document.head.appendChild(st);
})();

// 可视化标题（左上角 HUD）
const TITLE = {
  hero: ['HERO', '一个请求的旅程'], entry: ['① 接入层', '三入口 · Lake facade'],
  vecspace: ['② 能力·向量', '向量空间投影'], funnel: ['② 能力·向量', '检索漏斗 IVF_PQ'],
  modal: ['② 能力·多模态', 'CLIP 跨模态'], kg: ['②→⑤ KG 旁路', 'kg_papers 分图'],
  daft: ['③ 计算', 'Daft 批推理'], sql: ['④ 引擎', 'DuckDB EXPLAIN + SQL-PGQ'],
  version: ['⑤ 持久化', 'Lance 版本时间机器'], answer: ['① 接入·回', '带引用的答案'],
  cross: ['⟂ 横切面', 'HMAC 审计 + 优雅降级'], finale: ['收束', '向下看版本全景 ↓'],
};
function head(name) {
  const [t, s] = TITLE[name] || ['', ''];
  const d = document.createElement('div');
  d.style.cssText = 'position:absolute;top:14px;left:34px;right:14px;display:flex;justify-content:space-between;align-items:baseline;font-family:' + C.mono + ';pointer-events:none;z-index:2';
  const a = document.createElement('span'); a.style.cssText = 'font-size:.68rem;color:#2DD4BF;letter-spacing:.12em;text-transform:uppercase;font-weight:600'; a.textContent = t;
  const b = document.createElement('span'); b.style.cssText = 'font-size:.64rem;color:#8593AC'; b.textContent = s;
  d.appendChild(a); d.appendChild(b);
  return d;
}
// 把 head 节点挂到 card（每次 renderViz 重置一次）
function mount(card, name) {
  // 不清空 card——保留 HTML 预置的 <canvas>/<svg>（vecspace/kg 依赖）
  if (card.dataset.mounted) return;
  card.dataset.mounted = '1';
  card.appendChild(head(name));
}

// ============ HERO ============
function renderHero(card) {
  if (card.dataset.done) return; card.dataset.done = '1'; mount(card, 'hero');
  const wrap = document.createElement('div');
  wrap.className = 'vf'; wrap.style.cssText += 'justify-content:center;align-items:center;text-align:center';
  wrap.innerHTML = `<div class="vhero-num">552</div>
    <div style="color:#A7B2C8;font-size:.88rem;margin-top:-2px">页 PDF · 已摄入 <b style="color:#2DD4BF">kg_papers</b></div>
    <div style="margin-top:16px;padding:13px 20px;border:1px solid #243149;border-radius:8px;background:#0D1320;max-width:92%">
      <div style="color:#8593AC;font-size:.64rem;font-family:${C.mono};text-transform:uppercase;letter-spacing:.1em">用户提问</div>
      <div style="color:#EDF1F8;font-size:.98rem;margin-top:3px">「attention 机制的核心结论是什么？」</div></div>
    <div style="display:flex;gap:20px;margin-top:20px;font-family:${C.mono}">
      <div><b style="color:#2DD4BF;font-size:1.35rem" id="hQps">1284</b><div style="font-size:.58rem;color:#8593AC">QPS</div></div>
      <div><b style="color:#F59E0B;font-size:1.35rem">42ms</b><div style="font-size:.58rem;color:#8593AC">p50</div></div>
      <div><b style="color:#22C55E;font-size:1.35rem">5</b><div style="font-size:.58rem;color:#8593AC">层架构</div></div>
      <div><b style="color:#A78BFA;font-size:1.35rem">11</b><div style="font-size:.58rem;color:#8593AC">版本</div></div></div>`;
  card.appendChild(wrap);
  const q = wrap.querySelector('#hQps');
  if (q && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    let v = 1284; setInterval(() => { v += Math.round((Math.random() - .45) * 30); q.textContent = v.toLocaleString(); }, 1500);
  }
}

// ============ ENTRY · 三入口 ============
function renderEntry(card) {
  if (card.dataset.done) return; card.dataset.done = '1'; mount(card, 'entry');
  const tabs = [
    ['SDK', 'python', `lake = Lake.from_yaml("configs/prod.yaml")
ans = await lake.rag_query(
    "attention 的核心结论?",
    dataset_name="kg_papers",
    retrieval_strategy="hybrid")`],
    ['REST', 'POST /rag/query', `{ "question": "attention 的核心结论?",
  "dataset_name": "kg_papers",
  "retrieval_strategy": "hybrid" }`],
    ['CLI', 'arrow-lake rag', `arrow-lake rag query \\
  --question "attention 的核心结论?" \\
  --dataset kg_papers \\
  --strategy hybrid`],
  ];
  const wrap = document.createElement('div'); wrap.className = 'vf'; card.appendChild(wrap);
  const tbar = document.createElement('div'); tbar.className = 'vtabs';
  tbar.innerHTML = tabs.map((t, i) => `<button class="vtab${i === 0 ? ' on' : ''}" data-i="${i}">${t[0]}</button>`).join('') + '<span style="margin-left:auto;font-size:.6rem;color:#8593AC;font-family:' + C.mono + '">106 routes · 16 cmd</span>';
  wrap.appendChild(tbar);
  const code = document.createElement('pre'); code.className = 'vcode'; wrap.appendChild(code);
  const foot = document.createElement('div'); foot.style.cssText = 'margin-top:auto;font-size:.68rem;color:#8593AC;font-family:' + C.mono; foot.textContent = '三入口共享同一套 Pydantic 模型 · 行为一致'; wrap.appendChild(foot);
  let cur = 0;
  const render = () => { const t = tabs[cur]; code.innerHTML = `<span class="clang">${t[1]}</span>${t[2]}`; };
  render();
  tbar.querySelectorAll('.vtab').forEach(b => b.onclick = () => { cur = +b.dataset.i; tbar.querySelectorAll('.vtab').forEach(x => x.classList.toggle('on', x === b)); render(); });
}

// ============ 向量空间（Canvas 动画） ============
function genPts(w, h) {
  const pts = []; const cl = [[.28, .36], [.72, .6], [.5, .5], [.22, .72], [.8, .26]];
  for (let i = 0; i < 560; i++) {
    const c = cl[i % 5], a = Math.random() * 6.28, r = Math.random() * Math.random() * 90;
    pts.push({ bx: c[0] * w + Math.cos(a) * r, by: c[1] * h + Math.sin(a) * r, sim: Math.random() });
  }
  return pts;
}
function renderVecspace(card) {
  mount(card, 'vecspace');
  const cv = card.querySelector('canvas'); if (!cv) return;
  const ctx = cv.getContext('2d'), dpr = Math.min(2, window.devicePixelRatio || 1);
  cv.width = cv.clientWidth * dpr; cv.height = cv.clientHeight * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const w = cv.clientWidth, h = cv.clientHeight;
  if (!card._pts) card._pts = genPts(w, h);
  const pts = card._pts, qx = w * 0.5, qy = h * 0.5;
  if (card._raf) cancelAnimationFrame(card._raf);
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const t0 = performance.now();
  const draw = tt => {
    ctx.clearRect(0, 0, w, h);
    pts.forEach(p => {
      const cand = p.sim > 0.62;
      const tx = cand ? qx + (p.bx - qx) * 0.22 : p.bx, ty = cand ? qy + (p.by - qy) * 0.22 : p.by;
      const x = p.bx + (tx - p.bx) * tt, y = p.by + (ty - p.by) * tt;
      ctx.beginPath(); ctx.arc(x, y, cand ? 2.6 : 1.4, 0, 6.29);
      ctx.fillStyle = cand ? `rgba(45,212,191,${.4 + .55 * tt})` : `rgba(133,147,172,${.32 - .13 * tt})`; ctx.fill();
      if (cand && tt > .35) { ctx.strokeStyle = `rgba(20,184,166,${.13 * (tt - .35) / .65})`; ctx.lineWidth = .5; ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(qx, qy); ctx.stroke(); }
    });
    ctx.beginPath(); ctx.arc(qx, qy, 7, 0, 6.29); ctx.fillStyle = '#F59E0B'; ctx.shadowColor = '#D97706'; ctx.shadowBlur = 18; ctx.fill(); ctx.shadowBlur = 0;
    ctx.strokeStyle = 'rgba(245,158,11,.45)'; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(qx, qy, 15, 0, 6.29); ctx.stroke();
  };
  const tick = () => { const t = reduced ? 1 : Math.min(1, (performance.now() - t0) / 1100); draw(t); if (t < 1) card._raf = requestAnimationFrame(tick); };
  tick();
  const lg = card.querySelector('#vecLegend');
  if (lg && !lg.innerHTML) lg.innerHTML = '<span>● <b>候选</b> top-40 向 query 聚拢</span><span>● 其余向量</span><span>● <b style="color:#F59E0B">query</b> · Qwen3-1024d</span>';
}

// ============ 检索漏斗（SVG） ============
function renderFunnel(card) {
  if (card.dataset.done) return; card.dataset.done = '1'; mount(card, 'funnel');
  const stages = [['全量向量', 9432881, C.info], ['IVF nprobes=12', 141493, C.teal], ['PQ 近似排序', 2000, C.violet], ['Reranker 精排', 148, C.amber], ['top_k = 10', 10, C.amberD]];
  const W = 520, H = 340, top = 16, lh = (H - top) / stages.length;
  let s = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" style="width:100%;height:100%">`;
  stages.forEach((st, i) => {
    const fw = W * (1 - i * .16), fx = (W - fw) / 2, fw2 = W * (1 - (i + 1) * .16), fx2 = (W - fw2) / 2, y = top + i * lh;
    s += `<path d="M${fx},${y} L${fx + fw},${y} L${fx2 + fw2},${y + lh - 8} L${fx2},${y + lh - 8} Z" fill="${st[2]}" fill-opacity=".15" stroke="${st[2]}" stroke-width="1.3"/>`;
    s += `<text x="${W / 2}" y="${y + lh / 2 - 3}" text-anchor="middle" fill="${st[2]}" font-family="${C.mono}" font-size="12" font-weight="600">${st[0]}</text>`;
    s += `<text x="${W / 2}" y="${y + lh / 2 + 15}" text-anchor="middle" fill="${C.fgHi}" font-family="${C.mono}" font-size="17" font-weight="700">${st[1].toLocaleString()}</text>`;
  });
  s += '</svg>';
  const wrap = document.createElement('div'); wrap.className = 'vf'; wrap.style.paddingTop = '50px'; wrap.innerHTML = s;
  card.appendChild(wrap);
}

// ============ 多模态卡片墙 ============
function renderModal(card) {
  if (card.dataset.done) return; card.dataset.done = '1'; mount(card, 'modal');
  const items = [['📄', '论文摘要', 'text', .91], ['📊', 'Fig.3 架构图', 'clip', .87], ['🎵', '讲座音频片段', 'audio', .79], ['🎬', '讲解视频帧', 'clip', .74], ['🖼️', '注意力热力图', 'clip', .83], ['📝', '代码 snippet', 'text', .69]];
  const bar = c => `linear-gradient(90deg, ${c} ${Math.round(c * 100)}%, #243149 ${Math.round(c * 100)}%)`;
  const wrap = document.createElement('div'); wrap.className = 'vf';
  wrap.innerHTML = `<div style="font-size:.76rem;color:#A7B2C8">query: 「attention 可视化」→ CLIP 跨模态检索</div><div class="mgrid">${items.map(it => `<div class="mcard"><div class="mci">${it[0]}</div><div class="mct">${it[1]}</div><div class="mcm">${it[2]}</div><div class="mbar" style="background:${bar(it[3] > .8 ? C.teal : it[3] > .72 ? C.amber : C.fgLo)}"></div><div class="mcs">${it[3].toFixed(2)}</div></div>`).join('')}</div>`;
  card.appendChild(wrap);
}

// ============ KG 力导向图（d3-force） ============
function renderKg(card) {
  if (card.dataset.done) return; card.dataset.done = '1'; mount(card, 'kg');
  if (!window.d3) {
    const f = document.createElement('div'); f.className = 'vf'; f.style.alignItems = 'center'; f.style.justifyContent = 'center';
    f.textContent = 'd3 加载失败 · KG 力导向图不可用'; card.appendChild(f); return;
  }
  const wrap = document.createElement('div'); wrap.className = 'vf'; wrap.style.paddingTop = '46px';
  wrap.innerHTML = '<div style="font-size:.7rem;color:#8593AC;font-family:' + C.mono + ';margin-bottom:2px">kg_papers · per-dataset 分图 · GraphRAG 子图</div>';
  const svgEl = card.querySelector('#kgSvg');
  const w = card.clientWidth, h = card.clientHeight - 60;
  const svg = d3.select(svgEl).attr('viewBox', `0 0 ${w} ${h}`).attr('width', '100%').attr('height', '100%');
  svg.selectAll('*').remove();
  const nodes = [
    { id: 'attn', lbl: 'Attention', r: 19, c: C.amber }, { id: 'tf', lbl: 'Transformer', r: 15, c: C.teal },
    { id: 'qkv', lbl: 'Q·K·V', r: 12, c: C.teal }, { id: 'sm', lbl: 'Softmax', r: 11, c: C.teal },
    { id: 'vaswani', lbl: 'Vaswani 2017', r: 14, c: C.info }, { id: 'bhd', lbl: 'Bahdanau 2014', r: 13, c: C.info },
    { id: 'bert', lbl: 'BERT', r: 12, c: C.violet }, { id: 'gpt', lbl: 'GPT', r: 12, c: C.violet },
    { id: 'wiki', lbl: 'Wikipedia', r: 10, c: C.fgLo }, { id: 'arxiv', lbl: 'arXiv', r: 10, c: C.fgLo }, { id: 'lab', lbl: 'Google Brain', r: 10, c: C.fgLo },
  ];
  const links = [['attn', 'tf'], ['tf', 'qkv'], ['tf', 'sm'], ['attn', 'bert'], ['attn', 'gpt'], ['qkv', 'sm'], ['vaswani', 'attn'], ['bhd', 'attn'], ['vaswani', 'tf'], ['bert', 'gpt'], ['wiki', 'bert'], ['arxiv', 'vaswani'], ['arxiv', 'bhd'], ['lab', 'vaswani']].map(([s, t]) => ({ source: s, target: t }));
  const link = svg.append('g').selectAll('line').data(links).join('line').attr('stroke', C.line).attr('stroke-width', 1).attr('stroke-opacity', .7);
  const node = svg.append('g').selectAll('g').data(nodes).join('g');
  node.append('circle').attr('r', d => d.r).attr('fill', d => d.c + '22').attr('stroke', d => d.c).attr('stroke-width', 1.5);
  node.append('text').text(d => d.lbl).attr('text-anchor', 'middle').attr('dy', d => d.r + 12).attr('font-family', C.mono).attr('font-size', 9).attr('fill', C.fgMd);
  node.append('title').text(d => d.lbl);
  const sim = d3.forceSimulation(nodes).force('link', d3.forceLink(links).id(d => d.id).distance(56)).force('charge', d3.forceManyBody().strength(-190)).force('center', d3.forceCenter(w / 2, h / 2)).force('collide', d3.forceCollide().radius(d => d.r + 8));
  sim.on('tick', () => { link.attr('x1', d => d.source.x).attr('y1', d => d.source.y).attr('x2', d => d.target.x).attr('y2', d => d.target.y); node.attr('transform', d => `translate(${d.x},${d.y})`); });
  card.appendChild(wrap);
}

// ============ Daft 流水线 DAG ============
function renderDaft(card) {
  if (card.dataset.done) return; card.dataset.done = '1'; mount(card, 'daft');
  const nodes = [['read_parquet', C.info], ['decode', C.violet], ['CLIP embed', C.teal], ['quality score', C.amber], ['write_lance', C.amberD]];
  const wrap = document.createElement('div'); wrap.className = 'vf'; wrap.style.paddingTop = '50px';
  wrap.innerHTML = `<svg viewBox="0 0 560 180" preserveAspectRatio="xMidYMid meet" style="width:100%;height:100%" id="daftSvg"></svg><div style="font-size:.68rem;color:#8593AC;font-family:${C.mono};text-align:center;margin-top:4px">Ray 集群横向扩展 · set_runner 本地回退</div>`;
  card.appendChild(wrap);
  const svg = wrap.querySelector('#daftSvg'); const NS = 'http://www.w3.org/2000/svg';
  const W = 560, nw = 96, gap = (W - nodes.length * nw) / (nodes.length - 1), y = 62;
  nodes.forEach((n, i) => {
    const x = i * (nw + gap);
    const g = document.createElementNS(NS, 'g');
    g.innerHTML = `<rect x="${x}" y="${y}" width="${nw}" height="56" rx="8" fill="${n[1]}22" stroke="${n[1]}" stroke-width="1.3"/><text x="${x + nw / 2}" y="${y + 24}" text-anchor="middle" fill="${n[1]}" font-family="${C.mono}" font-size="10.5" font-weight="600">${n[0]}</text><text x="${x + nw / 2}" y="${y + 40}" text-anchor="middle" fill="${C.fgLo}" font-family="${C.mono}" font-size="8">step ${i + 1}</text>`;
    svg.appendChild(g);
    if (i < nodes.length - 1) { const ln = document.createElementNS(NS, 'line'); ln.setAttribute('x1', x + nw); ln.setAttribute('y1', y + 28); ln.setAttribute('x2', x + nw + gap); ln.setAttribute('y2', y + 28); ln.setAttribute('stroke', C.teal); ln.setAttribute('stroke-opacity', .5); ln.setAttribute('stroke-width', 1.2); svg.appendChild(ln); }
  });
  if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
    const rects = svg.querySelectorAll('rect');
    const pulse = () => { const off = (performance.now() % 2500) / 2500; const idx = Math.floor(off * nodes.length); rects.forEach((r, i) => r.setAttribute('fill', nodes[i][1] + (i === idx ? '44' : '22'))); card._raf = requestAnimationFrame(pulse); };
    pulse();
  }
}

// ============ SQL EXPLAIN 计划树 ============
function renderSql(card) {
  if (card.dataset.done) return; card.dataset.done = '1'; mount(card, 'sql');
  const nodes = [['Limit', 10, '取前 10', C.amberD], ['Sort', 148, 'score desc', C.amber], ['Filter (year>2017)', 2400, '谓词下推 Lance', C.violet], ['HashJoin ⨝ kg_papers', 18400, '向量 ∩ 图', C.info], ['Scan Lance(docs)', 9432881, '零拷贝', C.teal]];
  const W = 520, H = 300, top = 18, lh = 54;
  let s = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" style="width:100%;height:100%">`;
  nodes.forEach((n, i) => {
    const y = top + i * lh, x = W / 2 - 110, w = 220;
    s += `<rect x="${x}" y="${y}" width="${w}" height="42" rx="6" fill="${n[3]}18" stroke="${n[3]}" stroke-width="1.2"/>`;
    s += `<text x="${x + 12}" y="${y + 18}" fill="${n[3]}" font-family="${C.mono}" font-size="11" font-weight="600">${n[0]}</text>`;
    s += `<text x="${x + w - 12}" y="${y + 18}" text-anchor="end" fill="${C.fgHi}" font-family="${C.mono}" font-size="11" font-weight="700">${n[1].toLocaleString()}</text>`;
    s += `<text x="${x + 12}" y="${y + 34}" fill="${C.fgLo}" font-family="${C.mono}" font-size="9">${n[2]}</text>`;
    if (i < nodes.length - 1) s += `<line x1="${W / 2}" y1="${y + 42}" x2="${W / 2}" y2="${y + lh}" stroke="${n[3]}" stroke-opacity=".5" stroke-width="1.2"/>`;
  });
  s += '</svg>';
  const wrap = document.createElement('div'); wrap.className = 'vf'; wrap.style.paddingTop = '48px';
  wrap.innerHTML = `<div style="font-size:.66rem;color:#8593AC;font-family:${C.mono};margin-bottom:2px">EXPLAIN · SELECT … FROM docs JOIN kg_papers WHERE year&gt;2017 ORDER BY score LIMIT 10</div>${s}`;
  card.appendChild(wrap);
}

// ============ 版本时间机器 ============
function renderVersion(card) {
  if (card.dataset.done) return; card.dataset.done = '1'; mount(card, 'version');
  const vers = ['v1.7.1', 'v1.8.0', 'v1.8.3', 'v1.8.6'];
  const data = {
    'v1.7.1': ['基线快照', [['rows', '7,812,400', 0], ['schema', 'v3', 0], ['index', 'IVF_PQ(nprobes=8)', 0], ['kg', '单图混存', 0]]],
    'v1.8.0': ['+Reranker/CLIP', [['rows', '8,491,200 +679k', 1], ['+clip_col', 'vector(512) NEW', 1], ['index', '+reranker bridge', 0], ['branches', '2 (main,exp) NEW', 1]]],
    'v1.8.3': ['+HA', [['rows', '9,012,000', 0], ['readiness', 'gate ✅ NEW', 1], ['warmup', '后台化 ⚡', 0]]],
    'v1.8.6': ['+per-dataset 分图', [['rows', '9,432,881 +420k', 1], ['kg', 'kg_papers 独立 迁移', 1], ['acl', 'per-dataset ✅ NEW', 1], ['traverser', '8 × REST', 0]]],
  };
  const wrap = document.createElement('div'); wrap.className = 'vf'; card.appendChild(wrap);
  wrap.innerHTML = `<div style="display:flex;align-items:center;gap:10px;font-family:${C.mono};font-size:.72rem;color:#A7B2C8"><span>v1.7.1</span><input type="range" id="verSlider" min="0" max="3" value="3" step="1" style="flex:1;accent-color:#2DD4BF"><span id="verLabel" style="color:#F59E0B;font-weight:600">v1.8.6</span></div><div class="vbox" id="verBox"></div><div style="font-size:.68rem;color:#8593AC;font-family:${C.mono}">Lance branches · 表级快照 · 拖动滑块看每版 diff</div>`;
  const box = wrap.querySelector('#verBox'), lab = wrap.querySelector('#verLabel'), sl = wrap.querySelector('#verSlider');
  const upd = () => { const v = vers[+sl.value]; lab.textContent = v; const [title, rows] = data[v]; box.innerHTML = `<div class="vbox-h">${v} · ${title}</div>${rows.map(r => `<div class="diffrow${r[2] ? ' add' : ''}"><span class="k">${r[0]}</span><span class="v">${r[1]}</span></div>`).join('')}`; };
  sl.oninput = upd; upd();
}

// ============ 带引用的答案 ============
function renderAnswer(card) {
  if (card.dataset.done) return; card.dataset.done = '1'; mount(card, 'answer');
  const cites = [[1, 'chunk[42]', '§3.2', '0.91', '向量', 'scaled dot-product attention computes on queries, keys, values'], [2, 'chunk[118]', '§4.1', '0.84', 'BM25', 'applying softmax over scaled scores yields attention weights'], [3, 'chunk[207]', '§4.4', '0.79', 'GraphRAG', 'multi-head attention projects into multiple subspaces in parallel']];
  const wrap = document.createElement('div'); wrap.className = 'vf'; card.appendChild(wrap);
  wrap.innerHTML = `<div style="font-size:.64rem;color:#8593AC;font-family:${C.mono};text-transform:uppercase;letter-spacing:.1em">RAG · retrieval=hybrid · GraphRAG</div>
    <div class="ans">注意力机制让模型为序列每个位置动态分配关注权重<sup class="cit" data-c="1">[1]</sup>。核心是 Query-Key-Value 三元组的缩放点积配合 Softmax 归一化<sup class="cit" data-c="2">[2]</sup>。多头注意力在不同子空间并行捕捉关系<sup class="cit" data-c="3">[3]</sup>，是 Transformer 的基石<sup class="cit" data-c="1">[1]</sup>。</div>
    <div class="cites">${cites.map(c => `<div class="cite-card" data-c="${c[0]}"><div class="cc-h"><b>[${c[0]}]</b> <span style="color:#A7B2C8">${c[1]} · ${c[2]}</span><span class="cc-mode">${c[4]}</span></div><div style="color:#8593AC;font-size:.64rem;margin-top:2px;font-family:${C.mono}">score ${c[3]} · row ${1000 + c[0] * 37}</div><div style="color:#A7B2C8;font-size:.7rem;margin-top:3px;line-height:1.45">…${c[5]}…</div></div>`).join('')}</div>`;
  wrap.querySelectorAll('.cit').forEach(sup => sup.onmouseenter = () => wrap.querySelectorAll('.cite-card').forEach(c => c.classList.toggle('hl', c.dataset.c === sup.dataset.c)));
}

// ============ 横切面：HMAC 审计 + 降级开关 ============
function renderCross(card) {
  if (card.dataset.done) return; card.dataset.done = '1'; mount(card, 'cross');
  const audit = [['search.vector', 'docs', true, 'a1b2…7c3d'], ['rag.query', 'kg_papers', true, '9f0e…2b4a'], ['admin.acl.set', 'finance', false, '8c3d… 篡改'], ['kg.traverse', 'kg_papers', true, '1d5f…0e9a']];
  const togs = [['Ray', '本地多进程回退'], ['KG', 'Vector RAG 回退'], ['Gremlin', 'REST 降级']];
  const wrap = document.createElement('div'); wrap.className = 'vf'; card.appendChild(wrap);
  wrap.innerHTML = `<div style="font-size:.64rem;color:#8593AC;font-family:${C.mono};text-transform:uppercase;letter-spacing:.1em">HMAC-SHA256 审计流</div>
    <div class="audit">${audit.map(r => `<div class="arow${r[2] ? ' ok' : ' bad'}"><span class="alamp"></span><span class="aevt">${r[0]}</span><span class="ads">${r[1]}</span><span class="ahash">${r[3]}</span><span class="aver">${r[2] ? '✓ verified' : '✗ tampered'}</span></div>`).join('')}</div>
    <div style="font-size:.64rem;color:#8593AC;font-family:${C.mono};text-transform:uppercase;letter-spacing:.1em;margin-top:12px">优雅降级 · 一等公民</div>
    <div class="toggles">${togs.map((t, i) => `<div class="tog"><span class="tog-n">${t[0]}</span><span class="tog-d">${t[1]}</span><button class="tog-btn on" data-i="${i}"><span class="tog-k"></span></button><span class="tog-st">在线</span></div>`).join('')}</div>
    <div id="degMsg" style="font-size:.7rem;color:#22C55E;font-family:${C.mono};margin-top:4px">✓ 全部依赖在线 · 正常服务</div>`;
  const st = [true, true, true], fb = ['本地多进程', 'Vector RAG', 'REST 降级'];
  const upd = () => { const off = [0, 1, 2].filter(i => !st[i]); const m = wrap.querySelector('#degMsg'); if (!off.length) { m.textContent = '✓ 全部依赖在线 · 正常服务'; m.style.color = '#22C55E'; } else { m.innerHTML = '⚠ ' + off.map(i => fb[i]).join(' · ') + ' 回退中 · 仍持续服务'; m.style.color = '#F59E0B'; } };
  wrap.querySelectorAll('.tog-btn').forEach(b => b.onclick = () => { const i = +b.dataset.i; st[i] = !st[i]; b.classList.toggle('on', st[i]); wrap.querySelectorAll('.tog-st')[i].textContent = st[i] ? '在线' : '降级'; upd(); });
}

// ============ 收束提示 ============
function renderFinale(card) {
  if (card.dataset.done) return; card.dataset.done = '1'; mount(card, 'finale');
  const wrap = document.createElement('div'); wrap.className = 'vf'; wrap.style.justifyContent = 'center'; wrap.style.alignItems = 'center'; wrap.style.textAlign = 'center';
  wrap.innerHTML = `<div style="font-size:1rem;color:#A7B2C8">向下滚动 · 查看 <b style="color:#F59E0B">11 个版本</b> 全景时间轴</div><div style="font-size:2rem;margin-top:8px;animation:bob 2s infinite">↓</div>`;
  card.appendChild(wrap);
}

// ============ 调度 ============
const VIZ = { hero: renderHero, entry: renderEntry, vecspace: renderVecspace, funnel: renderFunnel, modal: renderModal, kg: renderKg, daft: renderDaft, sql: renderSql, version: renderVersion, answer: renderAnswer, cross: renderCross, finale: renderFinale };
window.renderViz = function (name) {
  const card = document.querySelector(`.viz-card[data-viz="${name}"]`);
  if (!card) return;
  const fn = VIZ[name];
  if (fn) fn(card);
};
