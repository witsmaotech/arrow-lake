// In-page confirm modal — replaces native confirm() so the prompt lives inside
// the interface (dark card, styled) instead of an OS/browser popup.
// Usage:  if (!(await confirmDialog({title, message, danger, confirmText}))) return;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export function confirmDialog({
  title = "确认操作",
  message = "",
  confirmText = "确认",
  cancelText = "取消",
  danger = false,
} = {}) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.innerHTML = `
      <div class="modal-card ${danger ? "modal-danger" : ""}" role="dialog" aria-modal="true">
        <div class="modal-title">${esc(title)}</div>
        ${message ? `<div class="modal-msg">${esc(message)}</div>` : ""}
        <div class="modal-actions">
          <button class="btn btn-ghost btn-sm" data-act="cancel">${esc(cancelText)}</button>
          <button class="btn btn-primary btn-sm" data-act="confirm">${esc(confirmText)}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add("show"));
    const close = (val) => {
      overlay.classList.remove("show");
      document.removeEventListener("keydown", onKey);
      setTimeout(() => overlay.remove(), 180);
      resolve(val);
    };
    const onKey = (e) => {
      if (e.key === "Escape") close(false);
      else if (e.key === "Enter") close(true);
    };
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) return close(false); // backdrop click → cancel
      const act = e.target.closest("[data-act]")?.dataset.act;
      if (act === "confirm") close(true);
      else if (act === "cancel") close(false);
    });
    document.addEventListener("keydown", onKey);
    setTimeout(() => overlay.querySelector('[data-act="confirm"]')?.focus(), 60);
  });
}
