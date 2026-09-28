# /// script
# requires-python = "==3.14.*"
# dependencies = [
#   "douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@5b93ffa7681038ecbd355c5ffbbbb3b5cdb53180",
#   "psycopg[binary]==3.3.6", "wmill==1.815.0",
# ]
# ///
"""Dispatch first submissions from persisted L3 approvals only."""
from douyin_research.reviewed_dispatch import run_scheduled


def main() -> dict:
    return run_scheduled("l3")
