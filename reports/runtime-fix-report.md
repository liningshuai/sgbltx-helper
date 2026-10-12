# 运行器缺陷修复报告

日期：2026-10-05。范围：仅修复第一阶段运行器与其交付文件；没有登录、控制真实游戏或实现领取流程。

## 修改与验证

| 问题 | 修改文件 | 结果 |
|---|---|---|
| R1 后置成功未绑定任务页面 | `src/sgbltx_helper/runner.py`、`tests/test_runner.py` | execute 要求前置页面等于已配置的任务目标页；后置已领标志必须来自同一目标页且素材已验证。其他页面、仍有可领取项或未验证标志均不能返回 `completed`。定向回归覆盖跨页已领、后置素材、剩余目标和正常目标页闭环。 |
| R2 同设备被不同别名绕过 | `src/sgbltx_helper/locking.py`、`tests/test_control.py` | 文件锁键改为设备序列号，别名保留在锁元数据中。同设备 `main`/`alt` 互斥；不同设备可并行。当前不额外建立跨设备账号别名锁。 |
| R3 静态预算低估 SDK 输入 | `src/sgbltx_helper/runner.py`、`src/sgbltx_helper/maa_adapter.py`、`tests/test_runner.py` | SDK 提交前先验证 Pipeline。当前安全子集只接收有限、线性、无汇合的 `TemplateMatch` 节点和 `DoNothing`/`Click`，按可达输入节点给出上界；循环、`repeat`、错误分支、未知字段/动作、分支汇合、不可达节点及不支持格式均拒绝。SDK 后的输入记录只作审计；记录缺失时以预先证明的静态上界记录并标明上界，避免将已发生动作记成 0。 |
| R4 超时不覆盖整轮 | `src/sgbltx_helper/runner.py`、`src/sgbltx_helper/maa_adapter.py`、`src/sgbltx_helper/classification.py`、`tests/conftest.py`、`tests/test_runner.py` | deadline 在运行入口建立，并传给设备发现、连接、截图、资源加载、逐模板识别、Pipeline 与后置验证；SDK 作业等待按同一 monotonic 截止时间轮询，并检查停止标记。新增延迟作业的实际等待测试，覆盖累计耗时超限、识别超限、Pipeline 超限、后置截图超限及连接等待中停止。 |
| 缺少基本交付与本机路径 | `README.md`、`.gitignore`、`requirements.lock.txt` | 添加 Windows/Python 环境与安装、使用边界、测试说明；忽略 `.venv`、运行产物、`config/local.json`、临时探针和私密截图；锁文件 editable 项由本机绝对路径改为项目相对 `-e .`。 |

## 验证记录

