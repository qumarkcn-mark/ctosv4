# Changelog

## Unreleased

### Architecture

- Radar frontend active path now uses `/api/radar/{symbol}` instead of `/api/chan/matrix/v2/{symbol}`.
- `/api/chan/matrix/v2/{symbol}` is frozen as a compatibility endpoint. New product behavior should be added to Radar, scanner, rotation, or dedicated engine contracts.
- Scanner, RotationCompass, BehaviorReport, Push/Alerts, and Coach/Event Log now use dedicated contracts instead of consuming matrix fields.
- Future QMT execution is reserved behind Execution Intent, Risk Gate, Windows QMT Agent, QMT Adapter, and Execution Audit Log. Phase 1/2 remain trading-coach only and do not execute orders.
