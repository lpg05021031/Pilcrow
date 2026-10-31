// Shared client-side types for data coming from the Pilcrow server.

export type SessionStatus = 'idle' | 'starting' | 'running' | 'requires_action' | 'error' | 'stopped';
export type PermissionMode = 'default' | 'acceptEdits' | 'plan' | 'bypassPermissions' | 'dontAsk' | 'auto';
export type Effort = '' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';

export interface Worktree {
  path: string;
  branch: string;
  base: string;
  repoRoot: string;
}

export interface SessionSummary {
  id: string;
  title: string;
  firstPrompt?: string;
  cwd: string;
  project: string;
  gitBranch?: string | null;
  lastModified?: number;
  createdAt?: number;
  archived: boolean;
  pinned: boolean;
  worktree: Worktree | null;
  status: SessionStatus;
  live: boolean;
  pending?: number;
  /** Started by a schedule; such sessions live in the inbox rather than the sidebar. */
  scheduled?: { id: string; name: string; at: number; manual?: boolean } | null;
  /** A run of a dispatch job (or its report session); reachable from the job's view. */
  dispatch?: { jobId: string; runId: string | null; name: string; target: string } | null;
  /** A helper of another session (HELPERS.md): nested under its lead in the sidebar. */
  helper?: { leadId: string; name: string; state: HelperStateName | null } | null;
  /** A session that has started helpers. */
  lead?: { since: number; count: number } | null;
}

export interface PermissionSuggestion {
  type: string;
  rules?: { toolName: string; ruleContent?: string }[];
  behavior?: string;
  destination?: string;
  mode?: PermissionMode;
  directories?: string[];
}

export interface PermissionRequest {
  requestId: string;
  toolName: string;
  input: ToolInput;
  toolUseId?: string;
  title?: string;
  displayName?: string;
  description?: string;
  decisionReason?: string;
  blockedPath?: string;
  suggestions: PermissionSuggestion[];
  agentId?: string;
  defaultToNo?: boolean;
  suppressAlwaysAllowRule?: boolean;
  interactive: boolean;
  at: number;
}

export interface TaskInfo {
  id: string;
  toolUseId?: string;
  description?: string;
  type?: string;
  subagentType?: string;
  background?: boolean;
  status: string;
  summary?: string;
  outputFile?: string;
  usage?: { total_tokens: number; tool_uses: number; duration_ms: number };
  lastTool?: string;
  progress?: string;
  startedAt?: number;
  endedAt?: number;
  runId?: string;
  parentTaskId?: string;
  workflowName?: string;
  ownedBySubagent?: boolean;
  ambient?: boolean;
  reason?: string;
  workflow?: WorkflowRun;
}

/** One agent inside a workflow run, as Claude Code reports it through task_progress. */
export interface WorkflowAgent {
  type: 'workflow_agent';
  index: number;
  label?: string;
  phaseIndex?: number;
  phaseTitle?: string;
  agentId?: string;
  agentType?: string;
  isolation?: 'worktree' | 'remote';
  model?: string;
  fallbackModel?: string;
  state: 'start' | 'progress' | 'done' | 'error';
  queuedAt?: number;
  startedAt?: number;
  lastProgressAt?: number;
  attempt?: number;
  lastAttemptReason?: string;
  lastToolName?: string;
  lastToolSummary?: string;
  promptPreview?: string;
  resultPreview?: string;
  cached?: boolean;
  blocked?: boolean;
  error?: string;
  toolUseCount?: number;
  totalTokens?: number;
  firstSeenAt?: number;
  updatedAt?: number;
}
export interface WorkflowPhase {
  index: number;
  title: string;
  kind?: string;
}
export interface WorkflowRun {
  phases: (WorkflowPhase | undefined)[];
  agents: (WorkflowAgent | undefined)[];
  logs: { at: number; message: string }[];
  updatedAt?: number;
}

export interface SlashCommandInfo {
  name: string;
  description: string;
  argumentHint?: string;
  aliases?: string[];
  builtin?: boolean;
}

export interface ModelInfo {
  value: string;
  resolvedModel?: string;
  displayName: string;
  description: string;
  supportsEffort?: boolean;
  supportedEffortLevels?: Effort[];
}

