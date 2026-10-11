# ProModel input distributions (draft)

Inputs for the ProModel model in the team design document (sections 3.2 Attributes, 3.3 Variables,
3.4 Distributions), filled from ACN-Data where it has the variable and from literature otherwise.

- `ProModel_inputs_draft.md`: the table to read (Chinese), with the ProModel expression and source of every input.
- `fit_acn.py`: fits arrivals, dwell, energy, announced-input errors and user classes from the Caltech
  sessions. Only the 54 Level-2 EVSEs of California Garage 01 (site 0002, cluster 0039) are used, and
  only the post-2018-11-01 paid-charging period, to the end of the data (see below). Writes `acn_fits.json`.
- `make_md.py`: rebuilds the Markdown table from `acn_fits.json`.
- `export_promodel_tables.py`: writes `ProModel_tables.xlsx` from `acn_fits.json`: the two arrival cycles, the
  user distributions (daily counts, empirical dwell and energy tables, user class), the dwell and energy expressions
  and the scenario macros, one sheet each, with percent columns that total exactly 100.
- `literature_review.json`: raw literature findings, each with a verbatim quote, the source URL and an
  independent re-check verdict (confirmed / corrected / not_found / source_unreachable).

Rebuild from this folder (needs pandas, numpy, scipy, openpyxl):

```bash
python fit_acn.py acn_fits.json && python make_md.py ProModel_inputs_draft.md && python export_promodel_tables.py ProModel_tables.xlsx
```

The numbers come from the complete download (`raw_acn/caltech_sessions.json`, 31,424 Caltech sessions,
2018-04-25 to 2021-09-13 local time). The fit uses every session from 2018-11-01 to 2021-09-12 (the last
full day), including the COVID-19 closures from mid-March 2020 (24 sessions in April 2020), so weekday
arrivals are far more dispersed than before COVID (variance/mean 14.1 against 3.3 for 2018-11-01 to
2020-02-29). `python fit_acn.py acn_fits.json --end 2020-03-01` fits only the paid period before COVID.
The before/after-pricing arrival comparison always uses 2018-11-01 to 2020-02-29.

ACN recorded nothing at Caltech or JPL from 2020-08-03 afternoon to 2020-11-18 (both sites stop within
20 minutes of each other and restart the same day), so those 108 days are a recording outage. They are
left out of the daily arrival counts and the monthly port counts instead of being counted as days with
no arrivals.

Station 2-39-81-4550, added on 2020-11-18, is a fast charger (most of its sessions average more than
7 kW over the whole stay, above what a 6.66 kW Level-2 port can deliver). Its 1,126 sessions in the fit
window are left out; `--keep-fast-charger` keeps them.
