import os
import markdown
from fpdf import FPDF

def convert_md_to_pdf(input_md_path: str, output_pdf_path: str):
    with open(input_md_path, 'r', encoding='utf-8') as f:
        md_text = f.read()

    # Convert Markdown to HTML with table support
    html_content = markdown.markdown(md_text, extensions=['tables', 'fenced_code'])

    # Create PDF
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font('Helvetica', size=12)
    pdf.write_html(html_content)

    os.makedirs(os.path.dirname(output_pdf_path), exist_ok=True)
    pdf.output(output_pdf_path)

if __name__ == '__main__':
    convert_md_to_pdf('notes.md', 'outputs/notes.pdf')
