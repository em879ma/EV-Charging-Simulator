# ProModel input distributions (draft)

Inputs for the ProModel model in the team design document (sections 3.2 Attributes, 3.3 Variables,
3.4 Distributions), filled from ACN-Data where it has the variable and from literature otherwise.

- `ProModel_inputs_draft.md`: the table to read (Chinese), with the ProModel expression and source of every input.
- `fit_acn.py`: fits arrivals, dwell, energy, announced-input errors and user classes from the Caltech
  sessions. Only the 54 Level-2 EVSEs of California Garage 01 (site 0002, cluster 0039) are used, and
  only the post-2018-11-01 paid-charging period, to the end of the data (see below). Writes `acn_fits.json`.
- `make_md.py`: rebuilds the Markdown table from `acn_fits.json`.
- `literature_review.json`: raw literature findings, each with a verbatim quote, the source URL and an
  independent re-check verdict (confirmed / corrected / not_found / source_unreachable).

Rebuild from this folder (needs pandas, numpy, scipy):

```bash
python fit_acn.py acn_fits.json && python make_md.py ProModel_inputs_draft.md
```

The numbers come from the complete download (`raw_acn/caltech_sessions.json`, 31,424 Caltech sessions,
2018-04-25 to 2021-09-13 local time). The fit uses every day from 2018-11-01 to 2021-09-12 (the last full
day). That period includes the COVID-19 closures from mid-March 2020 (24 sessions in April 2020) and 106
days with no sessions recorded at all (2020-08-04 to 2020-11-17), which count as zero-arrival days, so
weekday arrivals are far more dispersed than before COVID (variance/mean 16.4 against 3.3 for
2018-11-01 to 2020-02-29). `python fit_acn.py acn_fits.json --end 2020-03-01` fits only the paid period
before COVID. The before/after-pricing arrival comparison always uses 2018-11-01 to 2020-02-29.

Station 2-39-81-4550, added on 2020-11-18, is a fast charger (most of its sessions average more than
7 kW over the whole stay, above what a 6.66 kW Level-2 port can deliver). Its 1,126 sessions are left
out; `--keep-fast-charger` keeps them.
