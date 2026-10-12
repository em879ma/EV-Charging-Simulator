"""Write the ProModel input tables (arrival cycles, user distributions, macros) from acn_fits.json to an Excel file.

Usage: python export_promodel_tables.py ProModel_tables.xlsx
Each sheet is laid out so its rows can be pasted into the matching ProModel table. Percent columns are
rounded so each table totals exactly 100, which ProModel requires for arrival cycles and user distributions.
"""
import json
import sys

import pandas as pd

A = json.load(open('acn_fits.json'))

DWELL_TABLES = {'weekday 00-07': 'dDwellWD0007', 'weekday 07-09': 'dDwellWD0709', 'weekday 09-11': 'dDwellWD0911',
                'weekday 11-14': 'dDwellWD1114', 'weekday 14-24': 'dDwellWD1424', 'weekend 00-11': 'dDwellWE0011',
                'weekend 11-24': 'dDwellWE1124'}
ENERGY_TABLES = {'claimed, dwell 0-2h': 'dEnergy0002', 'claimed, dwell 2-4h': 'dEnergy0204', 'claimed, dwell 4-6h': 'dEnergy0406',
                 'claimed, dwell 6-8h': 'dEnergy0608', 'claimed, dwell 8-10h': 'dEnergy0810', 'claimed, dwell 10-24h': 'dEnergy1024',
                 'unclaimed (30-min auto stop)': 'dEnergyUnclaimed'}


def to_total(values, total, nd):
    """Round to nd decimals and put the rounding residual on the largest entry, so the column sums to total."""
    vals = [round(v, nd) for v in values]
    i = vals.index(max(vals))
    vals[i] = round(vals[i] + total - sum(vals), nd)
    return vals


def fitted_rows(section, names):
    rows = []
    for group, v in A[section].items():
        best = v['best_by_aic']
        expr = v['fits'][best]['promodel'].split(' [')[0]
        empirical = v['fits'][best]['ks'] > 0.1
        rows.append({'Group': group, 'n': v['n'], 'Median': v['median'],
                     'Use in ProModel': 'User distribution ' + names[group] if empirical else expr,
                     'KS of best fit': v['fits'][best]['ks'], 'Best fit (AIC)': expr,
                     'Gamma fit': v['fits']['gamma']['promodel'], 'Gamma KS': v['fits']['gamma']['ks']})
    return pd.DataFrame(rows)


def empirical_rows(section, names):
    rows = []
    for group, v in A[section].items():
        best = v['best_by_aic']
        if v['fits'][best]['ks'] <= 0.1:
            continue
        for pct, value in zip(range(0, 101, 10), v['deciles_0_to_100']):
            rows.append({'User distribution': names[group], 'Group': group, 'Cumulative %': pct, 'Value': value})
    return pd.DataFrame(rows)


def daily_rows(day_type):
    counts = A['daily_count_empirical'][day_type]
    values = sorted(counts, key=int)
    days = [counts[k] for k in values]
    pct = to_total([100 * d / sum(days) for d in days], 100, 2)
    cum = pd.Series(pct).cumsum().round(2)
    return pd.DataFrame({'Arrivals per day': [int(k) for k in values], 'Days observed': days, 'Percent': pct,
                         'Cumulative %': cum})


prof = A['arrival_profile_pct_by_hour']
cycles = pd.DataFrame({'Interval': ['%02d:00-%02d:00' % (h, h + 1) for h in range(24)],
                       'Time (hours)': list(range(1, 25)),
                       'Weekday %': to_total(prof['weekday'], 100, 2), 'Weekend %': to_total(prof['weekend'], 100, 2)})

uc = A['user_class']
classes = pd.DataFrame({'Value (aUserClass)': [1, 2, 3], 'Class': ['claimed repeat user', 'claimed one-time user', 'unclaimed'],
                        'Percent': to_total([100 * uc['claimed_repeat_user_share_of_sessions'],
                                             100 * uc['claimed_one_time_user_share_of_sessions'],
                                             100 * uc['unclaimed_share_of_sessions']], 100, 1)})

