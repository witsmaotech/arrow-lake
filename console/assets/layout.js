/* ============================================================
   Arrow Lake Console — Layout shell, icons, widgets
   Zero external deps. All visuals hand-rolled SVG.
   ============================================================ */
(function(){
"use strict";
const $ = (s,r=document)=>r.querySelector(s);
const $$ = (s,r=document)=>[...r.querySelectorAll(s)];

/* —— Icon set (Phosphor-ish, stroke 1.6) —— */
const P = {
dashboard:'<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
database:'<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
ingest:'<path d="M12 16V4"/><path d="M7 9l5-5 5 5"/><path d="M4 20h16"/>',
search:'<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
rag:'<path d="M21 12a8 8 0 1 1-3.2-6.4"/><path d="M21 4v4h-4"/><path d="M12 8v8M8 12h8"/>',
kg:'<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M7.8 7.8l3.4 8.4M16.2 7.8l-3.4 8.4M8.5 6h7"/>',
olap:'<path d="M9 6L4 12l5 6M15 6l5 6-5 6"/>',
embed:'<path d="M12 3v18M3 8l9-5 9 5M3 16l9 5 9-5"/>',
quality:'<path d="M12 2l8 4v6c0 5-3.5 8-8 10-4.5-2-8-5-8-10V6l8-4z"/><path d="M9 12l2 2 4-4"/>',
lineage:'<circle cx="6" cy="6" r="2"/><circle cx="6" cy="18" r="2"/><circle cx="18" cy="12" r="2"/><path d="M8 6h4a4 4 0 0 1 4 4M8 18h4a4 4 0 0 0 4-4"/>',
audit:'<path d="M12 2l8 4v6c0 5-3.5 8-8 10-4.5-2-8-5-8-10V6l8-4z"/><path d="M12 8v4M12 16h.01"/>',
governance:'<path d="M3 7l9-4 9 4-9 4-9-4z"/><path d="M3 7v6c0 1.5 4 3 9 3s9-1.5 9-3V7"/>',
backup:'<rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8"/><path d="M10 12h4"/>',
tasks:'<path d="M9 5h11M9 12h11M9 19h11"/><path d="M4 5l1 1 2-2M4 12l1 1 2-2M4 19l1 1 2-2"/>',
system:'<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2"/>',
admin:'<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-8 8-8s8 3.6 8 8"/>',
logout:'<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3"/><path d="M10 17l5-5-5-5"/><path d="M15 12H3"/>',
chevron:'<path d="M9 6l6 6-6 6"/>',
chevronD:'<path d="M6 9l6 6 6-6"/>',
menu:'<path d="M3 6h18M3 12h18M3 18h18"/>',
close:'<path d="M6 6l12 12M18 6L6 18"/>',
command:'<path d="M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z"/>',
bell:'<path d="M6 9a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6z"/><path d="M10 19a2 2 0 0 0 4 0"/>',
play:'<path d="M7 4l13 8-13 8z"/>',
plus:'<path d="M12 5v14M5 12h14"/>',
copy:'<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
code:'<path d="M16 18l6-6-6-6M8 6l-6 6 6 6"/>',
file:'<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
image:'<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/>',
video:'<rect x="3" y="6" width="14" height="12" rx="2"/><path d="M21 8l-4 4 4 4z"/>',
filter:'<path d="M3 5h18l-7 8v6l-4-2v-4z"/>',
sort:'<path d="M3 6h18M6 12h12M10 18h4"/>',
server:'<rect x="3" y="4" width="18" height="7" rx="1"/><rect x="3" y="13" width="18" height="7" rx="1"/><path d="M7 7.5h.01M7 16.5h.01"/>',
cloud:'<path d="M6 20a4 4 0 0 1 0-8 6 6 0 0 1 11.5-2A4.5 4.5 0 0 1 18 20z"/>',
key:'<circle cx="8" cy="8" r="4"/><path d="M11 11l9 9M16 16l2-2"/>',
warn:'<path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17h.01"/>',
check:'<path d="M5 12l5 5 9-9"/>',
x:'<path d="M6 6l12 12M18 6L6 18"/>',
arrowR:'<path d="M5 12h14M13 6l6 6-6 6"/>',
arrowUR:'<path d="M7 17L17 7M9 7h8v8"/>',
arrowD:'<path d="M12 5v14M6 13l6 6 6-6"/>',
up:'<path d="M12 19V5M6 11l6-6 6 6"/>',
down:'<path d="M12 5v14M6 13l6 6 6-6"/>',
scale:'<path d="M12 3v18M5 7h14M7 7l-3 6h6zM17 7l-3 6h6z"/>',
tag:'<path d="M3 7v5l9 9 5-5-9-9z"/><circle cx="7.5" cy="7.5" r="1"/>',
gauge:'<path d="M4 18a8 8 0 1 1 16 0"/><path d="M12 14l4-4"/>',
sparkle:'<path d="M12 3l2 5 5 2-5 2-2 5-2-5-5-2 5-2z"/>',
refresh:'<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 4v5h-5"/>',
doc:'<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M9 13h6M9 17h6"/>',
tree:'<rect x="2" y="9" width="6" height="6" rx="1"/><rect x="16" y="4" width="6" height="6" rx="1"/><rect x="16" y="14" width="6" height="6" rx="1"/><path d="M8 12h4V7h4M12 12v5h4"/>',
dot:'<circle cx="12" cy="12" r="3"/>',
pulse:'<path d="M3 12h4l3-7 4 14 3-7h4"/>',
copyR:'<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
};
function icon(name,cls=''){return `<svg class="ic ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[name]||''}</svg>`}
window.icon = icon;

/* —— Nav model —— */
const NAV = [
  {group:'概览',items:[
    {id:'dashboard',label:'总览',ic:'dashboard',href:'system.html'},
    {id:'tasks',label:'异步任务',ic:'tasks',href:'tasks.html',badge:'3'},
    {id:'system',label:'系统健康',ic:'system',href:'system.html'},
  ]},
  {group:'数据',items:[
    {id:'datasets',label:'数据集',ic:'database',href:'datasets.html'},
    {id:'ingest',label:'数据摄入',ic:'ingest',href:'ingest.html'},
    {id:'embeddings',label:'嵌入与索引',ic:'embed',href:'embeddings.html'},
  ]},
  {group:'智能',items:[
    {id:'search',label:'检索 Playground',ic:'search',href:'search.html'},
    {id:'rag',label:'RAG 问答',ic:'rag',href:'rag.html'},
    {id:'kg',label:'知识图谱',ic:'kg',href:'kg.html'},
    {id:'olap',label:'OLAP 分析',ic:'olap',href:'olap.html'},
  ]},
  {group:'治理',items:[
    {id:'lineage',label:'数据血缘',ic:'lineage',href:'lineage.html'},
    {id:'audit',label:'审计追踪',ic:'audit',href:'audit.html'},
    {id:'governance',label:'元数据治理',ic:'governance',href:'governance.html'},
    {id:'backup',label:'备份恢复',ic:'backup',href:'backup.html'},
  ]},
  {group:'管理',items:[
    {id:'admin',label:'用户与 RBAC',ic:'admin',href:'admin.html'},
  ]},
];

function renderShell({active,crumb}){
  const collapsed = localStorage.getItem('al-collapse')==='1';
  const navHtml = NAV.map(g=>`<div class="nav-group-title">${g.group}</div>`+g.items.map(it=>`
    <a class="nav-item ${it.id===active?'active':''}" href="${it.href}" data-nav="${it.id}">
      ${icon(it.ic)}<span class="nav-label">${it.label}</span>${it.badge?`<span class="nav-badge">${it.badge}</span>`:''}
    </a>`).join('')).join('');
  const sidebar = `
  <aside class="sidebar">
    <a class="brand" href="index.html">${icon('dashboard')}<span><div class="brand-name">Arrow Lake</div><div class="brand-sub">console v1.9.1</div></span></a>
    <nav class="nav">${navHtml}</nav>
    <div style="padding:var(--s3) var(--s4);border-top:1px solid var(--line-soft)">
      <div class="lamp ok pulse" style="margin-bottom:6px"><i></i>所有系统正常</div>
      <div class="muted" style="font-size:.625rem;font-family:var(--font-mono)">© 2026 · MIT</div>
    </div>
  </aside>`;
  const header = `
  <header class="header">
    <button class="btn btn-icon btn-ghost" id="navToggle" aria-label="折叠侧栏">${icon('menu')}</button>
    <div class="crumb">${crumb||'Console'}</div>
    <div class="h-spacer"></div>
    <div class="search-global" id="cmdk" role="button" tabindex="0" aria-label="命令面板">
      ${icon('search')}<input readonly placeholder="搜索数据集、跳转页面、运行 SQL…" /><kbd>⌘K</kbd>
    </div>
    <div class="deps" id="deps">
      <span class="dep" title="Ray 集群在线"><span class="lamp"></span>Ray</span>
      <span class="dep" title="HugeGraph 在线"><span class="lamp"></span>KG</span>
      <span class="dep warn" title="Gremlin 降级 → REST"><span class="lamp"></span>Gremlin</span>
      <span class="dep" title="MinIO 在线"><span class="lamp"></span>S3</span>
    </div>
    <button class="btn btn-icon btn-ghost" aria-label="通知" style="position:relative">${icon('bell')}<span style="position:absolute;top:4px;right:4px;width:7px;height:7px;border-radius:50%;background:var(--warn)"></span></button>
    <div class="userwrap" id="userWrap">
      <button class="user" id="userMenu" aria-haspopup="menu" aria-expanded="false" aria-label="账户菜单">
        <span class="avatar">SY</span><span class="meta"><b>sysop</b><span>ADMIN</span></span>${icon('chevronD','tiny')}
      </button>
      <div class="usermenu" id="userDrop" role="menu">
        <div class="usermenu-h"><span class="avatar" style="width:28px;height:28px;font-size:.7rem">SY</span><div><b>sysop</b><div class="muted mono" style="font-size:.65rem">sysop@arrow-lake · ADMIN</div></div></div>
        <a class="usermenu-item" href="my-workspace.html" role="menuitem">${icon('dashboard')}<span>我的工作区</span></a>
        <a class="usermenu-item" href="my-workspace.html#preferences" role="menuitem">${icon('gauge')}<span>偏好设置</span></a>
        <a class="usermenu-item" href="my-workspace.html#notifications" role="menuitem">${icon('bell')}<span>通知</span><span class="usermenu-badge">3</span></a>
        <div class="usermenu-sep"></div>
        <a class="usermenu-item" href="login.html" role="menuitem">${icon('logout')}<span>登出</span></a>
      </div>
    </div>
  </header>`;
  const root = $('#app'); if(!root) return;
  root.className = 'app'+(collapsed?' collapsed':'');
  root.insertAdjacentHTML('afterbegin', sidebar+header);
  // collapse toggle
  $('#navToggle')?.addEventListener('click',()=>{
    const c = document.body.closest('#app') || $('#app');
    c.classList.toggle('collapsed'); localStorage.setItem('al-collapse',c.classList.contains('collapsed')?'1':'0');
  });
  // [#v1.9.0] topbar avatar dropdown → My Workspace / preferences / notifications / logout
  if(!$('#alShellStyle')){document.head.insertAdjacentHTML('beforeend',`<style id="alShellStyle">
    .userwrap{position:relative}
    .userwrap .user{background:transparent;border:0;font:inherit;color:inherit}
    .user .ic.tiny{width:14px;height:14px;color:var(--fg-lo);margin-left:2px}
    .usermenu{position:absolute;top:calc(100% + 8px);right:0;min-width:252px;background:var(--ink-800);border:1px solid var(--line);border-radius:var(--r-lg);box-shadow:var(--shadow-3);padding:var(--s2);z-index:60;opacity:0;visibility:hidden;transform:translateY(-6px);transition:opacity var(--dur-2) var(--ease),transform var(--dur-2) var(--ease)}
    .usermenu.open{opacity:1;visibility:visible;transform:none}
    .usermenu-h{display:flex;gap:var(--s3);align-items:center;padding:var(--s2) var(--s3) var(--s3);border-bottom:1px solid var(--line-soft);margin-bottom:var(--s2)}
    .usermenu-item{display:flex;align-items:center;gap:var(--s3);padding:var(--s2) var(--s3);border-radius:var(--r-md);color:var(--fg-md);font-size:var(--fs-body);text-decoration:none;min-height:36px;font-weight:500}
    .usermenu-item:hover{background:var(--ink-750);color:var(--fg-hi);text-decoration:none}
    .usermenu-item .ic{color:var(--fg-lo)}
    .usermenu-item:hover .ic{color:var(--teal-bright)}
    .usermenu-badge{margin-left:auto;background:var(--amber-soft);color:var(--amber-bright);font-family:var(--font-mono);font-size:var(--fs-cap);padding:1px 8px;border-radius:99px;font-weight:600}
    .usermenu-sep{height:1px;background:var(--line-soft);margin:var(--s2) 0}
    @media(pointer:coarse){.usermenu-item{min-height:44px}}
    @media(max-width:860px){.usermenu{right:auto;left:0}}
  </style>`)}
  const um=$('#userMenu'),ud=$('#userDrop'),uw=$('#userWrap');
  if(um&&ud&&uw){
    um.addEventListener('click',e=>{e.stopPropagation();const o=ud.classList.toggle('open');um.setAttribute('aria-expanded',o?'true':'false')});
    document.addEventListener('click',e=>{if(ud.classList.contains('open')&&!uw.contains(e.target)){ud.classList.remove('open');um.setAttribute('aria-expanded','false')}});
    document.addEventListener('keydown',e=>{if(e.key==='Escape'&&ud.classList.contains('open')){ud.classList.remove('open');um.setAttribute('aria-expanded','false');um.focus()}});
  }
  // command palette (lightweight)
  $('#cmdk')?.addEventListener('click',openPalette);
  $('#cmdk')?.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openPalette()}});
  document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();openPalette()}});
}

