# Adding a speech engine

An engine is one small Python class. Nothing else in Lektor needs to change.

## 1. Check the licence

Weights and code must be Apache 2.0, MIT, BSD or similar. No
non-commercial or research-only weights. Record the licence in
`docs/engines.md`.

## 2. Write the class

Create `lektor/engines/myengine.py`:

```python
from typing import Any, Optional

import numpy as np

from .base import Engine


class MyEngine(Engine):
    name = "myengine"                 # registry name, used in voices.yaml
    licence = "MIT (weights and code)"
    sample_rate = 24000               # native output rate
    max_chars = 300                   # longest unit of spoken text per call
    paragraph_units = False           # True if stable on whole paragraphs
    max_paragraph_chars = 500
    supports_reference_audio = False  # True if it can be conditioned on a clip
    env = "myengine"                  # isolated environment (None = main process)
    needs_gpu = True
    default_params = {"temperature": 0.7}

    def load(self) -> None:
        # Import heavy libraries HERE, never at the top of the module:
        # the main process imports this module only to read the attributes above.
        import torch
        from mylib import Model

        self.model = Model.from_pretrained("org/model").to("cuda")
        self.loaded = True

    def synthesize(self, text: str, voice: str, params: dict[str, Any], seed: int,
                   reference: Optional[str] = None) -> np.ndarray:
        import torch

        torch.manual_seed(seed)       # the same seed must give the same audio
        wav = self.model.generate(text, voice=voice, **params)
        return wav.squeeze().cpu().numpy().astype(np.float32)   # mono float32

    def unload(self) -> None:
        self.model = None
        self.loaded = False
```

Optional:

- `synthesize_batch(items)` if the engine can batch on the GPU;
- `rpc_<name>(**kwargs)` methods, callable from the main process with
  `WorkerEngine.call("<name>", ...)`;
- `runtime_info()` returning a dict shown in the smoke test.

## 3. Register it

In `lektor/engines/registry.py`:

```python
ENGINES["myengine"] = "lektor.engines.myengine:MyEngine"

register_env(EnvSpec(
    name="myengine",
    packages=("mylib==1.2.3",),     # pin an exact version you have checked
    python="3.12",
    apt=(),                          # system packages, if any
    check="import mylib",
))
```

## 4. Add presets

In `lektor/data/voices.yaml` (or `My Drive/Lektor/voices.yaml`):

```yaml
presets:
  myengine-narrator:
    engine: myengine
    voice: narrator_1
    params: {temperature: 0.7}
    vary: {temperature: 0.05}        # optional seeded per-paragraph variation
    description: My engine, narrator voice.
```

A preset may use `design: british-female` to condition on a designed
(synthetic) voice. Never point a preset at a recording of a real person.

## 5. Test it

- `pytest tests/test_engines.py` checks the class imports without its
  dependencies, has a permissive licence and an environment spec.
- Add the preset to the smoke test list in `lektor/smoke.py` and run
  `notebooks/Smoke_Test.ipynb` in Colab.

## A cloud engine (later)

The interface does not assume a local model. A cloud engine would set
`env = None` (it runs in the main process, which only needs an HTTP
client), read its API key from Colab Secrets in `load()`, and call the API
in `synthesize()`. Paid APIs are out of scope for the prototype.
