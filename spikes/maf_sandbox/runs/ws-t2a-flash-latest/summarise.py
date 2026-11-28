import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path


def summarize_sales(input_csv_path: str, output_md_path: str) -> str:
    revenue_by_region_month = defaultdict(Decimal)

    with open(input_csv_path, mode="r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            month = row["date"][:7]
            region = row["region"]
            revenue = Decimal(row["revenue"])
            revenue_by_region_month[(region, month)] += revenue

    # Build markdown table
    lines = [
        "| Region | Month | Revenue |",
        "| :--- | :--- | ---: |",
    ]

    for (region, month), revenue in sorted(revenue_by_region_month.items()):
        lines.append(f"| {region} | {month} | {revenue:.2f} |")

    table_content = "\n".join(lines) + "\n"

    # Save to output file
    output_path = Path(output_md_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(table_content, encoding="utf-8")

    return table_content


if __name__ == "__main__":
    table = summarize_sales("sales.csv", "outputs/summary.md")
    print(table)