function openPalette(){
  if($('#palette')) return;
  const items = NAV.flatMap(g=>g.items).map(i=>({label:i.label,href:i.href,ic:i.ic}));
  const el = document.createElement('div'); el.id='palette';
  el.innerHTML = `<div style="position:fixed;inset:0;background:rgba(5,8,14,.66);backdrop-filter:blur(4px);z-index:100;display:grid;place-items:start center;padding-top:12vh">
    <div class="panel" style="width:min(560px,92vw)"><div class="panel-h">${icon('command')}<h3>命令面板</h3><span class="actions"><button class="btn btn-icon btn-ghost" id="palClose">${icon('close')}</button></span></div>
    <div class="panel-b"><input class="input mono" id="palInput" placeholder="输入页面名或动作…" style="margin-bottom:var(--s3)"/>
    <div id="palList" style="display:flex;flex-direction:column;gap:2px"></div></div></div></div>`;
  document.body.appendChild(el);
  const list = $('#palList');
  const render = (q='')=>list.innerHTML = items.filter(i=>i.label.toLowerCase().includes(q.toLowerCase())).map((i,idx)=>`<a class="nav-item ${idx===0?'active':''}" href="${i.href}">${icon(i.ic)}<span class="nav-label">${i.label}</span>${icon('arrowR','nav-badge')}</a>`).join('');
  render();
  $('#palInput').focus();
  $('#palInput').addEventListener('input',e=>render(e.target.value));
  const close=()=>el.remove();
  $('#palClose').onclick=close;
  el.addEventListener('click',e=>{if(e.target===el)close()});
  document.addEventListener('keydown',function esc(e){if(e.key==='Escape'){close();document.removeEventListener('keydown',esc)}});
}

