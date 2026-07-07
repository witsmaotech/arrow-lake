// Token management: login (X-API-Key in BOTH mode) / refresh / logout
const K_ACCESS = "al_access";
const K_REFRESH = "al_refresh";

// Dev (5180) → API on 8000; Prod (same origin /console) → relative /api/v1
function isDev() {
  const o = location.origin;
  return o.startsWith("http://localhost:5180") || o.startsWith("http://127.0.0.1:5180");
}
export const API_BASE = isDev() ? "http://127.0.0.1:8000/api/v1" : "/api/v1";

export function getAccessToken() { return localStorage.getItem(K_ACCESS); }
export function getRefreshToken() { return localStorage.getItem(K_REFRESH); }
export function setTokens(access, refresh) {
  if (access) localStorage.setItem(K_ACCESS, access);
  if (refresh) localStorage.setItem(K_REFRESH, refresh);
}
export function clearTokens() {
  localStorage.removeItem(K_ACCESS);
  localStorage.removeItem(K_REFRESH);
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
  return tok;
}

export async function logout() {
  try {
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      headers: { Authorization: `Bearer ${getAccessToken()}` },
    });
  } catch (_) {}
  clearTokens();
}
