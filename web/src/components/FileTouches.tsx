import { useEffect, useRef, useState } from 'react';
import { History, ArrowUpRight } from 'lucide-react';
import { useStore, call, openSession, setUi } from '../lib/store';
import type { FileTouch } from '../lib/types';
import { timeAgo, plural } from '../lib/format';
import { Popover } from './ui/Popover';
import { revealItem } from './Transcript';
import { useNow } from './ui/bits';

type HistoryMap = Record<string, FileTouch[]>;
const cache = new Map<string, { at: number; data: HistoryMap; p?: Promise<HistoryMap> }>();

/** Which sessions touched which files in a folder, from the timeline; cached briefly per folder. */
export function useFileHistory(cwd: string): HistoryMap | null {
  const fsRev = useStore((s) => s.fsRev);
  const schedRev = useStore((s) => s.schedRev);
  const [data, setData] = useState<HistoryMap | null>(() => cache.get(cwd)?.data || null);
  useEffect(() => {
    if (!cwd) return;
    let alive = true;
    const cur = cache.get(cwd);
    if (cur && Date.now() - cur.at < 15_000 && !cur.p) {
      setData(cur.data);
      return;
    }
    const p = call<HistoryMap>('project.fileHistory', { cwd }, { quiet: true }).catch(() => ({}) as HistoryMap);
    cache.set(cwd, { at: Date.now(), data: cur?.data || {}, p });
    p.then((d) => {
      cache.set(cwd, { at: Date.now(), data: d });
      if (alive) setData(d);
    });
    return () => {
      alive = false;
    };
  }, [cwd, fsRev, schedRev]);
  return data;
}

/**
 * A quiet mark on a file row: how many earlier sessions changed it. Opens a list of those sessions;
 * picking one opens it at the step that made the change.
 */
export function FileTouches({ touches, compact = false }: { touches: FileTouch[] | undefined; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);
  const now = useNow(30_000, open);
  const activeId = useStore((s) => s.activeId);
  if (!touches?.length) return null;
  const sessions = new Map<string, FileTouch[]>();
  for (const t of touches) sessions.set(t.sessionId, [...(sessions.get(t.sessionId) || []), t]);
  const others = [...sessions.keys()].filter((id) => id !== activeId).length;
  if (compact && !others) return null;
  const n = sessions.size;
  const go = (t: FileTouch) => {
    setOpen(false);
    openSession(t.sessionId).then(() => {
      setUi({ panel: true, panelTab: 'history' });
      setTimeout(async () => {
        const el = t.toolUseId ? await revealItem(t.sessionId, `tool-${t.toolUseId}`, t.toolUseId) : null;
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          el.classList.add('flash');
          setTimeout(() => el.classList.remove('flash'), 1400);
        }
      }, 400);
    });
  };
  return (
    <>
      <button
        ref={ref}
        className={`touches${open ? ' on' : ''}`}
        title={`Changed in ${plural(n, 'session')}`}
        onClick={(e) => {
          e.stopPropagation();
          setOpen(!open);
        }}
      >
        <History size={11} />
        <span className="tnum">{n}</span>
      </button>
      <Popover anchor={ref.current} open={open} onClose={() => setOpen(false)} placement="bottom-end" width={360}>
        <div className="touches-pop">
          <div className="touches-h">Changed in {plural(n, 'session')}</div>
          {[...sessions.entries()].map(([id, ts]) => {
            const latest = ts[0];
            const add = ts.reduce((s, t) => s + t.add, 0);
            const del = ts.reduce((s, t) => s + t.del, 0);
            return (
              <button key={id} className={`touches-row${id === activeId ? ' cur' : ''}`} onClick={() => go(latest)}>
                <span className="touches-main">
                  <span className="touches-title ellipsis">{latest.title}</span>
                  {latest.about && <span className="touches-about ellipsis">{latest.about}</span>}
                  <span className="touches-sub tnum">
                    {timeAgo(latest.at, now)} · {plural(ts.length, 'step')} · <span className="num-add">+{add}</span> <span className="num-del">−{del}</span>
                    {id === activeId ? ' · this session' : ''}
                  </span>
                </span>
                <ArrowUpRight size={13} className="touches-go" />
              </button>
            );
          })}
        </div>
      </Popover>
    </>
  );
}
