# ui-action-diet · 界面操作瘦身审计

把"按钮太多、不知道点哪、看着晕"这类评审反馈变成可执行的审计与改版流程：
盘点可见操作 → 分级（主/次/高级/冗余/死）→ 按渐进披露 playbook 瘦身 → 复审计对比。

## 来源与出处

- **来源**：原创方法论。起因是缅甸地缘风险智能分析系统的中期评审反馈
  （"按钮很多，不知道怎么用，太多了有些晕"），无外部 SKILL.md 或第三方仓库。
- **Logo**：`assets/avatar.svg` 为原创矢量图（漏斗隐喻：多进少出）。
- **案例**：`skills/action-diet/references/reduction-playbook.md` 中的 P1–P8
  案例取自该项目 2026-06 至 2026-11 的五代前端迭代实测。

## 包含内容

| 路径 | 说明 |
|------|------|
| `skills/action-diet/SKILL.md` | 审计与瘦身工作流（预算规则 + 四步流程） |
| `skills/action-diet/scripts/count_actions.py` | 零依赖可见操作计数脚本（标准库 html.parser） |
| `skills/action-diet/references/reduction-playbook.md` | P1–P8 瘦身模式与验收清单 |
| `assets/avatar.svg` | 插件图标 |

## 使用

```bash
# 盘点一个项目的模板目录（静态模式：闭合 details/隐藏容器内控件不计可见）
python skills/action-diet/scripts/count_actions.py templates/ -o action_report.md

# 首屏实测模式：Playwright 打开真实页面，统计 900px 折叠线以上可见控件（含 JS 渲染）
python skills/action-diet/scripts/count_actions.py --live http://127.0.0.1:5000/ http://127.0.0.1:5000/literature
```

输出每页的按钮/按钮化链接/下拉/输入/折叠/导航链接计数与"可见操作合计"，
合计 > 7±2 的页面按 SKILL.md 的 Step 2–4 分级瘦身。

## 省略与限制

- 不含 Playwright 首屏折叠线精算（SKILL.md 中列为可选步骤，未附脚本），
  静态计数对 Jinja 模板已足够定位超标页面。
- 不含 hooks/MCP/commands/agents：单一审计工作流，无需额外组件。

## 校验

- `python3 <create-plugin>/scripts/validate_qoder_plugin.py plugins/ui-action-diet` → 通过
- `count_actions.py` 对缅甸项目 `templates/` 实跑成功；`--live` 模式对 6 个真实页面实测首屏操作数均 ≤ 7±2。
- 实战记录：缅甸项目文献中心首屏可见操作 11 → 5（docs/action_audit_baseline.md）。
