"""Fit ProModel input distributions from the Caltech ACN sessions (post-2018-11 paid regime)."""
import json
import sys

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
from unobserved_behavior.data import load_sessions  # noqa: E402

df = load_sessions()
post = df[(df.regime == 'post_2018-11') & df.valid_interval].copy()
post['claimed'] = post['has_input']
first_day, last_day = post['date_local'].min(), post['date_local'].max()
# drop the last partial day of the snapshot
post = post[post['date_local'] < last_day]
last_day = post['date_local'].max()
out = {'data': {'source': df.attrs['source'], 'garage': 'California Garage 01 (site 0002, cluster %s, %d stations)' % (df.attrs['cluster'], df.station_id.nunique()), 'regime': 'post_2018-11 (paid charging)', 'first_day': str(first_day),
                'last_day': str(last_day), 'sessions': int(len(post)), 'claimed_share': round(post.claimed.mean(), 4)}}

# ---------- daily counts (calendar days, zeros included) ----------
days = pd.DataFrame({'date': pd.date_range(first_day, last_day, freq='D')})
days['date_local'] = days.date.dt.date
days['weekend'] = days.date.dt.dayofweek >= 5
counts = post.groupby('date_local').size().rename('n')
days = days.merge(counts, left_on='date_local', right_index=True, how='left').fillna({'n': 0})
daily = {}
for name, g in days.groupby('weekend'):
    n = g['n'].to_numpy()
    m, v = n.mean(), n.var(ddof=1)
    r = m * m / (v - m) if v > m else None
    daily['weekend' if name else 'weekday'] = {
        'days': int(len(n)), 'mean': round(m, 2), 'var': round(v, 2), 'var_to_mean': round(v / m, 2),
        'zero_days': int((n == 0).sum()), 'p10_p50_p90': [float(x) for x in np.percentile(n, [10, 50, 90])],
        'negbin_r': round(r, 2) if r else None,
        'promodel_poisson_gamma': 'P(G(%.2f, %.3f))' % (r, m / r) if r else None}
out['daily_arrivals'] = daily

# ---------- arrival time-of-day profile ----------
prof = {}
for name, g in post.groupby('is_weekend'):
    h = np.floor(g.arrival_hour).astype(int).value_counts(normalize=True).reindex(range(24), fill_value=0)
    prof['weekend' if name else 'weekday'] = [round(float(x) * 100, 2) for x in h]
out['arrival_profile_pct_by_hour'] = prof

# ---------- dwell by day type x arrival period ----------
def fit_all(x):
    x = np.asarray(x, float)
    x = x[x > 0]
    res = {}
    for nm, dist in (('gamma', stats.gamma), ('lognorm', stats.lognorm), ('weibull', stats.weibull_min)):
        p = dist.fit(x, floc=0)
        ll = dist.logpdf(x, *p).sum()
        ks = stats.kstest(x, dist.cdf, args=p).statistic
        res[nm] = {'params': p, 'aic': 2 * 2 - 2 * ll, 'ks': ks}
    best = min(res, key=lambda k: res[k]['aic'])
    def promodel(nm, p):
        if nm == 'gamma':
            return 'G(%.3f, %.3f)' % (p[0], p[2])  # shape, scale
        if nm == 'weibull':
            return 'W(%.3f, %.3f)' % (p[0], p[2])  # shape, scale
        s, sc = p[0], p[2]  # lognormal: sigma of ln X, scale = exp(mu)
        mean = sc * np.exp(s * s / 2)
        sd = mean * np.sqrt(np.exp(s * s) - 1)
        return 'L(%.3f, %.3f) [mean, sd of X]; ln X ~ N(%.3f, %.3f)' % (mean, sd, np.log(sc), s)
    return {'n': int(len(x)), 'mean': round(x.mean(), 3), 'median': round(float(np.median(x)), 3),
            'p10_p90': [round(float(v), 3) for v in np.percentile(x, [10, 90])], 'best_by_aic': best,
            'deciles_0_to_100': [round(float(v), 2) for v in np.percentile(x, range(0, 101, 10))],
            'fits': {k: {'promodel': promodel(k, v['params']), 'ks': round(v['ks'], 3), 'aic': round(v['aic'], 1)}
                     for k, v in res.items()}}

