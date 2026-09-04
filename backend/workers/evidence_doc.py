# backend/workers/evidence_doc.py
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

def build_explanation_letter(case: dict, claim: dict, evidence_rows: list) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    s = getSampleStyleSheet()
    flow = [
        Paragraph(f"Dispute Response — {case['dispute_id']}", s["Title"]),
        Spacer(1, 12),
        Paragraph(f"Payment ID: {case['payment_id']}", s["Normal"]),
        Paragraph(f"Order ID: {case['order_id']}", s["Normal"]),
        Paragraph(f"Amount: {case['currency']} {case['amount']}", s["Normal"]),
        Paragraph(f"Reason code: {case['reason_code']}", s["Normal"]),
        Spacer(1, 18),
        Paragraph("Merchant Response", s["Heading2"]),
        Paragraph(claim["statement"].replace("\n", "<br/>"), s["Normal"]),
        Spacer(1, 18),
        Paragraph("Supporting Evidence", s["Heading2"]),
    ]
    for e in evidence_rows:
        flow.append(Paragraph(
            f"<b>{e['type']}</b> — source: {e['source']}, "
            f"ref: {e.get('source_record_id')}, hash: {e['content_hash'][:16]}…",
            s["Normal"]))
    doc.build(flow)
    return buf.getvalue()