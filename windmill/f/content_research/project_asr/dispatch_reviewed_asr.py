# /// script
# requires-python = "==3.14.*"
# dependencies = ["douyin-research-platform", "wmill==1.815.0"]
# ///
"""Background-only project ASR dispatch placeholder.

It deliberately exposes no provider/media/storage arguments and does not run
until a reviewed service configuration binds a live provider factory and a
project-private signed-URL factory.
"""
def main(project_id: str, video_id: str, media_review_id: str) -> dict:
    return {"status": "blocked_unconfigured_provider_storage", "project_id": project_id,
            "video_id": video_id, "media_review_id": media_review_id,
            "external_calls": 0, "reason": "review wiring is ready; provider/storage binding remains unreviewed"}
