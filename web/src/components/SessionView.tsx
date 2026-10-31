import { useEffect, useRef, useState } from 'react';
import { GitBranch, PanelRight, PanelLeftOpen, Ellipsis, Pencil, GitFork, FolderOpen, Archive, Trash, Copy, SquareTerminal, RotateCcw, Power, FolderGit2, Folder, Settings2, CircleDollarSign, ChevronDown, BookMarked, ArrowUpRight } from 'lucide-react';
import { useStore, setUi, VIEW_MODES, renameSession, forkSession, archiveSession, deleteSession, confirmAction, call, insertIntoComposer, toast, openSession, setDialog, setCwd, getModel, repoFor } from '../lib/store';
import { basename, cost, errText, plural, tildify } from '../lib/format';
import { McpNotice } from './McpNotice';
import { HelpersBar } from './HelpersBar';
import type { GitInfo, RepoEntry, SessionSummary } from '../lib/types';
import { Transcript } from './Transcript';
import { FindBar } from './FindBar';
import { Composer } from './Composer';
import { MenuButton, Popover } from './ui/Popover';
import { FolderPicker } from './FolderPicker';
import { copyText, AppIcon, Leave } from './ui/bits';

export function useGitInfo(cwd: string | undefined, tick: unknown) {
  const [info, setInfo] = useState<GitInfo | null>(null);
  const last = useRef(0);
  useEffect(() => {
    if (!cwd) return;
    let alive = true;
    const run = () => {
      last.current = Date.now();
      call<GitInfo>('git.info', { cwd }, { quiet: true })
        .then((i) => alive && setInfo(i))
        .catch(() => {});
    };
    const wait = Math.max(0, 1500 - (Date.now() - last.current));
    const t = setTimeout(run, wait);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [cwd, tick]);
  return info;
}

function runningWorkflow(tasks?: { type?: string; status: string; workflowName?: string; description?: string }[]) {
  const t = (tasks || []).find((x) => x.type === 'local_workflow' && x.status === 'running');
  return t ? t.workflowName || t.description || 'workflow' : null;
}

const STATUS_TEXT: Record<string, string> = {
  running: 'Working',
  starting: 'Starting',
  leading: 'Leading',
  requires_action: 'Needs you',
  idle: 'Ready',
  stopped: 'Ready',
  error: 'Stopped with an error',
};

export function SessionView({ session }: { session: SessionSummary }) {
  const id = session.id;
  const rt = useStore((s) => s.rt[id]);
  const ui = useStore((s) => s.ui);
  const info = useStore((s) => s.info);
  const cwd = rt?.cwd || session.cwd;
  const status = rt?.status || session.status;
  // a lead that is quiet while its helpers work is live work too (HELPERS.md §6.1)
  const helpers = rt?.helpers || [];
  const activeHelpers = helpers.filter((h) => ['queued', 'preparing', 'running', 'needs_action'].includes(h.state));
  const shownStatus = (!status || status === 'idle' || status === 'stopped') && activeHelpers.length ? 'leading' : status;
  const fsRev = useStore((s) => s.fsRev);
  const git = useGitInfo(cwd, `${status === 'running' ? Math.floor((rt?.rev || 0) / 40) : status}:${fsRev}`);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(session.title);
  const [scrolled, setScrolled] = useState(false);
  const [finding, setFinding] = useState(false);
  useEffect(() => {
    useStore.setState({ findOpen: finding });
    return () => useStore.setState({ findOpen: false });
  }, [finding]);
  const [pickingFolder, setPickingFolder] = useState(false);
  const folderBtn = useRef<HTMLButtonElement>(null);
  const root = useRef<HTMLDivElement>(null);
  // Until the first message goes out, the folder is still a choice, not a fact.
  const fresh = Boolean(rt?.loaded) && !rt?.live && getModel(id).items.length === 0 && !session.worktree && (rt?.status === 'idle' || rt?.status === 'stopped' || !rt?.status);
  useEffect(() => {
    const open = () => setFinding(true);
    const pick = () => setPickingFolder(true);
    window.addEventListener('pilcrow:find', open);
    window.addEventListener('pilcrow:pick-folder', pick);
    return () => {
      window.removeEventListener('pilcrow:find', open);
      window.removeEventListener('pilcrow:pick-folder', pick);
    };
  }, []);

  useEffect(() => setTitle(session.title), [session.title]);

  const changes = (git?.changed || 0) + (git?.untracked || 0);
  const projectRoot = session.worktree?.repoRoot || session.project || cwd;

  const menu = [
    { label: 'Rename', icon: Pencil, onSelect: () => setEditing(true) },
    { label: 'Fork into a new session', icon: GitFork, onSelect: () => forkSession(id) },
    { label: 'Open folder', icon: FolderOpen, onSelect: () => call('shell.openPath', { path: cwd }) },
    { label: 'Open a terminal here', icon: SquareTerminal, onSelect: () => setUi({ panel: true, panelTab: 'terminal' }) },
    {
      label: repoFor(projectRoot) ? 'Repository settings…' : 'Register this repository…',
      hint: 'Names, aliases, policy and notes for helpers and Dispatch',
      icon: FolderGit2,
      onSelect: () => {
        if (repoFor(projectRoot)) {
          setDialog('settings', 'repos');
          return;
        }
        call<RepoEntry>('repos.register', { path: projectRoot })
          .then((e) => {
            toast(`Registered ${e.name} · names, policy and notes are in Settings → Repositories`, 'success');
            setDialog('settings', 'repos');
          })
          .catch((e) => toast(errText(e), 'error'));
      },
    },
    { separator: true },
    ...VIEW_MODES.map((v) => ({ label: `${v.label} view`, hint: v.hint, checked: v.id === ui.view, onSelect: () => setUi({ view: v.id }) })),
    { separator: true },
    { label: 'Copy session ID', icon: Copy, onSelect: () => copyText(id).then(() => toast('Session ID copied', 'success'), () => toast('Could not copy to the clipboard', 'error')) },
    ...((rt?.usage?.costUsd || 0) > 0 ? [{ label: `Cost so far ${cost(rt!.usage!.costUsd)}`, icon: CircleDollarSign, onSelect: () => setUi({ panel: true, panelTab: 'info' }) }] : []),
    { separator: true },
    ...(rt?.live ? [{ label: 'Stop Claude Code process', hint: 'It restarts on your next message', icon: Power, onSelect: () => call('session.close', { id }) }] : []),
    ...(rt?.helper ? [{ label: 'Detach from lead', hint: 'An ordinary session from now on; the lead is told', icon: GitBranch, onSelect: () => call('helpers.detach', { id }) }] : []),
    { label: session.archived ? 'Unarchive' : 'Archive', icon: Archive, onSelect: () => archiveSession(id, !session.archived) },
    {
      label: 'Delete…',
      icon: Trash,
      danger: true,
      onSelect: () =>
        confirmAction({
          title: 'Delete this session?',
          body: `The conversation history is removed from ~/.claude. Files Claude changed stay as they are.${helpers.length ? ' Its helpers stay as sessions unless you ask.' : ''}`,
          confirm: 'Delete session',
          danger: true,
          // a lead's helpers go with it when asked (HELPERS.md §5.10); a worktree session asks about its tree
          checkbox: helpers.length ? { label: `Also delete its ${plural(helpers.length, 'helper')} and their worktrees`, value: false } : session.worktree ? { label: `Also remove the worktree and branch ${session.worktree.branch}`, value: false } : undefined,
          onConfirm: (checked) => deleteSession(id, checked, checked && helpers.length > 0),
        }),
    },
  ];

  return (
    <div className="session" ref={root}>
      <header className={`session-head${scrolled ? ' scrolled' : ''}`}>
        {!ui.sidebar && (
          <button className="icon-btn head-sidebar" title="Show sidebar (Ctrl+B)" aria-label="Show sidebar" onClick={() => setUi({ sidebar: true })}>
            <PanelLeftOpen size={16} />
          </button>
        )}
        <div className="session-title-wrap">
          {editing ? (
            <input
              className="input title-input"
              autoFocus
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onBlur={() => {
                setEditing(false);
                if (title.trim() && title !== session.title) renameSession(id, title.trim());
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
                if (e.key === 'Escape') {
                  setTitle(session.title);
                  setEditing(false);
                }
              }}
            />
          ) : (
            <h1 className="session-title" title="Double-click to rename" onDoubleClick={() => setEditing(true)}>
              {session.title}
            </h1>
          )}
          <span className="session-crumbs" title={tildify(cwd, info?.home)}>
            {fresh ? (
              <button ref={folderBtn} className={`crumb crumb-btn${pickingFolder ? ' on' : ''}`} onClick={() => setPickingFolder(!pickingFolder)} title="Change the folder for this session" aria-expanded={pickingFolder}>
                <Folder size={12} />
                <span className="ellipsis">{basename(cwd)}</span>
                <ChevronDown size={11} className="crumb-chev" />
              </button>
            ) : (
              <span className="crumb">
                {session.worktree ? <FolderGit2 size={12} /> : <Folder size={12} />}
                <span className="ellipsis">{basename(session.worktree?.repoRoot || session.project || cwd)}</span>
              </span>
            )}
            <Popover anchor={folderBtn.current} open={pickingFolder} onClose={() => setPickingFolder(false)} placement="bottom-start" width={380}>
              <FolderPicker
                value={cwd}
                onPick={(dir) => {
                  setPickingFolder(false);
                  if (dir !== cwd) setCwd(id, dir).catch(() => {});
                }}
              />
            </Popover>
            {git?.branch && (
              <span className={`crumb${session.worktree ? ' wt' : ''}`} title={session.worktree ? `Worktree at ${tildify(session.worktree.path, info?.home)}` : 'Current branch'}>
                <GitBranch size={12} />
                <span className="ellipsis">{git.branch}</span>
              </span>
            )}
            {rt?.helper && (
              <button className="crumb crumb-btn wt" title="Open the lead session" onClick={() => openSession(rt.helper!.leadId)}>
                <ArrowUpRight size={12} />
                <span className="ellipsis">helper of {rt.helper.leadTitle || 'a lead session'}</span>
              </button>
            )}
          </span>
          {shownStatus && shownStatus !== 'idle' && shownStatus !== 'stopped' && (
            <span className={`status-pill ${shownStatus}`}>
              <span className="status-dot" />
              {STATUS_TEXT[shownStatus] || shownStatus}
              {shownStatus === 'leading' && (
                <span className="status-sub">
                  · {activeHelpers.length} of {plural(helpers.length, 'helper')}
                </span>
              )}
              {status === 'running' && runningWorkflow(rt?.tasks) && (
                <button className="status-sub status-link" title="Show the workflow in the Tasks panel" onClick={() => setUi({ panel: true, panelTab: 'tasks' })}>
                  · {runningWorkflow(rt?.tasks)}
                </button>
              )}
            </span>
          )}
        </div>
        <div className="session-actions">
          {git?.isRepo && changes > 0 && (
            <button className={`diffstat${ui.panel && ui.panelTab === 'changes' ? ' on' : ''}`} onClick={() => setUi({ panel: !(ui.panel && ui.panelTab === 'changes'), panelTab: 'changes' })} title={`${changes} changed file${changes > 1 ? 's' : ''} — review (Alt+1)`}>
              <span className={`num-add${git.insertions ? '' : ' zero'}`}>+{git.insertions ?? 0}</span>
              <span className={`num-del${git.deletions ? '' : ' zero'}`}>−{git.deletions ?? 0}</span>
            </button>
          )}
          <button className={`icon-btn${ui.panel ? ' active' : ''}`} title="Side panel (Ctrl+\)" onClick={() => setUi({ panel: !ui.panel })}>
            <PanelRight size={16} />
          </button>
          <MenuButton items={menu} title="Session actions" width={250}>
            <Ellipsis size={16} />
          </MenuButton>
        </div>
      </header>
      <McpNotice sessionId={id} />
      <HelpersBar sessionId={id} />
      {status === 'error' && rt?.error && (
        <Leave className="session-error">
          <span className="grow">{rt.error.split('\n')[0]}</span>
          {/Settings|not found|No Claude Code/i.test(rt.error) ? (
            <button className="btn sm" onClick={() => setDialog('settings', 'claude')}>
              <Settings2 size={13} /> Settings
            </button>
          ) : (
            <button className="btn sm" onClick={() => insertIntoComposer(id, 'continue')}>
              <RotateCcw size={13} /> Retry
            </button>
          )}
        </Leave>
      )}
      {finding && <FindBar root={() => document.querySelector('.session .transcript-inner')} onClose={() => setFinding(false)} />}
      <Transcript sessionId={id} cwd={cwd} onScrolled={setScrolled} empty={<SessionEmpty session={session} git={git} cwd={cwd} fresh={fresh} />} />
      <Composer sessionId={id} cwd={cwd} />
    </div>
  );
}

const EXAMPLES = ['Give me a tour of this codebase', 'Find the riskiest code path and explain why', 'Write tests for the module I changed last', 'Review my uncommitted changes'];

function SessionEmpty({ session, git, cwd, fresh }: { session: SessionSummary; git: GitInfo | null; cwd: string; fresh?: boolean }) {
  const info = useStore((s) => s.info);
  const [memory, setMemory] = useState<number>(0);
  useEffect(() => {
    let alive = true;
    call<{ notes: unknown[] }>('memory.list', { cwd: session.project || cwd }, { quiet: true })
      .then((r) => alive && setMemory(r.notes.length))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [cwd, session.project]);
  return (
    <div className="session-empty">
      <AppIcon size={56} className="session-empty-icon" />
      <div className="session-empty-title">{basename(cwd)}</div>
      <div className="session-empty-path">
        <span className="faint mono">{tildify(cwd, info?.home)}</span>
        {fresh && (
          <button className="link session-empty-change" onClick={() => window.dispatchEvent(new Event('pilcrow:pick-folder'))}>
            <Folder size={12} /> Change folder
          </button>
        )}
      </div>
      {git?.isRepo && (
        <div className="session-empty-git faint">
          <GitBranch size={12} /> {git.branch || 'detached'}
          {session.worktree ? ` · isolated worktree from ${session.worktree.base}` : ''}
          {git.changed || git.untracked ? ` · ${(git.changed || 0) + (git.untracked || 0)} uncommitted` : ''}
        </div>
      )}
      {memory > 0 && (
        <button className="session-empty-mem" onClick={() => setDialog('settings', 'memory')} title="Notes from earlier sessions in this folder ride along in this session">
          <BookMarked size={12} /> Claude starts with {memory} note{memory > 1 ? 's' : ''} from earlier sessions here
        </button>
      )}
      <div className="examples">
        {EXAMPLES.map((e) => (
          <button key={e} className="example" onClick={() => insertIntoComposer(session.id, e, true)}>
            {e}
          </button>
        ))}
      </div>
    </div>
  );
}

export function NoSession() {
  return <div className="session" />;
}

export { openSession };
