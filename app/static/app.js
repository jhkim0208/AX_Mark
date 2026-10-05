const state = { ctx: null, sheet: null, deptId: null, reqId: 0 };

function setStatus(msg, isError = false) {
  const el = $("status-msg");
  el.textContent = msg;
  el.classList.toggle("error", isError);
}

// ---------------------------------------------------------------------
// 렌더링
// ---------------------------------------------------------------------
function gradeSelect(m) {
  const opts = state.ctx.grades
    .map((g) => `<option value="${g.code}" ${g.code === m.grade_code ? "selected" : ""}>${g.code} (${esc(g.name)})</option>`)
    .join("");
  return `<select data-emp="${m.employee_id}" class="${m.grade_code ? "" : "empty"}" ${m.editable ? "" : "disabled"}>
            <option value="">선택</option>${opts}
          </select>`;
}

function rowHtml(m) {
  const sim = m.grade_code != null && m.next_base_salary != null;
  const cap = m.band_capped ? '<span class="badge" title="직급 연봉 밴드 상·하한 적용">밴드</span>' : "";
  const grade = state.ctx.grades.find((g) => g.code === m.grade_code);
  const incentive = !sim ? '<span class="muted">-</span>'
    : grade && !grade.incentive_eligible ? '<span class="muted">미지급</span>'
    : won(m.next_incentive);
  return `
    <td>${esc(m.emp_no)}</td>
    <td>${esc(m.name)}${m.department_name !== state.sheet.department.name ? ` <span class="muted">(${esc(m.department_name)})</span>` : ""}</td>
    <td>${esc(m.job_level)}</td>
    <td class="num">${won(m.cur_base_salary)}</td>
    <td>${gradeSelect(m)}</td>
    <td class="num">${sim ? pct(m.perf_raise_rate) : '<span class="muted">-</span>'}</td>
    <td class="num">${sim ? pct(m.raise_rate) : '<span class="muted">-</span>'}</td>
    <td class="num">${sim ? won(m.next_base_salary) + cap : '<span class="muted">-</span>'}</td>
    <td class="num">${sim ? signed(m.delta_base_salary) : '<span class="muted">-</span>'}</td>
    <td class="num">${incentive}</td>
    <td class="num">${sim ? signed(m.delta_total) : '<span class="muted">-</span>'}</td>`;
}

function renderRows() {
  const tbody = $("rows");
  tbody.innerHTML = "";
  for (const m of state.sheet.members) {
    const tr = document.createElement("tr");
    tr.dataset.emp = m.employee_id;
    tr.innerHTML = rowHtml(m);
    tbody.appendChild(tr);
  }
  if (!state.sheet.members.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="muted">평가 대상자가 없습니다.</td></tr>';
  }
}

function renderSummary() {
  const ms = state.sheet.members;
  const done = ms.filter((m) => m.next_base_salary != null);
  const sum = (arr, k) => arr.reduce((a, m) => a + (m[k] || 0), 0);

  $("t-progress").textContent = `${ms.filter((m) => m.grade_code).length} / ${ms.length}명`;
  $("t-cur").textContent = won(sum(ms, "cur_base_salary"));

  // 예상 합계: 미입력자는 현재 연봉/인센티브를 그대로 가정
  const nextBase = ms.reduce((a, m) => a + (m.next_base_salary ?? m.cur_base_salary ?? 0), 0);
  const nextInc = ms.reduce((a, m) => a + (m.next_incentive ?? m.cur_incentive ?? 0), 0);
  $("t-next").textContent = won(nextBase);
  $("t-next-delta").innerHTML = signed(nextBase - sum(ms, "cur_base_salary"));
  $("t-inc").textContent = won(nextInc);
  $("t-inc-delta").innerHTML = signed(nextInc - sum(ms, "cur_incentive"));

  const curDone = sum(done, "cur_base_salary");
  $("t-rate").textContent = curDone ? pct(sum(done, "next_base_salary") / curDone - 1) : "-";

  // 등급 분포 · 가이드 · 경고 (A 10% 초과, A·B 40% 초과 시 경고 / B 30% 는 안내만)
  const st = state.sheet.stats;
  const alertTypes = new Set(st.alerts.map((a) => a.type));
  const limit = state.ctx.cycle.top_grade_ratio_limit;
  const chip = (label, guide, over) =>
    `<span class="dist-item ${over ? "over" : ""}">${label}${guide}${over ? " · ⚠ 초과" : ""}</span>`;
  const chips = state.ctx.grades.map((g) => {
    const n = st.grade_counts[g.code];
    const guide = g.max_ratio != null ? ` · 가이드 ≤${pct(g.max_ratio, 0)}` : "";
    return chip(`${g.code} ${n}명 (${pct(st.grade_ratios[g.code])})`, guide, alertTypes.has(`GRADE_${g.code}`));
  });
  const topN = state.ctx.grades.filter((g) => g.is_top_grade).reduce((a, g) => a + st.grade_counts[g.code], 0);
  chips.splice(2, 0, `<span class="dist-sep"></span>` +
    chip(`<b>A·B 합계 ${topN}명 (${pct(st.top_rate)})</b>`, limit != null ? ` · 기준 ≤${pct(limit, 0)}` : "",
         alertTypes.has("TOP")) + `<span class="dist-sep"></span>`);
  $("distribution").innerHTML = chips.join("");

  // 부서 상위평가율 → A·B 조정계수
  $("org-top").textContent = pct(st.top_rate);
  $("org-factor").textContent = `A·B 성과인상률 조정계수 ×${Number(st.org_factor).toFixed(2)}`;
  $("alert-banner").hidden = !st.alerts.length;
  $("alert-banner").textContent = st.alerts.length
    ? `⚠ 등급 배분 기준 초과: ${st.alerts.map((a) => a.message).join(" / ")}` : "";

  $("submit-btn").disabled = !state.sheet.can_submit;
}

