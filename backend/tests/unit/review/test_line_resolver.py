"""Tests for deterministic line number resolver."""

from codelens.review.infrastructure.line_resolver import (
    normalize_line,
    parse_hunks,
    resolve_from_file_content,
    resolve_from_hunk,
    split_and_normalize,
)


class TestNormalizeLine:
    def test_strips_whitespace(self) -> None:
        assert normalize_line("  hello  ") == "hello"

    def test_strips_added_marker(self) -> None:
        assert normalize_line("+new code") == "new code"

    def test_strips_deleted_marker(self) -> None:
        assert normalize_line("-old code") == "old code"

    def test_preserves_plain_code(self) -> None:
        assert normalize_line("plain code") == "plain code"

    def test_empty_after_strip(self) -> None:
        assert normalize_line("   ") == ""


class TestSplitAndNormalize:
    def test_filters_empty_lines(self) -> None:
        code = "line1\n\nline2\n   \nline3"
        assert split_and_normalize(code) == ["line1", "line2", "line3"]

    def test_strips_diff_markers(self) -> None:
        code = "+added\n-deleted\n context"
        assert split_and_normalize(code) == ["added", "deleted", "context"]


SAMPLE_DIFF = """\
diff --git a/example.py b/example.py
index abc123..def456 100644
--- a/example.py
+++ b/example.py
@@ -10,7 +10,8 @@ def old_func():
     context_line_1
     context_line_2
     context_line_3
-    old_changed_line
+    new_changed_line_1
+    new_changed_line_2
     context_line_4
     context_line_5
     context_line_6
"""


class TestParseHunks:
    def test_parses_single_hunk(self) -> None:
        hunks = parse_hunks(SAMPLE_DIFF)
        assert len(hunks) == 1
        hunk = hunks[0]
        assert hunk.old_start == 10
        assert hunk.old_count == 7
        assert hunk.new_start == 10
        assert hunk.new_count == 8

    def test_parses_multiple_hunks(self) -> None:
        diff = """\
@@ -5,3 +5,4 @@
 context
-old
+new1
+new2
 context
@@ -20,2 +21,3 @@
 context
+added
 context
"""
        hunks = parse_hunks(diff)
        assert len(hunks) == 2
        assert hunks[0].old_start == 5
        assert hunks[1].old_start == 20


class TestResolveFromHunk:
    def test_matches_added_line(self) -> None:
        result = resolve_from_hunk(SAMPLE_DIFF, "new_changed_line_1")
        assert result == (13, 13)

    def test_matches_multiple_added_lines(self) -> None:
        result = resolve_from_hunk(SAMPLE_DIFF, "new_changed_line_1\nnew_changed_line_2")
        assert result == (13, 14)

    def test_matches_context_line(self) -> None:
        result = resolve_from_hunk(SAMPLE_DIFF, "context_line_3")
        assert result == (12, 12)

    def test_matches_with_diff_markers(self) -> None:
        result = resolve_from_hunk(SAMPLE_DIFF, "+new_changed_line_1\n+new_changed_line_2")
        assert result == (13, 14)

    def test_returns_none_when_no_match(self) -> None:
        result = resolve_from_hunk(SAMPLE_DIFF, "nonexistent_code")
        assert result is None

    def test_returns_none_for_empty_existing_code(self) -> None:
        result = resolve_from_hunk(SAMPLE_DIFF, "")
        assert result is None

    def test_returns_none_for_empty_diff(self) -> None:
        result = resolve_from_hunk("", "some_code")
        assert result is None

    def test_old_side_fallback(self) -> None:
        diff = """\
@@ -10,3 +10,2 @@
 context
-deleted_line
 context
"""
        result = resolve_from_hunk(diff, "deleted_line")
        assert result == (11, 11)


class TestResolveFromFileContent:
    def test_matches_single_line(self) -> None:
        content = "line1\nline2\nline3\nline4\nline5"
        result = resolve_from_file_content(content, "line3")
        assert result == (3, 3)

    def test_matches_multiple_lines(self) -> None:
        content = "line1\nline2\nline3\nline4\nline5"
        result = resolve_from_file_content(content, "line2\nline3\nline4")
        assert result == (2, 4)

    def test_skips_blank_lines(self) -> None:
        content = "line1\n\nline2\n\nline3"
        result = resolve_from_file_content(content, "line1\nline2\nline3")
        assert result == (1, 5)

    def test_returns_none_when_no_match(self) -> None:
        content = "line1\nline2\nline3"
        result = resolve_from_file_content(content, "nonexistent")
        assert result is None

    def test_returns_none_for_empty_existing_code(self) -> None:
        content = "line1\nline2"
        result = resolve_from_file_content(content, "")
        assert result is None

    def test_handles_windows_line_endings(self) -> None:
        content = "line1\r\nline2\r\nline3"
        result = resolve_from_file_content(content, "line2")
        assert result == (2, 2)


class TestIntegration:
    def test_hunk_match_preferred_over_file_content(self) -> None:
        diff = """\
@@ -5,3 +5,4 @@
 context
-old
+new1
+new2
 context
"""
        file_content = "line1\nline2\nline3\nline4\nline5\nnew1\nnew2\nline8"
        hunk_result = resolve_from_hunk(diff, "new1\nnew2")
        file_result = resolve_from_file_content(file_content, "new1\nnew2")
        assert hunk_result == (6, 7)
        assert file_result == (6, 7)