/** A message typed while Claude was busy; Pilcrow holds it until the turn ends. */
export interface HeldMsg {
  uuid: string;
  text: string;
  images: number;
  files: string[];
  at: number;
  /** A message Pilcrow wrote (a helper's news to its lead), not the person's. */
  note?: string | null;
  /** Who sent it when not the person ('lead'). */
  from?: string | null;
}

// ---- helpers: a session that leads other sessions (server/helpers.js, HELPERS.md)
export type HelperStateName = 'queued' | 'preparing' | 'running' | 'needs_action' | 'done' | 'failed' | 'stopped' | 'skipped';
export interface HelperState {
  name: string;
  id: string;
  cwd: string;
  branch: string | null;
  worktree: string | null;
  state: HelperStateName;
  since: number;
  elapsedMs: number;
  cost: number;
  files: number;
  add: number;
  del: number;
  pending: number;
  lastLine: string;
  after: string[];
  reason?: string;
  finished?: 'commit' | 'discard' | 'keep';
  commit?: string;
  pr?: string;
}
export interface LeadState {
  since: number | null;
  cost: number;
  caps: { total: number; parallel: number; maxMinutes: number; costUsd: number };
  rules: string[];
}
export interface HelperOf {
  leadId: string;
  leadTitle: string;
  name: string;
  state: HelperStateName | null;
}

export interface SessionState {
  unattended?: boolean;
  unattendedDenials?: number;
  activity?: Activity | null;
  lastActivity?: number;
  turnStartedAt?: number | null;
  processStartedAt?: number | null;
  lastReply?: { text: string; at: number } | null;
  id: string;
  status: SessionStatus;
  live: boolean;
  cwd: string;
  mode: PermissionMode;
  model: string;
  effort: Effort;
  activeModel: string | null;
  usage: { contextTokens: number; contextWindow: number; costUsd: number; outputTokens: number; model: string | null };
  mcp?: McpLive[];
  error: string | null;
  suggestion: string | null;
  compacting: boolean;
  pending: PermissionRequest[];
  tasks: TaskInfo[];
  bgTasks: { task_id: string; task_type: string; description: string; subagent_type?: string }[];
  queued: string[];
  held: HeldMsg[];
  /** A lead's helpers and its own numbers (HELPERS.md §5.11); a helper's lead. */
  helpers?: HelperState[];
  lead?: LeadState;
  helper?: HelperOf;
  init: null | {
    version: string;
    tools: string[];
    mcpServers: { name: string; status: string }[];
    slashCommands: string[];
    agents?: string[];
    skills?: string[];
    outputStyle?: string;
    apiKeySource?: string;
    model: string;
    capabilities?: string[];
    plugins?: { name: string }[];
    effort?: string | null;
  };
  commands: SlashCommandInfo[] | null;
  models: ModelInfo[] | null;
}

export interface AppInfo {
  version: string;
  home: string;
  user: string;
  displayName?: string;
  hostname: string;
  platform: string;
  node: string;
  shell: string;
  isRoot: boolean;
  configDir: string;
  claude: {
    path: string | null;
    source: string | null;
    version: string | null;
    bundled: { path: string; version: string } | null;
    system: { path: string; version: string } | null;
  };
  terminal: { python: string | null; script: string | null; pty: boolean };
  git: boolean;
  gateway: { baseUrl?: string | null; hasToken?: boolean; loggedIn?: boolean; provider?: string; source?: string | null; model?: string | null; error?: string };
}

export interface Config {
  theme: 'system' | 'dark' | 'light';
  fontSize: number;
  sendKey: 'enter' | 'mod-enter';
  claudeBinary: string;
  defaultModel: string;
  defaultMode: PermissionMode;
  defaultEffort: Effort;
  worktreeRoot: string;
  idleTimeoutMin: number;
  notifications: boolean;
  promptSuggestions: boolean;
  showThinking: boolean;
  timeline?: boolean;
  memoryAuto?: boolean;
  memoryInject?: boolean;
  memoryModel?: string;
  recentProjects: string[];
  repos?: RepoEntry[];
  helpers?: HelpersConfig;
  ui: Record<string, unknown>;
}

