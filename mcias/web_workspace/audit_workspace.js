import { L, el, field, button, panel, badge, loading, empty, errorState, openDrawer, toast } from './ui.js';
import { referenceChecks } from './reference_checks.js';

const saveBlob=(blob,name)=>{const url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};

export function auditWorkspace(ctx) {
  const {root,api,context}=ctx, can=code=>context.permissions.includes(code);
  root.append(panel(L('التدقيق والمراجعة','Audit and review'),L('المطالبات والفواتير هنا فقط. يُحفظ الملف الأصلي كما ورد، وتُنشأ نتائج مستقلة لكل بند دون تكرار.','Claims and invoices belong here. Original files remain unchanged; each service line has one output.')));
  const upload=el('form',null,'form-panel'),file=field('claims-file',L('ملف المطالبات الأصلي','Original claims file'),{type:'file'});
  file.input.accept='.xlsx,.csv';const submit=el('button',L('رفع ومعاينة','Upload and preview'),'button primary');submit.type='submit';submit.disabled=!can('ingestion.upload')||!can('claims.import');
  const uploadResult=el('div');uploadResult.setAttribute('aria-live','polite');upload.append(file.wrap,submit,uploadResult);root.append(upload);
  const selected=field('audit-batch',L('دفعة المطالبات','Claim batch'),{required:false,options:[['',L('اختر دفعة','Select a batch')]]});
  const content=el('div');root.append(selected.wrap,content);
  selected.input.addEventListener('change',()=>void showBatch());
  upload.addEventListener('submit',async event=>{event.preventDefault();if(submit.disabled||!upload.reportValidity())return;const source=file.input.files[0];if(!source)return;
    if(source.size>20*1024*1024){uploadResult.replaceChildren(el('p',L('حجم الملف يتجاوز 20 MiB','File exceeds 20 MiB')));return;}
    submit.disabled=true;uploadResult.replaceChildren(loading());try{
      const document=await api.request('/api/v1/ingestion/uploads?filename='+encodeURIComponent(source.name),{method:'POST',raw:true,body:await source.arrayBuffer()});
      if(document.status!=='extracted'){uploadResult.replaceChildren(empty(L('المصدر محفوظ ويحتاج مراجعة','Source preserved; review required'),L('لم يكتمل الاستخراج. لا تبدأ الدفعة قبل معالجة أسباب الحجر.','Extraction is incomplete. Resolve quarantine before staging.')));return;}
      const batch=await api.request('/api/v1/claims/batches/from-document/'+document.document_id,{method:'POST'});
      uploadResult.replaceChildren(el('p',L('حُفظ الأصل ببصمته: ','Original saved with hash: ')+document.sha256));
      await batches(batch.id);await mapColumns(batch.id);
    }catch(error){uploadResult.replaceChildren(errorState(error));}finally{submit.disabled=!can('ingestion.upload')||!can('claims.import');}});

  async function batches(id=''){
    const previous=id||selected.input.value;const page=await api.request('/api/v1/workspace/catalog/batches?limit=100');
    selected.input.replaceChildren();const placeholder=el('option',L('اختر دفعة','Select a batch'));placeholder.value='';selected.input.append(placeholder);
    for(const item of page.items){const option=el('option',`${item.id.slice(0,8)} · ${item.status} · ${item.imported_count}/${item.record_count}`);option.value=item.id;selected.input.append(option);}
    selected.input.value=previous;await showBatch();
  }

  async function mapColumns(id){
    const columns={claim_reference:'رقم الفاتورة',beneficiary_token:'الرقم التأميني',beneficiary_name:'اسم المستفيد',
      category_code:'الدرجة',provider_code:'مقدم الخدمة',service_code:'الخدمة',service_date:'تاريخ الخدمة',
      unit_price:'السعر',gross_amount:'المطلوب',copay_percent:'نسبة التحمل',copay_amount:'إجمالي التحمل',claimed_amount:'الصافي'};
    try{
      await api.request(`/api/v1/claims/batches/${id}/mapping`,{method:'PUT',body:{columns,currency:'LYD',date_format:'DMY'}});
      toast(L('تم التعرف على قالب المطالبات المعتمد دون ربط يدوي للأعمدة.','Approved claim template detected; no manual column mapping was needed.'));
      await showBatch();
    }catch(error){toast(error.message);}
  }

  function attachInvoice(batch,entries){
    const form=el('form'),choice=field('attachment-line',L('بند المطالبة المرتبط بالفاتورة','Claim line linked to invoice'),{options:entries.map(row=>[row.id,`${row.claim_reference} · ${row.id.slice(0,8)}`])}),source=field('invoice-file',L('الفاتورة / المرفق','Invoice / attachment'),{type:'file'});source.input.accept='.pdf,.png,.jpg,.jpeg';
    const feedback=el('div'),save=el('button',L('رفع وربط المرفق','Upload and link attachment'),'button primary');save.type='submit';form.append(choice.wrap,source.wrap,save,feedback);
    const kind=field('attachment-kind',L('نوع المستند','Document type'),{options:[['invoice',L('فاتورة الخدمة','Service invoice')],['supporting',L('مستند داعم آخر','Other supporting document')]]});form.prepend(kind.wrap);
    form.addEventListener('submit',async event=>{event.preventDefault();if(save.disabled||!form.reportValidity())return;const file=source.input.files[0];if(!file)return;
      if(file.size>20*1024*1024){feedback.replaceChildren(el('p',L('حجم الملف يتجاوز 20 MiB','File exceeds 20 MiB')));return;}
      save.disabled=true;try{const doc=await api.request('/api/v1/ingestion/uploads?filename='+encodeURIComponent(file.name),{method:'POST',raw:true,body:await file.arrayBuffer()});
        await api.request(`/api/v1/claims/entries/${choice.input.value}/attachments/${doc.document_id}?kind=${kind.input.value}`,{method:'POST'});
        if(can('claims.review'))await api.request(`/api/v1/claims/batches/${batch.id}/reference-checks`,{method:'POST'});
        document.getElementById('drawer').close();toast(L('حُفظ المرفق ورُبط؛ نتيجة الاستخراج لا تعني اعتماده ماليًا','Attachment preserved and linked; extraction does not mean financial approval'));await showBatch();
      }catch(error){feedback.replaceChildren(errorState(error));}finally{save.disabled=false;}});
    openDrawer(L('ربط الفاتورة ببند المطالبة','Link invoice to a claim line'),form);
  }

  async function showBatch(){
    const id=selected.input.value;if(!id){content.replaceChildren(empty(L('اختر دفعة أو ارفع ملفًا','Select a batch or upload a file'),L('لا تُنشأ نتائج مالية افتراضية.','No financial results are fabricated.')));return;}
    content.replaceChildren(loading());try{
      const batch=await api.request(`/api/v1/claims/batches/${id}`),page=await api.request(`/api/v1/claims/batches/${id}/entries?limit=100`);
      const body=el('div');body.append(badge(batch.status),el('p',`${L('السجلات المستوردة','Imported records')}: ${batch.imported_count}/${batch.record_count}`),el('code',batch.source_sha256));
      if(batch.status==='invalid_source')body.append(panel(L('الملف يحتاج إلى تصحيح','Workbook needs correction'),L('لم يُستورد أي بند. صحح الصفوف التي ظهرت في رسالة الفحص ثم ارفع نسخة جديدة من ملف Excel.','No claim lines were imported. Correct the rows listed by validation and upload a new Excel file.')));
      const actions=el('div',null,'actions');
      if(can('claims.import')&&batch.imported_count===0&&batch.status==='staged')actions.append(button(L('استخدام قالب المطالبات المعتمد','Use approved claims template'),()=>void mapColumns(id)));
      if(can('claims.import')&&['staged','importing','review_required'].includes(batch.status))actions.append(button(L('فحص الملف واستيراده بالكامل','Validate and import the complete workbook'),async()=>{
        const target=el('div');target.append(loading());openDrawer(L('معاينة الاستيراد','Import preview'),target);
        try{const preview=await api.request(`/api/v1/claims/batches/${id}/preview?limit=100`);target.replaceChildren(el('p',`${L('السجلات','Records')}: ${preview.record_count}`));
          for(const row of preview.rows)target.append(el('p',`${row.record_id.slice(0,8)} · ${row.valid?L('صالح','Valid'):row.reason}`));
          const resume=button(L('فحص الملف ثم الاستيراد','Validate then import'),async()=>{resume.disabled=true;try{let result;do{result=await api.request(`/api/v1/claims/batches/${id}/resume`,{method:'POST'});target.append(el('p',`${result.imported_count}/${result.record_count}`));}while(result.status==='importing');await batches(id);
            if(result.status==='invalid_source'){
              target.replaceChildren(el('h3',L('لم يُستورد الملف','Workbook was not imported')),
                el('p',L('صحح الصفوف المحددة في ملفك الأصلي ثم ارفع الملف من جديد. لم يُستورد أي بند.','Correct the listed rows in the original workbook and upload it again. No claim lines were imported.')));
              for(const item of result.rejected||[])target.append(el('p',`${item.locator?.row||item.record_id}: ${item.reason}`));
            }
            else document.getElementById('drawer').close();}catch(error){target.append(errorState(error));}finally{resume.disabled=false;}},'button primary');target.append(resume);
        }catch(error){target.replaceChildren(errorState(error));}}));
      if(can('ingestion.upload')&&can('claims.import')&&page.entries.length)actions.append(button(L('إضافة فاتورة أو مرفق','Add invoice or attachment'),()=>attachInvoice(batch,page.entries)));
      if(can('reports.create')&&batch.status==='imported')actions.append(button(L('تجهيز ملفي النتائج','Prepare the two result files'),async()=>{
        try{await api.request('/api/v1/result-files/batches/'+id,{method:'POST'});ctx.navigate(ctx.scope==='admin'?'/admin/result-reports':'/institution/result-reports');}
        catch(error){toast(error.message);}}));
      if(can('claims.review')&&batch.status==='imported'){
        const run=button(L('بدء التدقيق','Start audit'),async()=>{run.disabled=true;try{
          const result=await api.request(`/api/v1/audits/batches/${id}/run`,{method:'POST'});
          const actionCount=(result.counts.rejected||0)+(result.counts.held||0)+(result.counts.review||0);
          const out=panel(L('نتيجة دورة التدقيق','Audit cycle result'),`${L('مطابق مبدئيًا','Preliminary matches')}: ${result.counts.accepted||0} · ${L('يحتاج مراجعة أو رفضًا','Needs review or rejection')}: ${actionCount}`);
          body.append(out);toast(L('حُفظت النتائج؛ غياب الفاتورة أو السياسة المعتمدة يبقي البند معلقًا.','Results recorded; missing invoices or approved policy keep a line held.'));
        }catch(error){toast(error.message);}finally{run.disabled=false;}},'button primary');actions.append(run);
      }
      body.append(panel(L('حالة التدقيق','Audit status'),L('كل بند يحتاج فاتورة مرتبطة وسياسة مالية موقعة تخص مراجع الخدمة وفئتها. النتيجة مبدئية حتى الاعتماد البشري؛ نقص الفاتورة يعلّق البند بسبب نقص المرفقات.','Every line requires an explicitly linked invoice and a signed policy bound to its service references and category. Outcomes remain preliminary until human approval; missing invoices hold the line.')));
      const wrap=el('div',null,'table-scroll'),table=el('table'),head=el('tr');for(const label of [L('معرف البند','Line ID'),L('رقم الفاتورة','Invoice'),L('تاريخ الخدمة','Service date'),L('الصافي الأصلي','Original net'),L('العملة','Currency')])head.append(el('th',label));const thead=el('thead');thead.append(head);table.append(thead);const tbody=el('tbody');
      for(const row of page.entries){const tr=el('tr');for(const value of [row.id,row.claim_reference,row.service_date,row.claimed_amount,row.currency])tr.append(el('td',value));tbody.append(tr);}table.append(tbody);wrap.append(table);
      body.append(wrap);if(batch.imported_count>100)body.append(el('p',L('تُعرض أول 100 نتيجة فقط؛ ملفات النتائج تشمل كامل الدفعة ضمن حدود التصدير.','Showing the first 100 entries; output files cover the complete batch within export limits.')));body.append(actions);content.replaceChildren(body);
      if(batch.status==='imported')await referenceChecks(ctx,id,body,showBatch);
    }catch(error){content.replaceChildren(errorState(error,()=>void showBatch()));}
  }
  batches().catch(error=>content.replaceChildren(errorState(error)));
}

