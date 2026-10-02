"""Keep `plugin/` — the self-contained plugin folder the directories install — in step.

Two plugin directories review a *folder*, not a repository:

* Anthropic's Claude plugin directory takes a repository and a path inside it, and an
  installer receives only that folder. With the root marketplace's `"source": "./"`
  the folder was the whole repository — hundreds of files, a 400 KB changelog,
  binary images — which is what a reviewer holds a submission for.
* OpenAI's shared ChatGPT + Codex plugin directory takes a ZIP of the folder, with a
  portable `plugin.json` (the agent-plugins.org schema) at its root.

`plugin/` is that folder. Root `skills/` stays the source of truth — Antigravity only
discovers a root `skills/` directory (#775), and the root manifests point at it — so
everything in `plugin/` that also exists at the root is a **copy**, regenerated here:

    python3 scripts/plugin_bundle.py           # rewrite the copies from the root
    python3 scripts/plugin_bundle.py --check   # exit 1 if any copy has drifted
    python3 scripts/plugin_bundle.py zip       # write dist/ai-jury-plugin-<version>.zip

Real files rather than symlinks on purpose: the Claude directory refuses a plugin that
loads a file through a symlink, and a ZIP of a symlink is a ZIP of nothing.

`plugin/README.md` is the one hand-written file: it is the disclosure the directory
reviews (what the plugin runs, sends and reads), so it is authored, not derived.

Stdlib-only, like everything in this repository, and importable by path from a test.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLE_DIR = "plugin"

#: Bundle path -> root source, copied byte for byte. `docs/parameters.md` rides along
#: because the skill links to it as `../../docs/parameters.md`, which resolves to
#: `plugin/docs/parameters.md` inside the folder exactly as it resolves at the root.
COPIES: dict[str, str] = {
    ".claude-plugin/plugin.json": ".claude-plugin/plugin.json",
    "skills/ai-jury/SKILL.md": "skills/ai-jury/SKILL.md",
    "docs/parameters.md": "docs/parameters.md",
    "LICENSE": "LICENSE",
    "assets/logo.svg": "website/favicon.svg",
}

#: The portable manifest, derived from the root Claude manifest plus the presentation
#: OpenAI's directory reads. Limits (developers.openai.com/codex/plugins/build):
#: displayName and shortDescription <= 30 chars, longDescription <= 4000,
#: developerName <= 80, defaultPrompt <= 3 entries of <= 128 chars.
PORTABLE_MANIFEST = "plugin.json"
PORTABLE_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
OPENAI_INTERFACE: dict[str, object] = {
    "displayName": "AI Jury",
    "shortDescription": "Cross-vendor code review jury",
    "longDescription": (
        "AI Jury convenes a panel of reviewers from different vendors on the same diff, "
        "pull request or issue. Each reviewer works independently, they cross-examine each "
        "other's findings, a verification round checks them against the diff, and the run "
        "ends in one verdict: a chair's synthesis or a panel vote.\n\n"
        "Use it to get a second, third and fourth opinion on a change before merging, to "
        "gate a CI job on blocking findings, or to check an issue for completeness.\n\n"
        "Limitations: the skill drives the separately installed `jury` command-line tool "
        "(pipx install ai-jury), which needs at least one reviewer available - the Claude "
        "Code or Codex CLI, a hosted model API key, or a local model server. The diff is "
        "sent to every reviewer you configure. It reviews; it does not edit your code."
    ),
    "developerName": "Berkay Turancı",
    "category": "Developer Tools",
    "capabilities": [],
    "defaultPrompt": [
        "Convene the review jury on my current branch",
        "Run a cross-vendor review of pull request #123",
        "Check whether issue #45 is ready to implement",
    ],
    "brandColor": "#4F46E5",
    "logo": "./assets/logo.svg",
    "composerIcon": "./assets/logo.svg",
}

#: Root-manifest keys the portable manifest carries over, in this order.
_PORTABLE_KEYS = (
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
)

#: Files that live only in the bundle and are written by hand.
AUTHORED = ("README.md",)

#: Never shipped in the ZIP. The Claude manifest is for the Claude directory, which
#: reads the folder from git; the OpenAI upload has its own manifest at the root.
ZIP_EXCLUDE_PREFIXES = (".claude-plugin/",)

#: A fixed timestamp so the same tree always produces the same ZIP bytes.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _root_manifest(root: Path) -> dict:
    return json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))


def portable_manifest_text(root: Path = REPO_ROOT) -> str:
    """The portable `plugin.json`, derived from the root Claude manifest."""
    source = _root_manifest(root)
    manifest: dict[str, object] = {"$schema": PORTABLE_SCHEMA}
    for key in _PORTABLE_KEYS:
        if key in source:
            manifest[key] = source[key]
    manifest["extensions"] = {"com.openai": {"interface": OPENAI_INTERFACE}}
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def expected_files(root: Path = REPO_ROOT) -> dict[str, bytes]:
    """Every generated bundle file and the bytes it must hold."""
    files = {rel: (root / src).read_bytes() for rel, src in COPIES.items()}
    files[PORTABLE_MANIFEST] = portable_manifest_text(root).encode("utf-8")
    return files


def drift(root: Path = REPO_ROOT) -> list[str]:
    """Bundle files that are missing or differ from what the root would generate."""
    bundle = root / BUNDLE_DIR
    problems = []
    for rel, data in sorted(expected_files(root).items()):
        path = bundle / rel
        if path.is_symlink():
            problems.append(f"{BUNDLE_DIR}/{rel} is a symlink; the directories need a real file")
        elif not path.is_file():
            problems.append(f"{BUNDLE_DIR}/{rel} is missing")
        elif path.read_bytes() != data:
            problems.append(f"{BUNDLE_DIR}/{rel} differs from its source")
    for rel in AUTHORED:
        if not (bundle / rel).is_file():
            problems.append(f"{BUNDLE_DIR}/{rel} is missing (hand-written, not generated)")
    return problems


def write(root: Path = REPO_ROOT) -> list[str]:
    """Rewrite every generated bundle file; return the ones that changed."""
    changed = []
    for rel, data in sorted(expected_files(root).items()):
        path = root / BUNDLE_DIR / rel
        if path.is_symlink():
            path.unlink()
        if path.is_file() and path.read_bytes() == data:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        changed.append(f"{BUNDLE_DIR}/{rel}")
    return changed


def bundle_files(root: Path = REPO_ROOT) -> list[str]:
    """Every file in the bundle folder, as sorted POSIX paths relative to it."""
    bundle = root / BUNDLE_DIR
    return sorted(
        p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file() or p.is_symlink()
    )


def zip_members(root: Path = REPO_ROOT) -> list[str]:
    """The bundle files the OpenAI upload ZIP contains."""
    return [rel for rel in bundle_files(root) if not rel.startswith(ZIP_EXCLUDE_PREFIXES)]


def version(root: Path = REPO_ROOT) -> str:
    return str(_root_manifest(root)["version"])


def build_zip(destination: Path, root: Path = REPO_ROOT) -> Path:
    """Write the upload ZIP: sorted entries, fixed timestamps and modes, no directories."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    bundle = root / BUNDLE_DIR
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in zip_members(root):
            info = zipfile.ZipInfo(rel, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100644 & 0xFFFF) << 16
            info.create_system = 3
            zf.writestr(info, (bundle / rel).read_bytes())
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "command",
        nargs="?",
        choices=("sync", "zip"),
        default="sync",
        help="sync (default): regenerate the copies; zip: write the OpenAI upload ZIP",
    )
    parser.add_argument(
        "--check", action="store_true", help="with sync: report drift, write nothing"
    )
    parser.add_argument("-o", "--output", help="with zip: destination path")
    args = parser.parse_args(argv)

    if args.command == "zip":
        problems = drift()
        if problems:
            print(
                "plugin/ is out of date; run `python3 scripts/plugin_bundle.py` first:",
                file=sys.stderr,
            )
            print("\n".join(f"  {p}" for p in problems), file=sys.stderr)
            return 1
        out = (
            Path(args.output)
            if args.output
            else REPO_ROOT / "dist" / f"ai-jury-plugin-{version()}.zip"
        )
        build_zip(out)
        print(f"wrote {out} ({len(zip_members())} files)")
        return 0

    if args.check:
        problems = drift()
        if problems:
            print(
                "plugin/ has drifted from the root; run `python3 scripts/plugin_bundle.py`:",
                file=sys.stderr,
            )
            print("\n".join(f"  {p}" for p in problems), file=sys.stderr)
            return 1
        print(f"plugin/ is in sync ({len(expected_files())} generated files)")
        return 0

    changed = write()
    print("\n".join(f"updated {c}" for c in changed) if changed else "plugin/ already in sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
