"""사내 SSO(OIDC) 로그인과 권한 판정.

- 표준 OpenID Connect(Authorization Code + PKCE)로 Entra ID, Google Workspace, Okta,
  Keycloak 등 대부분의 사내 SSO와 연동된다.
- SSO 사용자는 ID 토큰의 클레임(기본: email)으로 employee 에 매핑된다.
- 권한: 부서장 = department.head_employee_id (자기 부서 조회·입력)
        인사팀 = app_user_role.HR_ADMIN (전 부서 조회)
"""
import html
import os
import time
from urllib.parse import urlencode
from dataclasses import dataclass, field

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .db import get_pool


@dataclass(frozen=True)
class Settings:
    mode: str                     # "oidc" | "dev"
    session_secret: str
    session_max_age: int
    https_only: bool
    issuer: str | None
    client_id: str | None
    client_secret: str | None
    redirect_uri: str | None
    scope: str
    user_claim: str               # ID 토큰에서 읽을 클레임
    match_column: str             # employee 매핑 컬럼: email | emp_no

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        mode = env("AUTH_MODE", "oidc")
        if mode not in ("oidc", "dev"):
            raise RuntimeError("AUTH_MODE 는 oidc 또는 dev 여야 합니다.")
        secret = env("SESSION_SECRET")
        if not secret:
            if mode == "oidc":
                raise RuntimeError("SESSION_SECRET 환경변수가 필요합니다.")
            secret = "dev-only-insecure-secret"
        match_column = env("OIDC_MATCH_COLUMN", "email")
        if match_column not in ("email", "emp_no"):
            raise RuntimeError("OIDC_MATCH_COLUMN 은 email 또는 emp_no 여야 합니다.")
        return cls(
            mode=mode,
            session_secret=secret,
            session_max_age=int(env("SESSION_MAX_AGE", 8 * 3600)),
            https_only=env("SESSION_HTTPS_ONLY", "true" if mode == "oidc" else "false") == "true",
            issuer=(env("OIDC_ISSUER") or "").rstrip("/") or None,
            client_id=env("OIDC_CLIENT_ID"),
            client_secret=env("OIDC_CLIENT_SECRET"),
            redirect_uri=env("OIDC_REDIRECT_URI"),
            scope=env("OIDC_SCOPE", "openid email profile"),
            user_claim=env("OIDC_USER_CLAIM", "email"),
            match_column=match_column,
        )


settings = Settings.from_env()

oauth = OAuth()
if settings.mode == "oidc" and settings.issuer and settings.client_id:
    oauth.register(
        name="sso",
        server_metadata_url=f"{settings.issuer}/.well-known/openid-configuration",
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        client_kwargs={"scope": settings.scope, "code_challenge_method": "S256"},
    )

router = APIRouter(prefix="/auth")


# ---------------------------------------------------------------------
# 사용자 / 권한
# ---------------------------------------------------------------------
@dataclass
class User:
    employee_id: int
    emp_no: str
    name: str
    is_hr: bool
    head_of: list[int] = field(default_factory=list)   # 부서장으로 있는 부서

    def can_view(self, dept_id: int) -> bool:
        return self.is_hr or dept_id in self.head_of

    def can_edit(self, dept_id: int) -> bool:
        return dept_id in self.head_of                  # 인사팀은 조회 전용

    def to_dict(self) -> dict:
        return {
            "employee_id": self.employee_id,
            "emp_no": self.emp_no,
            "name": self.name,
            "is_hr": self.is_hr,
            "head_of": self.head_of,
            "auth_mode": settings.mode,
        }


