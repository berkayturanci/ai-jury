// jury-progress: a read-only window on the live ai-jury runs, inside Claude Code.
//
// ai-jury writes each run's progress (who spoke, in which phase, whether it worked, how long it
// took, how many findings) as metadata-only NDJSON to a file of the run's own in
// $JURY_EVENTS_DIR, or, when no such variable is set, to ai-jury's cache directory plus /events
// while a `.watched` marker there reads `on`. This mod leaves that marker, so every `jury` on the
// machine (by hand, by an agent, through keel) writes where it reads. It draws:
//   - one line per live run above the prompt: the target, the phase bar, each seat's state
//   - a `/jury-progress` pane with every phase of a run, seat by seat
//   - a toast when a run ends (verdict and findings) or stops without finishing
// It never runs `jury`; it reads the events files and `ps`, and writes only the `.watched` marker.

import { PHASES, ago, baseName, cleanVerdict, defaultEventsDir, endText, eventsDir, findingsText, githubBase, isRunFile, parsePs, parseRun, phaseChips, phaseLabel, repoName, runLiveness, runState, seatChip, seatModel, severityText, targetUrl, verdictTone, withinFolder } from './view.js'

const PANE = 'jury-progress'
// The file that tells ai-jury (1.24.0+) someone watches its cache's events directory.
const WATCH_MARKER = '.watched'
// A scan is a directory listing and the files that changed (ps only while a run has no end
// record): cheap enough to run every second, so a new run or step shows within about one.
const POLL_MS = 1_000
// With no live run the timer still ticks every poll but reads only every IDLE_EVERY ticks.
const IDLE_EVERY = 2
// A run file untouched this long is not read at all.
const FRESH_MS = 24 * 60 * 60 * 1000
// A finished run stays in the band this long, so its verdict is seen.
const RECENT_MS = 60 * 1000
// How many runs the pane lists (live ones first, then the most recent finished).
const PANE_MAX = 10
const BAND_MAX = 3
const PS_TIMEOUT_MS = 5_000

// Text colors are the terminal's own (they follow its theme); a chip is white on a saturated
// background, which reads on a dark and a light theme alike.
const TONES = {
  title: { bold: true },
  ok: { color: 'green' },
  bad: { color: 'red' },
  wait: { color: 'yellow' },
  dim: { dimColor: true },
  plain: {},
  // phase chips: a segmented bar, done green, current blue, to come dim
  done: { backgroundColor: '#2D7D46', color: '#FFFFFF' },
  current: { backgroundColor: '#1F6FEB', color: '#FFFFFF', bold: true },
  todo: { backgroundColor: '#6E7681', color: '#FFFFFF' },
  // verdict chips
  good: { backgroundColor: '#2D7D46', color: '#FFFFFF', bold: true },
  block: { backgroundColor: '#B62324', color: '#FFFFFF', bold: true },
  warn: { backgroundColor: '#9A6700', color: '#FFFFFF', bold: true },
}
const BORDER = '#6E7681'
const SEAT_TONE = { ok: 'ok', failed: 'bad', pending: 'wait' }
const VERDICT_TONE = { good: 'good', bad: 'block', warn: 'warn' }
const BALLOT_TONE = { good: 'ok', bad: 'bad', warn: 'wait' }

const settings = { pollMs: POLL_MS, bandMax: BAND_MAX, notify: true, capture: true, allSessions: false }

let dir = null // the events directory read, or null
let dirSource = null // 'env' (the session's own $JURY_EVENTS_DIR), 'mod' (set here), 'off'
let home = null
let cwd = null
let cwdReal = null // the session's folder with symlinks resolved (/tmp is /private/tmp on macOS)
let all = [] // [{ name, run, state, mtimeMs }] newest first: state 'live' | 'ended' | 'stopped'
let cache = new Map() // name -> { mtimeMs, size, run }: a file is parsed again only when it changed
let previous = null // name -> state at the last scan; null before the first
let firstScanAt = null // when the first scan ran: a run started after it is this session's to announce
let scanAt = 0
let listError = null // why the directory could not be listed, if it could not
let markerError = null // why the .watched marker could not be left, if it could not
let inFlight = null
let again = false
let idleTicks = 0
let poller = null
let selected = null // the run (file name) the pane shows in full
// A run's checkout -> { base: its GitHub URL or null, name: the repository's name, branch }, read
// once with git: the band labels a run by its repository, not the worktree folder's name.
const repoBases = new Map()

