import { L, el, field, button, panel, empty, loading, errorState, openDrawer, confirm, toast, link } from './ui.js';

export function accessManagement(ctx) {
  const { root, api, context } = ctx;
  const technical = context.roles.includes('super_admin');
  const canManageUsers = context.permissions.includes('users.read') && context.permissions.includes('users.create');
  if (technical) root.append(organizationAdminPanel());
  root.append(panel(L('المستخدمون والصلاحيات','Users and permissions'),
    L('لإضافة الموظفين وإسناد الفئات المعتمدة استخدم «إدارة حسابات المستخدمين». منح الصلاحيات الفردية يبقى محصوراً بالسوبر أدمن ويسجل النظام كل منح أو سحب.',
      'Use “Manage user accounts” to add employees and assign approved groups. Individual permission grants remain restricted to the super-admin, and every grant or revocation is logged.')));
  function organizationAdminPanel() {
    const card=panel(L('إدارة الجهة','Organization administration'),
      L('غيّر اسم الجهة وامنح أعضاء مجموعة مدير الجهة الصلاحيات الإدارية غير المالية. كل إجراء يُسجل في التدقيق.',
        'Change the organization name and grant non-financial administrative permissions to institution-admin group members. Every action is audited.'),'settings');
    const body=el('div');card.append(body);let generation=0,stateGeneration=0;
    async function load(){
      const current=++generation;body.replaceChildren(loading());
      try{
        const page=await api.request('/api/v1/admin/organizations?offset=0&limit=100');
        if(current!==generation)return;
        if(!page.items.length){body.replaceChildren(empty(L('لا جهات','No organizations'),L('لم تُسجل جهات.','No organizations registered.')));return;}
        const organizations=new Map(page.items.map(item=>[item.id,item]));
        const selector=field('control-organization',L('الجهة','Organization'),{options:page.items.map(item=>[item.id,item.name])});
        const name=field('control-organization-name',L('اسم الجهة','Organization name'),{maxLength:200});
        const reason=field('control-organization-reason',L('سبب الإجراء الإداري','Administrative action reason'),{maxLength:500});
        const feedback=el('div');feedback.setAttribute('aria-live','polite');
        const status=el('div');
        async function showState(id){
          const version=++stateGeneration;status.replaceChildren(loading());
          try{
            const currentState=await api.request(`/api/v1/admin/organizations/${encodeURIComponent(id)}/admin-state`);
            if(version!==stateGeneration||current!==generation)return;
            const groups=currentState.admin_groups.map(group=>L(group.name_ar,group.name_en||group.name_ar)).join(' · ')||L('لا توجد مجموعة إدارية','No institution-admin group');
            status.replaceChildren(el('p',`${L('المجموعة','Group')}: ${groups} · ${L('الأعضاء المؤهلون','Eligible members')}: ${currentState.admin_members.length} · ${L('الصلاحيات','Permissions')}: ${currentState.permission_count}`,'muted'));
          }catch(error){if(version===stateGeneration)status.replaceChildren(errorState(error));}
        }
        const save=button(L('حفظ اسم الجهة','Save organization name'),async()=>{
          if(save.disabled||!form.reportValidity())return;save.disabled=true;
          try{
            const updated=await api.request(`/api/v1/admin/organizations/${encodeURIComponent(selector.input.value)}/name`,{method:'PUT',body:{name:name.input.value.trim(),reason:reason.input.value.trim()}});
            const option=[...selector.input.options].find(item=>item.value===selector.input.value);if(option)option.textContent=updated.name;
            organizations.get(selector.input.value).name=updated.name;
            toast(updated.changed?L('تم تحديث اسم الجهة','Organization name updated'):L('اسم الجهة لم يتغير','Organization name unchanged'));
            feedback.replaceChildren(el('p',L('حُفظ اسم الجهة وسُجل الإجراء.','Organization name saved and action logged.')));
          }catch(error){feedback.replaceChildren(errorState(error));}finally{save.disabled=false;}
        });
        const grant=button(L('منح صلاحيات مدير الجهة','Grant institution-admin permissions'),async()=>{
          if(grant.disabled||!form.reportValidity())return;
          if(!(await confirm(L('منح صلاحيات الإدارة غير المالية لأعضاء مجموعة مدير الجهة؟','Grant non-financial administrative permissions to institution-admin group members?'))))return;
          grant.disabled=true;
          try{
            const result=await api.request(`/api/v1/admin/organizations/${encodeURIComponent(selector.input.value)}/grant-admin`,{method:'POST',body:{reason:reason.input.value.trim()}});
            feedback.replaceChildren(el('p',L(`تم منح ${result.permission_count} صلاحية لـ ${result.granted.length} حساب؛ ${result.unchanged.length} حساب كان محدثاً.`,
              `${result.permission_count} permissions granted to ${result.granted.length} account(s); ${result.unchanged.length} already current.`)));
            toast(L('تم منح صلاحيات مدير الجهة','Institution-admin permissions granted'));
            await showState(selector.input.value);
          }catch(error){feedback.replaceChildren(errorState(error));}finally{grant.disabled=false;}
        },'button primary');
        const actions=el('div',null,'actions');actions.append(save,grant);
        const form=el('form');form.append(selector.wrap,name.wrap,reason.wrap,actions,feedback,status);
        selector.input.addEventListener('change',()=>{const item=organizations.get(selector.input.value);if(item)name.input.value=item.name;void showState(selector.input.value);});
        const initial=organizations.get(selector.input.value);if(initial)name.input.value=initial.name;body.replaceChildren(form);await showState(selector.input.value);
      }catch(error){if(current===generation)body.replaceChildren(errorState(error,()=>void load()));}
    }
    void load();return card;
  }
  const content=el('div');root.append(content);let offset=0;
  const tools=el('div',null,'actions');
  if (technical || canManageUsers) tools.append(link(L('إدارة حسابات المستخدمين','Manage user accounts'),'/institution/users','button'));
  if (technical) tools.append(link(L('ميزات الجهة','Organization features'),'/admin/features','button'),
    link(L('الأمان والتحقق','Security and MFA'),'/admin/security','button'),
    link(L('صحة النظام','System health'),'/admin/system-health','button'));
  if (tools.childElementCount) root.append(tools);
  async function edit(user) {
    const body=el('div');body.append(loading());openDrawer(L('صلاحيات المستخدم','User permissions'),body);
    try {
      const [current,catalog]=await Promise.all([api.request(`/api/v1/admin/access/users/${user.id}`),api.request('/api/v1/admin/access/catalog')]);
      if(current.technical_account){body.replaceChildren(empty(L('حساب إدارة تقنية','Technical account'),L('الصلاحيات التشغيلية والمالية لا تُمنح تلقائيًا للحساب التقني.','Functional and financial approval grants are separate from technical administration.')));return;}
      const form=el('form'), selected=new Set(current.codes), groups=new Map();
      form.append(el('h3',user.username),el('p',L('اختر الصلاحيات بناءً على الإجراءات التي اتخذتها خارج النظام.','Select permissions based on the administrative procedures completed outside MCIAS.')));
      for(const item of catalog.items.filter(p=>p.delegable)){
        const category=item.category||'other';if(!groups.has(category)){const box=el('fieldset',null,'permission-group');box.append(el('legend',category));groups.set(category,box);form.append(box);}
        const label=el('label',null,'permission-option'),check=el('input');check.type='checkbox';check.value=item.code;check.checked=selected.has(item.code);
        check.addEventListener('change',()=>check.checked?selected.add(item.code):selected.delete(item.code));
        label.append(check,el('span',L(item.description_ar||item.code,item.description_en||item.code)),el('code',item.code));groups.get(category).append(label);
      }
      const reason=field('access-reason',L('وصف الإجراء الإداري','Administrative action note'),{maxLength:500});
      const feedback=el('div');feedback.setAttribute('aria-live','polite');
      const save=el('button',L('حفظ الصلاحيات','Save permissions'),'button primary');save.type='submit';
      const cancel=button(L('إلغاء','Cancel'),()=>document.getElementById('drawer').close());
      const controls=el('div',null,'actions');controls.append(save,cancel);form.append(reason.wrap,controls,feedback);
      form.addEventListener('submit',async event=>{event.preventDefault();if(save.disabled||!form.reportValidity())return;save.disabled=true;
        try{await api.request(`/api/v1/admin/access/users/${user.id}`,{method:'PUT',body:{codes:[...selected].sort(),expected_revision:current.revision,reason:reason.input.value.trim()}});
          document.getElementById('drawer').close();toast(L('حُفظت الصلاحيات وسُجل الإجراء','Permissions saved and action logged'));await load();
        }catch(error){feedback.replaceChildren(errorState(error));}finally{save.disabled=false;}});
      body.replaceChildren(form);
    }catch(error){body.replaceChildren(errorState(error));}
  }
  async function load(){content.replaceChildren(loading());try{
    const page=await api.request(`/api/v1/users?offset=${offset}&limit=25`),wrap=el('div',null,'table-scroll'),table=el('table'),head=el('tr');
    for(const title of [L('اسم المستخدم','Username'),L('الحالة','Status'),L('الإجراءات','Actions')])head.append(el('th',title));
    const thead=el('thead');thead.append(head);table.append(thead);const tbody=el('tbody');
    const visible=page.items.filter(user=>!user.username.startsWith('SYN-seed-rbac-')&&user.username!=='SYN-laptop-admin');
    for(const user of visible){const row=el('tr'),actions=el('td');row.append(el('td',user.username),el('td',user.status));
      if(technical)actions.append(button(L('الصلاحيات','Permissions'),()=>void edit(user)));row.append(actions);tbody.append(row);}
    if(!visible.length){const emptyRow=el('tr'),emptyCell=el('td',L('لا توجد حسابات إدارية قابلة للتعديل. استخدم «مستخدمو الجهة» لإضافة الموظفين وإسناد الفئات.','No editable administrative accounts. Use “Institution users” to add employees and assign groups.'));emptyCell.colSpan=3;emptyRow.append(emptyCell);tbody.append(emptyRow);}
    table.append(tbody);wrap.append(table);const pagination=el('div',null,'actions');
    if(offset)pagination.append(button(L('السابق','Previous'),()=>{offset-=25;void load();}));
    if(page.has_more)pagination.append(button(L('التالي','Next'),()=>{offset+=25;void load();}));
    content.replaceChildren(wrap,pagination);
  }catch(error){content.replaceChildren(errorState(error,()=>void load()));}}
  void load();
}
