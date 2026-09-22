# /// script
# requires-python = "==3.14.*"
# dependencies = [
#   "douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@ab1255f97e2a05e226a24e3a47ac8c52398b9296",
#   "psycopg[binary]==3.3.6", "wmill==1.815.0",
# ]
# ///
"""Dispatch first submissions from persisted ASR approvals only."""
from douyin_research.reviewed_dispatch import run_scheduled


def main() -> dict:
    return run_scheduled("asr")
