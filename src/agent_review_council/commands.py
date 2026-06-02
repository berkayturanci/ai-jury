"""Parse GitHub PR comment commands into safe, allowlisted council runs (#11).

Mature review tools can be triggered from a PR comment like ``/council review``.
This module parses such a comment into a structured, **allowlisted** command and
maps it to a council CLI argument vector. Security properties:

- only a fixed allowlist of subcommands (``review``, ``summary``) is accepted;
- only an allowlist of flags (``--rounds N``) is accepted;
- the comment text is tokenized with :func:`shlex.split` and mapped to an argv
  — it is NEVER passed to a shell, so arbitrary commands in a comment cannot run;
- anything unrecognized raises :class:`CommandError` (the caller rejects it).

Pure and network-free, so parsing and rejection are fully unit-testable.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

# Allowlisted subcommands. ``review`` = a normal review; ``summary`` = a quick
# single-round pass intended for a short summary comment.
ALLOWED_COMMANDS = ("review", "summary")

# Bound on an allowlisted --rounds value (mirrors the council's practical range).
_MIN_ROUNDS, _MAX_ROUNDS = 1, 3

# The trigger: a line that begins (ignoring leading space) with "/council".
_TRIGGER_RE = re.compile(r"^\s*/council\b(.*)$", re.MULTILINE)


class CommandError(Exception):
    """Raised when a comment is not a valid, allowlisted council command."""


@dataclass
class ParsedCommand:
    command: str
    rounds: int | None = None

    def to_cli_args(self) -> list[str]:
        """Map the parsed command to a council CLI argument vector (allowlisted)."""
        args: list[str] = []
        if self.command == "summary":
            # A summary is a fast single-round pass unless an explicit rounds
            # override was given.
            args += ["--rounds", str(self.rounds if self.rounds is not None else 1)]
        elif self.rounds is not None:
            args += ["--rounds", str(self.rounds)]
        return args


def parse_comment(text: str) -> ParsedCommand:
    """Parse a PR comment into an allowlisted :class:`ParsedCommand`.

    Raises :class:`CommandError` when the text is not a ``/council`` command, the
    subcommand is not allowlisted, or any flag/argument is unrecognized or out of
    range.
    """
    if not text:
        raise CommandError("empty comment: no /council command found")

    match = _TRIGGER_RE.search(text)
    if not match:
        raise CommandError("no /council command found in comment")

    try:
        tokens = shlex.split(match.group(1).strip())
    except ValueError as exc:
        raise CommandError(f"could not parse command: {exc}") from exc

    if not tokens:
        raise CommandError(
            f"missing subcommand; expected one of {', '.join(ALLOWED_COMMANDS)}"
        )

    command, rest = tokens[0], tokens[1:]
    if command not in ALLOWED_COMMANDS:
        raise CommandError(
            f"unsupported command '{command}'; allowed: {', '.join(ALLOWED_COMMANDS)}"
        )

    rounds: int | None = None
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--rounds":
            if i + 1 >= len(rest):
                raise CommandError("--rounds requires a value")
            value = rest[i + 1]
            i += 2
        elif tok.startswith("--rounds="):
            value = tok.split("=", 1)[1]
            i += 1
        else:
            raise CommandError(f"unsupported argument '{tok}'; only --rounds is allowed")
        try:
            rounds = int(value)
        except ValueError as exc:
            raise CommandError(f"--rounds must be an integer (got {value!r})") from exc
        if not (_MIN_ROUNDS <= rounds <= _MAX_ROUNDS):
            raise CommandError(
                f"--rounds must be between {_MIN_ROUNDS} and {_MAX_ROUNDS} (got {rounds})"
            )

    return ParsedCommand(command=command, rounds=rounds)
