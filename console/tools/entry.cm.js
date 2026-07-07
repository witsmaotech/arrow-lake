// CodeMirror 6 vendor entry — esbuild bundles this to vendor/codemirror.bundle.js (IIFE).
// Exposes window.CM with the APIs used by src/olap/editor.js. Rebuild only on upgrade.
import { EditorView, keymap, lineNumbers, drawSelection, highlightActiveLine } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { syntaxHighlighting, defaultHighlightStyle, bracketMatching } from "@codemirror/language";
import { autocompletion, completionKeymap, closeBrackets, closeBracketsKeymap } from "@codemirror/autocomplete";
import { sql } from "@codemirror/lang-sql";

window.CM = {
  EditorView, keymap, lineNumbers, drawSelection, highlightActiveLine,
  defaultKeymap, history, historyKeymap, indentWithTab,
  syntaxHighlighting, defaultHighlightStyle, bracketMatching,
  autocompletion, completionKeymap, closeBrackets, closeBracketsKeymap,
  sql,
};
