from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from . import auth
from .auth import User, current_user, hr_user
from .db import close_pool, get_pool

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    close_pool()


app = FastAPI(title="업적평가 보상 시뮬레이션", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.add_middleware(
    SessionMiddleware,
    secret_key=auth.settings.session_secret,
    session_cookie="axmark_session",
    max_age=auth.settings.session_max_age,
    same_site="lax",
    https_only=auth.settings.https_only,
)
app.include_router(auth.router)


@app.middleware("http")
async def no_stale_assets(request: Request, call_next):
    """화면(HTML·CSS·JS)은 브라우저가 매번 서버에 변경 여부를 확인하게 한다.
    코드를 업데이트한 뒤 브라우저 캐시 때문에 예전 화면이 보이는 문제를 막는다 (변경 없으면 304로 빠르게 응답)."""
    response = await call_next(request)
    if request.url.path.startswith("/static/") or response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# ---------------------------------------------------------------------
# 공통 조회
# ---------------------------------------------------------------------
def _cycle(cur, cycle_id: int | None):
    if cycle_id is None:
        cur.execute("SELECT * FROM evaluation_cycle ORDER BY eval_year DESC LIMIT 1")
    else:
        cur.execute("SELECT * FROM evaluation_cycle WHERE id = %s", (cycle_id,))
    cycle = cur.fetchone()
    if cycle is None:
        raise HTTPException(404, "평가주기가 없습니다.")
    return cycle


def _department(cur, department_id: int):
    cur.execute(
        """SELECT d.id, d.code, d.name, d.parent_id, d.head_employee_id,
                  h.name AS head_name
             FROM department d
             LEFT JOIN employee h ON h.id = d.head_employee_id
            WHERE d.id = %s""",
        (department_id,),
    )
    dept = cur.fetchone()
    if dept is None:
        raise HTTPException(404, "부서가 없습니다.")
    return dept


# 평가 대상: 부서원(부서장 제외) + 직속 하위 부서의 부서장 (DB 함수 fn_unit_targets)
TARGETS_SQL = "SELECT fn_unit_targets(%(dept)s) AS id"

MEMBERS_SQL = f"""
    WITH targets AS ({TARGETS_SQL})
    SELECT e.id              AS employee_id,
           e.emp_no,
           e.name,
           jl.name           AS job_level,
           md.name           AS department_name,
           ec.base_salary    AS cur_base_salary,
           ec.incentive_amount AS cur_incentive,
           g.code            AS grade_code,
           g.is_top_grade,
           ev.status         AS evaluation_status,
           s.pay_grade_code, s.org_top_rate, s.org_factor,
           s.base_up_rate, s.perf_raise_rate, s.raise_rate,
           s.next_base_salary, s.next_incentive, s.next_total,
           s.delta_base_salary, s.delta_incentive, s.delta_total, s.band_capped
      FROM targets t
      JOIN employee e    ON e.id = t.id
      JOIN job_level jl  ON jl.id = e.job_level_id
      JOIN department md ON md.id = e.department_id
      LEFT JOIN employee_compensation ec
             ON ec.employee_id = e.id AND ec.comp_year = %(year)s
      LEFT JOIN evaluation ev
             ON ev.employee_id = e.id AND ev.cycle_id = %(cycle)s
      LEFT JOIN evaluation_grade g ON g.id = ev.grade_id
      LEFT JOIN LATERAL fn_simulate_comp(%(cycle)s, e.id, ev.grade_id) s ON true
    {{where}}
     ORDER BY jl.sort_order DESC, e.emp_no
"""


def _members(cur, cycle, dept_id: int, employee_id: int | None = None):
    where = "WHERE e.id = %(emp)s" if employee_id is not None else ""
    cur.execute(
        MEMBERS_SQL.format(where=where),
        {"dept": dept_id, "cycle": cycle["id"], "year": cycle["eval_year"], "emp": employee_id},
    )
    return cur.fetchall()


def _editable(cycle, member, user: User, dept_id: int) -> bool:
    return (user.can_edit(dept_id)
            and cycle["status"] == "OPEN"
            and member["evaluation_status"] in (None, "DRAFT"))


def _require_edit(user: User, dept_id: int) -> None:
    if not user.can_edit(dept_id):
        raise HTTPException(403, "이 부서의 평가를 입력할 권한이 없습니다.")


def _set_actor(cur, user: User) -> None:
    """평가 변경 이력(evaluation_history.changed_by)에 로그인 사용자 사번을 남긴다."""
    cur.execute("SELECT set_config('app.user', %s, true)", (user.emp_no,))


def _grades(cur, cycle_id: int):
    cur.execute(
        """SELECT g.code, g.name, g.raise_sign, g.incentive_eligible, g.is_top_grade,
                  gd.min_ratio, gd.max_ratio, COALESCE(gd.alert_on_exceed, false) AS alert_on_exceed
             FROM evaluation_grade g
             LEFT JOIN grade_distribution_guide gd ON gd.grade_id = g.id
            WHERE g.cycle_id = %s ORDER BY g.sort_order""",
        (cycle_id,),
    )
    return cur.fetchall()


# ---------------------------------------------------------------------
# 화면
# ---------------------------------------------------------------------
def _page_user(request: Request) -> User | None:
    try:
        return auth.session_user(request)
    except HTTPException:
        return None


@app.get("/login")
def login_page(request: Request):
    user = _page_user(request)
    if user is not None:
        return RedirectResponse(auth.home_path(user))
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/change-password")
def change_password_page(request: Request):
    if _page_user(request) is None:
        return RedirectResponse("/login")
    return FileResponse(STATIC_DIR / "change-password.html")


@app.get("/")
def index(request: Request):
    """부서장 평가 입력 화면"""
    user = _page_user(request)
    if user is None or user.must_change_password or not user.head_of:
        return RedirectResponse("/login" if user is None else auth.home_path(user))
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/hr")
def hr_page(request: Request):
    """인사팀 부서별 현황 화면"""
    user = _page_user(request)
    if user is None or user.must_change_password or not user.is_hr:
        return RedirectResponse("/login" if user is None else auth.home_path(user))
    return FileResponse(STATIC_DIR / "hr.html")


# ---------------------------------------------------------------------
# API – 공통
# ---------------------------------------------------------------------
@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return user.to_dict()


@app.get("/api/context")
def context(cycle_id: int | None = None, user: User = Depends(current_user)):
    with get_pool().connection() as conn, conn.cursor() as cur:
        cycle = _cycle(cur, cycle_id)
        grades = _grades(cur, cycle["id"])
        cur.execute("SELECT base_up_rate FROM comp_policy WHERE cycle_id = %s", (cycle["id"],))
        policy = cur.fetchone()
        cur.execute(
            """SELECT d.id, d.name, d.parent_id, h.name AS head_name
                 FROM department d
                 LEFT JOIN employee h ON h.id = d.head_employee_id
                WHERE d.id = ANY(%s)
                ORDER BY d.code""",
            (user.head_of,),
        )
        departments = cur.fetchall()                    # 본인이 부서장인 부서만
    return {"cycle": cycle, "grades": grades, "policy": policy, "departments": departments,
            "user": user.to_dict()}


# ---------------------------------------------------------------------
# API – 부서장 평가 입력
# ---------------------------------------------------------------------
@app.get("/api/departments/{dept_id}/sheet")
def sheet(dept_id: int, cycle_id: int | None = None, user: User = Depends(current_user)):
    _require_edit(user, dept_id)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cycle = _cycle(cur, cycle_id)
        return _sheet_data(cur, cycle, dept_id, user)


def _sheet_data(cur, cycle, dept_id: int, user: User) -> dict:
    dept = _department(cur, dept_id)
    members = _members(cur, cycle, dept_id)
    stats = _stats(members, _grades(cur, cycle["id"]), cycle)
    stats["org_factor"] = _org_factor(cur, cycle["id"], dept_id)
    for m in members:
        m["editable"] = _editable(cycle, m, user, dept_id)
    return {
        "cycle": cycle,
        "department": dept,
        "members": members,
        "stats": stats,             # 상위평가율 · 경고 · A·B 조정계수
        "can_submit": cycle["status"] == "OPEN"
        and bool(members)
        and all(m["evaluation_status"] in (None, "DRAFT") for m in members),
    }


class GradeInput(BaseModel):
    grade_code: str | None = None


def _save_grades(cur, cycle, dept_id: int, user: User, employee_ids: list[int],
                 grade_code: str | None) -> None:
    """여러 대상자에게 같은 등급을 임시저장(DRAFT). 하나라도 불가하면 전체 취소(트랜잭션)."""
    if cycle["status"] != "OPEN":
        raise HTTPException(409, "평가 입력 기간이 아닙니다.")
    _department(cur, dept_id)

    cur.execute(f"SELECT id FROM ({TARGETS_SQL}) t WHERE id = ANY(%(emps)s)",
                {"dept": dept_id, "emps": employee_ids})
    if len({r["id"] for r in cur.fetchall()}) != len(set(employee_ids)):
        raise HTTPException(403, "이 부서의 평가 대상자가 아닌 직원이 포함되어 있습니다.")

    grade_id = None
    if grade_code:
        cur.execute("SELECT id FROM evaluation_grade WHERE cycle_id = %s AND code = %s",
                    (cycle["id"], grade_code))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(422, "존재하지 않는 평가등급입니다.")
        grade_id = row["id"]

    cur.execute(
        """SELECT 1 FROM evaluation
            WHERE cycle_id = %s AND employee_id = ANY(%s) AND status <> 'DRAFT'
              FOR UPDATE""",
        (cycle["id"], employee_ids),
    )
    if cur.fetchone():
        raise HTTPException(409, "이미 제출된 평가는 수정할 수 없습니다.")

    cur.executemany(
        """INSERT INTO evaluation (cycle_id, employee_id, evaluator_id, grade_id)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (cycle_id, employee_id)
           DO UPDATE SET grade_id = EXCLUDED.grade_id, evaluator_id = EXCLUDED.evaluator_id""",
        [(cycle["id"], emp, user.employee_id, grade_id) for emp in sorted(set(employee_ids))],
    )


@app.put("/api/departments/{dept_id}/evaluations/{employee_id}")
def save_grade(dept_id: int, employee_id: int, body: GradeInput,
               cycle_id: int | None = None, user: User = Depends(current_user)):
    """등급 선택 즉시 저장(DRAFT)하고 재계산된 처우를 돌려준다."""
    _require_edit(user, dept_id)
    with get_pool().connection() as conn, conn.cursor() as cur:
        _set_actor(cur, user)
        cycle = _cycle(cur, cycle_id)
        _save_grades(cur, cycle, dept_id, user, [employee_id], body.grade_code)
        member = _members(cur, cycle, dept_id, employee_id)[0]
    member["editable"] = _editable(cycle, member, user, dept_id)
    return member


class GradeChange(BaseModel):
    employee_id: int
    grade_code: str | None = None


class ChangesInput(BaseModel):
    changes: list[GradeChange]


def _apply_changes(cur, cycle, dept_id: int, user: User, changes: list[GradeChange]) -> int:
    by_code: dict[str | None, list[int]] = {}
    for c in changes:
        by_code.setdefault(c.grade_code or None, []).append(c.employee_id)
    for code, ids in by_code.items():
        _save_grades(cur, cycle, dept_id, user, ids, code)
    return len({c.employee_id for c in changes})


@app.post("/api/departments/{dept_id}/preview")
def preview(dept_id: int, body: ChangesInput,
            cycle_id: int | None = None, user: User = Depends(current_user)):
    """저장하지 않은 화면상의 변경을 반영했을 때의 결과를 계산만 하고 DB에는 남기지 않는다.
       (조정계수가 부서 전체 등급에 따라 달라지므로 서버에서 같은 함수로 계산 후 롤백)"""
    _require_edit(user, dept_id)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cycle = _cycle(cur, cycle_id)
        with conn.transaction(force_rollback=True):
            _apply_changes(cur, cycle, dept_id, user, body.changes)
            return _sheet_data(cur, cycle, dept_id, user)


@app.post("/api/departments/{dept_id}/save")
def save_changes(dept_id: int, body: ChangesInput,
                 cycle_id: int | None = None, user: User = Depends(current_user)):
    """임시저장: 화면에서 바꾼 등급을 한 번에 저장(DRAFT). 저장 전에는 DB에 남지 않는다."""
    _require_edit(user, dept_id)
    with get_pool().connection() as conn, conn.cursor() as cur:
        _set_actor(cur, user)
        cycle = _cycle(cur, cycle_id)
        saved = _apply_changes(cur, cycle, dept_id, user, body.changes)
    return {"saved": saved}


class BulkGradeInput(BaseModel):
    employee_ids: list[int]
    grade_code: str | None = None


@app.post("/api/departments/{dept_id}/evaluations/bulk")
def save_grades_bulk(dept_id: int, body: BulkGradeInput,
                     cycle_id: int | None = None, user: User = Depends(current_user)):
    """체크한 여러 부서원에게 같은 등급을 한 번에 임시저장."""
    _require_edit(user, dept_id)
    if not body.employee_ids:
        raise HTTPException(422, "선택된 부서원이 없습니다.")
    with get_pool().connection() as conn, conn.cursor() as cur:
        _set_actor(cur, user)
        cycle = _cycle(cur, cycle_id)
        _save_grades(cur, cycle, dept_id, user, body.employee_ids, body.grade_code)
    return {"updated": len(set(body.employee_ids))}


@app.post("/api/departments/{dept_id}/submit")
def submit(dept_id: int, cycle_id: int | None = None, user: User = Depends(current_user)):
    _require_edit(user, dept_id)
    with get_pool().connection() as conn, conn.cursor() as cur:
        _set_actor(cur, user)
        cycle = _cycle(cur, cycle_id)
        if cycle["status"] != "OPEN":
            raise HTTPException(409, "평가 입력 기간이 아닙니다.")
        _department(cur, dept_id)
        members = _members(cur, cycle, dept_id)
        missing = [m["name"] for m in members if m["grade_code"] is None]
        if missing:
            raise HTTPException(422, f"미입력 대상자가 있습니다: {', '.join(missing)}")
        cur.execute(
            f"""UPDATE evaluation SET status = 'SUBMITTED', submitted_at = now()
                 WHERE cycle_id = %(cycle)s AND status = 'DRAFT'
                   AND employee_id IN ({TARGETS_SQL})""",
            {"cycle": cycle["id"], "dept": dept_id},
        )
        count = cur.rowcount
    return {"submitted": count}


# ---------------------------------------------------------------------
# API – 인사팀 현황
# ---------------------------------------------------------------------
def _stats(members: list[dict], grades: list[dict], cycle: dict) -> dict:
    """부서(평가 단위) 현황
       상위평가율      = A·B(is_top_grade) 인원 ÷ 평가 입력 인원
       평균 성과인상률 = 평가 입력 인원의 성과인상률 단순 평균
       경고            = 경고 대상 등급 가이드 초과(A > 10%) 또는 상위평가율 > 상한(40%)"""
    rated = [m for m in members if m["grade_code"] is not None]
    priced = [m for m in rated if m["perf_raise_rate"] is not None]
    submitted = [m for m in members if m["evaluation_status"] in ("SUBMITTED", "CONFIRMED")]
    n = len(rated)
    avg = lambda key: sum(m[key] for m in priced) / len(priced) if priced else None
    if not members:
        status = "NONE"
    elif len(submitted) == len(members):
        status = "SUBMITTED"
    elif n == 0:
        status = "NOT_STARTED"
    else:
        status = "IN_PROGRESS"

    counts = {g["code"]: sum(1 for m in rated if m["grade_code"] == g["code"]) for g in grades}
    ratios = {code: (c / n if n else None) for code, c in counts.items()}
    top = sum(1 for m in rated if m["is_top_grade"])
    top_rate = top / n if n else None
    # 경계값(정확히 10%·40%)은 경고하지 않는다 – 부동소수 오차 없이 인원수로 비교
    exceeds = lambda count, limit: n > 0 and count > limit * n
    alerts = []
    for g in grades:
        r = ratios[g["code"]]
        if g["alert_on_exceed"] and g["max_ratio"] is not None and exceeds(counts[g["code"]], g["max_ratio"]):
            alerts.append({"type": f"GRADE_{g['code']}",
                           "message": f"{g['code']} 비율 {r * 100:.1f}% > 가이드 {g['max_ratio'] * 100:.0f}%"})
    limit = cycle["top_grade_ratio_limit"]
    if limit is not None and exceeds(top, limit):
        top_codes = "·".join(g["code"] for g in grades if g["is_top_grade"])
        alerts.append({"type": "TOP",
                       "message": f"{top_codes} 비율 {top_rate * 100:.1f}% > 기준 {limit * 100:.0f}%"})
    return {
        "headcount": len(members),
        "rated": n,
        "submitted": len(submitted),
        "status": status,
        "grade_counts": counts,
        "grade_ratios": ratios,
        "top_rate": top_rate,
        "alerts": alerts,
        "avg_perf_raise_rate": avg("perf_raise_rate"),
        "avg_raise_rate": avg("raise_rate"),
    }


def _org_factor(cur, cycle_id: int, dept_id: int):
    """부서 상위평가율에 따른 A·B 성과인상률 조정계수 (DB 함수 fn_org_factor)"""
    cur.execute("SELECT factor FROM fn_org_factor(%s, %s)", (cycle_id, dept_id))
    row = cur.fetchone()
    return row["factor"] if row else None


@app.get("/api/hr/overview")
def hr_overview(cycle_id: int | None = None, user: User = Depends(hr_user)):
    with get_pool().connection() as conn, conn.cursor() as cur:
        cycle = _cycle(cur, cycle_id)
        grades = _grades(cur, cycle["id"])
        cur.execute("SELECT base_up_rate FROM comp_policy WHERE cycle_id = %s", (cycle["id"],))
        policy = cur.fetchone()
        cur.execute(
            """SELECT d.id, d.code, d.name, d.parent_id, p.name AS parent_name,
                      h.name AS head_name
                 FROM department d
                 LEFT JOIN department p ON p.id = d.parent_id
                 LEFT JOIN employee h ON h.id = d.head_employee_id
                ORDER BY COALESCE(d.parent_id, d.id), d.parent_id NULLS FIRST, d.code"""
        )
        departments = cur.fetchall()
        all_members = []
        for d in departments:
            members = _members(cur, cycle, d["id"])
            d.update(_stats(members, grades, cycle))
            d["org_factor"] = _org_factor(cur, cycle["id"], d["id"])
            all_members += members

    return {
        "cycle": cycle,
        "grades": grades,
        "policy": policy,
        "top_rate_limit": cycle["top_grade_ratio_limit"],      # 초과 시 경고
        "total": _stats(all_members, grades, cycle),
        "departments": [d for d in departments if d["headcount"] > 0],
        "user": user.to_dict(),
    }


@app.get("/api/hr/departments/{dept_id}")
def hr_department(dept_id: int, cycle_id: int | None = None, user: User = Depends(hr_user)):
    with get_pool().connection() as conn, conn.cursor() as cur:
        cycle = _cycle(cur, cycle_id)
        grades = _grades(cur, cycle["id"])
        dept = _department(cur, dept_id)
        members = _members(cur, cycle, dept_id)
        stats = _stats(members, grades, cycle)
        stats["org_factor"] = _org_factor(cur, cycle["id"], dept_id)
    return {"department": dept, "stats": stats, "members": members}


# ---------------------------------------------------------------------
# API – 성과인상률 산출공식 (부서장 안내 팝업)
# ---------------------------------------------------------------------
@app.get("/api/formula")
def formula(dept_id: int | None = None, cycle_id: int | None = None,
            user: User = Depends(current_user)):
    if dept_id is not None and not (user.can_edit(dept_id) or user.is_hr):
        raise HTTPException(403, "이 부서를 조회할 권한이 없습니다.")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cycle = _cycle(cur, cycle_id)
        cur.execute(
            """SELECT base_up_rate, top_rate_ref, org_factor_min, org_factor_max, perf_rate_unit
                 FROM comp_policy WHERE cycle_id = %s""", (cycle["id"],))
        policy = cur.fetchone()
        cur.execute(
            """SELECT jl.id, jl.code, lr.base_perf_rate
                 FROM comp_level_rate lr JOIN job_level jl ON jl.id = lr.job_level_id
                WHERE lr.cycle_id = %s ORDER BY jl.sort_order""", (cycle["id"],))
        levels = cur.fetchall()
        cur.execute(
            """SELECT g.id, g.code, g.name, r.perf_factor, r.org_adjusted, r.base_up_applies,
                      g.incentive_eligible, r.incentive_rate
                 FROM comp_rule r JOIN evaluation_grade g ON g.id = r.grade_id
                WHERE r.cycle_id = %s ORDER BY g.sort_order""", (cycle["id"],))
        rules = cur.fetchall()
        cur.execute(
            """SELECT g.code AS grade, jl.code AS level, o.perf_raise_rate
                 FROM comp_level_grade_override o
                 JOIN evaluation_grade g ON g.id = o.grade_id
                 JOIN job_level jl ON jl.id = o.job_level_id
                WHERE o.cycle_id = %s ORDER BY g.sort_order, jl.sort_order""", (cycle["id"],))
        overrides = cur.fetchall()
        for r in rules:                                  # 직급별 고정값이 있는 등급 (예: E)
            r["fixed_by_level"] = {o["level"]: o["perf_raise_rate"] for o in overrides
                                   if o["grade"] == r["code"]} or None

        # 상위평가율별 조정계수 예시
        cur.execute(
            """SELECT r AS top_rate,
                      round(LEAST(p.org_factor_max, GREATEST(p.org_factor_min, p.top_rate_ref / r)), 4) AS factor
                 FROM comp_policy p, unnest(ARRAY[0.2, 0.3, 0.4, 0.5, 0.6, 0.8]::numeric[]) AS r
                WHERE p.cycle_id = %s""", (cycle["id"],))
        examples = cur.fetchall()

        current = None
        factor = 1
        if dept_id is not None:
            cur.execute("SELECT * FROM fn_org_factor(%s, %s)", (cycle["id"], dept_id))
            current = cur.fetchone()
            factor = current["factor"]

        # 직급 × 등급 성과인상률표 (현재 부서 조정계수 기준, 부서 미지정 시 1.0)
        matrix = []
        for lv in levels:
            row = {"level": lv["code"], "rates": {}}
            for r in rules:
                cur.execute("SELECT fn_perf_rate(%s, %s, %s, %s) AS perf",
                            (cycle["id"], lv["id"], r["id"], factor))
                perf = cur.fetchone()["perf"]
                base = policy["base_up_rate"] if r["base_up_applies"] else 0
                row["rates"][r["code"]] = {"perf": perf, "total": perf + base}
            matrix.append(row)
    for r in rules:
        del r["id"]
    for lv in levels:
        del lv["id"]
    return {"policy": policy, "levels": levels, "rules": rules, "examples": examples,
            "current": current, "factor": factor, "matrix": matrix}
