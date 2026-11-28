---
name: csv-summary
description: Summarises CSV revenue or numeric metrics by grouping category and month, saving the result as a markdown table.
---

# csv-summary

Summarises numeric values (such as revenue) in a CSV file grouped by a categorical column (such as region, department, or product) and month (extracted from a date column), outputting a formatted markdown table.

## Script Location

`skills/csv-summary/scripts/summarize.py`

## Usage

```bash
python3 skills/csv-summary/scripts/summarize.py <input_csv_path> <output_md_path> [options]
```

### Positional Arguments

- `input_csv_path`: Path to the source CSV file (e.g., `sales.csv`).
- `output_md_path`: Path where the output markdown summary table should be saved (e.g., `outputs/summary.md`). Any missing parent directories are created automatically.

### Optional Arguments

- `--group-col`: Name of the column to group by. Default is `region`.
- `--date-col`: Name of the date column to extract year-month (`YYYY-MM`) from. Default is `date`.
- `--value-col`: Name of the numeric column to aggregate. Default is `revenue`.

## Examples

### Default (revenue by region and month)

```bash
python3 skills/csv-summary/scripts/summarize.py sales.csv outputs/summary.md
```

### Custom columns

```bash
python3 skills/csv-summary/scripts/summarize.py transactions.csv reports/monthly.md --group-col department --date-col transaction_date --value-col amount
```
