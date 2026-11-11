"""G7 Excel/PDF rendering from verified, frozen snapshots. Never export source PHI by default."""
from io import BytesIO
from html import escape
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from mcias.reporting_g7 import verify_snapshot, ReportBlocked

def _text_cell(sheet, row, column, value):
    # Always store source values as literal strings; Excel must not execute "=..."
    # or coerce long identifiers to floating point.
    value = "" if value is None else str(value)
    if len(value) > 32767:
        raise ReportBlocked("source cell exceeds Excel text length; no silent truncation")
    cell = sheet.cell(row, column, value)
    cell.data_type = "s"
    cell.number_format = "@"
    return cell

def render_xlsx(report, *, include_originals=False, language="en"):
    if language not in {"ar", "en"}:
        raise ReportBlocked("unsupported report language")
    verify_snapshot(report)
    wb = Workbook()
    ws = wb.active
    ws.title = "الملخص التنفيذي" if language == "ar" else "Executive Summary"
    ws.sheet_view.rightToLeft = language == "ar"
    headings = [
        ["MCIAS | Frozen Financial Report"],
        ["Report ID", report.id], ["Version", report.version],
        ["Frozen SHA-256", report.fingerprint],
        ["Batch", report.batch_id], ["Source SHA-256", report.dataset["source_sha256"]],
        ["Total claims", report.summary["total_claims"]], [],
        ["Currency", "Claimed", "Approved claimed", "Held claimed", "Payable",
         "Deductions", "Approved count", "Held count"],
    ]
    if language == "ar":
        headings = [
            ["MCIAS | تقرير مراجعة المطالبات"],
            ["معرّف التقرير", report.id], ["رقم النسخة", report.version],
            ["بصمة النسخة SHA-256", report.fingerprint],
            ["الدفعة", report.batch_id], ["بصمة المصدر SHA-256", report.dataset["source_sha256"]],
            ["إجمالي المطالبات", report.summary["total_claims"]], [],
            ["العملة", "المطالب به", "المطالبات المعتمدة", "المطالبات المعلقة",
             "صافي المستحق", "الخصومات", "عدد المعتمدة", "عدد المعلقة"],
        ]
    for row in headings:
        ws.append(row)
    for currency, item in sorted(report.summary["by_currency"].items()):
        ws.append([currency, item["claimed"], item["approved_claimed"],
                   item["held_claimed"], item["payable"], item["deductions"],
                   item["approved_count"], item["held_count"]])
    ws.freeze_panes = "A10"
    ws.column_dimensions["A"].width = 29
    for col in "BCDEFGH":
        ws.column_dimensions[col].width = 23
    for cell in ws[9]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="243B53")
        cell.alignment = Alignment(horizontal="center")
    ws.sheet_view.showGridLines = False
    for row in ws:
        for cell in row:
            cell.alignment = Alignment(horizontal="right" if language == "ar" else "left")
    for cell in ws[9]:
        cell.alignment = Alignment(horizontal="center")
    if include_originals:
        raw = wb.create_sheet("القيم الأصلية" if language == "ar" else "Original Source Values")
        raw.sheet_view.rightToLeft = language == "ar"
        fields = sorted({k for item in report.dataset["rows"] for k in item["source_values"]})
        headers = (["معرّف المطالبة", "معرّف سجل المصدر", "موضع المصدر"]
                   if language == "ar" else ["Claim ID", "Source Record ID", "Source Locator"]) + fields
        for col, heading in enumerate(headers, start=1):
            _text_cell(raw, 1, col, heading)
            cell = raw.cell(1, col)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="243B53")
        import json
        for index, item in enumerate(report.dataset["rows"], start=2):
            values = [item["claim_id"], item["source_record_id"],
                      json.dumps(item["source_locator"], ensure_ascii=False, sort_keys=True)]
            values += [item["source_values"].get(field) for field in fields]
            for col, value in enumerate(values, start=1):
                _text_cell(raw, index, col, value)
        raw.freeze_panes = "D2"
        raw.auto_filter.ref = raw.dimensions
        for col in raw.columns:
            raw.column_dimensions[col[0].column_letter].width = min(38, max(16, len(str(col[0].value)) + 2))
    output = BytesIO()
    wb.save(output)
    return output.getvalue()

def render_pdf(report):
    verify_snapshot(report)
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, title="MCIAS Frozen Report",
                            leftMargin=36, rightMargin=36, topMargin=44, bottomMargin=40)
    style = getSampleStyleSheet()
    story = [Paragraph("MCIAS | Executive Financial Report", style["Title"]),
             Spacer(1, 14),
             Paragraph("Frozen version: " + str(report.version), style["Normal"]),
             Paragraph("Batch: " + escape(report.batch_id), style["Normal"]),
             Paragraph("SHA-256: " + escape(report.fingerprint), style["Normal"]),
             Paragraph("Total claims: " + str(report.summary["total_claims"]), style["Normal"]),
             Spacer(1, 16)]
    rows = [["Currency", "Claimed", "Approved", "Held", "Payable", "Deductions"]]
    for currency, item in sorted(report.summary["by_currency"].items()):
        rows.append([currency, item["claimed"], item["approved_claimed"],
                     item["held_claimed"], item["payable"], item["deductions"]])
    table = Table(rows, repeatRows=1, hAlign="LEFT", colWidths=[64, 91, 91, 91, 91, 91])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#243B53")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#D7E2EF")),
    ]))
    story += [table, Spacer(1, 18),
              Paragraph("Held items are never treated as approved payable amounts.", style["Normal"]),
              Paragraph("Source/patient identifiers are intentionally absent from this PDF.", style["Normal"])]
    doc.build(story)
    return output.getvalue()

