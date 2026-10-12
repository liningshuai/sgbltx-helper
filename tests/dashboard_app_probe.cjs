"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class FakeElement {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.handlers = {};
    this.attributes = {};
    this.className = "";
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.value = "";
    this.childElementCount = 0;
    this.classList = { toggle() {}, add() {}, remove() {} };
  }
  append(...nodes) { this.children.push(...nodes); this.childElementCount = this.children.length; }
  replaceChildren(...nodes) { this.children = [...nodes]; this.childElementCount = this.children.length; }
  addEventListener(name, fn) { this.handlers[name] = fn; }
  setAttribute(name, value) { this.attributes[name] = value; }
  setAttributeValue(name, value) { this.attributes[name] = value; }
  get innerText() { return [this.textContent, ...this.children.map(child => child.innerText || child.textContent || "")].join(" "); }
}

const elements = new Map();
for (const id of ["notice", "module-list", "profile-select", "settings-content", "job-panel", "run-detail", "device-value", "last-updated", "retry-submission", "abandon-submission", "submission-recovery", "submission-recovery-message"]) {
  elements.set(`#${id}`, new FakeElement(id === "profile-select" ? "select" : "div"));
}
elements.get("#profile-select").value = "local-test-mail";
const meta = { content: "test-token" };
const document = {
  hidden: false,
  querySelector(selector) { return selector.startsWith("meta[") ? meta : elements.get(selector) || new FakeElement(); },
  querySelectorAll() { return []; },
  createElement(tag) { return new FakeElement(tag); },
  addEventListener() {},
};

const sessionValues = new Map();
const sessionStorage = {
  getItem(key) { return sessionValues.has(key) ? sessionValues.get(key) : null; },
  setItem(key, value) { sessionValues.set(key, String(value)); },
  removeItem(key) { sessionValues.delete(key); },
};
let uuid = 0;
const requests = [];
const context = vm.createContext({
  document,
  fetch(path, options) { return new Promise((resolve, reject) => requests.push({ path, options, resolve, reject })); },
  crypto: { randomUUID: () => `request-${++uuid}` },
  sessionStorage,
  setTimeout,
  clearTimeout,
  Date,
  JSON,
  TypeError,
  console,
});
const appSource = fs.readFileSync(process.argv[2], "utf8");
vm.runInContext(appSource, context, { filename: "app.js" });
vm.runInContext(`state.server = {
  modules: {
    mail_bulk_claim: { task_id: "mail_bulk_claim", name: "系统邮件", description: "mail", acceptance: "待验收", enabled: true },
    claim_primary: { task_id: "claim_primary", name: "单封", description: "claim", acceptance: "已验收", enabled: true },
    daily_rewards_claim: { task_id: "daily_rewards_claim", name: "日常任务奖励", description: "沿原生一键领取循环到灰态", acceptance: "partial", enabled: false, icon: "☆", route: "主城 → 任务 → 日常任务 → 一键领取循环（最多 5 轮）→ 灰态后返回主城", run_label: "运行一键领取", run_title: "每轮按新鲜绿态提交", max_batch_rounds: 5 }
  },
  profiles: [{ profile_id: "local-test-mail", label: "测试档案", available: true, device_serial: "emulator-5554", timeout_seconds: 120, max_actions: 20 }],
  selected_profile_id: "local-test-mail", instance_id: "instance-one", job: null, active_job: null
}; renderModules();`, context);
const dailyCard = elements.get("#module-list").children.find(card => card.innerText.includes("日常任务奖励"));
assert.ok(dailyCard, "daily reward module should render as an independent card");
assert.match(dailyCard.innerText, /一键领取循环（最多 5 轮）/);
assert.match(dailyCard.innerText, /运行一键领取/);
assert.doesNotMatch(dailyCard.innerText, /运行单封领取/);
vm.runInContext('state.server.modules.daily_rewards_claim.behavior_notes = ["公告跳过仅支持已校准的日常页；不点击前往。"]; renderModules();', context);
assert.match(elements.get("#module-list").innerText, /公告跳过仅支持已校准的日常页/);
assert.equal(requests.length, 0, "rendering or enabling preferences must not start a game job");

