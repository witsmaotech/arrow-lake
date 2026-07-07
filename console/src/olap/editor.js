// MVP SQL editor: textarea + line-number gutter. Zero deps, offline-capable.
// CodeMirror 6 upgrade listed as enhancement (see design doc ADR-4 / Phase 4).
export function createEditor(mount, { onRun, initial = "" } = {}) {
  mount.classList.add("editor");
  mount.innerHTML = `
    <div class="editor-gutter" aria-hidden="true"></div>
    <textarea class="editor-ta" spellcheck="false" autocomplete="off" autocapitalize="off"></textarea>`;
  const ta = mount.querySelector(".editor-ta");
  const gutter = mount.querySelector(".editor-gutter");
  ta.value = initial;

  const lineHeight = () => parseFloat(getComputedStyle(ta).lineHeight) || 22;

  function updateGutter() {
    const lines = Math.max(ta.value.split("\n").length, 1);
    gutter.innerHTML = Array.from({ length: lines }, (_, i) => `<div>${i + 1}</div>`).join("");
  }
  function sync() { gutter.scrollTop = ta.scrollTop; }

  ta.addEventListener("input", updateGutter);
  ta.addEventListener("scroll", sync);
  ta.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); onRun && onRun(); }
    if (e.key === "Tab") {
      e.preventDefault();
      const s = ta.selectionStart, en = ta.selectionEnd;
      ta.value = ta.value.slice(0, s) + "  " + ta.value.slice(en);
      ta.selectionStart = ta.selectionEnd = s + 2;
      updateGutter();
    }
  });
  updateGutter();

  return {
    get value() { return ta.value; },
    set value(v) { ta.value = v; updateGutter(); },
    focus() { ta.focus(); },
  };
}
