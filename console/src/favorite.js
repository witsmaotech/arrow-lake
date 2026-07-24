// 各页「收藏查询」入口:HTML 加 <button class="btn btn-ghost btn-sm" data-fav="search|rag">收藏</button>,
// module 加 `import "./src/favorite.js"`(副作用自动绑 data-fav)。复用 my-workspace 的 localStorage al_ptoken。
import { API_BASE } from "./api.js";
import { toast } from "./ui/toast.js";

function getPtoken() { return localStorage.getItem("al_ptoken") || ""; }

export async function saveQuery({ query_type, query_text, dataset = null, name }) {
  const pt = getPtoken();
  if (!pt) { toast("请先到「我的工作区」配置 personal token(管理后台发 token 后粘贴)", "warn", 5000); return false; }
  if (!query_text || !query_text.trim()) { toast("查询内容为空", "warn"); return false; }
  try {
    const r = await fetch(`${API_BASE}/me/saved-queries`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": pt },
      body: JSON.stringify({ name: name || query_text.slice(0, 60), query_text, query_type, dataset, is_public: false }),
    });
    if (!r.ok) { let d; try { d = (await r.json()).detail; } catch (_) {} toast("收藏失败:" + (d || r.statusText), "danger"); return false; }
    toast("已收藏到「我的工作区」", "ok"); return true;
  } catch { toast("收藏失败(后端不可达)", "danger"); return false; }
}

function bind() {
  document.querySelectorAll("[data-fav]").forEach((btn) => {
    if (btn.dataset.favBound) return;
    btn.dataset.favBound = "1";
    btn.addEventListener("click", () => {
      const type = btn.dataset.fav;
      const $ = (s) => document.querySelector(s);
      const qt = type === "rag" ? ($("#question")?.value || "")
        : type === "sql" ? (window.__olapGetSql?.() || "")
        : ($("#query")?.value || "");
      saveQuery({ query_type: type, query_text: qt, dataset: $("#ds")?.value || $("#dsSel")?.value || null });
    });
  });
}
bind();
document.addEventListener("DOMContentLoaded", bind);
