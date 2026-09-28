# EV Charging Simulator

IOE 474 project using ACN-Data charging-session records for preprocessing, exploratory analysis, and simulation-model development.

## Project structure

- `codespace/preprocess.ipynb`: preprocessing and modeling diagnostics.
- `raw_acn/`: downloaded ACN session tables, download status, and download script.
- `ACN-Data-Static/`: upstream static ACN time-series dataset, tracked as a Git submodule.

## Clone with the static dataset

```bash
git clone --recurse-submodules https://github.com/em879ma/EV-Charging-Simulator.git
```

The ACN API token is intentionally excluded from version control.
