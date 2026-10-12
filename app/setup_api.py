"""인사팀 '평가 준비': 평가대상자·연봉 엑셀 업로드 → 부서장 지정 → 평가화면 최종확정"""
import json
import secrets
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import importer
from .auth import User, hash_password, hr_user
from .db import get_pool

router = APIRouter(prefix="/api/hr/setup")


def _cycle(cur):
    cur.execute("SELECT * FROM evaluation_cycle ORDER BY eval_year DESC LIMIT 1")
    cycle = cur.fetchone()
    if cycle is None:
        raise HTTPException(404, "평가주기가 없습니다.")
    return cycle


def _confirmed(cycle) -> bool:
    return cycle["setup_confirmed_at"] is not None and cycle["status"] != "DRAFT"


def _require_editable(cycle) -> None:
    if _confirmed(cycle):
        raise HTTPException(409, "평가화면이 최종확정된 상태입니다. '확정 해제' 후 진행해 주세요.")
    if cycle["status"] not in ("DRAFT", "OPEN"):
        raise HTTPException(409, "평가가 이미 조정·확정 단계로 넘어가 변경할 수 없습니다.")


def _status(cur, cycle) -> dict:
    year = cycle["eval_year"]
    cur.execute(
        """SELECT d.id, d.name, d.head_employee_id,
                  h.name AS head_name, h.emp_no AS head_emp_no,
                  count(e.*) AS members,
                  count(e.*) FILTER (WHERE c.employee_id IS NULL) AS salary_missing
             FROM department d
             JOIN employee e ON e.department_id = d.id AND e.eval_target AND e.status = 'ACTIVE'
             LEFT JOIN employee h ON h.id = d.head_employee_id
             LEFT JOIN employee_compensation c ON c.employee_id = e.id AND c.comp_year = %s
            GROUP BY d.id, h.name, h.emp_no
            ORDER BY d.name""",
        (year,),
    )
    depts = cur.fetchall()
    cur.execute(
        """SELECT e.id, e.emp_no, e.name, e.department_id, e.job_title, jl.code AS level
             FROM employee e LEFT JOIN job_level jl ON jl.id = e.job_level_id
            WHERE e.eval_target AND e.status = 'ACTIVE'
            ORDER BY (e.job_title IS NULL), jl.sort_order DESC, e.emp_no"""
    )
    by_dept: dict[int, list] = {}
    for e in cur.fetchall():
        by_dept.setdefault(e["department_id"], []).append(
            {"id": e["id"], "emp_no": e["emp_no"], "name": e["name"], "level": e["level"], "title": e["job_title"]})
    for d in depts:
        d["candidates"] = by_dept.get(d["id"], [])

    cur.execute("SELECT count(*) AS n FROM employee WHERE NOT eval_target")
    excluded = cur.fetchone()["n"]
    cur.execute(
        """SELECT count(*) AS n FROM evaluation ev JOIN employee e ON e.id = ev.employee_id
            WHERE ev.cycle_id = %s AND ev.grade_id IS NOT NULL AND e.eval_target""", (cycle["id"],))
    graded = cur.fetchone()["n"]
    cur.execute(
        """SELECT kind, file_name, summary, applied_by, applied_at FROM import_log
            WHERE cycle_id = %s ORDER BY applied_at DESC LIMIT 10""", (cycle["id"],))
    logs = cur.fetchall()

    targets = sum(d["members"] for d in depts)
    salary_missing = sum(d["salary_missing"] for d in depts)
    heads_missing = sum(1 for d in depts if d["head_employee_id"] is None)
    employees_done = targets > 0
    salaries_done = employees_done and salary_missing == 0
    return {
        "cycle": {k: cycle[k] for k in ("id", "eval_year", "name", "status", "setup_confirmed_at", "setup_confirmed_by")},
        "confirmed": _confirmed(cycle),
        "steps": {"employees": employees_done, "salaries": salaries_done,
                  "heads": employees_done and heads_missing == 0,
                  "ready": salaries_done and heads_missing == 0},
        "counts": {"targets": targets, "departments": len(depts), "excluded": excluded,
                   "salary_missing": salary_missing, "heads_missing": heads_missing, "graded": graded},
        "departments": depts,
        "logs": logs,
    }


@router.get("/status")
def status(user: User = Depends(hr_user)):
    with get_pool().connection() as conn, conn.cursor() as cur:
        return _status(cur, _cycle(cur))


# ---------------------------------------------------------------------
# 엑셀 업로드 (원본 보관) → 미리보기 → 적용
# ---------------------------------------------------------------------
def _kind(kind: str) -> str:
    k = kind.upper()
    if k not in importer.FIELDS:
        raise HTTPException(404)
    return k


