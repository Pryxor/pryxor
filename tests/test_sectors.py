"""
DX tests: prove that adding a sector is trivial.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from pryxor_engine import PolicyEngine
from sectors import SECTORS, DeclarativeSector, SimpleSector, load_sector
from sectors._framework import Decision

# ----------------------------------------------------------------------
# Auto-discovery
# ----------------------------------------------------------------------


def test_finance_is_auto_discovered():
    assert "finance" in SECTORS


def test_unknown_sector_without_rules_raises(tmp_path):
    with pytest.raises(ValueError, match="declared as type 'code' but no module defines it"):
        load_sector("astrophysics", {"sectors": {}}, tmp_path / "s.sqlite3")


# ----------------------------------------------------------------------
# Declarative sector (YAML only)
# ----------------------------------------------------------------------


@pytest.fixture
def declarative_engine(tmp_path):
    policy = {
        "sector": "file_access",
        "hold_ttl_minutes": 60,
        "allowed_actions": {
            "agent_file_01": ["read_file", "write_file"],
        },
        "sectors": {
            "file_access": {
                "type": "declarative",
                "rules": [
                    {
                        "name": "Block system files",
                        "when": {"path_matches": "^/etc/|^/root/"},
                        "then": {"block": "SYSTEM_FILE_ACCESS"},
                    },
                    {
                        "name": "Block secrets",
                        "when": {"path_matches": r"\.(pem|key|env)$"},
                        "then": {"block": "SECRET_FILE_ACCESS"},
                    },
                    {
                        "name": "Prod writes need approval",
                        "when": {
                            "tool": "write_file",
                            "path_matches": "^/prod/",
                        },
                        "then": {"hold": "PROD_WRITE_REQUIRES_APPROVAL"},
                    },
                    {
                        "name": "Rate limit reads",
                        "when": {
                            "tool": "read_file",
                            "count_last_1h_gt": 3,
                        },
                        "then": {"hold": "READ_RATE_LIMIT"},
                    },
                ],
            }
        },
    }
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    state = tmp_path / f"state_{uuid4().hex}.sqlite3"
    return PolicyEngine(policy_path=policy_path, state_path=state)


def test_declarative_blocks_system_files(declarative_engine):
    r = declarative_engine.evaluate(
        "agent_file_01",
        "read_file",
        {"path": "/etc/passwd"},
    )
    assert r["status"] == "BLOCKED"
    assert r["reason"] == "SYSTEM_FILE_ACCESS"


def test_declarative_blocks_secret_files(declarative_engine):
    r = declarative_engine.evaluate(
        "agent_file_01",
        "read_file",
        {"path": "/home/user/.env"},
    )
    assert r["status"] == "BLOCKED"
    assert r["reason"] == "SECRET_FILE_ACCESS"


def test_declarative_holds_prod_writes(declarative_engine):
    r = declarative_engine.evaluate(
        "agent_file_01",
        "write_file",
        {"path": "/prod/config.yml"},
    )
    assert r["status"] == "HOLD"
    assert r["reason"] == "PROD_WRITE_REQUIRES_APPROVAL"


def test_declarative_rate_limit_reads(declarative_engine):
    # 4 lectures OK
    for i in range(4):
        r = declarative_engine.evaluate(
            "agent_file_01",
            "read_file",
            {"path": f"/data/file_{i}.txt"},
        )
        assert r["status"] == "APPROVED", f"Read {i} should be approved"

    # 5e → HOLD (count_last_1h_gt: 3 → 4 > 3)
    r = declarative_engine.evaluate(
        "agent_file_01",
        "read_file",
        {"path": "/data/file_5.txt"},
    )
    assert r["status"] == "HOLD"
    assert r["reason"] == "READ_RATE_LIMIT"


def test_declarative_approves_normal(declarative_engine):
    r = declarative_engine.evaluate(
        "agent_file_01",
        "read_file",
        {"path": "/data/normal.txt"},
    )
    assert r["status"] == "APPROVED"


# ----------------------------------------------------------------------
# Secteur custom en Python (SimpleSector)
# ----------------------------------------------------------------------


class DummyEmailSector(SimpleSector):
    # Distinct name from the real `email` sector so we do not pollute the
    # global registry shared between tests.
    name = "email_dummy"
    version = "0.1.0"

    def decide(self, agent_id, tool_name, parameters):
        recipients = parameters.get("recipients", [])
        # Block sends to more than 50 recipients
        if len(recipients) > 50:
            return Decision.hold(
                "MASS_EMAIL",
                f"{len(recipients)} recipients exceeds mass-mail threshold.",
            )
        # Rate limit : max 10 emails/heure
        if self.count_window(1, agent_id=agent_id) >= 10:
            return Decision.hold("EMAIL_RATE_LIMIT", "Too many emails in 1h.")
        return Decision.approve("OK.")


def test_custom_sector_with_simple_sector(tmp_path):
    """
    Un secteur custom en ~15 lignes fonctionne sans toucher au moteur.
    """
    # Enregistre le secteur custom
    SECTORS["email_dummy"] = DummyEmailSector
    policy = {
        "sector": "email_dummy",
        "hold_ttl_minutes": 60,
        "allowed_actions": {"agent_email_01": ["send_email"]},
        "sectors": {"email_dummy": {}},
    }
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    state = tmp_path / f"state_{uuid4().hex}.sqlite3"

    engine = PolicyEngine(policy_path=policy_path, state_path=state)

    # Cas normal
    r = engine.evaluate(
        "agent_email_01",
        "send_email",
        {"recipients": ["a@b.com", "c@d.com"]},
    )
    assert r["status"] == "APPROVED"

    # Mass email
    r = engine.evaluate(
        "agent_email_01",
        "send_email",
        {"recipients": [f"user{i}@x.com" for i in range(100)]},
    )
    assert r["status"] == "HOLD"
    assert r["reason"] == "MASS_EMAIL"


# ----------------------------------------------------------------------
# Preuve : pas de migration requise pour un nouveau secteur
# ----------------------------------------------------------------------


def test_new_sector_requires_no_schema_migration(tmp_path):
    """
    Adding an 'email' sector creates NO dedicated table.
    Everything goes into `sector_events`.
    """
    SECTORS["email_dummy"] = DummyEmailSector
    policy = {
        "sector": "email_dummy",
        "allowed_actions": {"agent_email_01": ["send_email"]},
        "sectors": {"email_dummy": {}},
    }
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(policy), encoding="utf-8")
    state = tmp_path / f"state_{uuid4().hex}.sqlite3"

    engine = PolicyEngine(policy_path=p, state_path=state)
    engine.evaluate("agent_email_01", "send_email", {"recipients": ["a@b.com"]})

    import sqlite3

    with sqlite3.connect(state) as conn:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

    # No dedicated `email_*` table
    assert not any(t.startswith("email_") for t in tables)
    # Everything is in the generic table
    assert "sector_events" in tables


# ----------------------------------------------------------------------
# Migration from the old schema
# ----------------------------------------------------------------------


def test_migration_from_v1_finance(tmp_path):
    """
    If the old `finance_transactions` table exists, it is migrated
    automatically into `sector_events`.
    """
    import sqlite3

    state = tmp_path / f"state_{uuid4().hex}.sqlite3"

    # Create the old schema by hand
    with sqlite3.connect(state) as conn:
        conn.execute("""
            CREATE TABLE finance_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                amount REAL NOT NULL,
                recipient TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                source TEXT NOT NULL,
                dedup_key TEXT NOT NULL UNIQUE
            )
        """)
        conn.execute(
            "INSERT INTO finance_transactions"
            "(agent_id, tool_name, amount, recipient, timestamp, source, dedup_key)"
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "agent_finance_01",
                "send_payment",
                100.0,
                "Fournisseur_A",
                "2026-01-01T00:00:00+00:00",
                "DIRECT_APPROVAL",
                "legacy:1",
            ),
        )
        conn.commit()

    # Instantiating a FinanceSector triggers the migration
    from sectors.finance import FinanceSector

    FinanceSector(
        policy={"sectors": {"finance": {}}},
        state_path=state,
    )

    # Check that the row is in sector_events
    with sqlite3.connect(state) as conn:
        row = conn.execute("SELECT * FROM sector_events WHERE sector_name='finance'").fetchone()
    assert row is not None
    assert row[5] == 100.0  # amount
