# --------------------------------------------------------------------------------
#       Payroll PDF Service - ReportLab Deterministic A4 Payslip Generator
# --------------------------------------------------------------------------------

import base64
import io
from decimal import Decimal

from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image as RLImage,
    KeepTogether,
    HRFlowable,
)
from reportlab.lib.units import mm, inch
from PIL import Image as PILImage


def _format_currency(val, currency='INR'):
    try:
        dec = Decimal(str(val))
        return f"{currency} {dec:,.2f}"
    except Exception:
        return f"{currency} 0.00"


def _safe_load_image(logo_data):
    """
    Safely parses base64 data URI, raw base64, or image bytes.
    Returns a BytesIO stream suitable for ReportLab, or None if invalid.
    """
    if not logo_data or not isinstance(logo_data, str) or not logo_data.strip():
        return None

    data_str = logo_data.strip()
    if data_str.startswith('data:image'):
        try:
            _, encoded = data_str.split(',', 1)
            img_bytes = base64.b64decode(encoded)
            buf = io.BytesIO(img_bytes)
            # Verify valid image using Pillow
            with PILImage.open(buf) as pil_img:
                pil_img.verify()
            buf.seek(0)
            return buf
        except Exception:
            return None

    try:
        img_bytes = base64.b64decode(data_str)
        buf = io.BytesIO(img_bytes)
        with PILImage.open(buf) as pil_img:
            pil_img.verify()
        buf.seek(0)
        return buf
    except Exception:
        return None


