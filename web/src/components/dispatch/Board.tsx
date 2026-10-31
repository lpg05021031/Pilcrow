import { useMemo } from 'react';
import { Plus, PanelLeftOpen, Play } from 'lucide-react';
import { useStore, call, openSession, respond, setUi, showDispatch, openJob, runsOf } from '../../lib/store';
import type { DispatchJob } from '../../lib/types';
import { cost, duration, plural } from '../../lib/format';
import { Mark, useNow } from '../ui/bits';
import { SegmentBar, askedBy, countsText, elapsedText, groupRequests, jobCostText, jobStateText, type RequestGroup } from './common';

/**
 * The board: every job at a glance. The control room's discipline: sections appear only when they
 * have something, what needs you comes first, and a request can be answered on the card (once, or
 * once for every repository that asks the same thing), so most jobs are never opened.
 */
export function Board() {
  const jobs = useStore((s) => s.jobs);
  const jobRuns = useStore((s) => s.jobRuns);
  const loaded = useStore((s) => s.jobsLoaded);
  const sidebar = useStore((s) => s.ui.sidebar);
  const now = useNow(1000, true);
  const buckets = useMemo(() => {
    const out = { needs: [] as DispatchJob[], running: [] as DispatchJob[], review: [] as DispatchJob[], drafts: [] as DispatchJob[], finished: [] as DispatchJob[] };
    for (const j of Object.values(jobs).sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))) {
      if ((j.state === 'running' || j.state === 'reviewing') && (j.needsAction || j.canaryWaiting)) out.needs.push(j);
      else if (j.state === 'running') out.running.push(j);
      else if (j.state === 'reviewing') out.review.push(j);
      else if (j.state === 'draft' || j.state === 'planned') out.drafts.push(j);
      else out.finished.push(j);
    }
    out.finished = out.finished.slice(0, 20);
    return out;
  }, [jobs]);
  const total = Object.keys(jobs).length;
  const running = buckets.running.length + buckets.needs.length;
  return (
    <div className="dispatch">
      <header className="ov-head">
        {!sidebar && (
          <button className="icon-btn head-sidebar" title="Show sidebar (Ctrl+B)" aria-label="Show sidebar" onClick={() => setUi({ sidebar: true })}>
            <PanelLeftOpen size={16} />
          </button>
        )}
        <h1 className="ov-title">Dispatch</h1>
        <span className="ov-sum tnum">
          {total ? plural(total, 'job') : ''}
          {buckets.needs.length ? ` · ${buckets.needs.length} need a hand` : running ? ` · ${running} running` : ''}
        </span>
        <div className="grow" />
        <button className="btn primary sm" onClick={() => showDispatch('new')}>
          <Plus size={13} /> New job
        </button>
      </header>
      <div className="ov-body dp-body">
        {(
          [
            ['needs', 'Needs a hand', true],
            ['running', 'Running', false],
            ['review', 'In review', false],
            ['drafts', 'Drafts', false],
          ] as ['needs' | 'running' | 'review' | 'drafts', string, boolean][]
        ).map(([key, title, glyph]) =>
          buckets[key].length ? (
            <section key={key} className="ov-sec">
              <h2 className="ov-h">
                {glyph && <span className="glyph needs" aria-hidden="true" />} {title} <span className="ov-count">{buckets[key].length}</span>
              </h2>
              <div className="ov-queue">
                {buckets[key].map((j) => (
                  <JobCard key={j.id} job={j} now={now} detail={key === 'needs'} />
                ))}
              </div>
            </section>
          ) : null,
        )}
        {buckets.finished.length > 0 && (
          <section className="ov-sec">
            <h2 className="ov-h">
              Finished <span className="ov-count">{buckets.finished.length}</span>
            </h2>
            <div className="ov-list">
              {buckets.finished.map((j) => (
                <FinishedLine key={j.id} job={j} now={now} prs={runsOf(jobRuns, j.id).filter((r) => r.pr).length} />
              ))}
            </div>
          </section>
        )}
        {loaded && !total && (
          <div className="ov-empty">
            <Mark size={18} tight />
            <span>No jobs yet. A job runs one brief across many repositories: each in a worktree of its own, a canary first, one answer for every repository that asks the same thing, one review at the end.</span>
          </div>
        )}
      </div>
    </div>
  );
}