function tick($) {
  if (!all.some((r) => r.state === 'live')) {
    idleTicks = (idleTicks + 1) % IDLE_EVERY
    if (idleTicks !== 0) return undefined
  }
  return refresh($, false)
}

// One scan at a time; a caller that needs a read taken after something it just did (a jury
// command, the pane, a button) gets one more once the running one ends.
function refresh($, fresh) {
  if (dir === null) return Promise.resolve()
  if (inFlight !== null) {
    if (fresh) again = true
    return inFlight
  }
  inFlight = (async () => {
    try {
      do {
        again = false
        await scan($)
      } while (again)
    } catch (err) {
      listError = String(err?.message ?? err)
    } finally {
      inFlight = null
      $.ui.invalidate('ui.render')
    }
  })()
  return inFlight
}

// Which of these pids still run, each with when its process started (ms). Null when `ps` could
// not say: then no run is called stopped.
async function alivePids($, pids, now) {
  if (pids.length === 0) return new Map()
  try {
    const run = await $.process.run(['ps', '-o', 'pid=,etime=', '-p', pids.join(',')], { timeoutMs: PS_TIMEOUT_MS })
    // ps exits 1, silently, when none of the pids runs: that is an answer too. Anything on
    // stderr (a ps without -p or etime) is not.
    if (run.stderr.trim() !== '' || (run.exitCode !== 0 && (run.exitCode !== 1 || run.stdout.trim() !== ''))) return null
    return parsePs(run.stdout, now)
  } catch {
    return null
  }
}

async function gitOut($, at, args) {
  try {
    const run = await $.process.run(['git', ...args], { cwd: at, timeoutMs: PS_TIMEOUT_MS })
    return run.exitCode === 0 ? run.stdout.trim() : null
  } catch {
    return null // no git, or the checkout is gone
  }
}

const REPO_RETRY_MS = 60_000
const REPO_RETRY_MAX_MS = 30 * 60_000
const REPO_REFRESH_MS = 5 * 60_000

// A read that failed keeps what an earlier one learned (a worktree keel removed after the merge
// still shows under its repository's name and link) and is tried again later and later: one
// minute, then twice that, up to half an hour.
function mergeInfo(was, got, now) {
  if (!got.failed) return { ...got, at: now }
  const retryMs = was?.failed ? Math.min(was.retryMs * 2, REPO_RETRY_MAX_MS) : REPO_RETRY_MS
  if (was && !was.failed) return { ...was, failed: true, retryMs, at: now }
  return { ...(was ?? got), failed: true, retryMs, at: now }
}

async function checkoutInfo($, at) {
  const [remote, branch] = await Promise.all([gitOut($, at, ['remote', 'get-url', 'origin']), gitOut($, at, ['rev-parse', '--abbrev-ref', 'HEAD'])])
  return {
    base: remote ? githubBase(remote) : null,
    name: repoName(remote, at),
    branch: branch && branch !== 'HEAD' ? branch : null,
    // No origin read (git failed, or the checkout has none): cheap to ask again in a minute.
    failed: remote === null,
  }
}

function urlOf(r) {
  return targetUrl(repoBases.get(r.run.start.cwd)?.base ?? null, r.run.start.target)
}

// How a run is named in the band: its repository; with several runs of one repository on screen,
// its branch too, so they can be told apart.
function runLabel(r, shown) {
  const info = repoBases.get(r.run.start.cwd)
  const nameOf = (x) => repoBases.get(x.run.start.cwd)?.name ?? (baseName(x.run.start.cwd) || '?')
  const name = nameOf(r)
  const twins = shown.filter((x) => nameOf(x) === name && x.run.start.cwd !== r.run.start.cwd)
  return twins.length > 0 && info?.branch ? `${name} · ${info.branch}` : name
}

