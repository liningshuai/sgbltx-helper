# 第一阶段纯领取闭环报告

日期：2026-10-05。状态：**本轮限定的单封、用户手动定位领取闭环已完成。** 在成功素材已预先登记的条件下，“太守奖励”新邮件实机 execute 自动返回 `completed/claim_confirmed`，action_count=1；同封邮件重复 execute 返回 `nothing_to_claim`，action_count=0。仍不包含主城导航、批量邮件或无人值守日常。

## 1. 结论

用户确认当前角色账号稳定，并手动进入“更多 → 邮件”，再手动打开“太守奖励”详情。本次前置实机 dry-run 同时命中太守邮件详情标题和绿色“领取”按钮，分数均为 1.000；账号冲突模板未命中。Maa 原生 Pipeline 先识别详情标题、再识别按钮，仅有一个 Click，`repeat=1`、`max_hit=1`，静态预算为 1。Pipeline 入口与页面身份被限定到“太守奖励”，旧“国庆同庆，三国献礼！”详情样本不匹配新任务入口。

此前“国庆同庆，三国献礼！”主题的首次 execute 审计到一个 Click，action_count=1。后置实机截图明确显示“恭喜获得奖励”、奖励物品，以及“点击任意区域关闭”；按钮已变灰。**这张真实后置画面是领取成功证据。** 但该次 execute 的 `result.json` 为 `needs_attention/outcome_uncertain`：当时成功状态模板还未登记，不能回写或改称 CLI 当次返回 completed。随后以真实成功截图制作“点击任意区域关闭”模板，经 Maa 回放命中 1.000；从成功画面再执行一轮时，可领取匹配为 miss（灰色按钮 score=0.923，小于阈值 0.97），成功标志命中，Pipeline 未提交，结果 `nothing_to_claim`、action_count=0。

本次“太守奖励”成功模板在执行前已登记并由真实回放验证。首次 TaskResult 自动返回 completed，后置成功标志命中；后续重复运行返回 nothing_to_claim，无额外输入。此前“国庆同庆，三国献礼！”的原始首次领取结果仍保留 needs_attention/outcome_uncertain，作为历史结果未改写。

## 2. 账号、设备与范围

- 用户确认当前登录角色稳定，且不会被别人顶号；遇到账号冲突时先前已停止并由用户恢复。
- 设备：`emulator-5554`；包名 `com.sgbltx.goodgame`；分辨率 `1080x1920`；匿名别名 `main`。账号/别名对应关系只依据用户确认。
- 本次范围是一封邮件中的纯领取动作。没有登录、切换账号、点击“一键阅读”、消费、招募、战斗或其他业务输入。
- 游戏版本元数据 `7.3` / code `10`、渠道未在本轮实机重新核验。

## 3. 本轮实现

- 新增详情页身份 `mail_claim_detail`：模板 `mail_detail_title.png` 必须匹配“国庆同庆，三国献礼！”。列表页改用底部“一键阅读”作为身份特征，避免详情弹窗被背景中的“邮件”标题误认成列表页。
- 领取状态采用 `mail_claim_button.png`，阈值 0.97。领取前绿色按钮得分 1.000；成功弹窗中的灰色按钮得分 0.923，不会再被当作可领取。
- 成功状态使用 `mail_claim_success.png`，匹配奖励弹窗底部的稳定文字“点击任意区域关闭”，ROI `[300,1080,500,140]`，阈值 0.88。真实领取后页面回放得分 1.000，领取前详情页不命中。
- 修正 `claim_primary.json` 为 Maa 5.14.2 原生 `recognition.type/param`、`action.type/param` 和 `next:[{name:...}]` 格式。静态动作计数器也只接受该原生格式，并要求线性无循环、无错误分支、显式 `repeat=1` 与 `max_hit=1`，Click 目标只能是当前识别框。
- 将 `ClaimReward` 输入节点的 threshold 从 0.90 同步为 0.97，与页面 `claimable` 条件的 template、ROI、阈值完全相同。真实 Maa 输入节点识别回归证明绿色按钮命中，实际成功后灰按钮（score 0.429267）和重复运行样本的灰按钮（score 0.923482）均不命中。
- 为实机新主题增加 `mail_taishou_detail` 页面身份，template `mail_taishou_title.png`；将 `target_page_id` 与 Pipeline 入口模板一并指向“太守奖励”。共用已验证的绿色按钮、动作及奖励成功弹窗条件。
- 增加对应的 Maa 实际截图回放、Pipeline 上界和资源包加载测试。更新 `docs/assets.md`、`docs/environment.md`。
- 没有改依赖、虚拟环境或运行器总体架构。原始截图保存在被忽略的本地 `artifacts/`，未将私密画面复制到版本管理。