function renderHeader() {
  $("evaluator").textContent = state.sheet.department.head_name || "미지정";
}

// ---------------------------------------------------------------------
// 이벤트
// ---------------------------------------------------------------------
async function loadSheet() {
  setStatus("");
  state.sheet = await api(`/api/departments/${state.deptId}/sheet`);
  renderHeader();
  renderRows();
  renderSummary();
}

// 등급 하나를 바꾸면 부서 상위평가율이 바뀌어 같은 부서 A·B 인원의 성과인상률도 함께 바뀐다.
// → 저장 후 부서 전체를 다시 받아 값이 바뀐 행만 갱신·강조한다.
const rowKey = (m) => [m.grade_code, m.evaluation_status, m.perf_raise_rate, m.next_base_salary, m.next_incentive].join("|");

async function refreshSheet() {
  const reqId = ++state.reqId;
  const sheet = await api(`/api/departments/${state.deptId}/sheet`);
  if (reqId !== state.reqId) return;                 // 더 최신 요청이 있으면 무시
  const before = new Map(state.sheet.members.map((m) => [m.employee_id, rowKey(m)]));
  state.sheet = sheet;
  for (const m of sheet.members) {
    if (before.get(m.employee_id) === rowKey(m)) continue;
    const tr = document.querySelector(`tr[data-emp="${m.employee_id}"]`);
    if (!tr) continue;
    tr.innerHTML = rowHtml(m);
    tr.classList.add("dirty");
    setTimeout(() => tr.classList.remove("dirty"), 900);
  }
  renderSummary();
}

async function onGradeChange(e) {
  const sel = e.target;
  if (sel.tagName !== "SELECT" || !sel.dataset.emp) return;
  const empId = Number(sel.dataset.emp);
  setStatus("계산 중…");
  try {
    await api(`/api/departments/${state.deptId}/evaluations/${empId}`, {
      method: "PUT",
      body: JSON.stringify({ grade_code: sel.value || null }),
    });
    await refreshSheet();
    setStatus("자동 저장됨 (임시저장)");
  } catch (err) {
    setStatus(err.message, true);
    await loadSheet();
  }
}

async function onSubmit() {
  if (!confirm("평가를 제출하면 더 이상 수정할 수 없습니다. 제출할까요?")) return;
  try {
    const res = await api(`/api/departments/${state.deptId}/submit`, { method: "POST" });
    await loadSheet();
    setStatus(`${res.submitted}명 제출 완료`);
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function init() {
  state.ctx = await api("/api/context");
  const { cycle, policy, departments, user } = state.ctx;
  renderUserBox(user, "/");
  const statusLabel = { DRAFT: "준비중", OPEN: "입력중", CALIBRATION: "조정중", CONFIRMED: "확정", CLOSED: "마감" };
  $("cycle-label").textContent =
    `${cycle.name} · ${statusLabel[cycle.status] || cycle.status} · ${cycle.eval_year + 1}년 처우 반영`;
  $("base-up").textContent = policy ? pct(policy.base_up_rate) : "-";
  $("incentive-grades").textContent =
    state.ctx.grades.filter((g) => g.incentive_eligible).map((g) => g.code).join("·") || "없음";

  const select = $("dept-select");
  // 본인이 부서장인 부서만 내려오므로 상위 부서가 목록에 없을 수 있다
  const depth = (d) => {
    const parent = departments.find((x) => x.id === d.parent_id);
    return parent ? 1 + depth(parent) : 0;
  };
  select.innerHTML = departments
    .map((d) => `<option value="${d.id}">${"  ".repeat(depth(d))}${esc(d.name)}</option>`)
    .join("");
  const saved = Number(new URLSearchParams(location.search).get("dept"));
  state.deptId = departments.some((d) => d.id === saved) ? saved : departments[0]?.id;
  select.value = state.deptId;
  select.addEventListener("change", () => {
    state.deptId = Number(select.value);
    history.replaceState(null, "", `?dept=${state.deptId}`);
    loadSheet().catch((err) => setStatus(err.message, true));
  });

  $("rows").addEventListener("change", onGradeChange);
  bindFormulaDialog(() => state.deptId);
  $("submit-btn").addEventListener("click", onSubmit);
  if (state.deptId) await loadSheet();
}

init().catch((err) => setStatus(err.message, true));
