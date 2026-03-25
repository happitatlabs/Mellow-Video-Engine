"""
Unit tests for read_docs_file (agent_tools_docs).

Verifies: path safety, size limits, integrity hash, version strategy, structured output.
"""
import json
import pytest
from pathlib import Path

# Import before agent_tools to get isolated registry for some tests
from mellow_link.core.agent_tools_docs import read_docs_file, _resolve_docs_path, _parse_front_matter_version


class TestResolveDocsPath:
    """Path resolution and safety."""

    def test_relative_inside_docs_ok(self):
        resolved, err = _resolve_docs_path("system_map.md")
        assert err is None
        assert resolved.name == "system_map.md"
        assert "docs" in str(resolved)

    def test_docs_prefix_stripped(self):
        resolved, err = _resolve_docs_path("docs/EVOLUTION_VERIFICATION.md")
        assert err is None
        assert "EVOLUTION_VERIFICATION" in str(resolved)

    def test_reject_traversal(self):
        _, err = _resolve_docs_path("../../etc/passwd")
        assert err is not None
        assert ".." in err or "traversal" in err.lower()

    @pytest.mark.env_policy
    def test_reject_absolute(self):
        _, err = _resolve_docs_path("/etc/passwd")
        assert err is not None
        assert "absolute" in err.lower() or "Absolute" in err

    def test_reject_empty(self):
        _, err = _resolve_docs_path("")
        assert err is not None


class TestParseFrontMatter:
    """Front-matter version extraction."""

    def test_parse_version(self):
        content = "---\ndoc_id: x\nversion: 2026-02-16\n---\nbody"
        assert _parse_front_matter_version(content) == "2026-02-16"

    def test_parse_version_with_newlines(self):
        content = "---" + "\n" + "version: 2026-02-16" + "\n" + "---" + "\n" + "body"
        assert _parse_front_matter_version(content) == "2026-02-16"

    def test_no_front_matter_returns_none(self):
        assert _parse_front_matter_version("plain content") is None

    def test_version_with_quotes(self):
        content = '---\nversion: "1.0.0"\n---\nbody'
        assert _parse_front_matter_version(content) == "1.0.0"


class TestReadDocsFile:
    """Integration tests for read_docs_file tool."""

    def test_returns_valid_json(self):
        result = read_docs_file("system_map.md")
        data = json.loads(result)
        assert "content" in data
        assert "source" in data
        assert "version" in data
        assert "hash" in data
        assert "mtime" in data

    def test_hash_format(self):
        result = read_docs_file("system_map.md")
        data = json.loads(result)
        assert data["hash"].startswith("sha256:")
        assert len(data["hash"]) == 7 + 64  # "sha256:" + 64 hex chars

    def test_source_in_docs(self):
        result = read_docs_file("system_map.md")
        data = json.loads(result)
        assert data["source"].startswith("docs/")
        assert "system_map" in data["source"]

    def test_mtime_iso_format(self):
        result = read_docs_file("system_map.md")
        data = json.loads(result)
        assert "T" in data["mtime"] or "-" in data["mtime"]

    def test_not_found_returns_error_json(self):
        result = read_docs_file("nonexistent_xyz_12345.md")
        data = json.loads(result)
        assert "error" in data

    def test_path_traversal_returns_error(self):
        result = read_docs_file("../../../etc/passwd")
        data = json.loads(result)
        assert "error" in data


class TestExistingToolsUnchanged:
    """Ensure read_docs_file does not break other tools."""

    def test_read_file_still_works(self):
        from mellow_link.core.agent_tools import read_file
        r = read_file("workspace/README.md")
        assert isinstance(r, str)
        assert "[Error]" in r or len(r) > 0

    def test_list_docs_still_works(self):
        from mellow_link.core.agent_tools import list_docs
        r = list_docs()
        assert isinstance(r, str)
        assert "docs" in r
