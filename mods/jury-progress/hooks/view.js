// Pure helpers for jury-progress: parse one run's events file (ai-jury.events.v1 NDJSON) and
// turn it into what the band and the pane draw. No I/O here; register.js does the reading.

export const SCHEMA = 'ai-jury.events.v1'
export const PHASES = ['review', 'debate', 'verify', 'synthesis']

// ai-jury names each run's file `<UTC start, to the ms>Z-<pid>[-n].ndjson` (events.run_file_name);
// nothing else in the directory is a run.
const RUN_NAME = /^\d{8}T\d{6}\.\d{3}Z-\d+(?:-\d+)?\.ndjson$/

export function isRunFile(name) {
  return RUN_NAME.test(name)
}

// One run's file: its start record, its steps in order, its end record (or null while it runs).
// A line that is not this schema's JSON (a torn last line while the run writes it) is skipped.
export function parseRun(text) {
  let start = null
  let end = null
  const steps = []
  for (const line of String(text ?? '').split('\n')) {
    if (!line.trim()) continue
    let rec
    try {
      rec = JSON.parse(line)
    } catch {
      continue
    }
    if (!rec || rec.schema !== SCHEMA) continue
    if (rec.event === 'start') start = rec
    else if (rec.event === 'step') steps.push(rec)
    else if (rec.event === 'end') end = rec
  }
  return start === null ? null : { start, steps, end }
}

// Where the run is now: the phase and round of its latest step (review before any step), and
// every seat of that phase with its state. Review and debate seat the whole panel; verify and
// synthesis list who has answered so far.
export function runState(run) {
  const last = run.steps[run.steps.length - 1]
  const phase = last?.phase ?? 'review'
  const round = last?.round ?? null
  const here = run.steps.filter((s) => s.phase === phase && (s.round ?? null) === round)
  const answered = new Map(here.map((s) => [s.agent, s]))
  const panel = (run.start.panel ?? []).map((p) => p.agent)
  const names = phase === 'review' || phase === 'debate' ? panel : [...answered.keys()]
  const seats = names.map((agent) => {
    const step = answered.get(agent)
    return { agent, state: step === undefined ? 'pending' : step.ok ? 'ok' : 'failed', step: step ?? null }
  })
  for (const [agent, step] of answered) {
    if (!names.includes(agent)) seats.push({ agent, state: step.ok ? 'ok' : 'failed', step })
  }
  return { phase, round, seats }
}

export function phaseLabel(phase, round) {
  return round != null && phase === 'debate' ? `${phase} r${round}` : phase
}

// "4m", "2h", "now": how long ago, compact. Null for a time in the future or not a number.
export function ago(ms) {
  if (!Number.isFinite(ms) || ms < 0) return null
  const s = Math.floor(ms / 1000)
  if (s < 10) return 'now'
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 48) return `${h}h`
  return `${Math.floor(h / 24)}d`
}

// The last path segment: a run's checkout named the way a person would.
export function baseName(path) {
  const parts = String(path ?? '').split('/').filter(Boolean)
  return parts[parts.length - 1] ?? ''
}

// The chair's verdict token as a word: markdown emphasis around it (`**COMMENT**`) dropped.
export function cleanVerdict(verdict) {
  return String(verdict ?? '').trim().replace(/^[*_`\s]+|[*_`\s]+$/g, '')
}

// Which way a verdict leans, for its color: approve, block, or anything else.
export function verdictTone(verdict) {
  const v = cleanVerdict(verdict).toUpperCase().replace(/[\s-]+/g, '_')
  if (/^(APPROVE|APPROVED|LGTM|PASS|READY)$/.test(v)) return 'good'
  if (/^(REQUEST_CHANGES|BLOCK|BLOCKED|REJECT|FAIL|NEEDS_INFO)$/.test(v)) return 'bad'
  return 'warn'
}

export function findingsText(n) {
  const k = n ?? 0
  return `${k} finding${k === 1 ? '' : 's'}`
}

// The finished line: verdict and findings, or how the run ended otherwise.
export function endText(end) {
  if (end.status === 'done') return `${cleanVerdict(end.verdict) || 'done'} · ${findingsText(end.findings)}`
  return end.status === 'cancelled' ? 'cancelled' : 'failed'
}

// The four phases as chips for a live run: each is done, the current one, or still to come.
// The current one carries its debate round.
export function phaseChips(run) {
  const { phase, round } = runState(run)
  const at = PHASES.indexOf(phase)
  // An ended run has no current phase: a finished one is done throughout; one that failed or was
  // cancelled is done up to the phase it stopped in, which is marked failed.
  const ended = run.end ?? null
  const here = ended === null ? 'current' : ended.status === 'done' ? 'done' : 'failed'
  return PHASES.map((name, i) => ({
    text: i === at ? phaseLabel(name, round) : name,
    state: i < at ? 'done' : i === at ? here : ended?.status === 'done' ? 'done' : 'todo',
  }))
}

