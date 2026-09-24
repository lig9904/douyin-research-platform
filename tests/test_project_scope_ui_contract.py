"""Static guardrails for project routing until browser-level Windmill tests exist.

These checks cannot prove runtime authorization; the server-side ACL tests do
that.  They prevent an accidental UI revert to projectless calls or mounting
unscoped review panels in project mode.
"""

from pathlib import Path


APP = Path(__file__).resolve().parents[1] / "windmill/f/content_research/research_dashboard.raw_app"


def _source(path: str) -> str:
    return (APP / path).read_text(encoding="utf-8")


def test_project_roster_is_required_before_loading_global_home() -> None:
    app = _source("App.tsx")
    shell = _source("AppShell.tsx")
    assert "backend.get_my_projects({})" in app
    assert "if (scope?.mode !== 'legacy-admin') return" in app
    assert "if (noScope || projectViewBlocked)" in app
    assert "key={scope.mode === 'project' ? scope.projectId : 'legacy-admin'}" in app
    assert "legacyAdmin ? [{ value: 'legacy-admin'" in shell
    assert "new Set<ResearchView>(['videos', 'briefs', 'accounts', 'collaboration', 'decisions', 'cost'])" in shell
    assert "<ProjectCostOverview key={scope.projectId} projectId={scope.projectId} />" in app
    cost = _source("src/components/ProjectCostOverview.tsx")
    assert "backend.get_project_daily_cost({ project_id: projectId, days: nextDays })" in cost
    assert "未知费用不计为零" in cost


def test_project_video_calls_carry_scope_and_hide_unscoped_review_paths() -> None:
    library = _source("VideoLibrary.tsx")
    metric = _source("src/components/MetricTimeline.tsx")
    raw = _source("src/components/RawRecordPanel.tsx")
    assert "...(projectId ? { project_id: projectId } : {})" in library
    assert "project_id: projectId" in metric
    assert "project_id: projectId" in raw
    for component in ("VideoMediaPreview", "L3ReviewPanel", "ASRMediaReviewPanel"):
        assert f"{{!isProject && <{component}" in library
    assert "<ASRTranscriptPanel transcript={detail.asr_transcript} />" in library
    assert "projectId && canReviewProject && <ProjectASRMediaReviewPanel" in library
    assert "projectId && canReviewProject && detail.asr_transcript?.transcript_id" in library
    assert "<ProjectL3ReviewPanel" in library
    assert "if (!isProject) void loadUserState()" in library
    assert "{!isProject && <div className=\"bulk-actions\">" in library
    assert "project_inclusion" in raw and "merged_metrics" in raw


def test_project_briefs_only_offer_metadata_and_carry_scope_on_mutation() -> None:
    briefs = _source("ResearchBriefs.tsx")
    assert "backend.get_research_briefs(projectId ? { project_id: projectId } : {})" in briefs
    assert "...(projectId ? { project_id: projectId } : {})" in briefs
    assert "if (projectId && values.depth !== 'metadata')" in briefs
    assert "subject_id: values?.subject_id" in briefs
    assert "请先创建或选择研究主体" in briefs
    assert "SubjectRelevancePanel" in briefs
    assert ".filter(([value]) => !projectId || value === 'metadata')" in briefs
    assert "const version = ++requestVersion.current" in briefs
    assert "if (version === requestVersion.current)" in briefs


def test_project_account_matrix_uses_project_route_and_public_fields_only() -> None:
    app = _source("App.tsx")
    matrix = _source("ProjectAccountMatrix.tsx")
    assert "<ProjectAccountMatrix key={scope.projectId} scope={scope}" in app
    assert "backend.get_project_accounts({ project_id: scope.projectId, limit: 100 })" in matrix
    assert "project_id: scope.projectId" in matrix
    assert "after_relation_id: data.next_cursor" in matrix
    assert "included_public_video_count" in matrix
    assert "last_included_at" in matrix
    assert "follower_captured_at" in matrix
    assert "requestEpoch.current !== epoch" in matrix
    assert "backend.get_accounts" not in matrix
    assert "credential_ref" not in matrix
    assert "evidence_ref" not in matrix
