from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "windmill/f/content_research/project_asr"

def test_project_asr_review_scripts_keep_identity_resources_and_secrets_server_side() -> None:
    preview = (ROOT / "media_review_preview.py").read_text()
    approve = (ROOT / "approve_media_review.py").read_text()
    dispatch = (ROOT / "dispatch_reviewed_asr.py").read_text()
    assert "os.environ" not in approve and "actor:" not in approve  # service owns identity
    assert "get_resource(\"f/content_research/research_db\")" in preview
    assert "media_storage_config" in preview
    assert "presigned_read_url" in preview and "audio_preview_url" in preview
    assert "expected_manifest_fingerprint" in approve and "consent_statement" in approve
    assert "blocked_unconfigured_provider_storage" in dispatch
    assert "api_key" not in dispatch and "media_url" not in dispatch
