"""인사팀 '평가 준비' (평가대상자·연봉 엑셀 업로드 → 최종확정) 테스트.

샘플 파일: docs/samples (scripts/make_sample_excels.py 로 생성)
"""
import io

import pytest
from openpyxl import Workbook

from tests.test_api import ROOT, client, db_rows, login  # noqa: F401  (fixture 재사용)

SAMPLES = ROOT / "docs" / "samples"
EMP_FILE = SAMPLES / "평가대상자_샘플.xlsx"
SAL_FILE = SAMPLES / "연봉_샘플.xlsx"


@pytest.fixture()
def hr(client):  # noqa: F811
    assert login(client, "E006").status_code == 200
    return client


def upload(c, kind, path=None, content=None, name=None):
    data = content if content is not None else path.read_bytes()
    return c.post(f"/api/hr/setup/upload/{kind}", params={"file_name": name or path.name}, content=data)


def run(c, kind, path, action="apply", **opts):
    up = upload(c, kind, path)
    assert up.status_code == 200, up.text
    body = {"upload_id": up.json()["upload_id"], **opts}
    return c.post(f"/api/hr/setup/{action}/{kind}", json=body)


def test_setup_requires_hr(client):  # noqa: F811
    assert login(client, "E002").status_code == 200
    assert client.get("/api/hr/setup/status").status_code == 403
    assert client.get("/hr/setup", follow_redirects=False).status_code in (302, 303, 307)


def test_confirmed_cycle_blocks_upload_until_unconfirm(hr):
    st = hr.get("/api/hr/setup/status").json()
    assert st["confirmed"] is True                  # 더미 데이터는 확정 상태로 시작
    r = upload(hr, "employee", EMP_FILE)
    assert r.status_code == 409
    st = hr.post("/api/hr/setup/unconfirm").json()
    assert st["confirmed"] is False and st["cycle"]["status"] == "DRAFT"
    # 준비 중에는 부서장이 입력할 수 없다
    assert login(hr, "E002").status_code == 200
    r = hr.put("/api/departments/2/evaluations/3", json={"grade_code": "B"})
    assert r.status_code == 409


def test_employee_upload_preview_maps_columns_and_filters_eval_type(hr):
    hr.post("/api/hr/setup/unconfirm")
    up = upload(hr, "employee", EMP_FILE)
    assert up.status_code == 200, up.text
    v = up.json()
    assert v["header_row"] == 3
    names = {f["key"]: v["headers"][i] for f in v["fields"] if (i := v["mapping"].get(f["key"])) is not None}
    assert names == {"dept": "소속", "emp_no": "사번", "name": "성명", "level": "직급",
                     "title": "직책", "eval_type": "평가유형"}
    p = hr.post("/api/hr/setup/preview/employee", json={"upload_id": v["upload_id"]}).json()["plan"]
    assert p["targets"] == 32
    assert p["excluded_total"] == 3
    assert p["errors_count"] == 2
    assert {d["name"] for d in p["departments"]} == {"연구1팀", "연구2팀", "플랫폼개발실"}
    assert all(d["new"] and len(d["heads"]) == 1 for d in p["departments"])


def test_full_setup_flow(hr):
    hr.post("/api/hr/setup/unconfirm")
    r = run(hr, "employee", EMP_FILE)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["result"]["targets"] == 32 and res["result"]["heads_set"] == 3
    st = res["status"]
    assert st["counts"]["targets"] == 32           # 부서장 포함 (부서장은 평가자로 지정)
    assert st["steps"]["employees"] and not st["steps"]["salaries"]
    # '정상'이 아닌 사람·기존 더미 인원은 평가 대상에서 빠진다
    assert db_rows("SELECT count(*) FROM employee WHERE eval_target")[0][0] == 32
    assert db_rows("SELECT eval_target FROM employee WHERE emp_no = 'E003'")[0][0] is False

    # 최종확정은 연봉이 모두 들어가야 가능
    assert hr.post("/api/hr/setup/confirm").status_code == 422

    r = run(hr, "salary", SAL_FILE)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["result"]["not_found"] == 2
    assert res["status"]["steps"]["ready"] is True

    r = hr.post("/api/hr/setup/confirm")
    assert r.status_code == 200, r.text
    accounts = r.json()["accounts"]
    assert len(accounts) == 3 and all(a["temp_password"] for a in accounts)
    assert r.json()["status"]["confirmed"] is True

    # 새 부서장은 임시 비밀번호로 로그인 → 비밀번호 변경 강제, 자기 부서 인원만 보인다
    a = accounts[0]
    r = login(hr, a["emp_no"], a["temp_password"])
    assert r.status_code == 200 and r.json()["redirect"] == "/change-password"


def test_salary_units_and_mapping(hr):
    hr.post("/api/hr/setup/unconfirm")
    assert run(hr, "employee", EMP_FILE).status_code == 200
    wb = Workbook()
    ws = wb.active
    ws.append(["사번", "이름", "기본연봉(만원)"])
    emp_no, name = db_rows("SELECT emp_no, name FROM employee WHERE eval_target ORDER BY emp_no LIMIT 1")[0]
    ws.append([emp_no, name, 5200])
    buf = io.BytesIO()
    wb.save(buf)
    up = upload(hr, "salary", content=buf.getvalue(), name="연봉.xlsx").json()
    p = hr.post("/api/hr/setup/preview/salary", json={"upload_id": up["upload_id"]}).json()["plan"]
    assert p["unit"] == "만원" and p["samples"][0]["base_salary"] == 52_000_000
    p = hr.post("/api/hr/setup/preview/salary", json={"upload_id": up["upload_id"], "unit": "천원"}).json()["plan"]
    assert p["samples"][0]["base_salary"] == 5_200_000


def test_missing_required_column_is_reported(hr):
    hr.post("/api/hr/setup/unconfirm")
    up = upload(hr, "employee", EMP_FILE).json()
    mapping = {**up["mapping"], "eval_type": None}
    r = hr.post("/api/hr/setup/preview/employee", json={"upload_id": up["upload_id"], "mapping": mapping}).json()
    assert r["plan"] is None and r["missing_fields"]


def test_set_department_head(hr):
    hr.post("/api/hr/setup/unconfirm")
    st = run(hr, "employee", EMP_FILE).json()["status"]
    d = st["departments"][0]
    other = next(c for c in d["candidates"] if c["id"] != d["head_employee_id"])
    st = hr.put(f"/api/hr/setup/departments/{d['id']}/head", json={"employee_id": other["id"]}).json()
    assert next(x for x in st["departments"] if x["id"] == d["id"])["head_employee_id"] == other["id"]