def _load_user(cur, employee_id: int) -> User | None:
    cur.execute(
        """SELECT e.id, e.emp_no, e.name,
                  EXISTS (SELECT 1 FROM app_user_role r
                           WHERE r.employee_id = e.id AND r.role = 'HR_ADMIN') AS is_hr,
                  ARRAY(SELECT d.id FROM department d
                         WHERE d.head_employee_id = e.id ORDER BY d.id) AS head_of
             FROM employee e
            WHERE e.id = %s AND e.status = 'ACTIVE'""",
        (employee_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return User(row["id"], row["emp_no"], row["name"], row["is_hr"], list(row["head_of"]))


def current_user(request: Request) -> User:
    """API 의존성: 로그인 + 권한(부서장 또는 인사팀) 확인."""
    emp_id = request.session.get("employee_id")
    if emp_id is None:
        raise HTTPException(401, "로그인이 필요합니다.")
    with get_pool().connection() as conn, conn.cursor() as cur:
        user = _load_user(cur, emp_id)
    if user is None:                                    # 퇴사/휴직 등으로 비활성화
        request.session.clear()
        raise HTTPException(401, "로그인이 필요합니다.")
    if not user.is_hr and not user.head_of:
        raise HTTPException(403, "이 시스템을 사용할 권한이 없습니다.")
    return user


def _start_session(request: Request, employee_id: int) -> None:
    request.session.clear()                             # 세션 고정 공격 방지
    request.session["employee_id"] = employee_id
    request.session["login_at"] = int(time.time())


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><link rel="stylesheet" href="/static/style.css"></head>
<body><main class="auth-page"><h1>{html.escape(title)}</h1>{body}</main></body></html>""",
        status_code=status,
    )


# ---------------------------------------------------------------------
# SSO 흐름
# ---------------------------------------------------------------------
async def fetch_userinfo(request: Request) -> dict:
    """인가 코드를 토큰으로 교환하고 검증된 ID 토큰 클레임을 돌려준다."""
    token = await oauth.sso.authorize_access_token(request)
    return token.get("userinfo") or await oauth.sso.userinfo(token=token)


@router.get("/login")
async def login(request: Request):
    if settings.mode == "dev":
        return RedirectResponse("/auth/dev-login")
    if oauth.create_client("sso") is None:
        raise HTTPException(500, "SSO 설정(OIDC_ISSUER, OIDC_CLIENT_ID)이 없습니다.")
    redirect_uri = settings.redirect_uri or str(request.url_for("auth_callback"))
    return await oauth.sso.authorize_redirect(request, redirect_uri)


@router.get("/callback", name="auth_callback")
async def callback(request: Request):
    if settings.mode != "oidc":
        raise HTTPException(404)
    try:
        userinfo = await fetch_userinfo(request)
    except OAuthError as err:
        return _page("로그인 실패", f"<p>SSO 인증에 실패했습니다: {html.escape(err.error)}</p>"
                     '<p><a href="/auth/login">다시 로그인</a></p>', 401)

    value = userinfo.get(settings.user_claim)
    if not value:
        return _page("로그인 실패", f"<p>SSO 응답에 {html.escape(settings.user_claim)} 정보가 없습니다.</p>", 401)

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT id FROM employee WHERE lower({settings.match_column}) = lower(%s) AND status = 'ACTIVE'",
            (value,),
        )
        row = cur.fetchone()
    if row is None:
        return _page("접근 불가", f"<p>{html.escape(value)} 계정은 이 시스템에 등록된 재직자가 아닙니다.</p>"
                     "<p>인사팀에 문의해 주세요.</p>", 403)

    _start_session(request, row["id"])
    return RedirectResponse("/", status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    if settings.mode == "oidc" and oauth.create_client("sso") is not None:
        try:
            metadata = await oauth.sso.load_server_metadata()
        except Exception:                               # IdP 장애 시에도 로컬 로그아웃은 완료
            metadata = {}
        end_session = metadata.get("end_session_endpoint")
        if end_session:
            params = urlencode({
                "client_id": settings.client_id,
                "post_logout_redirect_uri": str(request.url_for("logged_out")),
            })
            return RedirectResponse(f"{end_session}?{params}")
    return RedirectResponse("/auth/logged-out")


@router.get("/logged-out", name="logged_out")
def logged_out():
    return _page("로그아웃되었습니다", '<p><a href="/auth/login">다시 로그인</a></p>')


# ---------------------------------------------------------------------
# 개발용 로그인 (AUTH_MODE=dev 에서만 동작)
# ---------------------------------------------------------------------
@router.get("/dev-login")
def dev_login(request: Request, emp_no: str | None = None):
    if settings.mode != "dev":
        raise HTTPException(404)
    with get_pool().connection() as conn, conn.cursor() as cur:
        if emp_no:
            cur.execute("SELECT id FROM employee WHERE emp_no = %s AND status = 'ACTIVE'", (emp_no,))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(404, "사번이 없습니다.")
            _start_session(request, row["id"])
            return RedirectResponse("/", status_code=303)
        cur.execute(
            """SELECT e.emp_no, e.name, string_agg(d.name, ', ') AS depts,
                      bool_or(r.role IS NOT NULL) AS is_hr
                 FROM employee e
                 LEFT JOIN department d ON d.head_employee_id = e.id
                 LEFT JOIN app_user_role r ON r.employee_id = e.id
                WHERE e.status = 'ACTIVE'
                GROUP BY e.id ORDER BY e.emp_no"""
        )
        rows = cur.fetchall()
    items = "".join(
        f'<li><a href="/auth/dev-login?emp_no={html.escape(r["emp_no"])}">'
        f'{html.escape(r["emp_no"])} {html.escape(r["name"])}</a>'
        f' <span class="muted">{html.escape(r["depts"] or "")}{" · 인사팀" if r["is_hr"] else ""}</span></li>'
        for r in rows
    )
    return _page("개발용 로그인",
                 '<p class="dev-warning">AUTH_MODE=dev – 운영 환경에서는 절대 사용하지 마세요.</p>'
                 f"<ul>{items}</ul>")
