// API client: 双层 auth header(BOTH 模式)+ 401 auto-refresh + 错误归一
import { getAccessToken, getRefreshToken, setTokens, clearTokens, getApiKey, API_BASE } from "./auth.js";

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
let lastRefresh = 0;
const REFRESH_COOLDOWN = 5000;
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

// 供流式端点(裸 fetch,不走 request() 的 401 自动刷新)在 401 时调用:单飞 + 冷却
export async function refreshToken() {
  if (Date.now() - lastRefresh < REFRESH_COOLDOWN) return getAccessToken();
  refreshing = refreshing || doRefresh();
  try { const t = await refreshing; lastRefresh = Date.now(); return t; }
  finally { refreshing = null; }
}

// BOTH 模式:同时带 Authorization Bearer(jwt 层)和 X-API-Key(api_key 层)
function withAuth(headers) {
  const h = { "Content-Type": "application/json", ...(headers || {}) };
  const tok = getAccessToken();
  if (tok) h["Authorization"] = `Bearer ${tok}`;
  const ak = getApiKey();
  if (ak) h["X-API-Key"] = ak;
  return h;
}

export async function request(method, path, { body, headers, signal } = {}) {
  const opt = { method, headers: withAuth(headers), signal };
  if (body !== undefined) opt.body = JSON.stringify(body);

  let r;
  try {
    r = await fetch(`${API_BASE}${path}`, opt);
  } catch (e) {
    throw new ApiError(0, e.message || "网络错误(后端不可达?)");
  }

  // 401 → refresh once, retry(cooldown 防并发重试期间重复 refresh)
  if (r.status === 401 && getRefreshToken() && !path.startsWith("/auth/") && Date.now() - lastRefresh > REFRESH_COOLDOWN) {
    try {
      refreshing = refreshing || doRefresh();
      await refreshing;
      refreshing = null;
      lastRefresh = Date.now();
    } catch (e) {
      clearTokens();
      throw e;
    }
    const opt2 = { method, headers: withAuth(headers), signal };
    if (body !== undefined) opt2.body = JSON.stringify(body);
    r = await fetch(`${API_BASE}${path}`, opt2);
    if (r.status === 401) { clearTokens(); throw new ApiError(401, "刷新后仍 401,请重新登录"); }
  }

  if (!r.ok) {
    let detail = r.statusText, b = null;
    try { b = await r.json(); detail = b.detail || b.message || detail; } catch (_) {}
    throw new ApiError(r.status, detail, b);
  }
  return r.json();
}
