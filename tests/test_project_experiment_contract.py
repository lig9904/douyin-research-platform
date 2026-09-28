from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_preregistered_experiment_is_generic_and_release_gated() -> None:
    migration = (ROOT / "db/migrations/033_project_experiment_contract.sql").read_text(encoding="utf-8")
    timeline = (ROOT / "db/migrations/034_project_experiment_timeline.sql").read_text(encoding="utf-8")
    bootstrap = (ROOT / "db/schema.sql").read_text(encoding="utf-8")
    release = (ROOT / "scripts/test-server-release.sh").read_text(encoding="utf-8")
    page = (ROOT / "windmill/f/content_research/research_dashboard.raw_app/DecisionLoop.tsx").read_text(encoding="utf-8")
    mutate = (ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend/mutate_project_decision_loop.py").read_text(encoding="utf-8")

    for marker in (
        "observation_window_days", "comparison_basis", "confounder_plan",
        "review_verdict",
        "platform_content_id", "content_version", "distribution_mode",
        "preregistered project decision hypothesis is immutable",
        "preregistered project publication is immutable",
    ):
        assert marker in migration and marker in bootstrap
    assert "new project publication requires active preregistered adopted or observing action" in migration
    assert "verify_project_experiment_contract" in release.split("cmd_migrate() {", 1)[1]
    assert "verify_project_experiment_contract" in release.split("cmd_verify() {", 1)[1]
    assert "experiment_contract=%s" in release
    assert "033_project_experiment_contract.sql" in release
    for function in (
        "enforce_project_decision_card_experiment_contract",
        "enforce_project_publication_provenance",
        "reject_preregistered_publication_change",
    ):
        pattern = rf"create or replace function {function}\(\).*?as \$\$(.*?)\$\$;"
        source = timeline if function != "reject_preregistered_publication_change" else migration
        migration_body = re.search(pattern, source, re.S)
        bootstrap_body = list(re.finditer(pattern, bootstrap, re.S))[-1] if re.search(pattern, bootstrap, re.S) else None
        assert migration_body and bootstrap_body
        assert migration_body.group(1) == bootstrap_body.group(1)
        assert hashlib.md5(migration_body.group(1).encode()).hexdigest() in release
    assert "new.source_video_id" in timeline and "new.created_at" in timeline
    assert "new.published_at < card_created_at" in timeline
    assert "published_at" in timeline and "published_at" in bootstrap
    assert "034_project_experiment_timeline.sql" in release
    assert "the preregistered observation window is not complete" in mutate
    assert "review verdict is invalid" in mutate
    assert "证据不足" in page
    assert "未预设评估口径" in page
    assert "平台作品 ID" in page and "脚本/素材审核版本" in page
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts/test-server-release.sh")], capture_output=True).returncode == 0
