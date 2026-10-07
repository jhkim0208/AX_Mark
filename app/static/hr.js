const hr = { data: null };

const STATUS = {
  NOT_STARTED: { label: "미입력", cls: "st-none" },
  IN_PROGRESS: { label: "입력중", cls: "st-progress" },
  SUBMITTED: { label: "제출완료", cls: "st-done" },
};
const EVAL_STATUS = { DRAFT: "임시저장", SUBMITTED: "제출", CONFIRMED: "확정" };

// ---------------------------------------------------------------------
// 등급 분포 막대 (A~E, 발산형 색: A·B 파랑 / C 회색 / D·E 빨강)
// ---------------------------------------------------------------------
function distBar(counts, { labels = false } = {}) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  if (!total) return '<div class="dist-bar empty"><span>미입력</span></div>';
  const segs = hr.data.grades
    .filter((g) => counts[g.code] > 0)
    .map((g) => {
      const n = counts[g.code];
      const tip = `${g.code} (${g.name}) ${n}명 · ${pct(n / total)}`;
      const label = labels && n / total >= 0.06 ? `<span>${g.code} ${n}</span>` : "";
      return `<div class="seg grade-${g.code}" style="flex:${n}" data-tip="${esc(tip)}">${label}</div>`;
    })
    .join("");
  return `<div class="dist-bar ${labels ? "large" : ""}">${segs}</div>`;
}

const hasAlert = (d, type) => d.alerts.some((a) => a.type === type);
const alertBadge = (on, title) => (on ? `<span class="badge" title="${esc(title)}">⚠ 초과</span>` : "");

function aRateCell(d) {
  const r = d.grade_ratios.A;
  if (r == null) return '<span class="muted">-</span>';
  const guide = hr.data.grades.find((g) => g.code === "A")?.max_ratio;
  return `<span data-tip="A ${d.grade_counts.A}명${guide != null ? ` · 가이드 ${pct(guide, 0)} 이하` : ""}">${pct(r)}</span>` +
    alertBadge(hasAlert(d, "GRADE_A"), `A 비율 가이드 ${pct(guide, 0)} 초과`);
}

function topRateCell(rate, over = false) {
  const limit = hr.data.top_rate_limit;
  if (rate == null) return '<span class="muted">-</span>';
  return `
    <div class="meter" data-tip="${esc(`상위평가율 ${pct(rate)}${limit != null ? ` · 기준 ${pct(limit, 0)} 이하` : ""}`)}">
      <div class="meter-track">
        <div class="meter-fill ${over ? "over" : ""}" style="width:${Math.min(rate, 1) * 100}%"></div>
        ${limit != null ? `<div class="meter-guide" style="left:${limit * 100}%"></div>` : ""}
      </div>
      <span class="meter-value">${pct(rate)}</span>
      ${alertBadge(over, `상위평가율 기준 ${pct(limit, 0)} 초과`)}
    </div>`;
}

function statusChip(status) {
  const s = STATUS[status] || { label: status, cls: "" };
  return `<span class="chip ${s.cls}">${s.label}</span>`;
}

// ---------------------------------------------------------------------
// 메인 화면
// ---------------------------------------------------------------------
function renderTiles() {
  const { total, departments, top_rate_limit } = hr.data;
  $("t-headcount").textContent = `${total.headcount.toLocaleString()}명`;
  $("t-headcount-sub").textContent = `${departments.length}개 부서`;
  $("t-progress").textContent = pct(total.headcount ? total.rated / total.headcount : null);
  $("t-progress-sub").textContent = `${total.rated} / ${total.headcount}명 입력`;
  const done = departments.filter((d) => d.status === "SUBMITTED").length;
  $("t-submitted").textContent = `${done} / ${departments.length}`;
  $("t-top").textContent = pct(total.top_rate);
  const overDepts = departments.filter((d) => d.alerts.length);
  $("t-top-sub").innerHTML = (top_rate_limit == null ? "" : `기준 ${pct(top_rate_limit, 0)} 이하`) +
    (overDepts.length ? ` · <span class="badge" data-tip="${esc(overDepts.map((d) => d.name).join(", "))}">⚠ 경고 ${overDepts.length}개 부서</span>` : "");
  $("t-perf").textContent = signedPct(total.avg_perf_raise_rate);
  $("t-perf-sub").textContent = total.avg_raise_rate != null ? `총인상률 평균 ${pct(total.avg_raise_rate)}` : "";
}

