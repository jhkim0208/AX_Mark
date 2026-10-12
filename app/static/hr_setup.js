// 인사팀 '평가 준비': 평가대상자 → 연봉 → 부서장 지정·최종확정
const S = { status: null, up: { employee: null, salary: null }, accounts: [] };
const KIND_LABEL = { employee: "평가대상자", salary: "연봉" };

function setStatus(msg, isError = false) {
  const el = $("status-msg");
  el.textContent = msg;
  el.classList.toggle("error", isError);
}

async function uploadFile(kind, file) {
  const res = await fetch(`/api/hr/setup/upload/${kind}?file_name=${encodeURIComponent(file.name)}`, {
    method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file,
  });
  const body = await res.json().catch(() => ({}));
  if (res.status === 401) { location.href = "/login"; throw new Error("로그인이 필요합니다."); }
  if (!res.ok) throw new Error(body.detail || `업로드 실패 (${res.status})`);
  return body;
}

// ---------------------------------------------------------------------
// 상태 · 단계 표시
// ---------------------------------------------------------------------
function renderStatus() {
  const st = S.status;
  const c = st.counts;
  const banner = $("setup-banner");
  if (st.confirmed) {
    const at = new Date(st.cycle.setup_confirmed_at).toLocaleString("ko-KR");
    banner.className = "banner banner-info";
    banner.innerHTML = `✔ <b>평가화면 최종확정됨</b> (${esc(at)} · ${esc(st.cycle.setup_confirmed_by || "")}) – 부서장이 평가를 입력하는 중입니다.
      업로드·부서장 변경은 아래 3단계에서 <b>확정 해제</b> 후 할 수 있습니다.`;
  } else {
    banner.className = "banner banner-warn";
    banner.innerHTML = `준비 중 – 아직 부서장 화면에서 평가를 입력할 수 없습니다. 1 → 2 → 3단계를 마치면 평가가 시작됩니다.`;
  }
  const chip = (id, done, text) => {
    $(id).className = `chip ${done ? "st-done" : "st-none"}`;
    $(id).textContent = text;
  };
  chip("chip-employee", st.steps.employees, st.steps.employees ? `완료 · ${c.targets}명 / ${c.departments}개 부서` : "미완료");
  chip("chip-salary", st.steps.salaries, st.steps.salaries ? "완료 · 전원 연봉 입력"
    : st.steps.employees ? `연봉 미입력 ${c.salary_missing}명` : "대기");
  chip("chip-confirm", st.confirmed, st.confirmed ? "최종확정됨" : st.steps.ready ? "확정 가능" : "대기");
  for (const kind of ["employee", "salary"]) renderUploadStep(kind);
  renderConfirm();
  renderLogs();
}

function stepLocked(kind) {
  const st = S.status;
  if (st.confirmed) return "최종확정된 상태에서는 업로드할 수 없습니다. 3단계에서 '확정 해제' 후 진행하세요.";
  if (kind === "salary" && !st.steps.employees) return "1단계(평가대상자 업로드)를 먼저 완료하세요.";
  return null;
}

