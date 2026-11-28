
import csv
from collections import defaultdict

def summarize_sales(input_file):
    # Dictionary to store the summarized sales data
    sales_data = defaultdict(lambda: defaultdict(float))

    # Read the sales data from the CSV file
    with open(input_file, 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            # Extract the month from the date (YYYY-MM)
            month = '-'.join(row['date'].split('-')[:2])
            region = row['region']
            revenue = float(row['revenue'])
            
            # Add the revenue to the corresponding region and month
            sales_data[region][month] += revenue

    # Get the unique months and sort them
    months = sorted(list(set(month for region_data in sales_data.values() for month in region_data.keys())))

    # Build the Markdown table
    header = '| Region | ' + ' | '.join(months) + ' |'
    separator = '|--------|' + '|'.join(['---'] * len(months)) + '|'
    
    rows = [header, separator]
    for region, month_data in sorted(sales_data.items()):
        row = f'| {region} |'
        for month in months:
            row += f' {month_data.get(month, 0):.2f} |'
        rows.append(row)

    return '\n'.join(rows)

if __name__ == "__main__":
    # Specify the input file path
    input_file_path = 'sales.csv'

    # Get the summarized sales data in Markdown format
    markdown_output = summarize_sales(input_file_path)

    # Print the Markdown table
    print(markdown_output)