/** The repositories registry (REPOSITORIES.md): one entry the person maintains per repository. */
export type RepoPolicy = 'allowed' | 'ask' | 'never';
export interface RepoEntry {
  id: string;
  path: string;
  name: string;
  aliases: string[];
  about: string;
  policy: RepoPolicy;
  defaults: { mode?: 'default' | 'acceptEdits' | 'plan'; worktree?: boolean; base?: string; prepare?: string; verify?: string; model?: string };
  notes: string;
  deps: string[];
  facts: { manifest?: string | null; packageName?: string | null; isRepo?: boolean; branch?: string | null; dirty?: boolean; hasClaudeMd?: boolean; seenAt?: number; missing?: boolean };
  addedAt: number;
  machine: string;
  missing?: boolean;
}
export interface RepoScanHit {
  path: string;
  name: string;
  packageName: string | null;
  kind: string | null;
  branch: string | null;
  registered: boolean;
}
export interface HelpersConfig {
  enabled: boolean;
  total: number;
  parallel: number;
  maxMinutes: number;
  costUsd: number;
  wakeLead: boolean;
}

export interface DiffLine {
  t: '+' | '-' | ' ';
  s: string;
  o?: number;
  n?: number;
  eof?: boolean;
}
export interface DiffHunk {
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  header?: string;
  lines: DiffLine[];
}
/** One point on a session's timeline: the folder as it was after a step. */
export interface TimelineStep {
  n: number;
  at: number;
  kind: 'start' | 'message' | 'tool' | 'external' | 'restore';
  tool: string | null;
  toolUseId: string | null;
  uuid: string | null;
  label: string;
  commit: string | null;
  changed: boolean;
  files: { path: string; status: string; add: number; del: number; binary?: boolean }[];
  add: number;
  del: number;
}
export interface Timeline {
  cwd: string;
  enabled: boolean;
  reason: string | null;
  steps: TimelineStep[];
}

/** What Claude is doing right now, kept by the server so overviews need not load the transcript. */
export interface Activity {
  kind: 'tool' | 'text' | 'thinking';
  tool?: string;
  label: string;
  at: number;
}

export interface MemoryNote {
  id: string;
  at: number;
  text: string;
  kind: 'auto' | 'manual';
  sessionId: string | null;
  sessionTitle: string | null;
  pinned: boolean;
  editedAt?: number;
}
/** One session's touch on a file, from the timeline. */
export interface FileTouch {
  sessionId: string;
  title: string;
  about: string | null;
  at: number;
  n: number;
  toolUseId: string | null;
  kind: string;
  add: number;
  del: number;
}

export interface Cadence {
  kind: 'daily' | 'weekdays' | 'weekly' | 'hourly' | 'interval' | 'cron';
  time?: string;
  weekday?: number;
  everyHours?: number;
  cron?: string;
}
export interface Schedule {
  id: string;
  /** 'prompt' runs a prompt in a folder; 'dispatch' runs a playbook across its repositories. */
  kind?: 'prompt' | 'dispatch';
  playbookId?: string | null;
  playbookName?: string | null;
  name: string;
  cwd: string;
  prompt: string;
  cadence: Cadence;
  model: string;
  effort: string;
  mode: PermissionMode;
  worktree: boolean;
  maxMinutes: number;
  enabled: boolean;
  createdAt: number;
  lastRunAt: number | null;
  lastStatus: 'completed' | 'failed' | 'stopped' | null;
  runs: number;
  nextRunAt: number | null;
  cadenceLabel: string;
  error: string | null;
  running: { sessionId: string | null; jobId?: string | null; startedAt: number } | null;
}
export interface InboxItem {
  id: string;
  /** A dispatch job's report (`dispatch`), or a lead's helpers once they are all done (`helpers`). */
  kind?: 'dispatch' | 'helpers';
  jobId?: string;
  dispatch?: DispatchReport;
  leadId?: string;
  title?: string;
  helpers?: (HelperState & { handoff: string })[];
  scheduleId: string | null;
  scheduleName: string;
  sessionId: string | null;
  cwd: string;
  startedAt: number;
  endedAt: number;
  durationMs: number;
  status: 'completed' | 'failed' | 'stopped';
  reason: string | null;
  summary: string;
  cost: number;
  files: { path: string; add: number; del: number }[];
  read: boolean;
  manual?: boolean;
}

