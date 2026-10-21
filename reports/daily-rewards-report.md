# 日常任务奖励模块实现与实机记录

更新日期：2026-10-08。当前实机验收状态：**partial**。主聊天已修复日常循环复审 DL1—DL4，完整测试 281 passed，独立只读复审通过；本轮未操作真实游戏。修复详情见 [`daily-loop-fixes-20261008.md`](daily-loop-fixes-20261008.md)。下文保留各阶段当时的实现和证据，早期固定来源项目、物品名称成功门槛已被当前绿/灰控件闭环替代。首次不确定领取结果保留，新奖励完整实机闭环与重复检查仍待验收。

## 范围和保护

- 新任务 ID：`daily_rewards_claim`。沿用 MaaFramework 5.14.2、原设备锁、停止控制和整轮 deadline；资源位于 `resource/daily_rewards/`。
- 仅允许已完成任务奖励和已达标活跃宝箱。本轮没有足够的真实素材证明任务行或宝箱的可领取/已领取状态，因此没有登记领取输入，也没有点击“一键领取”或任何“前往”。购买、升级、派兵、战斗等动作均未执行。
- 管理网页模块默认关闭。启用开关只保存偏好，不启动作业。旧设置缺少该字段时按关闭处理。
- 新增 Maa `Controller.post_swipe` 适配；输入经过控制器 Job 有界等待。停止/超时后等待该 Controller Job 自身完成；超过确认窗口传播 `MaaStopUnconfirmed`，不伪造 Job 完成、不改走 ADB 输入。原生 Job 无法瞬时强制取消，沿用既有设备禁用标记策略。

## 页面调查与识别

授权档案为 `config/local-test.json`（`local-test-mail` / `test_mail`，设备 `emulator-5554`，1080×1920）。别名和等级不是账号认证；新鲜主城截图与已授权测试角色样本一致，未见账号冲突。

真实路线为：主城左下“任务” → “政务”中心 → “日常任务”。首个页签点击后的截图仍在播放动画并显示政务旧内容，程序未重复点击；随后只读检查确认标题已变成“日常任务”。当时可见“今日已完成 0/23”，首屏任务进度均为 0，顶部“一键领取”为灰态。

新增模板和 ROI 详见 [`docs/assets.md`](../docs/assets.md)。全部阈值为 0.97。真实 Maa 离线回放记录在 `artifacts/reviews/daily-rewards-recognition.json`：政务正样本两身份条件 score 均为 1.000；日常任务正样本标题和“今日已完成”score 均为 1.000；精确 0/23 只读模板命中。主城、邮件列表、邮件奖励及冲突旧图均未被分类为日常任务页。回放没有创建 Controller；它只证明截图识别，不证明真实领取。

列表扫描使用配置化安全滚动区域和有限页数。每次 Maa swipe 后重新截图、识别页面；没有复用行按钮坐标。即使画面重复也只停止扫描并保持 coverage incomplete，不以截图哈希声称列表到底。当前没有经过验证的任务行领取控件、已领取状态或活跃宝箱边界。

## 实机工件

所有源截图留在本机 `artifacts/runs/`，没有覆写旧邮件工件或原始任务结果。

| run | 类型与实际结果 |
|---|---|
| `dryrun-20261007T050442Z-197612` | 新鲜主城只读检查；未输入。主城身份与任务入口识别通过。 |
| `run-20261007T052223Z-192616` | 从主城进入政务中心 1 次导航；`needs_attention/unknown_page`，action 1，领取 0。原结果保留。 |
| `dryrun-20261007T052855Z-196172` | 政务页只读识别；零输入。 |
| `run-20261007T053134Z-187732` | 选择日常任务页签 1 次导航；后置截图仍在旧内容/动画帧，返回 `unknown_page`，领取 0。原结果保留。 |
| `dryrun-20261007T053223Z-180312` | 延迟后的日常任务页只读识别；零输入，识别到 0/23。 |
| `run-20261007T060747Z-180324` | 从日常任务页受控滚动 4 次、点击返回箭头 1 次；`needs_attention/partial_progress`，action 5（导航 0、领取 0、关闭 1、滚动 4），`cleanup_verified=true`，`final_page_id=main_city`。结果保留 `coverage.complete=false`、`boundary_verified=false`。`before.png`、`after.png` 至 `after_5.png`、`events.jsonl`、`result.json` 均在该 run 目录。 |
| `dryrun-20261007T061556Z-201500` | 后续主城只读检查 0 输入。截图出现未登记的“国战表彰/前往”前景弹窗，主城墙身份特征未通过，故分类未知且未操作。该弹窗目前需要用户手动关闭后，才可安全进行从主城开始的下一次验收。 |

