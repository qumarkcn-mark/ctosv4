# CZSC Engine Shadow Evaluation

> Branch: `codex/czsc-engine-adapter`
> Purpose: Track differences between the current `chan.py` primary engine and
> the CZSC shadow engine before any primary-engine switch is considered.

## Runtime Smoke

Date: 2026-05-12

Environment:

- Python: `/opt/homebrew/bin/python3.12`
- Temporary venv: `/tmp/ctos-czsc-venv`
- CZSC source: `https://github.com/waditu/czsc.git`
- Commit: `6f5bdf4d878c53b8221f0e64cdc281df60894a83`
- Built wheel: `czsc-1.0.0-cp310-abi3-macosx_11_0_arm64.whl`

Result:

```text
import czsc: ok
czsc.__version__: 1.0.0
CZSC synthetic bars: ok
fx_list: ok
bi_list: ok
zs_list: not exposed on CZSC object
derived ZS via czsc.ZS + bi_list: ok
```

Synthetic smoke output:

```text
fx 10
bi 4
derived_zs 1
latest 10.31 8.67 10.51 7.65 True
```

## Known API Differences

CZSC README describes `czsc_obj.zs_list`, but the tested 1.0.0 wheel does not
expose that attribute on `CZSC`. The adapter therefore derives valid `ZS`
objects from `bi_list` using CZSC's own `ZS` class.

This is adapter glue, not a separate hand-rolled structure engine.

## Evaluation Matrix

First real-symbol dual run:

- Levels: `day,30,5`
- Count: `300`
- Primary: `chan_py`
- Shadow: `czsc`
- Data source: BaoStock lake, `adjustflag=2`

Observed summary:

| Symbol | Slice | Expected regime | chan.py latest center | CZSC latest center | Notes |
|---|---|---|---|---|---|
| `sh.600519` | day | Large-cap trend / range mix | ZG 1523.65 / ZD 1477.41, bi 16, zs 2 | ZG 1473.90 / ZD 1390.32, bi 16, zs 3 | Same BI count, different latest center selection |
| `sh.600519` | 30 | Intraday tactical | ZG 1472.07 / ZD 1433.00, bi 19, zs 1 | ZG 1419.90 / ZD 1404.98, bi 19, zs 4 | Same BI count, CZSC derives more centers |
| `sh.600519` | 5 | Intraday tactical | ZG 1374.37 / ZD 1371.00, bi 13, zs 1 | ZG 1355.00 / ZD 1352.21, bi 18, zs 4 | BI count and center sequence diverge |
| `sz.002176` | day | Volatile swing | ZG 11.02 / ZD 9.01, bi 20, zs 3 | ZG 10.70 / ZD 9.46, bi 23, zs 2 | Similar area, different center width/count |
| `sz.002176` | 30 | Strong move / extension | ZG 11.06 / ZD 10.68, bi 18, zs 1 | ZG 14.77 / ZD 14.52, bi 16, zs 3 | Major disagreement; likely different latest operative center |
| `sz.002176` | 5 | Short-term structure | ZG 15.19 / ZD 15.06, bi 13, zs 2 | ZG 15.55 / ZD 15.35, bi 14, zs 3 | Both near current area, not same center |
| `sh.688008` | day | Strong trend with large swings | ZG 143.29 / ZD 128.50, bi 19, zs 3 | ZG 143.29 / ZD 128.50, bi 25, zs 4 | Latest center matches despite BI count difference |
| `sh.688008` | 30 | High volatility | ZG 168.70 / ZD 165.42, bi 25, zs 5 | ZG 177.36 / ZD 170.08, bi 30, zs 5 | Same center count, different latest center |
| `sh.688008` | 5 | High volatility | ZG 205.53 / ZD 200.28, bi 17, zs 2 | ZG 243.49 / ZD 236.72, bi 24, zs 5 | Major disagreement |
| `sz.000988` | day | Trend/range mix | ZG 84.95 / ZD 72.92, bi 25, zs 4 | ZG 75.98 / ZD 71.08, bi 25, zs 4 | Same BI/ZS count, different latest center |
| `sz.000988` | 30 | Tactical structure | ZG 118.20 / ZD 112.89, bi 20, zs 2 | ZG 118.20 / ZD 112.89, bi 22, zs 3 | Latest center matches |
| `sz.000988` | 5 | Short-term extension | ZG 129.39 / ZD 129.28, bi 15, zs 2 | ZG 147.99 / ZD 145.56, bi 20, zs 3 | Major disagreement |

Initial takeaways:

- CZSC 5-minute structure often produces more BIs and more derived centers than
  `chan.py`.
- Exact latest-center matches happened on `sh.688008/day` and `sz.000988/30`.
- Same BI count does not imply same latest center. `sh.600519/day` and
  `sh.600519/30` had equal BI counts but different center selection.
- Shadow output is useful as an AI reasoning contrast, but not ready as primary
  structure authority.

## Promotion Criteria

CZSC cannot become primary until:

- dual mode runs on real symbols without breaking the `chan_py` path
- latest-center differences are explainable across the evaluation matrix
- AI evidence pack improves reasoning quality in replay cases
- scanner, alert, and trading decisions remain gated away from CZSC shadow data
  until explicitly approved