// ---- dispatch: one job across many repositories (server/dispatch_core.js)
export type RunState = 'queued' | 'preparing' | 'running' | 'needs_action' | 'done' | 'failed' | 'stopped' | 'skipped';
export type JobState = 'draft' | 'planned' | 'running' | 'reviewing' | 'finished' | 'stopped';
export interface DispatchPending {
  requestId: string;
  toolName: string;
  displayName: string | null;
  key: string;
  label: string;
  interactive: boolean;
  at: number;
}
export interface DispatchRun {
  id: string;
  jobId: string;
  path: string;
  name: string;
  base: string;
  size: string | null;
  level: number;
  dependsOn: string[];
  wave: 'canary' | 'main';
  state: RunState;
  attempt: number;
  sessionId: string | null;
  worktree: { path: string; branch: string; base: string; repoRoot: string } | null;
  startedAt: number | null;
  endedAt: number | null;
  durationMs: number;
  cost: number;
  summary: string;
  files: { path: string; add: number; del: number }[];
  add: number;
  del: number;
  reason: string | null;
  verify: { ran: boolean; code: number; tail: string } | null;
  now: string | null;
  pending: DispatchPending[];
  commit: string | null;
  pr: string | null;
  finished: 'commit' | 'discard' | 'keep' | null;
  superseded?: string;
}
export interface DispatchSettings {
  mode: 'ask' | 'acceptEdits' | 'allowlist';
  allowedTools: string[];
  model: string;
  effort: string;
  parallel: number;
  maxMinutes: number;
  costCap: number;
  canary: number;
  learn: boolean;
  prepare: string;
  copy: string[];
  verify: string;
  verifyRetry: boolean;
  commitMessage: string;
  pr: boolean;
  report: 'table' | 'agent';
  unattended?: boolean;
  branch?: string;
}
export interface DispatchTarget {
  path: string;
  name: string;
  base: string;
  notes: string;
  size?: string | null;
  deps?: string[];
}
export interface DispatchPlanTarget {
  path: string;
  name: string;
  base: string;
  verdict: 'ok' | 'warning' | 'blocked';
  notes: { kind: 'blocked' | 'warning' | 'note'; text: string }[];
  brief: string;
  level: number;
  deps: string[];
  size: string | null;
}
export interface DispatchPlan {
  at: number;
  targets: DispatchPlanTarget[];
  levels: number;
  worst: { minutes: number; cost: number | null; runs: number; levels: number };
  ok: number;
  warnings: number;
  blocked: number;
}
export interface DispatchRule {
  key: string;
  label: string | null;
  at: number;
  count: number;
}
export interface DispatchReportTarget {
  name: string;
  path: string;
  state: RunState;
  reason: string | null;
  summary: string;
  files: number;
  add: number;
  del: number;
  cost: number;
  durationMs: number;
  branch: string | null;
  commit: string | null;
  pr: string | null;
  finished: 'commit' | 'discard' | 'keep' | null;
  verify: { code: number } | null;
}
export interface DispatchReport {
  at: number;
  targets: DispatchReportTarget[];
  rules: DispatchRule[];
  plan: { ok: number; warnings: number; blocked: number; levels: number } | null;
  cost: number;
  durationMs: number;
  prose?: string | null;
  proseState?: 'writing' | 'done' | 'failed';
  proseError?: string | null;
  proseCost?: number;
}
export interface DispatchJob {
  id: string;
  name: string;
  slug: string;
  brief: string;
  root: string;
  targets: DispatchTarget[];
  settings: DispatchSettings;
  state: JobState;
  wave: 'canary' | 'main' | 'done' | null;
  paused: boolean;
  createdAt: number;
  updatedAt: number;
  startedAt: number | null;
  finishedAt: number | null;
  plan: DispatchPlan | null;
  rules: DispatchRule[];
  learned: { name: string; summary: string }[];
  forecast: { cost: number; minutes: number; from: string[]; runs: number } | null;
  capReached: boolean;
  report: DispatchReport | null;
  stoppedReason: string | null;
  playbookId: string | null;
  scheduleId: string | null;
  /** From the summary the server sends: counts per state, the total, cost, requests waiting. */
  counts: Partial<Record<RunState, number>>;
  total: number;
  cost: number;
  needsAction: number;
  files: number;
  canaryWaiting: boolean;
}
export interface DispatchDelta {
  id: string;
  removed?: boolean;
  job?: DispatchJob;
  runs?: DispatchRun[];
  all?: boolean;
}
export interface Playbook {
  id: string;
  name: string;
  brief: string;
  root: string;
  settings: DispatchSettings;
  targets: DispatchTarget[];
  createdAt: number;
  updatedAt: number;
  uses: number;
  lastUsedAt: number | null;
}
export interface WorkspaceRepo {
  path: string;
  name: string;
  branch: string | null;
  dirty: boolean;
  changed: number;
  untracked: number;
  ahead: number;
  behind: number;
  remote: { host: string; owner?: string; repo?: string; url?: string } | null;
  hasClaudeMd: boolean;
  size: 'small' | 'medium' | 'large';
  files: number;
  kind: string | null;
  packageName: string | null;
  lastSession: number | null;
  deps: string[];
  /** what the registry (REPOSITORIES.md) knows about it, when it is registered */
  registered?: boolean;
  registryId?: string;
  registryName?: string;
  about?: string;
  policy?: RepoPolicy;
  notes?: string;
}
export interface DispatchReview {
  id: string;
  runs: { runId: string; name: string; files: { path: string; add: number; del: number }[]; add: number; del: number }[];
  same: { sig: string; path: string; runs: string[]; add: number; del: number; preview: string[] }[];
  differ: { name: string; files: { path: string; add: number; del: number }[] }[];
  files: number;
}

