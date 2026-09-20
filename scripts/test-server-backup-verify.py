#!/usr/bin/env python3
"""Verify one test-server-backup-v1 archive without printing its contents."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path


ARTIFACTS = ("globals.sql", "globals.inventory", "windmill.dump", "research.dump")
FILES = set(ARTIFACTS) | {"manifest.txt", "SHA256SUMS"}
MANIFEST_KEYS = {
    "format",
    "created_at_utc",
    "compose_project",
    "research_database",
    "windmill_database",
    "postgres_role",
    "globals_inventory_sha256",
}
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
STAMP_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
DATABASE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
HEX_NAME_RE = re.compile(r"^[0-9a-f]+$")


class BackupError(ValueError):
    pass


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode)


def _owned_by_current_user(path: Path) -> bool:
    return os.stat(path, follow_symlinks=False).st_uid == os.getuid()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BackupError("backup artifact could not be hashed") from exc
    return digest.hexdigest()


def _small_text(path: Path, label: str, *, maximum: int = 1_048_576) -> str:
    try:
        if os.stat(path, follow_symlinks=False).st_size > maximum:
            raise BackupError(f"{label} exceeds its size limit")
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise BackupError(f"{label} is unreadable") from exc


def _resolve_inputs(root_arg: Path, directory_arg: Path) -> tuple[Path, Path]:
    if not root_arg.is_absolute() or root_arg.is_symlink() or not root_arg.is_dir():
        raise BackupError("backup root must be an existing absolute non-symlink directory")
    root = root_arg.resolve(strict=True)
    if root == Path("/") or not _owned_by_current_user(root) or _mode(root) != 0o700:
        raise BackupError("backup root must be current-user-owned and exact mode 0700")
    if directory_arg.is_symlink() or not directory_arg.is_dir():
        raise BackupError("backup directory must be a real directory")
    directory = directory_arg.resolve(strict=True)
    if directory.parent != root or not _owned_by_current_user(directory) or _mode(directory) != 0o700:
        raise BackupError("backup directory must be one direct, current-user-owned mode-0700 child of backup root")
    return root, directory


def _verify_file_set(directory: Path) -> None:
    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        raise BackupError("backup directory is unreadable") from exc
    if {entry.name for entry in entries} != FILES:
        raise BackupError("backup directory has an incomplete or unexpected artifact set")
    for entry in entries:
        metadata = os.stat(entry, follow_symlinks=False)
        if entry.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise BackupError("backup artifacts must be regular non-symlink files")
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise BackupError("backup artifacts must be current-user-owned and exact mode 0600")


def _parse_checksums(directory: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in _small_text(directory / "SHA256SUMS", "checksum manifest", maximum=4096).splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (globals\.sql|globals\.inventory|windmill\.dump|research\.dump)", line)
        if not match or match.group(2) in checksums:
            raise BackupError("checksum manifest is malformed or contains duplicate artifacts")
        checksums[match.group(2)] = match.group(1)
    if set(checksums) != set(ARTIFACTS):
        raise BackupError("checksum manifest must cover the exact backup artifact set")
    for name, expected in checksums.items():
        if _sha256(directory / name) != expected:
            raise BackupError("backup checksum verification failed")
    return checksums


def _parse_manifest(directory: Path, checksums: dict[str, str]) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for line in _small_text(directory / "manifest.txt", "backup manifest", maximum=4096).splitlines():
        if line.count("=") != 1:
            raise BackupError("backup manifest is malformed")
        key, value = line.split("=", 1)
        if key not in MANIFEST_KEYS or not value or key in manifest:
            raise BackupError("backup manifest has an unknown, empty, or duplicate field")
        manifest[key] = value
    if set(manifest) != MANIFEST_KEYS or manifest["format"] != "test-server-backup-v1":
        raise BackupError("backup manifest is not a complete test-server-backup-v1 manifest")
    if not STAMP_RE.fullmatch(manifest["created_at_utc"]):
        raise BackupError("backup manifest timestamp is invalid")
    try:
        created_at = datetime.strptime(manifest["created_at_utc"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise BackupError("backup manifest timestamp is invalid") from exc
    if created_at.strftime("%Y%m%dT%H%M%SZ") != manifest["created_at_utc"]:
        raise BackupError("backup manifest timestamp is invalid")
    if not PROJECT_RE.fullmatch(manifest["compose_project"]):
        raise BackupError("backup manifest project is invalid")
    if any(not DATABASE_RE.fullmatch(manifest[key]) for key in ("research_database", "windmill_database")):
        raise BackupError("backup manifest database name is invalid")
    if not DATABASE_RE.fullmatch(manifest["postgres_role"]):
        raise BackupError("backup manifest PostgreSQL role is invalid")
    if not SHA_RE.fullmatch(manifest["globals_inventory_sha256"]):
        raise BackupError("backup manifest inventory digest is invalid")
    if manifest["globals_inventory_sha256"] != checksums["globals.inventory"]:
        raise BackupError("backup manifest inventory digest does not match the verified artifact")
    return manifest


def _verify_inventory(directory: Path, postgres_role: str) -> None:
    text = _small_text(directory / "globals.inventory", "globals inventory")
    previous = ""
    seen: set[str] = set()
    postgres_role_hex = postgres_role.encode("utf-8").hex()
    found_postgres_admin = False
    for line in text.splitlines():
        fields = line.split("|")
        valid_role = (
            len(fields) == 11
            and fields[0] == "role"
            and bool(HEX_NAME_RE.fullmatch(fields[1]))
            and all(value in {"true", "false"} for value in fields[2:9])
            and re.fullmatch(r"-?[0-9]+", fields[9]) is not None
            and (fields[10] == "" or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", fields[10]) is not None)
        )
        valid_member = (
            len(fields) == 7
            and fields[0] == "member"
            and bool(HEX_NAME_RE.fullmatch(fields[1]))
            and bool(HEX_NAME_RE.fullmatch(fields[2]))
            and bool(HEX_NAME_RE.fullmatch(fields[3]))
            and all(value in {"true", "false"} for value in fields[4:7])
        )
        if not (valid_role or valid_member) or line in seen or (previous and line < previous):
            raise BackupError("globals inventory is malformed, duplicated, or not byte-sorted")
        if valid_role and fields[1] == postgres_role_hex:
            if fields[2] != "true" or fields[6] != "true":
                raise BackupError("archived PostgreSQL administrator must be a superuser login role")
            found_postgres_admin = True
        seen.add(line)
        previous = line
    if not seen:
        raise BackupError("globals inventory is empty")
    if not found_postgres_admin:
        raise BackupError("globals inventory does not contain the archived PostgreSQL administrator")


def verify(root_arg: Path, directory_arg: Path) -> tuple[str, str, str]:
    _, directory = _resolve_inputs(root_arg, directory_arg)
    _verify_file_set(directory)
    checksums = _parse_checksums(directory)
    manifest = _parse_manifest(directory, checksums)
    _verify_inventory(directory, manifest["postgres_role"])
    compact_timestamp = datetime.strptime(manifest["created_at_utc"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return (
        _sha256(directory / "manifest.txt"),
        checksums["globals.inventory"],
        compact_timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest_sha, inventory_sha, created_at = verify(args.backup_root, args.backup_dir)
        verified_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        print(
            "BACKUP_VALID format=test-server-backup-v1 "
            f"manifest_sha256={manifest_sha} inventory_sha256={inventory_sha} "
            f"created_at_utc={created_at} verified_at_utc={verified_at}"
        )
    except BackupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("ERROR: filesystem operation failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
