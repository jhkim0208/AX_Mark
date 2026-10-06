// 부서장 평가 입력 화면
//   등급 변경은 화면에만 반영(서버에서 미리보기 계산)하고, '임시저장'을 눌러야 DB에 저장된다.
//   저장하지 않고 나가면 마지막으로 저장한 상태로 돌아간다.
const state = {
  ctx: null,
  sheet: null,            // 화면에 보이는 시트 (저장본 + 미저장 변경을 반영한 미리보기)
  saved: new Map(),       // employee_id → 마지막으로 저장된 등급
  changes: new Map(),     // employee_id → 저장 전 변경 등급 (null = 등급 삭제)
  selected: new Set(),
  deptId: null,
  reqId: 0,
};

const isDirty = () => state.changes.size > 0;

function setStatus(msg, isError = false) {
  const el = $("status-msg");
  el.textContent = msg;
  el.classList.toggle("error", isError);
}

// ---------------------------------------------------------------------
// 등급 한도 (부서 전체 인원 기준)
//   A 가이드 10% → 32명이면 A 최대 3명, A·B 기준 40% → 최대 12명.
//   입력 도중에도 "앞으로 몇 명까지 줄 수 있는지"가 보이도록 전체 인원을 기준으로 판단한다.
// ---------------------------------------------------------------------
const quota = (ratio, n) => Math.floor(ratio * n + 1e-9);

function gradeLimits() {
  const n = state.sheet.members.length;
  const limits = [];
  for (const g of state.ctx.grades) {
    if (g.alert_on_exceed && g.max_ratio != null) {
      limits.push({ type: `GRADE_${g.code}`, label: g.code, codes: [g.code], ratio: g.max_ratio, max: quota(g.max_ratio, n) });
    }
  }
  const limit = state.ctx.cycle.top_grade_ratio_limit;
  if (limit != null) {
    const codes = state.ctx.grades.filter((g) => g.is_top_grade).map((g) => g.code);
    limits.push({ type: "TOP", label: codes.join("·"), codes, ratio: limit, max: quota(limit, n) });
  }
  return limits;
}

// extra: Map(employee_id → 새 등급 | null). 현재 화면 상태에 extra 를 더한 등급별 인원
function countGrades(extra = new Map()) {
  const counts = Object.fromEntries(state.ctx.grades.map((g) => [g.code, 0]));
  for (const m of state.sheet.members) {
    const code = extra.has(m.employee_id) ? extra.get(m.employee_id) : m.grade_code;
    if (code) counts[code] += 1;
  }
  return counts;
}

const sumCodes = (counts, codes) => codes.reduce((a, c) => a + counts[c], 0);

// 이번 변경으로 한도를 넘는 항목 (이미 넘은 상태에서 더 늘리는 경우 포함, 줄이는 변경은 제외)
function exceededBy(extra) {
  const before = countGrades();
  const after = countGrades(extra);
  return gradeLimits()
    .map((l) => ({ ...l, before: sumCodes(before, l.codes), after: sumCodes(after, l.codes) }))
    .filter((l) => l.after > l.max && l.after > l.before);
}