export interface DiffFile {
  path: string;
  oldPath: string | null;
  status: 'M' | 'A' | 'D' | 'R';
  binary: boolean;
  additions: number;
  deletions: number;
  hunks: DiffHunk[];
  truncated: boolean;
  untracked?: boolean;
}

export interface GitInfo {
  isRepo: boolean;
  root?: string;
  mainRoot?: string;
  isWorktree?: boolean;
  branch?: string | null;
  head?: string | null;
  upstream?: string | null;
  ahead?: number;
  behind?: number;
  changed?: number;
  untracked?: number;
  insertions?: number;
  deletions?: number;
}

/**
 * What Claude Code records about a tool's outcome beside the text result (`toolUseResult` in the
 * transcript). The shape depends on the tool; these are the fields Pilcrow reads.
 */
export interface StructuredResult {
  type?: string;
  filePath?: string;
  oldString?: string;
  newString?: string;
  structuredPatch?: PatchHunk[];
  file?: { filePath?: string; content?: string; numLines?: number; totalLines?: number; startLine?: number };
  stdout?: string;
  stderr?: string;
  interrupted?: boolean;
  isAsync?: boolean;
  backgroundTaskId?: string;
  returnCodeInterpretation?: string;
  content?: unknown;
  truncated?: boolean;
  numFiles?: number;
  filenames?: string[];
  gitOperation?: { commit?: string };
  agentId?: string;
  usage?: unknown;
  task?: unknown;
  plan?: string;
  id?: string;
  answers?: Record<string, string>;
  [k: string]: unknown;
}

export interface PatchHunk {
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  lines: string[];
}

/** fs.read: a text file, a binary one (with a data URL when small enough to show), or one too large. */
export interface FileRead {
  path: string;
  size: number;
  mtime: number;
  text?: string;
  binary?: boolean;
  tooLarge?: boolean;
  mime?: string;
  dataUrl?: string;
}
export interface FileWrite {
  ok: boolean;
  conflict?: boolean;
  mtime?: number;
}
/** Live MCP status as reported by a running session, or the summary from session start. */
export type McpStatus = McpLive | { name: string; status: string; tools?: number | unknown[]; error?: string; serverInfo?: { name: string; version: string } };

/** The answer to a permission request, as the Agent SDK takes it. */
export interface PermissionDecision {
  behavior: 'allow' | 'deny';
  message?: string;
  updatedInput?: ToolInput;
  updatedPermissions?: unknown[];
  decisionClassification?: string;
}

/** A tool's input as the model wrote it: a JSON object whose keys depend on the tool. */
export type ToolInput = Record<string, unknown>;

