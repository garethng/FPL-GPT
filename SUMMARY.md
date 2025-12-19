# 修改摘要（2025-12-19）

本次修改聚焦 `fpl_price_monitor/fetch_and_notify.py` 的飞书推送内容与合并逻辑。

## 行为变化

- **合并发送**：原先按数据源分别发送消息，改为把 `ffhub / fix / livefpl` 三个数据源的结果**按球员合并**后，仅发送**一条**飞书消息。
- **合并更稳**：合并时统一使用“去重音/去空白/小写化后的姓名 + 球队”作为合并键，避免同一球员在不同源里因 `MID/Midfielder`、`Ekitike/Ekitiké` 等差异被拆分（也避免不同源 ID 口径不一致导致无法合并）。
- **筛选规则**：
  - `ffhub / fix`：仅保留 `change time` 包含 `tonight` 的球员（不再包含 `tomorrow` 等）。
  - `livefpl`：仅保留 `progressTonight` 的绝对值 **> 100** 的球员（不依赖 `change time`）。
- **消息格式**：按 **上涨 / 下跌** 两个分组输出；每个球员在“价格”后标注来源集合：`价格(来源1,来源2,...)`。
- **移除字段**：消息中不再展示 `progress`、`progress_tonight`、`change time`（其中 `change time` 仅用于 `ffhub/fix` 的 tonight 筛选，不对外展示）。
 - **展示细节**：采用“编号 + emoji + 两段式详情”的排版，并把 **数据源列表追加在位置之后**（例如 `- MID (ffhub,livefpl)`）。

## 输出示例（结构）

- 上涨
  - `球员A 价格(ffhub,fix) ...`
  - `球员B 价格(livefpl) ...`
- 下跌
  - `球员C 价格(ffhub,livefpl) ...`