上述实机路线分成多次受控运行以采集真实页面，尚未在同一次 runner 执行中从主城完成导航、完整扫描并回城。滚动实测没有领取任何奖励；因此不存在日常奖励的 `claim_confirmed`、`nothing_to_claim` 或重复领取证据。

## 管理网页接入

- `dashboard/registry.py` 注册独立“日常任务奖励”卡片、路由、验收状态和运行文案；`daily_rewards_claim` 默认关闭。
- `app.js` 显示模块自身路线、领取/关闭/滚动计数、扫描覆盖和最终回城状态；启用开关不触发任务。
- 真实 `8782` 服务检查时无活动作业，但仍运行旧服务实例，只暴露原两项模块。当前 Codex 任务没有附着该服务的启动终端；未强杀或更换占用端口的进程。代码和本地测试环境已接入，新卡片要在原服务终端正常停止并重启后才会出现在现有地址。
- 本轮网页验证通过实际 `app.js` Node VM 回归、临时 HTTP 服务与假 executor 测试；没有在旧 `8782` 服务提交游戏任务，也没有做真实浏览器点测。

## 验证命令

- `.\.venv\Scripts\python.exe -m pytest tests/test_daily_rewards.py tests/test_control.py tests/test_dashboard.py -p no:cacheprovider -o addopts= -q`：中间 targeted run `80 passed in 35.71s`；随后 app.js 回归发现滚动计数未渲染，修正后 Node 探针通过。
- `.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q`：`233 passed in 74.15s (0:01:14)`，退出码 0。
- `node tests/dashboard_app_probe.cjs src/sgbltx_helper/dashboard/static/app.js`：退出码 0；输出确认默认关闭文案、导航/领取/关闭/滚动计数、回城状态和不完整扫描状态。
- `.\.venv\Scripts\python.exe -m sgbltx_helper run --profile config/local-test.json --task daily_rewards_claim --dry-run`：日常页样本零输入通过；后续弹窗样本零输入并安全拒绝未知页面。
- `.\.venv\Scripts\python.exe -m sgbltx_helper run --profile config/local-test.json --task daily_rewards_claim --execute`：实际扫描/收尾如上，退出码 1（任务如实返回 `needs_attention/partial_progress`，不是成功领取）。
- 离线 Maa 截图回放：`artifacts/reviews/daily-rewards-recognition.json`；未连接 Controller。

## 历史首次扫描未验收与当时所需条件（后续状态见下文）

本节保留首次扫描阶段的限制，不代表当前页面；最新实际状态以本报告后续 2026-10-07 小节为准。

1. 当时前景“国战表彰/前往”弹窗需用户手动关闭，并停在主城；程序不会点未登记弹窗中的“前往”。
2. 需要实际出现至少一个已完成且可领取的日常任务奖励，供登记项目身份、可领/已领状态和安全领取目标。
3. 需要实际达到/未达标/已领取活跃宝箱状态及完整列表底部或分页边界样本。仅有 0/23 计数和当前可见行，不能证明全列表无奖励。
4. 之后需用一个新鲜有效奖励完成同次 runner 自动领取、后置项目确认、逐层回城以及重复运行零领取输入；不得复用本轮没有领取的样本冒充成功。
5. 现有 `8782` 服务需由其原启动终端正常重启后再检查网页模块；不杀占用端口的进程。

因此当前模块明确保持 **partial**，不会返回 `nothing_to_claim` 或 `completed`。

## 2026-10-07：顶部“一键领取”实现与实机阻断记录

### 本轮变更

