// CodeMirror 6 editor with lazily loaded languages and theme colors from CSS variables.
import { useEffect, useRef } from 'react';
import { EditorState, Compartment, type Extension } from '@codemirror/state';
import { EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, drawSelection, highlightSpecialChars, dropCursor, rectangularSelection } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands';
import { searchKeymap, highlightSelectionMatches, search } from '@codemirror/search';
import { syntaxHighlighting, HighlightStyle, indentOnInput, bracketMatching, foldGutter, foldKeymap, StreamLanguage, indentUnit, type StreamParser } from '@codemirror/language';
import { closeBrackets, closeBracketsKeymap } from '@codemirror/autocomplete';
import { tags as t } from '@lezer/highlight';
import { langFromPath } from '../../lib/format';

const style = HighlightStyle.define([
  { tag: [t.comment, t.lineComment, t.blockComment, t.docComment], color: 'var(--cm-comment)', fontStyle: 'italic' },
  { tag: [t.keyword, t.controlKeyword, t.operatorKeyword, t.definitionKeyword, t.moduleKeyword, t.self, t.modifier], color: 'var(--cm-keyword)' },
  { tag: [t.string, t.special(t.string), t.docString, t.character], color: 'var(--cm-string)' },
  { tag: [t.number, t.bool, t.null, t.atom, t.unit], color: 'var(--cm-number)' },
  { tag: [t.function(t.variableName), t.function(t.propertyName), t.macroName], color: 'var(--cm-function)' },
  { tag: [t.typeName, t.className, t.namespace, t.standard(t.typeName)], color: 'var(--cm-type)' },
  { tag: [t.propertyName, t.attributeName], color: 'var(--cm-property)' },
  { tag: [t.tagName, t.angleBracket], color: 'var(--cm-tag)' },
  { tag: [t.regexp, t.escape], color: 'var(--cm-regex)' },
  { tag: [t.operator, t.punctuation, t.separator, t.bracket], color: 'var(--cm-punct)' },
  { tag: [t.heading], color: 'var(--cm-function)', fontWeight: '600' },
  { tag: [t.strong], fontWeight: '600' },
  { tag: [t.emphasis], fontStyle: 'italic' },
  { tag: [t.link, t.url], color: 'var(--info)', textDecoration: 'underline' },
  { tag: [t.inserted], color: 'var(--ok)' },
  { tag: [t.deleted], color: 'var(--err)' },
  { tag: [t.meta, t.processingInstruction], color: 'var(--cm-number)' },
  { tag: [t.invalid], color: 'var(--err)' },
]);

const theme = EditorView.theme({
  '&': { height: '100%', fontSize: '12.5px', backgroundColor: 'transparent', color: 'var(--text)' },
  '.cm-scroller': { fontFamily: 'var(--font-mono)', lineHeight: '1.6' },
  '.cm-content': { caretColor: 'var(--accent)' },
  '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--accent)', borderLeftWidth: '2px' },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection': { backgroundColor: 'var(--selection) !important' },
  '.cm-gutters': { backgroundColor: 'var(--bg-2)', color: 'var(--text-3)', border: 'none', borderRight: '1px solid var(--line)' },
  '.cm-activeLine': { backgroundColor: 'color-mix(in srgb, var(--bg-3) 70%, transparent)' },
  '.cm-activeLineGutter': { backgroundColor: 'var(--bg-3)', color: 'var(--text-2)' },
  '.cm-searchMatch': { backgroundColor: 'var(--accent-wash)', outline: '1px solid var(--accent-line)' },
  '.cm-selectionMatch': { backgroundColor: 'var(--info-wash)' },
  '.cm-panels': { backgroundColor: 'var(--bg-2)', color: 'var(--text)', borderTop: '1px solid var(--line)' },
  '.cm-panels input, .cm-panels button': { fontFamily: 'var(--font-ui)', fontSize: '12px' },
  '.cm-foldPlaceholder': { backgroundColor: 'var(--bg-3)', border: '1px solid var(--line-2)', color: 'var(--text-2)' },
  '.cm-line.cm-flash': { backgroundColor: 'var(--accent-wash)' },
});

