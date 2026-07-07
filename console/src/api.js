// API client: Bearer injection + 401 auto-refresh + error normalization
import { getAccessToken, getRefreshToken, setTokens, clearTokens, API_BASE } from "./auth.js";

export { API_BASE };

export class ApiError extends Error {
  constructor(status, detail, body) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
    this.body = body;
  }
}

let refreshing = null;
async function doRefresh() {
  const rt = getRefreshToken();
  if (!rt) throw new ApiError(401, "无 refresh token,请重新登录");
  const r = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { Authorization: `Bearer ${rt}`, "Content-Type": "application/json" },
  });
  if (!r.ok) throw new ApiError(r.status, "token 刷新失败");
  const tok = await r.json();
  setTokens(tok.access_token, tok.refresh_token);
  return tok.access_token;
}

export async function request(method, path, { body, headers, signal } = {}) {
  const tok = getAccessToken();
  const h = { "Content-Type": "application/json", ...(headers || {}) };
  if (tok) h["Authorization"] = `Bearer ${tok}`;
  const opt = { method, headers: h, signal };
  if (body !== undefined) opt.body = JSON.stringify(body);

  let r;
  try {
    r = await fetch(`${API_BASE}${path}`, opt);
  } catch (e) {
    throw new ApiError(0, e.message || "网络错误(后端不可达?)");
  }

  // 401 → refresh once, retry
  if (r.status === 401 && getRefreshToken() && !path.startsWith("/auth/")) {
    try {
      refreshing = refreshing || doRefresh();
      await refreshing;
      refreshing = null;
    } catch (e) {
      clearTokens();
      throw e;
    }
    const tok2 = getAccessToken();
    const h2 = { "Content-Type": "application/json", ...(headers || {}) };
    if (tok2) h2["Authorization"] = `Bearer ${tok2}`;
    const opt2 = { method, headers: h2, signal };
    if (body !== undefined) opt2.body = JSON.stringify(body);
    r = await fetch(`${API_BASE}${path}`, opt2);
  }

  if (!r.ok) {
    let detail = r.statusText, b = null;
    try { b = await r.json(); detail = b.detail || b.message || detail; } catch (_) {}
    throw new ApiError(r.status, detail, b);
  }
  return r.json();
}