async function scan($) {
  const now = await $.clock.now()
  scanAt = now
  let entries
  try {
    entries = await $.fs.list(dir)
    listError = null
  } catch (err) {
    // No directory yet is the ordinary start: no jury has run since the variable was set.
    entries = []
    listError = /ENOENT|no such/i.test(String(err?.message ?? err)) ? null : String(err?.message ?? err)
  }
  const files = entries
    .filter((e) => e.kind === 'file' && isRunFile(e.name) && now - e.mtimeMs < FRESH_MS)
    .sort((a, b) => (a.name < b.name ? 1 : a.name > b.name ? -1 : 0))
  const nextCache = new Map()
  const read = []
  for (const f of files) {
    // Size as well as mtime: a filesystem with a coarse mtime can append without moving it.
    const known = cache.get(f.name)
    let run = known && known.mtimeMs === f.mtimeMs && known.size === f.size ? known.run : undefined
    if (run === undefined) {
      try {
        run = parseRun(await $.fs.read(`${dir}/${f.name}`))
      } catch {
        run = null // pruned between the list and the read, or unreadable: skip it this time
      }
      // A file listed before its start record landed parses to nothing: read it again next time.
      if (run !== null) nextCache.set(f.name, { mtimeMs: f.mtimeMs, size: f.size, run })
    } else nextCache.set(f.name, known)
    if (run !== null) read.push({ name: f.name, run, mtimeMs: f.mtimeMs })
  }
  cache = nextCache
  // A session shows its own runs; other sessions' are left out (and cost nothing) unless the user
  // asked to see every session's.
  if (!settings.allSessions) read.splice(0, read.length, ...read.filter(own))
  const unended = read.filter((r) => r.run.end === null && Number.isInteger(r.run.start.pid))
  const alive = await alivePids($, [...new Set(unended.map((r) => r.run.start.pid))], now)
  // Without an answer from ps a run counts as live, except one already found stopped: it keeps
  // that state, so a flaky ps cannot bring it back and have it announced as stopped again. An end
  // record always wins: it is the run's own word that it finished.
  const next = read.map((r) => ({
    ...r,
    state: r.run.end === null && alive === null && previous?.get(r.name) === 'stopped' ? 'stopped' : runLiveness(r.run, alive),
  }))
  // Each checkout's repository is read once (in parallel); a failed read is tried again after
  // a minute, and the branch is read again every five, since a checkout can switch branches.
  const stale = [...new Set(next.map((r) => r.run.start.cwd))].filter((at) => {
    if (typeof at !== 'string' || !at) return false
    const known = repoBases.get(at)
    return !known || now - known.at > (known.failed ? known.retryMs : REPO_REFRESH_MS)
  })
  const infos = await Promise.all(stale.map((at) => checkoutInfo($, at)))
  stale.forEach((at, i) => repoBases.set(at, mergeInfo(repoBases.get(at), infos[i], now)))
  announce($, next, now)
  all = next
}

function targetOf(run) {
  return run.start.target ?? 'jury run'
}

// Toasts for what changed since the last scan: a run that ended, one that stopped without an
// end record. Nothing on the first scan, and nothing for a run first seen already over.
// A run first seen already over is announced only if it started after the first scan: one that
// began and ended between two reads (a cache hit, a fast failure) still gets its toast.
function announce($, next, now) {
  if (previous !== null) {
    for (const r of next) {
      const was = previous.get(r.name)
      // A run already announced over is not announced again, except a stopped one that turns
      // out to have finished after all: its end record and verdict are news.
      if (was === 'ended' || (was === 'stopped' && r.state !== 'ended')) continue
      if (was === undefined && r.state !== 'live' && !(r.run.start.ts * 1000 >= firstScanAt)) continue
      if (r.state === 'ended') notify($, `jury ${targetOf(r.run)}: ${endText(r.run.end)}`)
      else if (r.state === 'stopped') notify($, `jury ${targetOf(r.run)} stopped without an end record`)
    }
  } else firstScanAt = now
  previous = new Map(next.map((r) => [r.name, r.state]))
}

function notify($, text) {
  if (!settings.notify) return
  try {
    Promise.resolve($.ui.toast(text, { timeoutMs: 8000 })).catch(() => {})
  } catch {
    // the band still shows the change
  }
}

// The runs the band draws: every live one, and one that ended in the last RECENT_MS.
function bandRuns() {
  return all.filter((r) => r.state === 'live' || (r.state === 'ended' && scanAt - r.mtimeMs < RECENT_MS))
}

