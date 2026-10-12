import json
import re
import sys

A = json.load(open('acn_fits.json'))


def best(v):
    b = v['best_by_aic']
    return v['fits'][b]['promodel'].split(' [')[0].split(';')[0], v['fits'][b]['ks']


def table_mean(points):
    """Mean of the piecewise-linear inverse CDF through the (cumulative %, value) points, as ProModel samples it."""
    return sum((p2 - p1) / 100 * (v1 + v2) / 2 for (p1, v1), (p2, v2) in zip(points, points[1:]))


def table_bias(sections, points=None):
    """Percent gap between table mean and sample mean for the empirical groups (KS > 0.1) of the given sections."""
    out = []
    for sec in sections:
        for v in A[sec].values():
            if best(v)[1] > 0.1:
                pts = [pv for pv in v['table_points'] if points is None or pv[0] in points]
                out.append(100 * (table_mean(pts) / v['mean'] - 1))
    return out


def dist_rows(section, unit):
    rows = []
    for k, v in A[section].items():
        pm, ks = best(v)
        use = '拟合分布 ' + pm if ks <= 0.1 else '经验表（分位点见附录）'
        rows.append('| %s | %d | %.2f | %s | %.3f | %s |' % (k, v['n'], v['median'], pm, ks, use))
    return '\n'.join(rows)


da = A['daily_arrivals']
prof = A['arrival_profile_pct_by_hour']
dep = A['announced_departure_error_min']['quantiles']
rat = A['requested_over_delivered_energy']['quantiles']
uc = A['user_class']
req = A['requested_energy_kwh']
req_ln = req['fits']['lognorm']
win = A['data']
long4 = A['share_dwell_over_4h']
above = A['sessions_above_port_rating']
DECILES = set(range(0, 101, 10))
dec_dwell, dec_energy = table_bias(['dwell_hours'], DECILES), table_bias(['energy_delivered_kwh'], DECILES)
pts_all = table_bias(['dwell_hours', 'energy_delivered_kwh'])


def pct_range(xs, sign=True):
    if not xs:
        return '无'
    f = '%+.1f' if sign else '%.1f'
    return (f + '%%') % xs[0] if len(xs) == 1 else (f + '–%.1f%%') % (min(xs), max(xs))


stations = re.search(r'(\d+) stations', win['garage']).group(1)
ASENSIO_WITHIN_4H = 0.887  # Asensio et al. 2022: sessions ending within the 4 free hours
gamma = '%.2f–%.2f' % tuple(sorted(1 - (1 - ASENSIO_WITHIN_4H) / x for x in long4.values()))
pricing = A['weekday_arrivals_before_after_pricing']
dep_median = ('%.0f' % dep['50']).replace('-', '−')
download = '**部分下载**' if 'PARTIAL' in win['source'] else '**完整下载**'
if win['window_end_exclusive'] == '2020-03-01':
    window_note = (f"只拟合 2020-03-01 之前的会话：此后 COVID 停课，车库用量骤降，到数据结束还没恢复，"
                   f"之后的 {win['sessions_left_out_after_window']:,} 个会话没有用（`fit_acn.py --end` 可改）。")
elif win['window_end_exclusive']:
    window_note = (f"只拟合 {win['window_end_exclusive']} 之前的会话（`fit_acn.py --end` 设定），"
                   f"之后的 {win['sessions_left_out_after_window']:,} 个会话没有用。")
elif win['last_day'] >= '2020-03-01':
    window_note = '拟合用到数据的最后一个完整日，包括 2020 年 3 月以后 COVID 停课、用量骤降的时期。'
    if win.get('recording_gap_days_left_out'):
        window_note += (f"{'、'.join(g.replace('..', ' 至 ') for g in win['recording_gaps'])} 的 {win['recording_gap_days_left_out']} 天 ACN 系统没有记录"
                        '（Caltech 和 JPL 同时中断），这些天不计入每天到达数，而不是算作 0 到达。')
    window_note += '只要收费后、COVID 之前的时期，用 `fit_acn.py --end 2020-03-01` 重跑。'
else:
    window_note = '拟合用到数据的最后一个完整日。'
if win['fast_charger_sessions_left_out']:
    window_note += (f"不含 2020-11 新增的一台快充桩（2-39-81-4550）的 {win['fast_charger_sessions_left_out']:,} 个会话，"
                    '模型里的接口是 54 个 Level-2 桩（`--keep-fast-charger` 可保留）。')


