---
applyTo: "**"
---

# Attach Copilot session metadata to pull requests

Whenever you **create or update a pull request** in this repository (for example via the ADO MCP
`repo_pull_request_write` tool or the GitHub PR tools), you MUST attach a "Generated with Copilot
CLI" metadata block to the top of the PR description. The block is produced **by code**, not by you.

## Steps

1. Generate the block by running the command and capturing its **stdout**:

   ```
   gitagentify pr-description
   ```

   The command prints only the metadata block to stdout (a human-readable summary goes to stderr).
   It computes every value - models, session ids, CLI version, files explored - from the commit
   trailers and the current session log. It does **not** contact Azure DevOps / GitHub or modify the
   PR.

2. Put that block at the **very top** of the PR description, then a `---` separator, then the
   human-authored description below it. Set the PR description via the tool you used to create or
   update the PR.

3. Keep it **idempotent**: the block is delimited by the paired markers
   `<!-- copilot-session-metadata:start -->` ... `<!-- copilot-session-metadata:end -->`. When you
   update an existing PR, replace whatever is between those markers (inclusive) with the freshly
   generated block; never stack a second copy. Preserve everything below it.

## Rules

- Do **not** hand-write, edit, or paraphrase the block - only the command authors it.
- Do **not** put the literal marker strings anywhere else in the PR description; that would confuse
  the replace step.
- Re-run the command and refresh the block whenever you push new commits to the PR, so late model
  switches and newly explored files are reflected.

The companion `.gitagentify/prepare-commit-msg` hook stamps the equivalent structured git trailers
(`Copilot-Session-Id`, `Copilot-Model`, `Copilot-CLI-Version`) onto each Copilot-authored commit;
the command aggregates those trailers across the branch. Files explored are **not** stamped into
commits - they come only from the current session log.