// 경고창: 확인 시 true, 취소 시 false
function confirmExceeded(items) {
  const dlg = $("warn-dialog");
  const n = state.sheet.members.length;
  $("warn-list").innerHTML = items
    .map((l) => `<li><b>${l.label} ${l.after}명</b> – 허용 ${l.max}명 (부서 ${n}명의 ${pct(l.ratio, 0)}) 을 ${l.after - l.max}명 초과</li>`)
    .join("");
  dlg.showModal();
  return new Promise((resolve) => {
    const done = (ok) => {
      $("warn-ok").onclick = $("warn-cancel").onclick = dlg.oncancel = null;
      dlg.close();
      resolve(ok);
    };
    $("warn-ok").onclick = () => done(true);
    $("warn-cancel").onclick = () => done(false);
    dlg.oncancel = (e) => { e.preventDefault(); done(false); };   // ESC = 취소
    $("warn-cancel").focus();
  });
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
  const checked = state.selected.has(m.employee_id) ? "checked" : "";
  const unsaved = state.changes.has(m.employee_id) ? '<span class="unsaved-tag" title="임시저장 전">미저장</span>' : "";
  return `
    <td>${esc(m.emp_no)}</td>
    <td><label class="check-label">
      <input type="checkbox" class="row-check" data-emp="${m.employee_id}" ${checked} ${m.editable ? "" : "disabled"}
             aria-label="${esc(m.name)} 선택">
      ${esc(m.name)}${m.department_name !== state.sheet.department.name ? ` <span class="muted">(${esc(m.department_name)})</span>` : ""}
    </label></td>
    <td>${esc(m.job_level)}</td>
    <td class="num">${won(m.cur_base_salary)}</td>
    <td>${gradeSelect(m)}${unsaved}</td>
    <td class="num">${sim ? pct(m.perf_raise_rate) : '<span class="muted">-</span>'}</td>
    <td class="num">${sim ? pct(m.raise_rate) : '<span class="muted">-</span>'}</td>
    <td class="num">${sim ? won(m.next_base_salary) + cap : '<span class="muted">-</span>'}</td>
    <td class="num">${sim ? signed(m.delta_base_salary) : '<span class="muted">-</span>'}</td>
    <td class="num">${incentive}</td>
    <td class="num">${sim ? signed(m.delta_total) : '<span class="muted">-</span>'}</td>`;
}

function decorateRow(tr, m) {
  tr.classList.toggle("selected", state.selected.has(m.employee_id));
  tr.classList.toggle("unsaved", state.changes.has(m.employee_id));
}

function renderRows() {
  const tbody = $("rows");
  tbody.innerHTML = "";
  for (const m of state.sheet.members) {
    const tr = document.createElement("tr");
    tr.dataset.emp = m.employee_id;
    tr.innerHTML = rowHtml(m);
    decorateRow(tr, m);
    tbody.appendChild(tr);
  }
  if (!state.sheet.members.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="muted">평가 대상자가 없습니다.</td></tr>';
  }
}

// 등급별 인원 카드 (A·B 합계 포함). 한도 초과 시 경고 표시
function renderDistribution() {
  const counts = countGrades();
  const n = state.sheet.members.length;
  const rated = Object.values(counts).reduce((a, b) => a + b, 0);
  const limits = gradeLimits();
  const over = limits.filter((l) => sumCodes(counts, l.codes) > l.max);
  const share = (c) => (rated ? pct(c / rated) : "-");

  const card = ({ label, swatch = "", count, sub, cls = "" }) => `
    <div class="dist-card ${cls}">
      <div class="dc-head">${swatch}${label}</div>
      <div class="dc-count">${count}<span>명</span></div>
      <div class="dc-sub">${sub}</div>
    </div>`;

  const cards = state.ctx.grades.map((g) => {
    const c = counts[g.code];
    const lim = limits.find((l) => l.type === `GRADE_${g.code}`);
    const isOver = lim && c > lim.max;
    let sub = `입력 대비 ${share(c)}`;
    if (lim) sub = `한도 ${lim.max}명 (${pct(lim.ratio, 0)})${isOver ? " · ⚠ 초과" : ""}`;
    else if (g.max_ratio != null) sub = `가이드 ${quota(g.max_ratio, n)}명 (${pct(g.max_ratio, 0)})`;
    return card({
      label: `${g.code} <span class="muted">${esc(g.name)}</span>`,
      swatch: `<i class="swatch grade-${g.code}"></i>`,
      count: c, sub, cls: isOver ? "over" : "",
    });
  });
  const top = limits.find((l) => l.type === "TOP");
  if (top) {
    const c = sumCodes(counts, top.codes);
    const isOver = c > top.max;
    cards.splice(2, 0, card({
      label: `${top.label} 합계`, count: c,
      sub: `한도 ${top.max}명 (${pct(top.ratio, 0)})${isOver ? " · ⚠ 초과" : ""}`,
      cls: `total ${isOver ? "over" : ""}`,
    }));
  }
  $("distribution").innerHTML = `
    <div class="dist-summary">
      <span class="dist-title">등급별 인원</span>
      <strong>${rated}<span class="muted"> / ${n}명</span></strong>
      <span class="muted">입력</span>
    </div>
    <div class="dist-cards">${cards.join("")}</div>`;

  $("alert-banner").hidden = !over.length;
  $("alert-banner").textContent = over.length
    ? `⚠ 평가 기준율을 초과했습니다: ${over.map((l) => `${l.label} ${sumCodes(counts, l.codes)}명 > 허용 ${l.max}명`).join(" / ")}`
    : "";
}

