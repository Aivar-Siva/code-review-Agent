"""Shared data schemas for the PR code review agent. No logic, no I/O."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class RiskLevel(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    SKIP = "skip"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    SUGGESTION = "suggestion"


class Category(str, Enum):
    SECURITY = "security"
    BUG = "bug"
    LOGIC = "logic"
    RELIABILITY = "reliability"
    TEST_COVERAGE = "test_coverage"


@dataclass
class DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: List[str]  # raw lines including +/-/space prefix
    diff_position_start: int  # position offset within the full patch


@dataclass
class ParsedFile:
    filename: str
    status: str  # added, modified, removed, renamed
    hunks: List[DiffHunk] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    patch: str = ""


@dataclass
class TriageResult:
    filename: str
    risk_level: RiskLevel
    reasoning: str


@dataclass
class Finding:
    file: str
    line: int
    severity: Severity
    category: Category
    confidence: float
    issue: str
    fix: str
    reasoning: str
    diff_position: Optional[int] = None
    fingerprint: Optional[str] = None


@dataclass
class ReviewOutput:
    filename: str
    findings: List[Finding] = field(default_factory=list)
