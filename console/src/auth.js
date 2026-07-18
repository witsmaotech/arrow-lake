// Token + API Key management.
// auth_mode=BOTH 注册 api_key middleware(查 X-API-Key)和 jwt middleware(查 Authorization Bearer)。
// 前端须同时带两个 header 才能通过两层。login 时存 API Key,api.js 每个请求带上。
const K_ACCESS = "al_access";
const K_REFRESH = "al_refresh";
const K_APIKEY = "al_apikey";

function isDev() {
  const o = location.origin;
  // dev: 5180 (prototype) / 5189 (console) on localhost or 127.0.0.1
  return o.startsWith("http://localhost:5180") || o.startsWith("http://127.0.0.1:5180")
      || o.startsWith("http://localhost:5189") || o.startsWith("http://127.0.0.1:5189");
}
export const API_BASE = isDev() ? "http://127.0.0.1:8000/api/v1" : "/api/v1";

export function getAccessToken() { return localStorage.getItem(K_ACCESS); }
export function getRefreshToken() { return localStorage.getItem(K_REFRESH); }
export function getApiKey() { return localStorage.getItem(K_APIKEY); }
export function setTokens(access, refresh) {
  if (access) localStorage.setItem(K_ACCESS, access);
  if (refresh) localStorage.setItem(K_REFRESH, refresh);
}
export function clearTokens() {
  localStorage.removeItem(K_ACCESS);
  localStorage.removeItem(K_REFRESH);
  localStorage.removeItem(K_APIKEY);
}
export function isLoggedIn() { return !!getAccessToken(); }

// Login: auth_mode=BOTH requires X-API-Key header
export async function login(apiKey) {
  const r = await fetch(`${API_BASE}/auth/token`, {
    method: "POST",
    headers: { "X-API-Key": apiKey, "Content-Type": "application/json" },
  });
  if (!r.ok) {
    let d = "登录失败";
    try { const j = await r.json(); d = j.detail || d; } catch (_) {}
    throw new Error(`${r.status}: ${d}`);
  }
  const tok = await r.json();
  setTokens(tok.access_token, tok.refresh_token);
  localStorage.setItem(K_APIKEY, apiKey); // 后续请求也带 X-API-Key(BOTH 双层 auth)
  return tok;
}

// v1.9.1: username + password → /auth/login(libSQL IdentityStore pbkdf2 验证)
export async function loginWithPassword(username, password) {
  const r = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!r.ok) {
    let d = "登录失败";
    try { const j = await r.json(); d = j.detail || d; } catch (_) {}
    throw new Error(`${r.status}: ${d}`);
  }
  const tok = await r.json();
  setTokens(tok.access_token, tok.refresh_token);
  localStorage.removeItem(K_APIKEY); // 密码登录无 api_key;后续请求只带 Bearer(jwt 层)
  return tok;
}

export async function logout() {
  try {
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      headers: { Authorization: `Bearer ${getAccessToken()}`, "X-API-Key": getApiKey() || "" },
    });
  } catch (_) {}
  clearTokens();
}
