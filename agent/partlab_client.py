"""CLI layer: run one partlab command, return a parsed Envelope. Knows nothing about agents or parts.

Every failure mode (JSON error, plaintext usage error, timeout, garbage output) becomes an Envelope,
so callers never need try/except around the CLI.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "partlab"
DEFAULT_PARTS = ROOT / "parts"


@dataclass
class Envelope:
    ok: bool
    result: object = None
    error: str | None = None
    error_type: str | None = None
    exit_code: int = 0
    raw: str = ""
    duration_ms: int = 0

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "raw"}


def make_workdir(path: Path, parts_dir: Path = DEFAULT_PARTS) -> Path:
    """Isolated session dir: partlab keeps state in CWD/.partlab, so one dir per run = no cross-talk."""
    path.mkdir(parents=True, exist_ok=True)
    link = path / "parts"
    if not link.exists():
        link.symlink_to(Path(parts_dir).resolve(), target_is_directory=True)
    return path


def parse_faults(spec: str | None) -> dict[str, str]:
    """PARTLAB_FAULTS="mesh:fail,solve:timeout,apply-load:garbage" -> {command: kind}. Each fires once."""
    if not spec:
        return {}
    return dict(item.split(":", 1) for item in spec.split(",") if ":" in item)


class PartLab:
    def __init__(self, workdir: Path, timeout_s: float = 30, faults: dict[str, str] | None = None):
        self.workdir = Path(workdir)
        self.timeout_s = timeout_s
        self.faults = dict(faults if faults is not None else parse_faults(os.environ.get("PARTLAB_FAULTS")))

    def run(self, *args: object) -> Envelope:
        argv = [str(a) for a in args]
        start = time.monotonic()
        fault = self.faults.pop(argv[0], None) if argv else None
        if fault == "timeout":
            return Envelope(False, error=f"partlab {argv[0]} timed out after {self.timeout_s}s (injected)",
                            error_type="TimeoutError", exit_code=-1)
        try:
            proc = subprocess.run([str(BINARY), *argv], cwd=self.workdir, capture_output=True,
                                  text=True, timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            return Envelope(False, error=f"partlab {argv[0]} timed out after {self.timeout_s}s",
                            error_type="TimeoutError", exit_code=-1)
        ms = int((time.monotonic() - start) * 1000)
        stdout = proc.stdout
        if fault == "garbage":
            stdout = "\x00partial wri"
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            first = (stdout or proc.stderr).strip().splitlines()[:1]
            return Envelope(False, error=first[0] if first else "no output", error_type="UsageError",
                            exit_code=proc.returncode, raw=stdout, duration_ms=ms)
        if fault == "fail":
            return Envelope(False, error=f"{argv[0]} failed: simulated solver/geometry failure (injected)",
                            error_type="MeshError" if argv[0] == "mesh" else "SolveError",
                            exit_code=1, raw=stdout, duration_ms=ms)
        return Envelope(bool(data.get("ok")), data.get("result"), data.get("error"), data.get("error_type"),
                        proc.returncode, stdout, ms)
