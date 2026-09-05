# --------------------------------------------------------------------------------
#       Monthly Invoice PDF Service - ReportLab Deterministic A4 Invoice Generator
# --------------------------------------------------------------------------------

import io
from decimal import Decimal

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
)
from reportlab.lib.units import mm


def _format_currency(val, currency='INR'):
    try:
        dec = Decimal(str(val))
        return f"₹{dec:,.2f} {currency}"
    except Exception:
        return f"₹0.00 {currency}"


def generate_invoice_pdf(invoice):
    """
    Renders an A4 PDF document for a MonthlyInvoice using stored immutable snapshot fields.
    Returns BytesIO buffer.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
    )

    story = []
    styles = getSampleStyleSheet()

    # Base typography styles
    header_title_style = ParagraphStyle(
        'InvoiceHeaderTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#0F172A'),
    )
    brand_subtitle_style = ParagraphStyle(
        'InvoiceBrandSub',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=12,
        textColor=colors.HexColor('#2563EB'),
    )
    normal_style = ParagraphStyle(
        'InvoiceNormal',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#334155'),
    )
    bold_style = ParagraphStyle(
        'InvoiceBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#0F172A'),
    )

    org = invoice.organization
    org_name = org.name if org else "Organization"
    billing_month_str = invoice.billing_month.strftime('%B %Y') if invoice.billing_month else "N/A"
    inv_type_display = dict(invoice.INVOICE_TYPE_CHOICES).get(invoice.invoice_type, invoice.invoice_type)

    # 1. Header Banner
    header_data = [
        [
            Paragraph("<b>CubeLogs</b><br/><font color='#64748B' size='8'>TAX / SERVICE INVOICE</font>", header_title_style),
            Paragraph(f"<b>INVOICE #{invoice.id}</b><br/>Date: {invoice.created_at.strftime('%Y-%m-%d') if invoice.created_at else 'N/A'}<br/>Status: <b>{'PAID' if invoice.is_paid else 'UNPAID'}</b>", normal_style),
        ]
    ]
    header_table = Table(header_data, colWidths=[100 * mm, 80 * mm])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 10 * mm))

    # 2. Billed To Info
    billed_to_data = [
        [
            Paragraph("<b>Billed To:</b>", bold_style),
            Paragraph("<b>Issued By:</b>", bold_style)
        ],
        [
            Paragraph(f"<b>{org_name}</b><br/>Billing Period: {billing_month_str}<br/>Type: {inv_type_display}", normal_style),
            Paragraph("<b>CubeLogs Inc.</b><br/>Email: support@cubelogs.com<br/>Web: https://cubelogs.com", normal_style)
        ]
    ]
    info_table = Table(billed_to_data, colWidths=[90 * mm, 90 * mm])
    info_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 8 * mm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#E2E8F0'), spaceAfter=8 * mm))

    # 3. Itemized Breakdown Table
    table_headers = [
        Paragraph("<b>Description</b>", bold_style),
        Paragraph("<b>Calculation / Details</b>", bold_style),
        Paragraph("<b>Amount</b>", ParagraphStyle('RAlign', parent=bold_style, alignment=2)),
    ]
    table_rows = [table_headers]

    base_p = invoice.base_price_snapshot or Decimal('0.00')
    emp_cnt = invoice.employee_count_snapshot or 0
    emp_unit = invoice.employee_unit_price_snapshot or Decimal('0.00')
    emp_tot = invoice.employee_total_snapshot or Decimal('0.00')
    att_p = invoice.attendance_total_snapshot or invoice.attendance_price_snapshot or Decimal('0.00')
    att_unit = invoice.attendance_unit_price_snapshot
    proj_p = invoice.project_total_snapshot or invoice.project_price_snapshot or Decimal('0.00')
    proj_unit = invoice.project_unit_price_snapshot

    if base_p > 0:
        table_rows.append([
            Paragraph("Base Platform Subscription", normal_style),
            Paragraph("Monthly Base Fee", normal_style),
            Paragraph(_format_currency(base_p), ParagraphStyle('R', parent=normal_style, alignment=2)),
        ])

    table_rows.append([
        Paragraph("Employee Seats", normal_style),
        Paragraph(f"{emp_cnt} billable seats × {_format_currency(emp_unit)}", normal_style),
        Paragraph(_format_currency(emp_tot), ParagraphStyle('R', parent=normal_style, alignment=2)),
    ])

    if invoice.attendance_enabled_snapshot:
        att_desc = f"{emp_cnt} billable seats × {_format_currency(att_unit)}" if att_unit else "Time & Attendance Tracking"
        table_rows.append([
            Paragraph("Attendance Module", normal_style),
            Paragraph(att_desc, normal_style),
            Paragraph(_format_currency(att_p), ParagraphStyle('R', parent=normal_style, alignment=2)),
        ])

    if invoice.project_enabled_snapshot:
        proj_desc = f"{emp_cnt} billable seats × {_format_currency(proj_unit)}" if proj_unit else "Project Management & Tracking"
        table_rows.append([
            Paragraph("Projects & Tasks Module", normal_style),
            Paragraph(proj_desc, normal_style),
            Paragraph(_format_currency(proj_p), ParagraphStyle('R', parent=normal_style, alignment=2)),
        ])

    subtotal = invoice.subtotal_snapshot or invoice.amount
    tax_pct = invoice.tax_percentage_snapshot or Decimal('0.00')
    tax_amt = invoice.tax_amount_snapshot or Decimal('0.00')

    if tax_amt > 0 or base_p > 0:
        table_rows.append([
            Paragraph("<b>Subtotal</b>", bold_style),
            Paragraph("", normal_style),
            Paragraph(f"<b>{_format_currency(subtotal)}</b>", ParagraphStyle('R', parent=bold_style, alignment=2)),
        ])

    if tax_amt > 0:
        table_rows.append([
            Paragraph(f"Tax ({tax_pct}%)", normal_style),
            Paragraph("Applicable Tax", normal_style),
            Paragraph(_format_currency(tax_amt), ParagraphStyle('R', parent=normal_style, alignment=2)),
        ])

    table_rows.append([
        Paragraph("<b>Total Amount Due</b>", ParagraphStyle('T', parent=bold_style, fontSize=11)),
        Paragraph("", normal_style),
        Paragraph(f"<b>{_format_currency(invoice.amount)}</b>", ParagraphStyle('TR', parent=bold_style, fontSize=11, alignment=2, textColor=colors.HexColor('#2563EB'))),
    ])

    inv_table = Table(table_rows, colWidths=[65 * mm, 75 * mm, 40 * mm])
    inv_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F8FAFC')),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(inv_table)
    story.append(Spacer(1, 10 * mm))

    # 4. Footer Payment Status
    paid_info = f"Paid on {invoice.paid_at.strftime('%Y-%m-%d %H:%M')}" if invoice.is_paid and invoice.paid_at else ("PAID" if invoice.is_paid else "UNPAID - Payment Pending")
    status_box = [
        [
            Paragraph(f"<b>Payment Status:</b> {paid_info}", normal_style)
        ]
    ]
    status_table = Table(status_box, colWidths=[180 * mm])
    status_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F1F5F9')),
        ('PADDING', (0, 0), (-1, -1), 10),
    ]))
    story.append(status_table)

    doc.build(story)
    buffer.seek(0)
    return buffer
