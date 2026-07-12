"""Generate the Copilot session-metadata block for a pull request description.

This module does NOT talk to Azure DevOps / GitHub and does NOT modify any PR. It only *computes*
the "Generated with Copilot CLI" block from data the tooling already records and prints it to
stdout. The agent that created the PR is responsible for placing this block at the top of the PR
description, replacing any previous block delimited by the paired markers.

The block is built deterministically - it takes nothing on faith from the agent:

  * Models        - union of every distinct Copilot-Model commit trailer on the branch and every
                    model recorded in the current session log (so a model is not lost when commits
                    are squashed).
  * CLI version   - the Copilot-CLI-Version commit trailer (falls back to $COPILOT_CLI_BINARY_VERSION).
  * Session Ids   - union of every distinct Copilot-Session-Id commit trailer and the current session
                    id, each rendered as a `copilot --resume=<id>` line in a fenced (copy-clickable)
                    code block.
  * Files explored- distinct files the current session viewed/edited/created, taken from the session
                    log's chat.json (this session only - it is not persisted to commit trailers).

Zero dependencies, standard library only, cross-platform.

Output contract: the metadata block (only) is written to stdout. A human-readable summary of what was
found is written to stderr. So `gitagentify pr-description > block.md` captures a clean block.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

START_MARKER = "<!-- copilot-session-metadata:start -->"
END_MARKER = "<!-- copilot-session-metadata:end -->"

# Resolved once at runtime to the top level of the git repo the command is invoked in.
REPO_ROOT = ""

# Tools that read or write a file identified by a 'path' argument; the set of files they touch
# across the session is what we surface as "files explored".
FILE_TOOLS = {"view", "edit", "create"}


def resolve_repo_root() -> str:
    """Resolve and cache REPO_ROOT to the git top level of the current working directory."""
    global REPO_ROOT
    if REPO_ROOT:
        return REPO_ROOT
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print("git executable not found on PATH.", file=sys.stderr)
        sys.exit(1)
    if out.returncode != 0:
        print("Not inside a git repository (run this from within your repo).", file=sys.stderr)
        sys.exit(1)
    REPO_ROOT = out.stdout.strip()
    return REPO_ROOT


def git(*args: str, must_succeed: bool = False) -> str:
    """Run a git command in the repo root and return trimmed stdout.

    Returns '' on non-zero exit unless ``must_succeed`` is set, in which case a non-zero exit is
    fatal (prints git's stderr and exits 1). Use ``must_succeed`` for commands whose silent failure
    would corrupt the output - e.g. a bad/unfetched ``--target-branch`` making ``git log`` fail must
    not be mistaken for "no metadata".
    """
    try:
        out = subprocess.run(
            ["git", "-C", resolve_repo_root(), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print("git executable not found on PATH.", file=sys.stderr)
        sys.exit(1)
    if out.returncode != 0:
        if must_succeed:
            print(f"git {' '.join(args)} failed: {out.stderr.strip()}", file=sys.stderr)
            sys.exit(1)
        return ""
    return out.stdout.strip()


def distinct_in_order(values):
    """Return distinct, non-empty, stripped values preserving first-seen order."""
    seen = set()
    result = []
    for v in values:
        t = (v or "").strip()
        if t and t not in seen:
            seen.add(t)
            result.append(t)
    return result


# --- Commit-trailer aggregation ----------------------------------------------


def trailer_values(trailer_lines: str, key: str):
    """Extract values for ``key`` from git-emitted trailer lines (see ``git log --format=%(trailers)``).

    Input is the trailer block git itself parsed out of each commit, so unlike scanning the whole
    body (%B) this will not match prose lines that merely happen to look like ``Key: value``.
    """
    if not trailer_lines:
        return []
    pattern = re.compile(rf"^{re.escape(key)}:\s*(.+)$", re.MULTILINE)
    return [m.group(1).strip() for m in pattern.finditer(trailer_lines)]


# --- Session-log extraction ---------------------------------------------------


def session_log_models():
    log_dir = os.environ.get("AGENCY_LOG_SESSION_DIR")
    if not log_dir or not os.path.isdir(log_dir):
        return []
    pattern = re.compile(r'"newModel"\s*:\s*"([^"]+)"')
    models = []
    for root, _dirs, files in os.walk(log_dir):
        for name in files:
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        models.extend(pattern.findall(line))
            except OSError:
                continue
    return models


def session_log_files():
    """Absolute file paths explored (viewed/edited/created) in the current session, from chat.json."""
    log_dir = os.environ.get("AGENCY_LOG_SESSION_DIR")
    if not log_dir:
        return []
    chat = os.path.join(log_dir, "chat.json")
    if not os.path.isfile(chat):
        return []
    try:
        with open(chat, "r", encoding="utf-8", errors="ignore") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    result = []
    for msg in data.get("chatHistory", []):
        for item in (msg.get("items") or []):
            if item.get("$type") == "FunctionCallContent" and item.get("name") in FILE_TOOLS:
                arguments = item.get("arguments")
                p = arguments.get("path") if isinstance(arguments, dict) else None
                if isinstance(p, str) and p.strip():
                    result.append(p.strip())
    return result


def to_repo_relative(path: str):
    """Return a forward-slash repo-relative path, or None if the path is outside the repo."""
    try:
        rel = os.path.relpath(path, resolve_repo_root())
    except ValueError:  # e.g. different drive on Windows
        return None
    if rel.startswith(".."):
        return None
    return rel.replace("\\", "/")


# Scratch/temp artifacts this tooling (or an ad-hoc probe) may create at the repo root. They are not
# real explored source files, so they are excluded from the "Files explored" list. Extend as needed.
SCRATCH_BASENAMES = {"pr_block.md", "pr_block_summary.txt"}


def is_scratch_file(rel_path: str) -> bool:
    base = os.path.basename(rel_path)
    return base in SCRATCH_BASENAMES or base.startswith("_probe")


# --- Block construction -------------------------------------------------------


def sanitize_inline(value: str) -> str:
    """Neutralize a value before it is interpolated into the Markdown/HTML block.

    Values originate from commit trailers, env vars and file paths - normally benign, but a straggler
    backtick, newline, or (worst) a literal marker string would break inline-code rendering or the
    idempotent start/end markers the agent relies on to replace the block. Strip markers, collapse
    whitespace/newlines, and swap backticks for a look-alike so they cannot close a code span.
    """
    text = (value or "")
    text = text.replace(START_MARKER, "").replace(END_MARKER, "")
    text = text.replace("`", "\u2018")  # left single quote - visually close, not a code-span delimiter
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def default_target_branch() -> str:
    ref = git("symbolic-ref", "refs/remotes/origin/HEAD", "--short")
    return ref or "origin/main"


def build_block(models, cli_version, sessions, files) -> str:
    models = [sanitize_inline(m) for m in models]
    sessions = [sanitize_inline(s) for s in sessions]
    cli_version = sanitize_inline(cli_version)
    files = [sanitize_inline(f) for f in files]

    models_md = ", ".join(f"`{m}`" for m in models)
    sessions_md = ", ".join(f"`{s}`" for s in sessions)
    resume_md = "\n".join(f"copilot --resume={s}" for s in sessions)
    cli_md = f"`{cli_version}`" if cli_version else "`unknown`"

    files_section = ""
    if files:
        listing = "\n".join(f"- `{f}`" for f in files)
        files_section = (
            f"<details><summary>\U0001F4C2 <b>Files explored ({len(files)})</b></summary>\n"
            "\n"
            f"{listing}\n"
            "\n"
            "</details>\n"
            "\n"
        )

    return (
        f"{START_MARKER}\n"
        "<details open><summary><b>Generated with Copilot CLI</b></summary>\n"
        "\n"
        f"- \U0001F9E0 **Models:** {models_md}\n"
        f"- \U0001F4E6 **CLI version:** {cli_md}\n"
        f"- \U0001F517 **Session Ids:** {sessions_md}\n"
        "\n"
        "```\n"
        f"{resume_md}\n"
        "```\n"
        "\n"
        f"{files_section}"
        "</details>\n"
        f"{END_MARKER}"
    )


def generate(target_branch: str | None = None) -> str:
    """Compute and return the metadata block string. Exits (non-zero) if no metadata can be found."""
    resolve_repo_root()
    target_branch = target_branch or default_target_branch()

    commit_trailers = git("log", f"{target_branch}..HEAD", "--format=%(trailers)", must_succeed=True)
    trailer_models = trailer_values(commit_trailers, "Copilot-Model")
    trailer_sessions = trailer_values(commit_trailers, "Copilot-Session-Id")
    trailer_cli = trailer_values(commit_trailers, "Copilot-CLI-Version")

    current_session = os.environ.get("COPILOT_AGENT_SESSION_ID") or os.environ.get("AGENCY_SESSION_ID")
    current_cli = os.environ.get("COPILOT_CLI_BINARY_VERSION")

    models = distinct_in_order([*trailer_models, *session_log_models()])
    sessions = distinct_in_order([*trailer_sessions, current_session])
    cli_list = distinct_in_order([*trailer_cli, current_cli])
    cli_version = cli_list[0] if cli_list else ""

    # Files explored come only from the current session log (not persisted to commit trailers).
    # Drop the tooling's own scratch/temp artifacts (the generated block file, probe scripts, etc.)
    # so they never masquerade as explored source files.
    log_files = [to_repo_relative(p) for p in session_log_files()]
    files = distinct_in_order(f for f in log_files if f and not is_scratch_file(f))

    if not models:
        print("Could not determine any model (no Copilot-Model trailers and no session-log model history).",
              file=sys.stderr)
        sys.exit(1)
    if not sessions:
        print("Could not determine any session id (no Copilot-Session-Id trailers and no session env vars).",
              file=sys.stderr)
        sys.exit(1)

    block = build_block(models, cli_version, sessions, files)

    print("Generated Copilot-metadata block:", file=sys.stderr)
    print(f"  Models        : {', '.join(models)}", file=sys.stderr)
    print(f"  CLI version   : {cli_version}", file=sys.stderr)
    print(f"  Session Ids   : {', '.join(sessions)}", file=sys.stderr)
    print(f"  Files explored: {len(files)}", file=sys.stderr)

    return block


def _reconfigure_utf8() -> None:
    # Ensure the emoji-bearing block survives on Windows consoles (default cp1252).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def main(argv=None) -> int:
    _reconfigure_utf8()
    parser = argparse.ArgumentParser(
        prog="gitagentify pr-description",
        description="Generate the Copilot session-metadata block for a PR description (prints to stdout).",
    )
    parser.add_argument(
        "--target-branch",
        default=None,
        help="Branch the PR merges into; bounds the commit range for trailer aggregation. "
             "Defaults to the remote default branch (origin/HEAD), else origin/main.",
    )
    args = parser.parse_args(argv)
    block = generate(args.target_branch)
    print(block)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
