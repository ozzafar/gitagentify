"""Unit tests for gitagentify.generate (pure functions - no git/network required)."""

import gitagentify.generate as g


def test_distinct_in_order_dedups_and_strips():
    assert g.distinct_in_order([" a ", "a", "b", "", None, "b", "c"]) == ["a", "b", "c"]


def test_trailer_values_extracts_only_matching_key():
    trailers = (
        "Copilot-Session-Id: abc\n"
        "Copilot-Model: claude-opus-4.8\n"
        "Co-authored-by: Someone <x@y.z>\n"
        "Copilot-Model: gpt-5.5\n"
    )
    assert g.trailer_values(trailers, "Copilot-Model") == ["claude-opus-4.8", "gpt-5.5"]
    assert g.trailer_values(trailers, "Copilot-Session-Id") == ["abc"]
    assert g.trailer_values("", "Copilot-Model") == []


def test_is_scratch_file():
    assert g.is_scratch_file("pr_block.md")
    assert g.is_scratch_file("sub/dir/pr_block_summary.txt")
    assert g.is_scratch_file("_probe.py")
    assert g.is_scratch_file("tools/_probe_thing.js")
    assert not g.is_scratch_file("src/app.py")


def test_sanitize_inline_neutralizes_markers_and_backticks():
    dirty = f"value {g.START_MARKER} with `backtick`\nand newline {g.END_MARKER}"
    clean = g.sanitize_inline(dirty)
    assert g.START_MARKER not in clean
    assert g.END_MARKER not in clean
    assert "`" not in clean
    assert "\n" not in clean


def test_build_block_structure_and_markers():
    block = g.build_block(
        models=["claude-opus-4.8", "gpt-5.5"],
        cli_version="1.0.62",
        sessions=["sess-1", "sess-2"],
        files=["src/a.py", "src/b.py"],
    )
    assert block.startswith(g.START_MARKER)
    assert block.endswith(g.END_MARKER)
    assert "**Models:**" in block
    assert "`claude-opus-4.8`" in block and "`gpt-5.5`" in block
    assert "`1.0.62`" in block
    assert "copilot --resume=sess-1" in block
    assert "copilot --resume=sess-2" in block
    assert "Files explored (2)" in block
    assert "`src/a.py`" in block


def test_build_block_omits_files_section_when_empty():
    block = g.build_block(["m"], "1.0", ["s"], [])
    assert "Files explored" not in block


def test_build_block_cli_unknown_when_blank():
    block = g.build_block(["m"], "", ["s"], [])
    assert "`unknown`" in block


def test_generate_excludes_directories_from_files(tmp_path, monkeypatch):
    repo = tmp_path
    (repo / "README.md").write_text("readme")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("code")

    monkeypatch.setattr(g, "REPO_ROOT", str(repo))
    monkeypatch.setattr(g, "resolve_repo_root", lambda: str(repo))
    monkeypatch.setattr(g, "default_target_branch", lambda: "origin/main")
    monkeypatch.setattr(g, "git", lambda *a, **k: "")
    monkeypatch.setattr(g, "session_log_models", lambda: ["claude-opus-4.8"])
    monkeypatch.setattr(
        g,
        "session_log_files",
        lambda: [
            str(repo),
            str(repo / "src"),
            str(repo / "src" / "app.py"),
            str(repo / "README.md"),
        ],
    )
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "sess-1")

    block = g.generate()

    assert "Files explored (2)" in block
    assert "`src/app.py`" in block
    assert "`README.md`" in block
    assert "`src`" not in block
    assert "`.`" not in block