export interface TokenUsage {
  input_tokens?: number;
  output_tokens?: number;
  cache_read_input_tokens?: number;
  cache_creation_input_tokens?: number;
}

/** One block of an assistant or user message, as the API shapes it. */
export interface ContentBlock {
  type: string;
  id?: string;
  name?: string;
  text?: string;
  thinking?: string;
  input?: ToolInput;
  content?: string | ContentBlock[];
  tool_use_id?: string;
  is_error?: boolean;
  tool_name?: string;
  title?: string;
  url?: string;
  source?: { type?: string; media_type?: string; data?: string };
}

export interface StreamEvent {
  type: string;
  index?: number;
  message?: { id?: string };
  content_block?: ContentBlock;
  delta?: { type?: string; text?: string; thinking?: string; partial_json?: string };
}

/**
 * Raw messages as produced by the Agent SDK (live) or getSessionMessages (history). The fields are
 * the ones Pilcrow reads; the SDK sends more, and `type`/`subtype` say which apply.
 */
export interface RawMsg {
  type: string;
  uuid?: string;
  subtype?: string;
  parent_tool_use_id?: string | null;
  session_id?: string;
  timestamp?: string;
  message?: { id?: string; role?: string; model?: string; content?: string | ContentBlock[]; usage?: TokenUsage } | string;
  event?: StreamEvent;
  content?: string;
  text?: string;
  level?: string;
  priority?: string;
  estimated_tokens?: number;
  error?: unknown;
  error_status?: number;
  is_error?: boolean;
  is_meta?: boolean;
  isMeta?: boolean;
  isSynthetic?: boolean;
  isCompactSummary?: boolean;
  result?: string;
  errors?: string[];
  duration_ms?: number;
  total_cost_usd?: number;
  num_turns?: number;
  usage?: TokenUsage;
  terminal_reason?: string;
  tool_use_id?: string;
  tool_use_result?: unknown;
  tool_name?: string;
  decision_reason?: string;
  summary?: string;
  task_id?: string;
  status?: string;
  patch?: { status?: string };
  output_file?: string;
  origin?: string | { kind?: string };
  preceding_tool_use_ids?: string[];
  compact_metadata?: { trigger?: string; pre_tokens?: number; post_tokens?: number };
  rate_limit_info?: { status?: string; rateLimitType?: string; resetsAt?: number; utilization?: number; [k: string]: unknown };
  attempt?: number;
  max_retries?: number;
  elapsed_time_seconds?: number;
  id?: string;
  _local?: boolean;
  _note?: string;
  _from?: string;
}

/** Models the API endpoint offers (GET /v1/models), as Pilcrow's server read them. */
export interface GatewayModel {
  id: string;
  displayName: string | null;
  created: number | null;
}
export interface GatewayModels {
  ok: boolean;
  base?: string;
  /** The list URL that answered. */
  url?: string;
  models?: GatewayModel[];
  error?: string;
  unsupported?: boolean;
  noCredential?: boolean;
  fetchedAt?: number;
}


// ---- skills & MCP management
export interface SkillInfo {
  name: string;
  dirName: string;
  description: string;
  scope: 'user' | 'project' | 'plugin';
  dir: string;
  file: string;
  mtime: number;
  files: number;
  hasScripts: boolean;
  body: string;
  plugin?: string;
}
export interface SkillsList {
  roots: { user: string; project: string | null };
  user: SkillInfo[];
  project: SkillInfo[];
  plugin: SkillInfo[];
}
export interface McpConfigSummary {
  type: 'http' | 'sse' | 'stdio';
  url?: string;
  headers?: Record<string, string>;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
}
export interface McpEntry {
  name: string;
  scope: 'user' | 'project' | 'local';
  source: string;
  config: McpConfigSummary;
  raw: unknown;
  /** Project (.mcp.json) servers: approved for this user, rejected, or not decided yet. */
  approved?: boolean | null;
}
export interface McpList {
  file: string;
  servers: McpEntry[];
}
/** Live status of a server in a running Claude Code process. */
export interface McpLive {
  name: string;
  status: 'connected' | 'failed' | 'needs-auth' | 'pending' | 'disabled' | string;
  error?: string;
  scope?: string;
  tools: number | { name: string; description?: string }[];
  serverInfo?: { name: string; version: string };
}