export function resultReports(ctx){
  const {root,api,context}=ctx;root.append(panel(L('ملفا النتائج','Result files'),L('الأخضر للمطابق مبدئيًا، والأحمر لكل بند يحتاج تدقيقًا بشريًا أو رفضًا. تظهر أسباب الحالة داخل الملف، وكل بند يرد مرة واحدة. النتائج ليست اعتمادًا ماليًا نهائيًا.','Green contains preliminary matches; red contains lines requiring human review or rejection with reasons. Each line appears once. These are not final financial approvals.')));
  const content=el('div');root.append(content);let offset=0;
  const names={accepted:['مطابق مبدئيًا','Preliminary match'],rejected:['مرفوض مبدئيًا','Preliminary rejection'],held:['ناقص أو متعذر التحقق','Incomplete or unverifiable'],review:['يحتاج مراجعة بشرية','Needs human review']};
  const reasonLabels={not_audited:'لم يُدقّق بعد',missing_invoice:'فاتورة الخدمة غير مرفقة',
    missing_or_ambiguous_signed_policy:'لا توجد سياسة معتمدة وحيدة لهذه المطالبة',
    unresolved_reference_or_evidence:'مرجع أو دليل لازم غير مكتمل',policy_or_evidence_blocked:'تعذر التحقق من السياسة أو الدليل',
    unresolved_review_finding:'توجد ملاحظة تتطلب مراجعة بشرية',verification_failed:'فشل التحقق المستقل',
    net_basis_confirmation_required:'يلزم تأكيد أن المبلغ صافي بعد التحمل',
    possible_second_copay_deduction:'احتمال خصم التحمل مرتين',proven_financial_difference:'ثبت وجود فرق مالي',
    verified_match:'اجتاز الفحص المبدئي',invoice_evidence_changed_reaudit_required:'تغيرت الفاتورة بعد التدقيق؛ يلزم إعادة التدقيق'};
  async function show(result){const body=el('div');body.append(loading());openDrawer(L('بنود النتائج','Result lines'),body);try{const page=await api.request(`/api/v1/result-files/${result.id}/lines?limit=100`);body.replaceChildren();
    for(const row of page.items){const reasons=(row.reasons||[]).map(reason=>{const code=typeof reason==='string'?reason:(reason.code||reason.message);const message=typeof reason==='string'?reason:(reason.message||code);return reasonLabels[message]||reasonLabels[code]||message;}).filter(Boolean).join('؛ ');const item=panel(`${row.invoice_number} · ${L(...names[row.bucket])}`,`${row.service_code} · ${row.claimed_net} ${row.currency} · ${reasons||reasonLabels[row.reason]||row.reason}`);item.append(el('p',L(row.audited?'له لقطة تدقيق':'لم يُدقّق بعد',row.audited?'Audit snapshot exists':'Not audited yet')));
      if(row.audit_snapshot_id)item.append(button(L('الأدلة والحسابات','Evidence and calculations'),async()=>{try{const snapshot=await api.request('/api/v1/audits/'+row.audit_snapshot_id);const pre=el('pre',JSON.stringify(snapshot,null,2));openDrawer(L('لقطة التدقيق','Audit snapshot'),pre);}catch(error){toast(error.message);}}));body.append(item);}
    if(page.has_more)body.append(el('p',L('تُعرض أول 100 نتيجة؛ التنزيل يشمل كامل المجموعة.','Showing the first 100 lines; download covers the complete set.')));
  }catch(error){body.replaceChildren(errorState(error));}}
  async function load(){content.replaceChildren(loading());try{const page=await api.request(`/api/v1/result-files?offset=${offset}&limit=25`);const list=el('div');
    for(const result of page.items){const counts=result.summary.counts,actionCount=(counts.rejected||0)+(counts.held||0)+(counts.review||0);const card=panel(`${L('نتائج مبدئية','Preliminary results')} · ${L('عدد البنود: ','Lines: ')}${result.summary.total_lines}`,'');
      card.append(el('p',`${L('المقبول مبدئيًا — الملف الأخضر','Preliminary matches — green file')}: ${counts.accepted||0}`));
      card.append(el('p',`${L('يحتاج مراجعة أو رفضًا — الملف الأحمر','Needs review or rejection — red file')}: ${actionCount}`));
      card.append(el('p',`${L('التفصيل: مرفوض','Details: rejected')}: ${counts.rejected||0} · ${L('ناقص','held')}: ${counts.held||0} · ${L('مراجعة بشرية','human review')}: ${counts.review||0}`));
      const actions=el('div',null,'actions');if(context.permissions.includes('claims.read'))actions.append(button(L('عرض البنود والأدلة','View lines and evidence'),()=>void show(result)));
      const download=button(L('تنزيل ملفي النتائج','Download both result files'),async()=>{try{saveBlob(await api.request(`/api/v1/result-files/${result.id}/download`,{download:true}),`mcias-two-results-${result.id}.zip`);}catch(error){toast(error.message);}});download.disabled=!context.permissions.includes('reports.export');actions.append(download);card.append(actions);list.append(card);}
    const pagination=el('div',null,'actions');if(offset)pagination.append(button(L('السابق','Previous'),()=>{offset-=25;void load();}));if(page.has_more)pagination.append(button(L('التالي','Next'),()=>{offset+=25;void load();}));
    content.replaceChildren(page.items.length?list:empty(L('لا نتائج محفوظة','No frozen results'),L('جهّز النتائج من دفعة مكتملة الاستيراد في قسم التدقيق.','Prepare results from a fully imported batch in Audit.')),pagination);
  }catch(error){content.replaceChildren(errorState(error,()=>void load()));}}void load();
}
