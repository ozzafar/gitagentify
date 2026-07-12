"""gitagentify command-line interface.

Subcommands:
  activate     Install the Copilot metadata hook + instructions into the CURRENT repo and wire the
               commit hook for THIS clone only (git config --local core.hooksPath .githooks).
  deactivate   Unset the local core.hooksPath wiring for this clone (leaves tracked files in place;
               --purge also removes the installed files).
  status       Show whether gitagentify is activated in the current repo/clone.
  pr-block     Print the PR session-metadata block to stdout (what the agent attaches to a PR).

Scoping guarantee: activation is strictly per-clone. We only ever touch the repository-local git
config (git config --local ...), which lives in this clone's .git/config. We never write --global or
--system config, so activating in one repo never affects any other repo or clone.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from . import __version__
from . import generate as _generate

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

HOOKS_DIR_NAME = ".githooks"
HOOKS_PATH_VALUE = ".githooks"  # value we set core.hooksPath to (repo-relative)

# (asset filename, destination relative to repo root, make executable?)
INSTALL_MAP = [
    ("prepare-commit-msg", Path(".githooks") / "prepare-commit-msg", True),
    ("gitattributes", Path(".githooks") / ".gitattributes", False),
    ("copilot-pr-metadata.instructions.md",
     Path(".github") / "instructions" / "copilot-pr-metadata.instructions.md", False),
]


def _run_git(args, cwd, capture=True, check=False):
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=capture,
            text=True,
            check=check,
        )
    except FileNotFoundError:
        print("git executable not found on PATH.", file=sys.stderr)
        sys.exit(1)


def _repo_root() -> str:
    """Top level of the git repo containing the current working directory (fatal if none)."""
    out = _run_git(["rev-parse", "--show-toplevel"], cwd=os.getcwd())
    if out.returncode != 0 or not out.stdout.strip():
        print("Not inside a git repository. Run 'gitagentify activate' from within your repo.",
              file=sys.stderr)
        sys.exit(1)
    return out.stdout.strip()


def _write_asset(asset_name: str, dest: Path, executable: bool) -> None:
    src = ASSETS_DIR / asset_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Read/write in binary and normalise to LF: the hook is bash and MUST stay LF-only, even on
    # Windows checkouts, or git/bash will choke on CRLF.
    data = src.read_bytes().replace(b"\r\n", b"\n")
    dest.write_bytes(data)
    if executable:
        mode = dest.stat().st_mode
        dest.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def cmd_activate(args) -> int:
    root = Path(_repo_root())

    for asset_name, rel_dest, executable in INSTALL_MAP:
        _write_asset(asset_name, root / rel_dest, executable)

    # Ensure git records the hook's executable bit (matters on Windows where the fs has no +x).
    _run_git(["update-index", "--chmod=+x", (Path(HOOKS_DIR_NAME) / "prepare-commit-msg").as_posix()],
             cwd=str(root))

    # Repo-LOCAL wiring only: this writes to <root>/.git/config, scoped to THIS clone. Never global.
    existing = _run_git(["config", "--local", "--get", "core.hooksPath"], cwd=str(root)).stdout.strip()
    if existing and existing != HOOKS_PATH_VALUE:
        print(f"NOTE: core.hooksPath was already set to '{existing}'. Overwriting with "
              f"'{HOOKS_PATH_VALUE}'. Your previous hooks still run if you add a "
              f"'.git/hooks/prepare-commit-msg.local' (the hook chains to it).", file=sys.stderr)
    _run_git(["config", "--local", "core.hooksPath", HOOKS_PATH_VALUE], cwd=str(root), check=True)

    print("gitagentify activated for this repo (this clone only).")
    print(f"  Installed:")
    for _asset, rel_dest, _x in INSTALL_MAP:
        print(f"    - {rel_dest.as_posix()}")
    print(f"  Wired (local): core.hooksPath = {HOOKS_PATH_VALUE}   [in {(root / '.git' / 'config').as_posix()}]")
    print()
    print("Next steps:")
    print("  1. Commit the installed files so teammates get the hook + instructions:")
    print(f"       git add {HOOKS_DIR_NAME} .github/instructions/copilot-pr-metadata.instructions.md")
    print("       git commit -m \"Add gitagentify Copilot session metadata\"")
    print("  2. core.hooksPath is per-clone and is NOT committed. Each teammate runs")
    print("     'gitagentify activate' once in their own clone to wire the hook.")
    return 0


def cmd_deactivate(args) -> int:
    root = Path(_repo_root())

    current = _run_git(["config", "--local", "--get", "core.hooksPath"], cwd=str(root)).stdout.strip()
    if current == HOOKS_PATH_VALUE:
        _run_git(["config", "--local", "--unset", "core.hooksPath"], cwd=str(root))
        print(f"Unset local core.hooksPath (was '{HOOKS_PATH_VALUE}'). Hook no longer runs in this clone.")
    elif current:
        print(f"Left core.hooksPath as '{current}' (not set by gitagentify) - nothing to unset.",
              file=sys.stderr)
    else:
        print("core.hooksPath was not set locally - nothing to unset.")

    if args.purge:
        for _asset, rel_dest, _x in INSTALL_MAP:
            target = root / rel_dest
            if target.exists():
                target.unlink()
                print(f"Removed {rel_dest.as_posix()}")
        hooks_dir = root / HOOKS_DIR_NAME
        if hooks_dir.is_dir() and not any(hooks_dir.iterdir()):
            hooks_dir.rmdir()
    else:
        print("Installed files were left in place (they are tracked). Use --purge to remove them.")
    return 0


def cmd_status(args) -> int:
    root = Path(_repo_root())
    hooks_path = _run_git(["config", "--local", "--get", "core.hooksPath"], cwd=str(root)).stdout.strip()
    activated = hooks_path == HOOKS_PATH_VALUE

    print(f"Repo: {root.as_posix()}")
    print(f"core.hooksPath (local): {hooks_path or '(unset)'}")
    print(f"Activated: {'yes' if activated else 'no'}")
    print("Installed files:")
    for _asset, rel_dest, _x in INSTALL_MAP:
        present = (root / rel_dest).exists()
        print(f"  [{'x' if present else ' '}] {rel_dest.as_posix()}")
    return 0


def cmd_pr_block(args) -> int:
    return _generate.main(["--target-branch", args.target_branch] if args.target_branch else [])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gitagentify",
        description="Stamp Copilot CLI session metadata onto git commits (trailers) and PR descriptions.",
    )
    parser.add_argument("--version", action="version", version=f"gitagentify {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_act = sub.add_parser("activate", help="Install + wire the hook for the current repo (this clone only).")
    p_act.set_defaults(func=cmd_activate)

    p_deact = sub.add_parser("deactivate", help="Unset the local hook wiring for this clone.")
    p_deact.add_argument("--purge", action="store_true", help="Also delete the installed files.")
    p_deact.set_defaults(func=cmd_deactivate)

    p_status = sub.add_parser("status", help="Show activation status for the current repo/clone.")
    p_status.set_defaults(func=cmd_status)

    p_block = sub.add_parser("pr-block", help="Print the PR session-metadata block to stdout.")
    p_block.add_argument("--target-branch", default=None,
                         help="Branch the PR merges into (defaults to origin/HEAD, else origin/main).")
    p_block.set_defaults(func=cmd_pr_block)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
