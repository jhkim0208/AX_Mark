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
  if (window.confirmLeave && !window.confirmLeave()) return;   // 저장하지 않은 변경 확인
  window.skipLeaveGuard = true;
  await fetch("/auth/logout", { method: "POST" }).catch(() => {});
  location.href = "/login";
}

// 상단 사용자 영역: 이름 · 역할 · 화면 전환 메뉴 · 로그아웃
function renderUserBox(user, current) {
  const roles = [user.head_of.length ? "부서장" : null, user.is_hr ? "인사팀" : null].filter(Boolean);
  const links = [
    user.head_of.length ? { href: "/", label: "부서 평가하기" } : null,
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

// ---------------------------------------------------------------------
// 성과인상률 산출공식 팝업
// ---------------------------------------------------------------------
async function openFormula(deptId = null) {
  const dlg = $("formula-dialog");
  $("formula-body").innerHTML = '<p class="muted">불러오는 중…</p>';
  if (!dlg.open) dlg.showModal();
  try {
    const f = await api(deptId == null ? "/api/formula" : `/api/formula?dept_id=${deptId}`);
    const p = f.policy;
    const factor = (v) => `×${Number(v).toFixed(2)}`;
    const ruleOf = (code) => f.rules.find((r) => r.code === code);
    const adjusted = f.rules.filter((r) => r.org_adjusted).map((r) => r.code).join("·");
    const noBase = f.rules.filter((r) => !r.base_up_applies).map((r) => r.code).join("·");
    const cur = f.current;
    const fixedText = (r) => Object.entries(r.fixed_by_level)
      .map(([lv, v]) => `${lv} ${Number(v) === 0 ? "0%(동결)" : signedPct(v)}`).join(", ");
    const fixedRules = f.rules.filter((r) => r.fixed_by_level);
    $("formula-body").innerHTML = `
      <div class="formula-box">
        <p><b>총인상률</b> = 기본인상률 <span class="muted">(${pct(p.base_up_rate)}${noBase ? `, ${noBase} 등급 미적용` : ""})</span> + 성과인상률</p>
        <p><b>성과인상률</b> = 직급별 기준 성과인상률 × 등급계수 × 부서 조정계수<span class="muted">(${adjusted} 등급만)</span></p>
        <p><b>부서 조정계수</b> = 기준 상위평가율 ${pct(p.top_rate_ref, 0)} ÷ 우리 부서 상위평가율
          <span class="muted">(${Number(p.org_factor_min).toFixed(1)} ~ ${Number(p.org_factor_max).toFixed(1)} 범위)</span></p>
      </div>
      <ul class="formula-notes">
        <li><b>상위평가율</b> = ${adjusted} 등급 인원 ÷ 평가 입력 인원. 등급을 입력할 때마다 다시 계산됩니다.</li>
        <li>부서에서 ${adjusted}를 <b>적게 줄수록</b> ${adjusted} 1인당 성과인상률이 <b>높아지고</b>, 많이 줄수록 낮아집니다.
            (범위 안에서는 부서의 ${adjusted} 성과인상 재원이 일정)</li>
        <li>같은 부서 · 같은 직급 · 같은 등급이면 성과인상률이 같습니다. 같은 CL4 A등급이라도 부서의 상위평가율에 따라 달라집니다.</li>
        <li>등급별 차등: A &gt; B &gt; C, D는 성과인상률·기본인상률 모두 0% (동결).</li>
        ${fixedRules.map((r) => `<li><b>${r.code}등급</b>은 공식 대신 직급별 고정 적용 (기본인상률 미적용): ${fixedText(r)}</li>`).join("")}
        <li>성과인상률은 ${(p.perf_rate_unit * 100).toFixed(1)}%p 단위로 반올림합니다.</li>
      </ul>

      <div class="formula-grid">
        <div>
          <h3>직급별 기준 성과인상률</h3>
          <table class="mini">
            <thead><tr><th>직급</th><th class="num">기준률</th></tr></thead>
            <tbody>${f.levels.map((l) => `<tr><td>${esc(l.code)}</td><td class="num">${pct(l.base_perf_rate)}</td></tr>`).join("")}</tbody>
          </table>
        </div>
        <div>
          <h3>등급계수</h3>
          <table class="mini">
            <thead><tr><th>등급</th><th class="num">계수</th><th>조정계수</th><th>기본인상</th></tr></thead>
            <tbody>${f.rules.map((r) => `<tr><td>${r.code} ${esc(r.name)}</td><td class="num">${r.fixed_by_level ? "직급별 고정" : Number(r.perf_factor).toFixed(2)}</td>
              <td>${r.org_adjusted ? "적용" : "-"}</td><td>${r.base_up_applies ? "적용" : "미적용"}</td></tr>`).join("")}</tbody>
          </table>
        </div>
        <div>
          <h3>상위평가율별 조정계수</h3>
          <table class="mini">
            <thead><tr><th>상위평가율</th><th class="num">조정계수</th></tr></thead>
            <tbody>${f.examples.map((e) => `<tr class="${Number(e.top_rate) === Number(p.top_rate_ref) ? "ref" : ""}">
              <td>${pct(e.top_rate, 0)}</td><td class="num">${factor(e.factor)}</td></tr>`).join("")}</tbody>
          </table>
        </div>
      </div>

      ${cur ? `<h3>우리 부서 적용 결과</h3>
      <p class="formula-current">
        상위평가율 <b>${cur.top_rate != null ? pct(cur.top_rate) : "-"}</b>
        <span class="muted">(${adjusted} ${cur.top_count}명 / 입력 ${cur.rated}명)</span>
        → 조정계수 <b>${factor(f.factor)}</b>
      </p>` : `<h3>직급 × 등급 성과인상률 (조정계수 ×1.00 기준)</h3>`}
      <table class="mini matrix">
        <thead><tr><th>직급</th>${f.rules.map((r) => `<th class="num">${r.code}</th>`).join("")}</tr></thead>
        <tbody>${f.matrix.map((row) => `<tr><td>${esc(row.level)}</td>${f.rules.map((r) => {
          const v = row.rates[r.code];
          return `<td class="num"><b>${signedPct(v.perf)}</b><br><span class="muted">총 ${pct(v.total)}</span></td>`;
        }).join("")}</tr>`).join("")}</tbody>
      </table>
      <p class="muted small">표의 굵은 값은 성과인상률, 아래는 기본인상률을 더한 총인상률입니다. (1차 적용 수치)
        예) ${esc(f.levels[0]?.code ?? "")} A등급 = ${pct(f.levels[0]?.base_perf_rate)} × ${Number(ruleOf("A")?.perf_factor ?? 0).toFixed(1)} × ${Number(f.factor).toFixed(2)}</p>`;
  } catch (err) {
    $("formula-body").innerHTML = `<p class="form-error">${esc(err.message)}</p>`;
  }
}

function bindFormulaDialog(getDeptId) {
  $("formula-btn").addEventListener("click", () => openFormula(getDeptId()));
  $("formula-close").addEventListener("click", () => $("formula-dialog").close());
  $("formula-dialog").addEventListener("click", (e) => {
    if (e.target === $("formula-dialog")) $("formula-dialog").close();
  });
}