// ---------------------------------------------------------------------
// 업로드 단계 (평가대상자 · 연봉 공통)
// ---------------------------------------------------------------------
function renderUploadStep(kind) {
  const body = document.querySelector(`.step-body[data-kind="${kind}"]`);
  const lock = stepLocked(kind);
  body.closest(".step").classList.toggle("locked", !!lock);
  if (lock) {
    body.innerHTML = `<p class="muted">${esc(lock)}</p>`;
    return;
  }
  const u = S.up[kind];
  const picker = `
    <div class="file-row">
      <label class="file-btn"><input type="file" accept=".xlsx,.csv" data-act="file"> 엑셀 파일 선택 (.xlsx)</label>
      <span class="muted">${u ? `📄 ${esc(u.file_name)} · ${u.row_count}행` : "선택된 파일 없음"}</span>
    </div>`;
  if (!u) { body.innerHTML = picker; return; }

  const headerOpts = (sel) => `<option value="">(선택 안 함)</option>` + u.headers
    .map((h, i) => `<option value="${i}" ${sel === i ? "selected" : ""}>${String.fromCharCode(65 + (i % 26))}${i >= 26 ? Math.floor(i / 26) : ""}열 · ${esc(h)}</option>`).join("");
  const mappingHtml = u.fields.map((f) => `
      <label class="map-item ${f.required && u.mapping[f.key] == null ? "missing" : ""}">
        <span>${esc(f.label)}${f.required ? ' <b class="req">*</b>' : ""}</span>
        <select data-act="map" data-key="${f.key}">${headerOpts(u.mapping[f.key])}</select>
      </label>`).join("");
  const options = kind === "employee" ? `
      <label class="map-item"><span>평가 대상으로 볼 평가유형 값</span>
        <input data-act="opt" data-key="target_value" value="${esc(u.options.target_value)}"></label>
      <label class="map-item"><span>부서장으로 인식할 직책 글자</span>
        <input data-act="opt" data-key="head_keyword" value="${esc(u.options.head_keyword ?? "")}" placeholder="예: 장 (팀장·실장)"></label>`
    : `
      <label class="map-item"><span>연봉 단위</span>
        <select data-act="opt" data-key="unit">
          ${["원", "천원", "만원"].map((x) => `<option ${(u.options.unit || u.plan?.unit) === x ? "selected" : ""}>${x}</option>`).join("")}
        </select></label>`;
  const mapped = new Set(Object.values(u.mapping).filter((v) => v != null));
  const sample = `
      <div class="table-wrap sample-table"><table>
        <thead><tr>${u.headers.map((h, i) => `<th class="${mapped.has(i) ? "mapped" : ""}">${esc(h)}</th>`).join("")}</tr></thead>
        <tbody>${u.sample.map((r) => `<tr>${u.headers.map((_, i) => `<td class="${mapped.has(i) ? "mapped" : ""}">${esc(r[i] ?? "")}</td>`).join("")}</tr>`).join("")}</tbody>
      </table></div>`;

  body.innerHTML = `${picker}
    <div class="map-grid">
      <label class="map-item"><span>시트</span>
        <select data-act="sheet">${u.sheets.map((s) => `<option ${s === u.sheet ? "selected" : ""}>${esc(s)}</option>`).join("")}</select></label>
      <label class="map-item"><span>머리글 행 번호</span>
        <input type="number" min="1" data-act="header" value="${u.header_row}"></label>
      ${options}
    </div>
    <h3 class="sub-title">열 연결 <span class="muted small">자동으로 찾은 열이 맞는지 확인하고, 틀리면 바꿔 주세요. 파란 열이 사용되는 열입니다.</span></h3>
    <div class="map-grid">${mappingHtml}</div>
    <p class="muted small">파일 앞부분 미리보기 (머리글 ${u.header_row}행 아래 5행)</p>
    ${sample}
    <div class="plan" id="plan-${kind}">${planHtml(kind, u)}</div>`;
}

