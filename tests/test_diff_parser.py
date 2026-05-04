"""Unit tests for diff_parser. No network calls."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.diff_parser import parse_patch, get_diff_position, extract_context_window


SAMPLE_PATCH = open(os.path.join(os.path.dirname(__file__), "fixtures/sample_patch.txt")).read()


def test_parse_patch_basic():
    pf = parse_patch("users.py", SAMPLE_PATCH)
    assert pf.filename == "users.py"
    assert len(pf.hunks) == 1
    assert pf.additions == 7
    assert pf.deletions == 2


def test_parse_patch_empty():
    pf = parse_patch("readme.md", "")
    assert pf.filename == "readme.md"
    assert pf.hunks == []
    assert pf.additions == 0


def test_get_diff_position_returns_int():
    pf = parse_patch("users.py", SAMPLE_PATCH)
    pos = get_diff_position(pf, 3)  # line 3 in new file
    assert pos is None or isinstance(pos, int)


def test_extract_context_window():
    pf = parse_patch("users.py", SAMPLE_PATCH)
    ctx = extract_context_window(pf, 0)
    assert isinstance(ctx, str)
    assert len(ctx) > 0


def test_parse_multi_hunk():
    patch = (
        "@@ -1,3 +1,3 @@\n"
        " line1\n-old2\n+new2\n line3\n"
        "@@ -10,3 +10,3 @@\n"
        " line10\n-old11\n+new11\n line12\n"
    )
    pf = parse_patch("multi.py", patch)
    assert len(pf.hunks) == 2
    assert pf.additions == 2
    assert pf.deletions == 2


if __name__ == "__main__":
    test_parse_patch_basic()
    test_parse_patch_empty()
    test_get_diff_position_returns_int()
    test_extract_context_window()
    test_parse_multi_hunk()
    print("All tests passed.")
