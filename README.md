# GitAgentify

Stamp **Copilot CLI session metadata** onto your git history and pull requests, so every commit and
PR can be traced back to the agent session (and model) that produced it.

> **Scope:** GitAgentify currently targets **[GitHub Copilot](https://github.com/features/copilot)**
> (the `copilot` CLI and its agents). It keys off Copilot's environment variables
> (`COPILOT_CLI`, `COPILOT_AGENT_SESSION_ID`, `COPILOT_CLI_BINARY_VERSION`, `AGENCY_LOG_SESSION_DIR`)
> and its `.github/instructions/` mechanism. Support for other coding agents may be added later.

GitAgentify installs two cooperating pieces into a repo:

1. **A `prepare-commit-msg` git hook** that adds structured trailers to every Copilot-authored
   commit:

   ```
   Copilot-Session-Id: 1594c4f7-f162-425b-b4ac-c0b533b6ccae
   Copilot-Model: claude-opus-4.8
   Copilot-CLI-Version: 1.0.62
   ```

   The hook only fires when `COPILOT_CLI=1`, skips merge/squash commits, is idempotent, and chains to
   any existing `.git/hooks/prepare-commit-msg.local`.

2. **A repo-scoped Copilot instructions file** (`.github/instructions/gitagentify/copilot-pr-metadata.instructions.md`)
   plus the `gitagentify pr-description` generator. Together they tell the agent to attach a code-authored
   metadata block (models, session ids, CLI version, files explored) to the top of every PR
   description it creates or updates.

## Quick Start

```bash
pipx install git+https://github.com/ozzafar/gitagentify
# or:  pip install git+https://github.com/ozzafar/gitagentify
cd your-repo
gitagentify activate
```

## Install (once per machine)

```bash
pipx install git+https://github.com/ozzafar/gitagentify
# or:  pip install git+https://github.com/ozzafar/gitagentify
```

## Activate (once per clone)

Run inside the repository you want to enable:

```bash
cd your-repo
gitagentify activate
```

This writes everything into two dedicated `gitagentify` folders:

- `.gitagentify/prepare-commit-msg` + `.gitagentify/.gitattributes` (the commit hook),
- `.github/instructions/gitagentify/copilot-pr-metadata.instructions.md` (the PR instructions),

and wires the hook with `git config --local core.hooksPath .gitagentify`.

> **Scope guarantee:** activation is **strictly per-clone**. GitAgentify only ever writes the
> repository-local git config (`git config --local ...`), which lives in this clone's `.git/config`.
> It never writes `--global` or `--system` config, so activating in one repo never affects any other
> repo or clone. Because `core.hooksPath` is per-clone and not committed, each teammate runs
> `gitagentify activate` once in their own clone.

Commit the installed files so teammates get the hook + instructions:

```bash
git add .gitagentify .github/instructions/gitagentify/copilot-pr-metadata.instructions.md
git commit -m "Add gitagentify Copilot session metadata"
```

## Commands

| Command | Description |
| --- | --- |
| `gitagentify activate` | Install the hook + instructions into the current repo and wire the hook for this clone only. |
| `gitagentify deactivate` | Unset the local `core.hooksPath` wiring. `--purge` also deletes the installed files. |
| `gitagentify status` | Show activation status and which files are installed. |
| `gitagentify pr-description` | Print the PR session-metadata block to stdout (what the agent attaches to a PR). |

## How the PR block is produced

`gitagentify pr-description` computes the block deterministically - it trusts nothing from the agent:

- **Models** - union of every distinct `Copilot-Model` commit trailer on the branch and every model
  recorded in the current session log (so a model is not lost when commits are squashed).
- **CLI version** - the `Copilot-CLI-Version` trailer (falls back to `$COPILOT_CLI_BINARY_VERSION`).
- **Session Ids** - every distinct `Copilot-Session-Id` trailer plus the current session id, each
  rendered as a copy-clickable `copilot --resume=<id>` line.
- **Files explored** - files the current session viewed/edited/created (from the session log only;
  intentionally *not* persisted to commit trailers).

The block is delimited by paired markers so the agent can replace it idempotently on every update:

```
<!-- copilot-session-metadata:start -->
...
<!-- copilot-session-metadata:end -->
```

## Requirements

- Python 3.8+
- git
- bash (for the commit hook; present via Git for Windows on Windows)

## Uninstall

```bash
gitagentify deactivate --purge
```

## License

This project is licensed under the [MIT License](LICENSE).