- 新增顶部绿色“一键领取”模板与四个来源项目标题模板：奇珍异宝、花点小钱、书商到访、名士来访。只有日常任务页面身份、四个项目身份及各自绿色行状态、顶部批量按钮同时通过当前画面识别，才允许单次批领。任务完成计数 `4/23` 只记录，不参与授权或领取结果判断。
- 一键领取标志在 Maa 点击 API 前置为已提交；任何 SDK 错误、停止、超时或后置状态不确定均不会第二次点击，也不会回退到逐项领取。
- 新增日常奖励前景模板及“点击任意区域关闭”控件。对物品模板无命中的奖励仍可通过已验证前景与关闭提示安全收尾；领取成功必须另由四个来源项目标题重新定位、各自行绿色领取状态消失且批量按钮不可用共同证明。
- 对奖励关闭后的旧帧进行有界只读等待；每张不同奖励都重新识别后才提交下一次关闭，最多 6 张；任务页关闭后重新截图并确认无 blocker/歧义的 `main_city`。单次等待沿用总 deadline、停止信号与动作预算。
- 新鲜截图中出现“攻城令发布/前往”覆盖层，新增双条件 blocker。即使底层日常页模板仍命中，也不解释按钮状态或输入。

### 真实 Maa 识别回放

- 新鲜原始图 `artifacts/runs/dryrun-20261007T080439Z-196708/before.png`：日常页身份成立；一键领取模板 `1.000`；四个来源标题均 `1.000`；行按钮分数 `1.000`、`0.991684`、`0.993249`、`0.995040`，均超过 `0.97` 阈值。
- 旧灰态负样本 `artifacts/runs/dryrun-20261007T053223Z-180312/before.png`：同一一键领取模板得分 `0.938210`，低于阈值，不命中。
- 保存的实机奖励图 `artifacts/runs/run-20261007T025546Z-201920/after.png`：被新日常奖励前景识别为 `daily_reward_popup`；关闭提示得分 `0.982079`。这是旧邮件奖励样本的识别回放，不是日常领取证据；日常新奖励弹窗仍未实机采集。
- 覆盖负样本 `artifacts/runs/dryrun-20261007T082025Z-177336/before.png`：公告标题及“前往”两项均得分 `1.000`，阻断优先；普通日常图未命中该 blocker。最新 CLI dry-run `dryrun-20261007T083623Z-199088` 返回 blocker、领取候选 `verified=false`，输入 0。

### 回归与实机执行结果

针对性命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_daily_rewards.py -p no:cacheprovider -o addopts= -q
```

结果：`18 passed in 39.82s`，退出码 0。回归覆盖干运行零输入、前景 blocker 拒绝、一次批量提交、未知奖励物品可安全关闭、连续奖励、相同内容奖励需观察过渡帧、旧帧不重复关闭、来源项目后置状态、重复运行 0 领取、结果不确定不重试等。状态机测试用 FakeBackend；它只验证分支逻辑，不代表实机成功。

实机命令：

```powershell
.\.venv\Scripts\python.exe -m sgbltx_helper run --profile config/local-test.json --task daily_rewards_claim --dry-run
```

完整测试命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q
```

结果：`243 passed in 91.59s`，退出码 0。定向与完整测试均在当前代码上通过。

- `dryrun-20261007T080439Z-196708`：新鲜日常页；零输入。之后页面出现公告遮挡。
- `dryrun-20261007T082025Z-177336` 与 `dryrun-20261007T083623Z-199088`：公告阻断命中；零输入。期间没有运行 execute，没有提交任何领取。
- 当前不执行“前往”或用背景按钮点击绕过阻断。领取前状态虽曾清晰显示四个可领奖励，但最新截图被前景覆盖；必须由用户关闭该公告并使新鲜 dry-run 再次显示无 blocker、四项按钮和批量按钮当前命中后，才能继续唯一一次 execute。

### 状态与未验收

本轮日常一键领取 execute、奖励弹窗批量关闭、任务页退出、主城最终确认及主城重复运行领取 0 均未实机执行。没有日常奖励后置状态截图；不得把旧 0/23 或邮件奖励回放当作后置证据。批后项目确认、连续奖励及不确定安全收尾目前由针对性 FakeBackend 测试覆盖，仍需真实奖励布局验证。

截至 `dryrun-20261007T083623Z-199088` 的历史状态：**partial**。当时的报告要求用户手动关闭公告；此要求已由本报告末尾记录的空白点击容错修正和实机收尾结果替代。旧 `8782` 网页服务未使用或重启；本轮依照要求只使用 CLI。

## 2026-10-07：随机公告容错、奖励弹窗识别修复与实机结果

### 公告容错

