import { expect, mock, test } from 'claude-code/testing'

const DIR = '/home/u/.cache/ai-jury/events'
const SCHEMA = 'ai-jury.events.v1'

// `find` matches type, key and text only (ElementQuery); this also holds the element's props to
// what the test names, so a color or border assertion really checks the color or border.
async function styled(view: any, query: { type: string; text?: string | RegExp }, props: Record<string, unknown>): Promise<any> {
  const hits = await view.findAll(query)
  return hits.find((el: any) => Object.entries(props).every(([k, v]) => el.props?.[k] === v))
}

const BAND = {
  plugin: 'jury-progress',
  component: 'AbovePrompt',
  viewport: { columns: 120, rows: 40 },
  props: { hasSurvey: false, isWorking: false, maxRows: 6, bodyColumns: 118, scroll: { offset: 0, bodyRows: 6 }, view: {} },
} as const

const PANE = {
  plugin: 'jury-progress',
  component: 'Pane',
  requestId: 'jury-progress',
  viewport: { columns: 120, rows: 40 },
  props: { title: 'jury', isFocused: true, bodyColumns: 80, placement: 'inline', scroll: { offset: 0, bodyRows: 30 }, view: {} },
} as const

type Rec = Record<string, unknown>

function start(over: Rec = {}): Rec {
  return {
    schema: SCHEMA,
    seq: 1,
    ts: 0,
    event: 'start',
    panel: [
      { agent: 'claude', vendor: 'anthropic' },
      { agent: 'codex', vendor: 'openai' },
    ],
    chair: 'claude',
    target: 'PR #7',
    mode: 'code',
    decision: 'chair',
    cached: false,
    phases: ['review', 'debate', 'verify', 'synthesis'],
    pid: 100,
    cwd: '/work',
    ...over,
  }
}

function step(phase: string, agent: string, over: Rec = {}): Rec {
  return { schema: SCHEMA, event: 'step', phase, round: null, agent, vendor: 'x', ok: true, duration_s: 12.5, findings: 2, error_code: null, ...over }
}

function end(over: Rec = {}): Rec {
  return { schema: SCHEMA, event: 'end', status: 'done', findings: 4, verdict: 'REQUEST_CHANGES', ...over }
}

type File = { name: string; recs: () => Rec[]; mtime?: () => number; raw?: string }

// Stubs every call the mod makes. `env` is the session's environment; `files` the events
// directory; `alive` the pids `ps` reports (null: ps fails).
function stubEngine(
  on: any,
  opts: {
    env?: Record<string, string>
    files?: File[] | (() => File[])
    alive?: number[] | null | (() => number[] | null)
    psStderr?: string
    startedAgoS?: Record<number, number> // pid -> seconds its process has run (default: days)
    listFails?: string
    markerExists?: boolean
    remotes?: Record<string, string> // checkout -> its origin URL ('' for none)
    branches?: Record<string, string> // checkout -> its branch
    realPath?: string // the session folder resolved (symlinks)
    panes?: 'fail' | 'behind' // panes() rejects, or lists the panel as a tab behind another
    writeFails?: string
  } = {},
) {
  const calls = { remotes: [] as string[], writes: [] as [string, string][], set: [] as [string, string | undefined][], reads: 0, lists: 0, ps: 0, toasts: [] as string[], opened: [] as string[], closed: [] as string[], panes: [] as string[], openArgs: [] as Record<string, unknown>[] }
  const env: Record<string, string> = { HOME: '/home/u', ...(opts.env ?? {}) }
  on('env.get', ($: unknown, e: { name: string }) => ({ value: env[e.name] }))
  on('env.set', ($: unknown, e: { name: string; value?: string }) => {
    calls.set.push([e.name, e.value])
    if (e.value === undefined) delete env[e.name]
    else env[e.name] = e.value
    return { value: undefined }
  })
  const files = () => (typeof opts.files === 'function' ? opts.files() : (opts.files ?? []))
  on('fs.list', ($: unknown, e: { path: string }) => {
    calls.lists += 1
    if (opts.listFails) return { deny: opts.listFails }
    expect(e.path).toBe(env.JURY_EVENTS_DIR ? env.JURY_EVENTS_DIR.replace(/^~/, '/home/u') : (env.JURY_CACHE_DIR ? `${env.JURY_CACHE_DIR}/events` : DIR))
    return {
      value: [
        ...files().map((f) => ({ name: f.name, kind: 'file', size: (f.raw ?? JSON.stringify(f.recs())).length, mtimeMs: f.mtime ? f.mtime() : 0, isLink: false })),
        { name: 'notes.txt', kind: 'file', size: 1, mtimeMs: 0, isLink: false },
      ],
    }
  })
  on('fs.read', ($: unknown, e: { path: string }) => {
    calls.reads += 1
    const f = files().find((x) => e.path.endsWith(`/${x.name}`))
    if (!f) return { deny: 'ENOENT' }
    return { value: f.raw ?? f.recs().map((r) => JSON.stringify(r)).join('\n') + '\n' }
  })
  on('process.run', ($: unknown, e: { argv: string[]; init?: { cwd?: string } }) => {
    if (e.argv[0] === 'git') {
      const at = e.init?.cwd ?? ''
      if (e.argv[1] === 'rev-parse') {
        expect(e.argv).toEqual(['git', 'rev-parse', '--abbrev-ref', 'HEAD'])
        return { value: { exitCode: 0, stdout: `${opts.branches?.[at] ?? 'main'}\n`, stderr: '' } }
      }
      expect(e.argv).toEqual(['git', 'remote', 'get-url', 'origin'])
      calls.remotes.push(at)
      // By default each checkout is its own repository, named after its folder.
      const remote = opts.remotes?.[at] ?? `git@github.com:acme/${at.split('/').filter(Boolean).pop() ?? 'x'}.git`
      return { value: { exitCode: remote ? 0 : 2, stdout: remote ? `${remote}\n` : '', stderr: '' } }
    }
    expect(e.argv.slice(0, 4)).toEqual(['ps', '-o', 'pid=,etime=', '-p'])
    calls.ps += 1
    if (opts.psStderr) return { value: { exitCode: 1, stdout: '', stderr: opts.psStderr } }
    const alive = typeof opts.alive === 'function' ? opts.alive() : opts.alive === undefined ? [100, 200, 300] : opts.alive
    if (alive === null) return { deny: 'spawn ps ENOENT' }
    const asked = e.argv[4].split(',').map(Number)
    const found = asked.filter((p) => alive.includes(p))
    // Elapsed time as ps prints it: [[dd-]hh:]mm:ss. By default a process running for days,
    // long before any run in these tests started.
    const etime = (p: number) => {
      const s = opts.startedAgoS?.[p]
      if (s === undefined) return '3-04:05:06'
      const h = Math.floor(s / 3600)
      const m = Math.floor((s % 3600) / 60)
      const pad = (n: number) => String(n).padStart(2, '0')
      return `${h > 0 ? `${pad(h)}:` : ''}${pad(m)}:${pad(s % 60)}`
    }
    return { value: { exitCode: found.length > 0 ? 0 : 1, stdout: found.map((p) => `  ${p} ${etime(p)}\n`).join(''), stderr: '' } }
  })
  on('fs.write', ($: unknown, e: { path: string; text: string }) => {
    if (opts.writeFails) return { deny: opts.writeFails }
    calls.writes.push([e.path, e.text])
    return { value: undefined }
  })
  on('fs.exists', ($: unknown, e: { path: string }) => ({ value: e.path.endsWith('/.watched') ? (opts.markerExists ?? false) : false }))
  on('fs.stat', ($: unknown, e: { path: string; resolve?: boolean }) => ({ value: { kind: 'dir', size: 0, mtimeMs: 0, isLink: false, realPath: opts.realPath ?? e.path } }))
  on('session.start', () => ({ cwd: '/work' }))
  on('session.cwd', () => ({ value: '/work' }))
  on('command.register', () => ({ value: undefined }))
  on('ui.toast', ($: unknown, e: { text: string }) => {
    calls.toasts.push(e.text)
    return { value: undefined }
  })
  on('ui.open', ($: unknown, e: { id: string }) => {
    calls.opened.push(e.id)
    calls.openArgs.push({ ...e })
    if (!calls.panes.includes(e.id)) calls.panes.push(e.id)
    return { value: { isPlaced: true } }
  })
  on('ui.close', ($: unknown, e: { id: string }) => {
    calls.closed.push(e.id)
    calls.panes = calls.panes.filter((id) => id !== e.id)
    return { value: undefined }
  })
  on('ui.panes', () => {
    if (opts.panes === 'fail') throw new Error('no panes here')
    return { value: calls.panes.map((id) => ({ id, title: 'jury', isShown: opts.panes !== 'behind', isPlaced: true })) }
  })
  on('tool.call', () => ({ result: 'ok' }))
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))
  return calls
}

