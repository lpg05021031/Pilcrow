import { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Pause, Play, Square, Ellipsis, GitCommitHorizontal, GitPullRequestArrow, ExternalLink, RotateCw, BookMarked, CalendarClock, ClipboardPaste, Trash, CircleDollarSign } from 'lucide-react';
import { useStore, call, openSession, respond, confirmAction, toast, showDispatch, openJob, runsOf, loadPlaybooks } from '../../lib/store';
import type { DispatchJob, DispatchReview, DispatchRun, Playbook } from '../../lib/types';
import { cost, plural } from '../../lib/format';
import { Switch, useNow, copyText } from '../ui/bits';
import { MenuButton } from '../ui/Popover';
import { CanaryRow, RequestRow } from './Board';
import { ACTIVE, Glyph, STATE_WORD, TERMINAL, clockMmss, elapsedText, groupRequests, jobCostText, jobStateText } from './common';

/**
 * A job's view: the header with its state and controls, the brief and the plan, the canary waiting
 * to be read, the requests waiting for a hand, one row per repository, and at the foot the review
 * across repositories with the ways to finish.
 */
export function JobView({ id }: { id: string }) {
  const job = useStore((s) => s.jobs[id]);
  const jobRuns = useStore((s) => s.jobRuns);
  const playbooks = useStore((s) => s.playbooks);
  const now = useNow(1000, true);
  const runs = useMemo(() => runsOf(jobRuns, id).sort((a, b) => a.level - b.level || a.name.localeCompare(b.name)), [jobRuns, id]);
  const [review, setReview] = useState<DispatchReview | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const st = job?.state;
  const reviewing = Boolean(job) && (st === 'reviewing' || st === 'finished' || st === 'stopped') && !job.canaryWaiting;
  const doneKey = runs
    .filter((r) => r.state === 'done')
    .map((r) => r.id)
    .join(',');
  useEffect(() => {
    if (!reviewing || st === 'finished') return;
    let alive = true;
    call<DispatchReview>('dispatch.review', { id }, { quiet: true })
      .then((r) => alive && setReview(r))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [id, reviewing, st, doneKey]);
  if (!job) {
    return (
      <div className="dispatch">
        <header className="ov-head">
          <button className="icon-btn" title="Back to the board" aria-label="Back" onClick={() => showDispatch(null)}>
            <ArrowLeft size={16} />
          </button>
          <h1 className="ov-title">Job</h1>
        </header>
      </div>
    );
  }
  const groups = groupRequests(runs);
  const openRuns = runs.filter((r) => TERMINAL.includes(r.state) && !r.finished && r.worktree);
  const doneOpen = openRuns.filter((r) => r.state === 'done');
  const rules = job.rules || [];
  const rep = job.report;

  const saveBrief = () => {
    setEditing(false);
    if (draft.trim() && draft !== job.brief) call('dispatch.save', { id, brief: draft }).catch(() => {});
  };
  const finish = (action: 'commit' | 'discard' | 'keep', pr = false) => {
    const list = action === 'commit' ? doneOpen : openRuns;
    if (!list.length) return;
    if (action === 'keep') return void call('dispatch.finish', { id, action }).catch(() => {});
    const branch = `pilcrow/${job.slug || 'job'}`;
    confirmAction({
      title: action === 'commit' ? `${pr ? 'Open pull requests for' : 'Commit'} ${plural(list.length, 'repository', 'repositories')}?` : `Discard the changes in ${plural(list.length, 'repository', 'repositories')}?`,
      body: action === 'commit' ? `Each run's changes are committed on its branch ${branch} with the message “${job.settings.commitMessage || job.name}”. ${pr ? 'The branches are pushed and a pull request opened with gh where the repository has a GitHub remote. ' : ''}The worktree directories are removed; the branches stay.` : `The worktrees and the branches ${branch} are removed. This cannot be undone.`,
      confirm: pr ? 'Open pull requests' : action === 'commit' ? 'Commit' : 'Discard',
      danger: action === 'discard',
      onConfirm: () => call('dispatch.finish', { id, action, pr }).catch(() => {}),
    });
  };
  const ensurePlaybook = async (): Promise<string | null> => {
    const pid = job.playbookId;
    if (pid && playbooks.some((p: Playbook) => p.id === pid)) return pid;
    const pb = await call<Playbook>('dispatch.savePlaybook', { fromJob: id }).catch(() => null);
    loadPlaybooks();
    return pb?.id || null;
  };
  const menu = [
    {
      label: 'Run again',
      hint: 'A new job from this brief',
      icon: RotateCw,
      onSelect: async () => {
        const pid = await ensurePlaybook();
        if (!pid) return;
        const j = await call<DispatchJob>('dispatch.startFromPlaybook', { id: pid, name: job.name }).catch(() => null);
        if (j) openJob(j.id);
      },
    },
    {
      label: 'Save as playbook',
      icon: BookMarked,
      onSelect: async () => {
        await call('dispatch.savePlaybook', { fromJob: id, name: job.name }).catch(() => null);
        loadPlaybooks();
        toast(`Saved as playbook: ${job.name}`, 'success');
      },
    },
    {
      label: 'Schedule this…',
      icon: CalendarClock,
      onSelect: async () => {
        const pid = await ensurePlaybook();
        if (pid) useStore.setState({ scheduleDialog: { kind: 'dispatch', playbookId: pid }, page: 'inbox' });
      },
    },
    { separator: true },
    {
      label: 'Copy the report as Markdown',
      icon: ClipboardPaste,
      onSelect: async () => {
        const md = await call<string>('dispatch.export', { id }).catch(() => null);
        if (md) {
          await copyText(md);
          toast('Report copied as Markdown.', 'info');
        }
      },
    },
    ...(job.state === 'running' && job.capReached
      ? [
          {
            label: 'Raise the cost cap and resume',
            icon: CircleDollarSign,
            onSelect: () => call('dispatch.save', { id, settings: { costCap: job.settings.costCap ? job.settings.costCap * 2 : 10 } }).then(() => call('dispatch.resume', { id })),
          },
        ]
      : []),
    { separator: true },
    {
      label: 'Remove the job',
      icon: Trash,
      danger: true,
      onSelect: () =>
        confirmAction({
          title: `Remove ${job.name}?`,
          body: `${job.state === 'running' ? 'It is still running: its runs are stopped first. ' : ''}Worktrees that were kept are removed with their branches; committed branches stay.`,
          confirm: 'Remove',
          danger: true,
          onConfirm: () => call('dispatch.remove', { id, removeWorktrees: true }).then(() => showDispatch(null)),
        }),
    },
  ];

  const reviewText = () => {
    if (st === 'finished') {
      const t = rep?.targets || [];
      const committed = t.filter((x) => x.finished === 'commit').length;
      const prs = t.filter((x) => x.pr).length;
      return `Finished: ${t.filter((x) => x.state === 'done').length} of ${t.length} done · ${committed} committed${prs ? ` · ${prs} PRs` : ''} · report in the inbox`;
    }
    if (!review) return 'Review: reading the changes…';
    const changed = review.runs.filter((r) => r.files.length).length;
    const bits = [`${plural(changed, 'repository', 'repositories')} changed`, plural(review.files, 'file')];
    if (review.same.length) bits.push(`the same change in ${review.same[0].runs.length} repos (${review.same[0].path})${review.same.length > 1 ? ` and ${review.same.length - 1} more` : ''}`);
    if (review.differ.length && review.same.length) bits.push(`${review.differ.length} differ`);
    return `Review: ${bits.join(' · ')}`;
  };
  const canEdit = st === 'running' || (st === 'reviewing' && job.canaryWaiting);
  return (
    <div className="dispatch">
      <header className="ov-head">
        <button className="icon-btn" title="Back to the board" aria-label="Back" onClick={() => showDispatch(null)}>
          <ArrowLeft size={16} />
        </button>
        <h1 className="ov-title ellipsis">{job.name || 'Untitled job'}</h1>
        <span className="ov-sum tnum">
          {plural(job.total || 0, 'repo')} · {jobStateText(job, now)}
          {job.cost ? ` · ${jobCostText(job)}` : ''}
          {st === 'finished' || st === 'stopped' ? ` · ${elapsedText(job, now)}` : ''}
        </span>
        <div className="grow" />
        {st === 'running' && (
          <button className="btn sm ghost" onClick={() => call(job.paused ? 'dispatch.resume' : 'dispatch.pause', { id }).catch(() => {})}>
            {job.paused ? <Play size={13} /> : <Pause size={13} />} {job.paused ? 'Resume' : 'Pause'}
          </button>
        )}
        {(st === 'running' || st === 'reviewing') && (
          <button className="btn sm ghost" onClick={() => confirmAction({ title: `Stop ${job.name}?`, body: 'Runs under way are interrupted and kept as they are; queued ones are skipped. Nothing is discarded.', confirm: 'Stop the job', danger: true, onConfirm: () => call('dispatch.stop', { id }).catch(() => {}) })}>
            <Square size={12} /> Stop
          </button>
        )}
        <MenuButton items={menu} title="More" width={280}>
          <Ellipsis size={16} />
        </MenuButton>
      </header>
      <div className="dp-top">
        <div className="dp-box">
          <div className="dp-box-h">
            <span>Brief</span>
            <span className="faint">
              {job.plan ? `Plan: ${job.plan.ok} ok${job.plan.warnings ? ` · ${plural(job.plan.warnings, 'warning')}` : ''}${job.plan.blocked ? ` · ${job.plan.blocked} blocked` : ''}${job.plan.levels > 1 ? ` · ${job.plan.levels} levels` : ''}` : ''}
            </span>
            <div className="grow" />
            {canEdit && (
              <button className="link" onClick={() => (editing ? saveBrief() : (setDraft(job.brief), setEditing(true)))}>
                {editing ? 'Save' : 'Edit'}
              </button>
            )}
          </div>
          {editing ? <textarea className="textarea" rows={5} value={draft} onChange={(e) => setDraft(e.target.value)} /> : <p className="dp-brief">{job.brief}</p>}
          {rules.length > 0 && <div className="dp-rules faint">Allowed for every repository: {rules.map((r) => r.label || r.key).join('; ')}</div>}
        </div>
        {rep && (rep.prose || rep.proseState === 'writing' || rep.proseState === 'failed') && (
          <div className="dp-box">
            <div className="dp-box-h">
              <span>Report</span>
            </div>
            <p className={`dp-brief${rep.prose ? '' : ' faint'}`}>{rep.prose || (rep.proseState === 'writing' ? 'An agent is writing the report from the runs’ summaries…' : `The written report did not come: ${rep.proseError || 'unknown reason'}. The table stands.`)}</p>
          </div>
        )}
        {job.canaryWaiting && (
          <>
            <CanaryRow job={job} />
            <div className="dp-learn">
              <Switch on={job.settings.learn !== false} onChange={(on) => call('dispatch.save', { id, settings: { learn: on } }, { quiet: true }).catch(() => {})} label="let the rest learn from the canary's summary" />
              <span className="faint">let the rest learn from the canary’s summary</span>
            </div>
          </>
        )}
        {groups.slice(0, 6).map((g) => (
          <RequestRow key={g.key} jobId={id} group={g} />
        ))}
      </div>
      <div className="dp-runs">
        <table className="dp-table">
          <thead>
            <tr>
              <th>state</th>
              <th>repository</th>
              <th>branch</th>
              <th className="num">elapsed</th>
              <th className="num">Δ files</th>
              <th className="num">cost</th>
              <th>now</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <RunRow key={r.id} run={r} jobId={id} now={now} runs={runs} />
            ))}
          </tbody>
        </table>
      </div>
      {reviewing && runs.length > 0 && (
        <footer className="dp-foot">
          <span className="grow">{reviewText()}</span>
          {doneOpen.length > 0 && (
            <>
              <button className="btn sm" onClick={() => finish('commit')}>
                <GitCommitHorizontal size={13} /> Commit all…
              </button>
              <button className="btn sm" onClick={() => finish('commit', true)}>
                <GitPullRequestArrow size={13} /> Open PRs…
              </button>
            </>
          )}
          {openRuns.length > 0 && (
            <>
              <button className="btn sm ghost" onClick={() => finish('keep')}>
                Keep
              </button>
              <button className="btn sm ghost" onClick={() => finish('discard')}>
                Discard…
              </button>
            </>
          )}
        </footer>
      )}
    </div>
  );
}

