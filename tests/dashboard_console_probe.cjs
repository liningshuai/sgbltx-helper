"use strict";
// 控制台首页探针：用真实 app.js 渲染今日任务卡片与功能总览折叠计数。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class FakeElement {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase(); this.children = []; this.handlers = {}; this.attributes = {};
    this.className = ""; this.textContent = ""; this.hidden = false; this.disabled = false; this.value = ""; this.dataset = {};
    this.classList = { toggle() {}, add() {}, remove() {} };
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; }
  addEventListener(name, fn) { this.handlers[name] = fn; }
  setAttribute(name, value) { this.attributes[name] = value; }
  get innerText() { return [this.textContent, ...this.children.map(child => child.innerText || "")].join(" "); }
}
const elements = new Map();
for (const id of ["console-cards", "catalog-groups", "catalog-summary", "catalog-search", "catalog-fold-count", "notice", "job-panel"]) {
  elements.set(`#${id}`, new FakeElement(id === "catalog-search" ? "input" : "div"));
}
const document = {
  hidden: false,
  querySelector(selector) { return selector.startsWith("meta[") ? { content: "t" } : elements.get(selector) || new FakeElement(); },
  querySelectorAll() { return []; }, createElement(tag) { return new FakeElement(tag); }, addEventListener() {},
};
const requests = [];
const context = vm.createContext({
  document, fetch(path, options) { requests.push({ path, options }); return new Promise(() => {}); },
  crypto: { randomUUID: () => "request-1" },
  sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {} }, setTimeout, clearTimeout, Date, JSON, TypeError, console,
});
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context, { filename: "app.js" });

function find(root, predicate) {
  const found = [];
  const walk = node => { if (predicate(node)) found.push(node); (node.children || []).forEach(walk); };
  walk(root); return found;
}

assert.equal(vm.runInContext("state.activeView", context), "console", "console is the default view");

vm.runInContext(`state.server = {
  instance_id: "instance-1", selected_profile_id: "local-test-mail", active_job: null,
  profiles: [{ profile_id: "local-test-mail", label: "测试", available: true }],
  modules: {
    mail_bulk_claim: { task_id: "mail_bulk_claim", name: "系统邮件一键领取", icon: "✉", enabled: true },
    claim_primary: { task_id: "claim_primary", name: "单封太守奖励", enabled: false },
    daily_rewards_claim: { task_id: "daily_rewards_claim", name: "日常任务奖励", icon: "☆", enabled: false },
    fief_industry_collect: { task_id: "fief_industry_collect", name: "封地产业收取", icon: "⛁", enabled: true },
  },
  catalog: [{ title: "基础功能", features: [
    { name: "系统邮件一键领取", status: "accepted" }, { name: "沙盘演绎", status: "planned" } ] }],
};
renderConsole();
state.loadingProbe = [...document.querySelector("#console-cards").children].map(card => card.innerText).join("|");
state.runsLoaded = true;
state.runs = [
  { run_id: "dryrun-2", task_id: "mail_bulk_claim", status: "inspection", mode: "dry-run", started_at: "2026-10-21T15:00:00+00:00" },
  { run_id: "run-2", task_id: "mail_bulk_claim", status: "completed", mode: "execute", started_at: "2026-10-21T14:57:50+00:00", action_count: 5 },
  { run_id: "run-1", task_id: "mail_bulk_claim", status: "needs_attention", mode: "execute", started_at: "2026-10-21T10:46:52+00:00" },
  { run_id: "run-3", task_id: "daily_rewards_claim", status: "nothing_to_claim", mode: "execute", started_at: "2026-10-21T10:51:07+00:00", action_count: 3 },
];
renderConsole(); renderCatalog();`, context);

assert.match(vm.runInContext("state.loadingProbe", context), /正在读取记录/, "before run records load, cards must not claim there are none");
assert.doesNotMatch(vm.runInContext("state.loadingProbe", context), /暂无执行记录/);
const cards = find(elements.get("#console-cards"), node => String(node.className).split(" ").includes("console-card"));
assert.equal(cards.length, 3, "mail, daily and fief cards; the manual single-mail task is not on the console");
assert.match(cards[0].innerText, /系统邮件一键领取/);
assert.match(cards[0].innerText, /任务返回完成/, "latest execute result, dry-run ignored");
assert.doesNotMatch(cards[0].innerText, /需要人工确认/);
assert.match(cards[1].innerText, /日常任务奖励/);
assert.match(cards[1].innerText, /无待领取奖励/);
assert.match(cards[2].innerText, /封地产业收取/);
assert.match(cards[2].innerText, /暂无执行记录/);

const buttons = card => find(card, node => node.tagName === "BUTTON");
assert.equal(buttons(cards[1])[0].textContent, "去开启", "disabled module links to the module switch");
const run = buttons(cards[0])[0];
assert.equal(run.textContent, "运行");
run.handlers.click();
assert.equal(requests.at(-1).path, "/api/jobs");
assert.match(requests.at(-1).options.body, /"task_id":"mail_bulk_claim"/);
assert.match(requests.at(-1).options.body, /"mode":"execute"/);

assert.match(elements.get("#catalog-fold-count").textContent, /已验收\s*1\s*\/\s*共\s*2/);
console.log(JSON.stringify({ consoleRendered: true }));
