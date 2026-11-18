import { L, el, field, button, panel, loading, empty, errorState, openDrawer, toast } from './ui.js';

const saveBlob=(blob,name)=>{const url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
const today=()=>{const d=new Date();return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,10);};
const categoryText=code=>{const raw=String(code||'');if(['B','أساسي','BASIC'].includes(raw))return L('الفئة الأساسية','Basic category');if(['C','تكميلي','SUPPLEMENTARY'].includes(raw))return L('الفئة التكميلية','Supplementary category');return raw||'—';};
const rowLabel=(row,index)=>row.locator?.row?L('صف ','Row ')+row.locator.row:L('سجل ','Record ')+(index+1);
const cleanBatchName=name=>String(name||'').replace(/\.[^.]+$/,'').replace(/\s*\(\d+\)\s*$/,'').replace(/[_]+/g,' ').replace(/\s+/g,' ').trim();

export function auditWorkspace(ctx) {
  const {root,api,context}=ctx, can=code=>context.permissions.includes(code);
  const statusText=status=>status==='imported'?L('مكتمل الاستيراد','Import complete'):status==='staged'?L('جاهز للاستيراد','Ready to import'):status==='importing'?L('جارٍ الاستيراد','Importing'):status==='review_required'?L('يتطلب مراجعة','Review required'):status==='invalid_source'?L('ملف غير صالح','Invalid source'):status||L('غير معروفة','Unknown');
  const reasonText=reason=>{
    const raw=String(reason||'');
    if(raw==='service date must use DD/MM/YYYY'||raw==='service date must use DD/MM/YYYY or YYYY-MM-DD')return L('تاريخ الخدمة يجب أن يكون بصيغة DD/MM/YYYY أو YYYY-MM-DD','Service date must use DD/MM/YYYY or YYYY-MM-DD');
    if(raw.startsWith('missing required cells: '))return L('خلايا مطلوبة ناقصة: ','Missing required cells: ')+raw.slice('missing required cells: '.length);
    if(raw.startsWith('invalid '))return L('قيمة غير صالحة في: ','Invalid value in: ')+raw.slice('invalid '.length);
    if(raw==='copay percentage must be between 0 and 100')return L('نسبة التحمل يجب أن تكون بين 0 و100','Copay percentage must be between 0 and 100');
    if(raw==='source number out of range or precision')return L('رقم المصدر خارج النطاق أو بدقة غير مقبولة','Source number out of range or precision');
    if(raw==='ambiguous numeric grouping')return L('تنسيق رقمي غامض (استخدم فاصلة آلاف واضحة أو بدونها)','Ambiguous numeric grouping');
    if(raw==='record is not a mapping')return L('سجل المصدر غير صالح','Source record is not a mapping');
    return raw||L('سبب غير محدد','Unknown reason');
  };
  root.append(panel(L('التدقيق والمراجعة','Audit and review'),L('المطالبات والفواتير هنا فقط. يُحفظ الملف الأصلي كما ورد، وتُنشأ نتائج مستقلة لكل بند دون تكرار.','Claims and invoices belong here. Original files remain unchanged; each service line has one output.')));
  const upload=el('form',null,'form-panel'),file=field('claims-file',L('ملف المطالبات الأصلي','Original claims file'),{type:'file'});
  const batchName=field('batch-name',L('اسم الملف / الدفعة','File / batch name'),{maxLength:200,placeholder:L('مثال: مطالبات أكتوبر','e.g. October claims')});
  const entryDate=field('batch-date',L('تاريخ الإدخال','Entry date'),{type:'date',value:today(),max:today()});
  file.input.accept='.xlsx,.csv';file.input.addEventListener('change',()=>{const chosen=file.input.files[0];if(chosen&&!batchName.input.value.trim())batchName.input.value=cleanBatchName(chosen.name);});
  const submit=el('button',L('رفع ومعاينة','Upload and preview'),'button primary');submit.type='submit';submit.disabled=!can('ingestion.upload')||!can('claims.import');
  const uploadResult=el('div');uploadResult.setAttribute('aria-live','polite');
  const help=el('details',null,'form-help');help.append(el('summary',L('الأعمدة المطلوبة في ملف Excel','Required Excel columns')),
    el('p',L('يجب أن تكون الترويسات الـ12 مطابقة تماماً. الأعمدة السبعة الأولى إلزامية، ويمكن ترك مبالغ السعر/المطلوب/التحمل/الصافي فارغة إذا أمكن حسابها من المراجع.','All 12 headers must match exactly. The first seven columns are mandatory; price, gross, copay and net cells may be left blank when they can be derived from references.')),
    el('p',L('الترتيب: رقم الفاتورة، الرقم التأميني، اسم المستفيد، الدرجة، مقدم الخدمة، الخدمة، تاريخ الخدمة (DD/MM/YYYY)، السعر، المطلوب، نسبة التحمل، إجمالي التحمل، الصافي.','Order: invoice number, insurance number, beneficiary name, category, provider, service, service date (DD/MM/YYYY), price, gross, copay percent, copay amount, net.')),
    el('p',L('يُستورد كل الصفوف في الملف؛ الحد الأقصى لحجم الملف 50 MiB.','All rows in the file are imported; maximum file size is 50 MiB.')));
  upload.append(file.wrap,batchName.wrap,entryDate.wrap,help,submit,uploadResult);root.append(upload);
  const selected=field('audit-batch',L('دفعة المطالبات','Claim batch'),{required:false,options:[['',L('اختر دفعة','Select a batch')]]});
  const content=el('div');root.append(selected.wrap,content);
  selected.input.addEventListener('change',()=>void showBatch());
  upload.addEventListener('submit',async event=>{event.preventDefault();if(submit.disabled||!upload.reportValidity())return;const source=file.input.files[0];if(!source)return;
    const displayName=batchName.input.value.trim(),entryDateValue=entryDate.input.value;
    if(source.size>50*1024*1024){const message=L('حجم الملف يتجاوز 50 MiB','File exceeds 50 MiB');uploadResult.replaceChildren(el('p',message));toast(message);return;}
    submit.disabled=true;uploadResult.replaceChildren(loading());try{
      const document=await api.request('/api/v1/ingestion/uploads?filename='+encodeURIComponent(source.name),{method:'POST',raw:true,body:await source.arrayBuffer()});
      let documentStatus=document.status;
      if(documentStatus==='duplicate'){
        const existing=await api.request('/api/v1/ingestion/documents/'+document.document_id);
        documentStatus=existing.status;
        if(documentStatus==='extracted')toast(L('الملف مطابق لمستند سبق رفعه؛ سنستخدم المستند الأصلي.','File matches an existing document; using the original.'));
      }
      if(documentStatus!=='extracted'){
        const message=L('لم يكتمل استخراج الملف. الحالة: ','Extraction did not complete. Status: ')+statusText(documentStatus);
        uploadResult.replaceChildren(panel(L('تعذر تجهيز الدفعة','Could not stage the batch'),message));
        toast(message);return;
      }
      const batch=await api.request('/api/v1/claims/batches/from-document/'+document.document_id,{method:'POST',body:{display_name:displayName,entry_date:entryDateValue}});
      uploadResult.replaceChildren(
        el('p',`${L('الدفعة','Batch')}: ${batch.display_name} · ${L('تاريخ الإدخال','Entry date')}: ${batch.entry_date} · ${L('الحالة','Status')}: ${statusText(batch.status)} · ${L('السجلات','Records')}: ${batch.record_count}`));
      toast(L('تم تجهيز الدفعة بنجاح','Batch staged successfully'));
      await batches(batch.id);
      if(batch.status==='staged')await mapColumns(batch.id);
    }catch(error){uploadResult.replaceChildren(errorState(error));toast(error.message);}finally{submit.disabled=!can('ingestion.upload')||!can('claims.import');}});

  async function batches(id=''){
    const previous=id||selected.input.value;const page=await api.request('/api/v1/workspace/catalog/batches?limit=100');
    selected.input.replaceChildren();const placeholder=el('option',L('اختر دفعة','Select a batch'));placeholder.value='';selected.input.append(placeholder);
    for(const item of page.items){const option=el('option',`${item.display_name||L('دفعة بلا اسم','Unnamed batch')} · ${item.entry_date||''} · ${statusText(item.status)} · ${item.imported_count}/${item.record_count}`);option.value=item.id;selected.input.append(option);}
    selected.input.value=previous;await showBatch();
  }

  async function completeImport(id){
    const target=el('div');target.append(loading());openDrawer(L('إكمال استيراد الدفعة','Complete batch import'),target);
    try{
      const preview=await api.request(`/api/v1/claims/batches/${id}/preview?limit=100`);
      target.replaceChildren(el('p',`${L('السجلات','Records')}: ${preview.record_count}`));
      const invalid=preview.rows.filter(row=>!row.valid);
      if(invalid.length){
        target.append(el('p',L('توجد صفوف غير صالحة؛ لن يُستورد الملف بالكامل.','Invalid rows found; the workbook will not be imported.')));
        for(const [index,row] of invalid.slice(0,20).entries())target.append(el('p',`${rowLabel(row,index)}: ${reasonText(row.reason)}`));
      }
      const resume=button(L('بدء الاستيراد الكامل','Run complete import'),async()=>{
        resume.disabled=true;
        try{
          let result;
          do{
            result=await api.request(`/api/v1/claims/batches/${id}/resume`,{method:'POST'});
            target.append(el('p',`${L('المستورد','Imported')}: ${result.imported_count}/${result.record_count} · ${statusText(result.status)}`));
          }while(result.status==='importing');
          await batches(id);
          if(result.status==='invalid_source'){
            target.replaceChildren(el('h3',L('لم يُستورد الملف','Workbook was not imported')),
              el('p',L('صحح الصفوف المحددة في ملفك الأصلي ثم ارفع الملف من جديد. لم يُستورد أي بند.','Correct the listed rows in the original workbook and upload it again. No claim lines were imported.')));
            for(const [index,item] of (result.rejected||[]).entries())target.append(el('p',`${rowLabel(item,index)}: ${reasonText(item.reason)}`));
            toast(L('فشل الاستيراد: الملف يحتوي صفوفاً غير صالحة','Import failed: workbook contains invalid rows'));
            return;
          }
          if(result.status==='imported'){
            toast(L('اكتمل استيراد الدفعة','Batch import completed'));
            document.getElementById('drawer').close();await showBatch();
          }else{
            target.append(el('p',L('الدفعة لم تكتمل بعد. أعد المحاولة أو راجع الحالة.','Batch is not complete yet. Retry or check the status.')));
            toast(L('لم تكتمل الدفعة بعد','Batch is not complete yet'));
          }
        }catch(error){
          target.append(errorState(error));toast(error.message);
          try{const current=await api.request(`/api/v1/claims/batches/${id}`);target.append(el('p',`${L('الحالة الحالية','Current status')}: ${statusText(current.status)} · ${current.imported_count}/${current.record_count}`));await batches(id);}catch{}
        }finally{resume.disabled=false;}
      },'button primary');target.append(resume);
    }catch(error){target.replaceChildren(errorState(error));toast(error.message);}
  }

  async function mapColumns(id){
    const columns={claim_reference:'رقم الفاتورة',beneficiary_token:'الرقم التأميني',beneficiary_name:'اسم المستفيد',
      category_code:'الدرجة',provider_code:'مقدم الخدمة',service_code:'الخدمة',service_date:'تاريخ الخدمة',
      unit_price:'السعر',gross_amount:'المطلوب',copay_percent:'نسبة التحمل',copay_amount:'إجمالي التحمل',claimed_amount:'الصافي'};
    try{
      await api.request(`/api/v1/claims/batches/${id}/mapping`,{method:'PUT',body:{columns,currency:'LYD',date_format:'DMY'}});
      toast(L('تم التعرف على قالب المطالبات المعتمد دون ربط يدوي للأعمدة.','Approved claim template detected; no manual column mapping was needed.'));
      await showBatch();
    }catch(error){uploadResult.replaceChildren(errorState(error));toast(error.message);}
  }

  function attachInvoice(batch,entries){
    const form=el('form'),choice=field('attachment-line',L('بند المطالبة المرتبط بالفاتورة','Claim line linked to invoice'),{options:entries.map(row=>[row.id,`${row.claim_reference} · ${row.beneficiary_name||L('مستفيد','Beneficiary')} · ${row.service_code||''}`])}),source=field('invoice-file',L('الفاتورة / المرفق','Invoice / attachment'),{type:'file'});source.input.accept='.pdf,.png,.jpg,.jpeg';
    const feedback=el('div'),save=el('button',L('رفع وربط المرفق','Upload and link attachment'),'button primary');save.type='submit';form.append(choice.wrap,source.wrap,save,feedback);
    const kind=field('attachment-kind',L('نوع المستند','Document type'),{options:[['invoice',L('فاتورة الخدمة','Service invoice')],['supporting',L('مستند داعم آخر','Other supporting document')]]});form.prepend(kind.wrap);
    const fail=message=>{feedback.replaceChildren(panel(L('تعذر ربط المرفق','Could not link attachment'),message));toast(message);};
    form.addEventListener('submit',async event=>{event.preventDefault();if(save.disabled||!form.reportValidity())return;const file=source.input.files[0];if(!file)return;
      if(file.size>50*1024*1024){fail(L('حجم الملف يتجاوز 50 MiB','File exceeds 50 MiB'));return;}
      save.disabled=true;try{
        const doc=await api.request('/api/v1/ingestion/uploads?filename='+encodeURIComponent(file.name),{method:'POST',raw:true,body:await file.arrayBuffer()});
        let documentStatus=doc.status;
        if(documentStatus==='duplicate'){
          const existing=await api.request('/api/v1/ingestion/documents/'+doc.document_id);
          documentStatus=existing.status;
          if(!['extracted','review_required'].includes(documentStatus)){
            fail(L('المستند المكرر غير جاهز للربط. الحالة: ','Duplicate document is not ready for linking. Status: ')+statusText(documentStatus));return;
          }
          toast(L('الفاتورة مكررة؛ سنستخدم المستند الأصلي.','Duplicate invoice; using the original document.'));
        }else if(!['extracted','review_required'].includes(documentStatus)){
          fail(L('لم يكتمل استخراج المرفق. الحالة: ','Attachment extraction did not complete. Status: ')+statusText(documentStatus));return;
        }
        await api.request(`/api/v1/claims/entries/${choice.input.value}/attachments/${doc.document_id}?kind=${kind.input.value}`,{method:'POST'});
        const current=await api.request(`/api/v1/claims/batches/${batch.id}`);
        if(current.status!=='imported'){
          const message=L('تم ربط المرفق، لكن الدفعة ليست مكتملة الاستيراد. الحالة: ','Attachment linked, but the batch is not fully imported. Status: ')+statusText(current.status)+L('. أكمل الاستيراد قبل الفحص المرجعي.','. Complete the import before reference validation.');
          feedback.replaceChildren(panel(L('تم الربط مع تحذير','Linked with warning'),message),
            button(L('إكمال الاستيراد الآن','Complete import now'),()=>{document.getElementById('drawer').close();void completeImport(batch.id);},'button primary'));
          toast(message);await showBatch();return;
        }
        if(can('claims.review')){
          try{await api.request(`/api/v1/claims/batches/${batch.id}/reference-checks`,{method:'POST'});}
          catch(error){feedback.replaceChildren(errorState(error));toast(error.message);await showBatch();return;}
        }
        document.getElementById('drawer').close();toast(L('حُفظ المرفق ورُبط؛ نتيجة الاستخراج لا تعني اعتماده ماليًا','Attachment preserved and linked; extraction does not mean financial approval'));await showBatch();
      }catch(error){feedback.replaceChildren(errorState(error));toast(error.message);}finally{save.disabled=false;}});
    openDrawer(L('ربط الفاتورة ببند المطالبة','Link invoice to a claim line'),form);
  }

  async function showBatch(){
    const id=selected.input.value;if(!id){content.replaceChildren(empty(L('اختر دفعة أو ارفع ملفًا','Select a batch or upload a file'),L('لا تُنشأ نتائج مالية افتراضية.','No financial results are fabricated.')));return;}
    content.replaceChildren(loading());try{
      const batch=await api.request(`/api/v1/claims/batches/${id}`),page=await api.request(`/api/v1/claims/batches/${id}/entries?limit=100`);
      const body=el('div');
      const statusPanel=panel(L('حالة الدفعة','Batch status'),
        `${batch.display_name} · ${L('تاريخ الإدخال','Entry date')}: ${batch.entry_date} · ${L('الحالة','Status')}: ${statusText(batch.status)} · ${L('المستورد','Imported')}: ${batch.imported_count}/${batch.record_count}`);
      const statusActions=el('div',null,'actions');
      if(can('claims.import')&&['staged','importing','review_required','invalid_source'].includes(batch.status))
        statusActions.append(button(batch.status==='invalid_source'?L('إعادة فحص الملف','Recheck workbook'):L('إكمال الاستيراد','Complete import'),()=>void completeImport(id),'button primary'));
      statusPanel.append(statusActions);body.append(statusPanel);
      if(batch.status==='invalid_source'){
        const invalidPanel=panel(L('الملف يحتاج إلى تصحيح','Workbook needs correction'),L('لم يُستورد أي بند. صحح الأسباب التالية ثم ارفع نسخة جديدة، أو اضغط «إعادة فحص الملف» إذا كان الملف نفسه صالحاً بعد تحديث النظام:','No claim lines were imported. Fix the following reasons and upload a new copy, or select "Recheck workbook" if the same file is valid after the system update:'));
        try{
          const invalidPreview=await api.request(`/api/v1/claims/batches/${id}/preview?limit=100`),reasons=new Map();
          for(const row of invalidPreview.rows.filter(row=>!row.valid)){const text=reasonText(row.reason);reasons.set(text,(reasons.get(text)||0)+1);}
          if(reasons.size){for(const [text,count] of reasons)invalidPanel.append(el('p',`${count} × ${text}`));if(invalidPreview.record_count>100)invalidPanel.append(el('p',L('تُعرض أسباب أول 100 صف فقط.','Showing reasons for the first 100 rows only.')));}
          else invalidPanel.append(el('p',L('لم تظهر أسباب صفوف غير صالحة في المعاينة الحالية.','No invalid-row reasons appeared in the current preview.')));
        }catch(error){invalidPanel.append(el('p',error.message));}
        body.append(invalidPanel);
      }
      const actions=el('div',null,'actions');
      if(can('claims.import')&&batch.imported_count===0&&['staged','invalid_source'].includes(batch.status))actions.append(button(L('استخدام قالب المطالبات المعتمد','Use approved claims template'),()=>void mapColumns(id)));
      if(can('ingestion.upload')&&can('claims.import')&&page.entries.length)actions.append(button(L('إضافة فاتورة أو مرفق','Add invoice or attachment'),()=>attachInvoice(batch,page.entries)));
      if(can('claims.review')&&batch.status==='imported')actions.append(button(L('تجهيز ملفي النتائج','Prepare the two result files'),async()=>{
        try{await api.request('/api/v1/phase1/batches/'+id+'/run',{method:'POST'});ctx.navigate(ctx.scope==='admin'?'/admin/result-reports':'/institution/result-reports');}
        catch(error){toast(error.message);}}));
      if(can('claims.review')&&batch.status==='imported'){
        const run=button(L('بدء التدقيق','Start audit'),async()=>{run.disabled=true;try{
          const result=await api.request(`/api/v1/phase1/batches/${id}/run`,{method:'POST'});
          const s=result.summary;
          const out=panel(L('نتيجة التدقيق — المرحلة الأولى','Phase 1 audit result'),`${L('مطابق مبدئي','Preliminary match')}: ${s.accepted||0} · ${L('مرفوض','Rejected')}: ${s.rejected||0} · ${L('مكرر','Duplicate')}: ${s.duplicates||0}`);
          body.append(out);toast(L('حُفظت النتائج؛ المطابق مبدئي وليس اعتماداً مالياً نهائياً.','Results recorded; preliminary matches are not final financial approvals.'));
        }catch(error){toast(error.message);}finally{run.disabled=false;}},'button primary');actions.append(run);
      }
      body.append(panel(L('حالة التدقيق — المرحلة الأولى','Phase 1 audit status'),L('تحقق مرجعي وحسابي كامل: المستفيد والفئة والمزود والخدمة والعقد واللائحة والتسعيرة والتحمل. لا يشترط مرفق فاتورة ولا سياسة مالية موقعة في هذه المرحلة، والنتيجة مبدئية حتى الاعتماد البشري.','Complete reference and arithmetic validation: beneficiary, category, provider, service, contract, regulation, tariff and copay. No invoice attachment or signed financial policy is required in this phase; outcomes remain preliminary until human approval.')));
      const wrap=el('div',null,'table-scroll'),table=el('table'),head=el('tr');for(const label of [L('#','#'),L('رقم الفاتورة','Invoice'),L('اسم المستفيد','Beneficiary'),L('الرقم التأميني','Insurance number'),L('الدرجة','Category'),L('الخدمة','Service'),L('تاريخ الخدمة','Service date'),L('الصافي الأصلي','Original net'),L('العملة','Currency')])head.append(el('th',label));const thead=el('thead');thead.append(head);table.append(thead);const tbody=el('tbody');
      for(const [index,row] of page.entries.entries()){const tr=el('tr');for(const value of [String(index+1),row.claim_reference,row.beneficiary_name||'—',row.beneficiary_token||'—',categoryText(row.category_code),row.service_code||'—',row.service_date,row.claimed_amount,row.currency])tr.append(el('td',value));tbody.append(tr);}table.append(tbody);wrap.append(table);
      body.append(wrap);if(batch.imported_count>100)body.append(el('p',L('تُعرض أول 100 نتيجة فقط؛ ملفات النتائج تشمل كامل الدفعة ضمن حدود التصدير.','Showing the first 100 entries; output files cover the complete batch within export limits.')));body.append(actions);content.replaceChildren(body);
    }catch(error){content.replaceChildren(errorState(error,()=>void showBatch()));}
  }
  batches().catch(error=>content.replaceChildren(errorState(error)));
}