d = post[(post.dwell_h > 0) & (post.dwell_h <= 24)].copy()
out['dwell_excluded_over_24h_share'] = round(1 - len(d) / len(post[post.dwell_h > 0]), 4)
wk_bins = [0, 7, 9, 11, 14, 24]
wk_lbl = ['00-07', '07-09', '09-11', '11-14', '14-24']
we_bins = [0, 11, 24]
we_lbl = ['00-11', '11-24']
dwell = {}
for weekend, g in d.groupby('is_weekend'):
    bins, lbl = (we_bins, we_lbl) if weekend else (wk_bins, wk_lbl)
    g = g.assign(period=pd.cut(g.arrival_hour, bins, labels=lbl, right=False))
    for per, gg in g.groupby('period', observed=True):
        dwell['%s %s' % ('weekend' if weekend else 'weekday', per)] = fit_all(gg.dwell_h)
out['dwell_hours'] = dwell

# ---------- energy (claimed sessions) conditional on dwell ----------
c = d[d.claimed & (d.kwh > 0)]
e_bins = [0, 2, 4, 6, 8, 10, 24]
e_lbl = ['0-2h', '2-4h', '4-6h', '6-8h', '8-10h', '10-24h']
c = c.assign(dbin=pd.cut(c.dwell_h, e_bins, labels=e_lbl, right=False))
energy = {}
for b, g in c.groupby('dbin', observed=True):
    energy['claimed, dwell ' + b] = fit_all(g.kwh)
u = d[(~d.claimed) & (d.kwh > 0)]
energy['unclaimed (30-min auto stop)'] = fit_all(u.kwh)
out['energy_delivered_kwh'] = energy
out['spearman_dwell_kwh_claimed'] = round(stats.spearmanr(c.dwell_h, c.kwh).statistic, 3)

# ---------- announced inputs (first version) ----------
a = d[d.claimed & d.dep_req_first.notna()].copy()
err = (a.dep_req_first - a.disconnect).dt.total_seconds() / 60  # announced minus actual, minutes
ratio = (a.kwh_req_first / a.kwh).replace([np.inf, -np.inf], np.nan).dropna()
q = [5, 10, 25, 50, 75, 90, 95]
out['announced_departure_error_min'] = {
    'n': int(err.notna().sum()), 'definition': 'announced departure minus actual unplug (negative = stayed longer)',
    'quantiles': {str(k): round(float(v), 1) for k, v in zip(q, np.percentile(err.dropna(), q))}}
out['requested_over_delivered_energy'] = {
    'n': int(len(ratio)), 'quantiles': {str(k): round(float(v), 3) for k, v in zip(q, np.percentile(ratio, q))}}
out['requested_energy_kwh'] = fit_all(a.kwh_req_first.dropna())

# ---------- user classes ----------
claimed = post[post.claimed]
per_user = claimed.groupby('user_id').size()
repeat_users = per_user[per_user >= 2].index
out['user_class'] = {
    'unclaimed_share_of_sessions': round(1 - post.claimed.mean(), 4),
    'claimed_repeat_user_share_of_sessions': round(claimed.user_id.isin(repeat_users).mean() * post.claimed.mean(), 4),
    'claimed_one_time_user_share_of_sessions': round((~claimed.user_id.isin(repeat_users)).mean() * post.claimed.mean(), 4),
    'identified_users': int(per_user.size), 'median_sessions_per_user': float(per_user.median())}

# ---------- capacity in service ----------
monthly_ports = post.groupby('month_local').station_id.nunique()
out['ports_in_service_by_month'] = {'min': int(monthly_ports.min()), 'max': int(monthly_ports.max()),
                                    'median': float(monthly_ports.median())}

json.dump(out, open(sys.argv[1], 'w'), indent=1, default=float)
print(json.dumps({k: out[k] for k in ['data', 'daily_arrivals', 'user_class', 'ports_in_service_by_month',
                                     'spearman_dwell_kwh_claimed', 'dwell_excluded_over_24h_share']}, indent=1, default=float))
for sec in ['dwell_hours', 'energy_delivered_kwh']:
    print('\n==', sec)
    for k, v in out[sec].items():
        b = v['best_by_aic']
        print('%-34s n=%5d med=%6.2f best=%-8s %s  ks=%.3f' % (k, v['n'], v['median'], b, v['fits'][b]['promodel'], v['fits'][b]['ks']))
print('\n== requested energy', out['requested_energy_kwh']['best_by_aic'], out['requested_energy_kwh']['fits'][out['requested_energy_kwh']['best_by_aic']])
print('== departure error', out['announced_departure_error_min'])
print('== request/delivered', out['requested_over_delivered_energy'])
print('== weekday profile', out['arrival_profile_pct_by_hour']['weekday'])
print('== weekend profile', out['arrival_profile_pct_by_hour']['weekend'])
