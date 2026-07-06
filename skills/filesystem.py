"""
skills/filesystem.py — Sandboxed local filesystem read/write utilities.

The :class:`WorkspaceReader` and :class:`WorkspaceWriter` classes confine all
file operations to a single root directory (the *workspace*) defined in
``config.WORKSPACE_PATH``.  Any path that attempts to escape the sandbox via
``../`` traversal raises a :class:`SandboxViolationError`.

The :class:`ModuleManager` convenience class combines both utilities and adds
hot-reload support so the engine can patch and re-import Python modules while
running.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
import types
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SandboxViolationError(PermissionError):
    """Raised when a requested path escapes the sandboxed workspace root."""


class WorkspaceError(OSError):
    """General workspace I/O error."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_safe(workspace_root: Path, relative_path: str | Path) -> Path:
    """
    Resolve *relative_path* inside *workspace_root* and verify it does not
    escape the sandbox.

    Raises
    ------
    SandboxViolationError
        If the resolved absolute path is outside *workspace_root*.
    """
    target = (workspace_root / relative_path).resolve()
    try:
        target.relative_to(workspace_root.resolve())
    except ValueError as exc:
        raise SandboxViolationError(
            f"Path '{relative_path}' escapes the workspace sandbox "
            f"(root: '{workspace_root}')"
        ) from exc
    return target


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


class WorkspaceReader:
    """
    Read-only access to files within the sandboxed workspace directory.

    Parameters
    ----------
    workspace_root:
        Absolute path to the sandboxed workspace.  Defaults to
        ``config.WORKSPACE_PATH``.
    """

    def __init__(self, workspace_root: str | Path | None = None) -> None:
        root = workspace_root or config.WORKSPACE_PATH
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """Absolute path of the workspace root."""
        return self._root

    def read_text(self, relative_path: str | Path, encoding: str = "utf-8") -> str:
        """
        Read and return the text content of a file inside the workspace.

        Parameters
        ----------
        relative_path:
            Path relative to the workspace root.
        encoding:
            Text encoding (default ``utf-8``).

        Returns
        -------
        str
            Full text content of the file.

        Raises
        ------
        SandboxViolationError
            If *relative_path* escapes the workspace.
        WorkspaceError
            If the file does not exist or cannot be read.
        """
        target = _resolve_safe(self._root, relative_path)
        try:
            return target.read_text(encoding=encoding)
        except OSError as exc:
            raise WorkspaceError(f"Cannot read '{target}': {exc}") from exc

    def read_bytes(self, relative_path: str | Path) -> bytes:
        """Return raw bytes of a file inside the workspace."""
        target = _resolve_safe(self._root, relative_path)
        try:
            return target.read_bytes()
        except OSError as exc:
            raise WorkspaceError(f"Cannot read bytes from '{target}': {exc}") from exc

    def list_files(self, pattern: str = "**/*.py") -> list[Path]:
        """
        Return a list of :class:`~pathlib.Path` objects matching *pattern*
        within the workspace root (relative paths).
        """
        return [p.relative_to(self._root) for p in self._root.glob(pattern)]

    def exists(self, relative_path: str | Path) -> bool:
        """Return *True* if *relative_path* exists inside the workspace."""
        try:
            return _resolve_safe(self._root, relative_path).exists()
        except SandboxViolationError:
            return False


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


