from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet

def generate_resume_pdf(sections, output_path):
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(output_path)
    flow = []

    for title, sec in sections.items():
        flow.append(Paragraph(f"<b>{title.upper()}</b>", styles["Heading2"]))
        flow.append(Paragraph(sec["after"], styles["Normal"]))

    doc.build(flow)