function renderLegend() {
  $("legend").innerHTML = hr.data.grades
    .map((g) => `<span class="legend-item"><i class="swatch grade-${g.code}"></i>${g.code} ${esc(g.name)}</span>`)
    .join("");
}

function deptRowHtml(d, { total = false } = {}) {
  const cls = total ? "total-row" : d.parent_id ? "child" : "parent";
  const name = total
    ? `<span class="dept-name">${esc(d.name)}</span> <span class="role-tag">전체</span>`
    : `<span class="dept-name">${esc(d.name)}</span>${d.parent_id ? "" : ' <span class="role-tag">본부</span>'}`;
  return `
      <tr class="clickable ${cls}" data-dept="${d.id}" tabindex="0" aria-label="${esc(d.name)} 부서원 현황 열기">
        <td>${name}</td>
        <td>${esc(d.head_name || "-")}</td>
        <td class="num">${d.headcount}</td>
        <td>
          <div class="progress" data-tip="${d.rated}/${d.headcount}명 입력 · ${d.submitted}명 제출">
            <div class="progress-track"><div class="progress-fill" style="width:${d.headcount ? (d.rated / d.headcount) * 100 : 0}%"></div></div>
            <span>${d.rated}/${d.headcount}</span>
          </div>
        </td>
        <td>${distBar(d.grade_counts)}</td>
        <td class="num">${aRateCell(d)}</td>
        <td>${topRateCell(d.top_rate, hasAlert(d, "TOP"))}</td>
        <td class="num">${d.rated && d.org_factor != null ? `×${Number(d.org_factor).toFixed(2)}` : '<span class="muted">-</span>'}</td>
        <td class="num">${signedPct(d.avg_perf_raise_rate)}</td>
        <td>${statusChip(d.status)}</td>
      </tr>`;
}

function renderDepartments() {
  // 맨 위: 모든 조직 합계 ('연구소 전체'), 그 아래 본부 → 팀/Lab
  const total = { ...hr.data.total, id: "all", name: hr.data.total_label, head_name: "-", org_factor: null };
  $("dept-rows").innerHTML = deptRowHtml(total, { total: true }) +
    hr.data.departments.map((d) => deptRowHtml(d)).join("");
}

