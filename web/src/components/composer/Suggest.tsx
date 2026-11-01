import { useId, useMemo, useState, type RefObject } from 'react';
import { File as FileIcon, Folder, FolderGit2 } from 'lucide-react';
import { useStore, useFileIndex } from '../../lib/store';
import { fuzzyFilter } from '../../lib/fuzzy';
import { tildify } from '../../lib/format';
import type { SlashCommandInfo } from '../../lib/types';
import { Leave } from '../ui/bits';

const BUILTIN_COMMANDS: SlashCommandInfo[] = [
  { name: 'compact', description: 'Summarize the conversation to free up context' },
  { name: 'clear', description: 'Start the conversation over' },
  { name: 'context', description: 'Show what is using the context window' },
  { name: 'usage', description: 'Show cost and token usage' },
  { name: 'init', description: 'Write a CLAUDE.md for this project' },
  { name: 'code-review', description: 'Review the current changes' },
  { name: 'model', description: 'Change the model' },
];

type Popup = { kind: 'mention' | 'slash'; query: string; start: number; index: number } | null;
interface Suggestion {
  key: string;
  label: string;
  hint: string;
  kind: 'cmd' | 'dir' | 'file' | 'repo';
}

/**
 * The list that opens under the caret: `/` at the start of the message for commands, `@` for files,
 * folders and registered repositories. The field keeps the focus; the list is its listbox, and the
 * highlighted row is the field's active descendant, so a screen reader follows the arrows.
 */
