"""Parse GitHub unified diff patches into structured ParsedFile objects. Pure functions, no I/O."""
import re
from typing import Optional, List

from agent.models import DiffHunk, ParsedFile

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_patch(filename: str, patch: str, status: str = "modified") -> ParsedFile:
    if not patch:
        return ParsedFile(filename=filename, status=status, patch=patch)

    hunks: List[DiffHunk] = []
    additions = deletions = 0
    diff_position = 0  # 1-based position within the patch (for GitHub API)

    current_hunk: Optional[DiffHunk] = None

    for line in patch.splitlines():
        diff_position += 1
        m = _HUNK_HEADER.match(line)
        if m:
            if current_hunk:
                hunks.append(current_hunk)
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) is not None else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) is not None else 1
            current_hunk = DiffHunk(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                lines=[],
                diff_position_start=diff_position,
            )
        elif current_hunk is not None:
            current_hunk.lines.append(line)
            if line.startswith("+"):
                additions += 1
            elif line.startswith("-"):
                deletions += 1

    if current_hunk:
        hunks.append(current_hunk)

    return ParsedFile(
        filename=filename,
        status=status,
        hunks=hunks,
        additions=additions,
        deletions=deletions,
        patch=patch,
    )


def get_diff_position(parsed_file: ParsedFile, new_line_number: int) -> Optional[int]:
    """Return the diff position (1-based offset in patch) for a given new-file line number."""
    position = 0
    for hunk in parsed_file.hunks:
        position += 1  # the @@ header line itself
        new_line = hunk.new_start
        for line in hunk.lines:
            position += 1
            if not line.startswith("-"):
                if new_line == new_line_number:
                    return hunk.diff_position_start + (position - 1)
                new_line += 1
    return None


def extract_context_window(parsed_file: ParsedFile, hunk_index: int, context_lines: int = 20) -> str:
    """Return a text snippet of the hunk plus surrounding context lines."""
    if hunk_index >= len(parsed_file.hunks):
        return ""
    hunk = parsed_file.hunks[hunk_index]
    lines = hunk.lines
    # Include a few lines before/after if adjacent hunks exist
    before = parsed_file.hunks[hunk_index - 1].lines[-context_lines:] if hunk_index > 0 else []
    after = parsed_file.hunks[hunk_index + 1].lines[:context_lines] if hunk_index + 1 < len(parsed_file.hunks) else []
    return "\n".join(before + lines + after)
