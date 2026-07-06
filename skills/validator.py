"""
skills/validator.py — Pre-execution verification for Python modules.

Before the engine hot-reloads a workspace module it should verify that the
source is at least syntactically valid and (optionally) passes a lightweight
style/lint check.  This file provides:

* :class:`ValidationResult` — structured outcome of a validation run.
* :func:`validate_syntax` — fast AST-based syntax check using ``ast.parse``.
* :func:`validate_with_pyflakes` — deeper static analysis using *pyflakes*
  (if installed) that catches undefined names, unused imports, etc.
* :class:`ModuleValidator` — orchestrating class that chains validators and
  returns a unified :class:`ValidationResult`.
"""

from __future__ import annotations

import ast
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

Severity = Literal["ok", "warning", "error"]


@dataclass
class ValidationIssue:
    """A single finding produced by a validator."""

    line: int | None
    column: int | None
    severity: Severity
    message: str
    source: str  # name of the validator that produced this issue

    def __str__(self) -> str:
        loc = ""
        if self.line is not None:
            loc = f":{self.line}"
            if self.column is not None:
                loc += f":{self.column}"
        return f"[{self.severity.upper()}] {self.source}{loc} — {self.message}"


@dataclass
class ValidationResult:
    """
    Aggregated outcome of one or more validation passes on a source file.

    Attributes
    ----------
    path:
        Absolute or relative path of the validated file.
    passed:
        ``True`` only if *no* error-severity issues were found.
    issues:
        Ordered list of all :class:`ValidationIssue` objects collected.
    """

    path: str
    passed: bool = True
    issues: list[ValidationIssue] = field(default_factory=list)

    def add_issue(self, issue: ValidationIssue) -> None:
        """Append *issue* and demote *passed* to ``False`` for error severity."""
        self.issues.append(issue)
        if issue.severity == "error":
            self.passed = False

    @property
    def errors(self) -> list[ValidationIssue]:
        """All issues with severity ``"error"``."""
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        """All issues with severity ``"warning"``."""
        return [i for i in self.issues if i.severity == "warning"]

    def summary(self) -> str:
        """Human-readable one-line summary."""
        status = "PASSED" if self.passed else "FAILED"
        return (
            f"{status} — {self.path} "
            f"({len(self.errors)} error(s), {len(self.warnings)} warning(s))"
        )

    def __str__(self) -> str:
        lines = [self.summary()]
        for issue in self.issues:
            lines.append(f"  {issue}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Syntax validator (built-in, no deps)
# ---------------------------------------------------------------------------


def validate_syntax(source: str, filename: str = "<string>") -> ValidationResult:
    """
    Parse *source* with :func:`ast.parse` and return a :class:`ValidationResult`.

    This check is instant and requires no external tools.  It catches:
    * ``SyntaxError`` — invalid Python syntax.
    * ``IndentationError`` — malformed indentation.
    * ``ValueError`` — embedded null bytes or other parse-level errors.
    """
    result = ValidationResult(path=filename)
    try:
        ast.parse(source, filename=filename)
        logger.debug("validate_syntax: '%s' OK", filename)
    except SyntaxError as exc:
        result.add_issue(
            ValidationIssue(
                line=exc.lineno,
                column=exc.offset,
                severity="error",
                message=str(exc.msg),
                source="ast.parse",
            )
        )
        logger.warning("validate_syntax: SyntaxError in '%s': %s", filename, exc)
    except ValueError as exc:
        result.add_issue(
            ValidationIssue(
                line=None,
                column=None,
                severity="error",
                message=str(exc),
                source="ast.parse",
            )
        )
    return result


# ---------------------------------------------------------------------------
# Pyflakes validator (optional dependency)
# ---------------------------------------------------------------------------


def validate_with_pyflakes(
    source: str, filename: str = "<string>"
) -> ValidationResult:
    """
    Run *pyflakes* static analysis on *source*.

    Falls back gracefully if pyflakes is not installed — the result will
    contain a single warning noting that the check was skipped.

    Pyflakes detects:
    * Undefined names / missing imports.
    * Unused imports and variables.
    * Redefined-while-unused bindings.
    * Various other logical issues.
    """
    result = ValidationResult(path=filename)

    try:
        import pyflakes.api as pyflakes_api  # type: ignore[import]
    except ImportError:
        result.add_issue(
            ValidationIssue(
                line=None,
                column=None,
                severity="warning",
                message=(
                    "pyflakes is not installed; static analysis skipped. "
                    "Install it with: pip install pyflakes"
                ),
                source="pyflakes",
            )
        )
        logger.info("validate_with_pyflakes: pyflakes not available, skipping.")
        return result

    # pyflakes.api.check() returns the number of warnings/errors and prints
    # messages to stdout.  We use check_path / compile ourselves to capture.
    warning_count: int = pyflakes_api.check(
        source, filename=filename
    )

    if warning_count == 0:
        logger.debug("validate_with_pyflakes: '%s' clean", filename)
    else:
        # pyflakes prints directly; we add a generic summary issue.
        result.add_issue(
            ValidationIssue(
                line=None,
                column=None,
                severity="warning",
                message=f"pyflakes reported {warning_count} issue(s) — see stdout for details.",
                source="pyflakes",
            )
        )

    return result


# ---------------------------------------------------------------------------
# Subprocess-based linter wrapper (e.g. ruff / flake8)
# ---------------------------------------------------------------------------


def validate_with_linter(
    file_path: str | Path,
    linter: str = "ruff",
    extra_args: list[str] | None = None,
) -> ValidationResult:
    """
    Invoke an external linter (*ruff* by default) on *file_path* as a
    subprocess and parse its output into a :class:`ValidationResult`.

    The linter must be installed and on ``PATH``.  If the command cannot be
    found the result will contain a warning and *passed* will remain ``True``
    (the check is treated as non-blocking).

    Parameters
    ----------
    file_path:
        Absolute or relative path to the file to lint.
    linter:
        Executable name (default ``"ruff"``).  Use ``"flake8"`` or a custom
        tool if ruff is not available.
    extra_args:
        Additional CLI arguments forwarded to the linter.
    """
    path_str = str(file_path)
    result = ValidationResult(path=path_str)
    cmd = [linter, "--format=text", path_str] + (extra_args or [])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = (proc.stdout + proc.stderr).strip()
        if proc.returncode != 0 and output:
            for line in output.splitlines():
                issue = _parse_linter_line(line, linter)
                if issue:
                    result.add_issue(issue)
        logger.debug(
            "validate_with_linter (%s): rc=%d for '%s'",
            linter,
            proc.returncode,
            path_str,
        )
    except FileNotFoundError:
        result.add_issue(
            ValidationIssue(
                line=None,
                column=None,
                severity="warning",
                message=(
                    f"Linter '{linter}' not found on PATH; lint check skipped. "
                    f"Install it with: pip install {linter}"
                ),
                source=linter,
            )
        )
        logger.info("validate_with_linter: '%s' not found, skipping.", linter)
    except subprocess.TimeoutExpired:
        result.add_issue(
            ValidationIssue(
                line=None,
                column=None,
                severity="warning",
                message=f"Linter '{linter}' timed out.",
                source=linter,
            )
        )

    return result


def _parse_linter_line(line: str, source: str) -> ValidationIssue | None:
    """
    Attempt to parse a single linter output line into a :class:`ValidationIssue`.

    Handles the common ``filename:line:col: CODE message`` format used by
    flake8, ruff, and similar tools.
    """
    import re

    pattern = re.compile(
        r"(?P<file>.+?):(?P<line>\d+):(?P<col>\d+):\s*(?P<code>[A-Z]\d+)\s+(?P<msg>.+)"
    )
    m = pattern.match(line.strip())
    if not m:
        if line.strip():
            return ValidationIssue(
                line=None,
                column=None,
                severity="warning",
                message=line.strip(),
                source=source,
            )
        return None

    code = m.group("code")
    severity: Severity = "error" if code.startswith("E") else "warning"
    return ValidationIssue(
        line=int(m.group("line")),
        column=int(m.group("col")),
        severity=severity,
        message=f"{code}: {m.group('msg')}",
        source=source,
    )


# ---------------------------------------------------------------------------
# Orchestrating validator
# ---------------------------------------------------------------------------


class ModuleValidator:
    """
    Orchestrates multiple validation passes on a Python source file.

    Usage
    -----
    ::

        validator = ModuleValidator(run_pyflakes=True, run_linter=True)
        result = validator.validate_file(Path("workspace/my_module.py"))
        if not result.passed:
            print(result)
        else:
            # safe to hot-reload
            ...
    """

    def __init__(
        self,
        run_pyflakes: bool = True,
        run_linter: bool = False,
        linter: str = "ruff",
        linter_args: list[str] | None = None,
    ) -> None:
        self._run_pyflakes = run_pyflakes
        self._run_linter = run_linter
        self._linter = linter
        self._linter_args = linter_args or []

    def validate_source(self, source: str, filename: str = "<string>") -> ValidationResult:
        """
        Validate Python *source* text (no file I/O required).

        Runs syntax check and optionally pyflakes analysis.
        """
        # Syntax check first — if it fails, no point running further checks
        result = validate_syntax(source, filename)
        if not result.passed:
            return result

        if self._run_pyflakes:
            pf_result = validate_with_pyflakes(source, filename)
            for issue in pf_result.issues:
                result.add_issue(issue)

        return result

    def validate_file(self, file_path: str | Path) -> ValidationResult:
        """
        Validate a Python file on disk.

        Reads the file, runs syntax and optional pyflakes checks, then
        optionally invokes the configured external linter.
        """
        path = Path(file_path)
        if not path.exists():
            r = ValidationResult(path=str(path))
            r.add_issue(
                ValidationIssue(
                    line=None,
                    column=None,
                    severity="error",
                    message=f"File not found: '{path}'",
                    source="ModuleValidator",
                )
            )
            return r

        source = path.read_text(encoding="utf-8")
        result = self.validate_source(source, filename=str(path))

        if self._run_linter:
            linter_result = validate_with_linter(
                path, linter=self._linter, extra_args=self._linter_args
            )
            for issue in linter_result.issues:
                result.add_issue(issue)

        logger.info("ModuleValidator: %s", result.summary())
        return result
