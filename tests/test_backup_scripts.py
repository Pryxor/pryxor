"""
Tests pour les scripts de backup/restore.
On ne teste pas le shell directement (pas portable en CI),
mais on teste la logique VACUUM INTO sur laquelle ils reposent.
"""

from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _bash_works() -> bool:
    # True only if a *usable* bash is available.
    # `shutil.which('bash')` is not enough on Windows: it can find the WSL
    # stub or a broken shim that hangs. We probe it for real.
    exe = shutil.which("bash")
    if not exe:
        return False
    try:
        r = subprocess.run(
            [exe, "-c", "echo ok"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return r.returncode == 0 and "ok" in r.stdout
    except (subprocess.TimeoutExpired, OSError):
        return False


BASH = shutil.which("bash")
HAS_BASH = _bash_works()
requires_bash = pytest.mark.skipif(not HAS_BASH, reason="no usable bash available")
requires_posix = pytest.mark.skipif(
    os.name == "nt", reason="POSIX file-mode bits are not meaningful on Windows"
)


# ---------------------------------------------------------------------------
# Files present
# ---------------------------------------------------------------------------


def test_backup_scripts_exist():
    assert (SCRIPTS_DIR / "backup.sh").exists()
    assert (SCRIPTS_DIR / "restore.sh").exists()
    assert (SCRIPTS_DIR / "backup.ps1").exists()
    assert (SCRIPTS_DIR / "restore.ps1").exists()


@requires_bash
@requires_posix
def test_backup_sh_is_executable():
    mode = (SCRIPTS_DIR / "backup.sh").stat().st_mode
    # Au moins owner-executable
    assert mode & 0o100


# ---------------------------------------------------------------------------
# VACUUM INTO logic (independent of the shell)
# ---------------------------------------------------------------------------


def test_vacuum_into_creates_consistent_copy(tmp_path):
    """Simulates what backup.sh does - creates a consistent copy."""
    db = tmp_path / "test.sqlite3"
    backup = tmp_path / "backup.sqlite3"

    # Create a DB with a few rows
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        for i in range(100):
            conn.execute("INSERT INTO t(v) VALUES (?)", (f"row_{i}",))
        conn.commit()

    # VACUUM INTO
    with sqlite3.connect(db) as conn:
        conn.execute(f"VACUUM INTO '{backup}'")

    # Check that the copy exists and holds the same data
    assert backup.exists()

    with sqlite3.connect(backup) as conn:
        count = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    assert count == 100

    # Integrity
    with sqlite3.connect(backup) as conn:
        result = conn.execute("PRAGMA integrity_check;").fetchone()[0]
    assert result == "ok"


@requires_bash
def test_backup_sh_end_to_end(tmp_path):
    """Lance backup.sh sur une DB de test."""
    db = tmp_path / "pryxor_state.sqlite3"

    # Create a minimal Pryxor DB
    with sqlite3.connect(db) as conn:
        conn.execute("""
            CREATE TABLE holds (action_id TEXT PRIMARY KEY, status TEXT)
        """)
        conn.execute("""
            CREATE TABLE audit_events (id INTEGER PRIMARY KEY, event_type TEXT)
        """)
        conn.execute("""
            CREATE TABLE outbox_events (id INTEGER PRIMARY KEY, event_type TEXT)
        """)
        conn.commit()

    out_dir = tmp_path / "backups"
    script = SCRIPTS_DIR / "backup.sh"

    result = subprocess.run(
        ["bash", str(script), "--state", str(db), "--out", str(out_dir), "--keep", "5"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, f"backup.sh failed:\n{result.stderr}"
    assert "Backup complete" in result.stdout

    # Check that a .sqlite3.gz file exists
    backups = list(out_dir.glob("pryxor_backup_*.sqlite3.gz"))
    assert len(backups) == 1

    # Decompress and validate
    extracted = tmp_path / "extracted.sqlite3"
    with gzip.open(backups[0], "rb") as f_in, open(extracted, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

    with sqlite3.connect(extracted) as conn:
        result = conn.execute("PRAGMA integrity_check;").fetchone()[0]
    assert result == "ok"


@requires_bash
def test_backup_sh_rotation(tmp_path):
    """Verify that rotation keeps only N backups."""
    db = tmp_path / "pryxor_state.sqlite3"

    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE holds (action_id TEXT PRIMARY KEY, status TEXT)")
        conn.commit()

    out_dir = tmp_path / "backups"
    script = SCRIPTS_DIR / "backup.sh"

    # Create 5 backups
    for _ in range(5):
        result = subprocess.run(
            ["bash", str(script), "--state", str(db), "--out", str(out_dir), "--keep", "3"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0

    # Doit rester 3
    backups = list(out_dir.glob("pryxor_backup_*.sqlite3.gz"))
    assert len(backups) == 3, f"expected 3, got {len(backups)}"