// Whether a run is this session's: started in its folder or below it (keel puts a run's worktree
// inside the session's checkout, and the jury it starts runs there). jury records its real path, so
// the session's is compared both as given and resolved.
function own(r) {
  const at = r.run.start.cwd
  if (typeof at !== 'string' || !at) return false
  return [cwd, cwdReal].some((c) => c !== null && withinFolder(at, c))
}

function textProps(part) {
  return { ...TONES[part.tone], wrap: 'truncate', children: [part.text] }
}

function startedAgo(r) {
  const ts = r.run.start.ts
  return Number.isFinite(ts) && scanAt > 0 ? ago(scanAt - ts * 1000) : null
}

function chip(Text, text, tone) {
  return Text({ ...TONES[tone], wrap: 'truncate', children: [text] })
}

// Below this many columns the band shows only the current phase (and how far along it is), not
// the whole bar, so the target and the seats keep their room.
const WIDE_BAND_COLUMNS = 100

// The phases of a live run as one segmented bar: each phase a chip, side by side.
function phaseBar(ui, run, compact = false) {
  const { Box, Text } = ui
  const chips = phaseChips(run)
  const at = chips.findIndex((c) => c.state === 'current')
  // A phase this mod does not know (a newer ai-jury) has no chip to show alone: draw the whole bar.
  if (compact && at >= 0) {
    return Box({
      flexDirection: 'row',
      flexShrink: 0,
      columnGap: 1,
      children: [chip(Text, ` ${chips[at].text} `, 'current'), chip(Text, `${at + 1}/${chips.length}`, 'dim')],
    })
  }
  return Box({
    flexDirection: 'row',
    flexShrink: 0,
    children: chips.map((c) => chip(Text, ` ${c.text} `, c.state === 'failed' ? 'block' : c.state)),
  })
}

function seatRow(ui, run) {
  const { Box, Text } = ui
  return Box({
    flexDirection: 'row',
    columnGap: 1,
    children: runState(run).seats.map((seat) => {
      const c = seatChip(seat)
      return chip(Text, c.text, SEAT_TONE[c.state])
    }),
  })
}

function verdictChip(ui, end) {
  const { Text } = ui
  if (end.status !== 'done') return chip(Text, ` ${end.status === 'cancelled' ? 'cancelled' : 'failed'} `, 'block')
  return chip(Text, ` ${cleanVerdict(end.verdict) || 'done'} `, VERDICT_TONE[verdictTone(end.verdict)])
}

// What a band row shows after the target button.
function bandRow(ui, r, compact) {
  const { Text } = ui
  if (r.state === 'ended') {
    const parts = [verdictChip(ui, r.run.end), ...(r.run.end.status === 'done' ? [chip(Text, findingsText(r.run.end.findings), 'dim')] : [])]
    // How each seat voted, as a colored dot: who said what at a glance.
    // Not on a narrow band: there the verdict and the finding count need the room.
    if (!compact) for (const b of r.run.end.ballots ?? []) parts.push(chip(Text, `● ${b.agent}`, BALLOT_TONE[verdictTone(b.verdict)]))
    return parts
  }
  if (r.run.start.cached) return [chip(Text, 'from the cache', 'dim')]
  const parts = [phaseBar(ui, r.run, compact)]
  if (runState(r.run).seats.length > 0) parts.push(seatRow(ui, r.run))
  const since = startedAgo(r)
  if (since !== null) parts.push(chip(Text, since, 'dim'))
  return parts
}

async function openRun($, name) {
  selected = name
  await $.ui.open({ id: PANE, title: 'jury', closeOnEscape: true })
  $.ui.invalidate('ui.render')
}

function selectRun($, name) {
  selected = name
  $.ui.invalidate('ui.render')
}