- 用户确认“攻城令发布/前往”是随机公告，允许点别处空白处跳过后继续。工作区 `AGENTS.md` 记录了后续任务约束：永不点击“前往”；空白目标须按底层页面分别验证；只尝试一次，重新截图确认公告消失后继续；dry-run 保持只读；没有对应页面已验证目标时停止。
- 日常任务页新增 `daily_announcement_dismiss_blank.png`，取自标题左侧装饰横线，模板 ROI `[280,210,150,80]`，阈值 `0.97`。真实 Maa 5.14.2：公告图 `dryrun-20261007T083623Z-199088`、`dryrun-20261007T082025Z-177336` 均得分 `1.000`，框中心 `[350,245]`；干净日常页也会命中 `0.999579`，故此素材本身不授权输入。只有底层 `daily_tasks` 已验证且双条件公告 blocker 精确命中时才可点一次；邮件/奖励页负样本得分 `0.308922`、`0.311437`，未命中。
- 回归覆盖空白目标已验证后只点一次并继续、目标未登记时不输入、公告点后仍可见不重试，以及真实截图正负样本。

### 新鲜实机执行与保留的不确定结果

- 新鲜只读 `dryrun-20261007T093400Z-193804`：页面身份为 `daily_tasks`；公告标题/前往 blocker 均 `1.000`；批量领取候选未验证；输入 0。
- 首次执行 `run-20261007T093435Z-203824`：先在 `[350,245]` 点击一次已识别空白装饰横线，`after.png` 新截图确认公告消失；新鲜重识别确认四个来源行及绿色按钮、顶部“一键领取”，随后只提交一次批量领取。结果为 `needs_attention/outcome_uncertain`，`action_count=3`，领取 1、关闭类 2、导航 0，`claim_verified=false`、`post_batch_state_verified=false`、`cleanup_verified=false`。原始 `result.json` 未修改，禁止重复提交领取。
- 该首次执行还暴露前景分类缺陷：领取后奖励标题模板命中 `0.998407`，旧关闭提示分数 `0.952639` 低于 `0.97`，分类器却返回背景 `daily_tasks`。状态机没有识别奖励弹窗，随后点到了背景日常页返回箭头，弹窗仍在；这次实际的额外点击已由 events 和截图保留。不能将该次执行称为成功，也不能将其输入解释为完成的项目领取。
- 用真实 `after_3.png` 增加前景回归并修正分类器：部分命中的前景目标现在会压过完整的背景页面身份，且把日常列表页角色调整为 entry，使完整识别的奖励前景优先。新关闭模板 `daily_reward_dismiss_hint_live.png` 从本次真实提示裁剪，ROI `[330,1080,420,110]`、阈值 `0.97`；新日常奖励截图得分 `1.000`，旧已验证邮件奖励样本得分 `0.986487`，正常日常列表未命中 `0.360119`。已增加“从奖励弹窗开始只做 cleanup-only”分支，绝不进入领取路径。
- 新鲜 dry-run `dryrun-20261007T100159Z-197816`：当前前景分类为 `daily_reward_popup`；标题 `0.998`、关闭提示 `1.000`；0 输入。
- 安全收尾运行 `run-20261007T100238Z-203128` 是独立 cleanup-only：领取 0，关闭奖励 1 次、关闭日常页 1 次，总输入 2；确认 `cleanup_verified=true`、`final_page_id=main_city`。奖励弹窗物品素材未命中，故 `unverified_reward_popup_count=1`；本次仍返回 `needs_attention/outcome_uncertain`，没有改写首次领取结果。

首次不确定领取后没有再次运行领取检查，避免重复提交。`4/23` 只表示已完成任务数量。奖励内容显示本身、缺少来源项目批后状态、以及本轮开始时随机公告打断后的整体自动完成均不能替代成功判据。因此本模块仍为 **partial**：保留首次不确定领取，未完成同一任务从主城重复运行零领取验证；当前已由安全收尾回到主城。

本轮验证：`tests/test_daily_rewards.py -k 'live_daily_reward_overlay or known_announcement or starting_on_daily_reward_popup'` 定向用例通过；完整套件末次结果见下方最终验证记录。未启动旧 `8782` 管理网页服务，也未向其提交作业。

### 最终验证记录

