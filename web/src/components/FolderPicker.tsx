import { useEffect, useMemo, useState } from 'react';
import { Folder, FolderGit2, Home, CornerLeftUp, ChevronRight, Check, Search } from 'lucide-react';
import { useStore, call } from '../lib/store';
import { basename, tildify } from '../lib/format';

interface Browse {
  dir: string;
  display: string;
  parent: string | null;
  repo: boolean;
  dirs: { name: string; path: string; repo: boolean }[];
  error?: string;
}

/**
 * A compact folder chooser for popovers: recent projects first, then a small browser.
 * Used by the session header to retarget a session that has not started yet.
 */
export function FolderPicker({ value, onPick }: { value: string; onPick: (dir: string) => void }) {
  const config = useStore((s) => s.config);
  const info = useStore((s) => s.info);
  const sessions = useStore((s) => s.sessions);
  const [browse, setBrowse] = useState<Browse | null>(null);
  const [browsing, setBrowsing] = useState(false);
  const [q, setQ] = useState('');
  const native = window.pilcrowNative?.pickFolder;

  const recents = useMemo(() => {
    const set = new Set<string>(config?.recentProjects || []);
    for (const s of sessions) if (set.size < 12 && !s.archived) set.add(s.project || s.cwd);
    const list = [...set].filter(Boolean);
    const needle = q.trim().toLowerCase();
    return needle ? list.filter((p) => p.toLowerCase().includes(needle)) : list.slice(0, 8);
  }, [config?.recentProjects, sessions, q]);

  const nav = async (dir: string) => {
    const r = await call<Browse>('fs.browse', { dir }, { quiet: true }).catch(() => null);
    if (r) setBrowse(r);
  };
  useEffect(() => {
    if (browsing && !browse) nav(value || info?.home || '/');
  }, [browsing]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="fpick">
      <label className="fpick-search">
        <Search size={13} />
        <input autoFocus placeholder="Recent folders, or type a path" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && q.trim().startsWith('/') && onPick(q.trim())} spellCheck={false} />
      </label>
      {!browsing && (
        <div className="fpick-list">
          {recents.map((p) => (
            <button key={p} className={`fpick-row${p === value ? ' on' : ''}`} onClick={() => onPick(p)} title={p}>
              <Folder size={14} className="fpick-ico" />
              <span className="fpick-name ellipsis">{basename(p)}</span>
              <span className="fpick-path ellipsis mono">{tildify(p, info?.home)}</span>
              {p === value && <Check size={13} className="fpick-check" />}
            </button>
          ))}
          {!recents.length && <div className="fpick-empty faint">{q ? 'No recent folder matches. Type a full path and press Enter.' : 'No recent folders yet.'}</div>}
          {q.trim().startsWith('/') && !recents.includes(q.trim()) && (
            <button className="fpick-row" onClick={() => onPick(q.trim())}>
              <CornerLeftUp size={14} className="fpick-ico" />
              <span className="fpick-name ellipsis">Use {q.trim()}</span>
            </button>
          )}
        </div>
      )}
      {browsing && browse && (
        <div className="browser fpick-browser">
          <div className="browser-head">
            <button className="icon-btn sm" title="Home" onClick={() => nav(info?.home || '/')}>
              <Home size={13} />
            </button>
            <button className="icon-btn sm" title="Up one level" disabled={!browse.parent} onClick={() => browse.parent && nav(browse.parent)}>
              <CornerLeftUp size={13} />
            </button>
            <span className="mono ellipsis grow">{browse.display}</span>
            {browse.repo && <span className="badge">git</span>}
          </div>
          <div className="browser-list">
            {browse.error && <div className="faint browser-empty">{browse.error}</div>}
            {browse.dirs.map((d) => (
              <button key={d.path} className="browser-item" onClick={() => nav(d.path)} onDoubleClick={() => onPick(d.path)}>
                {d.repo ? <FolderGit2 size={14} /> : <Folder size={14} />}
                <span className="ellipsis grow">{d.name}</span>
                <ChevronRight size={13} className="faint" />
              </button>
            ))}
            {!browse.dirs.length && !browse.error && <div className="faint browser-empty">No subfolders</div>}
          </div>
          <div className="fpick-foot">
            <button className="btn sm ghost" onClick={() => setBrowsing(false)}>
              Recent
            </button>
            <span className="grow" />
            <button className="btn sm primary" onClick={() => onPick(browse.dir)}>
              Use {basename(browse.dir) || browse.dir}
            </button>
          </div>
        </div>
      )}
      {!browsing && (
        <div className="fpick-foot">
          <button className="btn sm ghost" onClick={() => setBrowsing(true)}>
            Browse…
          </button>
          {native && (
            <button
              className="btn sm ghost"
              onClick={async () => {
                const p = await native(value);
                if (p) onPick(p);
              }}
            >
              System picker…
            </button>
          )}
        </div>
      )}
    </div>
  );
}