## 4. 验证命令与结果

### 真实截图 Maa 回放和静态 Pipeline 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q tests/test_runner.py::TestBudget tests/test_recognition.py::TestMailPageReplay
```

结果：`22 passed in 6.45s`。覆盖 Maa SDK 可加载原生 Pipeline、静态上界恰为一次 Click、邮件列表/详情区分、领取前按钮和真实成功后置模板。所有截图识别均调用本机 MaaFw 5.14.2，不是预写结果或 fake。

本次针对 M1 的真实执行节点样本回归命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q tests/test_recognition.py::TestClaimRewardNodeReplay tests/test_runner.py::TestBudget
```

结果：M1 修复前后的针对性测试曾以真实 Maa 识别 API 重现 Pipeline threshold 0.90 会把重复运行灰按钮 score 0.923482 判为 hit；改为 0.97 后绿色领取按钮命中、首次 after 灰按钮与重复运行灰按钮均不命中。本轮新主题追加回归，针对性测试命令如下：

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q tests/test_recognition.py::TestMailPageReplay tests/test_recognition.py::TestClaimRewardNodeReplay tests/test_runner.py::TestBudget
```

结果：`30 passed in 7.97s`。覆盖太守真实详情/领取按钮，Pipeline 入口只接受太守主题而拒绝旧国庆主题，且页面与执行节点参数一致。

### 领取前实机零输入 dry-run

```powershell
.\.venv\Scripts\python.exe -m sgbltx_helper run --profile config/local.json --task claim_primary --dry-run
```

run_id `dryrun-20261005T150309Z-155852`，退出码 0；`page_id=mail_claim_detail`，详情标题和可领取按钮各得分 1.000，`claim_attempted=false`，冲突未命中。

### 用户授权的一次真实 execute

```powershell
.\.venv\Scripts\python.exe -m sgbltx_helper run --profile config/local.json --task claim_primary --execute
```

run_id `run-20261005T150342Z-154560`，CLI 退出码 1；TaskResult 为 `needs_attention/outcome_uncertain`、`action_count=1`。事件审计 `task_finished.succeeded=true`、`input_actions=["Click"]`，节点依次为 `ClaimPrimary`、`ClaimReward`。后置实机截图显示真实奖励弹窗；结果模板当时尚未登记，所以 CLI 保守报不确定。

### 后置截图回放及当前成功状态零输入检查

先用该真实后置截图制作成功模板，回放命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q tests/test_recognition.py::TestMailPageReplay
```

结果：`5 passed in 1.98s`。后续当前实机 dry-run `dryrun-20261005T151042Z-163996` 退出码 0：领取按钮 score 0.923，低于 0.97；`mail_claim_success.png` score 1.000；`claim_attempted=false`。

第二次 execute：

```powershell
.\.venv\Scripts\python.exe -m sgbltx_helper run --profile config/local.json --task claim_primary --execute
```

run_id `run-20261005T151113Z-155752`，退出码 0；状态 `nothing_to_claim`，`action_count=0`。事件在 gate 阶段命中已领取标志并结束；没有 `task_finished` / `post_task`，即没有把 Pipeline 提交给 Maa。

