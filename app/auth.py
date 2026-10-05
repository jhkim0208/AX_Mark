"""ID/비밀번호 로그인과 권한 판정.

- 계정: user_account (로그인 ID = 사번). 비밀번호는 scrypt 해시로만 저장한다.
- 연속 5회 실패 시 15분 잠금, 최초 로그인/초기화 후에는 비밀번호 변경을 강제한다.
- 권한: 부서장 = department.head_employee_id (자기 부서 평가 입력)
        인사팀 = app_user_role.HR_ADMIN (전 부서 현황 조회)
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .db import get_pool

MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15
PASSWORD_CHANGE_REQUIRED = "PASSWORD_CHANGE_REQUIRED"


@dataclass(frozen=True)
class Settings:
    session_secret: str
    session_max_age: int
    https_only: bool

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        secret = env("SESSION_SECRET")
        if not secret:
            # 미설정 시 프로세스마다 임의 키 → 재시작하면 모두 로그아웃된다 (운영에서는 반드시 설정)
            secret = secrets.token_urlsafe(48)
        return cls(
            session_secret=secret,
            session_max_age=int(env("SESSION_MAX_AGE", 8 * 3600)),
            https_only=env("SESSION_HTTPS_ONLY", "true") == "true",
        )


settings = Settings.from_env()
router = APIRouter(prefix="/auth")


# ---------------------------------------------------------------------
# 비밀번호 해시 (scrypt, 표준 라이브러리)
# ---------------------------------------------------------------------
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    b64 = lambda b: base64.b64encode(b).decode()
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))   # 없는 ID 도 같은 시간 소요 (ID 추측 방지)


def password_policy_error(login_id: str, password: str) -> str | None:
    if len(password) < 8:
        return "비밀번호는 8자 이상이어야 합니다."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "비밀번호는 영문과 숫자를 모두 포함해야 합니다."
    if login_id.lower() in password.lower():
        return "비밀번호에 아이디를 포함할 수 없습니다."
    return None


# ---------------------------------------------------------------------
# 사용자 / 권한
# ---------------------------------------------------------------------
@dataclass
class User:
    employee_id: int
    emp_no: str
    name: str
    is_hr: bool
    must_change_password: bool
    head_of: list[int] = field(default_factory=list)   # 부서장으로 있는 부서

    def can_edit(self, dept_id: int) -> bool:
        return dept_id in self.head_of

    def to_dict(self) -> dict:
        return {
            "employee_id": self.employee_id,
            "emp_no": self.emp_no,
            "name": self.name,
            "is_hr": self.is_hr,
            "head_of": self.head_of,
        }


def _load_user(cur, employee_id: int, password_version: str | None) -> User | None:
    cur.execute(
        """SELECT e.id, e.emp_no, e.name, a.must_change_password,
                  a.password_changed_at::text AS pv,
                  EXISTS (SELECT 1 FROM app_user_role r
                           WHERE r.employee_id = e.id AND r.role = 'HR_ADMIN') AS is_hr,
                  ARRAY(SELECT d.id FROM department d
                         WHERE d.head_employee_id = e.id ORDER BY d.id) AS head_of
             FROM employee e
             JOIN user_account a ON a.employee_id = e.id
            WHERE e.id = %s AND e.status = 'ACTIVE' AND a.is_active""",
        (employee_id,),
    )
    row = cur.fetchone()
    # 비밀번호가 바뀌면 기존 세션은 무효
    if row is None or row["pv"] != password_version:
        return None
    return User(row["id"], row["emp_no"], row["name"], row["is_hr"],
                row["must_change_password"], list(row["head_of"]))


def session_user(request: Request) -> User:
    """로그인만 확인 (비밀번호 변경 화면용)."""
    emp_id = request.session.get("uid")
    if emp_id is None:
        raise HTTPException(401, "로그인이 필요합니다.")
    with get_pool().connection() as conn, conn.cursor() as cur:
        user = _load_user(cur, emp_id, request.session.get("pv"))
    if user is None:                                    # 퇴사/계정 비활성/비밀번호 변경
        request.session.clear()
        raise HTTPException(401, "로그인이 필요합니다.")
    return user


def current_user(request: Request) -> User:
    """API 의존성: 로그인 + 비밀번호 변경 완료 + 역할(부서장 또는 인사팀) 확인."""
    user = session_user(request)
    if user.must_change_password:
        raise HTTPException(403, PASSWORD_CHANGE_REQUIRED)
    if not user.is_hr and not user.head_of:
        raise HTTPException(403, "이 시스템을 사용할 권한이 없습니다.")
    return user


def hr_user(request: Request) -> User:
    user = current_user(request)
    if not user.is_hr:
        raise HTTPException(403, "인사팀 담당자만 조회할 수 있습니다.")
    return user


def home_path(user: User) -> str:
    if user.must_change_password:
        return "/change-password"
    return "/" if user.head_of else "/hr"


# ---------------------------------------------------------------------
# 로그인 / 로그아웃 / 비밀번호 변경
# ---------------------------------------------------------------------
class LoginInput(BaseModel):
    login_id: str
    password: str


@router.post("/login")
def login(body: LoginInput, request: Request):
    invalid = HTTPException(401, "아이디 또는 비밀번호가 올바르지 않습니다.")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT a.employee_id, a.password_hash, a.failed_attempts,
                      a.locked_until > now() AS locked
                 FROM user_account a
                 JOIN employee e ON e.id = a.employee_id
                WHERE lower(a.login_id) = lower(%s)
                  AND a.is_active AND e.status = 'ACTIVE'
                  FOR UPDATE OF a""",
            (body.login_id.strip(),),
        )
        acct = cur.fetchone()
        if acct is None:
            verify_password(body.password, _DUMMY_HASH)
            raise invalid
        if acct["locked"]:
            raise HTTPException(423, f"로그인 {MAX_FAILED_ATTEMPTS}회 실패로 계정이 잠겼습니다. "
                                     f"{LOCK_MINUTES}분 후 다시 시도하거나 인사팀에 문의하세요.")
        if not verify_password(body.password, acct["password_hash"]):
            cur.execute(
                """UPDATE user_account
                      SET failed_attempts = failed_attempts + 1,
                          locked_until = CASE WHEN failed_attempts + 1 >= %s
                                              THEN now() + make_interval(mins => %s) END
                    WHERE employee_id = %s""",
                (MAX_FAILED_ATTEMPTS, LOCK_MINUTES, acct["employee_id"]),
            )
            conn.commit()                               # 아래 예외로 롤백되지 않도록
            raise invalid
        cur.execute(
            """UPDATE user_account
                  SET failed_attempts = 0, locked_until = NULL, last_login_at = now()
                WHERE employee_id = %s
            RETURNING password_changed_at::text AS pv""",
            (acct["employee_id"],),
        )
        pv = cur.fetchone()["pv"]
        user = _load_user(cur, acct["employee_id"], pv)

    request.session.clear()                             # 세션 고정 공격 방지
    request.session.update({"uid": user.employee_id, "pv": pv})
    return {"redirect": home_path(user)}


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"redirect": "/login"}


@router.get("/logout")
def logout_link(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


class ChangePasswordInput(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password")
def change_password(body: ChangePasswordInput, request: Request):
    user = session_user(request)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT login_id, password_hash FROM user_account WHERE employee_id = %s FOR UPDATE",
            (user.employee_id,),
        )
        acct = cur.fetchone()
        if not verify_password(body.current_password, acct["password_hash"]):
            raise HTTPException(400, "현재 비밀번호가 올바르지 않습니다.")
        if body.new_password == body.current_password:
            raise HTTPException(400, "현재 비밀번호와 다른 비밀번호를 입력하세요.")
        error = password_policy_error(acct["login_id"], body.new_password)
        if error:
            raise HTTPException(400, error)
        cur.execute(
            """UPDATE user_account
                  SET password_hash = %s, must_change_password = false,
                      password_changed_at = clock_timestamp()
                WHERE employee_id = %s
            RETURNING password_changed_at::text AS pv""",
            (hash_password(body.new_password), user.employee_id),
        )
        pv = cur.fetchone()["pv"]
    request.session["pv"] = pv                          # 현재 세션만 유지, 다른 세션은 무효화
    user.must_change_password = False
    return {"redirect": home_path(user)}