@router.post("/upload/{kind}")
async def upload(kind: str, request: Request, file_name: str, user: User = Depends(hr_user)):
    """파일 원본을 그대로 받는다 (요청 본문 = 파일 바이트)."""
    kind = _kind(kind)
    content = await request.body()
    if not content:
        raise HTTPException(422, "빈 파일입니다.")
    if len(content) > importer.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "파일이 너무 큽니다 (최대 20MB).")
    tbl = importer.table(content, file_name, kind, None, None)       # 읽을 수 있는 파일인지 먼저 확인
    with get_pool().connection() as conn, conn.cursor() as cur:
        cycle = _cycle(cur)
        _require_editable(cycle)
        if kind == "SALARY" and not _status(cur, cycle)["steps"]["employees"]:
            raise HTTPException(409, "먼저 평가대상자를 업로드해 주세요.")
        cur.execute("DELETE FROM import_staging WHERE created_at < now() - interval '1 day'")
        cur.execute(
            "INSERT INTO import_staging (kind, file_name, content, created_by) VALUES (%s, %s, %s, %s) RETURNING id",
            (kind, file_name, content, user.emp_no))
        upload_id = cur.fetchone()["id"]
    return {"upload_id": str(upload_id), "file_name": file_name, **_table_view(tbl, kind)}


def _cell(v):
    """미리보기용: 엑셀 날짜(00:00:00)는 날짜만"""
    if isinstance(v, datetime) and v.time() == datetime.min.time():
        return v.date().isoformat()
    return v


def _table_view(tbl: dict, kind: str, mapping: dict | None = None) -> dict:
    fields = importer.FIELDS[kind]
    auto = importer.auto_map(tbl["headers"], fields)
    return {
        "sheets": tbl["sheets"], "sheet": tbl["sheet"], "header_row": tbl["header_row"],
        "headers": tbl["headers"], "fields": tbl["fields"],
        "mapping": mapping if mapping is not None else auto,
        "sample": [[_cell(v) for v in r] for _, r in tbl["rows"][:5]],
        "row_count": len(tbl["rows"]),
    }


class ImportInput(BaseModel):
    upload_id: UUID
    sheet: str | None = None
    header_row: int | None = None
    mapping: dict[str, int | None] | None = None
    target_value: str = "정상"          # 평가유형 중 평가 대상으로 볼 값
    head_keyword: str | None = "장"     # 직책에 이 글자가 있으면 부서장 후보 (팀장, 실장, Lab장 …)
    unit: str | None = None             # 연봉 단위: 원 / 천원 / 만원 (없으면 자동 추정)


def _load(cur, kind: str, body: ImportInput, user: User):
    cur.execute("SELECT * FROM import_staging WHERE id = %s AND kind = %s", (body.upload_id, kind))
    staged = cur.fetchone()
    if staged is None:
        raise HTTPException(404, "업로드한 파일을 찾을 수 없습니다. 파일을 다시 올려 주세요.")
    tbl = importer.table(bytes(staged["content"]), staged["file_name"], kind, body.sheet, body.header_row)
    view = _table_view(tbl, kind, None)
    mapping = body.mapping if body.mapping is not None else view["mapping"]    # 없으면 자동 인식
    mapping = {k: (v if v is not None and 0 <= v < len(tbl["headers"]) else None) for k, v in mapping.items()}
    view["mapping"] = mapping
    return staged, tbl, view, mapping


def _plan(cur, kind, tbl, mapping, body, cycle):
    if kind == "EMPLOYEE":
        return importer.plan_employees(cur, tbl["rows"], mapping, body.target_value, body.head_keyword)
    return importer.plan_salaries(cur, tbl["rows"], mapping, body.unit, cycle["eval_year"])


def _public(plan: dict) -> dict:
    """화면 전송용: 큰 목록은 줄인다"""
    out = {k: v for k, v in plan.items() if k not in ("people", "rows")}
    for key in ("errors", "not_found", "missing_targets", "name_mismatches"):
        if key in out:
            out[f"{key}_count"] = len(out[key])
            out[key] = out[key][:200]
    return out


@router.post("/preview/{kind}")
def preview(kind: str, body: ImportInput, user: User = Depends(hr_user)):
    kind = _kind(kind)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cycle = _cycle(cur)
        _, tbl, view, mapping = _load(cur, kind, body, user)
        missing = [f["label"] for f in view["fields"] if f["required"] and mapping.get(f["key"]) is None]
        if missing:
            return {**view, "plan": None, "missing_fields": missing}
        return {**view, "plan": _public(_plan(cur, kind, tbl, mapping, body, cycle)), "missing_fields": []}


