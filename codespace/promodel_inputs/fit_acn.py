"""Fit ProModel input distributions from the Caltech ACN sessions (post-2018-11 paid regime).

Usage: python fit_acn.py acn_fits.json [--end YYYY-MM-DD] [--keep-fast-charger]
"""
import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / 'codespace'))
from unobserved_behavior.data import load_sessions  # noqa: E402

# COVID-19 closures emptied the garage from mid-March 2020: weekday plug-ins fell from about 37 a day
# (to 2020-03-06) to 4 in the week of 2020-03-16 and under 1 in April; monthly sessions went 847
# (2020-02), 381, 24 (2020-04). After the recording gap below, to the end of the data (2021-09), the
# Level-2 ports were at 5-13% of their earlier monthly level until 2021-02 and about half by 2021-08,
# with a median stay of about 3 h instead of 5-6 h, and a new fast charger (2-39-81-4550) took a third
# of the sessions. The fit keeps every day by default; --end 2020-03-01 fits only the paid period before
# COVID. The before/after-pricing comparison always stops at COVID_START, so the COVID drop is not
# counted as a pricing effect.
COVID_START = dt.date(2020, 3, 1)
# Nothing was recorded at Caltech or JPL from 2020-08-03 about 15:00 to 2020-11-18 about 13:00 local: both
# sites stop within 20 minutes of each other and restart the same day, so this is a recording outage,
# not a garage without demand. Its days (inclusive, the two partial ones too) are left out of the daily
# arrival counts instead of being counted as zero-arrival days; no session is dropped.
RECORDING_GAPS = [(dt.date(2020, 8, 3), dt.date(2020, 11, 18))]
# Station 2-39-81-4550 (from 2020-11-18) is a fast charger: 96% of its sessions average more than 7 kW
# over the whole stay, which a 6.66 kW Level-2 port cannot do. The modeled ports are the garage's 54
# Level-2 EVSEs, so its sessions are left out unless --keep-fast-charger is given.
FAST_CHARGERS = {'2-39-81-4550'}


def end_date(text):
    if text.lower() == 'none':
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError('expected YYYY-MM-DD or none, got %r' % text)


parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument('output')
parser.add_argument('--end', type=end_date, default=None,
                    help='first local day left out of the fit (default: keep every day; 2020-03-01 = before COVID)')
parser.add_argument('--keep-fast-charger', action='store_true', help='keep the fast-charger sessions (%s)' % ', '.join(FAST_CHARGERS))
args = parser.parse_args()
end = args.end

df = load_sessions()
source, cluster = df.attrs['source'], df.attrs['cluster']
post = df[(df.regime == 'post_2018-11') & df.valid_interval].copy()
post['claimed'] = post['has_input']
data_last_day = post['date_local'].max()
after_end = int((post['date_local'] >= end).sum()) if end else 0
# drop the last partial day of the download
post = post[post['date_local'] < data_last_day]
fast = post.station_id.isin(FAST_CHARGERS)
fast_left_out = 0 if args.keep_fast_charger else int((fast & (post['date_local'] < end if end else True)).sum())
if not args.keep_fast_charger:
    post = post[~fast]
paid = post[post['date_local'] < COVID_START]  # paid period before COVID, whatever the fit window
if end:
    post = post[post['date_local'] < end]
if post.empty:
    raise SystemExit('No post-2018-11 sessions before --end %s' % end)
first_day, last_day = post['date_local'].min(), post['date_local'].max()
out = {'data': {'source': os.path.relpath(source, REPO_ROOT), 'garage': 'California Garage 01 (site 0002, cluster %s, %d stations)' % (cluster, post.station_id.nunique()), 'regime': 'post_2018-11 (paid charging)', 'first_day': str(first_day),
                'last_day': str(last_day), 'sessions': int(len(post)), 'claimed_share': round(post.claimed.mean(), 4),
                'window_end_exclusive': str(end) if end else None, 'data_last_day': str(data_last_day),
                'sessions_left_out_after_window': after_end, 'fast_charger_sessions_left_out': fast_left_out}}

# ---------- daily counts (calendar days, zeros included) ----------
calendar = pd.DataFrame({'date': pd.date_range(first_day, last_day, freq='D')})
calendar['date_local'] = calendar.date.dt.date
calendar['weekend'] = calendar.date.dt.dayofweek >= 5
calendar['gap'] = False
for gap_from, gap_to in RECORDING_GAPS:
    calendar['gap'] |= (calendar.date_local >= gap_from) & (calendar.date_local <= gap_to)
counts = post.groupby('date_local').size().rename('n')
calendar = calendar.merge(counts, left_on='date_local', right_index=True, how='left').fillna({'n': 0})
days = calendar[~calendar.gap]
out['data']['recording_gap_days_left_out'] = int(calendar.gap.sum())
out['data']['recording_gaps'] = ['%s..%s' % (a, b) for a, b in RECORDING_GAPS if a <= last_day and b >= first_day]
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
zero = (calendar['n'] == 0) & ~calendar.gap  # recording-gap days break a run instead of joining it
runs = calendar[zero].groupby((~zero).cumsum()[zero])['date_local'].agg(['min', 'max', 'size'])
longest = runs.sort_values('size').iloc[-1] if len(runs) else None
out['longest_run_without_sessions'] = (None if longest is None else
                                       {'from': str(longest['min']), 'to': str(longest['max']), 'days': int(longest['size'])})
pre = df[(df.regime == 'pre_2018-11') & df.valid_interval]
pre_days = pd.date_range(pre['date_local'].min(), pre['date_local'].max(), freq='D')
pre_n = pre.groupby('date_local').size().reindex(pre_days.date, fill_value=0)
pre_weekday = pre_n[pre_days.dayofweek < 5].mean()
paid_days = pd.date_range(paid['date_local'].min(), paid['date_local'].max(), freq='D')
paid_n = paid.groupby('date_local').size().reindex(paid_days.date, fill_value=0)
paid_weekday = paid_n[paid_days.dayofweek < 5].mean()
out['weekday_arrivals_before_after_pricing'] = {
    'pre_2018-11_mean': round(pre_weekday, 2), 'post_mean': round(paid_weekday, 2), 'ratio': round(paid_weekday / pre_weekday, 2),
    'post_period': '%s..%s' % (paid_days[0].date(), paid_days[-1].date())}

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
# share staying over 4 h (ACN has no time limit), for the move-after-charge compliance estimate
stays = post[post.dwell_h > 0]
over4 = stays.dwell_h > 4
out['share_dwell_over_4h'] = {'all': round(over4.mean(), 3),
                              'claimed_weekday': round(over4[stays.claimed & ~stays.is_weekend].mean(), 3)}
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
gap_months = set(calendar.loc[calendar.gap, 'date'].dt.strftime('%Y-%m'))  # months cut short by a recording gap
monthly_ports = post[~post.month_local.isin(gap_months)].groupby('month_local').station_id.nunique()
out['ports_in_service_by_month'] = {'min': int(monthly_ports.min()), 'max': int(monthly_ports.max()),
                                    'median': float(monthly_ports.median())}

json.dump(out, open(args.output, 'w'), indent=1, default=float)
print(json.dumps({k: out[k] for k in ['data', 'daily_arrivals', 'weekday_arrivals_before_after_pricing', 'user_class',
                                     'ports_in_service_by_month', 'spearman_dwell_kwh_claimed', 'dwell_excluded_over_24h_share',
                                     'share_dwell_over_4h']}, indent=1, default=float))
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
