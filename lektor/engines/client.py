"""Main-process side of an engine worker.

:class:`WorkerEngine` looks like any other :class:`~lektor.engines.base.Engine`
but forwards every call to a worker process running in the engine's own
environment (see :mod:`lektor.engines.worker` and :mod:`lektor.envs`).
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from .. import envs
from ..errors import EngineError
from .base import Engine


class WorkerEngine(Engine):
    def __init__(self, cls: type[Engine], log: Callable[[str], None] = print, python: Optional[str] = None,
                 options: Optional[dict] = None, load_timeout: float = 3600, call_timeout: float = 1800) -> None:
        super().__init__()
        self.cls = cls
        self.name = cls.name
        self.sample_rate = cls.sample_rate
        self.log = log
        self.python = python
        self.worker_options = options or {}
        self.load_timeout = load_timeout
        self.call_timeout = call_timeout
        self.proc: Optional[subprocess.Popen] = None
        self._lines: "queue.Queue[str]" = queue.Queue()
        self._stderr_tail: deque = deque(maxlen=60)
        self.info: dict = {}

    # -- process management -------------------------------------------------
    def _start(self) -> None:
        if self.python:
            py, env = self.python, dict(os.environ)
            env["PYTHONPATH"] = str(envs.lektor_src())
        else:
            if self.cls.env is None:
                py, env = sys.executable, dict(os.environ)
            else:
                py = str(envs.ensure_env(self.cls.env, self.log))
                env = envs.env_vars(self.cls.env)
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        self.proc = subprocess.Popen(
            [py, "-u", "-m", "lektor.engines.worker", "--engine", self.cls.name],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, bufsize=1,
        )
        threading.Thread(target=self._pump, args=(self.proc.stdout, self._lines), daemon=True).start()
        threading.Thread(target=self._pump_err, args=(self.proc.stderr,), daemon=True).start()
        hello = self._read(600)
        if not hello.get("ready"):
            raise EngineError(f"The {self.name} engine did not start.\n{self._tail()}")

    @staticmethod
    def _pump(stream, q) -> None:
        for line in stream:
            q.put(line)
        q.put("")

    def _pump_err(self, stream) -> None:
        for line in stream:
            self._stderr_tail.append(line.rstrip())

    def _tail(self, n: int = 25) -> str:
        return "\n".join(list(self._stderr_tail)[-n:])

    def _read(self, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill()
                raise EngineError(f"The {self.name} engine stopped responding (no answer in {int(timeout)} s).",
                                  hint="Run the cell again; Lektor will carry on where it stopped.")
            try:
                line = self._lines.get(timeout=min(remaining, 5))
            except queue.Empty:
                if self.proc is not None and self.proc.poll() is not None:
                    raise EngineError(self._crash_message())
                continue
            if line == "":
                raise EngineError(self._crash_message())
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                self._stderr_tail.append(line.rstrip())

    def _crash_message(self) -> str:
        tail = self._tail()
        hint = ""
        if "out of memory" in tail.lower() or "CUDA out of memory" in tail:
            hint = " The GPU ran out of memory."
        return f"The {self.name} engine stopped unexpectedly.{hint}\nLast messages:\n{tail}"

    def _request(self, obj: dict, timeout: float) -> dict:
        if self.proc is None or self.proc.poll() is not None:
            raise EngineError(f"The {self.name} engine is not running.")
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()
        rep = self._read(timeout)
        if not rep.get("ok"):
            err = rep.get("error", "unknown error")
            raise EngineError(f"The {self.name} engine reported a problem: {err}",
                              hint="See the technical details below." if rep.get("traceback") else "")
        return rep

    def _kill(self) -> None:
        if self.proc is not None:
            try:
                self.proc.kill()
            except Exception:
                pass
            self.proc = None

    # -- Engine interface -------------------------------------------------------
    def load(self) -> None:
        self._start()
        rep = self._request({"op": "load", "options": self.worker_options}, self.load_timeout)
        self.info = rep.get("info", {})
        self.sample_rate = int(self.info.get("sample_rate", self.sample_rate))
        self.loaded = True

    def synthesize_batch(self, items: list[dict]) -> list[np.ndarray]:
        rep = self._request({"op": "synth", "items": items}, self.call_timeout)
        self.sample_rate = int(rep.get("sample_rate", self.sample_rate))
        out = []
        for f in rep["files"]:
            out.append(np.load(f))
            try:
                Path(f).unlink()
            except OSError:
                pass
        return out

    def synthesize(self, text: str, voice: str, params: dict[str, Any], seed: int,
                   reference: Optional[str] = None) -> np.ndarray:
        return self.synthesize_batch([{"text": text, "voice": voice, "params": params, "seed": seed,
                                       "reference": reference}])[0]

    def call(self, method: str, timeout: Optional[float] = None, **kwargs) -> Any:
        return self._request({"op": "call", "method": method, "kwargs": kwargs},
                             timeout or self.call_timeout).get("result")

    def unload(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.stdin.write(json.dumps({"op": "quit"}) + "\n")
                self.proc.stdin.flush()
                self.proc.wait(timeout=60)
            except Exception:
                self._kill()
        self.proc = None
        self.loaded = False