@router.post("/apply/{kind}")
def apply(kind: str, body: ImportInput, user: User = Depends(hr_user)):
    kind = _kind(kind)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cycle = _cycle(cur)
        _require_editable(cycle)
        staged, tbl, view, mapping = _load(cur, kind, body, user)
        plan = _plan(cur, kind, tbl, mapping, body, cycle)
        if kind == "EMPLOYEE":
            if plan["targets"] == 0:
                raise HTTPException(422, f"평가유형이 '{plan['target_value']}'인 대상자가 한 명도 없습니다. 평가유형 값을 확인해 주세요.")
            result = importer.apply_employees(cur, plan)
        else:
            if plan["matched"] == 0:
                raise HTTPException(422, "사번이 일치하는 직원이 없습니다. 사번 열을 확인해 주세요.")
            result = importer.apply_salaries(cur, plan)
        cur.execute(
            "INSERT INTO import_log (cycle_id, kind, file_name, summary, applied_by) VALUES (%s, %s, %s, %s, %s)",
            (cycle["id"], kind, staged["file_name"], json.dumps(result, ensure_ascii=False), user.emp_no))
        cur.execute("DELETE FROM import_staging WHERE id = %s", (body.upload_id,))
        return {"result": result, "status": _status(cur, cycle)}


# ---------------------------------------------------------------------
# 부서장 지정 · 최종확정
# ---------------------------------------------------------------------
class HeadInput(BaseModel):
    employee_id: int | None


@router.put("/departments/{dept_id}/head")
def set_head(dept_id: int, body: HeadInput, user: User = Depends(hr_user)):
    with get_pool().connection() as conn, conn.cursor() as cur:
        cycle = _cycle(cur)
        _require_editable(cycle)
        if body.employee_id is not None:
            cur.execute("SELECT 1 FROM employee WHERE id = %s AND department_id = %s AND eval_target",
                        (body.employee_id, dept_id))
            if cur.fetchone() is None:
                raise HTTPException(422, "해당 부서의 평가 대상자 중에서 부서장을 선택해 주세요.")
        cur.execute("UPDATE department SET head_employee_id = %s WHERE id = %s", (body.employee_id, dept_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "부서가 없습니다.")
        return _status(cur, cycle)


@router.post("/confirm")
def confirm(user: User = Depends(hr_user)):
    """평가화면 최종확정: 조건 확인 → 부서장 계정 발급 → 평가 입력 시작(OPEN)"""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cycle = _cycle(cur)
        if _confirmed(cycle):
            raise HTTPException(409, "이미 최종확정되었습니다.")
        st = _status(cur, cycle)
        problems = []
        if not st["steps"]["employees"]:
            problems.append("평가대상자가 없습니다")
        if st["counts"]["salary_missing"]:
            problems.append(f"연봉이 없는 평가 대상자 {st['counts']['salary_missing']}명")
        if st["counts"]["heads_missing"]:
            problems.append(f"부서장이 지정되지 않은 부서 {st['counts']['heads_missing']}개")
        if problems:
            raise HTTPException(422, "최종확정할 수 없습니다: " + ", ".join(problems))

        # 부서장 중 로그인 계정이 없는 사람에게 임시 비밀번호 발급 (첫 로그인 시 변경 강제)
        accounts = []
        for d in st["departments"]:
            cur.execute("SELECT 1 FROM user_account WHERE employee_id = %s", (d["head_employee_id"],))
            if cur.fetchone():
                continue
            temp = secrets.token_urlsafe(9)
            cur.execute(
                """INSERT INTO user_account (employee_id, login_id, password_hash, must_change_password)
                   VALUES (%s, %s, %s, true)""",
                (d["head_employee_id"], d["head_emp_no"], hash_password(temp)))
            accounts.append({"department": d["name"], "emp_no": d["head_emp_no"], "name": d["head_name"],
                             "temp_password": temp})

        cur.execute(
            """UPDATE evaluation_cycle SET status = 'OPEN', setup_confirmed_at = now(), setup_confirmed_by = %s
                WHERE id = %s""", (user.emp_no, cycle["id"]))
        cycle = _cycle(cur)
        return {"accounts": accounts, "status": _status(cur, cycle)}


@router.post("/unconfirm")
def unconfirm(user: User = Depends(hr_user)):
    """확정 해제: 평가 입력을 멈추고(준비 상태) 다시 업로드·부서장 지정을 할 수 있게 한다. 입력된 평가는 보존."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cycle = _cycle(cur)
        if cycle["status"] not in ("OPEN", "DRAFT"):
            raise HTTPException(409, "평가가 이미 조정·확정 단계로 넘어가 해제할 수 없습니다.")
        cur.execute(
            "UPDATE evaluation_cycle SET status = 'DRAFT', setup_confirmed_at = NULL, setup_confirmed_by = NULL "
            "WHERE id = %s", (cycle["id"],))
        return _status(cur, _cycle(cur))
