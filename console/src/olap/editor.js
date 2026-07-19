// SQL editor: CodeMirror 6 (本地 vendor,window.CM)+ textarea fallback。
// CM 提供 SQL 语法高亮 + 括号匹配 + 关键字/函数补全 + 行号。
// 无 window.CM(bundle 未加载)时降级 textarea,功能仍可用。
const CM_THEME = {
  "&": { color: "var(--fg-hi)", backgroundColor: "var(--ink-950)", height: "100%", fontSize: ".8125rem", fontFamily: "var(--font-mono)" },
  ".cm-content": { caretColor: "var(--teal-bright)", padding: "6px 0" },
  ".cm-gutters": { backgroundColor: "var(--ink-900)", color: "var(--fg-lo)", border: "none", borderRight: "1px solid var(--line-soft)" },
  ".cm-activeLine": { backgroundColor: "rgba(20,184,166,.06)" },
  ".cm-activeLineGutter": { backgroundColor: "rgba(20,184,166,.06)" },
  "&.cm-focused .cm-selectionBackground, ::selection, .cm-selectionBackground": { backgroundColor: "rgba(20,184,166,.22)" },
  ".cm-cursor": { borderLeftColor: "var(--teal-bright)" },
  ".cm-tooltip": { background: "var(--ink-700)", border: "1px solid var(--line)", color: "var(--fg-hi)", borderRadius: "6px", fontFamily: "var(--font-mono)", fontSize: ".75rem" },
  ".cm-tooltip-autocomplete > ul > li[aria-selected]": { backgroundColor: "rgba(20,184,166,.25)", color: "var(--fg-hi)" },
};

export function createEditor(mount, { onRun, initial = "" } = {}) {
  const CM = window.CM;
  if (CM && CM.EditorView) return cmEditor(mount, CM, { onRun, initial });
  return textareaEditor(mount, { onRun, initial });
}

function cmEditor(mount, CM, { onRun, initial }) {
  mount.classList.add("editor-cm");
  const view = new CM.EditorView({
    doc: initial,
    extensions: [
      CM.lineNumbers(),
      CM.history(),
      CM.drawSelection(),
      CM.syntaxHighlighting(CM.defaultHighlightStyle, { fallback: true }),
      CM.bracketMatching(),
      CM.closeBrackets(),
      CM.autocompletion(),
      CM.highlightActiveLine(),
      CM.sql({ upperCaseKeywords: true }),
      CM.keymap.of([
        ...CM.defaultKeymap, ...CM.historyKeymap,
        ...CM.closeBracketsKeymap, ...CM.completionKeymap, CM.indentWithTab,
        { key: "Mod-Enter", run: () => { onRun && onRun(); return true; }, preventDefault: true },
      ]),
      CM.EditorView.theme(CM_THEME),
      CM.EditorView.lineWrapping,
    ],
    parent: mount,
  });
  return {
    get value() { return view.state.doc.toString(); },
    set value(v) { view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: v } }); view.focus(); },
    insert(text) { const h = view.state.selection.main.head; view.dispatch({ changes: { from: h, insert: text }, selection: { anchor: h + text.length } }); view.focus(); },
    focus() { view.focus(); },
  };
}

// Fallback: 零依赖 textarea + 行号(CM bundle 未加载时)
function textareaEditor(mount, { onRun, initial }) {
  mount.classList.add("editor");
  mount.innerHTML = `
    <div class="editor-gutter" aria-hidden="true"></div>
    <textarea class="editor-ta" spellcheck="false" autocomplete="off" autocapitalize="off"></textarea>`;
  const ta = mount.querySelector(".editor-ta");
  const gutter = mount.querySelector(".editor-gutter");
  ta.value = initial;
  const updateGutter = () => {
    const lines = Math.max(ta.value.split("\n").length, 1);
    gutter.innerHTML = Array.from({ length: lines }, (_, i) => `<div>${i + 1}</div>`).join("");
  };
  ta.addEventListener("input", updateGutter);
  ta.addEventListener("scroll", () => { gutter.scrollTop = ta.scrollTop; });
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
    insert(text) { const s = ta.selectionStart, en = ta.selectionEnd; ta.value = ta.value.slice(0, s) + text + ta.value.slice(en); ta.selectionStart = ta.selectionEnd = s + text.length; updateGutter(); ta.focus(); },
    focus() { ta.focus(); },
  };
}