- 针对性回归：`.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q tests/test_daily_rewards.py -k 'starting_on_daily_reward_popup or live_daily_reward_overlay or known_announcement'`：`6 passed, 18 deselected in 5.06s`。
- 完整套件：`.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q`：`249 passed in 96.92s`，退出码 0。
- 新鲜只读检查：`.\.venv\Scripts\python.exe -m sgbltx_helper run --profile config/local-test.json --task daily_rewards_claim --dry-run`，`dryrun-20261007T093400Z-193804`（被公告挡住，零输入）以及 `dryrun-20261007T100159Z-197816`（识别奖励弹窗，零输入）。
- 单次 execute：`.\.venv\Scripts\python.exe -m sgbltx_helper run --profile config/local-test.json --task daily_rewards_claim --execute`，`run-20261007T093435Z-203824`；仅提交一键领取一次，原始 `result.json` 是 `needs_attention/outcome_uncertain`，不重复领取。
- 收尾 execute：同一 CLI 命令，`run-20261007T100238Z-203128`；cleanup-only，claim 0、close 2、总输入 2，`cleanup_verified=true`、`final_page_id=main_city`，状态仍为 `needs_attention/outcome_uncertain`。
- 本轮 CLI 输出持续包含 Maa 方向探测子命令 `grep` 错误；但设备识别、截图保存、模板识别、Maa 控制器输入和结果工件均正常返回。未改动旧网页服务，也没有运行重复领取检查。

## 2026-10-07 主聊天复审与网页补充

已核对上述两份原始结果与截图：出现奖励、日常页公告跳过、独立安全回城均有证据，但不能合并成一份完整成功结果。来源项目后置真实布局尚未支持，模块保持 partial。

复审修复公告部分命中仍放行背景、按钮未命中冒充领取成功、收尾覆盖停止/超时以及终态后旧帧追加输入。领取成功/已无奖励必须有明确后置正样本；当前生产资源没有登记，不使用 fake 测试标记替代实机验证。

网页补充日常模块范围、公告跳过限制、领取确认/批后状态/回城的独立展示、cleanup-only 说明和活动分类计数。完整测试由根聊天实跑 `258 passed in 98.78s`，退出码 0。本次未操作真实游戏。

旧 8782 服务只含旧模块注册。自动审批阻止停止及新服务启动，均未执行；已提供 `start-dashboard-daily.cmd` 在 8783 由用户启动，再检查实际页面。详见工作区 `docs/reviews/2026-10-07-daily-rewards-review.md`。

## 2026-10-07 原生一键循环与公告变体补充

### 代码变更

- `src/sgbltx_helper/daily_rewards.py`：从固定四个任务行改为已验证日常页身份 + 顶部绿色/灰色一键按钮状态。起始明确灰态时零领取回城；每轮最多提交一次，必须先看到并关闭本轮奖励、重新识别日常页仍为绿态才允许下一轮。最多 5 轮，每轮最多处理 6 张奖励弹窗，共用动作预算和整轮截止时间。轮数/预算上限回城后报 `partial_progress`；未知奖品可安全关闭，但业务确认保持 false；旧奖励弹窗起始仍为 cleanup-only。
- `resource/daily_rewards/daily_rewards.json`、`resource/daily_rewards/pages.json` 与素材 `daily_claim_all_disabled.png`、`daily_unrelated_announcement_defense_title.png`：登记真实灰态按钮及“守城令发布/前往”公告变体。部分 blocker 线索仍阻止背景操作；完整识别的公告可与日常页底图区分。公告只允许页面专属空白目标一次，绝不点“前往”。
- `dashboard/registry.py`、`dashboard/jobs.py`、`dashboard/static/app.js`：去掉固定四项范围文案；显示最多 5 轮、灰态停止，以及本轮/历史轮次的确认、不确定和进行中数量。
- `tests/test_daily_rewards.py`、`tests/test_dashboard.py`、`tests/dashboard_app_probe.cjs`：覆盖两轮 green→reward→green→reward→gray→home、起始灰态、冲突/未知、轮数上限、未知奖品、未见奖励不得重提、停止后不追加输入、真实公告变体和网页轮次展示。真实 Maa 测试明确展示：灰态素材会匹配被遮暗的奖励背景，必须先由前景页面身份授权解释。

### 验证