function jsonResponse(status, body) {
  return { ok: status >= 200 && status < 300, status, async json() { return body; } };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  const first = vm.runInContext('startTask("mail_bulk_claim", "dry-run")', context);
  const second = vm.runInContext('startTask("mail_bulk_claim", "dry-run")', context);
  assert.equal(requests.length, 1, "the synchronous pending lock must suppress a second click");
  vm.runInContext("renderModules()", context);
  assert.equal(vm.runInContext('state.pendingSubmission !== null', context), true);
  const actionButtons = elements.get("#module-list").children.flatMap(card => card.children[3].children);
  assert.ok(actionButtons.length >= 4 && actionButtons.every(button => button.disabled), "all launch buttons stay disabled while a submit is pending");

  // First transport response is lost; retry must use exactly the same request id.
  requests[0].reject(new TypeError("network lost"));
  await settle();
  assert.equal(requests.length, 2, "one idempotent transport retry is expected");
  const ids = requests.slice(0, 2).map(request => JSON.parse(request.options.body).request_id);
  assert.equal(ids[0], ids[1]);

  // Lose the retried response too. The UI must keep an explicit recovery path and preserve the id.
  requests[1].reject(new TypeError("response lost"));
  await Promise.all([first, second]);
  assert.equal(vm.runInContext('state.pendingSubmission.status', context), "uncertain");
  const persisted = JSON.parse(sessionStorage.getItem("dashboard.pendingSubmission"));
  assert.equal(persisted.payload.request_id, ids[0]);
  assert.equal(persisted.payload.instance_id, "instance-one");

  // Simulate a page reload with the same sessionStorage, without auto-submitting on load.
  const reloadElements = new Map([...elements.keys()].map(key => [key, new FakeElement(key === "#profile-select" ? "select" : "div")]));
  reloadElements.get("#profile-select").value = "local-test-mail";
  const reloadDocument = { ...document, querySelector(selector) { return selector.startsWith("meta[") ? meta : reloadElements.get(selector) || new FakeElement(); } };
  const reloadRequests = [];
  const reloadContext = vm.createContext({
    document: reloadDocument,
    fetch(path, options) { return new Promise((resolve, reject) => reloadRequests.push({ path, options, resolve, reject })); },
    crypto: { randomUUID: () => `unexpected-${++uuid}` }, sessionStorage,
    setTimeout, clearTimeout, Date, JSON, TypeError, console,
  });
  vm.runInContext(appSource, reloadContext, { filename: "app.js" });
  vm.runInContext(`state.server = {
    modules: {
      mail_bulk_claim: { task_id: "mail_bulk_claim", name: "系统邮件", description: "mail", acceptance: "待验收", enabled: true },
      claim_primary: { task_id: "claim_primary", name: "单封", description: "claim", acceptance: "已验收", enabled: true }
    }, profiles: [{ profile_id: "local-test-mail", label: "测试", available: true, device_serial: "emulator-5554", max_actions: 20 }],
    selected_profile_id: "local-test-mail", instance_id: "instance-one", job: null, active_job: null
  }; renderModules();`, reloadContext);
  assert.equal(reloadRequests.length, 0, "refresh must not automatically submit the pending job");
  assert.equal(vm.runInContext('state.pendingSubmission.payload.request_id', reloadContext), ids[0]);
  const recovered = vm.runInContext("retryPendingSubmission()", reloadContext);
  assert.equal(reloadRequests.length, 1);
  assert.equal(JSON.parse(reloadRequests[0].options.body).request_id, ids[0]);
  reloadRequests[0].resolve(jsonResponse(202, { job: { job_id: "job-1", request_id: ids[0], status: "running" } }));
  await settle();
  assert.equal(reloadRequests.length, 2, "accepted response should refresh state");
  reloadRequests[1].resolve(jsonResponse(200, {
    modules: {
      mail_bulk_claim: { task_id: "mail_bulk_claim", name: "系统邮件", description: "mail", acceptance: "待验收", enabled: true },
      claim_primary: { task_id: "claim_primary", name: "单封", description: "claim", acceptance: "已验收", enabled: true }
    }, profiles: [{ profile_id: "local-test-mail", label: "测试", available: true, device_serial: "emulator-5554", max_actions: 20 }],
    selected_profile_id: "local-test-mail", instance_id: "instance-one", active_job: { job_id: "job-1", request_id: ids[0], task_id: "mail_bulk_claim", profile_id: "local-test-mail", mode: "dry-run", status: "running", stage: "检测中", progress: { action_count: 2, action_upper_bound: 20, navigation_action_count: 2, claim_action_count: null, dismiss_action_count: null } },
    job: { job_id: "job-1", request_id: ids[0], task_id: "mail_bulk_claim", profile_id: "local-test-mail", mode: "dry-run", status: "running", stage: "检测中", progress: { action_count: 2, action_upper_bound: 20, navigation_action_count: 2, claim_action_count: null, dismiss_action_count: null } },
  }));
  await recovered;
  assert.equal(vm.runInContext("state.pendingSubmission", reloadContext), null);
  vm.runInContext("renderJob()", reloadContext);
  const rendered = reloadElements.get("#job-panel").innerText;
  assert.match(rendered, /已记录输入 2/);
  assert.match(rendered, /上限 20/);
  assert.match(rendered, /领取 未记录/);
  assert.doesNotMatch(rendered, /领取 0/);

  vm.runInContext(`state.server.active_job = null; state.server.job = {
    task_id: "mail_bulk_claim", mode: "execute", status: "finished", stage: "已结束",
    result: { status: "cancelled", action_count: 0, detail: { claim_action_count: 0 } }
  }; renderJob();`, reloadContext);
  const finalRendered = reloadElements.get("#job-panel").innerText;
  assert.match(finalRendered, /领取记录 0/);
  assert.match(finalRendered, /审计输入 0/);
  assert.match(finalRendered, /导航记录 未记录/);
  assert.doesNotMatch(finalRendered, /导航记录 0/);

  vm.runInContext(`state.server.job = {
    task_id: "mail_bulk_claim", mode: "execute", status: "finished", stage: "已结束",
    result: { status: "completed", action_count: 3, detail: { navigation_action_count: 2, claim_action_count: 1, dismiss_action_count: 0 } }
  }; renderJob();`, reloadContext);
  assert.match(reloadElements.get("#job-panel").innerText, /审计输入 3/);
  vm.runInContext(`state.server.job = {
    task_id: "mail_bulk_claim", mode: "execute", status: "finished", stage: "已结束",
    result: { status: "needs_attention", reason_code: "stop_unconfirmed", action_count: 1,
      detail: { action_count_is_upper_bound: true, input_outcome_uncertain: true, navigation_action_count: 1 } }
  }; renderJob();`, reloadContext);
  const uncertainRendered = reloadElements.get("#job-panel").innerText;
  assert.match(uncertainRendered, /输入计数上界 1（实际输入未确认）/);
  assert.doesNotMatch(uncertainRendered, /审计输入 1/);
  assert.match(uncertainRendered, /导航记录 1/);
  vm.runInContext(`state.server.active_job = null; state.server.job = {
    task_id: "daily_rewards_claim", mode: "execute", status: "finished", stage: "已结束",
    result: { task_id: "daily_rewards_claim", status: "needs_attention", reason_code: "partial_progress", action_count: 5, detail: {
      navigation_action_count: 0, claim_action_count: 0, dismiss_action_count: 1, scroll_action_count: 4,
      cleanup_verified: true, final_page_id: "main_city", coverage: { scanned_pages: 5, complete: false },
      batch_rounds: [{ round_index: 1, submission: "confirmed", reward_content_verified: true }],
      confirmed_batch_round_count: 1, uncertain_batch_round_count: 0
    } }
  }; renderJob();`, reloadContext);
  const dailyRendered = reloadElements.get("#job-panel").innerText;
  assert.match(dailyRendered, /关闭记录 1/);
  assert.match(dailyRendered, /滚动记录 4/);
  assert.match(dailyRendered, /返回主城：已确认；最终页面 main_city/);
  assert.match(dailyRendered, /日常任务扫描：5 页；覆盖不完整/);
  assert.match(dailyRendered, /一键领取轮次：1；流程已确认 1；不确定 0/);
  vm.runInContext(`delete state.server.job.result.detail.confirmed_batch_round_count;
    delete state.server.job.result.detail.uncertain_batch_round_count;
    state.server.job.result.detail.batch_rounds = [{ submission: "confirmed", reward_content_verified: false,
      reward_popup_seen: true, reward_popup_closed: true, post_batch_state: "gray" }]; renderJob();`, reloadContext);
  assert.match(reloadElements.get("#job-panel").innerText, /一键领取轮次：1；流程已确认 1；不确定 0/);

  vm.runInContext(`state.server.active_job = { task_id: "daily_rewards_claim", progress: {
    action_count: 4, action_upper_bound: 20, navigation_action_count: 2, claim_action_count: 1,
    dismiss_action_count: 1, scroll_action_count: 0, batch_round_count: 1,
    confirmed_batch_round_count: 0, uncertain_batch_round_count: 0, pending_batch_round_count: 1
  }}; state.server.job = state.server.active_job; renderJob();`, reloadContext);
  const activeDailyRendered = reloadElements.get("#job-panel").innerText;
  assert.match(activeDailyRendered, /批领轮次 1 · 已确认 0 · 不确定 0 · 进行中 1/);
  assert.match(activeDailyRendered, /识别顶部灰态后停止/);

  const cleanupResult = {
    task_id: "daily_rewards_claim", status: "needs_attention", reason_code: "outcome_uncertain", action_count: 2,
    detail: { cleanup_only: true, claim_verified: false, post_batch_state_verified: false,
      claim_action_count: 0, dismiss_action_count: 2, cleanup_verified: true, final_page_id: "main_city",
      announcement_dismiss_attempted: true, announcement_dismissed: true }
  };
  vm.runInContext(`state.server.job.result = ${JSON.stringify(cleanupResult)}; renderJob();`, reloadContext);
  const cleanupRendered = reloadElements.get("#job-panel").innerText;
  assert.match(cleanupRendered, /本轮仅收尾，未重新领取/);
  assert.match(cleanupRendered, /领取确认：未确认/);
  assert.match(cleanupRendered, /公告处理：已跳过并重新识别/);
  assert.doesNotMatch(cleanupRendered, /领取确认：已确认/);
  const beforeHistoryRequests = reloadRequests.length;
  const historyRead = vm.runInContext('openRun("run-test-cleanup", false)', reloadContext);
  assert.equal(reloadRequests.length, beforeHistoryRequests + 1);
  assert.equal(reloadRequests.at(-1).path, "/api/runs/run-test-cleanup");
  reloadRequests.at(-1).resolve(jsonResponse(200, { result: cleanupResult, events: [], images: [] }));
  await historyRead;
  const historyRendered = reloadElements.get("#run-detail").innerText;
  assert.match(historyRendered, /领取确认：未确认/);
  assert.match(historyRendered, /返回主城：已确认/);
  assert.match(historyRendered, /领取记录 0/);
  assert.equal(reloadRequests.length, beforeHistoryRequests + 1, "opening history must not launch a game job");

  sessionStorage.setItem("dashboard.pendingSubmission", JSON.stringify({ payload: {
    request_id: "old-instance-request", task_id: "mail_bulk_claim", profile_id: "local-test-mail",
    mode: "dry-run", instance_id: "instance-old"
  } }));
  const staleElements = new Map([...elements.keys()].map(key => [key, new FakeElement(key === "#profile-select" ? "select" : "div")]));
  staleElements.get("#profile-select").value = "local-test-mail";
  const staleRequests = [];
  const staleDocument = { ...document, querySelector(selector) { return selector.startsWith("meta[") ? meta : staleElements.get(selector) || new FakeElement(); } };
  const staleContext = vm.createContext({
    document: staleDocument,
    fetch(path, options) { return new Promise((resolve, reject) => staleRequests.push({ path, options, resolve, reject })); },
    crypto: { randomUUID: () => `unexpected-${++uuid}` }, sessionStorage,
    setTimeout, clearTimeout, Date, JSON, TypeError, console,
  });
  vm.runInContext(appSource, staleContext, { filename: "app.js" });
  vm.runInContext("renderSubmissionRecovery()", staleContext);
  const oldStateLoad = vm.runInContext("loadState()", staleContext);
  staleRequests[0].resolve(jsonResponse(200, {
    instance_id: "instance-new", modules: {
      mail_bulk_claim: { task_id: "mail_bulk_claim", name: "系统邮件", description: "mail", acceptance: "待验收", enabled: true },
      claim_primary: { task_id: "claim_primary", name: "单封", description: "claim", acceptance: "已验收", enabled: true }
    }, profiles: [{ profile_id: "local-test-mail", label: "测试", available: true, max_actions: 20 }],
    selected_profile_id: "local-test-mail", active_job: null, job: null,
  }));
  await oldStateLoad;
  assert.equal(vm.runInContext('state.pendingSubmission.status', staleContext), "stale");
  assert.equal(staleElements.get("#retry-submission").disabled, true);
  assert.match(staleElements.get("#submission-recovery-message").textContent, /旧服务实例/);
  assert.match(staleElements.get("#submission-recovery-message").textContent, /刷新页面/);
  const retryCountBefore = staleRequests.length;
  await vm.runInContext("retryPendingSubmission()", staleContext);
  assert.equal(staleRequests.length, retryCountBefore, "old-instance recovery must not submit again");
  vm.runInContext("abandonPendingSubmission()", staleContext);
  assert.equal(vm.runInContext("state.pendingSubmission", staleContext), null);
  assert.equal(sessionStorage.getItem("dashboard.pendingSubmission"), null);

  console.log(JSON.stringify({ rapidSubmissionCount: 1, retryRequestIdsSame: true, refreshDoesNotSubmit: true, progressShowsUnknown: true, finalAuditZeroPreserved: true, upperBoundExplained: true, oldInstanceNotResubmitted: true, abandonRecoveryAvailable: true, dailyModuleCopyAndDefaultOff: true, dailyCleanupAndCoverageShown: true }));
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
