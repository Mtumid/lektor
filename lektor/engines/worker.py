"""Engine worker: runs inside an engine's own environment.

Started by :class:`lektor.engines.client.WorkerEngine` as::

    python -m lektor.engines.worker --engine kokoro

and kept running, so the model stays loaded ("warm"). It speaks JSON lines:
one request per line on stdin, one reply per line on stdout.

Requests::

    {"op": "load", "options": {...}}
    {"op": "synth", "items": [{"text", "voice", "params", "seed", "reference"}, ...]}
    {"op": "call", "method": "design", "kwargs": {...}}     # engine-specific extras
    {"op": "info"}
    {"op": "quit"}

Replies::

    {"ok": true, ...}   or   {"ok": false, "error": "...", "traceback": "..."}

Audio is not sent through the pipe: each clip is saved as a ``.npy`` file
in a temporary folder and the reply lists the paths.

Anything the engine libraries print to stdout is redirected to stderr, so
it cannot corrupt the protocol.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    args = ap.parse_args(argv)

    # Keep a private copy of stdout for the protocol; send everything else to stderr.
    proto = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    import numpy as np

    from .registry import engine_class

    cls = engine_class(args.engine)
    engine = None
    out_dir = Path(tempfile.mkdtemp(prefix=f"lektor_{args.engine}_"))
    counter = 0

    def reply(obj):
        proto.write(json.dumps(obj) + "\n")
        proto.flush()

    reply({"ok": True, "ready": True, "engine": args.engine, "pid": os.getpid()})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            op = req.get("op")
            if op == "quit":
                reply({"ok": True, "bye": True})
                break
            if op == "load":
                engine = cls(**req.get("options", {}))
                engine.load()
                reply({"ok": True, "info": engine_info(engine)})
            elif op == "info":
                reply({"ok": True, "info": engine_info(engine) if engine else cls.describe()})
            elif op == "synth":
                if engine is None:
                    raise RuntimeError("The engine was not loaded.")
                audios = engine.synthesize_batch(req["items"])
                files = []
                for a in audios:
                    counter += 1
                    p = out_dir / f"{counter:08d}.npy"
                    np.save(p, np.asarray(a, dtype=np.float32))
                    files.append(str(p))
                reply({"ok": True, "files": files, "sample_rate": engine.sample_rate})
            elif op == "call":
                if engine is None:
                    raise RuntimeError("The engine was not loaded.")
                method = getattr(engine, "rpc_" + req["method"])
                result = method(**req.get("kwargs", {}))
                reply({"ok": True, "result": result})
            else:
                raise ValueError(f"unknown op {op!r}")
        except Exception as e:  # noqa: BLE001 - reported to the client
            reply({"ok": False, "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()})
    if engine is not None:
        try:
            engine.unload()
        except Exception:
            pass
    return 0


def engine_info(engine) -> dict:
    info = type(engine).describe()
    info["sample_rate"] = engine.sample_rate
    extra = getattr(engine, "runtime_info", None)
    if callable(extra):
        try:
            info.update(extra())
        except Exception as e:  # pragma: no cover
            info["runtime_info_error"] = str(e)
    return info


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
