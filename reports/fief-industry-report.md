# 封地产业收取实现报告

日期：2026-10-21。状态：**未验收**。代码与离线测试完成；资源气泡及产业奖励弹窗素材待首次实机采样。

## 实现

- `src/sgbltx_helper/fief_industry.py`：有限状态控制器。主城 → 点一个资源气泡（一次领完）→ 只读等待奖励弹窗 → 逐张关闭 → 主城视野内无气泡。
- `resource/fief_industry/`：主城、顶号、两个军令公告身份照抄日常奖励资源包（已实机验证）；奖励弹窗复用邮件素材，标为未验证；`resource_bubbles` 为空。
- runner 新增 `fief_industry_collect` 分发；网页注册“封地产业收取”模块，默认关闭。
- 设计与计划：工作区根目录 `docs/superpowers/specs/2026-10-21-fief-industry-collect-design.md`、`docs/superpowers/plans/2026-10-21-fief-industry-collect.md`。

## 素材与样本

| 素材 | 来源 | 状态 |
| --- | --- | --- |
| `fief_invasion_label.png`（红底“入侵”字样） | `dryrun-20261021T070159Z-39596/before.png`，左侧入侵标记 | 已验证：入侵样本命中，卷轴标记不命中；整幅地图搜索得分 0.999999 |
| `tests/fixtures/fief_invasion_marker_roi.png`、`fief_scroll_marker_roi.png` | 同一截图裁剪，不含账号信息 | 负样本 |
| 资源气泡模板 | 无 | 待首次实机采样（用户已手动领取，聊天截图为缩放窗口图不可用） |
| `fief_reward_title.png`、`fief_reward_dismiss_hint.png` | 复制自邮件奖励弹窗 | 未在产业场景验证 |

同一截图在新资源包下真实 Maa 分类为 `main_city`，无阻断。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts= -q tests/test_fief_industry.py
```

21 passed：2 个真实 Maa 资源包/负样本用例，19 个 FakeBackend 状态机用例，覆盖正常领取、领取后动画等待、多张弹窗与上限、同一弹窗不重复关闭、无气泡、素材未验证、入侵排除、领取后无弹窗、关闭后仍有气泡、弹窗素材未验证、预先存在弹窗仅收尾、顶号/公告/未知页、dry-run 两种、停止与超时。FakeBackend 只验证状态机，不是实机证据。

## 未验收与下一步

尚无任何实机领取。下次主城出现资源气泡时按 README“首次启用步骤”采样验证，再做 execute；之后从主城复跑应零领取。