// The directory: the session's own $JURY_EVENTS_DIR when it has one (`off` means none), else
// ai-jury's cache directory plus /events, where this mod leaves a `.watched` marker reading
// `on`. A mod cannot hand a variable to the commands its session runs (`$.env.set` does not
// reach them), so the marker is how every jury on the machine (ai-jury 1.24.0+) learns that
// someone watches. With capture off, a marker this mod left is turned to `off` (a mod cannot
// delete a file).
async function chooseDir($) {
  markerError = null
  home = (await $.env.get('HOME')) ?? null
  const asked = await $.env.get('JURY_EVENTS_DIR')
  if (asked !== undefined) {
    dir = eventsDir(asked, home)
    dirSource = dir === null ? 'off' : 'env'
    return
  }
  const cacheDir = defaultEventsDir({
    cacheDir: (await $.env.get('JURY_CACHE_DIR')) || null,
    xdgCache: (await $.env.get('XDG_CACHE_HOME')) || null,
    home,
  })
  if (!settings.capture || cacheDir === null) {
    // Any existing marker is turned off, whoever left it: watching is one switch per cache.
    try {
      if (cacheDir !== null && (await $.fs.exists(`${cacheDir}/${WATCH_MARKER}`))) {
        await $.fs.write(`${cacheDir}/${WATCH_MARKER}`, 'off\n')
      }
    } catch (err) {
      markerError = `cannot turn the ${WATCH_MARKER} marker off: ${String(err?.message ?? err)}`
    }
    dir = null
    dirSource = 'off'
    return
  }
  dir = cacheDir
  try {
    // Not atomic (truncate, then write): a jury starting in that instant reads an empty marker
    // and runs without events, which fails closed.
    await $.fs.write(`${dir}/${WATCH_MARKER}`, 'on\n')
    dirSource = 'mod'
  } catch (err) {
    // Without the marker no jury writes here; the directory is still read, and the pane says why.
    dirSource = 'unwatched'
    markerError = `cannot leave the ${WATCH_MARKER} marker, so no jury writes here: ${String(err?.message ?? err)}`
  }
}

// One run in full: its target and how it is decided, the phase bar, then one row per phase
// and round with each seat's state, seconds and findings (or error), and how it ended.
function runCard(ui, r) {
  const { Box, Text } = ui
  const s = r.run.start
  const rows = []
  rows.push(
    Box({
      flexDirection: 'row',
      columnGap: 1,
      children: [
        urlOf(r) ? ui.Link({ href: urlOf(r), label: s.target }) : chip(Text, s.target ?? 'jury run', 'title'),
        chip(Text, `${s.mode ?? 'code'} review · decision ${s.decision ?? '?'}${s.chair ? ` · chair ${s.chair}` : ''}`, 'dim'),
      ],
    }),
  )
  // The panel with each seat's model, when jury named one (ai-jury 1.25.0 and newer).
  const seats = (s.panel ?? []).filter((p) => p.agent)
  if (seats.some((p) => p.model)) {
    rows.push(chip(Text, `panel: ${seats.map((p) => (p.model ? `${p.agent} · ${p.model}` : p.agent)).join('  ')}`, 'dim'))
  }
  if (s.cached) rows.push(chip(Text, 'answered from the cache: no agent ran', 'dim'))
  else rows.push(phaseBar(ui, r.run))
  for (const phase of PHASES) {
    const steps = r.run.steps.filter((x) => x.phase === phase)
    for (const round of [...new Set(steps.map((x) => x.round ?? null))]) {
      const these = steps.filter((x) => (x.round ?? null) === round)
      rows.push(
        Box({
          flexDirection: 'row',
          children: [
            Box({ width: 12, flexShrink: 0, children: [chip(Text, phaseLabel(phase, round), 'dim')] }),
            // One chip per seat in a column of their own: a narrow pane wraps between seats,
            // never inside one, and the wrapped ones stay under the first.
            Box({
              flexDirection: 'row',
              flexWrap: 'wrap',
              flexGrow: 1,
              columnGap: 2,
              children: these.map((x) => {
                const secs = Number.isFinite(x.duration_s) ? ` ${x.duration_s}s` : ''
                const sev = severityText(x.severity)
                const tail = x.ok ? (x.findings ? ` · ${x.findings} found${sev ? ` (${sev})` : ''}` : '') : ` · ${x.error_code ?? 'failed'}`
                return chip(Text, `${x.ok ? '●' : '✗'} ${x.agent}${secs}${tail}`, x.ok ? 'ok' : 'bad')
              }),
            }),
          ],
        }),
      )
    }
  }
  if (r.run.end === null) {
    const pending = runState(r.run).seats.filter((seat) => seat.state === 'pending')
    if (pending.length > 0) rows.push(chip(Text, `◌ waiting on ${pending.map((seat) => seat.agent).join(', ')}`, 'wait'))
  } else {
    rows.push(
      Box({
        flexDirection: 'row',
        columnGap: 1,
        children: [chip(Text, 'ended', 'dim'), verdictChip(ui, r.run.end), ...(r.run.end.status === 'done' ? [chip(Text, findingsText(r.run.end.findings), 'dim')] : [])],
      }),
    )
    // Who said what: each seat's own verdict, its model and how much it raised.
    for (const b of r.run.end.ballots ?? []) {
      const model = b.model ?? seatModel(r.run, b.agent, null)
      rows.push(
        Box({
          flexDirection: 'row',
          columnGap: 1,
          children: [
            Box({ width: 12, flexShrink: 0, children: [chip(Text, '', 'dim')] }),
            chip(Text, ` ${cleanVerdict(b.verdict) || '?'} `, VERDICT_TONE[verdictTone(b.verdict)]),
            chip(Text, `${b.agent}${model ? ` · ${model}` : ''}`, 'title'),
            chip(Text, `${findingsText(b.findings)}${b.review ? '' : ' · not counted as a review'}`, 'dim'),
          ],
        }),
      )
    }
  }
  return Box({ flexDirection: 'column', borderStyle: 'round', borderColor: BORDER, paddingX: 1, children: rows })
}