rate = A['effective_rate_kw']
macros = pd.DataFrame([
    {'Macro': 'mPorts', 'Baseline': 54, 'Scenario values': '30 / 42 / 54 / 66', 'Unit': 'EVSEs',
     'Used in': 'EVSE_BANK capacity', 'Source': 'Garage 01 has 54 Level-2 EVSEs; proposal factor levels'},
    {'Macro': 'mRate', 'Baseline': rate['median'], 'Scenario values': '%s / 4.8 / 6.66' % rate['median'], 'Unit': 'kW',
     'Used in': 'constant effective charging rate (stages 1-3)',
     'Source': 'median observed kWh per charging hour (n=%d); 4.8 = vehicle-rate mix mean; 6.66 = EVSE rating' % rate['n']},
    {'Macro': 'mDemand', 'Baseline': 1.0, 'Scenario values': '0.8 / 1.0 / 1.2 / 1.5', 'Unit': 'x daily arrivals',
     'Used in': 'Arrivals Qty Each', 'Source': 'proposal (R17)'},
    {'Macro': 'mPBalk', 'Baseline': 1.0, 'Scenario values': '0.5 - 1.0', 'Unit': 'probability',
     'Used in': 'leave at once when all EVSEs are busy (stage 5)', 'Source': 'assumption (R02)'},
    {'Macro': 'mSiteLimitkW', 'Baseline': 150, 'Scenario values': '20 - 150', 'Unit': 'kW',
     'Used in': 'site power cap (stage 5)', 'Source': 'Lee et al. 2021a; acnportal (150 kVA transformer)'},
    {'Macro': 'mPolicy', 'Baseline': 1, 'Scenario values': '1 uncontrolled / 2 equal share / 3 earliest departure first / 4 move after charge',
     'Unit': 'code', 'Used in': 'charging policy (stage 5)', 'Source': 'proposal'},
])

dep = A['announced_departure_error_min']['quantiles']
ratio = A['requested_over_delivered_energy']['quantiles']
announced = pd.DataFrame({'Quantile': ['P' + k for k in dep],
                          'Announced minus actual departure (min)': list(dep.values()),
                          'Requested / delivered energy': [ratio[k] for k in dep]})

with pd.ExcelWriter(sys.argv[1], engine='openpyxl') as xl:
    pd.DataFrame({'Sheet': ['Arrival cycles', 'Daily count weekday', 'Daily count weekend', 'Dwell', 'Energy',
                            'Empirical tables', 'User class', 'Macros', 'Announced inputs'],
                  'What it holds': ['CYC_WEEKDAY and CYC_WEEKEND, percent of the day\'s arrivals per hour',
                                    'User distribution dDailyWD (discrete): observed arrivals per weekday',
                                    'User distribution dDailyWE (discrete): observed arrivals per weekend day',
                                    'Dwell hours by day type and arrival period: expression or user distribution to use',
                                    'Energy kWh by dwell bin (claimed) and for unclaimed sessions',
                                    'Continuous cumulative user distributions for the groups whose best fit has KS > 0.1',
                                    'User distribution dUserClass (discrete)',
                                    'Scenario macros with baseline and test values',
                                    'Announced-input error quantiles (for aDepAnnounced and aEnergyReq in later stages)'],
                  }).to_excel(xl, sheet_name='Contents', index=False)
    pd.DataFrame({'Item': list(A['data']), 'Value': [str(v) for v in A['data'].values()]}).to_excel(
        xl, sheet_name='Contents', index=False, startrow=12)
    cycles.to_excel(xl, sheet_name='Arrival cycles', index=False)
    daily_rows('weekday').to_excel(xl, sheet_name='Daily count weekday', index=False)
    daily_rows('weekend').to_excel(xl, sheet_name='Daily count weekend', index=False)
    fitted_rows('dwell_hours', DWELL_TABLES).to_excel(xl, sheet_name='Dwell', index=False)
    fitted_rows('energy_delivered_kwh', ENERGY_TABLES).to_excel(xl, sheet_name='Energy', index=False)
    pd.concat([empirical_rows('dwell_hours', DWELL_TABLES), empirical_rows('energy_delivered_kwh', ENERGY_TABLES)]).to_excel(
        xl, sheet_name='Empirical tables', index=False)
    classes.to_excel(xl, sheet_name='User class', index=False)
    macros.to_excel(xl, sheet_name='Macros', index=False)
    announced.to_excel(xl, sheet_name='Announced inputs', index=False)
    for ws in xl.sheets.values():
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(60, max(10, max(len(str(c.value or '')) for c in col) + 2))
print('wrote', sys.argv[1])
