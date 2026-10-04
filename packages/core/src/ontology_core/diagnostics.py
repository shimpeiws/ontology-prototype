"""Shared validation diagnostics for workspace loading.

Apps (CLI, future API) present these diagnostics; they never build their own.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Diagnostic:
    """One actionable finding about a workspace definition.

    ``file`` is the workspace file name (for example ``domain.yaml``) and
    ``path`` is a dotted location inside that document (for example
    ``entities.Job.properties.assignee.ref``). ``line`` is the 1-based YAML
    line number when the parser reports one.
    """

    severity: str
    code: str
    message: str
    file: str | None = None
    path: str | None = None
    line: int | None = None
    hint: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "file": self.file,
            "path": self.path,
            "line": self.line,
            "hint": self.hint,
        }

    def format(self) -> str:
        location = self.file or "<workspace>"
        if self.line is not None:
            location = f"{location}:{self.line}"
        if self.path:
            location = f"{location}:{self.path}"
        text = f"{self.severity.upper()} {self.code} {location} - {self.message}"
        if self.hint:
            text = f"{text} (hint: {self.hint})"
        return text


@dataclass
class Diagnostics:
    """Ordered collection of diagnostics produced during loading."""

    items: list[Diagnostic] = field(default_factory=list)

    def error(
        self,
        code: str,
        message: str,
        *,
        file: str | None = None,
        path: str | None = None,
        line: int | None = None,
        hint: str | None = None,
    ) -> None:
        self.items.append(Diagnostic(ERROR, code, message, file, path, line, hint))

    def warning(
        self,
        code: str,
        message: str,
        *,
        file: str | None = None,
        path: str | None = None,
        line: int | None = None,
        hint: str | None = None,
    ) -> None:
        self.items.append(Diagnostic(WARNING, code, message, file, path, line, hint))

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.items if d.severity == ERROR]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.items if d.severity == WARNING]

    @property
    def has_errors(self) -> bool:
        return any(d.severity == ERROR for d in self.items)
