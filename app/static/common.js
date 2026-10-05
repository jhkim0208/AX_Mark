// 화면 공통: API 호출, 숫자 포맷, 상단 사용자 메뉴
const $ = (id) => document.getElementById(id);
const won = (v) => (v == null ? "-" : Math.round(v).toLocaleString("ko-KR"));
const pct = (v, digits = 1) => (v == null ? "-" : (v * 100).toFixed(digits) + "%");
const signedPct = (v) => (v == null ? "-" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(1)}%`);
const signed = (v) => {
  if (v == null) return '<span class="muted">-</span>';
  const cls = v > 0 ? "pos" : v < 0 ? "neg" : "muted";
  return `<span class="${cls}">${v > 0 ? "+" : ""}${won(v)}</span>`;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await res.json().catch(() => ({}));
  if (res.status === 401) {
    location.href = "/login";                    // 세션 만료 → 재로그인
    throw new Error("로그인이 필요합니다.");
  }
  if (res.status === 403 && body.detail === "PASSWORD_CHANGE_REQUIRED") {
    location.href = "/change-password";
    throw new Error("비밀번호 변경이 필요합니다.");
  }
  if (!res.ok) throw new Error(body.detail || `요청 실패 (${res.status})`);
  return body;
}

async function logout() {
  await fetch("/auth/logout", { method: "POST" }).catch(() => {});
  location.href = "/login";
}

// 상단 사용자 영역: 이름 · 역할 · 화면 전환 메뉴 · 로그아웃
function renderUserBox(user, current) {
  const roles = [user.head_of.length ? "부서장" : null, user.is_hr ? "인사팀" : null].filter(Boolean);
  const links = [
    user.head_of.length ? { href: "/", label: "평가 입력" } : null,
    user.is_hr ? { href: "/hr", label: "부서별 현황" } : null,
  ].filter(Boolean);
  $("user-box").innerHTML = `
    ${links.length > 1 ? `<nav class="nav">${links
      .map((l) => `<a href="${l.href}" class="${l.href === current ? "active" : ""}">${l.label}</a>`)
      .join("")}</nav>` : ""}
    <span><strong>${esc(user.name)}</strong> ${roles.map((r) => `<span class="role-tag">${r}</span>`).join(" ")}</span>
    <a href="/change-password">비밀번호 변경</a>
    <button type="button" class="link-btn" id="logout-btn">로그아웃</button>`;
  $("logout-btn").addEventListener("click", logout);
}