function renderBulkBar() {
  const n = state.selected.size;
  $("bulk-count").textContent = `선택 ${n}명`;
  $("bulk-apply").disabled = n === 0;
  $("bulk-clear").disabled = n === 0;
  const editable = state.sheet.members.filter((m) => m.editable);
  const all = $("check-all");
  all.disabled = editable.length === 0;
  all.checked = editable.length > 0 && editable.every((m) => state.selected.has(m.employee_id));
  all.indeterminate = n > 0 && !all.checked;
}

function renderSaveBar() {
  const n = state.changes.size;
  $("dirty-note").hidden = n === 0;
  $("dirty-note").textContent = `저장하지 않은 변경 ${n}건`;
  $("save-btn").disabled = n === 0;
  $("save-btn").classList.toggle("attention", n > 0);
  $("discard-btn").disabled = n === 0;
  const editable = state.sheet.members.some((m) => m.editable);
  $("submit-btn").disabled = !editable || !state.sheet.members.every((m) => m.grade_code);
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

  // 부서 상위평가율 → A·B 조정계수 (성과인상률 계산은 입력 인원 기준)
  const st = state.sheet.stats;
  $("org-top").textContent = pct(st.top_rate);
  $("org-factor").textContent = `A·B 성과인상률 조정계수 ×${Number(st.org_factor).toFixed(2)}`;

  renderDistribution();
  renderBulkBar();
  renderSaveBar();
}

function renderHeader() {
  $("evaluator").textContent = state.sheet.department.head_name || "미지정";
}

// ---------------------------------------------------------------------
// 데이터
// ---------------------------------------------------------------------
// 저장된 상태를 다시 읽는다 (미저장 변경은 버림)
async function loadSheet() {
  const reqId = ++state.reqId;
  const sheet = await api(`/api/departments/${state.deptId}/sheet`);
  if (reqId !== state.reqId) return;
  state.sheet = sheet;
  state.saved = new Map(sheet.members.map((m) => [m.employee_id, m.grade_code]));
  state.changes.clear();
  const editable = new Set(sheet.members.filter((m) => m.editable).map((m) => m.employee_id));
  state.selected = new Set([...state.selected].filter((id) => editable.has(id)));
  renderHeader();
  renderRows();
  renderSummary();
}

// 등급 하나를 바꾸면 부서 상위평가율이 바뀌어 같은 부서 A·B 인원의 성과인상률도 함께 바뀐다.
// → 미저장 변경을 반영한 미리보기를 서버에서 계산해 값이 바뀐 행만 갱신·강조한다.
const rowKey = (m) => [m.grade_code, m.evaluation_status, m.perf_raise_rate, m.next_base_salary,
  m.next_incentive, state.changes.has(m.employee_id)].join("|");