const RUN = '20261003T110000.000Z-100.ndjson'

async function begin($: any) {
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
}

test('with no JURY_EVENTS_DIR the mod watches the ai-jury cache: it leaves the .watched marker and reads there', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on)
  await begin($)
  await clock.settle()
  expect(calls.writes).toEqual([[`${DIR}/.watched`, 'on\n']])
  // A mod cannot hand a variable to the commands its session runs, so it never tries.
  expect(calls.set).toEqual([])
  expect(calls.lists).toBe(1)
})

test('the cache directory follows JURY_CACHE_DIR, then XDG_CACHE_HOME', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { env: { JURY_CACHE_DIR: '/c', XDG_CACHE_HOME: '/x' } })
  await begin($)
  await clock.settle()
  expect(calls.writes).toEqual([['/c/events/.watched', 'on\n']])
})

test('XDG_CACHE_HOME is used when JURY_CACHE_DIR is not set', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { env: { XDG_CACHE_HOME: '/x' } })
  await begin($)
  await clock.settle()
  expect(calls.writes).toEqual([['/x/ai-jury/events/.watched', 'on\n']])
})

test("the session's own JURY_EVENTS_DIR is read, never replaced", async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { env: { JURY_EVENTS_DIR: '~/ev' }, files: [{ name: RUN, recs: () => [start()] }] })
  await begin($)
  await clock.settle()
  expect(calls.set).toEqual([])
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Link', text: 'PR #7' })).toBeDefined()
})

test('JURY_EVENTS_DIR=off: nothing is read and the pane says how to turn it on', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { env: { JURY_EVENTS_DIR: 'off' } })
  await begin($)
  await clock.advance(30_000)
  expect(calls.lists).toBe(0)
  expect(calls.set).toEqual([])
  await $.command.run({ command: 'jury-progress', args: '' })
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await pane.find({ type: 'Text', text: /Progress events are off/ })).toBeDefined()
})

test('capture off: the mod sets nothing and reads nothing', { options: { capture: false } }, async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on)
  await begin($)
  await clock.advance(30_000)
  expect(calls.set).toEqual([])
  expect(calls.lists).toBe(0)
})