export function resultReports(ctx){
  const {root,api,context}=ctx;root.append(panel(L('ملفا النتائج','Result files'),L('الأخضر للمطابق مبدئيًا، والأحمر لكل بند مرفوض مع جميع أسبابه في أعمدة منفصلة. كل بند يرد مرة واحدة، والنتائج ليست اعتمادًا ماليًا نهائيًا.','Green contains preliminary matches; red contains every rejected line with all reasons in separate columns. Each line appears once. These are not final financial approvals.')));
  const content=el('div');root.append(content);let offset=0;
  const statusLabel=status=>status==='accepted'?L('مطابق مبدئي','Preliminary match'):L('مرفوض','Rejected');
  async function show(result){const body=el('div');body.append(loading());openDrawer(L('بنود النتائج','Result lines'),body);try{const page=await api.request(`/api/v1/phase1/results/${result.id}/lines?limit=100`);body.replaceChildren();
    for(const row of page.items){const reasons=Object.values(row.reasons||{}).flat().join('؛ ');const invoice=row.values?.['رقم الفاتورة']||row.line_id;const service=row.values?.['الخدمة']||'';const net=row.computed?.net_amount||row.values?.['الصافي']||'';const item=panel(`${invoice} · ${statusLabel(row.status)}`,`${service} · ${net} ${row.currency} · ${reasons||L('لا أسباب','No reasons')}`);
      if(row.notes?.length)item.append(el('p',L('ملاحظات: ','Notes: ')+row.notes.join('؛ ')));
      body.append(item);}
    if(page.has_more)body.append(el('p',L('تُعرض أول 100 نتيجة؛ التنزيل يشمل كامل المجموعة.','Showing the first 100 lines; downloads cover the complete set.')));
  }catch(error){body.replaceChildren(errorState(error));}}
  async function load(){content.replaceChildren(loading());try{const page=await api.request(`/api/v1/phase1/results?offset=${offset}&limit=25`);const list=el('div');
    for(const result of page.items){const s=result.summary;const card=panel(`${L('نتائج المرحلة الأولى','Phase 1 results')} · ${L('عدد البنود: ','Lines: ')}${s.total_lines}`,'');
      card.append(el('p',`${L('مطابق مبدئي — الملف الأخضر','Preliminary matches — green file')}: ${s.accepted||0}`));
      card.append(el('p',`${L('مرفوض — الملف الأحمر','Rejected — red file')}: ${s.rejected||0}`));
      card.append(el('p',`${L('منها مكرر','Duplicates')}: ${s.duplicates||0}`));
      const actions=el('div',null,'actions');if(context.permissions.includes('claims.read'))actions.append(button(L('عرض البنود والأسباب','View lines and reasons'),()=>void show(result)));
      const green=button(L('تنزيل الملف الأخضر','Download green file'),async()=>{try{saveBlob(await api.request(`/api/v1/phase1/results/${result.id}/green`,{download:true}),`mcias-green-${result.id}.xlsx`);}catch(error){toast(error.message);}});green.disabled=!context.permissions.includes('reports.export');
      const red=button(L('تنزيل الملف الأحمر','Download red file'),async()=>{try{saveBlob(await api.request(`/api/v1/phase1/results/${result.id}/red`,{download:true}),`mcias-red-${result.id}.xlsx`);}catch(error){toast(error.message);}});red.disabled=!context.permissions.includes('reports.export');
      actions.append(green,red);card.append(actions);list.append(card);}
    const pagination=el('div',null,'actions');if(offset)pagination.append(button(L('السابق','Previous'),()=>{offset-=25;void load();}));if(page.has_more)pagination.append(button(L('التالي','Next'),()=>{offset+=25;void load();}));
    content.replaceChildren(page.items.length?list:empty(L('لا نتائج محفوظة','No frozen results'),L('جهّز النتائج من دفعة مكتملة الاستيراد في قسم التدقيق.','Prepare results from a fully imported batch in Audit.')),pagination);
  }catch(error){content.replaceChildren(errorState(error,()=>void load()));}}void load();
}