### 全套测试

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q
```

结果：`111 passed in 15.90s`，退出码 0。

### M2 实机验收

```powershell
.\.venv\Scripts\python.exe -m sgbltx_helper run --profile config/local.json --task claim_primary --dry-run
```

前次已领取主题的 dry-run `dryrun-20261005T154933Z-151432` 识别已领成功、未识别可领。之后用户手动打开另一封真实“太守奖励”邮件，本轮首次只读采集 `dryrun-20261006T011905Z-164888` 当时因新主题尚未登记而分类未知，没有输入。

登记并回放验证新主题后，M2 前置 dry-run：

```powershell
.\.venv\Scripts\python.exe -m sgbltx_helper run --profile config/local.json --task claim_primary --dry-run
```

run_id `dryrun-20261006T014052Z-173164`，退出码 0；`mail_taishou_detail` 标题与可领按钮各 score=1.000，成功标志未命中，claim_attempted=false。

M2 新奖励真实 execute：

```powershell
.\.venv\Scripts\python.exe -m sgbltx_helper run --profile config/local.json --task claim_primary --execute
```

run_id `run-20261006T014134Z-173084`，退出码 0；`completed/claim_confirmed`，action_count=1。原 result.json 的 `postcondition` 命中 `mail_claim_success.png`，审计到一次 Click。

同封邮件重复 execute：

```powershell
.\.venv\Scripts\python.exe -m sgbltx_helper run --profile config/local.json --task claim_primary --execute
```

run_id `run-20261006T014320Z-151748`，退出码 0；`nothing_to_claim`、action_count=0。事件记录在 gate 命中已领取标志后直接结束，没有 Pipeline task 提交。

## 5. 实机证据

| 阶段 | 工件 | 说明 |
|---|---|---|
| 领取前零输入识别 | [before.png](../artifacts/runs/dryrun-20261005T150309Z-155852/before.png) · [inspection.json](../artifacts/runs/dryrun-20261005T150309Z-155852/inspection.json) · [events.jsonl](../artifacts/runs/dryrun-20261005T150309Z-155852/events.jsonl) | 详情标题、可领取按钮命中；未输入 |
| 首次真实领取 | [before.png](../artifacts/runs/run-20261005T150342Z-154560/before.png) · [after.png](../artifacts/runs/run-20261005T150342Z-154560/after.png) · [events.jsonl](../artifacts/runs/run-20261005T150342Z-154560/events.jsonl) · [result.json](../artifacts/runs/run-20261005T150342Z-154560/result.json) | 一个已审计 Click；after 显示获得奖励；TaskResult 仍是 outcome_uncertain |
| 模板校准期间的实机成功画面 | [before.png](../artifacts/runs/dryrun-20261005T150727Z-171276/before.png) · [inspection.json](../artifacts/runs/dryrun-20261005T150727Z-171276/inspection.json) | 零输入；发现动画导致旧成功模板漏检及灰按钮假阳性，故未 execute 并重采稳定关闭文字 |
| 稳定后置状态确认 | [before.png](../artifacts/runs/dryrun-20261005T151042Z-163996/before.png) · [inspection.json](../artifacts/runs/dryrun-20261005T151042Z-163996/inspection.json) · [events.jsonl](../artifacts/runs/dryrun-20261005T151042Z-163996/events.jsonl) | 成功状态 1.000、按钮 0.923；零输入 |
| 再运行验证 | [before.png](../artifacts/runs/run-20261005T151113Z-155752/before.png) · [events.jsonl](../artifacts/runs/run-20261005T151113Z-155752/events.jsonl) · [result.json](../artifacts/runs/run-20261005T151113Z-155752/result.json) | `nothing_to_claim`，action_count 0，没有 Pipeline 提交 |
| M1 当前阈值检查 | [before.png](../artifacts/runs/dryrun-20261005T154933Z-151432/before.png) · [inspection.json](../artifacts/runs/dryrun-20261005T154933Z-151432/inspection.json) · [events.jsonl](../artifacts/runs/dryrun-20261005T154933Z-151432/events.jsonl) | 当前 success 标志命中、灰按钮未命中；零输入。无新奖励 |
| 太守主题未登记前采集 | [before.png](../artifacts/runs/dryrun-20261006T011905Z-164888/before.png) · [events.jsonl](../artifacts/runs/dryrun-20261006T011905Z-164888/events.jsonl) · [inspection.json](../artifacts/runs/dryrun-20261006T011905Z-164888/inspection.json) | 真实详情页面；页面当时未知，因此未操作 |
| 太守主题领取前 dry-run | [before.png](../artifacts/runs/dryrun-20261006T014052Z-173164/before.png) · [events.jsonl](../artifacts/runs/dryrun-20261006T014052Z-173164/events.jsonl) · [inspection.json](../artifacts/runs/dryrun-20261006T014052Z-173164/inspection.json) | 新主题身份及领取按钮命中；未输入 |
| 太守主题真实领取 | [before.png](../artifacts/runs/run-20261006T014134Z-173084/before.png) · [after.png](../artifacts/runs/run-20261006T014134Z-173084/after.png) · [events.jsonl](../artifacts/runs/run-20261006T014134Z-173084/events.jsonl) · [result.json](../artifacts/runs/run-20261006T014134Z-173084/result.json) | execute 自动 completed/claim_confirmed，action_count 1；after 显示奖励弹窗 |
| 太守主题重复运行 | [before.png](../artifacts/runs/run-20261006T014320Z-151748/before.png) · [events.jsonl](../artifacts/runs/run-20261006T014320Z-151748/events.jsonl) · [result.json](../artifacts/runs/run-20261006T014320Z-151748/result.json) | nothing_to_claim，action_count 0，Pipeline 未提交 |

## 6. 验证矩阵

| 项目 | 结果 | 类型/限制 |
|---|---|---|
| 正确账户页面与邮件详情身份 | 通过 | 实机截图与 Maa 回放；账号身份依据用户确认 |
| 领取前可领取状态 | 通过 | 绿色“领取”在真实详情页命中 1.000 |
| 单次纯领取动作 | 已执行一次 | 实机 Maa 审计 Click，action_count 1；没有其他业务动作 |
| 领取后成功证据 | 通过 | 实机截图显示“恭喜获得奖励”、奖励物品与“点击任意区域关闭”；之后真实截图回放命中后置模板 |
| 国庆邮件首次 TaskResult 自动 completed | 未达成 | 原始结果保留 `needs_attention/outcome_uncertain`，因成功模板是在动作后才登记；没有篡改结果 |
| 太守奖励预登记成功模板后的自动 completed | 通过 | 新邮件实机 execute 返回 `completed/claim_confirmed`，action_count 1 |
| 执行节点的绿/灰按钮边界 | 通过 | 实际 Maa 识别 API 回放真实 before/after/重复运行截图；page 与 Pipeline 参数一致 |
| 再次执行不重复领取 | 通过 | 实机第二次 execute 识别已领状态，`nothing_to_claim`、action_count 0、无 Pipeline 提交 |
| mock/fake | 未作为领取证据 | 测试使用 fixture 验证边界；领取证据全部来自实机截图、实机事件及原始结果 |
| 从主城无人值守导航 | 未实现 | 本轮按约定由用户手动进入邮件详情，不能称为完整日常自动化 |

## 7. 限制与分类

- **实机：** 当前用户登录会话、零输入 dry-run、一次 Maa Click、领取后奖励弹窗、后置零输入检查。账号角色映射来自用户确认，程序未独立核验。
- **截图回放：** MaaFw 5.14.2 对真实领取前详情截图、领取成功截图和列表截图的模板回放。回放不等于第二次真实领取。
- **mock：** 没有 fake/mock 领取结果；自动化单元测试不作为实机领取证明。
- Maa 仍打印 `dumpsys input | grep SurfaceOrientation ...` 子命令错误；本次截图、Pipeline 和输入审计均成功，但该环境诊断错误尚未查明。
- 当前 Pipeline 精确针对“太守奖励”；其他邮件主题及空邮箱、其他分辨率、长期动画/网络延迟、无人值守导航均未验证。
- M2 对当前太守奖励主题已完成；第一次国庆邮件的旧 `result.json` 保持原样。

本报告不把运行器基础修复或一次真实点击夸大成全面游戏自动化。实际执行证据、原始状态和限制均如上记录，交主聊天复审。