function RunRow({ run, jobId, now, runs }: { run: DispatchRun; jobId: string; now: number; runs: DispatchRun[] }) {
  const open = () => (run.sessionId ? openSession(run.sessionId) : toast('This run has no session yet.', 'info'));
  const pending = run.pending?.[0];
  const answer = (allow: boolean) => pending && run.sessionId && respond(run.sessionId, pending.requestId, allow ? { behavior: 'allow', decisionClassification: 'user_temporary' } : { behavior: 'deny', message: 'The user declined this action.', decisionClassification: 'user_reject' });
  const elapsed = run.startedAt ? clockMmss((run.endedAt || now) - run.startedAt) : '—';
  let nowText = '';
  let nowClass = '';
  if (run.state === 'done') {
    const bits: string[] = [];
    if (run.verify?.ran) bits.push(run.verify.code === 0 ? 'verify passed' : `verify exit ${run.verify.code}`);
    if (run.files?.length) bits.push(plural(run.files.length, 'file'));
    if (run.commit) bits.push(`committed ${run.commit}`);
    else if (run.finished) bits.push(run.finished);
    nowText = bits.join(' · ') || (run.summary || '').slice(0, 80);
    nowClass = bits.length ? 'ok' : '';
  } else if (run.state === 'failed' || run.state === 'stopped' || run.state === 'skipped') {
    nowText = run.reason || STATE_WORD[run.state];
    nowClass = run.state === 'failed' ? 'err' : 'faint';
  } else if (run.state === 'queued') {
    const deps = runs.filter((x) => run.dependsOn.includes(x.path)).map((x) => x.name);
    nowText = deps.length ? `after ${deps.join(', ')}` : 'waiting for a slot';
    nowClass = 'faint';
  } else nowText = run.now || STATE_WORD[run.state];
  const stop = (e: React.MouseEvent) => (e.stopPropagation(), call('dispatch.stop', { id: jobId, runId: run.id }).catch(() => {}));
  const retry = (e: React.MouseEvent) => (e.stopPropagation(), call('dispatch.retry', { id: jobId, runId: run.id }).catch(() => {}));
  const skip = (e: React.MouseEvent) => (e.stopPropagation(), call('dispatch.skip', { id: jobId, runId: run.id }).catch(() => {}));
  return (
    <tr className={`dp-row ${run.state}`} onClick={open}>
      <td className="dp-state">
        <Glyph state={run.state} /> <span className={`dp-word ${run.state}`}>{STATE_WORD[run.state]}</span>
      </td>
      <td className="dp-repo">{run.name}</td>
      <td className="mono faint">{run.worktree?.branch || '—'}</td>
      <td className="num tnum">{elapsed}</td>
      <td className="num tnum">
        {run.add || run.del ? (
          <>
            <span className="num-add">+{run.add}</span> <span className="num-del">−{run.del}</span>
          </>
        ) : (
          '—'
        )}
      </td>
      <td className="num tnum">{run.cost ? cost(run.cost) : '—'}</td>
      <td className={`dp-now ${nowClass}`}>
        <span className="ellipsis">{nowText}</span>
        <span className="dp-acts" onClick={(e) => e.stopPropagation()}>
          {run.state === 'needs_action' && pending && !pending.interactive && (
            <>
              <button className="btn xs primary" onClick={() => answer(true)}>
                Allow
              </button>
              <button className="btn xs ghost" onClick={() => answer(false)}>
                Deny
              </button>
            </>
          )}
          {run.state === 'needs_action' && pending?.interactive && (
            <button className="btn xs primary" onClick={open}>
              Open to respond
            </button>
          )}
          {(run.state === 'failed' || run.state === 'stopped') && (
            <>
              <button className="btn xs" onClick={retry}>
                Retry
              </button>
              <button className="btn xs ghost" onClick={open}>
                Open
              </button>
            </>
          )}
          {ACTIVE.includes(run.state) && run.state !== 'needs_action' && (
            <button className="btn xs ghost" onClick={stop}>
              Stop
            </button>
          )}
          {run.state === 'queued' && (
            <button className="btn xs ghost" onClick={skip}>
              Skip
            </button>
          )}
          {run.state === 'done' && run.pr && (
            <a className="link" href={run.pr} target="_blank" rel="noreferrer">
              PR <ExternalLink size={11} />
            </a>
          )}
        </span>
      </td>
    </tr>
  );
}