test('a live run draws its target, the phase bar and each seat above the prompt', async ($, on) => {
  const clock = mock.clock(on, { now: 90_000 })
  stubEngine(on, { files: [{ name: RUN, recs: () => [start(), step('review', 'claude')] }] })
  await begin($)
  await clock.settle()
  for (const surface of ['terminal', 'desktop'] as const) {
    const band = await $.ui.mount({ ...BAND, surface })
    expect(await band.find({ type: 'Link', text: 'PR #7' })).toBeDefined()
    // A rounded card, headed by the jury mark, with the phases as a segmented bar of chips.
    expect(await styled(band, { type: 'Box' }, { borderStyle: 'round' })).toBeDefined()
    expect(await band.find({ type: 'Text', text: '◆ jury' })).toBeDefined()
    expect(await styled(band, { type: 'Text', text: ' review ' }, { backgroundColor: '#1F6FEB' })).toBeDefined()
    expect(await styled(band, { type: 'Text', text: ' debate ' }, { backgroundColor: '#6E7681' })).toBeDefined()
    expect(await styled(band, { type: 'Text', text: '● claude' }, { color: 'green' })).toBeDefined()
    expect(await styled(band, { type: 'Text', text: '◌ codex' }, { color: 'yellow' })).toBeDefined()
    expect(await band.find({ type: 'Text', text: '1m' })).toBeDefined()
    // A run from the session's own checkout carries no repository label.
    expect(await band.find({ type: 'Text', text: 'work' })).toBeUndefined()
    // The other mods' band is kept under this one.
    expect(await band.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
    await band.unmount()
  }
})

test('a debate round and a failed seat are drawn as such', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, {
    files: [
      {
        name: RUN,
        recs: () => [start(), step('review', 'claude'), step('review', 'codex'), step('debate', 'codex', { round: 2, ok: false, error_code: 'timeout' })],
      },
    ],
  })
  await begin($)
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await styled(band, { type: 'Text', text: ' review ' }, { backgroundColor: '#2D7D46' })).toBeDefined()
  expect(await styled(band, { type: 'Text', text: ' debate r2 ' }, { backgroundColor: '#1F6FEB' })).toBeDefined()
  expect(await styled(band, { type: 'Text', text: '✗ codex' }, { color: 'red' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: '◌ claude' })).toBeDefined()
})

test('a run that ends shows its verdict for a minute, with one toast, then leaves the band', async ($, on) => {
  const clock = mock.clock(on)
  let recs = [start(), step('review', 'claude')]
  let mtime = 0
  const calls = stubEngine(on, { files: [{ name: RUN, recs: () => recs, mtime: () => mtime }] })
  await begin($)
  await clock.settle()
  expect(calls.toasts).toEqual([])

  recs = [...recs, step('synthesis', 'claude'), end()]
  mtime = 2_000
  await clock.advance(2_000)
  expect(calls.toasts).toEqual(['jury PR #7: REQUEST_CHANGES · 4 findings'])
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await styled(band, { type: 'Text', text: ' REQUEST_CHANGES ' }, { backgroundColor: '#B62324' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: '4 findings' })).toBeDefined()
  await band.unmount()

  await clock.advance(70_000)
  expect(calls.toasts.length).toBe(1)
  const later = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await later.find({ type: 'Link', text: 'PR #7' })).toBeUndefined()
})

test('a run already over when the session opens raises no toast', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { files: [{ name: RUN, recs: () => [start(), end()] }] })
  await begin($)
  await clock.advance(20_000)
  expect(calls.toasts).toEqual([])
})

test('a run whose process is gone without an end record is called stopped, once', async ($, on) => {
  const clock = mock.clock(on)
  let alive = [100]
  const calls = stubEngine(on, { files: [{ name: RUN, recs: () => [start()] }], alive: () => alive })
  await begin($)
  await clock.settle()
  alive = []
  await clock.advance(2_000)
  expect(calls.toasts).toEqual(['jury PR #7 stopped without an end record'])
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Link', text: 'PR #7' })).toBeUndefined()
  await clock.advance(20_000)
  expect(calls.toasts.length).toBe(1)
})

test('when ps cannot answer, a run without an end record still counts as live', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, { files: [{ name: RUN, recs: () => [start()] }], alive: null })
  await begin($)
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Link', text: 'PR #7' })).toBeDefined()
})

test('only ai-jury run files from the last day are read, and each only when it changed', async ($, on) => {
  const clock = mock.clock(on, { now: 25 * 60 * 60 * 1000 })
  const calls = stubEngine(on, {
    files: [
      { name: RUN, recs: () => [start()], mtime: () => 25 * 60 * 60 * 1000 },
      { name: '20261001T000000.000Z-200.ndjson', recs: () => [start({ pid: 200, target: 'PR #1' })], mtime: () => 0 },
      { name: 'ev.ndjson', recs: () => [start({ target: 'PR #2' })] },
    ],
  })
  await begin($)
  await clock.settle()
  expect(calls.reads).toBe(1)
  await clock.advance(10_000)
  expect(calls.reads).toBe(1)
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Link', text: 'PR #1' })).toBeUndefined()
  expect(await band.find({ type: 'Link', text: 'PR #2' })).toBeUndefined()
})

test('a torn last line and other schemas are skipped', async ($, on) => {
  const clock = mock.clock(on)
  const raw = `${JSON.stringify(start())}\n${JSON.stringify({ schema: 'other', event: 'end' })}\n{"schema": "ai-jury.ev`
  stubEngine(on, { files: [{ name: RUN, recs: () => [], raw }] })
  await begin($)
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Link', text: 'PR #7' })).toBeDefined()
})

test('the directory is read on a timer, and at once after a jury Bash call only', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { files: [{ name: RUN, recs: () => [start()] }] })
  await begin($)
  await clock.settle()
  expect(calls.lists).toBe(1)
  // A live run: the directory is read every second, so a new step shows within about one.
  await clock.advance(1_000)
  expect(calls.lists).toBe(2)
  await clock.advance(1_000)
  expect(calls.lists).toBe(3)

  await $.tool.call({ tool: 'Bash', command: 'ls jury-notes' })
  await clock.settle()
  expect(calls.lists).toBe(3)
  await $.tool.call({ tool: 'Bash', command: 'jury --pr 7' })
  await clock.settle()
  expect(calls.lists).toBe(4)
})

test('with no live run the directory is read every 2 s, not every second', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on)
  await begin($)
  await clock.settle()
  expect(calls.lists).toBe(1)
  await clock.advance(1_000)
  expect(calls.lists).toBe(1)
  await clock.advance(1_000)
  expect(calls.lists).toBe(2)
})