def generate_payslip_pdf(payslip):
    """
    Generates an authoritative, immutable A4 PDF payslip from frozen Payslip
    and PayrollEmployeeSnapshot records.

    Returns:
        io.BytesIO: Buffer containing the binary PDF stream.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"Payslip_{payslip.payslip_number}",
        author="CubeLogs Payroll Engine",
    )

    styles = getSampleStyleSheet()
    
    # Custom Typography Styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=17,
        textColor=colors.HexColor('#0f172a'),
    )
    
    company_name_style = ParagraphStyle(
        'CompanyName',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=16,
        textColor=colors.HexColor('#1e293b'),
    )

    company_sub_style = ParagraphStyle(
        'CompanySub',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=11,
        textColor=colors.HexColor('#64748b'),
    )

    section_header_style = ParagraphStyle(
        'SectionHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#0f172a'),
        textTransform='uppercase',
    )

    body_bold = ParagraphStyle(
        'BodyBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#1e293b'),
    )

    body_regular = ParagraphStyle(
        'BodyRegular',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#334155'),
    )

    body_right = ParagraphStyle(
        'BodyRight',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=11,
        alignment=2,
        textColor=colors.HexColor('#334155'),
    )

    body_right_bold = ParagraphStyle(
        'BodyRightBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        alignment=2,
        textColor=colors.HexColor('#0f172a'),
    )

    footer_style = ParagraphStyle(
        'FooterStyle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=10,
        alignment=1,
        textColor=colors.HexColor('#94a3b8'),
    )

    superseded_banner_style = ParagraphStyle(
        'SupersededBanner',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=13,
        alignment=1,
        textColor=colors.HexColor('#b91c1c'),
    )

    elements = []

    # -------------------------------------------------------------------------
    # 1. Company & Header Section
    # -------------------------------------------------------------------------
    company_details = payslip.company_details_snapshot or {}
    employee_details = payslip.employee_details_snapshot or {}
    snap = payslip.payroll_snapshot
    currency = snap.currency or 'INR'

    # Company Logo loading
    logo_flowable = None
    logo_raw = company_details.get('brandLogo')
    if logo_raw:
        img_buf = _safe_load_image(logo_raw)
        if img_buf:
            try:
                logo_flowable = RLImage(img_buf, width=28 * mm, height=28 * mm)
            except Exception:
                logo_flowable = None

    company_lines = []
    company_lines.append(Paragraph(f"<b>{payslip.company_name_snapshot or 'Company'}</b>", company_name_style))
    if company_details.get('address'):
        company_lines.append(Paragraph(company_details['address'], company_sub_style))
    contact_parts = []
    if company_details.get('email'):
        contact_parts.append(company_details['email'])
    if company_details.get('phone'):
        contact_parts.append(company_details['phone'])
    if contact_parts:
        company_lines.append(Paragraph(" • ".join(contact_parts), company_sub_style))
    if company_details.get('tax_id'):
        company_lines.append(Paragraph(f"Tax / TRN ID: {company_details['tax_id']}", company_sub_style))

    month_names = [
        'January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December'
    ]
    period_year = payslip.payroll_period.year
    period_month = payslip.payroll_period.month
    month_label = f"{month_names[period_month - 1]} {period_year}"

    doc_info_lines = [
        Paragraph(f"<b>SALARY PAYSLIP</b>", title_style),
        Paragraph(f"<b>Period:</b> {month_label}", company_sub_style),
        Paragraph(f"<b>Payslip No:</b> {payslip.payslip_number}", company_sub_style),
        Paragraph(f"<b>Issue Date:</b> {payslip.issued_at.strftime('%d %b %Y') if payslip.issued_at else '-'}", company_sub_style),
        Paragraph(f"<b>Status:</b> {payslip.status} (Revision {payslip.revision})", company_sub_style),
    ]

    header_table_data = [
        [
            logo_flowable if logo_flowable else company_lines,
            company_lines if logo_flowable else '',
            doc_info_lines
        ]
    ]

    col_widths = [32 * mm, 78 * mm, 72 * mm] if logo_flowable else [110 * mm, 0 * mm, 72 * mm]
    header_table = Table(header_table_data, colWidths=col_widths)
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (2, 0), (2, 0), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 4 * mm))

    # -------------------------------------------------------------------------
    # 2. Superseded Warning Banner (if applicable)
    # -------------------------------------------------------------------------
    if payslip.status == 'Superseded':
        superseded_table = Table([
            [Paragraph("⚠️ <b>SUPERSEDED PAYSLIP — REVISED BY A SUBSEQUENT PAYROLL FINALIZATION</b>", superseded_banner_style)]
        ], colWidths=[182 * mm])
        superseded_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fef2f2')),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#f87171')),
            ('PADDING', (0, 0), (-1, -1), 6),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ]))
        elements.append(superseded_table)
        elements.append(Spacer(1, 4 * mm))

    # -------------------------------------------------------------------------
    # 3. Employee Information Box
    # -------------------------------------------------------------------------
    emp_code = employee_details.get('employee_code') or f"EMP-{payslip.employee_id:04d}"
    emp_name = employee_details.get('name') or snap.employee_name
    emp_designation = employee_details.get('designation') or snap.designation or 'Staff'
    emp_department = employee_details.get('department') or '-'
    comp_type = getattr(snap, 'compensation_type', 'MONTHLY') or 'MONTHLY'
    calc_breakdown = snap.calculation_breakdown or {}

    if comp_type == 'HOURLY':
        pay_basis_label = f"Hourly Wage ({_format_currency(snap.hourly_rate, currency)}/hour)"
    elif comp_type == 'DAILY':
        pay_basis_label = f"Daily Wage ({_format_currency(snap.daily_rate, currency)}/day)"
    else:
        pay_basis_label = "Monthly Salary"

    emp_info_data = [
        [
            Paragraph("<b>Employee Name:</b>", body_regular),
            Paragraph(f"<b>{emp_name}</b>", body_bold),
            Paragraph("<b>Employee Code:</b>", body_regular),
            Paragraph(emp_code, body_bold),
        ],
        [
            Paragraph("<b>Designation:</b>", body_regular),
            Paragraph(emp_designation, body_regular),
            Paragraph("<b>Department:</b>", body_regular),
            Paragraph(emp_department, body_regular),
        ],
        [
            Paragraph("<b>Pay Basis:</b>", body_regular),
            Paragraph(pay_basis_label, body_bold),
            Paragraph("<b>Currency:</b>", body_regular),
            Paragraph(currency, body_bold),
        ]
    ]

    emp_table = Table(emp_info_data, colWidths=[32 * mm, 59 * mm, 32 * mm, 59 * mm])
    emp_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(emp_table)
    elements.append(Spacer(1, 4 * mm))

    # -------------------------------------------------------------------------
    # 4. Attendance Metrics Ribbon
    # -------------------------------------------------------------------------
    att_snap = snap.attendance_summary_snapshot or {}
    working_days = att_snap.get('working_days', snap.working_days)
    present_days = att_snap.get('present_days', 0.0)
    paid_leaves = att_snap.get('paid_leave_days', snap.paid_leave_days)
    unpaid_leaves = att_snap.get('unpaid_leave_days', snap.unpaid_leave_days)
    absent_days = att_snap.get('absent_days', snap.absent_days)
    payable_units = att_snap.get('payable_attendance_units', snap.payable_attendance_units)

    att_unit_header = "<b>Payable Hours</b>" if comp_type == 'HOURLY' else "<b>Payable Units</b>"
    att_unit_display = f"{snap.payable_hours} hrs" if comp_type == 'HOURLY' else f"{payable_units}"

    att_table_data = [
        [
            Paragraph("<b>Working Days</b>", section_header_style),
            Paragraph("<b>Present Days</b>", section_header_style),
            Paragraph("<b>Paid Leaves</b>", section_header_style),
            Paragraph("<b>Unpaid Days</b>", section_header_style),
            Paragraph("<b>Absent Days</b>", section_header_style),
            Paragraph(att_unit_header, section_header_style),
        ],
        [
            Paragraph(f"{working_days}", body_bold),
            Paragraph(f"{present_days}", body_regular),
            Paragraph(f"{paid_leaves}", body_regular),
            Paragraph(f"{unpaid_leaves}", body_regular),
            Paragraph(f"{absent_days}", body_regular),
            Paragraph(f"<b>{att_unit_display}</b>", body_bold),
        ]
    ]

    att_table = Table(att_table_data, colWidths=[30 * mm, 30 * mm, 30 * mm, 30 * mm, 30 * mm, 32 * mm])
    att_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f1f5f9')),
        ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#ffffff')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(att_table)
    elements.append(Spacer(1, 4 * mm))

    # -------------------------------------------------------------------------
    # 5. Earnings & Deductions Breakdown Tables (Side-by-Side)
    # -------------------------------------------------------------------------
    # 5a. Build Earnings Lines
    earnings_rows = []
    components = snap.salary_components_snapshot or []

    if comp_type == 'HOURLY':
        wage_earn = calc_breakdown.get('wage_earnings', str(snap.earned_gross))
        hourly_rate_str = _format_currency(snap.hourly_rate, currency)
        hours_str = snap.payable_hours if hasattr(snap, 'payable_hours') and snap.payable_hours else calc_breakdown.get('payable_hours', '0.00')
        earnings_rows.append([
            Paragraph(f"Hourly Wage Earnings ({hours_str} hrs @ {hourly_rate_str}/hr)", body_regular),
            Paragraph(_format_currency(wage_earn, currency), body_right)
        ])
        for comp in components:
            if comp.get('component_type') == 'Earning':
                name = comp.get('name', 'Fixed Allowance')
                amt = comp.get('amount', '0.00')
                earnings_rows.append([
                    Paragraph(f"{name} (Fixed)", body_regular),
                    Paragraph(_format_currency(amt, currency), body_right)
                ])
    elif comp_type == 'DAILY':
        wage_earn = calc_breakdown.get('wage_earnings', str(snap.earned_gross))
        earnings_rows.append([
            Paragraph(f"Daily Wage Earnings ({payable_units} days @ {_format_currency(snap.daily_rate, currency)}/day)", body_regular),
            Paragraph(_format_currency(wage_earn, currency), body_right)
        ])
        for comp in components:
            if comp.get('component_type') == 'Earning':
                name = comp.get('name', 'Fixed Allowance')
                amt = comp.get('amount', '0.00')
                earnings_rows.append([
                    Paragraph(f"{name} (Fixed)", body_regular),
                    Paragraph(_format_currency(amt, currency), body_right)
                ])
    else:
        for comp in components:
            if comp.get('component_type') == 'Earning':
                name = comp.get('name', 'Earning')
                prorated_tag = " (Prorated)" if comp.get('is_proratable') else ""
                amt = comp.get('amount', '0.00')
                earnings_rows.append([
                    Paragraph(f"{name}{prorated_tag}", body_regular),
                    Paragraph(_format_currency(amt, currency), body_right)
                ])

    if Decimal(str(snap.additional_earnings)) > Decimal('0.00'):
        earnings_rows.append([
            Paragraph("Additional Earnings / Bonuses", body_regular),
            Paragraph(_format_currency(snap.additional_earnings, currency), body_right)
        ])

    # 5b. Build Deductions Lines
    deductions_rows = []
    for comp in components:
        if comp.get('component_type') == 'Deduction':
            name = comp.get('name', 'Deduction')
            amt = comp.get('amount', '0.00')
            deductions_rows.append([
                Paragraph(name, body_regular),
                Paragraph(_format_currency(amt, currency), body_right)
            ])

    if Decimal(str(snap.attendance_deduction)) > Decimal('0.00'):
        deductions_rows.append([
            Paragraph("Attendance Deductions (Unpaid Days)", body_regular),
            Paragraph(f"-{_format_currency(snap.attendance_deduction, currency)}", body_right)
        ])

    if Decimal(str(snap.additional_deductions)) > Decimal('0.00'):
        deductions_rows.append([
            Paragraph("Additional Deductions / Penalties", body_regular),
            Paragraph(f"-{_format_currency(snap.additional_deductions, currency)}", body_right)
        ])

    # Pad rows so both tables align vertically
    max_rows = max(len(earnings_rows), len(deductions_rows), 1)
    while len(earnings_rows) < max_rows:
        earnings_rows.append([Paragraph("", body_regular), Paragraph("", body_right)])
    while len(deductions_rows) < max_rows:
        deductions_rows.append([Paragraph("", body_regular), Paragraph("", body_right)])

    # Construct Earnings Table
    earn_table_data = [
        [Paragraph("<b>EARNINGS</b>", section_header_style), Paragraph("<b>AMOUNT</b>", body_right_bold)]
    ] + earnings_rows + [
        [Paragraph("<b>TOTAL EARNED GROSS</b>", body_bold), Paragraph(f"<b>{_format_currency(snap.earned_gross + snap.additional_earnings, currency)}</b>", body_right_bold)]
    ]
    earn_table = Table(earn_table_data, colWidths=[58 * mm, 31 * mm])
    earn_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f1f5f9')),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f8fafc')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor('#cbd5e1')),
        ('LINEABOVE', (0, -1), (-1, -1), 1, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#f1f5f9')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
    ]))

    # Construct Deductions Table
    ded_table_data = [
        [Paragraph("<b>DEDUCTIONS</b>", section_header_style), Paragraph("<b>AMOUNT</b>", body_right_bold)]
    ] + deductions_rows + [
        [Paragraph("<b>TOTAL DEDUCTIONS</b>", body_bold), Paragraph(f"<b>-{_format_currency(snap.total_deductions, currency)}</b>", body_right_bold)]
    ]
    ded_table = Table(ded_table_data, colWidths=[58 * mm, 31 * mm])
    ded_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f1f5f9')),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f8fafc')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor('#cbd5e1')),
        ('LINEABOVE', (0, -1), (-1, -1), 1, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#f1f5f9')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
    ]))

    two_col_table = Table([[earn_table, ded_table]], colWidths=[91 * mm, 91 * mm])
    two_col_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(two_col_table)
    elements.append(Spacer(1, 5 * mm))

    # -------------------------------------------------------------------------
    # 6. Final Net Payable Highlight Box
    # -------------------------------------------------------------------------
    net_box_data = [
        [
            Paragraph("<b>NET SALARY PAYABLE</b>", ParagraphStyle(
                'NetLabel',
                parent=styles['Normal'],
                fontName='Helvetica-Bold',
                fontSize=11,
                leading=14,
                textColor=colors.HexColor('#166534')
            )),
            Paragraph(f"<b>{_format_currency(snap.net_payable, currency)}</b>", ParagraphStyle(
                'NetValue',
                parent=styles['Normal'],
                fontName='Helvetica-Bold',
                fontSize=14,
                leading=17,
                alignment=2,
                textColor=colors.HexColor('#166534')
            ))
        ]
    ]
    net_table = Table(net_box_data, colWidths=[90 * mm, 92 * mm])
    net_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f0fdf4')),
        ('BOX', (0, 0), (-1, -1), 1.5, colors.HexColor('#bbf7d0')),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(net_table)
    elements.append(Spacer(1, 6 * mm))

    # -------------------------------------------------------------------------
    # 7. Document Footer Note
    # -------------------------------------------------------------------------
    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#e2e8f0'), spaceBefore=2, spaceAfter=4))
    elements.append(Paragraph("This is an electronically generated document produced by CubeLogs Payroll Engine. No signature is required.", footer_style))
    elements.append(Paragraph("Confidential • For Employee Personal Record Only", footer_style))

    # Build Document
    doc.build(elements)
    buffer.seek(0)
    return buffer
