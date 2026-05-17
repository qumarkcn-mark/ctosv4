from pathlib import Path


V5_FILES = [
    Path("server/api/ai_structure.py"),
    Path("server/engines/ai_native/universe_resolver.py"),
    Path("server/engines/ai_native/czsc_snapshot_service.py"),
    Path("server/engines/ai_native/structure_context_service.py"),
    Path("server/engines/ai_native/scenario_branch_service.py"),
    Path("server/engines/ai_native/structure_chat_service.py"),
    Path("server/engines/ai_native/structure_evidence_service.py"),
    Path("server/engines/ai_native/structure_reminder_service.py"),
    Path("server/engines/ai_native/scenario_outcome_service.py"),
    Path("server/engines/ai_native/outcome_settlement_service.py"),
    Path("server/workers/ai_structure_snapshot_worker.py"),
    Path("server/workers/ai_structure_context_worker.py"),
    Path("server/workers/ai_structure_outcome_worker.py"),
]

FORBIDDEN_SNIPPETS = [
    "server.api.radar",
    "server.api.chan",
    "server.services.chan_service",
    "server.services.chan_detail_service",
    "server.engines.structure.engine_router",
    "server.engines.structure.chan_adapter",
    "analyze_structure_with_engine",
    "structure_engine=\"dual\"",
    "structure_engine='dual'",
    "server.prompts.czsc_agent",
    "CZSC_SYSTEM_PROMPT",
]


def test_v5_pr1_files_do_not_reference_legacy_structure_paths():
    for path in V5_FILES:
        text = path.read_text()
        for snippet in FORBIDDEN_SNIPPETS:
            assert snippet not in text, f"{path} must not reference {snippet}"
