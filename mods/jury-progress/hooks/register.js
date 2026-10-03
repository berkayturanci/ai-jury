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

import { ago, baseName, parsePs, runLiveness, defaultEventsDir, endText, eventsDir, isRunFile, paneLines, parseRun, phaseBar, phaseLabel, runState, seatText } from './view.js'

const PANE = 'jury-progress'
// The file that tells ai-jury (1.24.0+) someone watches its cache's events directory.
const WATCH_MARKER = '.watched'
const POLL_MS = 2_000
// With no live run the timer still ticks every poll but reads only every IDLE_EVERY ticks.
const IDLE_EVERY = 5
// A run file untouched this long is not read at all.
const FRESH_MS = 24 * 60 * 60 * 1000
// A finished run stays in the band this long, so its verdict is seen.
const RECENT_MS = 60 * 1000
// How many runs the pane lists (live ones first, then the most recent finished).
const PANE_MAX = 10
const BAND_MAX = 3
const PS_TIMEOUT_MS = 5_000

const TONES = {
  title: { bold: true },
  bar: { color: 'cyan' },
  ok: { color: 'green' },
  wait: { color: 'yellow' },
  bad: { color: 'red' },
  dim: { dimColor: true },
  plain: {},
}

const settings = { pollMs: POLL_MS, bandMax: BAND_MAX, notify: true, capture: true }

let dir = null // the events directory read, or null
let dirSource = null // 'env' (the session's own $JURY_EVENTS_DIR), 'mod' (set here), 'off'
let home = null
let cwd = null
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
  const unended = read.filter((r) => r.run.end === null && Number.isInteger(r.run.start.pid))
  const alive = await alivePids($, [...new Set(unended.map((r) => r.run.start.pid))], now)
  // Without an answer from ps a run counts as live, except one already found stopped: it keeps
  // that state, so a flaky ps cannot bring it back and have it announced as stopped again. An end
  // record always wins: it is the run's own word that it finished.
  const next = read.map((r) => ({
    ...r,
    state: r.run.end === null && alive === null && previous?.get(r.name) === 'stopped' ? 'stopped' : runLiveness(r.run, alive),
  }))
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

function own(r) {
  const at = r.run.start.cwd
  return typeof at === 'string' && cwd !== null && (at === cwd || at.startsWith(`${cwd}/`))
}

function textProps(part) {
  return { ...TONES[part.tone], wrap: 'truncate', children: [part.text] }
}

function startedAgo(r) {
  const ts = r.run.start.ts
  return Number.isFinite(ts) && scanAt > 0 ? ago(scanAt - ts * 1000) : null
}

// The band line's parts after the target button.
function bandParts(r) {
  if (r.state === 'ended') {
    const done = r.run.end.status === 'done'
    return [{ text: `${done ? '✓' : '✗'} ${endText(r.run.end)}`, tone: done ? 'ok' : 'bad' }]
  }
  if (r.run.start.cached) return [{ text: 'from the cache', tone: 'dim' }]
  const { phase, round, seats } = runState(r.run)
  const parts = [
    { text: phaseBar(phase), tone: 'bar' },
    { text: phaseLabel(phase, round), tone: 'plain' },
  ]
  if (seats.length > 0) {
    parts.push({ text: '·', tone: 'dim' })
    for (const seat of seats) parts.push({ text: seatText(seat), tone: seat.state === 'ok' ? 'ok' : seat.state === 'failed' ? 'bad' : 'dim' })
  }
  const since = startedAgo(r)
  if (since !== null) parts.push({ text: `· ${since}`, tone: 'dim' })
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

export function register(on, options = {}) {
  if (Number.isFinite(options.poll_seconds)) settings.pollMs = Math.max(1, Math.min(30, options.poll_seconds)) * 1000
  if (Number.isFinite(options.band_rows)) settings.bandMax = Math.max(1, Math.min(9, options.band_rows))
  if (typeof options.notify === 'boolean') settings.notify = options.notify
  if (typeof options.capture === 'boolean') settings.capture = options.capture

  on('session.start', async ($, e, next) => {
    cwd = (await $.session.cwd()) ?? null
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
    const { Box, Text, Button } = $.ui.resolve(e)
    const labelled = shown.length > 1 || shown.some((r) => !own(r))
    const lines = shown.slice(0, settings.bandMax).map((r) =>
      Box({
        key: `jury-progress-${r.name}`,
        flexDirection: 'row',
        columnGap: 1,
        children: [
          ...(labelled ? [Text(textProps({ text: baseName(r.run.start.cwd) || '?', tone: own(r) ? 'title' : 'dim' }))] : []),
          Text(textProps({ text: 'jury', tone: 'title' })),
          Button({
            key: `jury-progress-open-${r.name}`,
            label: targetOf(r.run),
            plain: true,
            // No digit hotkey: a passive band must not take the first key of a prompt.
            onPress: () => openRun($, r.name),
          }),
          ...bandParts(r).map((part) => Text(textProps(part))),
        ],
      }),
    )
    if (shown.length > settings.bandMax) {
      lines.push(Text(textProps({ text: `+${shown.length - settings.bandMax} more jury runs · /jury-progress`, tone: 'dim' })))
    }
    const theirs = await next(e)
    return Box({ flexDirection: 'column', children: theirs ? [...lines, theirs] : lines })
  })

  on('ui.render', { component: 'Pane' }, async ($, e, next) => {
    if (e.requestId !== PANE) return next(e)
    const { Box, Text, Button } = $.ui.resolve(e)
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
        for (const part of paneLines(focus.run)) line(part)
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
                label: `${baseName(r.run.start.cwd) || '?'} · ${targetOf(r.run)} · ${tail}`,
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
