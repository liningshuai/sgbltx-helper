"use strict";

const token = document.querySelector('meta[name="dashboard-token"]')?.content || "";
const PENDING_SUBMISSION_KEY = "dashboard.pendingSubmission";
function readPendingSubmission() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(PENDING_SUBMISSION_KEY) || "null");
    if (saved?.payload?.request_id && saved.payload.task_id && saved.payload.profile_id && saved.payload.mode) {
      return { payload: saved.payload, status: saved.payload.instance_id ? "uncertain" : "stale" };
    }
  } catch { /* Storage is advisory; no task is auto-submitted on page load. */ }
  return null;
}
const state = { server: null, runs: [], activeView: "modules", selectedRun: null, pollTimer: null, lastJobId: null, lastJobStatus: null, pendingSubmission: readPendingSubmission() };
const $ = (selector, root = document) => root.querySelector(selector);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const statusNames = {
  completed: "任务返回完成", nothing_to_claim: "无待领取奖励", failed: "运行失败",
  needs_attention: "需要人工确认", cancelled: "已取消", inspection: "只读检查",
  evidence_error: "工件读取异常", incomplete: "记录尚未完成"
};

function showNotice(message, error = false) {
  const box = $("#notice");
  box.hidden = !message;
  box.classList.toggle("is-error", Boolean(error));
  box.textContent = message || "";
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, { cache: "no-store", ...options });
  let payload;
  try { payload = await response.json(); }
  catch { throw new Error(`服务返回了无效响应（HTTP ${response.status}）`); }
  if (!response.ok) {
    const error = new Error(payload?.error?.message || `请求失败（HTTP ${response.status}）`);
    error.status = response.status;
    error.code = payload?.error?.code;
    throw error;
  }
  return payload;
}

async function postJson(path, payload, requestId) {
  const body = JSON.stringify(payload);
  const options = {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Dashboard-Token": token },
    body
  };
  try {
    return await requestJson(path, options);
  } catch (error) {
    // Only retry transport failures for an idempotent request; a job reuses its request_id.
    if (error instanceof TypeError && requestId) return requestJson(path, options);
    throw error;
  }
}

