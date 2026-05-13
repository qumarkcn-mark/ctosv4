# Structure Engine Router Plan

> Branch: `codex/czsc-engine-adapter`
> Status: Phase 1 implementation started

## Goal

Introduce a pluggable structure engine boundary so CT-OS can run `chan.py`,
CZSC, or both in dual mode without changing existing production behavior.

The first phase does not replace `chan.py`. It adds CZSC as a shadow engine for
AI reasoning and engine comparison.

## Modes

| Mode | Behavior | Production status |
|---|---|---|
| `chan_py` | Existing `chan.py` adapter output only | Default |
| `czsc` | CZSC output only | Experimental/debug |
| `dual` | `chan.py` primary output plus CZSC shadow output and comparison | Experimental/debug |

## Files

| File | Role |
|---|---|
| `server/engines/structure/engine_contract.py` | Shared envelope and mode normalization |
| `server/engines/structure/engine_router.py` | Dispatches `chan_py`, `czsc`, and `dual` modes |
| `server/engines/structure/czsc_adapter.py` | Loads lake K-lines and runs CZSC |
| `server/engines/structure/czsc_serializer.py` | Converts CZSC objects to stable JSON |
| `server/engines/structure/czsc_evidence.py` | Compresses CZSC output for AI evidence packs |
| `server/engines/structure/engine_comparison.py` | Compares primary and shadow engine outputs |

## Contract

Every engine output should expose:

```json
{
  "engine": "chan_py | czsc",
  "adapter_version": "...",
  "symbol": "sh.600519",
  "levels": {
    "day": {
      "klines": [],
      "fxs": [],
      "bis": [],
      "segs": [],
      "bi_zhongshus": [],
      "seg_zhongshus": [],
      "zhongshus": [],
      "bsps": [],
      "metadata": {}
    }
  }
}
```

Unsupported fields must stay empty. The adapter must not invent `segs`, `bsps`,
or `seg_zhongshus` for CZSC until those are actually sourced from CZSC.

## AI Integration

`server/engines/ai_native/evidence_pack.py` now accepts optional:

- `shadow_structure`
- `czsc_structure`
- `structure_engine_comparison`

The resulting evidence pack exposes:

- `czsc_shadow`
- `structure_engine_comparison`

This is an auxiliary structure view. It must not drive trading decisions in
Phase 1.

## Debug API

Use the internal structure endpoint to run side-by-side comparisons:

```text
GET /api/structure/engine/{symbol}?levels=day,30,5&structure_engine=dual&count=300
```

Supported `structure_engine` values:

- `chan_py`
- `czsc`
- `dual`

The response keeps `chan_py` as the primary payload in `dual` mode and attaches:

- `shadow_structure`
- `structure_engine_comparison`

## Dependency Notes

CZSC 1.0 uses Rust/PyO3 and requires Python `>=3.10`. The current local venv in
this workspace is Python 3.9.6, so local runtime validation requires rebuilding
the venv with Python 3.11 or 3.12. The server Docker image already uses
`python:3.12-slim`.

PyPI currently does not publish `czsc==1.0.0`; the public PyPI line stops at
`0.10.12`. The branch therefore pins the researched Git commit and builds a
wheel from source. Docker needs Rust/Cargo/git build tools until an internal or
upstream wheel is available.

The dependency is pinned to the researched commit:

```text
czsc @ git+https://github.com/waditu/czsc.git@6f5bdf4d878c53b8221f0e64cdc281df60894a83
```

Runtime smoke on Python 3.12 succeeded:

- `czsc.__version__ == 1.0.0`
- `CZSC` exposes `fx_list` and `bi_list`
- `CZSC` does not expose `zs_list` in the tested wheel
- `czsc_adapter` derives valid `ZS` objects from `bi_list` with CZSC's own
  `ZS` class

## Guardrails

- Default mode remains `chan_py`.
- `dual` mode returns CZSC as shadow output.
- CZSC unavailable must not break the `chan_py` path.
- No scanner, alert, or trading decision may depend directly on CZSC shadow data
  in Phase 1.
- Engine comparison is observability, not a decision gate.

## Next Tasks

1. Rebuild local backend venv to Python 3.11/3.12.
2. Install pinned CZSC dependency and run an import smoke test.
3. Run CZSC adapter against a real lake symbol.
4. Add debug/internal API surface for `structure_engine=dual`.
5. Build a five-symbol shadow evaluation document.