async function languageFor(path: string): Promise<Extension | null> {
  const lang = langFromPath(path);
  const legacy = async (loader: () => Promise<unknown>, key: string) => StreamLanguage.define(((await loader()) as Record<string, StreamParser<unknown>>)[key]);
  switch (lang) {
    case 'javascript':
    case 'jsx':
    case 'typescript':
    case 'tsx': {
      const m = await import('@codemirror/lang-javascript');
      return m.javascript({ jsx: lang === 'jsx' || lang === 'tsx', typescript: lang === 'typescript' || lang === 'tsx' });
    }
    case 'json':
    case 'jsonc':
      return (await import('@codemirror/lang-json')).json();
    case 'python':
      return (await import('@codemirror/lang-python')).python();
    case 'markdown':
      return (await import('@codemirror/lang-markdown')).markdown();
    case 'css':
    case 'scss':
    case 'less':
      return (await import('@codemirror/lang-css')).css();
    case 'html':
    case 'vue':
    case 'svelte':
      return (await import('@codemirror/lang-html')).html();
    case 'c':
    case 'cpp':
      return (await import('@codemirror/lang-cpp')).cpp();
    case 'rust':
      return (await import('@codemirror/lang-rust')).rust();
    case 'java':
      return (await import('@codemirror/lang-java')).java();
    case 'go':
      return (await import('@codemirror/lang-go')).go();
    case 'sql':
      return (await import('@codemirror/lang-sql')).sql();
    case 'yaml':
      return (await import('@codemirror/lang-yaml')).yaml();
    case 'xml':
      return (await import('@codemirror/lang-xml')).xml();
    case 'verilog':
    case 'system-verilog':
      return legacy(() => import('@codemirror/legacy-modes/mode/verilog'), 'verilog');
    case 'vhdl':
      return legacy(() => import('@codemirror/legacy-modes/mode/vhdl'), 'vhdl');
    case 'tcl':
      return legacy(() => import('@codemirror/legacy-modes/mode/tcl'), 'tcl');
    case 'perl':
      return legacy(() => import('@codemirror/legacy-modes/mode/perl'), 'perl');
    case 'bash':
    case 'fish':
      return legacy(() => import('@codemirror/legacy-modes/mode/shell'), 'shell');
    case 'toml':
      return legacy(() => import('@codemirror/legacy-modes/mode/toml'), 'toml');
    case 'ini':
      return legacy(() => import('@codemirror/legacy-modes/mode/properties'), 'properties');
    case 'dockerfile':
      return legacy(() => import('@codemirror/legacy-modes/mode/dockerfile'), 'dockerFile');
    case 'cmake':
      return legacy(() => import('@codemirror/legacy-modes/mode/cmake'), 'cmake');
    case 'lua':
      return legacy(() => import('@codemirror/legacy-modes/mode/lua'), 'lua');
    case 'ruby':
      return legacy(() => import('@codemirror/legacy-modes/mode/ruby'), 'ruby');
    case 'r':
      return legacy(() => import('@codemirror/legacy-modes/mode/r'), 'r');
    case 'swift':
      return legacy(() => import('@codemirror/legacy-modes/mode/swift'), 'swift');
    case 'kotlin':
      return legacy(() => import('@codemirror/legacy-modes/mode/clike'), 'kotlin');
    case 'csharp':
      return legacy(() => import('@codemirror/legacy-modes/mode/clike'), 'csharp');
    case 'scala':
      return legacy(() => import('@codemirror/legacy-modes/mode/clike'), 'scala');
    case 'diff':
      return legacy(() => import('@codemirror/legacy-modes/mode/diff'), 'diff');
    case 'powershell':
      return legacy(() => import('@codemirror/legacy-modes/mode/powershell'), 'powerShell');
    case 'proto':
      return legacy(() => import('@codemirror/legacy-modes/mode/protobuf'), 'protobuf');
    case 'haskell':
      return legacy(() => import('@codemirror/legacy-modes/mode/haskell'), 'haskell');
    case 'julia':
      return legacy(() => import('@codemirror/legacy-modes/mode/julia'), 'julia');
    case 'fortran-free-form':
    case 'fortran-fixed-form':
      return legacy(() => import('@codemirror/legacy-modes/mode/fortran'), 'fortran');
    case 'matlab':
      return legacy(() => import('@codemirror/legacy-modes/mode/octave'), 'octave');
    default:
      return null;
  }
}

export function Editor({ path, doc, onChange, onSave, line, nonce }: { path: string; doc: string; onChange: (text: string) => void; onSave: () => void; line?: number; nonce?: number }) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const langComp = useRef(new Compartment());
  const cbs = useRef({ onChange, onSave });
  cbs.current = { onChange, onSave };

  useEffect(() => {
    const state = EditorState.create({
      doc,
      extensions: [
        lineNumbers(),
        foldGutter(),
        highlightSpecialChars(),
        history(),
        drawSelection(),
        dropCursor(),
        rectangularSelection(),
        indentOnInput(),
        bracketMatching(),
        closeBrackets(),
        highlightActiveLine(),
        highlightActiveLineGutter(),
        highlightSelectionMatches(),
        search({ top: true }),
        indentUnit.of('  '),
        syntaxHighlighting(style),
        theme,
        langComp.current.of([]),
        keymap.of([{ key: 'Mod-s', preventDefault: true, run: () => (cbs.current.onSave(), true) }, ...closeBracketsKeymap, ...defaultKeymap, ...searchKeymap, ...historyKeymap, ...foldKeymap, indentWithTab]),
        EditorView.updateListener.of((u) => {
          if (u.docChanged) cbs.current.onChange(u.state.doc.toString());
        }),
      ],
    });
    const v = new EditorView({ state, parent: host.current! });
    view.current = v;
    let alive = true;
    languageFor(path).then((ext) => {
      if (alive && ext) v.dispatch({ effects: langComp.current.reconfigure(ext) });
    });
    return () => {
      alive = false;
      v.destroy();
      view.current = null;
    };
  }, [path]); // eslint-disable-line react-hooks/exhaustive-deps

  // External reloads replace the document.
  useEffect(() => {
    const v = view.current;
    if (!v) return;
    if (v.state.doc.toString() !== doc) v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: doc } });
  }, [doc]);

  useEffect(() => {
    const v = view.current;
    if (!v || !line) return;
    const ln = Math.min(Math.max(1, line), v.state.doc.lines);
    const pos = v.state.doc.line(ln).from;
    v.dispatch({ selection: { anchor: pos }, effects: EditorView.scrollIntoView(pos, { y: 'center' }) });
    v.focus();
  }, [line, nonce]);

  return <div className="editor" ref={host} />;
}