/* —— View API drawer (signature) —— */
function openApi(name,curl,py){
  const el = document.createElement('div'); el.id='apiDrawer';
  el.innerHTML=`<div style="position:fixed;inset:0;background:rgba(5,8,14,.5);z-index:90" id="apiMask"></div>
  <aside class="panel" style="position:fixed;top:0;right:0;bottom:0;width:min(560px,94vw);z-index:95;border-radius:0;display:flex;flex-direction:column;animation:slideIn var(--dur-3) var(--ease)">
    <div class="panel-h">${icon('code')}<div><h3>${name}</h3><div class="sub">等价 API 调用 · 透明可复现</div></div><span class="actions"><button class="btn btn-icon btn-ghost" id="apiClose">${icon('close')}</button></span></div>
    <div class="panel-b" style="overflow-y:auto;flex:1">
      <div class="muted" style="font-size:var(--fs-cap);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px">cURL</div>
      <pre class="codeblk" data-code>${escapeHtml(curl)}</pre>
      <div class="muted" style="font-size:var(--fs-cap);text-transform:uppercase;letter-spacing:.07em;margin:var(--s4) 0 6px">Python SDK</div>
      <pre class="codeblk" data-code>${escapeHtml(py)}</pre>
    </div>
    <style>@keyframes slideIn{from{transform:translateX(20px);opacity:.6}to{transform:none;opacity:1}}.codeblk{background:var(--ink-950);border:1px solid var(--line);border-radius:var(--r-md);padding:var(--s4);font-family:var(--font-mono);font-size:.75rem;color:var(--fg-md);overflow-x:auto;white-space:pre-wrap;line-height:1.6;margin:0}</style>
  </aside>`;
  document.body.appendChild(el);
  const close=()=>el.remove();
  $('#apiClose').onclick=close; $('#apiMask').onclick=close;
  $$('pre[data-code]',el).forEach(pre=>{pre.style.position='relative';const b=document.createElement('button');b.className='btn btn-sm btn-ghost';b.style.position='absolute';b.style.top='8px';b.style.right='8px';b.innerHTML=icon('copy')+' 复制';b.onclick=()=>{navigator.clipboard?.writeText(pre.textContent);b.innerHTML=icon('check')+' 已复制';setTimeout(()=>b.innerHTML=icon('copy')+' 复制',1500)};pre.appendChild(b)});
}
function escapeHtml(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
window.openApi=openApi;
window.renderShell=renderShell;

/* —— Chart helpers (pure SVG) —— */
function sparkline(vals,w=120,h=28,color='var(--teal-bright)'){const mx=Math.max(...vals),mn=Math.min(...vals),r=mx-mn||1;const pts=vals.map((v,i)=>`${(i/(vals.length-1))*w},${h-((v-mn)/r)*(h-4)-2}`).join(' ');const a=document.createElementNS('http://www.w3.org/2000/svg','svg');a.setAttribute('viewBox',`0 0 ${w} ${h}`);a.setAttribute('width',w);a.setAttribute('height',h);a.innerHTML=`<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><polyline points="0,${h} ${pts} ${w},${h}" fill="${color}" opacity=".12"/>`;return a.outerHTML}
window.sparkline=sparkline;

function areachart(vals,w=600,h=140,color='var(--teal-bright)'){const mx=Math.max(...vals),mn=Math.min(...vals),r=mx-mn||1;const x=i=>(i/(vals.length-1))*w;const y=v=>h-((v-mn)/r)*(h-12)-6;const line=vals.map((v,i)=>`${x(i)},${y(v)}`).join(' ');const area=`0,${h} ${line} ${w},${h}`;let grid='';for(let i=1;i<4;i++){const gy=(h/4)*i;grid+=`<line x1="0" y1="${gy}" x2="${w}" y2="${gy}" stroke="var(--line-soft)" stroke-width="1"/>`}return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" preserveAspectRatio="none"><defs><linearGradient id="ag" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${color}" stop-opacity=".35"/><stop offset="100%" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>${grid}<polygon points="${area}" fill="url(#ag)"/><polyline points="${line}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`}
window.areachart=areachart;

function donut(val,label='%',w=120,h=120,color='var(--teal-bright)'){const r=46,c=2*Math.PI*r,off=c*(1-val/100);return `<svg viewBox="0 0 120 120" width="${w}" height="${h}"><circle cx="60" cy="60" r="${r}" fill="none" stroke="var(--ink-700)" stroke-width="10"/><circle cx="60" cy="60" r="${r}" fill="none" stroke="${color}" stroke-width="10" stroke-linecap="round" stroke-dasharray="${c}" stroke-dashoffset="${off}" transform="rotate(-90 60 60)"/><text x="60" y="58" text-anchor="middle" font-family="var(--font-mono)" font-size="20" fill="var(--fg-hi)" font-weight="600">${val}</text><text x="60" y="76" text-anchor="middle" font-family="var(--font-mono)" font-size="10" fill="var(--fg-lo)">${label}</text></svg>`}
window.donut=donut;

window.AL={$,$$};

/* —— Auto-replace static <i data-ic="name"></i> with icon SVG.
   Use this in static HTML instead of template literals. Trusted constants only. —— */
function applyIcons(root=document){
  root.querySelectorAll('i[data-ic]').forEach(el=>{
    const s=icon(el.dataset.ic, el.className.replace('placeholder',''));
    if(s) el.outerHTML=s;
  });
}
window.applyIcons=applyIcons;
document.addEventListener('DOMContentLoaded',()=>applyIcons());
})();
