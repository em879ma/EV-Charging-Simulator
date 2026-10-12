# ACN 原始会话数据

来源：https://ev.caltech.edu/api/v1/sessions/{site}
站点：caltech、jpl、office001。无日期、电量筛选，不包含 /ts 时间序列。

下载：`python download_acn_raw.py --token-file .acn_token --output .`（或设环境变量 `ACN_API_TOKEN`）。重跑时从 pages/ 的缓存续传。

## 完整下载（2026-10-10，三个站点都完整）

| 站点 | 会话数 | API 总数 | connectionTime（UTC） |
|---|---|---|---|
| caltech | 31,424 | 31,424 | 2018-04-25 至 2021-09-14 |
| jpl | 33,638 | 33,638 | 2018-09-05 至 2021-09-14 |
| office001 | 1,683 | 1,683 | 2019-03-25 至 2021-09-13 |

- pages/<site>/windows/<YYYY-MM>/：按月查询（connectionTime 窗口，按 connectionTime、_id 排序）的每次 API 响应原始 JSON，包含分页元数据。每个月的条数都和该月查询的 API 总数一致。
- *_sessions.json：该站点各月的 _items 合并成 JSON 数组。相邻月份共用边界那一秒，同一 _id 只保留一次（这次没有出现，见 manifest.json 的 repeated_ids_dropped）。只有唯一 _id 数等于 API 总数时才写这个文件。
- all_sessions.json：三个站点记录合并；除上述边界重复外，不删除、去重、填补或转换日期。
- all_sessions.csv：一行一条 API 会话记录，字段取所有记录的并集。嵌套对象/列表保留为 JSON 字符串。CSV 中 null 和缺失字段显示为空单元格；精确信息以 JSON 为准。
- manifest.json：各站点条数、API 总数、页数、出问题的月份、边界重复数和下载时间。

API 原样保留的格式差异：caltech 2018-10-01 至 2018-10-28 的 2,081 条记录里 siteID、clusterID 是整数（2、39），其他记录是补零的字符串（"0002"、"0039"）。读取时先转成字符串再补零（codespace/unobserved_behavior/data.py 就是这样做的）。

## 早先的部分下载（保留作对照）

- caltech_sessions_PARTIAL.json（23,200 条，到 2019-09-17）、jpl_sessions_PARTIAL.json（16,700 条）、all_sessions_PARTIAL.json/.csv、download_status.json：不分月的列表在深页处卡住时留下的部分结果。codespace/preprocess.ipynb 还在读 all_sessions_PARTIAL.csv 和 download_status.json。
- pages/caltech/、other_sites/pages/jpl/、office/：那次下载的原始分页。
- office001_sessions_COMPLETE.json：和 office001_sessions.json 是同一批 1,683 条记录，只是顺序不同。

部分下载里的每条记录都在完整文件里，而且逐字段相同。

完整性仅指本次 API 查询可提供的记录，不代表静态时间序列仓库的所有车库或时间段。不推断缺失的用户输入，不从时间序列计算会话字段。