async function refreshPreview() {
  const reqId = ++state.reqId;
  const changes = [...state.changes].map(([employee_id, grade_code]) => ({ employee_id, grade_code }));
  const sheet = changes.length
    ? await api(`/api/departments/${state.deptId}/preview`, { method: "POST", body: JSON.stringify({ changes }) })
    : await api(`/api/departments/${state.deptId}/sheet`);
  if (reqId !== state.reqId) return;                 // 더 최신 요청이 있으면 무시
  const before = new Map(state.sheet.members.map((m) => [m.employee_id, rowKey(m)]));
  state.sheet = sheet;
  for (const m of sheet.members) {
    const tr = document.querySelector(`tr[data-emp="${m.employee_id}"]`);
    if (!tr) continue;
    if (before.get(m.employee_id) !== rowKey(m)) {
      tr.innerHTML = rowHtml(m);
      tr.classList.add("dirty");
      setTimeout(() => tr.classList.remove("dirty"), 900);
    }
    decorateRow(tr, m);
  }
  renderSummary();
}

// 변경을 화면 상태에 반영 (저장본과 같아지면 변경 목록에서 제거)
function stage(extra) {
  for (const [id, code] of extra) {
    if ((state.saved.get(id) ?? null) === (code ?? null)) state.changes.delete(id);
    else state.changes.set(id, code ?? null);
  }
}

// ---------------------------------------------------------------------
// 이벤트
// ---------------------------------------------------------------------
async function onGradeChange(e) {
  const sel = e.target;
  if (sel.tagName !== "SELECT" || !sel.dataset.emp) return;
  const empId = Number(sel.dataset.emp);
  const member = state.sheet.members.find((m) => m.employee_id === empId);
  const code = sel.value || null;
  const extra = new Map([[empId, code]]);

  const exceeded = exceededBy(extra);
  if (exceeded.length && !(await confirmExceeded(exceeded))) {
    sel.value = member.grade_code ?? "";                 // 취소 → 원래 등급으로 되돌림
    setStatus("입력을 취소했습니다.");
    return;
  }
  stage(extra);
  setStatus("계산 중…");
  try {
    await refreshPreview();
    setStatus("");
  } catch (err) {
    setStatus(err.message, true);
  }
}

function onCheck(e) {
  const box = e.target;
  const empId = Number(box.dataset.emp);
  if (box.checked) state.selected.add(empId);
  else state.selected.delete(empId);
  box.closest("tr").classList.toggle("selected", box.checked);
  renderBulkBar();
}

function onCheckAll(e) {
  const on = e.target.checked;
  state.selected = new Set(on ? state.sheet.members.filter((m) => m.editable).map((m) => m.employee_id) : []);
  document.querySelectorAll(".row-check:not(:disabled)").forEach((box) => {
    box.checked = on;
    box.closest("tr").classList.toggle("selected", on);
  });
  renderBulkBar();
}

function clearSelection() {
  state.selected.clear();
  document.querySelectorAll(".row-check").forEach((box) => {
    box.checked = false;
    box.closest("tr").classList.remove("selected");
  });
  renderBulkBar();
}

