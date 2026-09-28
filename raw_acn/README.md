# ACN 原始会话数据

来源：https://ev.caltech.edu/api/v1/sessions/{site}
站点：caltech、jpl、office001。无日期、电量筛选，不包含 /ts 时间序列。

- pages/：每次 API 响应的原始 JSON，包含分页元数据，可断点续传。
- *_sessions.json：各站点所有分页的 _items 合并成 JSON 数组。
- all_sessions.json：三个站点记录合并；不删除、去重、填补或转换日期。
- all_sessions.csv：一行一条 API 会话记录，字段取所有记录的并集。嵌套对象/列表保留为 JSON 字符串。CSV 中 null 和缺失字段显示为空单元格；精确信息以 JSON 为准。
- manifest.json：各站点条数、页数、API 总数及重复 ID 检查。

完整性仅指本次 API 查询可提供的记录，不代表静态时间序列仓库的所有车库或时间段。不推断缺失的用户输入，不从时间序列计算会话字段。