function planHtml(kind, u) {
  if (u.missing_fields?.length) {
    return `<p class="form-error">필수 열을 선택해 주세요: ${u.missing_fields.map(esc).join(", ")}</p>`;
  }
  const p = u.plan;
  if (!p) return '<p class="muted">미리보기 계산 중…</p>';
  const stat = (label, value, cls = "") => `<div class="stat ${cls}"><span>${label}</span><strong>${value}</strong></div>`;
  const errTable = (title, rows, cols) => !rows.length ? "" : `
      <details class="issue"><summary>${title} ${rows.length}건</summary>
        <div class="table-wrap"><table><thead><tr>${cols.map((c) => `<th>${c[0]}</th>`).join("")}</tr></thead>
        <tbody>${rows.map((r) => `<tr>${cols.map((c) => `<td>${esc(r[c[1]] ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
      </details>`;
  if (kind === "employee") {
    const excluded = Object.entries(p.excluded).map(([k, n]) => `${esc(k)} ${n}`).join(", ") || "-";
    return `
      <div class="dialog-stats">
        ${stat("파일 행", `${p.total_rows}행`)}
        ${stat(`평가 대상 (${esc(p.target_value)})`, `${p.targets}명`, "good")}
        ${stat("대상 제외", `${p.excluded_total}명`)}
        ${stat("오류 (반영 안 함)", `${p.errors_count}건`, p.errors_count ? "warn" : "")}
        ${stat("부서", `${p.departments.length}개 (신규 ${p.new_departments})`)}
      </div>
      <p class="muted small">대상 제외 평가유형: ${excluded}</p>
      ${p.dropped_from_targets ? `<p class="warn-text">⚠ 현재 평가 대상 중 <b>${p.dropped_from_targets}명</b>은 이 파일에 '${esc(p.target_value)}'으로 없어 평가 대상에서 제외됩니다 (기록은 보존).</p>` : ""}
      <div class="table-wrap"><table>
        <thead><tr><th>부서</th><th></th><th class="num">대상 인원</th><th class="num">CL2</th><th class="num">CL3</th><th class="num">CL4</th><th>부서장 후보 (직책 기준)</th></tr></thead>
        <tbody>${p.departments.map((d) => `<tr><td><b>${esc(d.name)}</b></td><td>${d.new ? '<span class="role-tag">신규</span>' : ""}</td>
          <td class="num">${d.count}</td><td class="num">${d.levels.CL2}</td><td class="num">${d.levels.CL3}</td><td class="num">${d.levels.CL4}</td>
          <td>${d.heads.length ? esc(d.heads.join(", ")) : '<span class="muted">없음 – 3단계에서 지정</span>'}</td></tr>`).join("")}</tbody>
      </table></div>
      ${errTable("오류 행", p.errors, [["엑셀 행", "row"], ["사번", "emp_no"], ["이름", "name"], ["사유", "reason"]])}
      <div class="apply-row"><button type="button" data-act="apply" ${p.targets ? "" : "disabled"}>평가대상자 ${p.targets}명 반영</button></div>`;
  }
  return `
      <div class="dialog-stats">
        ${stat("파일 행", `${p.total_rows}행`)}
        ${stat("사번 일치", `${p.matched}명 (평가 대상 ${p.matched_targets})`, "good")}
        ${stat("시스템에 없는 사번", `${p.not_found_count}건`, p.not_found_count ? "warn" : "")}
        ${stat("오류", `${p.errors_count}건`, p.errors_count ? "warn" : "")}
        ${stat("반영 후 연봉 없는 대상자", `${p.missing_targets_count}명`, p.missing_targets_count ? "warn" : "good")}
        ${stat("단위", `${esc(p.unit)}${p.unit !== p.guessed_unit ? "" : " (자동)"}`)}
      </div>
      <p class="muted small">변환 예시 (${p.eval_year}년 연봉, 원 단위로 저장): ${p.samples.map((m) => `${esc(m.name)} ${won(m.base_salary)}원`).join(" · ") || "-"}</p>
      ${errTable("시스템에 없는 사번", p.not_found, [["엑셀 행", "row"], ["사번", "emp_no"], ["이름", "name"]])}
      ${errTable("오류 행", p.errors, [["엑셀 행", "row"], ["사번", "emp_no"], ["이름", "name"], ["사유", "reason"]])}
      ${errTable("이름이 다른 사번 (확인 필요)", p.name_mismatches, [["사번", "emp_no"], ["시스템 이름", "name"]])}
      ${errTable("반영 후에도 연봉이 없는 평가 대상자", p.missing_targets, [["부서", "dept"], ["사번", "emp_no"], ["이름", "name"]])}
      <div class="apply-row"><button type="button" data-act="apply" ${p.matched ? "" : "disabled"}>연봉 ${p.matched}명 반영</button></div>`;
}

async function preview(kind) {
  const u = S.up[kind];
  u.plan = null;
  $(`plan-${kind}`) && ($(`plan-${kind}`).innerHTML = planHtml(kind, u));
  const res = await api(`/api/hr/setup/preview/${kind}`, {
    method: "POST",
    body: JSON.stringify({ upload_id: u.upload_id, sheet: u.sheet, header_row: u.header_row,
                           mapping: u.mapping, ...u.options }),
  });
  Object.assign(u, res);
  renderUploadStep(kind);
}

function bindUploadStep(kind) {
  const body = document.querySelector(`.step-body[data-kind="${kind}"]`);
  body.addEventListener("change", async (e) => {
    const el = e.target;
    const u = S.up[kind];
    try {
      if (el.dataset.act === "file" && el.files[0]) {
        setStatus(`${KIND_LABEL[kind]} 파일 읽는 중…`);
        const res = await uploadFile(kind, el.files[0]);
        S.up[kind] = { ...res, options: kind === "employee" ? { target_value: "정상", head_keyword: "장" } : { unit: null } };
        renderUploadStep(kind);
        await preview(kind);
        setStatus("");
      } else if (el.dataset.act === "sheet") {
        Object.assign(u, { sheet: el.value, header_row: null, mapping: null });
        await preview(kind);
      } else if (el.dataset.act === "header") {
        Object.assign(u, { header_row: Number(el.value) || null, mapping: null });
        await preview(kind);
      } else if (el.dataset.act === "map") {
        u.mapping = { ...u.mapping, [el.dataset.key]: el.value === "" ? null : Number(el.value) };
        await preview(kind);
      } else if (el.dataset.act === "opt") {
        u.options = { ...u.options, [el.dataset.key]: el.value };
        await preview(kind);
      }
    } catch (err) {
      setStatus(err.message, true);
      if (el.dataset.act === "file") el.value = "";
    }
  });
  body.addEventListener("click", async (e) => {
    if (e.target.dataset.act !== "apply") return;
    const u = S.up[kind];
    const p = u.plan;
    const msg = kind === "employee"
      ? `평가 대상 ${p.targets}명 · 부서 ${p.departments.length}개를 반영합니다.` +
        (p.dropped_from_targets ? `\n기존 평가 대상 ${p.dropped_from_targets}명은 평가 대상에서 제외됩니다.` : "") +
        (p.errors_count ? `\n오류 ${p.errors_count}건은 반영되지 않습니다.` : "") + "\n진행할까요?"
      : `${p.matched}명의 ${p.eval_year}년 연봉을 입력합니다 (단위: ${p.unit}).` +
        (p.not_found_count ? `\n시스템에 없는 사번 ${p.not_found_count}건은 건너뜁니다.` : "") + "\n진행할까요?";
    if (!confirm(msg)) return;
    try {
      e.target.disabled = true;
      const res = await api(`/api/hr/setup/apply/${kind}`, {
        method: "POST",
        body: JSON.stringify({ upload_id: u.upload_id, sheet: u.sheet, header_row: u.header_row,
                               mapping: u.mapping, ...u.options }),
      });
      S.up[kind] = null;
      S.status = res.status;
      renderStatus();
      const r = res.result;
      setStatus(kind === "employee"
        ? `평가대상자 반영 완료: ${r.targets}명 · 부서 ${r.departments}개 · 부서장 자동 지정 ${r.heads_set}개 부서`
        : `연봉 반영 완료: ${r.updated}명 (연봉 없는 대상자 ${r.missing_targets}명)`);
    } catch (err) {
      setStatus(err.message, true);
      e.target.disabled = false;
    }
  });
}

// ---------------------------------------------------------------------
// 3단계: 부서장 지정 · 최종확정
// ---------------------------------------------------------------------
function renderConfirm() {
  const st = S.status;
  const body = $("confirm-body");
  const step = $("step-confirm");
  if (!st.steps.employees) {
    step.classList.add("locked");
    body.innerHTML = '<p class="muted">1단계(평가대상자 업로드)를 먼저 완료하세요.</p>';
    return;
  }
  step.classList.toggle("locked", !st.steps.salaries && !st.confirmed);
  const locked = st.confirmed;
  const rows = st.departments.map((d) => `
      <tr>
        <td><b>${esc(d.name)}</b></td>
        <td class="num">${d.members}</td>
        <td class="num">${d.salary_missing ? `<span class="warn-text">${d.salary_missing}</span>` : "0"}</td>
        <td>
          <select data-dept="${d.id}" ${locked ? "disabled" : ""} class="${d.head_employee_id ? "" : "missing"}">
            <option value="">(부서장 선택)</option>
            ${d.candidates.map((c) => `<option value="${c.id}" ${c.id === d.head_employee_id ? "selected" : ""}>
              ${esc(c.name)} (${esc(c.emp_no)}) ${esc(c.level || "")}${c.title ? ` · ${esc(c.title)}` : ""}</option>`).join("")}
          </select>
        </td>
      </tr>`).join("");
  const check = (ok, text) => `<li class="${ok ? "ok" : "no"}">${ok ? "✔" : "✕"} ${text}</li>`;
  const c = st.counts;
  body.innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr><th>부서</th><th class="num">평가 대상</th><th class="num">연봉 미입력</th><th>부서장 (평가자)</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    <p class="muted small">부서장은 자기 부서 평가 대상에서 빠지고 평가자가 됩니다. 계정이 없는 부서장에게는 최종확정 시 임시 비밀번호가 발급됩니다.</p>
    <ul class="checklist">
      ${check(st.steps.employees, `평가대상자 ${c.targets}명 · ${c.departments}개 부서`)}
      ${check(st.steps.salaries, c.salary_missing ? `연봉 미입력 ${c.salary_missing}명` : "전원 연봉 입력")}
      ${check(c.heads_missing === 0, c.heads_missing ? `부서장 미지정 ${c.heads_missing}개 부서` : "전 부서 부서장 지정")}
    </ul>
    <div class="apply-row">
      ${st.confirmed
        ? `<button type="button" class="secondary-btn" id="unconfirm-btn">확정 해제 (준비 상태로 되돌리기)</button>`
        : `<button type="button" id="confirm-btn" ${st.steps.ready ? "" : "disabled"}>평가화면 최종확정</button>`}
    </div>`;
}

function bindConfirm() {
  $("confirm-body").addEventListener("change", async (e) => {
    const sel = e.target.closest("select[data-dept]");
    if (!sel) return;
    try {
      S.status = await api(`/api/hr/setup/departments/${sel.dataset.dept}/head`, {
        method: "PUT", body: JSON.stringify({ employee_id: sel.value ? Number(sel.value) : null }),
      });
      renderStatus();
      setStatus("부서장을 변경했습니다.");
    } catch (err) {
      setStatus(err.message, true);
    }
  });
  $("confirm-body").addEventListener("click", async (e) => {
    if (e.target.id === "confirm-btn") {
      const c = S.status.counts;
      if (!confirm(`평가대상자 ${c.targets}명 · ${c.departments}개 부서로 평가화면을 최종확정합니다.\n확정하면 부서장이 평가를 입력할 수 있습니다. 진행할까요?`)) return;
      try {
        const res = await api("/api/hr/setup/confirm", { method: "POST" });
        S.status = res.status;
        renderStatus();
        showAccounts(res.accounts);
        setStatus("평가화면을 최종확정했습니다. 부서장 화면에서 평가 입력이 시작됩니다.");
      } catch (err) {
        setStatus(err.message, true);
      }
    } else if (e.target.id === "unconfirm-btn") {
      const g = S.status.counts.graded;
      if (!confirm(`확정을 해제하면 부서장이 평가를 입력할 수 없게 됩니다.${g ? `\n(이미 입력된 평가 ${g}건은 보존됩니다)` : ""}\n진행할까요?`)) return;
      try {
        S.status = await api("/api/hr/setup/unconfirm", { method: "POST" });
        renderStatus();
        setStatus("확정을 해제했습니다. 업로드·부서장 변경 후 다시 최종확정하세요.");
      } catch (err) {
        setStatus(err.message, true);
      }
    }
  });
}

function showAccounts(accounts) {
  S.accounts = accounts;
  if (!accounts.length) {
    alert("최종확정되었습니다. (모든 부서장에게 이미 계정이 있어 새로 발급된 계정은 없습니다)");
    return;
  }
  $("acc-rows").innerHTML = accounts.map((a) => `<tr><td>${esc(a.department)}</td><td>${esc(a.emp_no)}</td>
    <td>${esc(a.name)}</td><td><code>${esc(a.temp_password)}</code></td></tr>`).join("");
  $("accounts-dialog").showModal();
}

function downloadAccounts() {
  const lines = [["부서", "사번(아이디)", "이름", "임시 비밀번호"],
    ...S.accounts.map((a) => [a.department, a.emp_no, a.name, a.temp_password])]
    .map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(","));
  const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "부서장_임시비밀번호.csv" });
  a.click();
  URL.revokeObjectURL(a.href);
}

function renderLogs() {
  const logs = S.status.logs;
  $("log-rows").innerHTML = logs.length ? logs.map((l) => {
    const s = l.summary;
    const result = l.kind === "EMPLOYEE"
      ? `대상 ${s.targets}명 · 부서 ${s.departments}개 · 제외 ${s.excluded_total}명 · 오류 ${s.errors}건`
      : `연봉 ${s.updated}명 · 없는 사번 ${s.not_found}건 · 오류 ${s.errors}건`;
    return `<tr><td>${esc(new Date(l.applied_at).toLocaleString("ko-KR"))}</td><td>${l.kind === "EMPLOYEE" ? "평가대상자" : "연봉"}</td>
      <td>${esc(l.file_name)}</td><td>${result}</td><td>${esc(l.applied_by)}</td></tr>`;
  }).join("") : '<tr><td colspan="5" class="muted">아직 업로드 이력이 없습니다.</td></tr>';
}

async function init() {
  const me = await api("/api/me");
  renderUserBox(me, "/hr/setup");
  S.status = await api("/api/hr/setup/status");
  $("cycle-label").textContent = `${S.status.cycle.name} · ${S.status.cycle.eval_year + 1}년 처우 반영`;
  renderStatus();
  bindUploadStep("employee");
  bindUploadStep("salary");
  bindConfirm();
  $("acc-close").addEventListener("click", () => {
    if (confirm("창을 닫으면 임시 비밀번호를 다시 볼 수 없습니다. CSV로 내려받으셨나요?")) $("accounts-dialog").close();
  });
  $("acc-download").addEventListener("click", downloadAccounts);
}

init().catch((err) => setStatus(err.message, true));
