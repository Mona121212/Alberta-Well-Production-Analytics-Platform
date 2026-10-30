"""Power BI files in powerbi/ (DEC-025): no credentials, and every measure documented. Run: python -m pytest tests"""
import re
from pathlib import Path

import pytest

POWERBI = Path(__file__).resolve().parents[1] / "powerbi"
FILES = sorted(p for p in POWERBI.rglob("*") if p.is_file() and p.suffix in {".tmdl", ".json", ".pbir", ".pbism", ""})
TABLES = sorted((POWERBI / "sm_petrinex_production.SemanticModel" / "definition" / "tables").glob("*.tmdl"))

# Patterns that would mean a secret or a personal identifier was exported
FORBIDDEN = {
    "password": r"(?i)\b(password|pwd)\s*[=:]",
    "secret": r"(?i)client_?secret|\bsecret\s*[=:]",
    "storage key": r"(?i)accountkey\s*=|sharedaccesssignature|\bsig=",
    "token": r"(?i)\bbearer\s+[a-z0-9._-]{20,}",
    "tenant": r"(?i)tenant(id)?\W{1,3}[0-9a-f]{8}-",
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
}


def test_files_exported():
    assert TABLES, "semantic model tables missing"
    assert (POWERBI / "well_production_dashboard.Report" / "definition.pbir").is_file()


@pytest.mark.parametrize("name, pattern", FORBIDDEN.items())
def test_no_credentials(name, pattern):
    hits = [f"{p.relative_to(POWERBI)}:{i}" for p in FILES
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if re.search(pattern, line)]
    assert hits == [], f"{name} found in {hits}"


def test_connection_string_only_in_pbir_and_without_credentials():
    for p in FILES:
        text = p.read_text(encoding="utf-8")
        if "connectionString" in text:
            assert p.name == "definition.pbir", p
            conn = re.search(r'"connectionString":\s*"([^"]*)"', text).group(1)
            assert "integrated security=ClaimsToken" in conn
            assert not re.search(r"(?i)user id|password", conn)
