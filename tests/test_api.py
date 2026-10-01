"""API 통합 테스트.

TEST_DATABASE_URL 이 가리키는 DB의 public 스키마를 초기화한 뒤
db/schema.sql + db/seed_sample.sql 을 적재하고 실행한다. (기존 데이터 삭제 주의)
"""
import os
from pathlib import Path

import psycopg
import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL 미설정")

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client():
    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.execute((ROOT / "db/schema.sql").read_text())
        conn.execute((ROOT / "db/seed_sample.sql").read_text())

    os.environ["DATABASE_URL"] = TEST_DB
    from fastapi.testclient import TestClient

    from app import db
    from app.main import app

    db.DATABASE_URL = TEST_DB
    db.close_pool()
    with TestClient(app) as c:
        yield c


def put_grade(client, dept, emp, code):
    return client.put(f"/api/departments/{dept}/evaluations/{emp}", json={"grade_code": code})


def test_sheet_starts_blank_with_current_salary(client):
    sheet = client.get("/api/departments/2/sheet").json()
    names = [m["name"] for m in sheet["members"]]
    assert names == ["이과장", "박대리", "최사원"]           # 부서장(김팀장) 제외
    assert all(m["grade_code"] is None for m in sheet["members"])
    assert sheet["members"][0]["cur_base_salary"] == 72_000_000


def test_division_head_evaluates_team_heads(client):
    names = {m["name"] for m in client.get("/api/departments/1/sheet").json()["members"]}
    assert names == {"김팀장", "한팀장"}


def test_raise_by_individual_grade_only(client):
    # 이과장 72,000,000 / 당해 인센티브 5,000,000, 기본인상률 2% (임시값)
    a = put_grade(client, 2, 3, "A").json()
    assert (a["base_up_rate"], a["perf_raise_rate"], a["raise_rate"]) == (0.02, 0.05, 0.07)
    assert a["next_base_salary"] == 77_040_000
    assert a["next_incentive"] == 10_800_000                 # 72,000,000 × 15%
    assert a["delta_total"] == (77_040_000 + 10_800_000) - (72_000_000 + 5_000_000)

    # 부서가 달라도 같은 등급이면 같은 성과인상률
    other = put_grade(client, 5, 11, "A").json()
    assert other["perf_raise_rate"] == a["perf_raise_rate"]


def test_d_freezes_and_e_is_negative(client):
    d = put_grade(client, 2, 4, "D").json()                  # 박대리 56,500,000
    assert d["perf_raise_rate"] == 0 and d["raise_rate"] == 0.02
    assert d["next_base_salary"] == 57_630_000

    e = put_grade(client, 2, 3, "E").json()                  # 이과장 72,000,000
    assert e["perf_raise_rate"] == -0.01 and e["raise_rate"] == 0.01
    assert e["next_base_salary"] == 72_720_000


def test_incentive_only_for_a_and_b(client):
    assert put_grade(client, 2, 3, "B").json()["next_incentive"] == 5_760_000   # 72,000,000 × 8%
    for code in ("C", "D", "E"):
        m = put_grade(client, 2, 3, code).json()
        assert m["next_incentive"] == 0
        assert m["delta_incentive"] == -5_000_000


@pytest.mark.parametrize(
    "code, perf, inc, message",
    [
        ("A", 0, 0.15, "양수"),
        ("D", 0.01, 0, r"0\(동결\)"),
        ("E", 0.01, 0, "음수"),
        ("C", 0.01, 0.05, "인센티브 지급 대상이 아닙니다"),
    ],
)
def test_rule_policy_is_enforced(client, code, perf, inc, message):
    with psycopg.connect(TEST_DB) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match=message):
            conn.execute(
                """UPDATE comp_rule SET perf_raise_rate = %s, incentive_rate = %s
                    WHERE grade_id = (SELECT id FROM evaluation_grade WHERE code = %s)""",
                (perf, inc, code),
            )


def test_band_cap_and_clear_grade(client):
    m = put_grade(client, 2, 5, "A").json()                  # 사원 상한 5,200만
    assert m["next_base_salary"] == 52_000_000 and m["band_capped"] is True
    cleared = put_grade(client, 2, 5, None).json()
    assert cleared["grade_code"] is None and cleared["next_base_salary"] is None


def test_rejects_non_target_and_invalid_grade(client):
    assert put_grade(client, 2, 9, "A").status_code == 403   # 타 본부장
    assert put_grade(client, 2, 3, "S").status_code == 422


def test_submit_requires_all_and_locks(client):
    assert client.post("/api/departments/2/submit").status_code == 422
    for emp, g in [(3, "A"), (4, "C"), (5, "D")]:
        put_grade(client, 2, emp, g)
    assert client.post("/api/departments/2/submit").json() == {"submitted": 3}
    assert put_grade(client, 2, 3, "B").status_code == 409
    sheet = client.get("/api/departments/2/sheet").json()
    assert sheet["can_submit"] is False
    assert all(not m["editable"] for m in sheet["members"])
