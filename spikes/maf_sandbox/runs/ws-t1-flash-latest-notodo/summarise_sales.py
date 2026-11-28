import csv
from collections import defaultdict
from decimal import Decimal
import os

def summarize_sales(input_csv="sales.csv", output_md="outputs/summary.md"):
    # Aggregate revenue by region and month
    revenue_by_region_month = defaultdict(Decimal)

    with open(input_csv, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            month = row["date"][:7]
            region = row["region"]
            revenue = Decimal(row["revenue"])
            revenue_by_region_month[(region, month)] += revenue

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_md), exist_ok=True)

    # Sort keys by region and month
    sorted_items = sorted(revenue_by_region_month.items())

    # Build markdown table
    lines = [
        "| region | month | revenue |",
        "| --- | --- | --- |",
    ]
    for (region, month), rev in sorted_items:
        lines.append(f"| {region} | {month} | {rev:.2f} |")

    content = "\n".join(lines) + "\n"

    with open(output_md, mode="w", encoding="utf-8") as f:
        f.write(content)

    print("Summary written to", output_md)
    print(content)

if __name__ == "__main__":
    summarize_sales()