export function register(on, options = {}) {
  if (Number.isFinite(options.poll_seconds)) settings.pollMs = Math.max(1, Math.min(30, options.poll_seconds)) * 1000
  if (Number.isFinite(options.band_rows)) settings.bandMax = Math.max(1, Math.min(9, options.band_rows))
  if (typeof options.notify === 'boolean') settings.notify = options.notify
  if (typeof options.capture === 'boolean') settings.capture = options.capture
  if (typeof options.all_sessions === 'boolean') settings.allSessions = options.all_sessions

  on('session.start', async ($, e, next) => {
    cwd = (await $.session.cwd()) ?? null
    try {
      cwdReal = cwd === null ? null : ((await $.fs.stat(cwd, { resolve: true })).realPath ?? null)
    } catch {
      cwdReal = null
    }
    await chooseDir($)
    if (dir !== null) {
      $.clock.after(0, () => refresh($, true))
      poller?.cancel()
      poller = $.clock.every(settings.pollMs, () => tick($))
    }
    await $.command.register({
      name: 'jury-progress',
      description: 'Show the live ai-jury runs: every phase, seat by seat, and how recent runs ended',
      immediate: true,
    })
    return next(e)
  })

  // A jury command may have just started or finished a run: read at once.
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const result = await next(e)
    if (dir !== null && /(^|[\s;&|(/`'"])jury[`'"]?(\s|$)/.test(String(e.command ?? ''))) {
      $.clock.after(0, () => refresh($, true))
    }
    return result
  })

  on('command.run', { command: 'jury-progress' }, async ($) => {
    selected = null
    await $.ui.open({ id: PANE, title: 'jury', closeOnEscape: true })
    $.clock.after(0, () => refresh($, true))
    return {}
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const shown = bandRuns()
    if (shown.length === 0) return next(e)
    const ui = $.ui.resolve(e)
    const { Box, Text, Button } = ui
    const labelled = shown.length > 1 || shown.some((r) => !own(r))
    const compact = (e.props.bodyColumns ?? 0) < WIDE_BAND_COLUMNS
    const rows = shown.slice(0, settings.bandMax).map((r, i) =>
      Box({
        key: `jury-progress-${r.name}`,
        flexDirection: 'row',
        // Seats that do not fit go to the next line rather than being cut.
        flexWrap: 'wrap',
        columnGap: 1,
        children: [
          // The jury mark heads the first row (no header row of its own: the card is short).
          chip(Text, i === 0 ? '◆ jury' : '      ', 'title'),
          ...(labelled ? [chip(Text, runLabel(r, shown), own(r) ? 'title' : 'dim')] : []),
          // The target opens on GitHub when it is a PR or an issue there; the button beside it
          // (or the target itself, when it has no page) opens the pane on this run. No digit
          // hotkey: a passive band must not take the first key of a prompt.
          Box({
            flexShrink: 0,
            flexDirection: 'row',
            children: urlOf(r)
              ? [
                  ui.Link({ href: urlOf(r), label: targetOf(r.run) }),
                  Button({ key: `jury-progress-open-${r.name}`, label: ' ›', plain: true, onPress: () => openRun($, r.name) }),
                ]
              : [Button({ key: `jury-progress-open-${r.name}`, label: targetOf(r.run), plain: true, onPress: () => openRun($, r.name) })],
          }),
          ...bandRow(ui, r, compact),
        ],
      }),
    )
    if (shown.length > settings.bandMax) {
      rows.push(chip(Text, `+${shown.length - settings.bandMax} more jury runs · /jury-progress`, 'dim'))
    }
    const card = Box({ flexDirection: 'column', borderStyle: 'round', borderColor: BORDER, paddingX: 1, children: rows })
    const theirs = await next(e)
    return Box({ flexDirection: 'column', children: theirs ? [card, theirs] : [card] })
  })

  on('ui.render', { component: 'Pane' }, async ($, e, next) => {
    if (e.requestId !== PANE) return next(e)
    const ui = $.ui.resolve(e)
    const { Box, Text, Button } = ui
    const children = []
    const line = (part) => children.push(Text(textProps(part)))
    if (dir === null) {
      line({ text: 'Progress events are off: $JURY_EVENTS_DIR is set to off, or the capture setting is off.', tone: 'dim' })
      line({ text: 'Turn capture on in /config (jury-progress), or set JURY_EVENTS_DIR to a directory.', tone: 'dim' })
      // A marker that could not be turned off still reads on: every jury keeps writing events.
      if (markerError !== null) line({ text: markerError, tone: 'bad' })
    } else {
      const live = all.filter((r) => r.state === 'live')
      const listed = [...live, ...all.filter((r) => r.state !== 'live')].slice(0, PANE_MAX)
      const from = dirSource === 'mod' ? 'watched by this mod' : dirSource === 'unwatched' ? 'marker not written' : 'from $JURY_EVENTS_DIR'
      line({ text: `${live.length} live jury run(s) · events in ${dir} (${from})`, tone: 'title' })
      if (listError !== null) line({ text: `cannot read the events directory: ${listError}`, tone: 'bad' })
      if (markerError !== null) line({ text: markerError, tone: 'bad' })
      const focus = listed.find((r) => r.name === selected) ?? listed[0]
      if (focus === undefined) {
        line({ text: ' ', tone: 'plain' })
        line({ text: 'No jury run yet. Runs started from this session (or by keel) show here as they happen.', tone: 'dim' })
        if (dirSource === 'mod') line({ text: 'Any jury on this machine (ai-jury 1.24.0 or newer) writes here while the .watched marker says on.', tone: 'dim' })
      } else {
        line({ text: ' ', tone: 'plain' })
        const since = startedAgo(focus)
        const state = focus.state === 'live' ? 'running' : focus.state === 'stopped' ? 'stopped without an end record' : 'finished'
        line({ text: `${state}${since !== null ? ` · started ${since === 'now' ? 'just now' : `${since} ago`}` : ''}${Number.isInteger(focus.run.start.pid) ? ` · pid ${focus.run.start.pid}` : ''}`, tone: focus.state === 'stopped' ? 'bad' : 'dim' })
        if (focus.run.start.cwd) line({ text: focus.run.start.cwd, tone: 'dim' })
        children.push(runCard(ui, focus))
        const others = listed.filter((r) => r !== focus)
        if (others.length > 0) {
          line({ text: ' ', tone: 'plain' })
          line({ text: 'Other runs:', tone: 'dim' })
          for (const r of others) {
            const at = r.state === 'live' ? runState(r.run) : null
            const tail = at !== null ? phaseLabel(at.phase, at.round) : r.state === 'stopped' ? 'stopped' : endText(r.run.end)
            children.push(
              Button({
                key: `jury-progress-pick-${r.name}`,
                label: `${runLabel(r, listed)} · ${targetOf(r.run)} · ${tail}`,
                plain: true,
                onPress: () => selectRun($, r.name),
              }),
            )
          }
        }
      }
    }
    children.push(
      Box({
        key: 'jury-progress-actions',
        flexDirection: 'row',
        columnGap: 2,
        children: [
          Button({ key: 'refresh', label: 'Refresh', onPress: () => refresh($, true) }),
          Button({ key: 'close', label: 'Close', onPress: () => $.ui.close({ id: PANE }) }),
        ],
      }),
    )
    return Box({ flexDirection: 'column', children })
  })
}