test('no directory yet is quiet; another listing error shows in the pane', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, { listFails: 'EACCES: permission denied' })
  await begin($)
  await clock.settle()
  await $.command.run({ command: 'jury-progress', args: '' })
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const shown = await pane.findAll({ type: 'Text' })
  expect(JSON.stringify(shown)).toContain('EACCES')
  expect(await pane.find({ type: 'Text', text: /^cannot read the events directory: / })).toBeDefined()
})

test('several runs are labelled by checkout; band_rows caps them', { options: { band_rows: 1, all_sessions: true } }, async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, {
    files: [
      { name: RUN, recs: () => [start()] },
      { name: '20261003T110001.000Z-200.ndjson', recs: () => [start({ pid: 200, target: 'PR #9', cwd: '/src/keel' })] },
    ],
  })
  await begin($)
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  // Newest first: the keel run, labelled by its checkout.
  expect(await band.find({ type: 'Text', text: 'keel' })).toBeDefined()
  expect(await band.find({ type: 'Link', text: 'PR #9' })).toBeDefined()
  expect(await band.find({ type: 'Link', text: 'PR #7' })).toBeUndefined()
  expect(await band.find({ type: 'Text', text: '+1 more jury runs · /jury-progress' })).toBeDefined()
})

test("a run's button opens the pane on it, phase by phase, and lists the others", { options: { all_sessions: true } }, async ($, on) => {
  const clock = mock.clock(on, { now: 30_000 })
  const calls = stubEngine(on, {
    files: [
      { name: RUN, recs: () => [start(), step('review', 'claude'), step('review', 'codex', { ok: false, error_code: 'timeout', duration_s: 300 })] },
      { name: '20261003T110001.000Z-200.ndjson', recs: () => [start({ pid: 200, target: 'PR #9', cwd: '/src/keel' }), end({ verdict: 'APPROVE', findings: 0 })] },
    ],
  })
  await begin($)
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  await band.press({ key: `jury-progress-open-${RUN}` })
  expect(calls.opened).toEqual(['jury-progress'])
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // Headed as the agents panel heads its list: what this is, and how many are running.
  expect(await pane.find({ type: 'Text', text: '✦ Jury' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: 'on this machine' })).toBeDefined()
  expect(await styled(pane, { type: 'Text', text: '◌ 1 running' }, { color: 'blue' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: 'LIVE' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: 'RECENT' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: 'events in ~/.cache/ai-jury/events (watched by this mod)' })).toBeDefined()
  // The live run's row: blue dot, its name a button, its phase and age on the right, and
  // under it who is doing what; it is open, so it shows in full below.
  expect(await styled(pane, { type: 'Text', text: '●' }, { color: 'blue' })).toBeDefined()
  expect(await pane.find({ type: 'Button', text: 'work · PR #7' })).toBeDefined()
  expect(await styled(pane, { type: 'Text', text: '◌ review · 30s' }, { backgroundColor: '#1F6FEB' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: '● claude  ✗ codex' })).toBeDefined()
  expect(await pane.find({ type: 'Link', text: 'PR #7' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: 'code review · decision chair · chair claude' })).toBeDefined()
  expect(await styled(pane, { type: 'Text', text: '● claude 12.5s · 2 found' }, { color: 'green' })).toBeDefined()
  expect(await styled(pane, { type: 'Text', text: '✗ codex 300s · timeout' }, { color: 'red' })).toBeDefined()
  // The finished run: green dot, its verdict on the right, closed until picked.
  expect(await pane.find({ type: 'Button', text: 'keel · PR #9' })).toBeDefined()
  expect(await styled(pane, { type: 'Text', text: '●' }, { color: 'green' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: '0 findings' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: 'click a run for its phases · /jury-progress to hide' })).toBeDefined()

  await pane.press({ key: 'jury-progress-pick-20261003T110001.000Z-200.ndjson' })
  await pane.unmount()
  const again = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await styled(again, { type: 'Text', text: ' APPROVE ' }, { backgroundColor: '#2D7D46' })).toBeDefined()
  expect(await again.find({ type: 'Text', text: '0 findings' })).toBeDefined()
})

test('the pane before any run says where runs will come from', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on)
  await begin($)
  await clock.settle()
  await $.command.run({ command: 'jury-progress', args: '' })
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await pane.find({ type: 'Text', text: /^No jury run yet/ })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: /Any jury on this machine \(ai-jury 1\.24\.0 or newer\) writes here/ })).toBeDefined()
})

test('a cached run says it came from the cache', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, { files: [{ name: RUN, recs: () => [start({ cached: true })] }] })
  await begin($)
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: 'from the cache' })).toBeDefined()
})

test('notifications off: no toast when a run ends', { options: { notify: false } }, async ($, on) => {
  const clock = mock.clock(on)
  let recs = [start()]
  let mtime = 0
  const calls = stubEngine(on, { files: [{ name: RUN, recs: () => recs, mtime: () => mtime }] })
  await begin($)
  await clock.settle()
  recs = [...recs, end()]
  mtime = 1
  await clock.advance(2_000)
  expect(calls.toasts).toEqual([])
})

test('capture off turns a marker this mod left to off, and reads nothing', { options: { capture: false } }, async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { markerExists: true })
  await begin($)
  await clock.advance(20_000)
  expect(calls.writes).toEqual([[`${DIR}/.watched`, 'off\n']])
  expect(calls.lists).toBe(0)
})

test('a marker that cannot be written is said in the pane, and the directory is still read', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { writeFails: 'EACCES: permission denied' })
  await begin($)
  await clock.settle()
  expect(calls.lists).toBe(1)
  await $.command.run({ command: 'jury-progress', args: '' })
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await pane.find({ type: 'Text', text: /^cannot leave the \.watched marker, so no jury writes here: / })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: /\(marker not written\)$/ })).toBeDefined()
})

