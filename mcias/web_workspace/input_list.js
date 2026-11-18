import { L, el, button, field, panel, badge, empty, loading, errorState, openDrawer, confirm, toast, details, preferences } from "./ui.js";

const day = () => new Date().toISOString().slice(0, 10);
export function inputList(ctx) {
  const { root, api, route, demo } = ctx, feature = route.feature;
  if (demo) {
    root.append(empty(L("سجل المدخلات", "Reference intake"),
      L("العرض الاصطناعي لا ينشئ سجلات. سجّل الدخول لعرض المدخلات المصرح بها.", "The synthetic preview creates no records. Sign in to view authorized intake."), route.icon));
    return;
  }
  const base = `/api/v1/registry/${encodeURIComponent(feature)}`;
  const card = panel(L("السجلات والمصادر", "Records and sources"),
    L("حفظ المدخل لا يفعّل سياسة مالية. تبقى النسخ والمصادر محفوظة للمراجعة المؤسسية.",
      "Saving intake does not activate financial policy. Versions and sources remain available for institutional review."), route.icon);
  const content = el("div"); card.append(content); root.append(card);
  let schema, offset = 0, generation = 0, selected = null;
  const clauseSection = ['inputs.contracts','inputs.regulations','inputs.exceptions','inputs.caps','inputs.coverage','inputs.approvals'].includes(feature);
  const inputApproval = feature.startsWith('inputs.');
  const provider = field('price-provider',L('مقدم الخدمة','Provider'),{required:false,options:[['',L('اختر مقدم الخدمة','Select provider')]]});
  let providerOptionsLoaded=false;
  provider.input.addEventListener('change',()=>{offset=0;selected=null;void load();});
  const search = field("intake-search", L("بحث", "Search"), { required: false, maxLength: 200 });
  const status = field("intake-status", L("الحالة", "Status"), { required: false,
    options: [["", L("الكل", "All")], ["open", L("مفتوح", "Open")], ["closed", L("مغلق", "Closed")]] });

  function recordForm(previous, evidenceDocumentId = null, onSubmit = null, parent = null) {
    const form = el("form"), inputs = new Map(), grid = el("div", null, "form-grid");
    const title = field("intake-title", L("الاسم / العنوان", "Name / title"), { maxLength: 200, value: previous?.title || "" });
    const from = field("intake-from", L("تاريخ بداية السريان", "Effective from"), { type: "date", value: previous?.effective_from || parent?.effective_from || day() });
    const until = field("intake-to", L("تاريخ نهاية السريان (اختياري)", "Effective until (optional)"), { type: "date", required: false, value:previous?.effective_to || '' });
    grid.append(title.wrap, from.wrap, until.wrap);
    for (const item of schema.fields) {
      const initial=previous?.values?.[item.key] || (item.key==='record_code'?'':parent?.values?.[item.key]) || (item.key==='provider_code'?provider.input.value:'');
      const control = field(`intake-${item.key}`, L(item.ar, item.en), { maxLength: item.max_length || 200, value: initial });
      if (item.key === "record_code" && previous) control.input.readOnly = true;
      if (item.key === "currency") control.input.pattern = "[A-Z]{3}";
      if (["amount", "percent"].includes(item.key)) control.input.inputMode = "decimal";
      if (item.key === "coding_system") control.input.placeholder = "LOCAL / ICD / CPT / LOINC / SNOMED";
      inputs.set(item.key, control.input); grid.append(control.wrap);
    }
    const relationControls=new Map(),termControls=new Map(),relations=schema.relationships||{};
    const hasMetadata=Object.keys(relations).length||(schema.term_fields||[]).length;
    let linked=null;
    if(hasMetadata){
      const box=el('fieldset'),legend=el('legend',L('روابط وقواعد الفحص','Validation links and terms'));
      const label=el('label'),toggle=el('input');toggle.type='checkbox';toggle.name='intake-linked';
      toggle.checked=Boolean(Object.keys(previous?.links||parent?.links||{}).length||Object.keys(previous?.terms||parent?.terms||{}).length);
      toggle.disabled=Boolean(Object.keys(previous?.links||{}).length||Object.keys(previous?.terms||{}).length);
      label.append(toggle,el('span',L('ربط هذا السجل للفحص بحسب الفئة والخدمة والعقد','Link this record for category, service and contract validation')));
      box.append(legend,label,el('p',L('السجل غير المرتبط يبقى محفوظًا ويحتاج استكمالًا قبل الفحص. النسبة هي تحمل المستفيد، وليست نسبة تغطية الجهة.','Unlinked records remain preserved and require completion before validation. The percentage is beneficiary copay, not institution coverage.')));
      const names={provider:['مقدم الخدمة المرتبط','Linked provider'],contract:['العقد المرتبط','Linked contract'],category:['الفئة المرتبطة','Linked category'],service:['الخدمة المرتبطة','Linked service']};
      for(const [role,options] of Object.entries(relations)){
        const control=field('intake-link-'+role,L(...names[role]),{options:[['',L('اختر المرجع','Select reference')],...options.map(r=>[r.id,r.title])]});
        control.input.value=previous?.links?.[role]||parent?.links?.[role]||'';
        control.input.addEventListener('change',()=>{const target=options.find(r=>r.id===control.input.value);if(!target)return;
          for(const [key,input] of inputs)if(key!=='record_code'&&target.values[key])input.value=target.values[key];});
        relationControls.set(role,control.input);box.append(control.wrap);
      }
      for(const item of schema.term_fields||[]){
        const control=field('intake-term-'+item.key,L(item.ar,item.en),item.options.length?{options:[['',L('اختر القيمة','Select value')],...item.options.map(([value,ar,en])=>[value,L(ar,en)])]}:{maxLength:3,pattern:'[A-Z]{3}'});
        const value=previous?.terms?.[item.key]??parent?.terms?.[item.key];control.input.value=value===undefined?'':String(value);
        termControls.set(item.key,control.input);box.append(control.wrap);
      }
      const update=()=>{for(const input of [...relationControls.values(),...termControls.values()]){input.disabled=!toggle.checked;input.required=toggle.checked;}};
      toggle.addEventListener('change',update);update();linked=toggle;grid.append(box);
    }
    const clause = parent ? field('intake-clause',L('رقم البند الهرمي، مثل 1.1','Hierarchical clause number, e.g. 1.1'),{maxLength:60,pattern:'[1-9][0-9]*(\\.[1-9][0-9]*)*'}) : null;
    if(clause)grid.append(clause.wrap);
    const bodyLabel=el('label',L('التفاصيل / نص البند','Details / clause text')),body=el('textarea');
    body.id='intake-body';body.maxLength=20000;body.rows=5;body.value=previous?.body||'';bodyLabel.htmlFor=body.id;grid.append(bodyLabel,body);
    const reason=previous?.status==='open'?field('intake-edit-reason',L('سبب التعديل','Reason for change'),{maxLength:2000}):null;
    if(reason)grid.append(reason.wrap);
    const feedback = el("div"); feedback.setAttribute("aria-live", "polite");
    const save = el("button", L("حفظ السجل", "Save record"), "button primary"); save.type = "submit";
    form.append(grid, save, feedback);
    form.addEventListener("submit", async event => {
      event.preventDefault(); if (save.disabled || !form.reportValidity()) return;
      save.disabled = true;
      try {
        const payload = { title: title.input.value.trim(), effective_from: from.input.value,
          effective_to: until.input.value || null, values: Object.fromEntries([...inputs].map(([key, input]) => [key, input.value.trim()])) };
        if (evidenceDocumentId) payload.evidence_document_id = evidenceDocumentId;
        payload.body=body.value;
        if(linked?.checked){
          payload.links=Object.fromEntries([...relationControls].map(([key,input])=>[key,input.value]));
          payload.terms=Object.fromEntries([...termControls].map(([key,input])=>[key,key==='precision'?Number(input.value):key==='require_beneficiary_name'?input.value==='true':input.value]));
        }
        if(parent){payload.parent_id=parent.id;payload.clause_number=clause.input.value.trim();}
        if(previous?.parent_id){payload.parent_id=previous.parent_id;payload.clause_number=previous.clause_number;}
        if (onSubmit) await onSubmit(payload);
        else {
          if (previous) payload.expected_version = previous.version;
          if(reason)payload.reason=reason.input.value.trim();
          await api.request(previous ? `${base}/records/${previous.id}/${previous.status==='open'?'replace':'versions'}` : `${base}/records`, { method: "POST", body: payload });
          document.getElementById("drawer").close(); await load();
          toast(L("تم حفظ السجل للمراجعة", "Record saved for review"));
        }
      } catch (error) { feedback.replaceChildren(errorState(error)); }
      finally { save.disabled = false; }
    });
    return form;
  }

  async function showRecord(id) {
    const body = el("div"); body.append(loading()); openDrawer(L("تفاصيل السجل", "Record details"), body);
    try {
      const [record, history] = await Promise.all([api.request(`${base}/records/${id}`), api.request(`${base}/records/${id}/history`)]);
      const values={...(record.values||{})};delete values.record_code;
      const approved = record.approval_status === "approved";
      body.replaceChildren(el("h3", record.title), badge(L(record.status === "open" ? "مفتوح" : "مغلق", record.status === "open" ? "Open" : "Closed")),
        el("p", inputApproval ? (approved ? L("معتمد مؤسسياً", "Institutionally approved") : L("بانتظار المراجعة المؤسسية", "Pending institutional review")) : L("مرجع محفوظ", "Reference stored"), "muted"),
        details({effective_from:record.effective_from,effective_to:record.effective_to,...values}));
      if(Object.keys(record.terms||{}).length)
        body.append(el('h3',L('القواعد المرتبطة','Linked terms')),details(record.terms));
      if(Object.keys(record.links||{}).length)
        body.append(el('p',L('هذا السجل مرتبط بمراجع محفوظة؛ تظهر معرّفاتها التقنية فقط عند الحاجة.','This record is linked to preserved references; technical identifiers are hidden unless needed.'),'muted'));
      if (record.close_reason) body.append(el("p", record.close_reason));
      const actions = el("div", null, "actions"); body.append(actions);
      if(record.status==='open' && schema.permissions.update)
        actions.append(button(L('تعديل','Edit'),()=>openDrawer(L('تعديل مع حفظ النسخة السابقة','Edit and preserve previous version'),recordForm(record))));
      if(inputApproval && record.status==='open' && schema.permissions.approve && !approved && !record.created_by_me) {
        const approve = button(L('اعتماد السجل','Approve record'), async () => {
          if (!(await confirm(L('اعتماد هذا السجل؟','Approve this record?')))) return;
          approve.disabled = true;
          try {
            await api.request(`${base}/records/${id}/approve`, { method: 'POST', body: { expected_version: record.version } });
            document.getElementById('drawer').close(); await load();
            toast(L('تم اعتماد السجل','Record approved'));
          } catch (error) { toast(error.message); approve.disabled = false; }
        }, 'button primary');
        actions.append(approve);
      }
      if (record.status === "open" && schema.permissions.close) {
        const close = button(L("إغلاق السجل", "Close record"), () => {
          const form = el("form"), reason = field("intake-close-reason", L("سبب الإغلاق", "Closing reason"), { maxLength: 2000 });
          const save = el("button", L("تأكيد الإغلاق", "Confirm closure"), "button primary"); save.type = "submit";
          const result = el("div"); result.setAttribute("aria-live", "polite"); form.append(reason.wrap, save, result);
          form.addEventListener("submit", async event => {
            event.preventDefault(); if (save.disabled || !form.reportValidity()) return; save.disabled = true;
            try {
              await api.request(`${base}/records/${id}/close`, { method: "POST", body: { expected_version: record.version, reason: reason.input.value.trim() } });
              document.getElementById("drawer").close(); await load(); toast(L("تم إغلاق السجل", "Record closed"));
            } catch (error) { result.replaceChildren(errorState(error)); }
            finally { save.disabled = false; }
          }); openDrawer(L("إغلاق السجل", "Close record"), form);
        });
        if (record.effective_from > day()) { close.disabled = true; close.title = L("لا يُغلق سجل قبل بدء سريانه", "Cannot close before its effective date"); }
        actions.append(close);
      }
      if (record.status === "closed" && schema.permissions.create)
        actions.append(button(L("إضافة نسخة جديدة", "Add new version"), () => openDrawer(L("نسخة جديدة", "New version"), recordForm(record))));
      body.append(el("h3", L("تاريخ النسخ", "Version history")));
      for (const version of history.items) body.append(el("p", `${version.version} · ${L(version.status === "open" ? "مفتوح" : "مغلق", version.status === "open" ? "Open" : "Closed")} · ${version.approval_status === "approved" ? L("معتمد", "Approved") : L("بانتظار الاعتماد", "Pending approval")} · ${version.effective_from} → ${version.effective_to || "—"}`));
      if (record.evidence) {
        body.append(el("h3", L("المرفقات والمصدر", "Attachments and source")), el("code", record.evidence.sha256));
        body.append(button(L("تنزيل المصدر المرتبط", "Download linked source"), async () => {
          try {
            const blob = await api.request(`${base}/records/${id}/evidence/${record.evidence.document_id}`, { download: true });
            const extension = ["csv","xlsx","pdf"].includes(record.evidence.format) ? record.evidence.format : "bin";
            const url = URL.createObjectURL(blob), anchor = document.createElement("a"); anchor.href = url; anchor.download = `source-${record.evidence.document_id}.${extension}`;
            anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
          } catch (error) { toast(error.message); }
        }));
      }
    } catch (error) { body.replaceChildren(errorState(error, () => void showRecord(id))); }
  }

  function importDialog() {
    const body = el("div"), form = el("form"), file = field("intake-file", L("ملف المصدر", "Source file"), { type: "file" });
    file.input.accept = ".xlsx,.csv,.pdf";
    const upload = el("button", L("رفع ومعاينة", "Upload and preview"), "button primary"); upload.type = "submit";
    const preview = el("div"); preview.setAttribute("aria-live", "polite");
    const importType = key => ["effective_from", "effective_to"].includes(key) ? L("تاريخ", "Date")
      : ["amount", "percent"].includes(key) ? L("رقم عشري", "Decimal")
      : key === "currency" ? L("عملة", "Currency")
      : key === "coding_system" ? L("رمز ترميز", "Code") : L("نص", "Text");
    const importExample = key => ({
      title: L("عقد تأمين 2026", "Insurance contract 2026"), effective_from: "2026-11-13", effective_to: "2027-11-12",
      record_code: "SYN-001", provider_code: "HOSP-01", contract_number: "C-2026-001", insurer_code: "INS-01",
      beneficiary_token: "123456789", category_code: "A", service_code: "CONSULT", coding_system: "LOCAL",
      currency: "LYD", amount: "1000.00", percent: "20", scope: L("عام", "General"), document_number: "DOC-2026-001",
    })[key] || L("نص تجريبي", "Sample text");
    const importNote = (key, maxLength) => {
      if (key === "amount") return L("رقم غير سالب، حتى 4 منازل عشرية", "Non-negative number, up to 4 decimals");
      if (key === "percent") return L("رقم بين 0 و100", "Number from 0 to 100");
      if (key === "currency") return L("3 أحرف إنجليزية كبيرة", "3 uppercase English letters");
      if (key === "coding_system") return "LOCAL / ICD / CPT / LOINC / SNOMED";
      if (key === "effective_from" || key === "effective_to") return L("YYYY-MM-DD أو DD/MM/YYYY", "YYYY-MM-DD or DD/MM/YYYY");
      if (key === "record_code") return L("معرّف داخلي فريد", "Unique internal identifier");
      return maxLength ? L(`حتى ${maxLength} حرفاً`, `Up to ${maxLength} characters`) : "";
    };
    const columns = [
      { key: "title", ar: L("الاسم / العنوان", "Name / title"), required: true, maxLength: 200 },
      { key: "effective_from", ar: L("تاريخ بداية السريان", "Effective from"), required: true },
      { key: "effective_to", ar: L("تاريخ نهاية السريان", "Effective until"), required: false },
      ...schema.fields.map(item => ({ key: item.key, ar: L(item.ar, item.en), required: true,
        maxLength: item.max_length || 200, example: item.example || "" })),
    ];
    const guide = el("div", null, "table-scroll"), guideTable = el("table"), guideHead = el("thead"), guideHeader = el("tr");
    for (const label of [L("الحقل", "Field"), L("اسم العمود في الملف", "File column"), L("نوع البيانات", "Data type"),
      L("مطلوب", "Required"), L("مثال", "Example"), L("ملاحظات", "Notes")]) guideHeader.append(el("th", label));
    guideHead.append(guideHeader); guideTable.append(guideHead);
    const guideBody = el("tbody");
    for (const column of columns) {
      const row = el("tr");
      row.append(el("td", column.ar),
        el("td", `${column.ar} (${column.key})`),
        el("td", importType(column.key)),
        el("td", column.required ? L("نعم", "Yes") : L("لا", "No")),
        el("td", column.example || importExample(column.key)),
        el("td", importNote(column.key, column.maxLength)));
      guideBody.append(row);
    }
    guideTable.append(guideBody); guide.append(guideTable);
    const template = columns.map(column => column.ar).join(",");
    const locatorText = locator => {
      if (!locator) return L("الملف", "File");
      if (locator.kind === "sheet") return L(`الورقة ${locator.index} · الصف ${locator.row || "—"}`, `Sheet ${locator.index} · Row ${locator.row || "—"}`);
      if (locator.kind === "row") return L(`الصف ${locator.row || locator.index}`, `Row ${locator.row || locator.index}`);
      return L("الملف", "File");
    };
    const ingestionReason = reason => ({
      "invalid worksheet header": L("ترويسة الورقة غير صالحة: يجب أن تحتوي كل خلية على اسم عمود فريد وغير فارغ.",
        "Invalid worksheet header: every cell must contain a unique, non-empty column name."),
      "empty worksheet": L("الورقة فارغة.", "The worksheet is empty."),
      "formula cell": L("خلية معادلة غير مسموحة. استبدلها بقيمة ثابتة.", "Formula cell is not allowed. Replace it with a static value."),
      "unreadable or unsafe source": L("تعذر قراءة الملف أو أنه غير آمن. جرّب ملف Excel/CSV صالحاً.",
        "The file could not be read or is unsafe. Try a valid Excel/CSV file."),
    })[reason] || reason;
    body.append(el("h3", L("أعمدة ملف Excel / CSV", "Excel / CSV columns")),
      el("p", L("استخدم أسماء الأعمدة العربية التالية في الصف الأول من كل ورقة. يمكن أيضاً استخدام المفتاح الإنجليزي المعروض بين الأقواس.",
        "Use the following Arabic headers in the first row of each sheet. The English key shown in parentheses is also accepted."), "muted"),
      guide,
      el("p", L("صف الترويسة الجاهز للنسخ:", "Ready-to-copy header row:"), "muted"),
      el("code", template),
      el("p", L("التواريخ تُقبل بصيغة YYYY-MM-DD أو DD/MM/YYYY. لا تستخدم معادلات داخل الخلايا.",
        "Dates accept YYYY-MM-DD or DD/MM/YYYY. Do not use formulas in cells."), "muted"));
    form.append(file.wrap, upload); body.append(form, preview);
    form.addEventListener("submit", async event => {
      event.preventDefault(); if (upload.disabled || !form.reportValidity()) return;
      const selected = file.input.files[0]; if (!selected) return;
      if (selected.size > 50 * 1024 * 1024) { preview.replaceChildren(el("p", L("الحد الأقصى 50 MiB", "Maximum 50 MiB"))); return; }
      upload.disabled = true; preview.replaceChildren(loading());
      try {
        const source = await api.request(`${base}/imports?filename=${encodeURIComponent(selected.name)}`, { method: "POST", raw: true, body: await selected.arrayBuffer() });
        if (source.status === "review_required") {
          let issues = [];
          try { issues = (await api.request(`/api/v1/ingestion/jobs/${source.job_id}/quarantine`)).items || []; } catch { /* keep the generic guidance below */ }
          const box = panel(L("المصدر يحتاج إلى تصحيح", "Source needs correction"),
            L("لم يُستورد أي سجل. صحح الأسباب التالية ثم ارفع نسخة جديدة.",
              "No records were imported. Fix the following reasons and upload a new copy."));
          if (issues.length) for (const issue of issues) box.append(el("p", `${locatorText(issue.locator)} — ${ingestionReason(issue.reason)}`));
          else box.append(el("p", L("تعذر استخراج الملف. تحقق من أن الصف الأول يحتوي أسماء أعمدة فريدة وغير فارغة.",
            "The file could not be extracted. Check that the first row has unique, non-empty column names.")));
          preview.replaceChildren(box); return;
        }
        const result = await api.request(`${base}/imports/${source.document_id}/preview`); preview.replaceChildren(el("p", L("تم حفظ المصدر الأصلي وبصمته. راجع الصفوف قبل الحفظ.","Original source and fingerprint preserved. Review rows before saving."), "muted"));
        const invalidCount = result.items.filter(item => !item.valid).length;
        if (invalidCount) preview.append(el("p", L(`عدد الصفوف التي تحتاج تصحيحاً: ${invalidCount}. راجع سبب كل صف ثم ارفع نسخة جديدة.`,
          `${invalidCount} row(s) need correction. Review each row reason and upload a new copy.`), "muted"));
        if (result.items.some(item => item.manual_required)) {
          preview.append(el("p", L("PDF دليل مصدر؛ أدخل الحقول بعد مراجعة المستند.", "PDF is source evidence; enter structured fields after reviewing it.")));
          for (const item of result.items) preview.append(el("pre", item.text_excerpt));
          preview.append(recordForm(null, source.document_id, async payload => {
            await api.request(`${base}/imports/${source.document_id}/commit`, { method: "POST", body: { manual_record: payload } });
            document.getElementById("drawer").close(); await load(); toast(L("تم حفظ المدخل المرتبط بالمصدر", "Source-linked intake saved"));
          })); return;
        }
        const selectedRows = new Set();
        for (const item of result.items) {
          const row = el("div", null, "import-row");
          if (item.valid) {
            const checkbox = el("input"); checkbox.type = "checkbox"; checkbox.checked = true; checkbox.id = `import-row-${item.id}`;
            const label = el("label", item.record.title); label.htmlFor = checkbox.id;
            selectedRows.add(item.id); checkbox.addEventListener("change", () => checkbox.checked ? selectedRows.add(item.id) : selectedRows.delete(item.id));
            row.append(checkbox, label, details(item.record));
          } else {
            const errorBox = el("div", null, "error");
            errorBox.append(el("strong", L("سبب رفض الصف", "Row rejection reason")), el("p", item.error));
            row.append(badge(L("سجل غير صالح", "Invalid record"), "warning"), el("p", locatorText(item.locator), "muted"), errorBox);
          }
          preview.append(row);
        }
        const save = button(L("تأكيد وحفظ المحدد", "Confirm and save selected"), async () => {
          if (!selectedRows.size) { toast(L("اختر سجلاً صالحاً", "Select a valid row")); return; }
          if (!(await confirm(L("حفظ المدخلات المحددة للمراجعة؟", "Save selected intake for review?")))) return;
          save.disabled = true;
          try {
            await api.request(`${base}/imports/${source.document_id}/commit`, { method: "POST", body: { record_ids: [...selectedRows] } });
            document.getElementById("drawer").close(); await load(); toast(L("تم حفظ المدخلات", "Intake saved"));
          } catch (error) { preview.append(errorState(error)); }
          finally { save.disabled = false; }
        }, "button primary");
        preview.append(save);
      } catch (error) { preview.replaceChildren(errorState(error)); }
      finally { upload.disabled = false; }
    });
    openDrawer(L("استيراد ملف", "Import file"), body);
  }

  async function load() {
    const version = ++generation; content.replaceChildren(loading());
    try {
      schema = await api.request(`${base}/schema`);
      if(feature==='providers.prices'&&!providerOptionsLoaded){
        const options=await api.request(`${base}/provider-options`);
        for(const item of options.items){const option=el('option',item.name);option.value=item.code;provider.input.append(option);}providerOptionsLoaded=true;
      }
      const data = await api.request(`${base}/records?offset=${offset}&limit=25&q=${encodeURIComponent(search.input.value)}${status.input.value ? `&status=${status.input.value}` : ""}${feature==='providers.prices'&&provider.input.value?`&provider_code=${encodeURIComponent(provider.input.value)}`:''}`);
      if (version !== generation || !content.isConnected) return;
      const filters = el("form", null, "filters"), apply = el("button", L("تطبيق", "Apply"), "button"); apply.type = "submit";
      if(feature==='providers.prices')filters.append(provider.wrap);
      filters.append(search.wrap, status.wrap, apply);
      filters.addEventListener("submit", event => { event.preventDefault(); offset = 0; void load(); });
      const actions = el("div", null, "actions");
      const nouns={ 'inputs.contracts':['عقد','contract'],'inputs.regulations':['لائحة','regulation'],
        'inputs.exceptions':['استثناء','exception'],'inputs.caps':['سقف','ceiling'],'inputs.coverage':['تغطية','coverage'],'inputs.approvals':['موافقة','approval'] };
      const noun=nouns[feature];
      const importFile=button(L('استيراد Excel / CSV','Import Excel / CSV'),importDialog);
      importFile.disabled=!schema.permissions.create||!schema.permissions.upload;
      importFile.title=importFile.disabled?L('يتطلب صلاحية الإنشاء والاستيراد لحساب الإدخال.','Requires create and import permissions for a data-entry account.'):'';
      actions.append(importFile);
      const add=button(noun?L(`إضافة ${noun[0]} جديد`,`Add new ${noun[1]}`):L('إضافة','Add'),()=>openDrawer(L('سجل جديد','New record'),recordForm(null)),'button primary');
      add.disabled=!schema.permissions.create;actions.append(add);
      const edit=button(clauseSection?L('تعديل بند','Edit clause'):L('تعديل','Edit'),()=>{if(selected)openDrawer(L('تعديل مع حفظ التاريخ','Edit with history preserved'),recordForm(selected));});
      edit.disabled=!selected||!schema.permissions.update;actions.append(edit);
      const addClause=clauseSection?button(L('إضافة بند','Add clause'),()=>{if(selected)openDrawer(L('إضافة بند','Add clause'),recordForm(null,null,null,selected));}):null;
      if(addClause){addClause.disabled=!selected||!schema.permissions.create;actions.append(addClause);}
      const canApproveSelected = () => Boolean(inputApproval && selected && selected.status === 'open' &&
        schema.permissions.approve && selected.approval_status !== 'approved' && !selected.created_by_me);
      const approve = inputApproval ? button(L('اعتماد','Approve'), async () => {
        if (!canApproveSelected() || !(await confirm(L('اعتماد هذا السجل؟','Approve this record?')))) return;
        approve.disabled = true;
        try {
          await api.request(`${base}/records/${selected.id}/approve`, { method: 'POST', body: { expected_version: selected.version } });
          await load(); toast(L('تم اعتماد السجل','Record approved'));
        } catch (error) { toast(error.message); approve.disabled = !canApproveSelected(); }
      }, 'button primary') : null;
      if (approve) { approve.disabled = !canApproveSelected(); actions.append(approve); }
      const updateSelectionActions = () => {
        edit.disabled = !selected || !schema.permissions.update;
        if (addClause) addClause.disabled = !selected || !schema.permissions.create || selected.status !== 'open';
        if (approve) approve.disabled = !canApproveSelected();
      };
      const list = el("div", null, "table-scroll"),table=el('table'),thead=el('thead'),header=el('tr');
      for(const label of [L('تحديد','Select'),L('الاسم / البند','Name / clause'),L('القيم المرجعية','Reference values'),L('الحالة','Status'),L('فترة السريان','Validity'),L('الإصدار','Version'),L('المصدر والاكتمال','Source and completeness'),inputApproval?L('الاعتماد','Approval'):L('الحالة المرجعية','Reference status')])header.append(el('th',label));
      thead.append(header);table.append(thead);const tbody=el('tbody');
      for (const record of data.items) {
        const row=el('tr'),choice=el('td'),radio=el('input');radio.type='radio';radio.name='selected-intake';radio.checked=selected?.id===record.id;radio.setAttribute('aria-label',record.title);
        radio.addEventListener('change',()=>{selected=record;updateSelectionActions();});choice.append(radio);
        const title=el('td');title.append(button(`${record.clause_number?record.clause_number+' · ':''}${record.title}`,()=>void showRecord(record.id),'link-button'));
        const values=el('td');for(const item of schema.fields.filter(i=>i.key!=='record_code'))values.append(el('div',`${L(item.ar,item.en)}: ${record.values[item.key]}`));
        if(record.terms?.network)values.append(el('div',L(record.terms.network==='in_network'?'داخل الشبكة':'خارج الشبكة',record.terms.network==='in_network'?'In network':'Out of network')));
        if(record.terms?.service_type)values.append(el('div',record.terms.service_type));
        row.append(choice,title,values,el('td',L(record.status==='open'?'مفتوح':'مغلق',record.status==='open'?'Open':'Closed')),
          el('td',`${record.effective_from} → ${record.effective_to||'—'}`),el('td',record.version),
          el('td',L(record.evidence?'مستند مرتبط؛ حقول مكتملة':'إدخال يدوي؛ حقول مكتملة',record.evidence?'Linked document; fields complete':'Manual entry; fields complete')),
          el('td',inputApproval?L(record.approval_status==='approved'?'معتمد':'بانتظار الاعتماد',record.approval_status==='approved'?'Approved':'Pending approval'):L('مرجع محفوظ','Reference stored')));tbody.append(row);
      }
      table.append(tbody);list.append(table);
      const pagination = el("div", null, "actions");
      if (offset) pagination.append(button(L("السابق", "Previous"), () => { offset = Math.max(0, offset - 25); void load(); }));
      if (data.has_more) pagination.append(button(L("التالي", "Next"), () => { offset += 25; void load(); }));
      const showPrices=feature!=='providers.prices'||Boolean(provider.input.value);
      content.replaceChildren(filters,actions,showPrices?(data.items.length?list:empty(L('لا سجلات','No records'),L('أضف سجلًا جديدًا.','Add a new record.'),route.icon)):
        empty(L('اختر مقدم الخدمة أولًا','Select a provider first'),L('تظهر تسعيرات المقدم المختار فقط.','Only the selected provider’s price list is shown.'),route.icon),pagination);
    } catch (error) { content.replaceChildren(errorState(error, () => void load())); }
  }
  void load();
}