// A seat as its state mark and name: ● answered, ✗ failed, ◌ still thinking.
const SEAT_MARK = { ok: '●', failed: '✗', pending: '◌' }

export function seatChip(seat) {
  return { text: `${SEAT_MARK[seat.state]} ${seat.agent}`, state: seat.state }
}


// What `$JURY_EVENTS_DIR` asks for, as ai-jury reads it (events.events_dir): null when unset or
// off, the path otherwise with a leading `~` expanded against `home`.
const OFF = new Set(['', 'off', '0', 'false', 'no'])

export function eventsDir(value, home) {
  if (value === undefined || value === null) return null
  const v = String(value).trim()
  if (OFF.has(v.toLowerCase())) return null
  if (v === '~') return home ?? v
  if (v.startsWith('~/') && home) return `${home}${v.slice(1)}`
  return v
}

// Where ai-jury keeps its cache (cache.default_cache_dir), the events directory's home when the
// mod chooses one: $JURY_CACHE_DIR, else $XDG_CACHE_HOME/ai-jury, else ~/.cache/ai-jury.
export function defaultEventsDir({ cacheDir, xdgCache, home }) {
  if (cacheDir) return `${cacheDir}/events`
  if (xdgCache) return `${xdgCache}/ai-jury/events`
  return home ? `${home}/.cache/ai-jury/events` : null
}

// `ps -o pid=,etime=` lines: pid -> when that process started (ms), from its elapsed time
// ([[dd-]hh:]mm:ss) counted back from `now`.
export function parsePs(stdout, now) {
  const started = new Map()
  for (const line of String(stdout ?? '').split('\n')) {
    const m = line.trim().match(/^(\d+)\s+(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)$/)
    if (!m) continue
    const [, pid, d, h, mi, se] = m
    const secs = ((Number(d ?? 0) * 24 + Number(h ?? 0)) * 60 + Number(mi)) * 60 + Number(se)
    started.set(Number(pid), now - secs * 1000)
  }
  return started
}

// 'ended' with an end record; 'live' while its process runs; 'stopped' when its pid is gone or
// now belongs to a process that started after the run did (a reused pid). Without an answer
// from ps (`alive` null) or a pid, a run without an end record counts as live.
export function runLiveness(run, alive) {
  if (run.end !== null) return 'ended'
  const pid = run.start.pid
  if (alive === null || !Number.isInteger(pid)) return 'live'
  const startedAt = alive.get(pid)
  if (startedAt === undefined) return 'stopped'
  const ts = run.start.ts
  // A process that started after the run did cannot be the one writing it. (Started before is
  // the ordinary case: the start record lands after config and diff are read.)
  if (Number.isFinite(ts) && startedAt > ts * 1000 + 60_000) return 'stopped'
  return 'live'
}

// https://github.com/<owner>/<repo> from a git remote URL (ssh or https), or null.
export function githubBase(remote) {
  const m = String(remote ?? '').trim().match(/(?:^|[@/])github\.com[:/]([^/\s]+)\/([^/\s]+?)(?:\.git)?\/?$/)
  return m ? `https://github.com/${m[1]}/${m[2]}` : null
}

// Where a run's target lives on GitHub: `PR #N` and `issue #N` link there; a local diff, a
// commit or a range has no page of its own.
export function targetUrl(base, target) {
  if (!base) return null
  const pr = String(target ?? '').match(/^PR #(\d+)$/)
  if (pr) return `${base}/pull/${pr[1]}`
  const issue = String(target ?? '').match(/^issue #(\d+)$/)
  return issue ? `${base}/issues/${issue[1]}` : null
}

const SEVERITY_ORDER = ['critical', 'major', 'minor', 'nit', 'info']

// "1 major, 2 minor" from a step's severity counts; empty when it has none.
export function severityText(counts) {
  if (!counts || typeof counts !== 'object') return ''
  const known = SEVERITY_ORDER.filter((k) => counts[k] > 0).map((k) => `${counts[k]} ${k}`)
  const other = Object.keys(counts).filter((k) => !SEVERITY_ORDER.includes(k) && counts[k] > 0).map((k) => `${counts[k]} ${k}`)
  return [...known, ...other].join(', ')
}

// A seat's model: what its step says it sent, else what the panel says it was asked for.
export function seatModel(run, agent, step) {
  if (step?.model) return step.model
  return (run.start.panel ?? []).find((p) => p.agent === agent)?.model ?? null
}

// A repository's name from its remote URL (`git@host:owner/name.git`, `https://host/owner/name`),
// else the checkout folder's own name.
export function repoName(remote, cwd) {
  const m = String(remote ?? '').trim().match(/([^/:\s]+?)(?:\.git)?\/?$/)
  return m ? m[1] : baseName(cwd) || null
}