class WorkspaceWriter:
    """
    Write / patch access to files within the sandboxed workspace directory.

    Parameters
    ----------
    workspace_root:
        Absolute path to the sandboxed workspace.  Defaults to
        ``config.WORKSPACE_PATH``.
    """

    def __init__(self, workspace_root: str | Path | None = None) -> None:
        root = workspace_root or config.WORKSPACE_PATH
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """Absolute path of the workspace root."""
        return self._root

    def write_text(
        self,
        relative_path: str | Path,
        content: str,
        encoding: str = "utf-8",
        create_parents: bool = True,
    ) -> Path:
        """
        Write *content* to a file inside the workspace.

        Parameters
        ----------
        relative_path:
            Destination path relative to the workspace root.
        content:
            Text to write.
        encoding:
            Text encoding (default ``utf-8``).
        create_parents:
            Create intermediate directories if they do not yet exist.

        Returns
        -------
        pathlib.Path
            Absolute path of the written file.
        """
        target = _resolve_safe(self._root, relative_path)
        if create_parents:
            target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(content, encoding=encoding)
            logger.info("WorkspaceWriter: wrote %d chars to '%s'", len(content), target)
            return target
        except OSError as exc:
            raise WorkspaceError(f"Cannot write to '{target}': {exc}") from exc

    def write_bytes(
        self,
        relative_path: str | Path,
        data: bytes,
        create_parents: bool = True,
    ) -> Path:
        """Write raw *data* bytes to a file inside the workspace."""
        target = _resolve_safe(self._root, relative_path)
        if create_parents:
            target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_bytes(data)
            logger.info("WorkspaceWriter: wrote %d bytes to '%s'", len(data), target)
            return target
        except OSError as exc:
            raise WorkspaceError(f"Cannot write bytes to '{target}': {exc}") from exc

    def patch_text(
        self,
        relative_path: str | Path,
        old: str,
        new: str,
        encoding: str = "utf-8",
    ) -> int:
        """
        Replace occurrences of *old* with *new* inside an existing file.

        Returns
        -------
        int
            Number of replacements made.
        """
        reader = WorkspaceReader(self._root)
        source = reader.read_text(relative_path, encoding=encoding)
        patched, count = source.replace(old, new), source.count(old)
        self.write_text(relative_path, patched, encoding=encoding)
        logger.info(
            "WorkspaceWriter.patch_text: %d replacement(s) in '%s'", count, relative_path
        )
        return count

    def delete(self, relative_path: str | Path) -> None:
        """Delete a file inside the workspace."""
        target = _resolve_safe(self._root, relative_path)
        try:
            target.unlink()
            logger.info("WorkspaceWriter: deleted '%s'", target)
        except OSError as exc:
            raise WorkspaceError(f"Cannot delete '{target}': {exc}") from exc


# ---------------------------------------------------------------------------
# Module manager (hot-reload)
# ---------------------------------------------------------------------------


class ModuleManager:
    """
    Combines read/write utilities with Python module hot-reload support.

    Typical workflow
    ~~~~~~~~~~~~~~~~
    1. Use :meth:`write_module` to write or patch a ``.py`` file.
    2. Call :meth:`reload_module` to import / re-import it at runtime.
    3. Inspect returned :class:`types.ModuleType` as needed.
    """

    def __init__(self, workspace_root: str | Path | None = None) -> None:
        root = workspace_root or config.WORKSPACE_PATH
        self._root = Path(root)
        self.reader = WorkspaceReader(self._root)
        self.writer = WorkspaceWriter(self._root)

    def write_module(self, relative_path: str | Path, source: str) -> Path:
        """Write Python *source* to *relative_path* inside the workspace."""
        return self.writer.write_text(relative_path, source)

    def reload_module(self, relative_path: str | Path) -> types.ModuleType:
        """
        Import (or re-import) a Python module from *relative_path*.

        The module is identified by a dotted name derived from the path.
        Any existing cached module under that name is evicted from
        ``sys.modules`` before re-import, ensuring the fresh source is used.

        Raises
        ------
        WorkspaceError
            If the file does not exist or importlib cannot load it.
        """
        target = _resolve_safe(self._root, relative_path)
        if not target.exists():
            raise WorkspaceError(f"Module file not found: '{target}'")

        module_name = _path_to_module_name(self._root, target)

        # Evict stale module from cache
        sys.modules.pop(module_name, None)

        spec = importlib.util.spec_from_file_location(module_name, target)
        if spec is None or spec.loader is None:
            raise WorkspaceError(f"Cannot create module spec for '{target}'")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception as exc:
            sys.modules.pop(module_name, None)
            raise WorkspaceError(f"Error loading module '{module_name}': {exc}") from exc

        logger.info("ModuleManager: loaded module '%s' from '%s'", module_name, target)
        return module

    def get_attribute(self, relative_path: str | Path, attr: str) -> Any:
        """
        Convenience: reload module and return the named attribute.

        Useful for quickly fetching a freshly patched class or function.
        """
        module = self.reload_module(relative_path)
        if not hasattr(module, attr):
            raise AttributeError(
                f"Module '{relative_path}' has no attribute '{attr}'"
            )
        return getattr(module, attr)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _path_to_module_name(workspace_root: Path, absolute_path: Path) -> str:
    """Convert an absolute file path to a dotted Python module name."""
    relative = absolute_path.relative_to(workspace_root.resolve())
    parts = list(relative.with_suffix("").parts)
    return ".".join(parts)
