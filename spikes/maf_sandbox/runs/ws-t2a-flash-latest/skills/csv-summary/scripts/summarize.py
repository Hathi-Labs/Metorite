import argparse
import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
import re
import sys


def extract_month(date_str: str) -> str:
    """Extract YYYY-MM from a date string."""
    date_str = date_str.strip()
    match = re.match(r"^(\d{4})[-/.]?(\d{2})", date_str)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    return date_str[:7]


def summarize_csv(
    input_csv_path: str,
    output_md_path: str,
    group_col: str = "region",
    date_col: str = "date",
    value_col: str = "revenue",
) -> str:
    input_path = Path(input_csv_path)
    if not input_path.is_file():
        raise FileNotFoundError(f"Input CSV file not found: {input_csv_path}")

    summary = defaultdict(Decimal)

    with open(input_path, mode="r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"CSV file {input_csv_path} is empty or has no header.")

        # Normalize lookup for columns (case-insensitive fallback)
        field_map = {name.lower(): name for name in reader.fieldnames}
        actual_group_col = field_map.get(group_col.lower(), group_col)
        actual_date_col = field_map.get(date_col.lower(), date_col)
        actual_value_col = field_map.get(value_col.lower(), value_col)

        missing = []
        for req_col, actual in [
            (group_col, actual_group_col),
            (date_col, actual_date_col),
            (value_col, actual_value_col),
        ]:
            if actual not in reader.fieldnames:
                missing.append(req_col)
        if missing:
            raise KeyError(
                f"Missing required column(s): {', '.join(missing)}. Available columns: {', '.join(reader.fieldnames)}"
            )

        for row in reader:
            group_val = row[actual_group_col].strip()
            month_val = extract_month(row[actual_date_col])
            val_str = row[actual_value_col].strip().replace("$", "").replace(",", "")
            summary[(group_val, month_val)] += Decimal(val_str)

    # Format as markdown table
    group_header = actual_group_col.capitalize()
    value_header = actual_value_col.capitalize()

    lines = [
        f"| {group_header} | Month | {value_header} |",
        "| :--- | :--- | ---: |",
    ]

    for (grp, mth), total in sorted(summary.items()):
        lines.append(f"| {grp} | {mth} | {total:.2f} |")

    table_content = "\n".join(lines) + "\n"

    output_path = Path(output_md_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(table_content, encoding="utf-8")

    return table_content


def main():
    parser = argparse.ArgumentParser(
        description="Summarise CSV numerical values by a grouping column and month."
    )
    parser.add_argument("input_csv", help="Path to input CSV file")
    parser.add_argument("output_path", help="Path to output markdown file")
    parser.add_argument(
        "--group-col",
        default="region",
        help="Column to group by (default: 'region')",
    )
    parser.add_argument(
        "--date-col",
        default="date",
        help="Date column used to extract month (default: 'date')",
    )
    parser.add_argument(
        "--value-col",
        default="revenue",
        help="Numeric column to aggregate (default: 'revenue')",
    )

    args = parser.parse_args()

    try:
        table = summarize_csv(
            input_csv_path=args.input_csv,
            output_md_path=args.output_path,
            group_col=args.group_col,
            date_col=args.date_col,
            value_col=args.value_col,
        )
        print(table)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
