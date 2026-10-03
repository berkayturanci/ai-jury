# jury-progress

An optional Claude Code mod that shows the live ai-jury runs inside Claude Code, while the
panel deliberates. It only reads: it never runs `jury`, and it sees nothing but metadata.

## Above the prompt

One line per live run:

![A live jury run above the Claude Code prompt, in its debate round, and a finished one with its verdict](docs/band.svg)

- the target (a button: click it to open the pane on that run; no digit hotkey, so the band never
  takes the first key of a prompt)
- the phase bar: review, debate, verify, synthesis (`▰` done, `▶` now, `▱` to come)
- each seat in the current phase: `✓` answered, `✗` failed, `…` still thinking
- how long the run has been going

A run from another checkout carries that checkout's name in front. A finished run stays for a
minute with its verdict (`✓ REQUEST_CHANGES · 4 findings`), then leaves. With more runs than
the band's rows, `+N more` points to the pane.

## The `/jury-progress` pane

![The /jury-progress pane: the run's checkout, target and panel, every phase seat by seat, and the other runs](docs/pane.svg)

The run you picked (or the newest) in full: its target, review mode, decision and chair, when
it started and its pid, then every phase seat by seat: who answered, in how many seconds, how
many findings, or the error code of a seat that failed, and who it still waits on. Below it,
the other live and recent runs, each a button to switch to.

## Notifications

A toast when a run ends (its verdict and finding count) or stops without an end record (its
process is gone, or its pid now belongs to a process that started later). A run that starts
and ends between two reads (a cache hit, a fast failure) still gets its toast; runs that were
already over when the session opened do not.

## How it finds the runs

ai-jury writes each run's progress to a file of its own in `$JURY_EVENTS_DIR`
(`ai-jury.events.v1` NDJSON: the panel, one record per phase result, the end). The records
carry metadata only: no reviewer output, diff or finding text.

When the Claude Code session has no `JURY_EVENTS_DIR`, the mod sets one for the session's own
process: ai-jury's cache directory plus `/events` (`$JURY_CACHE_DIR/events`, else
`$XDG_CACHE_HOME/ai-jury/events`, else `~/.cache/ai-jury/events`). It also sets
`JURY_PROGRESS_SET_DIR` to the same value, so after a reload it knows the directory is its own,
and turning capture off takes both back. Every `jury` the session
starts, by hand, by an agent or through keel, inherits it. A `jury` you start in another
terminal is shown too if that terminal has the same `JURY_EVENTS_DIR`:

```bash
export JURY_EVENTS_DIR="$HOME/.cache/ai-jury/events"
```

A `JURY_EVENTS_DIR` you set yourself is read as it is, never replaced; `off` turns the
events off. A `--mock` run never writes there. A run counts as live until its end record, or
until `ps` no longer finds its pid (or finds it held by a newer process). When `ps` cannot
answer, a run without an end record still counts as live.

## Settings

In `/config`, under jury-progress:

| Setting | Default | What it does |
| --- | --- | --- |
| Capture this session's jury runs | on | Set `JURY_EVENTS_DIR` for the session when it has none. Off: only a `JURY_EVENTS_DIR` you set is read. |
| Refresh every (seconds) | 2 | How often the events are read while a run is live; five times less often otherwise. A `jury` command also refreshes at once. |
| Runs above the prompt | 3 | How many runs the band shows before `+N more`. |
| Notifications | on | The toast when a run ends or stops. |

Next to keel's own mod, keel-progress, the band shows both: the jury runs and the keel runs
that drive them.

![jury-progress and keel-progress in one band: two jury runs above three parallel keel runs](docs/with-keel-progress.svg)

The images are captures of Claude Code 2.1.288 running the mods over demo runs, rendered as SVG.

## Install

You need ai-jury with `$JURY_EVENTS_DIR` support (installing ai-jury itself: [docs/install.md](../../docs/install.md))
and Claude Code **2.1.287 or newer**, with mods enabled for your account.

```bash
claude plugin marketplace add berkayturanci/ai-jury
claude plugin install jury-progress@ai-jury
```

Restart Claude Code. The `ai-jury` plugin and the `jury` CLI do not need the mod; other hosts
(Codex, Cursor, Antigravity) are unaffected.

## Develop

```bash
cd mods/jury-progress
claude plugin validate .
claude plugin test
claude --plugin-dir .
```

## Limits

- Runs started outside Claude Code show only when their shell sets `JURY_EVENTS_DIR`.
- Only the newest 20 runs are kept in the directory (ai-jury prunes it), and files untouched
  for a day are not read.
- A run on another machine (CI) is not shown; the pane reads the local directory only.