async function onBulkApply() {
  const ids = [...state.selected];
  const code = $("bulk-grade").value || null;
  if (!ids.length) return;
  const label = code ? `${code}등급` : "미입력(등급 삭제)";
  const extra = new Map(ids.map((id) => [id, code]));

  const exceeded = exceededBy(extra);
  if (exceeded.length && !(await confirmExceeded(exceeded))) {
    setStatus("일괄 입력을 취소했습니다.");
    return;
  }
  stage(extra);
  clearSelection();
  setStatus("계산 중…");
  try {
    await refreshPreview();
    setStatus(`${ids.length}명 ${label} 반영 – '임시저장'을 눌러야 저장됩니다.`);
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function saveChanges() {
  const changes = [...state.changes].map(([employee_id, grade_code]) => ({ employee_id, grade_code }));
  const res = await api(`/api/departments/${state.deptId}/save`, {
    method: "POST", body: JSON.stringify({ changes }),
  });
  await loadSheet();
  return res.saved;
}

async function onSave() {
  if (!isDirty()) return;
  setStatus("저장 중…");
  try {
    const n = await saveChanges();
    const time = new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
    setStatus(`임시저장 완료 (${n}건, ${time})`);
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function onDiscard() {
  if (!isDirty()) return;
  if (!confirm(`저장하지 않은 변경 ${state.changes.size}건을 취소하고 마지막 저장 상태로 되돌릴까요?`)) return;
  await loadSheet();
  setStatus("변경을 취소했습니다.");
}

async function onSubmit() {
  const pending = state.changes.size;
  const msg = pending
    ? `저장하지 않은 변경 ${pending}건을 저장한 뒤 제출합니다. 제출하면 더 이상 수정할 수 없습니다. 진행할까요?`
    : "평가를 제출하면 더 이상 수정할 수 없습니다. 제출할까요?";
  if (!confirm(msg)) return;
  try {
    if (pending) await saveChanges();
    const res = await api(`/api/departments/${state.deptId}/submit`, { method: "POST" });
    state.selected.clear();
    await loadSheet();
    setStatus(`${res.submitted}명 제출 완료`);
  } catch (err) {
    setStatus(err.message, true);
  }
}

// 표 머리글을 고정 툴바 바로 아래에 붙이기 위해 툴바 높이를 CSS 변수로 전달
function trackToolbarHeight() {
  const bar = $("grade-toolbar");
  const set = () => document.documentElement.style.setProperty("--toolbar-h", `${bar.offsetHeight}px`);
  new ResizeObserver(set).observe(bar);
  set();
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
  $("bulk-grade").innerHTML = state.ctx.grades
    .map((g) => `<option value="${g.code}">${g.code} (${esc(g.name)})</option>`).join("") +
    '<option value="">미입력 (등급 삭제)</option>';

  const select = $("dept-select");
  // 본인이 부서장인 부서만 내려오므로 상위 부서가 목록에 없을 수 있다
  const depth = (d) => {
    const parent = departments.find((x) => x.id === d.parent_id);
    return parent ? 1 + depth(parent) : 0;
  };
  select.innerHTML = departments
    .map((d) => `<option value="${d.id}">${"  ".repeat(depth(d))}${esc(d.name)}</option>`)
    .join("");
  const saved = Number(new URLSearchParams(location.search).get("dept"));
  state.deptId = departments.some((d) => d.id === saved) ? saved : departments[0]?.id;
  select.value = state.deptId;
  select.addEventListener("change", () => {
    if (isDirty() && !confirm("저장하지 않은 변경이 있습니다. 저장하지 않고 다른 부서로 이동할까요?")) {
      select.value = state.deptId;
      return;
    }
    state.deptId = Number(select.value);
    state.selected.clear();
    history.replaceState(null, "", `?dept=${state.deptId}`);
    loadSheet().catch((err) => setStatus(err.message, true));
  });

  $("rows").addEventListener("change", (e) => {
    if (e.target.classList.contains("row-check")) onCheck(e);
    else onGradeChange(e);
  });
  $("check-all").addEventListener("change", onCheckAll);
  $("bulk-apply").addEventListener("click", onBulkApply);
  $("bulk-clear").addEventListener("click", clearSelection);
  $("save-btn").addEventListener("click", onSave);
  $("discard-btn").addEventListener("click", onDiscard);
  $("submit-btn").addEventListener("click", onSubmit);
  bindFormulaDialog(() => state.deptId);

  // 저장하지 않은 변경이 있으면 페이지 이동·로그아웃 전에 확인
  window.addEventListener("beforeunload", (e) => {
    if (isDirty() && !window.skipLeaveGuard) { e.preventDefault(); e.returnValue = ""; }
  });
  window.confirmLeave = () =>
    !isDirty() || confirm(`저장하지 않은 변경 ${state.changes.size}건이 사라집니다. 그래도 나갈까요?`);

  trackToolbarHeight();
  if (state.deptId) await loadSheet();
}

init().catch((err) => setStatus(err.message, true));