/** A request waiting on a job: what, who asked, and Allow / Allow for all / Deny. */
export function RequestRow({ jobId, group }: { jobId: string; group: RequestGroup }) {
  const first = group.asks[0];
  const [tool, ...rest] = group.label.split(': ');
  const what = rest.join(': ');
  const allowOne = () => first && respond(first.run.sessionId || '', first.pending.requestId, { behavior: 'allow', decisionClassification: 'user_temporary' });
  const allowAll = () => call('dispatch.allow', { id: jobId, key: group.key });
  const denyOne = () => first && respond(first.run.sessionId || '', first.pending.requestId, { behavior: 'deny', message: 'The user declined this action.', decisionClassification: 'user_reject' });
  const open = () => first?.run.sessionId && openSession(first.run.sessionId);
  return (
    <div className="dp-ask" onClick={(e) => e.stopPropagation()}>
      <span className="dp-ask-tool">{what ? tool : group.label}</span>
      {what && <span className="dp-ask-what mono ellipsis">{what}</span>}
      <span className="dp-ask-who faint">{askedBy(group.names)}</span>
      {group.interactive ? (
        <button className="btn perm primary" onClick={open}>
          Open to respond
        </button>
      ) : (
        <>
          <button className="btn perm primary" onClick={allowOne}>
            Allow
          </button>
          <button className="btn perm" onClick={allowAll} title="Answer every run of this job that asks the same thing, now and later">
            Allow for all{group.asks.length > 1 ? ` · ${group.asks.length}` : ''}
          </button>
          <button className="btn perm ghost" onClick={denyOne}>
            Deny
          </button>
        </>
      )}
    </div>
  );
}

/** The canary finished: read it and continue, with the forecast for the rest. */
export function CanaryRow({ job }: { job: DispatchJob }) {
  const jobRuns = useStore((s) => s.jobRuns);
  const names = job.learned.map((l) => l.name).join(', ') || 'the canary';
  const f = job.forecast;
  const openCanary = () => {
    const run = runsOf(jobRuns, job.id).find((r) => r.wave === 'canary' && r.sessionId);
    if (run?.sessionId) openSession(run.sessionId);
    else openJob(job.id);
  };
  return (
    <div className="dp-canary" onClick={(e) => e.stopPropagation()}>
      <span className="grow">
        Canary <b>{names}</b> finished. Read it and continue.
      </span>
      {f && (
        <span className="faint tnum">
          forecast {cost(f.cost)} · {f.minutes} min for {plural(f.runs, 'repo')}
        </span>
      )}
      <button className="btn sm ghost" onClick={openCanary}>
        Open canary
      </button>
      <button className="btn sm primary" onClick={() => call('dispatch.continue', { id: job.id }).catch(() => {})}>
        <Play size={12} /> Continue
      </button>
    </div>
  );
}

function JobCard({ job, now, detail }: { job: DispatchJob; now: number; detail: boolean }) {
  const jobRuns = useStore((s) => s.jobRuns);
  const groups = detail ? groupRequests(runsOf(jobRuns, job.id)).slice(0, 4) : [];
  return (
    <article className="ovc dp-card" data-card={job.id} onClick={() => openJob(job.id)} role="button" tabIndex={0}>
      <div className="dp-line">
        <span className="dp-name ellipsis">{job.name || 'Untitled job'}</span>
        <span className="dp-scope faint">
          {plural(job.total || job.targets?.length || 0, 'repo')} · {jobStateText(job, now)}
        </span>
        <SegmentBar job={job} />
        <span className="dp-counts tnum">{countsText(job)}</span>
        <span className="dp-cost tnum faint">{job.cost ? jobCostText(job) : ''}</span>
      </div>
      {groups.map((g) => (
        <RequestRow key={g.key} jobId={job.id} group={g} />
      ))}
      {detail && job.canaryWaiting && <CanaryRow job={job} />}
    </article>
  );
}

function FinishedLine({ job, now, prs }: { job: DispatchJob; now: number; prs: number }) {
  const c = job.counts || {};
  const failed = (c.failed || 0) + (c.stopped || 0);
  return (
    <button className="ovr dp-fin" onClick={() => openJob(job.id)}>
      <span className="ovr-main">
        <span className="ovr-title ellipsis">
          {job.name} · {plural(job.total || 0, 'repo')}
        </span>
      </span>
      <span className="ovr-end tnum">
        <span>
          {c.done || 0} done{failed ? ` · ${failed} failed` : ''}
          {prs ? ` · ${prs} PRs` : ''}
          {job.state === 'stopped' ? ' · stopped' : ''}
        </span>
        <span className="faint">{cost(job.cost || 0)}</span>
        <span className="faint">{elapsedText(job, now) || duration(0)}</span>
      </span>
    </button>
  );
}