async function loadState() {
  try {
    const previousJob = state.server?.job?.job_id;
    const data = await requestJson("/api/state");
    state.server = data;
    if (state.pendingSubmission && state.pendingSubmission.payload.instance_id !== data.instance_id) {
      state.pendingSubmission.status = "stale";
      savePendingSubmission();
    } else if (state.pendingSubmission && data.job?.request_id === state.pendingSubmission.payload.request_id) {
      clearPendingSubmission();
    } else if (state.pendingSubmission?.status === "accepted") {
      state.pendingSubmission.status = "uncertain";
      savePendingSubmission();
    }
    renderProfileSelect();
    renderModules();
    renderCatalog();
    renderSettings();
    renderJob();
    renderSubmissionRecovery();
    $("#device-value").textContent = selectedProfile()?.device_serial || "尚未检查";
    $("#last-updated").textContent = `状态更新于 ${new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
    if (data.job?.job_id && data.job.status === "finished" && (previousJob !== data.job.job_id || state.lastJobStatus !== data.job.status)) {
      loadRuns().catch(() => {});
    }
    state.lastJobId = data.job?.job_id || null;
    state.lastJobStatus = data.job?.status || null;
    if (!state.serverError) showNotice("");
    state.serverError = false;
  } catch (error) {
    state.serverError = true;
    showNotice(`服务暂时未更新：${error.message}。保留上次显示的数据。`, true);
  }
}

function selectedProfile() {
  return state.server?.profiles?.find(profile => profile.profile_id === state.server.selected_profile_id) || null;
}

function renderProfileSelect() {
  const select = $("#profile-select");
  const profiles = state.server?.profiles || [];
  const selected = state.server?.selected_profile_id;
  select.replaceChildren();
  for (const profile of profiles) {
    const option = el("option", "", `${profile.label} · ${profile.account_alias || "配置不可用"}${profile.available ? "" : "（不可用）"}`);
    option.value = profile.profile_id;
    option.disabled = !profile.available;
    option.selected = profile.profile_id === selected;
    select.append(option);
  }
}

function moduleCard(module, busy) {
  const card = el("article", "module-card");
  const top = el("div", "module-top");
  const titleWrap = el("div", "module-title-wrap");
  titleWrap.append(el("span", "module-icon", module.icon || (module.task_id === "mail_bulk_claim" ? "✉" : "◇")));
  const title = el("div");
  title.append(el("h3", "", module.name), el("p", "module-subtitle", module.description));
  titleWrap.append(title);
  const label = el("label", "switch-label");
  const input = el("input");
  input.type = "checkbox";
  input.checked = module.enabled;
  input.setAttribute("role", "switch");
  input.setAttribute("aria-label", `启用${module.name}`);
  input.addEventListener("change", () => toggleModule(module.task_id, input, module.enabled));
  label.append(input, el("span", "switch-track"), el("span", "", module.enabled ? "已开启" : "已关闭"));
  top.append(titleWrap, label);
  card.append(top);
  card.append(el("div", "module-status", module.acceptance));
  card.append(el("p", "module-route", module.route || ""));
  const actions = el("div", "module-actions");
  const profile = selectedProfile();
  const canRun = module.enabled && !busy && Boolean(profile?.available);
  const check = el("button", "button button-secondary", "只读检查");
  check.type = "button";
  check.disabled = !canRun;
  check.title = profile?.available ? "只分析当前画面，不自动导航" : "请选择可用的已登记档案";
  check.addEventListener("click", () => startTask(module.task_id, "dry-run"));
  actions.append(check);
  const run = el("button", "button button-primary", module.run_label || (module.task_id === "mail_bulk_claim" ? "测试运行" : "运行单封领取"));
  run.type = "button";
  run.disabled = !canRun;
  run.title = module.run_title || (module.task_id === "mail_bulk_claim" ? "批量有效奖励仍待实机验收" : "仅运行已登记的单封领取任务");
  run.addEventListener("click", () => startTask(module.task_id, "execute"));
  actions.append(run);
  card.append(actions);
  for (const note of module.behavior_notes || []) card.append(el("p", "module-route", note));
  return card;
}

function renderModules() {
  const container = $("#module-list");
  container.replaceChildren();
  if (!state.server) return;
  const busy = Boolean(state.server.active_job || state.pendingSubmission);
  for (const module of Object.values(state.server.modules)) container.append(moduleCard(module, busy));
}

function statusText(status) { return statusNames[status] || status || "状态未知"; }

const catalogStatus = {
  accepted: { label: "已验收", className: "is-accepted" },
  in_progress: { label: "开发中", className: "is-progress" },
  recommended: { label: "推荐下一步", className: "is-recommended" },
  planned: { label: "未开始", className: "is-planned" },
};

function renderCatalog() {
  const container = $("#catalog-groups");
  const summary = $("#catalog-summary");
  container.replaceChildren();
  summary.replaceChildren();
  const catalog = state.server?.catalog;
  if (!Array.isArray(catalog)) {
    container.append(el("p", "catalog-empty", "功能目录暂未读取；请刷新页面或重启本地服务。"));
    return;
  }
  const query = ($("#catalog-search").value || "").trim();
  const counts = { accepted: 0, in_progress: 0, recommended: 0, planned: 0 };
  let total = 0;
  for (const group of catalog) {
    for (const feature of group.features || []) { total += 1; counts[feature.status] = (counts[feature.status] || 0) + 1; }
  }
  for (const [status, meta] of Object.entries(catalogStatus)) {
    const pill = el("span", "catalog-count " + meta.className);
    pill.append(el("span", "catalog-dot"), el("span", "", meta.label + " " + (counts[status] || 0)));
    summary.append(pill);
  }
  summary.append(el("span", "catalog-total", "共 " + total + " 项"));
  for (const group of catalog) {
    const features = (group.features || []).filter(feature => !query || feature.name.includes(query) || (feature.note || "").includes(query));
    if (query && !features.length) continue;
    const card = el("section", "catalog-group");
    const head = el("div", "catalog-group-head");
    head.append(el("h3", "", group.title));
    if (group.subtitle) head.append(el("span", "catalog-group-sub", group.subtitle));
    const accepted = (group.features || []).filter(feature => feature.status === "accepted").length;
    head.append(el("span", "catalog-group-count", accepted + "/" + (group.features || []).length + " 已验收"));
    card.append(head);
    const grid = el("div", "feature-grid");
    for (const feature of features) {
      const meta = catalogStatus[feature.status] || catalogStatus.planned;
      const chip = el("div", "feature-chip " + meta.className);
      chip.setAttribute("title", feature.note || meta.label);
      chip.append(el("span", "feature-name", feature.name), el("span", "feature-status", meta.label));
      if (feature.note && feature.status !== "planned") chip.append(el("span", "feature-note", feature.note));
      grid.append(chip);
    }
    card.append(grid);
    container.append(card);
  }
  if (query && !container.children.length) container.append(el("p", "catalog-empty", "没有匹配“" + query + "”的功能。"));
}

function appendResultFacts(body, result) {
  const detail = result.detail || {};
  const counts = el("div", "job-counts");
  const classifiedKeys = ["navigation_action_count", "claim_action_count", "dismiss_action_count", "scroll_action_count"];
  if (classifiedKeys.some(key => Object.hasOwn(detail, key))) {
    const labels = { navigation_action_count: "导航记录", claim_action_count: "领取记录", dismiss_action_count: "关闭记录", scroll_action_count: "滚动记录" };
    for (const key of classifiedKeys) counts.append(el("span", "count-pill", `${labels[key]} ${Number.isInteger(detail[key]) ? detail[key] : "未记录"}`));
  }
  const count = Number.isInteger(result.action_count) ? result.action_count : "未记录";
  const inputText = detail.action_count_is_upper_bound === true
    ? `输入计数上界 ${count}（实际输入未确认）`
    : detail.input_outcome_uncertain === true ? `实际输入未确认（计数 ${count}）` : `审计输入 ${count}`;
  counts.append(el("span", "count-pill", inputText));
  body.append(counts);
  const confirmed = value => value === true ? "已确认" : value === false ? "未确认" : "未记录";
  if (detail.cleanup_only === true) body.append(el("div", "", "本轮仅收尾，未重新领取；不改变先前领取结果。"));
  if (Object.hasOwn(detail, "claim_verified")) body.append(el("div", "", `领取确认：${confirmed(detail.claim_verified)}`));
  if (Object.hasOwn(detail, "post_batch_state_verified")) body.append(el("div", "", `批后任务状态：${confirmed(detail.post_batch_state_verified)}`));
  if (result.task_id === "daily_rewards_claim" && Array.isArray(detail.batch_rounds)) {
    const submitted = detail.batch_rounds.length;
    const confirmedRounds = Number.isInteger(detail.confirmed_batch_round_count)
      ? detail.confirmed_batch_round_count : detail.batch_rounds.filter(round => round?.submission === "confirmed"
        && round?.reward_popup_seen === true && round?.reward_popup_closed === true
        && ["green", "gray"].includes(round?.post_batch_state)).length;
    const uncertainRounds = Number.isInteger(detail.uncertain_batch_round_count)
      ? detail.uncertain_batch_round_count : Math.max(0, submitted - confirmedRounds);
    body.append(el("div", "", `一键领取轮次：${submitted}；流程已确认 ${confirmedRounds}；不确定 ${uncertainRounds}`));
  }
  if (Object.hasOwn(detail, "cleanup_verified")) {
    const finalPage = typeof detail.final_page_id === "string" ? detail.final_page_id : "未记录";
    body.append(el("div", "", `返回主城：${confirmed(detail.cleanup_verified)}；最终页面 ${finalPage}`));
  }
  if (Object.hasOwn(detail, "announcement_dismiss_attempted")) {
    const notice = detail.announcement_dismissed === true ? "已跳过并重新识别"
      : detail.announcement_dismiss_attempted === true ? "已尝试，尚未确认消失"
        : detail.announcement_dismiss_attempted === false ? "本轮未尝试" : "未记录";
    body.append(el("div", "", `公告处理：${notice}`));
  }
  if (detail.coverage && typeof detail.coverage === "object") {
    const scanned = Number.isInteger(detail.coverage.scanned_pages) ? detail.coverage.scanned_pages : "未记录";
    body.append(el("div", "", `日常任务扫描：${scanned} 页；覆盖${detail.coverage.complete === true ? "已完整确认" : "不完整"}`));
  }
}

function renderJob() {
  const panel = $("#job-panel");
  const job = state.server?.job;
  if (!job) { panel.hidden = true; panel.replaceChildren(); return; }
  panel.hidden = false;
  panel.replaceChildren();
  const header = el("div", "job-header");
  const title = el("h3", "", job.mode === "dry-run" ? "只读检查记录" : "手动任务记录");
  header.append(title, el("span", "job-stage", job.stage || statusText(job.status)));
  panel.append(header);
  const body = el("div", "job-body");
  body.append(el("div", "", `任务：${job.task_id} · 状态：${statusText(job.status)}`));
  if (job.error) body.append(el("div", "", `错误：${job.error}`));
  const result = job.result;
  if (result) {
    const isInspection = result.result_type === "inspection" || result.status === "inspection";
    const explanation = isInspection
      ? `只读检查完成：页面 ${result.page_id || "未识别"}；未触发领取。`
      : result.status === "nothing_to_claim" ? "当前任务未找到可领目标；这不代表奖励领取成功。"
        : `运行器结果：${statusText(result.status)}${result.reason_code ? `（${result.reason_code}）` : ""}`;
    body.append(el("div", "", explanation));
    appendResultFacts(body, result);
  } else if (state.server.active_job) {
    const progress = job.progress || {};
    const counts = el("div", "job-counts");
    const total = Number.isInteger(progress.action_count) ? `已记录输入 ${progress.action_count}` : "输入 未记录";
    const upper = Number.isInteger(progress.action_upper_bound) ? ` / 上限 ${progress.action_upper_bound}` : " / 上限 未记录";
    counts.append(el("span", "count-pill", total + upper));
    if (job.task_id === "daily_rewards_claim") {
      const value = item => Number.isInteger(progress[item]) ? progress[item] : "未记录";
      counts.append(el("span", "count-pill", `批领轮次 ${value("batch_round_count")} · 已确认 ${value("confirmed_batch_round_count")} · 不确定 ${value("uncertain_batch_round_count")} · 进行中 ${value("pending_batch_round_count")}`));
      counts.append(el("span", "count-pill", `上限 ${state.server.modules?.daily_rewards_claim?.max_batch_rounds || 5} 轮；识别顶部灰态后停止`));
    }
    const labelByKey = { navigation_action_count: "导航", claim_action_count: "领取", dismiss_action_count: "关闭", scroll_action_count: "滚动" };
    for (const key of Object.keys(labelByKey)) {
      const count = Number.isInteger(progress[key]) ? progress[key] : "未记录";
      counts.append(el("span", "count-pill", `${labelByKey[key]} ${count}`));
    }
    body.append(counts);
  }
  panel.append(body);
  if (state.server.active_job) {
    const actions = el("div", "job-actions");
    const stop = el("button", "button button-stop", job.status === "stop_requested" ? "停止请求已发送…" : "停止当前任务");
    stop.type = "button";
    stop.disabled = job.status === "stop_requested";
    stop.addEventListener("click", () => stopJob(job.job_id));
    actions.append(stop);
    panel.append(actions);
  }
}

function savePendingSubmission() {
  if (!state.pendingSubmission) return;
  try { sessionStorage.setItem(PENDING_SUBMISSION_KEY, JSON.stringify({ payload: state.pendingSubmission.payload })); }
  catch { /* The in-memory request id remains available until the page closes. */ }
}

function clearPendingSubmission() {
  state.pendingSubmission = null;
  try { sessionStorage.removeItem(PENDING_SUBMISSION_KEY); } catch { /* Ignore unavailable storage. */ }
}

function renderSubmissionRecovery() {
  const panel = $("#submission-recovery");
  const button = $("#retry-submission");
  const abandon = $("#abandon-submission");
  const message = $("#submission-recovery-message");
  if (!panel || !button) return;
  const pending = state.pendingSubmission;
  panel.hidden = !pending || pending.status === "submitting";
  button.disabled = pending?.status === "submitting" || pending?.status === "stale";
  if (abandon) abandon.disabled = pending?.status === "submitting";
  if (message) message.textContent = pending?.status === "stale"
    ? "此恢复记录属于旧服务实例，原作业结果无法确认；系统不会重放。放弃只清除本页恢复记录，不表示旧动作已撤销或未发生。服务刚重启时，请刷新页面后再显式启动新作业。"
    : "请求结果尚未确认。恢复时会使用原服务实例与 request ID；此操作不会创建新的作业编号。";
  if (pending) panel.setAttribute("data-request-id", pending.payload.request_id);
}

function abandonPendingSubmission() {
  if (!state.pendingSubmission || state.pendingSubmission.status === "submitting") return;
  clearPendingSubmission();
  renderSubmissionRecovery();
  renderModules();
  showNotice("已放弃浏览器中的请求恢复记录；这只清除界面记录，不会撤销或确认旧动作。服务刚重启时，请刷新页面后再单独显式启动新作业。", true);
}

async function toggleModule(taskId, input, oldValue) {
  input.disabled = true;
  try {
    const response = await postJson("/api/settings", { task_id: taskId, enabled: input.checked });
    state.server.modules[taskId].enabled = response.modules[taskId];
    showNotice(`${state.server.modules[taskId].name}已${input.checked ? "开启" : "关闭"}；未启动任务。`);
  } catch (error) {
    input.checked = oldValue;
    showNotice(`设置未保存：${error.message}`, true);
  } finally {
    input.disabled = false;
    renderModules();
  }
}

async function startTask(taskId, mode) {
  if (state.pendingSubmission || state.server?.active_job) return;
  if (typeof state.server?.instance_id !== "string" || !state.server.instance_id) {
    showNotice("当前服务实例尚未确认，未提交任务。", true);
    return;
  }
  const profile = selectedProfile();
  if (!profile?.available) { showNotice("所选档案不可用；未连接设备或启动任务。", true); return; }
  const requestId = crypto.randomUUID();
  const payload = { request_id: requestId, task_id: taskId, profile_id: profile.profile_id, mode, instance_id: state.server.instance_id };
  // Lock the UI synchronously, before the first await or network request.
  state.pendingSubmission = { payload, status: "submitting" };
  savePendingSubmission();
  renderModules();
  renderSubmissionRecovery();
  showNotice(mode === "dry-run" ? "正在提交只读检查请求；不会自动导航。" : "正在提交你手动启动的任务。", false);
  await submitPendingSubmission();
}

async function retryPendingSubmission() {
  const pending = state.pendingSubmission;
  if (!pending || pending.status === "submitting" || pending.status === "stale") return;
  pending.status = "submitting";
  savePendingSubmission();
  renderModules();
  renderSubmissionRecovery();
  showNotice("正在用原请求 ID 恢复提交状态；不会创建新任务编号。", false);
  await submitPendingSubmission();
}

async function submitPendingSubmission() {
  const pending = state.pendingSubmission;
  if (!pending) return;
  try {
    await postJson("/api/jobs", pending.payload, pending.payload.request_id);
    if (state.pendingSubmission !== pending) return;
    pending.status = "accepted";
    savePendingSubmission();
    renderSubmissionRecovery();
    await loadState();
    if (state.pendingSubmission === pending) {
      pending.status = "uncertain";
      savePendingSubmission();
      renderSubmissionRecovery();
      renderModules();
      showNotice("服务已回应，但暂未确认对应作业。请恢复同一请求；不要重新启动新请求。", true);
    }
  } catch (error) {
    if (state.pendingSubmission !== pending) return;
    if (error.code === "stale_instance") {
      pending.status = "stale";
      savePendingSubmission();
      renderSubmissionRecovery();
      renderModules();
      showNotice("恢复请求属于旧服务实例，原作业结果无法确认；没有重放任务。可放弃此浏览器恢复记录后，再单独显式启动新任务。", true);
      return;
    }
    if (Number.isInteger(error.status) && error.status >= 400 && error.status < 500) {
      clearPendingSubmission();
      renderSubmissionRecovery();
      renderModules();
      showNotice(`任务请求被服务拒绝，未启动：${error.message}`, true);
      return;
    }
    pending.status = "uncertain";
    savePendingSubmission();
    renderSubmissionRecovery();
    renderModules();
    showNotice(`提交结果未确定：${error.message}。恢复时会沿用原请求 ID，不会自动另建任务。`, true);
  }
}

async function stopJob(jobId) {
  try {
    await postJson(`/api/jobs/${encodeURIComponent(jobId)}/stop`, {});
    showNotice("停止请求已发送；等待运行器确认后才显示终态。");
    await loadState();
  } catch (error) { showNotice(`停止请求未确认：${error.message}`, true); }
}

function renderRuns() {
  const list = $("#runs-list");
  list.replaceChildren();
  if (!state.runs.length) { list.append(el("div", "empty-state", "暂无运行记录。网页启动不会自动连接设备或创建记录。")); return; }
  for (const run of state.runs) {
    const row = el("div", "run-row");
    const open = el("button");
    open.type = "button";
    open.append(el("span", "run-id", run.run_id), el("span", "run-meta", `${run.task_id || "任务未知"} · ${run.started_at || "时间未知"} · 输入 ${run.action_count ?? "—"}`));
    open.addEventListener("click", () => openRun(run.run_id));
    row.append(open, el("span", "run-badge", statusText(run.status)));
    list.append(row);
  }
}

async function loadRuns() {
  try {
    const result = await requestJson("/api/runs?limit=100");
    state.runs = result.runs || [];
    renderRuns();
    if (state.selectedRun) await openRun(state.selectedRun, false);
  } catch (error) { showNotice(`历史记录暂不可用：${error.message}`, true); }
}

async function openRun(runId, announce = true) {
  state.selectedRun = runId;
  const panel = $("#run-detail");
  panel.hidden = false;
  panel.replaceChildren(el("div", "empty-state", "正在读取运行证据…"));
  try {
    const data = await requestJson(`/api/runs/${encodeURIComponent(runId)}`);
    panel.replaceChildren();
    const header = el("div", "detail-head");
    header.append(el("h3", "", runId));
    const close = el("button", "detail-close", "×");
    close.type = "button";
    close.setAttribute("aria-label", "关闭运行记录详情");
    close.addEventListener("click", () => { panel.hidden = true; state.selectedRun = null; });
    header.append(close);
    panel.append(header);
    const result = data.result || data.inspection;
    panel.append(el("p", "detail-summary", result
      ? `状态：${statusText(result.status || "inspection")} · 任务：${result.task_id || "未知"} · 输入：${result.action_count ?? 0}${result.page_id ? ` · 页面：${result.page_id}` : ""}`
      : (data.evidence_errors || []).join("；") || "没有完整结果文件。"));
    if (result) appendResultFacts(panel, result);
    if (data.events?.length) {
      const events = el("div", "event-list");
      events.setAttribute("aria-label", "实际运行事件");
      for (const event of data.events) {
        const detail = event.detail && typeof event.detail === "object" ? event.detail : {};
        const detailKeys = ["reason", "reason_code", "message", "page_id", "template", "score", "status", "state", "count", "action_count", "duration_ms"];
        const detailText = detailKeys.filter(key => Object.hasOwn(detail, key)).map(key => `${key}=${String(detail[key])}`).join(" ");
        events.append(el("div", "", `${event.at || event.timestamp || ""} ${event.node || ""} ${event.phase || event.stage || ""} ${event.event || event.kind || ""} ${detailText}`));
      }
      panel.append(events);
    }
    if (data.images?.length) {
      const images = el("div", "image-grid");
      for (const name of data.images) {
        const figure = el("figure");
        const image = el("img", "evidence-image");
        image.src = `/api/runs/${encodeURIComponent(runId)}/images/${encodeURIComponent(name)}`;
        image.alt = `${runId} ${name}`;
        image.loading = "lazy";
        figure.append(image, el("figcaption", "image-caption", name));
        images.append(figure);
      }
      panel.append(images);
    }
    if (announce) showNotice(`已读取运行记录 ${runId}。`);
  } catch (error) { panel.replaceChildren(el("div", "empty-state", `读取失败：${error.message}`)); }
}

function renderSettings() {
  const container = $("#settings-content");
  container.replaceChildren();
  if (!state.server) return;
  for (const profile of state.server.profiles || []) {
    const card = el("article", "setting-card");
    card.append(el("h3", "", `${profile.label} · ${profile.profile_id}`));
    card.append(el("p", "", `配置状态：${profile.available ? "可读取" : profile.availability_reason || "不可用"}`));
    card.append(el("p", "", `匿名别名：${profile.account_alias || "未配置"}（不认证游戏角色）`));
    card.append(el("p", "", `设备：${profile.device_serial || "未配置"} · ${profile.package_name || "未配置"}`));
    card.append(el("p", "", `预算：超时 ${profile.timeout_seconds ?? "—"} 秒 · 输入上限 ${profile.max_actions ?? "—"}（只读）`));
    container.append(card);
  }
}

function switchView(view) {
  state.activeView = view;
  for (const button of document.querySelectorAll(".nav-item")) button.classList.toggle("is-active", button.dataset.view === view);
  for (const id of ["catalog", "modules", "runs", "settings"]) $(`#${id}-view`).hidden = id !== view;
  $("#page-title").textContent = ({ catalog: "功能总览", modules: "功能模块", runs: "运行记录", settings: "设置" })[view];
  if (view === "runs") loadRuns();
}

async function chooseProfile() {
  const select = $("#profile-select");
  const prior = state.server.selected_profile_id;
  select.disabled = true;
  try {
    await postJson("/api/settings", { profile_id: select.value });
    state.server.selected_profile_id = select.value;
    await loadState();
    showNotice("已保存本机档案选择；没有连接设备。");
  } catch (error) {
    select.value = prior;
    showNotice(`档案选择未保存：${error.message}`, true);
  } finally { select.disabled = false; }
}

function startPolling() {
  clearTimeout(state.pollTimer);
  const delay = document.hidden ? 5000 : 1000;
  state.pollTimer = setTimeout(async () => {
    await loadState();
    startPolling();
  }, delay);
}

document.addEventListener("DOMContentLoaded", async () => {
  for (const button of document.querySelectorAll(".nav-item")) button.addEventListener("click", () => switchView(button.dataset.view));
  $("#profile-select").addEventListener("change", chooseProfile);
  $("#refresh-runs").addEventListener("click", loadRuns);
  $("#catalog-search").addEventListener("input", renderCatalog);
  $("#retry-submission").addEventListener("click", retryPendingSubmission);
  $("#abandon-submission").addEventListener("click", abandonPendingSubmission);
  renderSubmissionRecovery();
  document.addEventListener("visibilitychange", startPolling);
  await loadState();
  await loadRuns();
  startPolling();
});
