# 操作瘦身审计报告（Action Diet）

审计工具：`plugins/ui-action-diet/skills/action-diet/scripts/count_actions.py`
口径说明：静态模式解析模板，**闭合 `<details>` 内控件不计可见**、`display:none`/`hidden` 不计、
导航链接单列；live 模式（`--live`）用 Playwright 统计首屏 900px 内真实可见控件（含 JS 渲染）。

## 一、改版前后静态对照（修正口径）

| 页面 | 改前可见操作 | 改后 | 采用模式 |
|------|-------------|------|----------|
| literature.html | 7（3按钮+3下拉+1输入，分页条另计） | **2**（搜索框+搜索按钮，高级筛选折叠） | P2 折叠+摘要、P4 智能默认、分页条按需显示 |
| chat.html | 4（含链式推理复选框外露） | **4**（复选框移入"模型设置"折叠，首屏少一个决策点） | P8 高级配置外移 |
| _nav.html 时空分析下拉 | 6 项平铺 | 6 项分两块（地图与趋势/统计与对照） | 分块 chunking，单块 ≤3 项 |
| research.html | 1（其余均在折叠内） | 1 | 无需改动（旧工具误报 8） |
| 其余页面 | ≤4 | ≤4 | 达标 |

## 二、改后 live 首屏审计（2026-11-09 实测）

| 页面 | 首屏可见操作 | 其中禁用 | 构成 |
|------|-------------|---------|------|
| / （文本分析） | **7** | 0 | 上传、填充示例、开始分析、2 折叠、输入框等 |
| /dashboard | **1** | 0 | 整卡跳转链接 |
| /literature | **5** | 0 | 搜索按钮、搜索框、3 折叠（含结果卡摘要） |
| /dashboard/indicators | **3** | 0 | 3 折叠 |
| /analysis/risk | **4** | 0 | 主按钮 1 + 3 折叠 |
| /remote-sensing | **2** | 0 | 渲染按钮 + 月份下拉 |

预算：首屏 ≤ 7±2、主操作每屏 1 个 —— **全部达标**。

## 三、工具修正记录

1. 闭合 `<details>` 内容原被计为可见 → 修正为首屏不可见（summary 除外）。
2. `<input style="display:none">` 等 void 元素曾使隐藏深度永不恢复 → void 元素不入栈。
3. 隐藏容器深度原只进不出 → 改为标记栈，闭合标签正确出栈。
4. 新增 `--live` 首屏模式，JS 渲染控件（如 chat 按钮）也计入。

## 四、回归证据

- 浏览器契约三件套（test_product_browser / test_home_redesign / test_browser_contracts）：41 passed。
- 全量 pytest：466 passed, 16 skipped（与改版前基线一致，零回归）。
