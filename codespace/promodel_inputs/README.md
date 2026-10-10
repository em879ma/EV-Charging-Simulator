# ProModel input distributions (draft)

Inputs for the ProModel model in the team design document (sections 3.2 Attributes, 3.3 Variables,
3.4 Distributions), filled from ACN-Data where it has the variable and from literature otherwise.

- `ProModel_inputs_draft.md`: the table to read (Chinese), with the ProModel expression and source of every input.
- `fit_acn.py`: fits arrivals, dwell, energy, announced-input errors and user classes from the Caltech
  sessions. Only California Garage 01 (site 0002, cluster 0039, 54 EVSEs) is used, and only the
  post-2018-11-01 paid-charging period. Writes `acn_fits.json`.
- `make_md.py`: rebuilds the Markdown table from `acn_fits.json`.
- `literature_review.json`: raw literature findings, each with a verbatim quote, the source URL and an
  independent re-check verdict (confirmed / corrected / not_found / source_unreachable).

Rebuild from this folder (needs pandas, numpy, scipy):

```bash
python fit_acn.py acn_fits.json && python make_md.py ProModel_inputs_draft.md
```

The current numbers come from the partial download (23,200 Caltech sessions to 2019-09-17). Rerun
after the complete download.