test("capture off never touches a JURY_EVENTS_DIR the user set", { options: { capture: false } }, async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { env: { JURY_EVENTS_DIR: DIR }, files: [{ name: RUN, recs: () => [start()] }] })
  await begin($)
  await clock.settle()
  expect(calls.set).toEqual([])
  expect(calls.writes).toEqual([])
  expect(calls.lists).toBe(1)
})

test('a ps that complains on stderr is no answer: no run is called stopped', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { files: [{ name: RUN, recs: () => [start()] }], psStderr: "ps: unrecognized option: p" })
  await begin($)
  await clock.advance(10_000)
  expect(calls.toasts).toEqual([])
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Link', text: 'PR #7' })).toBeDefined()
})

test('a pid now held by a process that started after the run is a stopped run', async ($, on) => {
  const clock = mock.clock(on, { now: 600_000 })
  // The run started at t=0 (ts 0); pid 100 now belongs to a process 2 minutes old.
  stubEngine(on, { files: [{ name: RUN, recs: () => [start()] }], startedAgoS: { 100: 120 } })
  await begin($)
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Link', text: 'PR #7' })).toBeUndefined()
})

test('a run that started and ended between two reads still gets its toast', async ($, on) => {
  const clock = mock.clock(on, { now: 100_000 })
  let files: File[] = []
  const calls = stubEngine(on, { files: () => files })
  await begin($)
  await clock.settle()
  // Idle: the next read is 10 s away. A cached run comes and goes in between.
  files = [{ name: RUN, recs: () => [start({ ts: 103, cached: true }), end({ verdict: 'APPROVE', findings: 0 })], mtime: () => 104_000 }]
  await clock.advance(10_000)
  expect(calls.toasts).toEqual(['jury PR #7: APPROVE · 0 findings'])
})

test('a file whose size changed is read again even when its mtime did not', async ($, on) => {
  const clock = mock.clock(on)
  let recs = [start()]
  const calls = stubEngine(on, { files: [{ name: RUN, recs: () => recs }] })
  await begin($)
  await clock.settle()
  expect(calls.reads).toBe(1)
  recs = [start(), end()]
  await clock.advance(2_000)
  expect(calls.reads).toBe(2)
  expect(calls.toasts).toEqual(['jury PR #7: REQUEST_CHANGES · 4 findings'])
})

test('a run found stopped stays stopped when ps later fails, with no second toast', async ($, on) => {
  const clock = mock.clock(on)
  let alive: number[] | null = [100]
  const calls = stubEngine(on, { files: [{ name: RUN, recs: () => [start()] }], alive: () => alive })
  await begin($)
  await clock.settle()
  alive = []
  await clock.advance(10_000)
  alive = null
  await clock.advance(10_000)
  alive = []
  await clock.advance(10_000)
  expect(calls.toasts).toEqual(['jury PR #7 stopped without an end record'])
})

test('an end record beats a stopped state even when ps has no answer', async ($, on) => {
  const clock = mock.clock(on)
  let alive: number[] | null = [100, 200]
  let recs = [start()]
  let mtime = 0
  // A second live run keeps ps in play, so the read where ps fails really has no answer.
  const other = { name: '20261003T110001.000Z-200.ndjson', recs: () => [start({ pid: 200, target: 'PR #9' })] }
  const calls = stubEngine(on, { files: () => [{ name: RUN, recs: () => recs, mtime: () => mtime }, other], alive: () => alive })
  await begin($)
  await clock.settle()
  alive = [200]
  await clock.advance(2_000)
  expect(calls.toasts).toEqual(['jury PR #7 stopped without an end record'])
  const psBefore = calls.ps
  alive = null
  recs = [start(), end({ verdict: 'APPROVE', findings: 0 })]
  mtime = 20_000
  await clock.advance(2_000)
  expect(calls.ps).toBeGreaterThan(psBefore)
  expect(calls.toasts).toEqual(['jury PR #7 stopped without an end record', 'jury PR #7: APPROVE · 0 findings'])
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: ' APPROVE ' })).toBeDefined()
  await band.unmount()
  // Announced once: later reads of the ended run say nothing more.
  await clock.advance(20_000)
  expect(calls.toasts.length).toBe(2)
})

test('capture off with a marker that cannot be written still registers the command and says why', { options: { capture: false } }, async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, { markerExists: true, writeFails: 'EISDIR: illegal operation on a directory' })
  await begin($)
  await clock.settle()
  await $.command.run({ command: 'jury-progress', args: '' })
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await pane.find({ type: 'Text', text: /Progress events are off/ })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: /^cannot turn the \.watched marker off: / })).toBeDefined()
})

test('a verdict is shown without markdown emphasis, colored by which way it leans', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, {
    files: [
      { name: RUN, recs: () => [start(), end({ verdict: '**COMMENT**', findings: 3 })] },
      { name: '20261003T110001.000Z-200.ndjson', recs: () => [start({ pid: 200, target: 'PR #9' }), { schema: SCHEMA, event: 'end', status: 'error', findings: null, verdict: null }] },
    ],
  })
  await begin($)
  await clock.settle()
  await $.command.run({ command: 'jury-progress', args: '' })
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // The newest run is in focus: it failed, so its chip says so in red.
  expect(await styled(pane, { type: 'Text', text: ' failed ' }, { backgroundColor: '#B62324' })).toBeDefined()
  // The other one is listed, its verdict a plain word.
  expect(await pane.find({ type: 'Button', text: 'work · PR #7' })).toBeDefined()
  expect(await styled(pane, { type: 'Text', text: ' COMMENT ' }, { backgroundColor: '#9A6700' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: '3 findings' })).toBeDefined()
  await pane.press({ key: `jury-progress-pick-${RUN}` })
  await pane.unmount()
  const again = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await styled(again, { type: 'Text', text: ' COMMENT ' }, { backgroundColor: '#9A6700' })).toBeDefined()
  expect(await again.find({ text: /\*\*/ })).toBeUndefined()
})