// ---------------------------------------------------------------------
// 부서원 팝업
// ---------------------------------------------------------------------
async function openDepartment(deptId) {
  const dlg = $("dept-dialog");
  $("dlg-title").textContent = "불러오는 중…";
  $("dlg-sub").textContent = "";
  $("dlg-stats").innerHTML = "";
  $("dlg-dist").innerHTML = "";
  $("dlg-rows").innerHTML = "";
  if (!dlg.open) dlg.showModal();
  try {
    const isAll = deptId === "all";
    const { department, stats, members } = await api(isAll ? "/api/hr/all" : `/api/hr/departments/${deptId}`);
    $("dlg-title").textContent = department.name;
    $("dlg-sub").textContent = isAll ? `전 조직 평가 대상자 ${stats.headcount}명` : `부서장 ${department.head_name || "-"}`;
    const stat = (label, value) => `<div class="stat"><span>${label}</span><strong>${value}</strong></div>`;
    $("dlg-stats").innerHTML = [
      stat("인원", `${stats.headcount}명`),
      stat("입력 / 제출", `${stats.rated} / ${stats.submitted}명`),
      stat("A 비율", pct(stats.grade_ratios.A) + alertBadge(hasAlert(stats, "GRADE_A"), "A 비율 가이드 초과")),
      stat("상위평가율 (A·B)", pct(stats.top_rate) + alertBadge(hasAlert(stats, "TOP"), "상위평가율 기준 초과")),
      stat("A·B 조정계수", stats.rated && stats.org_factor != null ? `×${Number(stats.org_factor).toFixed(2)}` : "-"),
      stat("평균 성과인상률", signedPct(stats.avg_perf_raise_rate)),
      stat("평균 총인상률", stats.avg_raise_rate == null ? "-" : pct(stats.avg_raise_rate)),
      stat("상태", statusChip(stats.status)),
    ].join("");
    $("dlg-dist").innerHTML = distBar(stats.grade_counts, { labels: true });
    $("dlg-rows").innerHTML = members
      .map((m) => {
        const has = m.grade_code != null;
        return `
          <tr>
            <td>${esc(m.emp_no)}</td>
            <td>${esc(m.name)}${m.department_name !== department.name ? ` <span class="muted">(${esc(m.department_name)})</span>` : ""}</td>
            <td>${esc(m.job_level)}</td>
            <td>${has ? `<span class="grade-chip"><i class="swatch grade-${m.grade_code}"></i>${m.grade_code}</span>` : '<span class="muted">미입력</span>'}</td>
            <td>${has ? EVAL_STATUS[m.evaluation_status] || m.evaluation_status : '<span class="muted">-</span>'}</td>
            <td class="num">${has ? signedPct(m.perf_raise_rate) : "-"}</td>
            <td class="num">${has ? pct(m.raise_rate) : "-"}</td>
            <td class="num">${won(m.cur_base_salary)}</td>
            <td class="num">${has ? won(m.next_base_salary) : "-"}</td>
            <td class="num">${has ? signed(m.delta_base_salary) : '<span class="muted">-</span>'}</td>
          </tr>`;
      })
      .join("");
  } catch (err) {
    $("dlg-title").textContent = "불러오지 못했습니다";
    $("dlg-sub").textContent = err.message;
  }
}

// ---------------------------------------------------------------------
// 툴팁 (data-tip 속성)
// ---------------------------------------------------------------------
function bindTooltip() {
  const tip = $("tooltip");
  document.addEventListener("mouseover", (e) => {
    const el = e.target.closest("[data-tip]");
    if (!el) { tip.hidden = true; return; }
    tip.textContent = el.dataset.tip;
    tip.hidden = false;
  });
  document.addEventListener("mousemove", (e) => {
    if (tip.hidden) return;
    const x = Math.min(e.clientX + 12, window.innerWidth - tip.offsetWidth - 8);
    tip.style.left = `${x}px`;
    tip.style.top = `${e.clientY + 16}px`;
  });
}

async function init() {
  hr.data = await api("/api/hr/overview");
  const { cycle, user } = hr.data;
  renderUserBox(user, "/hr");
  $("cycle-label").textContent = `${cycle.name} · ${cycle.eval_year + 1}년 처우 반영`;
  renderTiles();
  renderLegend();
  const aGuide = hr.data.grades.find((g) => g.code === "A" && g.alert_on_exceed)?.max_ratio;
  $("limit-note").textContent = [aGuide != null ? `A ${pct(aGuide, 0)} 초과` : null,
    hr.data.top_rate_limit != null ? `A·B ${pct(hr.data.top_rate_limit, 0)} 초과 시 ⚠ 경고` : null]
    .filter(Boolean).join(" 또는 ");
  renderDepartments();

  const rows = $("dept-rows");
  rows.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-dept]");
    if (tr) openDepartment(tr.dataset.dept === "all" ? "all" : Number(tr.dataset.dept));
  });
  rows.addEventListener("keydown", (e) => {
    const tr = e.target.closest("tr[data-dept]");
    if (tr && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      openDepartment(tr.dataset.dept === "all" ? "all" : Number(tr.dataset.dept));
    }
  });
  const dlg = $("dept-dialog");
  $("dlg-close").addEventListener("click", () => dlg.close());
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });   // 바깥 클릭 시 닫기
  bindTooltip();
  bindFormulaDialog(() => null);
}

init().catch((err) => { $("status-msg").textContent = err.message; });
