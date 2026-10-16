"""Chunk C32: catalog loading and client migration with count and sample checks (LL-132, LL-133)."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.golive import catalog, migrate
from app.golive.inproc import Api
from app.models import Role
from sqlalchemy import text

from tests.audit_helpers import events
from tests.conftest import make_settings
from tests.helpers import db_session, make_user
from tests.test_clients import COMPANY_GSTIN, FIRM_GSTIN, PAN

pytestmark = pytest.mark.requires_db

CATALOG = Path(__file__).resolve().parents[2] / "docs" / "catalog" / "india-common.json"
HEADER = ",".join(migrate.COLUMNS)


def api(url: str) -> Api:
    return Api(make_settings(database_url=url), "boss@firm.in")


def people(url: str) -> None:
    make_user(url, "boss@firm.in", Role.ADMINISTRATOR, name="Boss Person")
    make_user(url, "sita@firm.in", Role.STAFF, name="Sita Staff")


def load(url: str, path: Path = CATALOG, *, commit: bool) -> catalog.Result:
    parsed, result = catalog.read(path)
    assert parsed is not None, result.text()
    with api(url) as a:
        catalog.check(a, parsed, result)
        if commit:
            catalog.commit(a, parsed, result)
    return result


def run(url: str, csv_text: str, tmp_path: Path, *, commit: bool = False) -> migrate.Report:
    source = tmp_path / "clients.csv"
    source.write_text(csv_text, encoding="utf-8")
    with api(url) as a, a.factory() as db:
        report = migrate.check(db, a, migrate.read(source))
        if commit:
            migrate.commit(db, a, report)
            migrate.write_map(report, tmp_path / "map.csv")
            migrate.verify(db, report)
    return report


# --- LL-133: filing types and due-date rules load from one file ---------------------------------


def test_the_catalog_is_checked_then_loaded_once(migrated_db: str) -> None:
    people(migrated_db)
    checked = load(migrated_db, commit=False)
    assert checked.ok, checked.text()
    assert "filing TDS-24Q: quarterly, 4 periods" in checked.text()
    with db_session(migrated_db) as db:
        assert db.scalar(text("SELECT COUNT(*) FROM filings")) == 0  # checking saves nothing

    loaded = load(migrated_db, commit=True)
    assert loaded.ok, loaded.text()
    assert "package 'GST Monthly': created and published" in loaded.done
    with db_session(migrated_db) as db:
        assert db.scalar(text("SELECT COUNT(*) FROM filings")) == 11
        assert db.scalar(text("SELECT COUNT(*) FROM packages")) == 6
    assert events(migrated_db).count("filing.created") == 11  # audited as the Administrator

    again = load(migrated_db, commit=True)
    assert all(line.endswith("already there") for line in again.done)


def test_a_bad_catalog_is_refused_with_reasons(migrated_db: str, tmp_path: Path) -> None:
    people(migrated_db)
    bad = tmp_path / "bad.json"
    bad.write_text(
        '{"filings": [{"code": "X1", "name": "X", "calendar": {"frequency": "monthly",'
        ' "due": {"months_after": 1, "day": 11, "overrides": {"Q9": {"months_after": 1,'
        ' "day": 1}}}}}], "packages": [{"name": "P", "fee": "10", "billing_frequency":'
        ' "monthly", "filings": ["NOPE"]}]}'
    )
    parsed, result = catalog.read(bad)
    assert parsed is None
    assert "Q9" in result.text()

    bad.write_text(
        '{"filings": [], "packages": [{"name": "P", "fee": "10",'
        ' "billing_frequency": "monthly", "filings": ["NOPE"]}]}'
    )
    parsed, result = catalog.read(bad)
    assert parsed is not None
    with api(migrated_db) as a:
        catalog.check(a, parsed, result)
        with pytest.raises(SystemExit, match="Not loaded"):
            catalog.commit(a, parsed, result)
    assert "filing NOPE is not defined" in result.text()


# --- LL-132: migration with a count check and a 5% sample check ---------------------------------


def good_rows() -> str:
    return "\n".join(
        [
            HEADER,
            f"OLD-1,individual,Asha  Rao,{PAN},,sita@firm.in,GST Monthly,Asha Rao,"
            "Asha@Example.in,98765 43210,yes,no",
            f"OLD-2,business,U Firm,,{FIRM_GSTIN},SITA@firm.in,gst monthly,Accounts,"
            "accounts@ufirm.in,,,",
            f"OLD-3,business,B Ltd,,{COMPANY_GSTIN},boss@firm.in,TDS Quarterly,Finance,"
            "finance@bltd.in,,no,no",
        ]
    )


def test_rows_with_problems_are_listed_and_nothing_is_saved(
    migrated_db: str, tmp_path: Path
) -> None:
    people(migrated_db)
    load(migrated_db, commit=True)
    other = "ABCPF1234G"
    rows = "\n".join(
        [
            HEADER,
            f"OLD-1,individual,Asha Rao,{other[:-1]}1,,sita@firm.in,GST Monthly,Asha,a@b.in,,,",
            f"OLD-2,individual,Ravi,{other},,nobody@firm.in,GST Monthly,Ravi,r@b.in,,,",
            f"OLD-3,individual,Ravi,{other},,sita@firm.in,No Such Package,Ravi,r@b.in,,maybe,",
        ]
    )
    report = run(migrated_db, rows, tmp_path)
    assert not report.ok
    problems = report.text()
    assert "line 2 (OLD-1): pan:" in problems
    assert "line 3 (OLD-2): owner_email is not an active Staff member" in problems
    assert "line 4 (OLD-3): package 'No Such Package' is not a published" in problems
    assert "line 4 (OLD-3): whatsapp must be yes or no." in problems
    assert "line 4 (OLD-3): the same PAN as line 3." in problems
    # The report names rows, never their identifiers.
    assert other not in problems
    assert other[:-1] not in problems
    with api(migrated_db) as a, a.factory() as db, pytest.raises(SystemExit, match="Not saved"):
        migrate.commit(db, a, report)
    with db_session(migrated_db) as db:
        assert db.scalar(text("SELECT COUNT(*) FROM clients")) == 0


def test_a_clean_file_is_saved_counted_and_sampled_and_can_run_again(
    migrated_db: str, tmp_path: Path
) -> None:
    people(migrated_db)
    load(migrated_db, commit=True)
    report = run(migrated_db, good_rows(), tmp_path, commit=True)
    assert report.ok, report.text()
    assert [s for s, _ in report.saved] == ["OLD-1", "OLD-2", "OLD-3"]
    assert "PASS  count: 3 rows in the file, 3 found in Ledgerline" in report.text()
    assert "PASS  sample: 1 of 3 rows (5%)" in report.text()
    mapping = (tmp_path / "map.csv").read_text()
    assert mapping.splitlines()[0] == "source_id,client_id,result"
    assert PAN not in mapping
    assert FIRM_GSTIN not in mapping
    with db_session(migrated_db) as db:
        assert db.scalar(text("SELECT COUNT(*) FROM cycles")) > 0  # cycles made as usual
    assert events(migrated_db).count("client.created") == 3

    # Running again saves nothing twice.
    again = run(migrated_db, good_rows(), tmp_path, commit=True)
    assert (len(again.saved), len(again.already)) == (0, 3)
    assert again.ok, again.text()


def test_the_sample_check_finds_a_field_that_differs(migrated_db: str, tmp_path: Path) -> None:
    people(migrated_db)
    load(migrated_db, commit=True)
    report = run(migrated_db, good_rows(), tmp_path, commit=True)
    with db_session(migrated_db) as db:
        db.execute(text("UPDATE clients SET legal_name = 'Someone Else'"))
        db.commit()
    with api(migrated_db) as a, a.factory() as db:
        migrate.verify(db, report, percent=100)
    assert not report.ok
    assert "legal_name differ" in report.checks[-1][1]


def test_only_an_active_administrator_runs_the_tools(migrated_db: str) -> None:
    people(migrated_db)
    with pytest.raises(SystemExit, match="not an active Administrator"):
        Api(make_settings(database_url=migrated_db), "sita@firm.in").__enter__()