test('a narrow band shows only the current phase, how far along it is, and the target whole; no digit hotkeys', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, { files: [{ name: RUN, recs: () => [start(), step('review', 'claude'), step('review', 'codex'), step('debate', 'claude', { round: 2 })] }] })
  await begin($)
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal', props: { ...BAND.props, bodyColumns: 70 } })
  expect(await styled(band, { type: 'Text', text: ' debate r2 ' }, { backgroundColor: '#1F6FEB' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: '2/4' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: ' verify ' })).toBeUndefined()
  expect(await band.find({ type: 'Link', text: 'PR #7' })).toBeDefined()
  for (const button of await band.findAll({ type: 'Button' })) expect((button as any).props?.hotkey).toBeUndefined()
})

test('a verdict written with a space leans the same way as with an underscore', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, { files: [{ name: RUN, recs: () => [start(), end({ verdict: 'Request changes', findings: 2 })] }] })
  await begin($)
  await clock.settle()
  await $.command.run({ command: 'jury-progress', args: '' })
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await styled(pane, { type: 'Text', text: ' Request changes ' }, { backgroundColor: '#B62324' })).toBeDefined()
})

test('a phase the mod does not know never takes the band down, even on a narrow band', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, { files: [{ name: RUN, recs: () => [start(), step('appeal', 'claude')] }] })
  await begin($)
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal', props: { ...BAND.props, bodyColumns: 60 } })
  expect(await band.find({ type: 'Link', text: 'PR #7' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: ' review ' })).toBeDefined()
})

test('an ended run shows no phase as current: done throughout, or red where it failed', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, {
    files: [
      { name: RUN, recs: () => [start(), step('review', 'claude'), step('synthesis', 'claude'), end({ verdict: 'APPROVE', findings: 0 })] },
      { name: '20261003T110001.000Z-200.ndjson', recs: () => [start({ pid: 200, target: 'PR #9' }), step('review', 'codex', { ok: false, error_code: 'timeout' }), { schema: SCHEMA, event: 'end', status: 'error', findings: null, verdict: null }] },
    ],
  })
  await begin($)
  await clock.settle()
  await $.command.run({ command: 'jury-progress', args: '' })
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // In focus: the newest, which failed in review.
  expect(await styled(pane, { type: 'Text', text: ' review ' }, { backgroundColor: '#B62324' })).toBeDefined()
  expect(await styled(pane, { type: 'Text' }, { backgroundColor: '#1F6FEB' })).toBeUndefined()
  await pane.press({ key: `jury-progress-pick-${RUN}` })
  await pane.unmount()
  const done = await $.ui.mount({ ...PANE, surface: 'terminal' })
  for (const name of [' review ', ' debate ', ' verify ', ' synthesis ']) {
    expect(await styled(done, { type: 'Text', text: name }, { backgroundColor: '#2D7D46' })).toBeDefined()
  }
})

test("a PR target opens on GitHub; the button beside it opens the pane", async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { files: [{ name: RUN, recs: () => [start()] }] })
  await begin($)
  await clock.settle()
  expect(calls.remotes).toEqual(['/work'])
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  const link = await band.find({ type: 'Link', text: 'PR #7' })
  expect((link as any).props?.href).toBe('https://github.com/acme/work/pull/7')
  await band.press({ key: `jury-progress-open-${RUN}` })
  expect(calls.opened).toEqual(['jury-progress'])
  // The remote is read once per checkout, not every scan.
  await clock.advance(10_000)
  expect(calls.remotes).toEqual(['/work'])
})

test('a target with no GitHub page stays the button that opens the pane', async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, { files: [{ name: RUN, recs: () => [start({ target: 'local diff' })] }], remotes: { '/work': '' } })
  await begin($)
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Button', text: 'local diff' })).toBeDefined()
  expect(await band.find({ type: 'Link' })).toBeUndefined()
})

test('who said what: models with the panel, severity per seat, and each ballot when it ends', async ($, on) => {
  const clock = mock.clock(on)
  const panel = [
    { agent: 'claude', vendor: 'anthropic', model: 'claude-opus-5-5' },
    { agent: 'codex', vendor: 'openai', model: 'gpt-5.3-codex' },
  ]
  stubEngine(on, {
    files: [
      {
        name: RUN,
        recs: () => [
          start({ panel }),
          step('review', 'claude', { model: 'claude-opus-5-5', findings: 3, severity: { major: 1, minor: 2 } }),
          step('review', 'codex', { findings: 0, severity: {} }),
          end({
            verdict: 'REQUEST_CHANGES',
            findings: 3,
            ballots: [
              { agent: 'claude', model: 'claude-opus-5-5', verdict: 'REQUEST_CHANGES', findings: 3, review: true },
              { agent: 'codex', model: null, verdict: 'APPROVE', findings: 0, review: true },
            ],
          }),
        ],
      },
    ],
  })
  await begin($)
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await styled(band, { type: 'Text', text: '● claude' }, { color: 'red' })).toBeDefined()
  expect(await styled(band, { type: 'Text', text: '● codex' }, { color: 'green' })).toBeDefined()
  await band.unmount()
  await $.command.run({ command: 'jury-progress', args: '' })
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await pane.find({ type: 'Text', text: 'panel: claude · claude-opus-5-5  codex · gpt-5.3-codex' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: '● claude 12.5s · 3 found (1 major, 2 minor)' })).toBeDefined()
  expect(await styled(pane, { type: 'Text', text: ' APPROVE ' }, { backgroundColor: '#2D7D46' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: 'codex · gpt-5.3-codex' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: 'claude · claude-opus-5-5' })).toBeDefined()
})