- 新增的两条核心循环回归在实现前先运行，旧代码按预期 `2 failed`（仍要求四行来源项目，且无法用显式灰态完成 `nothing_to_claim`）。
- 日常回归：`.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q tests/test_daily_rewards.py`：`40 passed in 42.30s`。
- Dashboard 定向进度：`.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q tests/test_dashboard.py -k 'active_job_snapshot_tracks_real_event_stage_and_classified_input_counts or progress_counts_preserve_unknown_zero_and_action_upper_bound'`：`4 passed, 40 deselected in 0.53s`。
- app.js Node 探针：`node tests/dashboard_app_probe.cjs src/sgbltx_helper/dashboard/static/app.js`：退出码 0，覆盖模块默认关闭、活动/终态轮次及历史读取不启动任务。
- 完整测试：`.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q`：`267 passed in 94.53s`，退出码 0。
- Maa 5.14.2 离线真实截图回放：灰按钮分数 `1.000` 命中；绿色页 `0.943104` 未命中；公告页 `0.943022` 未命中；邮件页 `0.102224` 未命中；奖励前景下背景灰按钮仍可达 `0.999635`，但前景识别为 `daily_reward_popup`，不会授权日常灰态。新“守城令发布”标题 `0.999999`、“前往”`1.000`，AND 分类为日常页 + 公告 blocker 且无歧义；公告外空白框中心 `(350,245)` 得分 `1.000`。

### 新鲜检查与实机零领取收尾

- 修复前 dry-run `dryrun-20261007T125433Z-196872` 捕获日常页被“守城令发布/前往”公告覆盖，旧标题模板得分 `0.964`；零输入，原截图保留。
- 修复后 dry-run `dryrun-20261007T130822Z-192288`：页面 `daily_tasks`，公告 blocker 已确认，领取候选未验证，输入 0。
- CLI execute `run-20261007T130853Z-209804`：点公告外空白一次，重拍确认公告消失和按钮灰态，再点已识别的日常页返回箭头；`after_2.png` 确认主城。结果 `nothing_to_claim`，`claim_action_count=0`、`dismiss_action_count=2`、`action_count=2`、`claim_request_submitted=false`、`announcement_dismissed=true`、`cleanup_verified=true`、`final_page_id=main_city`。领取前、公告消失后的日常页和最终主城截图，以及事件/结果，保存在 `artifacts/runs/run-20261007T130853Z-209804/`。
- 后续 dry-run `dryrun-20261007T131327Z-205172`：`page_id=main_city`，零输入，重复确认实机最终状态仍是主城。
- Maa CLI 每次继续打印一个 ADB 方向探测子命令错误；设备选择、截图、识别、输入与工件均成功。本轮未提交网页任务。

### 状态边界

旧不确定领取 `run-20261007T093435Z-203824` 与 cleanup-only `run-20261007T100238Z-203128` 原始结果未修改。本轮没有新的绿色奖励条件，未提交领取，也没有真实奖励内容、连续奖励或正向领取循环的实机证据；模块仍为 **partial，等待新奖励**。本次只实测了公告容错、灰态零领取和返回主城。

端口 8783 当前仍由 PID `192816` 提供服务。只读 `/api/state` 返回旧的日常模块文案，没有 `max_batch_rounds`；未向该服务提交任务、未停止或强杀。代码和 Node 回归已更新，但本轮没有浏览器点测；需从原服务终端正常重启后，网页才会显示新文案和轮次计数。


## 2026-10-21 有效奖励实机验收

主城身份改为固定界面（顶部资源栏 + 左下“任务”入口，`min_mean 60`），与封地包一致：封地产业收取会让镜头停在被点的产业，旧城墙标记失配会使本任务无法启动（`dryrun-20261021T103312Z-48096` 城墙 0.41）。测试假截图改为中灰以模拟无遮罩画面。

| 运行 | 结果 |
| --- | --- |
| `run-20261021T105040Z-9260` | **`completed/claim_confirmed`**：主城 → 任务 → 日常任务（1/23，顶部绿色一键领取，奇珍异宝可领）→ 一键领取 1 次 → 奖励弹窗（粮草 10000、珍宝强化箱 1）关闭 1 次 → 顶部灰态 → 返回箭头 → 主城。导航 2、领取 1、关闭 2 |
| `run-20261021T105107Z-43780` | 复跑 `nothing_to_claim`：明确灰态，未提交领取，回到主城 |

状态：**已实机验收**。
