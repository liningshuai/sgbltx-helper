"use strict";
// 功能总览视图探针：用真实 app.js 渲染目录，检查分组、状态计数、已验收标记与搜索过滤。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class FakeElement {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase(); this.children = []; this.handlers = {}; this.attributes = {};
    this.className = ""; this.textContent = ""; this.hidden = false; this.value = ""; this.dataset = {};
    this.classList = { toggle() {}, add() {}, remove() {} };
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; }
  addEventListener(name, fn) { this.handlers[name] = fn; }
  setAttribute(name, value) { this.attributes[name] = value; }
  get innerText() { return [this.textContent, ...this.children.map(child => child.innerText || "")].join(" "); }
}
const elements = new Map();
for (const id of ["catalog-groups", "catalog-summary", "catalog-search", "notice"]) elements.set(`#${id}`, new FakeElement(id === "catalog-search" ? "input" : "div"));
const document = {
  hidden: false,
  querySelector(selector) { return selector.startsWith("meta[") ? { content: "t" } : elements.get(selector) || new FakeElement(); },
  querySelectorAll() { return []; }, createElement(tag) { return new FakeElement(tag); }, addEventListener() {},
};
const context = vm.createContext({ document, fetch() { return new Promise(() => {}); }, crypto: { randomUUID: () => "x" },
  sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {} }, setTimeout, clearTimeout, Date, JSON, TypeError, console });
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context, { filename: "app.js" });

function chips(root) {
  const found = [];
  const walk = node => { if (String(node.className).includes("feature-chip")) found.push(node); (node.children || []).forEach(walk); };
  walk(root); return found;
}
vm.runInContext(`state.server = { catalog: [
  { title: "挂机", features: [{ name: "一键挂机", status: "recommended", note: "串联已验收任务" }] },
  { title: "基础功能", features: [
    { name: "系统邮件一键领取", status: "accepted", task_id: "mail_bulk_claim" },
    { name: "日常任务奖励", status: "accepted", task_id: "daily_rewards_claim" },
    { name: "沙盘演绎", status: "planned" } ] },
  { title: "钻石功能", features: [{ name: "自动派兵", status: "planned" }] } ] }; renderCatalog();`, context);

const groups = elements.get("#catalog-groups");
assert.equal(groups.children.length, 3, "three groups rendered");
const all = chips(groups);
assert.equal(all.length, 5);
assert.ok(all.some(chip => chip.innerText.includes("系统邮件一键领取") && String(chip.className).includes("is-accepted")));
assert.ok(all.some(chip => chip.innerText.includes("一键挂机") && String(chip.className).includes("is-recommended")));
const summary = elements.get("#catalog-summary").innerText;
assert.match(summary, /已验收\s*2/);
assert.match(summary, /共\s*5/);

elements.get("#catalog-search").value = "邮件";
vm.runInContext("renderCatalog()", context);
const filtered = chips(groups);
assert.equal(filtered.length, 1);
assert.match(filtered[0].innerText, /系统邮件一键领取/);

vm.runInContext("state.server = { modules: {} }; renderCatalog();", context);
assert.match(groups.innerText, /功能目录暂未读取/);
console.log(JSON.stringify({ catalogRendered: true }));