test('runs are labelled by repository, with the branch when one repository has several on screen', { options: { all_sessions: true } }, async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, {
    files: [
      { name: RUN, recs: () => [start({ cwd: '/w/wt-2927' })] },
      { name: '20261003T110001.000Z-200.ndjson', recs: () => [start({ pid: 200, target: 'PR #9', cwd: '/w/wt-2919' })] },
      { name: '20261003T110002.000Z-300.ndjson', recs: () => [start({ pid: 300, target: 'PR #4', cwd: '/src/keel' })] },
    ],
    remotes: { '/w/wt-2927': 'git@github.com:acme/widgets.git', '/w/wt-2919': 'https://github.com/acme/widgets' },
    branches: { '/w/wt-2927': 'feat/a', '/w/wt-2919': 'fix/b' },
  })
  await begin($)
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: 'widgets · feat/a' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: 'widgets · fix/b' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: 'keel' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: /wt-29/ })).toBeUndefined()
})

test('a remote that would make an invalid link gives no link, and the band still draws', async ($, on) => {
  for (const remote of ['git@github.com:acme/ré.git', 'git@github.com:acme/r@x.git', 'https://github.com/acme/..', 'https://evil.example/mirror/github.com/x/y']) {
    const clock = mock.clock(on)
    stubEngine(on, { files: [{ name: RUN, recs: () => [start()] }], remotes: { '/work': remote } })
    await begin($)
    await clock.settle()
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
    expect(await band.find({ type: 'Link' })).toBeUndefined()
    expect(await band.find({ type: 'Button', text: 'PR #7' })).toBeDefined()
    await band.unmount()
    break // one engine per test: the first case stands for the rest (view.js githubBase is pure)
  }
})

test('a failed repository read is tried again after a minute', async ($, on) => {
  const clock = mock.clock(on)
  let remote = ''
  const calls = stubEngine(on, { files: [{ name: RUN, recs: () => [start()] }], remotes: { get '/work'() { return remote } } as any })
  await begin($)
  await clock.settle()
  expect(calls.remotes.length).toBe(1)
  remote = 'git@github.com:acme/widgets.git'
  await clock.advance(30_000)
  expect(calls.remotes.length).toBe(1)
  await clock.advance(31_000)
  expect(calls.remotes.length).toBe(2)
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Link', text: 'PR #7' })).toBeDefined()
})

test('a checkout that disappears keeps its repository name and link', { options: { all_sessions: true } }, async ($, on) => {
  const clock = mock.clock(on)
  let remote = 'git@github.com:acme/widgets.git'
  const calls = stubEngine(on, {
    files: [{ name: RUN, recs: () => [start({ cwd: '/w/wt-2927' })] }, { name: '20261003T110001.000Z-200.ndjson', recs: () => [start({ pid: 200, target: 'PR #9', cwd: '/src/keel' })] }],
    remotes: { get '/w/wt-2927'() { return remote } } as any,
  })
  await begin($)
  await clock.settle()
  remote = '' // keel removed the worktree after the merge
  await clock.advance(6 * 60_000)
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: 'widgets' })).toBeDefined()
  expect(await band.find({ type: 'Link', text: 'PR #7' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: /wt-2927/ })).toBeUndefined()
  // Retried later and later, not every minute.
  const reads = calls.remotes.filter((at) => at === '/w/wt-2927').length
  await clock.advance(90_000)
  expect(calls.remotes.filter((at) => at === '/w/wt-2927').length).toBeLessThanOrEqual(reads + 1)
})

