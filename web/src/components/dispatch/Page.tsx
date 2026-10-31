import { useEffect } from 'react';
import { useStore, loadJobs } from '../../lib/store';
import { Board } from './Board';
import { JobView } from './JobView';
import { Composer } from './Composer';

/** The Dispatch page: the board, a job's view or the composer, by `dispatchView`. */
export function DispatchPage() {
  const view = useStore((s) => s.dispatchView);
  useEffect(() => {
    loadJobs();
  }, []);
  if (view === 'new') return <Composer key="new" />;
  if (view) return <JobView key={view} id={view} />;
  return <Board />;
}
