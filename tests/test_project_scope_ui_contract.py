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
    assert "new Set<ResearchView>(['videos', 'briefs'])" in shell


def test_project_video_calls_carry_scope_and_hide_unscoped_review_paths() -> None:
    library = _source("VideoLibrary.tsx")
    metric = _source("src/components/MetricTimeline.tsx")
    raw = _source("src/components/RawRecordPanel.tsx")
    assert "...(projectId ? { project_id: projectId } : {})" in library
    assert "project_id: projectId" in metric
    assert "project_id: projectId" in raw
    for component in ("VideoMediaPreview", "L3ReviewPanel", "ASRMediaReviewPanel", "ASRTranscriptPanel"):
        assert f"{{!isProject && <{component}" in library
    assert "if (!isProject) void loadUserState()" in library
    assert "{!isProject && <div className=\"bulk-actions\">" in library
    assert "project_inclusion" in raw and "merged_metrics" in raw


def test_project_briefs_only_offer_metadata_and_carry_scope_on_mutation() -> None:
    briefs = _source("ResearchBriefs.tsx")
    assert "backend.get_research_briefs(projectId ? { project_id: projectId } : {})" in briefs
    assert "...(projectId ? { project_id: projectId } : {})" in briefs
    assert "if (projectId && values.depth !== 'metadata')" in briefs
    assert ".filter(([value]) => !projectId || value === 'metadata')" in briefs
    assert "const version = ++requestVersion.current" in briefs
    assert "if (version === requestVersion.current)" in briefs
