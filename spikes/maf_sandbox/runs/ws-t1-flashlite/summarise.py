import csv
from collections import defaultdict
from datetime import datetime
import os

revenue_data = defaultdict(lambda: defaultdict(float))
region_totals = defaultdict(float)
month_totals = defaultdict(float)
months_set = set()
regions_set = set()

with open('sales.csv', mode='r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        date_str = row['date']
        region = row['region']
        revenue = float(row['revenue'])
        
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        month_key = dt.strftime('%Y-%m') # e.g. 2026-01
        
        revenue_data[region][month_key] += revenue
        region_totals[region] += revenue
        month_totals[month_key] += revenue
        months_set.add(month_key)
        regions_set.add(region)

months = sorted(list(months_set))
regions = sorted(list(regions_set))

# Format month headers nicely (e.g. "January 2026" or keep "2026-01")
# Let's use month name / year or YYYY-MM. datetime.strptime("2026-01", "%Y-%m").strftime("%B %Y") -> "January 2026"
month_headers = [datetime.strptime(m, '%Y-%m').strftime('%B %Y') for m in months]

# Build markdown table
lines = []
lines.append("# Revenue Summary by Region and Month")
lines.append("")

header = "| Region | " + " | ".join(month_headers) + " | Total |"
separator = "|:---|" + ":---:|" * len(months) + ":---:|"
lines.append(header)
lines.append(separator)

grand_total = 0.0

for region in regions:
    row_cells = [region.title()]
    reg_sum = 0.0
    for m in months:
        rev = revenue_data[region][m]
        reg_sum += rev
        row_cells.append(f"${rev:,.2f}")
    row_cells.append(f"${reg_sum:,.2f}")
    grand_total += reg_sum
    lines.append("| " + " | ".join(row_cells) + " |")

# Total row
total_cells = ["**Total**"]
for m in months:
    m_sum = month_totals[m]
    total_cells.append(f"**${m_sum:,.2f}**")
total_cells.append(f"**${grand_total:,.2f}**")
lines.append("| " + " | ".join(total_cells) + " |")

markdown_content = "\n".join(lines) + "\n"

os.makedirs('outputs', exist_ok=True)
with open('outputs/summary.md', 'w', encoding='utf-8') as f:
    f.write(markdown_content)

print("Markdown summary generated successfully at outputs/summary.md:")
print(markdown_content)