def render_result_partition(dataset, bucket):
    """Render the green preliminary match file or the complete red action file."""
    from mcias.services.result_service import BUCKETS, LABELS, partition
    if bucket not in (*BUCKETS,'requires_review'):raise ReportBlocked('Unsupported output category')
    partition(dataset['rows'],[row['line_id'] for row in dataset['rows']])
    label=LABELS.get(bucket,'مراجعة بشرية أو مرفوض')
    wb=Workbook();sheet=wb.active;sheet.title=label;sheet.sheet_view.rightToLeft=True
    columns=[('line_id','معرف البند'),('invoice_number','رقم الفاتورة'),('provider_code','مقدم الخدمة'),
        ('service_code','الخدمة'),('service_date','تاريخ الخدمة'),('currency','العملة'),
        ('claimed_net','الصافي الأصلي'),('proposed_net','الصافي المقترح'),
        ('proposed_difference','الفرق المقترح'),('reason','سبب النتيجة'),('audited','تم تدقيقه'),
        ('audit_snapshot_id','مرجع لقطة التدقيق'),('source_record_id','سجل المصدر')]
    target_fill='15803D' if bucket=='accepted' else 'B91C1C'
    reason_labels={'not_audited':'لم يُدقّق بعد','missing_invoice':'فاتورة الخدمة غير مرفقة',
        'missing_or_ambiguous_signed_policy':'لا توجد سياسة معتمدة وحيدة لهذه المطالبة',
        'unresolved_reference_or_evidence':'مرجع أو دليل لازم غير مكتمل',
        'policy_or_evidence_blocked':'تعذر التحقق من السياسة أو الدليل',
        'unresolved_review_finding':'توجد ملاحظة تتطلب مراجعة بشرية',
        'verification_failed':'فشل التحقق المستقل',
        'net_basis_confirmation_required':'يلزم تأكيد أن المبلغ صافي بعد التحمل',
        'possible_second_copay_deduction':'احتمال خصم التحمل مرتين',
        'proven_financial_difference':'ثبت وجود فرق مالي','verified_match':'اجتاز الفحص المبدئي',
        'invoice_evidence_changed_reaudit_required':'تغيرت الفاتورة بعد التدقيق؛ يلزم إعادة التدقيق'}
    for col,(_,title) in enumerate(columns,1):
        cell=_text_cell(sheet,1,col,title);cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor=target_fill)
    selected=(r for r in dataset['rows'] if (r['bucket']=='accepted' if bucket=='accepted' else r['bucket']!='accepted'))
    for row_index,row in enumerate(selected,2):
        for col,(key,_) in enumerate(columns,1):
            if key=='reason':
                reasons=row.get('reasons') or []
                def label(reason):
                    if isinstance(reason,dict):
                        code=str(reason.get('code',''));message=str(reason.get('message',code))
                        return reason_labels.get(message,reason_labels.get(code,message))
                    return reason_labels.get(str(reason),str(reason))
                value='؛ '.join(label(reason) for reason in reasons)
                value=row.get('reason_ar') if len(reasons)<=1 and row.get('reason_ar') else value or row.get(key)
            else:value=row.get(key)
            _text_cell(sheet,row_index,col,value)
    sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
    for col in sheet.columns:sheet.column_dimensions[col[0].column_letter].width=24
    sheet.sheet_properties.pageSetUpPr.fitToPage=True
    output=BytesIO();wb.save(output);return output.getvalue()


def render_phase1_file(dataset, color):
    from mcias.services.phase1_rules import (FIXED_HEADERS, REASON_CATEGORIES,
        STATUS_LABELS, ACCEPTED, REJECTED, validate_dataset)
    if color not in ('green', 'red'):
        raise ReportBlocked('Unsupported phase 1 file color')
    summary = validate_dataset(dataset)
    target = ACCEPTED if color == 'green' else REJECTED
    rows = [row for row in dataset['rows'] if row['status'] == target]
    expected = summary['accepted' if color == 'green' else 'rejected']
    if len(rows) != expected:
        raise ReportBlocked('Phase 1 output partition mismatch')
    label = STATUS_LABELS[target]
    wb = Workbook()
    sheet = wb.active
    sheet.title = label
    sheet.sheet_view.rightToLeft = True
    headers = list(FIXED_HEADERS) + ['الحالة', 'الصافي المحتسب']
    if color == 'red':
        headers += [title for _, title in REASON_CATEGORIES]
    headers += ['ملاحظات']
    fill = '15803D' if color == 'green' else 'B91C1C'
    for col, title in enumerate(headers, 1):
        cell = _text_cell(sheet, 1, col, title)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor=fill)
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    for index, row in enumerate(rows, 2):
        values = [row.get('values', {}).get(header, '') for header in FIXED_HEADERS]
        values.append(row.get('status_ar') or STATUS_LABELS[row['status']])
        values.append(row.get('computed', {}).get('net_amount', ''))
        if color == 'red':
            for key, _ in REASON_CATEGORIES:
                values.append('؛ '.join(row.get('reasons', {}).get(key, [])))
        values.append('؛ '.join(row.get('notes', [])))
        for col, value in enumerate(values, 1):
            cell = _text_cell(sheet, index, col, value)
            cell.alignment = Alignment(horizontal='right', vertical='top', wrap_text=True)
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    widths = [22] * len(FIXED_HEADERS) + [16, 16]
    if color == 'red':
        widths += [32] * len(REASON_CATEGORIES)
    widths += [40]
    for col, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(col)].width = width
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = 'landscape'
    output = BytesIO()
    wb.save(output)
    return output.getvalue()
