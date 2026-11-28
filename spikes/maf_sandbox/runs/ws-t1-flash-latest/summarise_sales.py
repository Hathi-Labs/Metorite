import csv
from collections import defaultdict
from decimal import Decimal
import os

def summarise_sales(input_csv="sales.csv", output_md="outputs/summary.md"):
    # Aggregate revenue by (region, month)
    revenue_by_region_month = defaultdict(Decimal)

    with open(input_csv, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            region = row["region"].strip()
            # Extract YYYY-MM from YYYY-MM-DD
            month = row["date"].strip()[:7]
            revenue = Decimal(row["revenue"].strip())
            revenue_by_region_month[(region, month)] += revenue

    # Sort by region, then month
    sorted_entries = sorted(revenue_by_region_month.keys())

    # Build markdown table
    lines = [
        "| Region | Month | Revenue |",
        "| --- | --- | --- |",
    ]
    for region, month in sorted_entries:
        rev = revenue_by_region_month[(region, month)]
        lines.append(f"| {region} | {month} | {rev:.2f} |")

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_md), exist_ok=True)

    with open(output_md, mode="w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Summary successfully written to {output_md}")

if __name__ == "__main__":
    summarise_sales()
