"""Isolated Python environments for Docling and the speech engines.

Why: the speech packages pin conflicting versions of torch, transformers
and numpy (for example ``chatterbox-tts`` pins ``torch==2.6.0`` and
``transformers==5.2.0`` while ``qwen-tts`` pins ``transformers==4.57.3``).
Installing them into Colab's own Python would break it, or each other.

How: each environment is a self-contained virtual environment under
``/content/lektor_envs/<name>`` created with ``uv``. ``uv`` caches every
wheel, so torch is downloaded once and hard-linked into later
environments. ``--torch-backend auto`` makes uv choose the PyTorch build
that matches the GPU driver (or the CPU build when there is no GPU).

The main process never imports engine packages. It starts a worker
(``python -m lektor.engines.worker``) inside the environment and talks
to it with JSON lines over stdin/stdout (see :mod:`lektor.engines.worker`).
Lektor's own code reaches the environment through ``PYTHONPATH``, pointing
at a small copy of the ``lektor`` package, so nothing else leaks in.

Environments are created lazily, the first time they are needed, and a
marker file records the exact specification so a changed spec triggers a
rebuild. They live on the Colab machine (not Drive), so they are rebuilt
after each new Colab session; this takes a few minutes per engine.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .errors import LektorError


@dataclass(frozen=True)
class EnvSpec:
    name: str
    packages: tuple[str, ...]
    python: str = "3.12"
    # Extra pip arguments, for example an index URL for a git package.
    pip_args: tuple[str, ...] = ()
    # Python snippet that must run without error once installed.
    check: str = ""
    # Packages installed after the main set, each with --no-deps.
    no_deps: tuple[str, ...] = ()
    notes: str = ""

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:16]


# Engine environments are registered by lektor.engines.registry; Docling is
# registered here because ingest needs it before any engine.
ENV_SPECS: dict[str, EnvSpec] = {
    "docling": EnvSpec(
        name="docling",
        packages=("docling>=2.102,<3",),
        check="import docling, docling_core",
        notes="Docling (MIT) for PDF, DOCX, PPTX and HTML layout analysis.",
    ),
}


def register_env(spec: EnvSpec) -> None:
    ENV_SPECS[spec.name] = spec


def in_colab() -> bool:
    return "COLAB_RELEASE_TAG" in os.environ or "google.colab" in sys.modules


def env_root() -> Path:
    if os.environ.get("LEKTOR_ENV_ROOT"):
        return Path(os.environ["LEKTOR_ENV_ROOT"])
    if Path("/content").is_dir():
        return Path("/content/lektor_envs")
    return Path.home() / ".cache" / "lektor" / "envs"


def env_dir(name: str) -> Path:
    return env_root() / name


def env_python(name: str) -> Path:
    d = env_dir(name)
    return d / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _marker(name: str) -> Path:
    return env_dir(name) / ".lektor-ready.json"


def is_ready(name: str) -> bool:
    spec = ENV_SPECS.get(name)
    m = _marker(name)
    if spec is None or not m.exists() or not env_python(name).exists():
        return False
    try:
        return json.loads(m.read_text()).get("digest") == spec.digest()
    except Exception:
        return False


def _uv() -> list[str]:
    exe = shutil.which("uv")
    if exe:
        return [exe]
    # Colab normally has uv; otherwise install it into the main Python.
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "uv"], check=True)
    exe = shutil.which("uv")
    return [exe] if exe else [sys.executable, "-m", "uv"]


def _run(cmd: list[str], log: Callable[[str], None], env: Optional[dict] = None) -> None:
    log("$ " + " ".join(cmd))
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    tail = "\n".join(proc.stdout.strip().splitlines()[-25:])
    if proc.returncode != 0:
        raise LektorError(
            f"Installing a component failed (command exited with code {proc.returncode}).\n{tail}",
            hint="Run the cell again; network hiccups are common on Colab. If it fails twice, paste "
                 "this message to the developer.",
        )
    if tail:
        log(tail)


def lektor_src() -> Path:
    """A clean copy of the lektor package for PYTHONPATH inside environments."""
    pkg = Path(__file__).resolve().parent
    dest_root = env_root() / "_lektor_src"
    dest = dest_root / "lektor"
    # Always refresh: it is small, and the code may have changed (new branch).
    if dest.exists():
        shutil.rmtree(dest)
    dest_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(pkg, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return dest_root


def ensure_env(name: str, log: Callable[[str], None] = print, force: bool = False) -> Path:
    """Create (if needed) the environment ``name`` and return its Python."""
    if name not in ENV_SPECS:
        raise LektorError(f"There is no environment called '{name}'.")
    spec = ENV_SPECS[name]
    if is_ready(name) and not force:
        return env_python(name)
    d = env_dir(name)
    if d.exists():
        shutil.rmtree(d)
    d.parent.mkdir(parents=True, exist_ok=True)
    uv = _uv()
    log(f"Preparing the '{name}' environment (first time in this Colab session; this can take a few minutes)...")
    _run(uv + ["venv", "--quiet", "--python", spec.python, str(d)], log)
    py = str(env_python(name))
    base = uv + ["pip", "install", "--python", py, "--torch-backend", "auto"]
    _run(base + list(spec.pip_args) + list(spec.packages), log)
    for pkg in spec.no_deps:
        _run(base + ["--no-deps", pkg], log)
    if spec.check:
        _run([py, "-c", spec.check], log)
    _marker(name).write_text(json.dumps({"digest": spec.digest(), "spec": asdict(spec)}, indent=1))
    log(f"The '{name}' environment is ready.")
    return env_python(name)


def env_vars(name: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(lektor_src())
    env["PYTHONNOUSERSITE"] = "1"
    env.pop("PYTHONHOME", None)
    return env


def run_module(name: str, module: str, args: list[str], log: Callable[[str], None] = print,
               timeout: Optional[float] = None) -> subprocess.CompletedProcess:
    """Run ``python -m module args`` inside environment ``name``."""
    py = ensure_env(name, log)
    proc = subprocess.run([str(py), "-m", module, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, env=env_vars(name), timeout=timeout)
    return proc


def describe() -> list[dict]:
    out = []
    for name, spec in ENV_SPECS.items():
        out.append({"name": name, "ready": is_ready(name), "path": str(env_dir(name)), "packages": list(spec.packages)})
    return out


__all__ = ["EnvSpec", "ENV_SPECS", "register_env", "ensure_env", "env_python", "env_vars", "run_module",
           "is_ready", "in_colab", "env_root", "describe", "field"]
