# Person B：联合随机建模

入口：`person_b_model_comparison.ipynb`。在 VS Code 中选择装有 pandas、numpy、scipy、matplotlib 和 pyarrow 的 Python 内核（当前已验证 `/opt/anaconda3/bin/python`）。

当前比较八种会话生成模型：参数独立边际、经验独立边际、条件配对抽样、圆周近邻、联合 KDE、Gaussian copula、整条会话 bootstrap、日历／近期加权 bootstrap。
`person_b_models.py` 包含数据适配、时间切分、训练、验证调参、生成和评估。
Person A 的原始 notebook 不改动。本模块不会输出或冒充 Person A 的 `clean_sessions.parquet`。

## 输入约定

优先自动寻找 `codespace/clean_sessions.parquet`、根目录 `clean_sessions.parquet`，否则读取现有 `raw_acn/all_sessions_PARTIAL.csv`。
也可用 `--input` 指定文件。必须包含 `siteID`、`connectionTime`、`disconnectTime`、`kWhDelivered`；时间必须是 UTC 或带时区，未带时区的值按 UTC 解释。可选 `_id` 用于去重。
当地时间目前固定为 America/Los_Angeles，仅用于现有三个 ACN 站点。

默认 Caltech（0002）；JPL 用 `--site 0001`，Office 1 用 `--site 0019`。不同站点请用不同输出目录。
当前正时长、正交付电量目标排除无效/非有限值和重复 ID，保留全部有效尾部；详细数量在运行元数据中。

## 命令行

在项目根目录：

```bash
/opt/anaconda3/bin/python codespace/person_b_models.py --repeats 10
/opt/anaconda3/bin/python codespace/person_b_models.py --input /path/to/clean_sessions.parquet --output codespace/person_b_clean_results
/opt/anaconda3/bin/python -m unittest discover -s codespace -p 'test_person_b_models.py' -v
```

再次运行会覆盖指定结果目录中的同名产物，原始数据不变。
结果在 `person_b_results/`：模型选型报告、逐随机种子指标、均值/标准差、验证分数、运行元数据、覆盖审计、比较图和**验证集优选模型**的一份合成样本。
图片使用第一个测试随机种子，仅作示例。数值指标使用所有随机种子和全部有效尾部。

## 正确解读

- 60/20/20 按观测日期切分；配置和优选方法使用两段向前验证窗口选择，评估前用训练+验证重新拟合。复用上一轮已经看过的评估期，因此仍是探索性实验，最终结论需要新的留出数据。
- 八种模型都给定 weekday/weekend；bootstrap 是**会话级**，还不是整日/用户 clustered bootstrap。
- 模型不读取测试会话的 arrival、dwell、energy 来生成，但**使用真实测试日期和每日条数**。这是评估会话变量的 oracle-count 实验，不能当作完整到达过程预测。
- 不完整下载无法确定真实零到达日。到达数量模型需要 Person A 的覆盖表后另做实验。
- 占用是已插枪车辆数，不是队长、电力负荷或容量受限模拟。
- Gaussian copula 的线性小时不能完整表达午夜周期；KDE 使用 sin/cos 时间嵌入和对数正变量。
- requested energy、post-charge dwell、用户效应、外推、排队与 abandonment 留待后续扩展；不得把 delivered energy 当作 requested energy。
- 测试排名只是明确的启发式评估，不应单独作为最终选型。先看依赖结构和占用，再检查尾部与模型复杂度。

## 完整 Git 下载与部分 API 导出

本地静态目录实际有 85,877 个 `.csv.gz`，逐目录数量与该目录 README 一致。这是文件数量核对，不是每个压缩文件的完整性校验。
这份静态仓库与 `raw_acn/all_sessions_PARTIAL.csv` 是不同快照：前者是充电测量时间序列，后者是 API 会话字段和用户输入。
模型已经读取了指定 CSV 的全部行。`partial` 来自 API 导出状态，不表示程序没读完 CSV，也不表示 Git clone 缺文件。
静态数据不能通过把最后一个测量时间直接当拔枪时间就无损替代会话表。当前不自动推断这些字段。

## 优化后的验证

15 项合成数据检查覆盖日期泄漏、跨午夜占用、微秒时间精度、缺失 ID、夏令时、退化 KDE、独立基线、配对关系和近期加权。
可视化拆成较大的 arrival–dwell 与 dwell–energy 面板，另有指标仪表板。误差条仅为不同生成种子的标准差。
新增 conditional KS 用固定4小时时段检查组内分布；样本不足20条的时段不参与，因此还需要检查稀疏组。
实现中使用的包版本已记录在 `run_metadata.json`。

补充运行使用 `raw_acn/office/all_sessions.csv` 的完整 Office 1 API 导出，结果在 `person_b_office_results/`，也已在主要 notebook 中汇总。验证选出90天半衰期，Caltech选出30天；两站点不共用固定参数。Office只有7条周末会话，不能验证独立周末模型。

```bash
/opt/anaconda3/bin/python codespace/person_b_models.py --site 0019 --input raw_acn/office/all_sessions.csv --output codespace/person_b_office_results --repeats 10
```
