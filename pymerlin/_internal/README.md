# pymerlin internal

Implementation details behind pymerlin's public API. Two execution paths live here, both
driving the same `@MissionModel` class:

- `_framework.py` — the pure-Python `simulate()` engine used for local prototyping (no Java,
  no subprocess).
- `_server.py` — the entry points the Java shim calls when a packaged model runs in-process on
  GraalPy inside an Aerie worker (host calls, no subprocess, no protocol; see `roadmap.md` §6/§7).

Ideally none of these details leak out to user-facing code.
