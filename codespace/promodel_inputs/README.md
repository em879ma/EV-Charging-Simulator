# ProModel input distributions (draft)

Inputs for the ProModel model in the team design document (sections 3.2 Attributes, 3.3 Variables,
3.4 Distributions), filled from ACN-Data where it has the variable and from literature otherwise.

- `ProModel_inputs_draft.md`: the table to read (Chinese), with the ProModel expression and source of every input.
- `fit_acn.py`: fits arrivals, dwell, energy, announced-input errors and user classes from the Caltech
  sessions. Only California Garage 01 (site 0002, cluster 0039, 54 EVSEs) is used, and only the
  post-2018-11-01 paid-charging period up to 2020-02-29 (see below). Writes `acn_fits.json`.
- `make_md.py`: rebuilds the Markdown table from `acn_fits.json`.
- `literature_review.json`: raw literature findings, each with a verbatim quote, the source URL and an
  independent re-check verdict (confirmed / corrected / not_found / source_unreachable).

Rebuild from this folder (needs pandas, numpy, scipy):

```bash
python fit_acn.py acn_fits.json && python make_md.py ProModel_inputs_draft.md
```

The numbers come from the complete download (`raw_acn/caltech_sessions.json`, 31,424 Caltech sessions,
2018-04-25 to 2021-09-13 local time). The fit window ends before 2020-03-01: from mid-March 2020 COVID-19
closures emptied the garage (24 sessions in April 2020, none in September-October 2020), and by the end
of the data use was still lower, with much shorter stays. Fitting through 2021 would mix those days into
a model of normal operation (weekday arrivals would get 114 zero days and a variance/mean of 14.7 instead
of 3.3). `python fit_acn.py acn_fits.json --end YYYY-MM-DD` moves the cut; `--end none` keeps every day.