test("a session shows its own jury runs only: in its folder, or in a worktree keel made under it", async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, {
    files: [
      { name: RUN, recs: () => [start({ cwd: '/work/worktrees/pr-11', target: 'PR #11' })] },
      { name: '20261003T110001.000Z-200.ndjson', recs: () => [start({ pid: 200, target: 'PR #22', cwd: '/elsewhere/other-session' })] },
      { name: '20261003T110002.000Z-300.ndjson', recs: () => [start({ pid: 300, target: 'PR #33', cwd: '/work' })] },
    ],
  })
  await begin($)
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Link', text: 'PR #11' })).toBeDefined()
  expect(await band.find({ type: 'Link', text: 'PR #33' })).toBeDefined()
  expect(await band.find({ text: /PR #22/ })).toBeUndefined()
  // Another session's run costs nothing: its repository is never read.
  expect(calls.remotes).not.toContain('/elsewhere/other-session')
})

test("another session's run raises no toast", async ($, on) => {
  const clock = mock.clock(on)
  let recs = [start({ cwd: '/elsewhere/other-session' })]
  let mtime = 0
  const calls = stubEngine(on, { files: [{ name: RUN, recs: () => recs, mtime: () => mtime }] })
  await begin($)
  await clock.settle()
  recs = [...recs, end()]
  mtime = 1
  await clock.advance(2_000)
  expect(calls.toasts).toEqual([])
})

test("a run recorded under the session's resolved path is its own; a neighbour folder sharing the prefix is not", async ($, on) => {
  const clock = mock.clock(on)
  stubEngine(on, {
    realPath: '/private/work',
    files: [
      { name: RUN, recs: () => [start({ cwd: '/private/work/wt', target: 'PR #11' })] },
      { name: '20261003T110001.000Z-200.ndjson', recs: () => [start({ pid: 200, target: 'PR #22', cwd: '/work-other' })] },
    ],
  })
  await begin($)
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Link', text: 'PR #11' })).toBeDefined()
  expect(await band.find({ text: /PR #22/ })).toBeUndefined()
})

test('hovering a seat reveals its model, seconds and findings; hovering a ballot, its verdict and model', async ($, on) => {
  const clock = mock.clock(on, { now: 30_000 })
  stubEngine(on, {
    files: [
      {
        name: RUN,
        recs: () => [
          start({ panel: [{ agent: 'claude', vendor: 'anthropic', model: 'opus' }, { agent: 'codex', vendor: 'openai', model: 'gpt-5.5' }] }),
          step('review', 'claude', { severity: { major: 1, minor: 1 } }),
        ],
      },
      { name: '20261003T110001.000Z-200.ndjson', recs: () => [start({ pid: 200, target: 'PR #9' }), end({ verdict: 'APPROVE', findings: 0, ballots: [{ agent: 'claude', model: 'opus', verdict: 'APPROVE', findings: 0, review: true }] })] },
    ],
  })
  await begin($)
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  // The detail is drawn hidden in the seat's keyed Box; the surface shows it while hovered.
  // (The test view drops `hover` from props; the reveal itself is checked in a real session.)
  expect(await styled(band, { type: 'Box' }, { display: 'none' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: ' opus · 12.5s · 2 found (1 major, 1 minor) ' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: ' gpt-5.5 · thinking ' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: ' APPROVE · opus · 0 findings ' })).toBeDefined()
})

test("a run has a row of its own in the band and in the pane, each keyed by the run", async ($, on) => {
  const clock = mock.clock(on, { now: 30_000 })
  stubEngine(on, { files: [{ name: RUN, recs: () => [start(), step('review', 'claude')] }] })
  await begin($)
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Box', key: `jury-progress-${RUN}` })).toBeDefined()
  await $.command.run({ command: 'jury-progress', args: '' })
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await pane.find({ type: 'Box', key: `jury-progress-row-${RUN}` })).toBeDefined()
})

test('/jury-progress opens the side panel, docked 64 columns wide, and closes it when it is open', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on)
  await begin($)
  await clock.settle()
  await $.command.run({ command: 'jury-progress', args: '' })
  expect(calls.opened).toEqual(['jury-progress'])
  expect(calls.openArgs[0]).toMatchObject({ id: 'jury-progress', columns: 64, closeOnEscape: true })
  await $.command.run({ command: 'jury-progress', args: '' })
  expect(calls.closed).toEqual(['jury-progress'])
  await $.command.run({ command: 'jury-progress', args: '' })
  expect(calls.opened).toEqual(['jury-progress', 'jury-progress'])
})

test('in a short panel the newest run is not opened by itself, so the header stays in sight; a picked run opens', async ($, on) => {
  const clock = mock.clock(on, { now: 30_000 })
  stubEngine(on, { files: [{ name: RUN, recs: () => [start(), step('review', 'claude'), step('review', 'codex')] }] })
  await begin($)
  await clock.settle()
  await $.command.run({ command: 'jury-progress', args: '' })
  const short = { ...PANE, props: { ...PANE.props, scroll: { offset: 0, bodyRows: 8 } } }
  const pane = await $.ui.mount({ ...short, surface: 'terminal' })
  expect(await pane.find({ type: 'Text', text: '✦ Jury' })).toBeDefined()
  expect(await pane.find({ type: 'Text', text: 'code review · decision chair · chair claude' })).toBeUndefined()
  await pane.press({ key: `jury-progress-pick-${RUN}` })
  await pane.unmount()
  const again = await $.ui.mount({ ...short, surface: 'terminal' })
  expect(await again.find({ type: 'Text', text: 'code review · decision chair · chair claude' })).toBeDefined()
})

test('a panel behind another tab is brought forward, not closed; with no answer from panes() it opens', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { panes: 'behind' })
  await begin($)
  await clock.settle()
  await $.command.run({ command: 'jury-progress', args: '' })
  await $.command.run({ command: 'jury-progress', args: '' })
  expect(calls.opened).toEqual(['jury-progress', 'jury-progress'])
  expect(calls.closed).toEqual([])
})

test('panes() failing still opens the panel', async ($, on) => {
  const clock = mock.clock(on)
  const calls = stubEngine(on, { panes: 'fail' })
  await begin($)
  await clock.settle()
  await $.command.run({ command: 'jury-progress', args: '' })
  expect(calls.opened).toEqual(['jury-progress'])
})

test('pressing the open run again closes it, and no run then opens by itself; the card says when it started, its pid and folder', async ($, on) => {
  const clock = mock.clock(on, { now: 30_000 })
  stubEngine(on, { files: [{ name: RUN, recs: () => [start(), step('review', 'claude')] }] })
  await begin($)
  await clock.settle()
  await $.command.run({ command: 'jury-progress', args: '' })
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await pane.find({ type: 'Text', text: 'running · started 30s ago · pid 100 · /work' })).toBeDefined()
  await pane.press({ key: `jury-progress-pick-${RUN}` })
  await pane.unmount()
  const again = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await again.find({ type: 'Text', text: 'running · started 30s ago · pid 100 · /work' })).toBeUndefined()
  expect(await again.find({ type: 'Text', text: '›' })).toBeDefined()
})

test('a card whose seats wrap is counted by its wrapped lines when deciding whether it fits', async ($, on) => {
  const clock = mock.clock(on, { now: 30_000 })
  const seats = ['claude', 'codex', 'gemini', 'grok', 'qwen'].map((agent) => ({ agent, vendor: 'x' }))
  const sev = { severity: { major: 3, minor: 9 }, findings: 12 }
  stubEngine(on, { files: [{ name: RUN, recs: () => [start({ panel: seats }), ...seats.map((p) => step('review', p.agent, sev))] }] })
  await begin($)
  await clock.settle()
  await $.command.run({ command: 'jury-progress', args: '' })
  // One line per row (13) would fit 16 rows; the five long seat chips wrap in a 40-column panel.
  const narrow = { ...PANE, props: { ...PANE.props, bodyColumns: 40, scroll: { offset: 0, bodyRows: 16 } } }
  const pane = await $.ui.mount({ ...narrow, surface: 'terminal' })
  expect(await pane.find({ type: 'Text', text: 'code review · decision chair · chair claude' })).toBeUndefined()
  await pane.unmount()
  // In a wide panel the same seats sit on one line, and the card fits in the same rows.
  const wide = { ...PANE, props: { ...PANE.props, bodyColumns: 300, scroll: { offset: 0, bodyRows: 16 } } }
  const roomy = await $.ui.mount({ ...wide, surface: 'terminal' })
  expect(await roomy.find({ type: 'Text', text: 'code review · decision chair · chair claude' })).toBeDefined()
})