export function useSuggest({ sessionId, cwd, text, setText, field }: { sessionId: string; cwd: string; text: string; setText: (t: string) => void; field: RefObject<HTMLTextAreaElement | null> }) {
  const listed = useStore((s) => s.rt[sessionId]?.commands);
  const slashNames = useStore((s) => s.rt[sessionId]?.init?.slashCommands);
  const repos = useStore((s) => s.config?.repos);
  const home = useStore((s) => s.info?.home);
  const [popup, setPopup] = useState<Popup>(null);
  // A folder that cannot be listed still offers the registered repositories.
  const files = useFileIndex(cwd, popup?.kind === 'mention');
  const listId = useId();

  const commands: SlashCommandInfo[] = useMemo(() => {
    if (listed?.length) return listed;
    if (slashNames?.length) return slashNames.map((n) => BUILTIN_COMMANDS.find((b) => b.name === n) || { name: n, description: '' });
    return BUILTIN_COMMANDS;
  }, [listed, slashNames]);

  const suggestions: Suggestion[] = useMemo(() => {
    if (!popup) return [];
    if (popup.kind === 'slash') return fuzzyFilter(popup.query, commands, (c) => c.name, 40).map((m) => ({ key: m.item.name, label: '/' + m.item.name, hint: m.item.argumentHint ? `${m.item.argumentHint} — ${m.item.description}` : m.item.description, kind: 'cmd' }));
    const q = popup.query;
    const list = q ? fuzzyFilter(q, files, (f) => f, 40).map((m) => m.item) : files.filter((f) => !f.includes('/') || f.split('/').length <= 2).slice(0, 40);
    const found = list.map((f): Suggestion => ({ key: f, label: f, hint: '', kind: f.endsWith('/') ? 'dir' : 'file' }));
    // registered repositories (REPOSITORIES.md) answer to their names and the person's aliases
    const norm = (s: string) => s.toLowerCase().replace(/[\s_-]+/g, '');
    const nq = norm(q);
    const named = (repos || [])
      .filter((e) => e.name && (!nq || [e.name, ...(e.aliases || [])].some((w) => norm(w).includes(nq))))
      .slice(0, 8)
      .map((e): Suggestion => ({ key: e.name, label: e.name, hint: e.about || tildify(e.path, home), kind: 'repo' }));
    return [...named, ...found];
  }, [popup, commands, files, repos, home]);

  const open = Boolean(popup && suggestions.length);

  const detect = (value: string, caret: number) => {
    const before = value.slice(0, caret);
    const slash = before.match(/^\/([\w:-]*)$/);
    if (slash) return setPopup({ kind: 'slash', query: slash[1], start: 0, index: 0 });
    const at = before.match(/(?:^|\s)@([^\s@]*)$/);
    if (!at) return setPopup(null);
    setPopup({ kind: 'mention', query: at[1], start: caret - at[1].length - 1, index: 0 });
  };

  const apply = (s: Suggestion) => {
    const el = field.current;
    if (!popup || !el) return;
    const caret = el.selectionStart;
    let next: string;
    let pos: number;
    if (popup.kind === 'slash') {
      next = `/${s.key} ` + text.slice(caret);
      pos = s.key.length + 2;
    } else {
      const ins = `@${s.key}${s.kind === 'dir' ? '' : ' '}`;
      next = text.slice(0, popup.start) + ins + text.slice(caret);
      pos = popup.start + ins.length;
    }
    setText(next);
    setPopup(null);
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(pos, pos);
      if (s.kind === 'dir') detect(next, pos);
    });
  };

  /** The keys the list owns while it is open. True when it took the key. */
  const onKey = (e: React.KeyboardEvent) => {
    if (!popup || !open) return false;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      const d = e.key === 'ArrowDown' ? 1 : -1;
      setPopup({ ...popup, index: (popup.index + d + suggestions.length) % suggestions.length });
    } else if (e.key === 'Enter' || e.key === 'Tab') apply(suggestions[popup.index]);
    else if (e.key === 'Escape') {
      e.stopPropagation();
      setPopup(null);
    } else return false;
    e.preventDefault();
    return true;
  };

  const option = (i: number) => `${listId}-${i}`;
  /** For the field: what the list is, and which row is lit. */
  const fieldProps = open ? { 'aria-controls': listId, 'aria-activedescendant': option(popup!.index) } : {};

  const view = open ? (
    <Leave className="suggest" duration={160}>
      <div className="suggest-head" id={`${listId}-h`}>
        {popup!.kind === 'slash' ? 'Commands' : popup!.query ? (suggestions.some((s) => s.kind === 'repo') ? 'Repositories and files' : 'Files') : 'Files — keep typing to narrow down'}
      </div>
      <div className="suggest-list" role="listbox" id={listId} aria-labelledby={`${listId}-h`} style={{ ['--name-ch' as string]: `${Math.min(30, Math.max(8, ...suggestions.filter((x) => x.kind === 'cmd').map((x) => x.label.length)))}ch` }}>
        {suggestions.slice(0, 40).map((s, i) => (
          <button key={s.key} id={option(i)} role="option" aria-selected={i === popup!.index} tabIndex={-1} className={`suggest-item${i === popup!.index ? ' active' : ''}`} onMouseDown={(e) => e.preventDefault()} onClick={() => apply(s)} onMouseEnter={() => setPopup({ ...popup!, index: i })} ref={i === popup!.index ? (el) => el?.scrollIntoView({ block: 'nearest' }) : undefined}>
            {s.kind === 'repo' ? <FolderGit2 size={14} className="suggest-repo" aria-hidden="true" /> : s.kind === 'dir' ? <Folder size={14} aria-hidden="true" /> : s.kind === 'file' ? <FileIcon size={14} aria-hidden="true" /> : null}
            <span className={`suggest-label${s.kind === 'cmd' ? ' cmd' : ''}`}>
              {s.kind === 'cmd' ? (
                <>
                  <span className="suggest-slash">/</span>
                  {s.label.slice(1)}
                </>
              ) : (
                s.label
              )}
            </span>
            {s.hint && <span className="suggest-hint">{s.hint}</span>}
          </button>
        ))}
      </div>
    </Leave>
  ) : null;

  return { view, detect, onKey, close: () => setPopup(null), fieldProps };
}
