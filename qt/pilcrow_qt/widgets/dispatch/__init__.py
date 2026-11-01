"""Dispatch: one job across many repositories. The board (every job at a glance), a job's view (one
row per repository, on the canvas engine), and the composer (a new job: brief, targets, settings, the
plan as you type). The server's state machine is server/dispatch_core.js; the client shows it and
patches its rows from ``dispatch.changed`` deltas."""
