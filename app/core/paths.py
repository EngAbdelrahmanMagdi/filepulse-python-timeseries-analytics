from pathlib import Path, PureWindowsPath


class UnsafePath(ValueError):
    pass


def normalize_relative(value: str) -> str:
    """Domain/wire paths are relative on every supported operating system."""
    if not value or len(value) > 4096 or any(ord(char) < 32 for char in value):
        raise UnsafePath("invalid path")
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or PureWindowsPath(value).drive:
        raise UnsafePath("absolute or drive-qualified path")
    parts = normalized.split("/")
    if ".." in parts or any(":" in part for part in parts):
        raise UnsafePath("traversal or drive-qualified path")
    parts = [part for part in parts if part not in ("", ".")]
    if not parts:
        raise UnsafePath("root is not an event path")
    return "/".join(parts)


class RootPaths:
    """One root-containment policy, with distinct watcher and wire entry points."""

    def __init__(self, root: Path) -> None:
        if root.is_symlink():
            raise UnsafePath("root cannot be a symlink")
        self.root = root.resolve(strict=False)
        if not self.root.is_dir():
            raise ValueError("WATCH_ROOT must be an existing directory")

    def relative(self, value: str) -> str:
        return self._contained(self.root / normalize_relative(value))

    def observed(self, value: str | Path) -> str:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        return self._contained(candidate)

    def _contained(self, candidate: Path) -> str:
        # Check lexical components for symlinks before resolving. Non-strict resolution
        # handles deleted paths while still resolving any existing parent components.
        try:
            lexical = candidate.absolute().relative_to(self.root)
        except ValueError as exc:
            raise UnsafePath("path outside root") from exc
        if ".." in lexical.parts:
            raise UnsafePath("traversal")
        current = self.root
        for part in lexical.parts:
            current /= part
            if current.is_symlink():
                raise UnsafePath("symlink path")
        try:
            resolved = candidate.resolve(strict=False)
            relative = resolved.relative_to(self.root)
        except (ValueError, OSError, RuntimeError) as exc:
            raise UnsafePath("path outside root or cannot resolve") from exc
        return normalize_relative(relative.as_posix())

    def local(self, relative: str) -> Path:
        return self.root / self.relative(relative)