- 修复前定向回归复现：跨页面的 unrelated 已领标志返回 `completed`；同设备使用不同 alias 可同时取得锁。对应测试在修改前失败。
- 完整回归命令：

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q
  ```

  结果：复审补丁后 `92 passed in 11.87s`，退出码 0。
- 锁文件安装解析检查：

  ```powershell
  .\.venv\Scripts\python.exe -m pip install --dry-run --no-deps -r requirements.lock.txt
  ```

  结果：退出码 0；pip 将 `-e .` 解析到当前项目并识别 `sgbltx-helper==0.1.0`。这是 dry-run，没有安装或替换现有虚拟环境。

## 尚有限制

- 当前 Pipeline 验证器刻意只支持很小的线性子集；有限 `repeat` 也因 Maa 对其执行语义尚未在本任务核实而拒绝。扩展动作或分支前需依据锁定版本实测并增加可证明的输入上界。
- Maa 的任务超时/停止会请求 `post_stop`，然后最多额外等待 10 秒确认作业结束。该确认等待可能超过整轮业务 deadline；若 SDK 未在这段时间内结束，Python 侧不能强制中止原生调用。控制器连接、`post_*` 本身、结果读取以及 Toolkit 设备发现中的同步原生代码也没有进程隔离，不能承诺硬实时退出。
- 发生 Pipeline 超时或用户停止时，已经提交的输入无法回滚；结果保持待人工核对。总超时使后置证据尚未取得时，不会推断领取成功。
- 真实运行器仅有历史重复登录阻断记录，`action_count=0`；本轮没有真实领取证据。资源 Pipeline 仍为占位流程，邮件奖励页面与素材尚未制作或验证。本报告不代表真实领取任务完成。

## 复审 F1—F3 修复

- **F1 后置识别异常：** `runner.py` 在保存 `after.png` 后、调用后置 `classify` 前建立完整 evidence 列表。后置识别抛 `MaaTimeout` 或 `MaaStopped` 时，返回并落盘 `TaskResult`，保留 after 截图与事件路径。`tests/test_runner.py` 覆盖两种抛出路径；修复前均复现 `UnboundLocalError`。
- **F2 SDK Pipeline 合并：** `maa_adapter.validate_pipeline_bundle` 拒绝资源树内任意 `default_pipeline.json`/`.jsonc`、额外/嵌套 Pipeline `.json`，并要求资源包只含与任务 ID 对应的 Pipeline 文件。运行器在预算前验证，`MaaBackend.run_task` 在 `post_task` 前再次验证。第二轮补齐见下文：额外 Pipeline 的 `.jsonc`（含嵌套目录）也在提交前拒绝；此安全子集执行仅允许单个任务 `.json` 文件。
- **F3 停止未确认：** `MaaBackend` 不再吞掉 `post_stop` 异常。收到停止或 Pipeline 超时后，最多等待 10 秒，分别确认 stop job 成功完成、原任务 job 完成且 Tasker 不再运行；任一失败均抛 `MaaStopUnconfirmed`，不会返回普通 `cancelled`。运行器返回 `needs_attention/stop_unconfirmed` 并写入设备禁用标记 `artifacts/locks/<设备锁名>.stop-unconfirmed.json`。后续运行会在连接设备前拒绝。只有人工核查设备任务已停止并完成恢复后，才调用 `RunLock.clear_stop_unconfirmed(lock_dir, device_serial)` 清除标记。适配器测试覆盖成功停止、stop 请求失败、stop job 失败、stop job 完成但 task job 未结束；运行器测试覆盖下一轮受阻与显式恢复。
- F1—F3 与已有回归的完整验证：`.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q`，结果 `92 passed in 11.87s`，退出码 0。F2 真实 Maa Resource 用例与 F1/F3 定向用例也包含在完整套件中。

停止确认仍有明确限制：确认窗口最多额外 10 秒，可能超过任务 deadline；Maa 原生同步调用不能由 Python 强制中止。确认失败时设备被持久禁用，不能仅靠再次运行解除。没有真实设备输入或领取操作。

## 第二轮复审 S1—S2 补齐

- **S1 锁后停止标记复查：** `src/sgbltx_helper/locking.py` 保留取得文件锁前的快速标记检查，并在成功取得原生文件锁后、写入锁元数据前再次检查。若标记在两次检查之间出现，释放并关闭刚取得的锁，再抛出 `StopUnconfirmedError`。`tests/test_control.py::TestProcessMutex::test_stop_marker_written_during_lock_acquire_blocks_second_run_and_releases_lock` 用线程事件确定性暂停第二个真实 Windows 文件锁尝试；首轮持锁写入禁用标记并释放后，第二轮被拒绝。测试清除标记后让第三个锁实例成功取得并释放同一锁文件，验证失败路径没有遗留锁资源。
- **S2 JSONC Pipeline 资源边界：** `src/sgbltx_helper/maa_adapter.py::validate_pipeline_bundle` 现递归枚举 `pipeline/` 下 `.json` 与 `.jsonc`（后缀大小写不敏感），并拒绝除指定任务 `.json` 外的所有文件；也拒绝资源树内的 `default_pipeline.jsonc`。`tests/test_control.py::TestPipelineResourceBoundary::test_sdk_loaded_extra_jsonc_is_rejected_before_post_task` 分别用 MaaFw 5.14.2 的真实 `Resource` 加载 `pipeline/extra.jsonc` 和 `pipeline/nested/extra.jsonc`，确认资源已加载，但 `post_task` 调用数为 0。测试只离线加载资源，没有创建 Controller 或连接设备。
- **先失败后修复：** 新增的 S1 并发用例修复前观察到第二轮结果为 `acquired`；S2 两个真实 SDK 用例修复前均未抛资源边界错误。补齐后定向验证命令：

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q tests/test_control.py::TestProcessMutex::test_stop_marker_written_during_lock_acquire_blocks_second_run_and_releases_lock tests/test_control.py::TestPipelineResourceBoundary
  ```

  结果：`4 passed in 1.40s`，退出码 0。
- **全套回归：**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q
  ```

  结果：`95 passed in 12.35s`，退出码 0。
- **边界：** 资源检查覆盖 `pipeline/` 下所有层级的 `.json`/`.jsonc`，以及资源树内名称为 `default_pipeline.json` 或 `default_pipeline.jsonc` 的文件。没有扩展 SDK 格式白名单，也未操作真实游戏。Pipeline 仍为占位流程，本轮不构成真实领取验证。
