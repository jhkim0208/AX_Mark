const state = { ctx: null, sheet: null, deptId: null, seq: {} };

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

  // 등급 분포 + 상위평가율(A·B ÷ 입력 인원, 인사팀 화면과 같은 기준)
  const rated = ms.filter((m) => m.grade_code);
  const share = (n) => (rated.length ? pct(n / rated.length) : "-");
  const chips = state.ctx.grades.map((g) => {
    const n = rated.filter((m) => m.grade_code === g.code).length;
    return `<span class="dist-item">${g.code} ${n}명 (${share(n)})</span>`;
  });
  const limit = state.ctx.cycle.top_grade_ratio_limit;
  const top = rated.filter((m) => m.is_top_grade).length;
  const topRate = rated.length ? top / rated.length : null;
  const over = limit != null && topRate != null && topRate > limit;
  chips.unshift(
    `<span class="dist-item top ${over ? "over" : ""}">상위평가(A·B) ${top}명 · ${pct(topRate)}` +
    `${limit != null ? ` / 기준 ${pct(limit, 0)}` : ""}${over ? " · ⚠ 초과" : ""}</span>`);
  $("distribution").innerHTML = chips.join("");

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

async function onGradeChange(e) {
  const sel = e.target;
  if (sel.tagName !== "SELECT" || !sel.dataset.emp) return;
  const empId = Number(sel.dataset.emp);
  const seq = (state.seq[empId] = (state.seq[empId] || 0) + 1);
  setStatus("계산 중…");
  try {
    const updated = await api(`/api/departments/${state.deptId}/evaluations/${empId}`, {
      method: "PUT",
      body: JSON.stringify({ grade_code: sel.value || null }),
    });
    if (seq !== state.seq[empId]) return; // 더 최신 요청이 있으면 무시
    const idx = state.sheet.members.findIndex((m) => m.employee_id === empId);
    state.sheet.members[idx] = updated;
    const tr = document.querySelector(`tr[data-emp="${empId}"]`);
    tr.innerHTML = rowHtml(updated);
    tr.classList.add("dirty");
    setTimeout(() => tr.classList.remove("dirty"), 600);
    renderSummary();
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
  $("submit-btn").addEventListener("click", onSubmit);
  if (state.deptId) await loadSheet();
}

init().catch((err) => setStatus(err.message, true));