def to_total(values, total, nd):
    """Round to nd decimals and put the rounding residual on the largest entry, so a row sums to total."""
    vals = [round(v, nd) for v in values]
    i = vals.index(max(vals))
    vals[i] = round(vals[i] + total - sum(vals), nd)
    return vals


prof = {k: to_total(v, 100, 2) for k, v in prof.items()}  # ProModel arrival cycles must total 100 %
uc_shares = dict(zip(['repeat', 'one_time', 'unclaimed'], to_total(
    [uc['claimed_repeat_user_share_of_sessions'], uc['claimed_one_time_user_share_of_sessions'], uc['unclaimed_share_of_sessions']], 1, 3)))

md = f"""# ProModel 输入补充表（初稿）

依据：设计文档 *EV Charging Simulation System Design*（第 3.2 Attributes、3.3 Variables、3.4 Distributions），以及 Person C 的 18 个变量（R01–R18）。

## 先看这里

**来源标注**
- **ACN 拟合**：用 Caltech ACN 数据直接拟合，**只用大停车场 California Garage 01**（集群 0039，{stations} 个桩）的会话。范围是 2018-11-01 收费之后的时期，{win['first_day']} 至 {win['last_day']}，{win['sessions']:,} 个会话。数据是{download}（到 {win['data_last_day']}）；{window_note}
- **ACN 推导**：从 ACN 数据算出来，但不是直接拟合分布。
- **文献（已核实）**：调研者打开了原文并抄录原句。
- **文献（仅摘要）**：原文打不开，只在搜索结果摘要里看到，使用前要再核对。
- **假设**：没有依据，必须做敏感性扫描并报告结果。

**ProModel 写法**
- `G(形状, 尺度)`，`W(形状, 尺度)`，`E(均值)`，`P(均值)`，`T(最小, 众数, 最大)`。
- **`U(均值, 半宽)`**：不是 (最小, 最大)。例如 U(5,30) 分钟要写成 `U(17.5, 12.5)`。
- **`L(a, b)`**：文档写的是 mean 和 sd，但没说是变量本身的，还是取对数后正态分布的。下表默认按**变量本身的均值和标准差**填写，同时附上 ln X 的参数。**先在 ProModel 里抽 1 万个样本，检查均值对不对。**

以上语法来自 ProModel 帮助文档的搜索摘要（文献，仅摘要），用之前在 ProModel 里按 F1 打开帮助核对。

---

## 1. Attributes（设计文档 3.2）

| Attribute | 含义 | 分布或规则（ProModel） | 来源 |
|---|---|---|---|
| aArrival | 到达时刻 | 每天的到达数加上 Arrival Cycle 的分时百分比，见 3.1–3.2 | ACN 拟合 |
| aDwell | 停留时长 | 按日型和到达时段分组的条件分布，见 3.3 | ACN 拟合 |
| aDeparture | 实际离开时刻 | = aArrival + aDwell，不单独抽样 | 设计规则 |
| aEnergyNeed | **真实**电量需求 kWh | 按停留时长分档的条件分布（实际送出电量），见 3.4。另有约 30% 的会话拔枪时还没充完，基线把需求取为实际送出的电量，属于保守估计（R11）。抽完后截到 {above['port_kw']} kW × aDwell：档内电量和停留是分开抽的，不截的话约 6% 的会话要的电量按桩的额定功率在停留时间内也充不进去。数据里只有 {above['share']*100:.1f}% 的会话平均功率略高于额定功率（最高 {above['max_avg_kw']} kW，多出的电量占 {above['excess_energy_share']*100:.2f}%） | ACN 拟合 |
| aEnergyDelivered | 已送电量 | 初始为 0，每个时间步累加 | 设计规则 |
| aMaxRate | 车辆自身最大功率 | 离散分布：3.27 kW 概率 0.515，6.43 kW 概率 0.485，上限是接口的 6.66 kW | ACN 推导（电流曲线 n=198，R12） |
| aCompatibility | 接口兼容性 | Caltech 全部是 J1772 Level-2，**全部兼容，基线可以不用这个属性**。多站点或多接口类型时再启用 | ACN（站点配置） |
| aUserClass | 用户类型 | 离散分布：**登记的回头客 {uc_shares['repeat']:.3f}**；登记的一次性用户 {uc_shares['one_time']:.3f}；**未登记 {uc_shares['unclaimed']:.3f}**（收费后被 30 分钟规则截断，电量单独建模，见 3.4）。未登记的车大多停得短，所以先抽停留、再按停留分档定是否未登记：{' / '.join(f'{k} {v:.0%}' for k, v in A['unclaimed_share_by_dwell'].items())} | ACN 拟合 |
| aPatience | 最长愿意等多久 | 基线：等到自己的离开时刻为止（虚拟排队名单，R03）。"没耐心"情景：`G(1.70, 25.8)` 分钟，均值 43.8、标准差 33.6，扫描均值 15–60 分钟 | 文献（已核实）：Wang et al. 2024（中国高速公路快充的问卷，**可迁移性弱**）。分布形状是我们的假设 |
| aOutcome | 结果 | full / partial / abandoned / balked / lost / interrupted | 设计规则 |

**建议新增的 Attribute**（讲故事用的指标需要它们）

| Attribute | 含义 | 分布或规则 | 来源 |
|---|---|---|---|
| aEnergyReq | 用户**报的**需求电量（第一次填报） | `{req_ln['promodel'].split(' [')[0]}` kWh（{req_ln['promodel'].split('; ')[1]}，KS {req_ln['ks']:.3f}）。也可以用 aEnergyNeed × 比值，比值的分位数见附录 B | ACN 拟合 |
| aDepAnnounced | 用户**报的**离开时刻 | = aDeparture + 误差，误差按经验分位数抽（附录 B），中位数 {dep_median} 分钟，即大多数人比报的走得晚 | ACN 拟合（R10） |
| aComply | 是否遵守充完即挪车 | 伯努利分布：只有规则时 γ=0.6（**假设**，扫描 0.2–1.0）；4 小时免费、之后约 $1/h 的收费情景：γ≈{gamma}（扫描 0.75–0.95） | 规则情景：假设。收费情景：推导，见 3.6 |
| aMoveDelay | 遵守者收到"充完"通知后多久挪车 | 宽限期 15 分钟，加上 `L(24.7, 32.4)` 分钟（中位数 15，ln σ=1.0）；扫描中位数 5–60 | 假设（R07） |
| aOverstay | 不遵守者超出时限多久 | `L(73.1, 195.9)` 分钟（ln X ~ N(3.24, 1.45)，中位数 25.5），截断在本人的离开时刻 | 文献推导：Asensio et al. 2022 的公开数据，n=378 |
| aRetry | 被挡后是否再来 | 伯努利分布 p=0（基线），扫描 0–0.5；回来的间隔 `E(150)` 分钟 | 假设（R04） |
| aDoneTime / aIdleStart | 充完的时刻 | 模型内部计算：剩余电量到 0 的那一刻 | 设计规则 |
| aSessionFail | 插枪后充电失败 | 伯努利分布 p=0（基线），扫描 0–0.029。失败的会话送电 0、占用接口；占用多久需另定 | 文献（之前核实过）：EV WATTS 公共 L2 中 2.9% 的会话送电 <0.3 kWh，只作上限 |

---

## 2. Variables / Arrays（设计文档 3.3）

| 名称 | 用途 | 取值 | 来源 |
|---|---|---|---|
| vPorts | 接口数（实验因子） | 30 / 42 / 54 / 66；ACN 每月实际有会话的接口数在 {A['ports_in_service_by_month']['min']}–{A['ports_in_service_by_month']['max']} 之间 | proposal；ACN |
| vSiteLimitkW | 站点功率上限（实验因子） | 基线 150（150 kVA），扫描 20–150 kW | 文献（之前核实过）：Lee et al. 2021a；acnportal |
| vPortkW | 单个接口额定功率 | 6.66 kW（32 A × 208 V） | 同上 |
| vPolicy | 充电策略 | 1 不管控 / 2 平均分配 / 3 最早离开优先（**按 aDepAnnounced 排序**）/ 4 充完即挪车 | proposal |
| vDemandFactor | 需求倍数 | 0.8 / 1.0 / 1.2 / 1.5（乘在每天到达数上） | proposal（R17） |
| vPBalk | 满站时直接离开的概率 | 1.0（基线），扫描 0.5–1.0 | 假设（R02） |
| vHoldMin | 排队名单的保留时间 | 15（ChargePoint），或 30（Cal Poly） | 文献（之前核实过） |
| aTOU[季节, 日型, 小时] | 电价 $/kWh | SCE TOU-EV-4，见 3.7 | 文献（已核实，acnportal 文件） |
| vDemandChargeRate | 需量电费 | $15.51 /kW·月 | 文献（已核实） |
| vUserPrice | 向用户收费 | S0 免费 / S1 $0.12/kWh（Caltech 实际）/ S2 前 4 小时免费、之后约 $1/h、最低 $0.50 | S1：Lee 2019；S2：从 Asensio 数据推断 |
| vCapexPerPort / vLife / vNetworkFee | 投资与运营成本 | $5,500（$3,400–7,300，2019 年美元）/ 10 年（7–12）/ $300 每年每接口（$100–900） | 文献（之前核实过）：Nicholas 2019；Smith & Castellano 2015（R18）。这次调研打不开原文，没有重新核对 |
| vA（可用率） | 接口可用率 | 1.00 / 0.99 / **0.97（基线）** / 0.921 / 0.733，见 3.8 | 0.97 是 NEVI 法规的最低要求；0.921 和 0.733 是公共桩的数据，可迁移性弱 |
| 计数器（新增） | 讲故事用的指标 | 被挡、放弃、完全满足和部分满足的会话数；按"请求电量"和"真实电量"两种口径的未满足电量；闲置占用的接口分钟数；峰值 kW；电费；需量电费；收入；年化投资 | — |

---

## 3. Distributions（设计文档 3.4，填上具体参数）

### 3.1 每天到达数（ACN 拟合）
| 日型 | 天数 | 均值 | 方差/均值 | P10/P50/P90 | ProModel |
|---|---|---|---|---|---|
| 工作日 | {da['weekday']['days']} | {da['weekday']['mean']} | {da['weekday']['var_to_mean']} | {'/'.join(str(int(x)) for x in da['weekday']['p10_p50_p90'])} | `{da['weekday']['promodel_poisson_gamma']}` |
| 周末 | {da['weekend']['days']} | {da['weekend']['mean']} | {da['weekend']['var_to_mean']} | {'/'.join(str(int(x)) for x in da['weekend']['p10_p50_p90'])} | `{da['weekend']['promodel_poisson_gamma']}` |

`P(G(r, m/r))` 的意思是：先从 Gamma 分布抽当天的平均到达率，再用 Poisson 抽实际次数，合起来就是负二项分布，方差大于均值。ProModel 能不能这样嵌套，要先测试；不行的话改用"按天重抽历史数据"的经验表。需求倍数 vDemandFactor 乘在这里。

### 3.2 一天内的到达时间（Arrival Cycle，按百分比）
注意：在 ProModel 的 Arrival Cycle 里，每个时段内的到达时刻是**均匀分布**的。

| 小时 | {' | '.join(str(h) for h in range(24))} |
|---|{'---|' * 24}
| 工作日 % | {' | '.join(str(x) for x in prof['weekday'])} |
| 周末 % | {' | '.join(str(x) for x in prof['weekend'])} |

### 3.3 停留时长（小时，按日型 × 到达时段）
KS 大于 0.1 的组改用经验表：用 User Distribution，选 Continuous 和 Cumulative，填入附录 A 的 13 个分位点（P0–P90 每 10% 一个，再加 P95、P99、P100）。只用十分位数时，最高 10% 一段线性插值到最大值，会把均值抬高（经验表里的停留 {pct_range(dec_dwell)}，未登记电量 {pct_range(dec_energy)}）；加上 P95、P99 后偏差在 {pct_range(pts_all, sign=False)}。超过 24 小时的会话（{A['dwell_excluded_over_24h_share']*100:.1f}%）没有参与拟合。

| 分组 | n | 中位数 | AIC 最优 | KS | 建议 |
|---|---|---|---|---|---|
{dist_rows('dwell_hours', 'h')}

早上到达的组拟合较差，是因为这批人里既有短暂停留的，也有停一整天上班的，单一分布描述不了，所以用经验表。

### 3.4 电量（kWh，已登记会话按停留时长分档；未登记会话单独一类）
停留时长和电量的 Spearman 相关系数为 {A['spearman_dwell_kwh_claimed']}，所以电量要按停留时长分档抽取。

| 分组 | n | 中位数 | AIC 最优 | KS | 建议 |
|---|---|---|---|---|---|
{dist_rows('energy_delivered_kwh', 'kWh')}

### 3.5 充电功率
有效功率 = min(aMaxRate, vPortkW, 策略分配到的功率)。基线不考虑充满前的功率下降；需要时用"电量 80% 以后线性下降"（R12，ACN-Sim）。

### 3.6 司机行为（ACN 观测不到）
| 输入 | 分布 | 参数 | 来源与说明 |
|---|---|---|---|
| 满站直接离开 | 伯努利分布 | p=1（基线）；0.5–1 | 假设。Erlang 损失模型的惯例（Aveklouris 2017） |
| 排队耐心（没耐心情景） | Gamma | `G(1.70, 25.8)` 分钟 | 文献（已核实）：Wang 2024，问卷说"还能接受的等待"均值 43.8、标准差 33.6 分钟。中国高速快充场景，**偏长，可迁移性弱** |
| 排队名单的通知响应 | 对数正态 | 中位数 10 分钟，ln σ=0.8，超出保留时间就失去名额 | 保留时间来自文献；响应时间是假设 |
| 只发"充完"通知、没有规则 | 不额外建模 | 等同于 ACN 基线 | 文献（已核实）：Lee 2021a 提到大多数车本来就会通知车主充完 |
| 只有规则时的遵守率 | 伯努利分布 | γ=0.6（扫描 0.2–1.0） | **假设，H1 的关键参数**。要报告收益随 γ 变化的曲线和临界值 |
| 有收费时的遵守率 | 伯努利分布 | γ≈{gamma} | 推导：Asensio 2022 数据里，{ASENSIO_WITHIN_4H:.1%} 的会话在 4 小时内结束（免费 4 小时，之后收费）；ACN 没有时限时，超过 4 小时的占 {long4['all']:.0%}（全部会话）到 {long4['claimed_weekday']:.0%}（已登记的工作日会话）。由 γ ≈ 1 − {1 - ASENSIO_WITHIN_4H:.3f}/{long4['all']:.2f}~{long4['claimed_weekday']:.2f} 算出。**不是因果估计，而且是另一家公司的数据** |
| 不遵守者超时多久 | 对数正态 | `L(73.1, 195.9)` 分钟 | 推导：Asensio 公开数据 n=378 |
| 代客挪车 | 均匀分布加单次成本 | `U(17.5, 12.5)` 分钟（即 5–30），每次 $0.44 | 成本：Zeng 2019 案例里的设定值，不是实测。时长是假设，可能偏乐观 |
| 被挡后再来 | 伯努利分布加指数分布 | p=0；`E(150)` 分钟 | 假设 |

### 3.7 电价（文献已核实：acnportal 里的 SCE TOU-EV-4，2019-03 生效）
| 季节 | 日型 | 0–8 | 8–12 | 12–18 | 18–23 | 23–24 |
|---|---|---|---|---|---|---|
| 夏（6/1–9/30） | 工作日 | 0.05623 | 0.0925 | **0.26668** | 0.0925 | 0.05623 |
| 夏 | 周末 | 0.05623 全天 | | | | |
| 冬（10/1–5/31） | 工作日 | 0.06087 | 0.07492 | 0.0869 | 0.07492 | 0.06087 |
| 冬 | 周末 | 0.06087 全天 | | | | |

- 需量电费 = $15.51 × 当月最高站点功率（kW）。
- 敏感性：TOU-EV-8 没有需量电费，但 16–21 点电价高；PG&E A-10 的需量电费是 $19.99（夏）和 $11.66（冬）。
- **验证基准**：ACN-Sim 的案例（类似 JPL 的需求，102 个接口，2019 年 6 月）里，总运营成本是 **$0.226–0.346/kWh**，其中只算电费是 $0.118–0.163/kWh，需量电费占 30–66%。可以用来对照我们模型的成本输出。

### 3.8 接口故障与维修
| 输入 | 分布 | 参数 | 来源 |
|---|---|---|---|
| 故障间隔 | 指数分布（按时钟触发的 downtime） | `E(5432)` 小时，对应 A=0.97、平均维修 7 天 | 0.97 是法规要求的下限，不是实测；平均维修 7 天是**假设** |
| 维修时长 | 对数正态 | `L(168, 168)` 小时（中位数 119） | **完全是假设**。L2 的维修时长数据这次没能打开原文。最该补的来源是 Stanford 2026 年的 arXiv:2601.10861 和 ChargerHelp 的报告 |
| 可用率情景 | 固定值 | 1.00 / 0.99 / 0.97 / 0.921 / 0.733 | 0.921：德国公共桩的数据（文献，可迁移性弱）；0.733：湾区快充的调查（Rempel，偏悲观） |
| 开始时已坏的接口 | 二项分布 | Binomial(54, 1−A)，剩余维修时间从维修分布里抽 | 方法。否则 MTBF 太长，短时间的仿真里几乎看不到故障 |

### 3.9 需求情景
| 输入 | 取值 | 来源 |
|---|---|---|
| 需求倍数 | 0.8–1.5 | proposal |
| 收费对到达量的影响 | **首选 ACN 自身的数据**：收费前后工作日从 {pricing['pre_2018-11_mean']:.1f} 降到 {pricing['post_mean']:.1f} 次/天（×{pricing['ratio']:.2f}；收费后取 {pricing['post_period'].replace('..', ' 至 ')}，不含 COVID 时期）。参照：Palo Alto 公共 L2 收费后下降 30–43%（×0.57–0.70，未经同行评审） | ACN 推导；文献 |
| 年增长率 | 每年 10–33%；33% 是美国 EV 保有量 2018–2024 的年均增长率 | 文献（已核实）：IEA。10% 是假设 |

---

## 4. 讲故事用的指标需要哪些输入
| 指标 | 依赖的输入 |
|---|---|
| 被挡和流失的需求、等待时间 | vPorts, vDemandFactor, vPBalk, aPatience, vHoldMin, aRetry |
| 电量满足率（请求口径和真实口径） | aEnergyNeed, aEnergyReq, aDepAnnounced, aMaxRate, vSiteLimitkW, vPolicy |
| 闲置占用、接口利用率 | aComply, aMoveDelay, aOverstay, vPolicy |
| 峰值功率、需量电费 | vSiteLimitkW, vPolicy, aTOU, vDemandChargeRate |
| 单位电量成本、收入、投资 | vCapexPerPort, vLife, vNetworkFee, aTOU, vUserPrice |
| 可靠性损失 | vA, 故障间隔, 维修时长, aSessionFail |

## 5. 还需要确认或补充的
1. **拟合窗口**：{window_note}另外，引自 Person C 表（R 编号）的数字，例如约 30% 的会话拔枪时还没充完（R11）、aMaxRate 的两档功率（R12），还是部分下载时算的，重跑 `unobserved_behavior.build_table` 以后才会更新。
2. **ProModel 语法**：`L()` 的参数含义、`U()` 的半宽写法、`P(G())` 能不能嵌套，都要在 ProModel 里实际测一次。
3. **L2 的维修时长、Level-2 的故障率**：没有找到能打开的原文，目前都是假设。
4. **CAPEX 和网络费**：沿用之前核实过的 R18，这次没能重新打开原文。
5. **遵守率 γ** 和 **排队耐心** 是最弱的环节：一个是假设，一个来自不同场景的问卷，要做扫描，并把结果写进报告。

## 附录 A：经验表的分位点（P0, P10, …, P90, P95, P99, P100）
"""
for sec in ['dwell_hours', 'energy_delivered_kwh']:
    md += '\n**%s**\n\n| 分组 | 分位点 |\n|---|---|\n' % ('停留时长（小时）' if sec == 'dwell_hours' else '电量（kWh）')
    for k, v in A[sec].items():
        md += '| %s | %s |\n' % (k, ', '.join('P%d %s' % (p, x) for p, x in v['table_points']))
md += f"""
## 附录 B：报的值与实际值的偏差（已登记会话，n={A['announced_departure_error_min']['n']:,}）
| 分位数 | P5 | P10 | P25 | P50 | P75 | P90 | P95 |
|---|---|---|---|---|---|---|---|
| 报的离开时刻 − 实际离开（分钟） | {' | '.join(str(dep[k]) for k in ['5','10','25','50','75','90','95'])} |
| 报的电量 ÷ 实际送出 | {' | '.join(str(rat[k]) for k in ['5','10','25','50','75','90','95'])} |
"""
open(sys.argv[1], 'w').write(md)
print(len(md))
