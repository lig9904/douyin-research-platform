from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/test-server-backup-verify.py"


def _archive(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "backups"
    root.mkdir(mode=0o700, parents=True)
    root.chmod(0o700)
    directory = root / "20260920T000000Z"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    contents = {
        "globals.sql": "CREATE ROLE example;\n",
        "globals.inventory": "role|706f737467726573|true|true|true|true|true|false|false|-1|\n",
        "windmill.dump": "windmill\n",
        "research.dump": "research\n",
    }
    hashes: dict[str, str] = {}
    for name, value in contents.items():
        path = directory / name
        path.write_text(value, encoding="utf-8")
        path.chmod(0o600)
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (directory / "SHA256SUMS").write_text(
        "".join(f"{hashes[name]}  {name}\n" for name in sorted(hashes)), encoding="utf-8"
    )
    (directory / "manifest.txt").write_text(
        "\n".join(
            (
                "format=test-server-backup-v1",
                "created_at_utc=20260920T000000Z",
                "compose_project=research-test",
                "research_database=douyin_research",
                "windmill_database=windmill",
                "postgres_role=postgres",
                f"globals_inventory_sha256={hashes['globals.inventory']}",
                "",
            )
        ),
        encoding="utf-8",
    )
    for name in ("SHA256SUMS", "manifest.txt"):
        (directory / name).chmod(0o600)
    return root, directory


def _verify(root: Path, directory: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), "--backup-root", str(root), "--backup-dir", str(directory)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_accepts_exact_v1_archive_and_returns_only_digests(tmp_path: Path) -> None:
    root, directory = _archive(tmp_path)
    result = _verify(root, directory)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("BACKUP_VALID format=test-server-backup-v1 manifest_sha256=")
    assert str(directory) not in result.stdout


def test_rejects_missing_extra_symlink_and_public_artifacts(tmp_path: Path) -> None:
    root, directory = _archive(tmp_path / "missing")
    (directory / "research.dump").unlink()
    assert "artifact set" in _verify(root, directory).stderr

    root, directory = _archive(tmp_path / "extra")
    extra = directory / "unexpected"
    extra.write_text("x", encoding="utf-8")
    extra.chmod(0o600)
    assert "artifact set" in _verify(root, directory).stderr

    root, directory = _archive(tmp_path / "link")
    target = directory / "globals.sql"
    target.unlink()
    target.symlink_to(directory / "windmill.dump")
    assert "non-symlink" in _verify(root, directory).stderr

    root, directory = _archive(tmp_path / "mode")
    (directory / "manifest.txt").chmod(0o644)
    assert "exact mode 0600" in _verify(root, directory).stderr


def test_rejects_hash_format_inventory_and_path_escape(tmp_path: Path) -> None:
    root, directory = _archive(tmp_path / "hash")
    (directory / "research.dump").write_text("changed", encoding="utf-8")
    assert "checksum verification failed" in _verify(root, directory).stderr

    root, directory = _archive(tmp_path / "format")
    manifest = directory / "manifest.txt"
    manifest.write_text(manifest.read_text().replace("test-server-backup-v1", "other"), encoding="utf-8")
    manifest.chmod(0o600)
    assert "not a complete" in _verify(root, directory).stderr

    root, directory = _archive(tmp_path / "inventory")
    inventory = directory / "globals.inventory"
    inventory.write_text("role|plaintext-name|true|true|true|true|true|false|false|-1|\n", encoding="utf-8")
    inventory.chmod(0o600)
    checksum = hashlib.sha256(inventory.read_bytes()).hexdigest()
    sums = directory / "SHA256SUMS"
    sums.write_text(sums.read_text().replace(hashlib.sha256(b"role|706f737467726573|true|true|true|true|true|false|false|-1|\n").hexdigest(), checksum), encoding="utf-8")
    sums.chmod(0o600)
    manifest = directory / "manifest.txt"
    manifest.write_text(manifest.read_text().replace(hashlib.sha256(b"role|706f737467726573|true|true|true|true|true|false|false|-1|\n").hexdigest(), checksum), encoding="utf-8")
    manifest.chmod(0o600)
    assert "inventory is malformed" in _verify(root, directory).stderr

    outside_root, outside = _archive(tmp_path / "outside")
    other_root = tmp_path / "other-root"
    other_root.mkdir(mode=0o700)
    other_root.chmod(0o700)
    assert "one direct" in _verify(other_root, outside).stderr
    assert outside_root != other_root


def test_rejects_unsafe_checksum_paths_without_echoing_them(tmp_path: Path) -> None:
    root, directory = _archive(tmp_path)
    sentinel = "../../secret-do-not-echo"
    sums = directory / "SHA256SUMS"
    sums.write_text("0" * 64 + f"  {sentinel}\n", encoding="utf-8")
    sums.chmod(0o600)
    result = _verify(root, directory)
    assert result.returncode == 1
    assert sentinel not in result.stdout + result.stderr


def test_rejects_impossible_archive_timestamp(tmp_path: Path) -> None:
    root, directory = _archive(tmp_path)
    manifest = directory / "manifest.txt"
    manifest.write_text(manifest.read_text().replace("20260920T000000Z", "20261340T250000Z"), encoding="utf-8")
    manifest.chmod(0o600)
    result = _verify(root, directory)
    assert result.returncode == 1
    assert "timestamp is invalid" in result.stderr
