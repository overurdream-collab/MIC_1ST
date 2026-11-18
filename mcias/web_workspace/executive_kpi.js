import {
  L,
  el,
  icon,
  button,
  badge,
  panel,
  loading,
  empty,
  errorState,
  field,
  kpi,
  table,
  openDrawer,
  preferences,
} from "./ui.js";

const defaults = {
  batch_id: "",
  provider: "",
  service: "",
  currency: "",
  outcome: "",
  rule: "",
  date_basis: "service",
  date_from: "",
  date_to: "",
  q: "",
};
const outcomeLabels = {
  accepted: ["مطابق مبدئي", "Preliminary match"],
  rejected: ["مرفوض", "Rejected"],
  held: ["معلّق", "Held"],
  review: ["يحتاج مراجعة", "Review"],
  unverified: ["غير محسوم", "Unverified"],
  flagged: ["موسوم", "Flagged"],
};
const categoryLabels = {
  price: ["فرق السعر", "Price variance"],
  duplicate: ["تكرار", "Duplicate"],
  missing_data: ["نقص بيانات", "Missing data"],
  eligibility: ["أهلية", "Eligibility"],
  policy: ["سياسة أو تعرفة", "Policy or tariff"],
  verification: ["تحقق", "Verification"],
  evidence: ["دليل أو مرفق", "Evidence or attachment"],
  other: ["ملاحظات أخرى", "Other notes"],
};

function formatNumber(value) {
  return new Intl.NumberFormat(preferences.lang).format(Number(value));
}
function formatRate(value) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat(preferences.lang, {
    style: "percent",
    maximumFractionDigits: 1,
  }).format(value);
}
function formatMoney(value, currency) {
  if (value === null || value === undefined) return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return (
    new Intl.NumberFormat(preferences.lang, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 4,
    }).format(number) +
    " " +
    currency
  );
}
function categoryLabel(value, fallback) {
  const labels = categoryLabels[value] || fallback;
  return labels ? L(labels[0], labels[1]) : value;
}
function outcomeLabel(value) {
  const labels = outcomeLabels[value] || [value, value];
  return L(labels[0], labels[1]);
}
function amountView(data, field) {
  const currencies = Object.keys(data.summary.by_currency).sort();
  if (!currencies.length) return "—";
  if (data.filters.currency)
    return formatMoney(data.summary.by_currency[data.filters.currency][field], data.filters.currency);
  if (currencies.length === 1)
    return formatMoney(data.summary.by_currency[currencies[0]][field], currencies[0]);
  return L("متعدد العملات", "Multiple currencies");
}
function queryFor(state, extra = {}) {
  const values = { ...state, ...extra };
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (!value) continue;
    params.set(key === "service" ? "service_code" : key, value);
  }
  return params.toString();
}
function metricCard(title, value, note, asset, onOpen) {
  const card = kpi(title, value, asset, note);
  if (onOpen) {
    const foot = card.querySelector(".kpi-foot");
    foot.append(button(L("عرض البنود", "View lines"), onOpen, "link-button"));
  }
  return card;
}
function legendButton(count, total, label, className, onSelect) {
  const row = button("", onSelect, "legend-row");
  row.append(el("span", null, "legend-dot " + className));
  const text = el("span");
  text.append(el("strong", label), el("span", " · " + formatNumber(count) + " · " + formatRate(total ? count / total : null)));
  row.append(text);
  return row;
}
function outcomeChart(counts, onSelect) {
  const total = Object.values(counts).reduce((sum, value) => sum + value, 0);
  const wrap = el("div", null, "donut-wrap");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "donut");
  svg.setAttribute("viewBox", "0 0 160 160");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", L("توزيع نتائج البنود", "Line outcome distribution"));
  const make = (tag, attrs, text) => {
    const node = document.createElementNS(svg.namespaceURI, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    svg.append(node);
    return node;
  };
  const circumference = 2 * Math.PI * 70;
  make("circle", {
    cx: 80,
    cy: 80,
    r: 70,
    fill: "none",
    "stroke-width": 16,
    class: "track",
  });
  if (total) {
    let offset = 0;
    for (const key of ["accepted", "rejected", "held", "review"]) {
      const count = counts[key] || 0;
      if (!count) continue;
      const length = (count / total) * circumference;
      const segment = make("circle", {
        cx: 80,
        cy: 80,
        r: 70,
        fill: "none",
        "stroke-width": 16,
        "stroke-dasharray": length + " " + circumference,
        "stroke-dashoffset": -offset,
        transform: "rotate(-90 80 80)",
        tabindex: 0,
        role: "button",
        class: "outcome-segment " + key,
        "aria-label": outcomeLabel(key) + ": " + count,
      });
      const open = () => onSelect(key);
      segment.addEventListener("click", open);
      segment.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          open();
        }
      });
      offset += length;
    }
  }
  make("text", { x: 80, y: 81, "text-anchor": "middle" }, formatNumber(total));
  make("text", { x: 80, y: 102, "text-anchor": "middle", class: "small" }, L("بند", "lines"));
  wrap.append(svg);
  const legend = el("div", null, "chart-legend");
  for (const key of ["accepted", "rejected", "held", "review"]) {
    legend.append(legendButton(counts[key] || 0, total, outcomeLabel(key), key, () => onSelect(key)));
  }
  wrap.append(legend);
  return wrap;
}
function reasonChart(items, onSelect) {
  const rows = items.slice(0, 8);
  if (!rows.length) return empty(L("لا توجد ملاحظات", "No notes"), L("لم تُسجل ملاحظات في البنود المطابقة لهذه التصفية.", "No notes were recorded for the filtered lines."), "audit", true);
  const width = 640,
    rowHeight = 30,
    height = 18 + rows.length * rowHeight,
    max = Math.max(1, ...rows.map(row => row.count));
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "reason-chart");
  svg.setAttribute("viewBox", "0 0 " + width + " " + height);
  svg.setAttribute("role", "group");
  svg.setAttribute("aria-label", L("توزيع الملاحظات حسب النوع", "Notes by category"));
  const make = (tag, attrs, text) => {
    const node = document.createElementNS(svg.namespaceURI, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    svg.append(node);
    return node;
  };
  rows.forEach((row, index) => {
    const y = 8 + index * rowHeight;
    const label = categoryLabel(row.value, row.labels).slice(0, 38);
    const group = make("g", {
      class: "chart-hit",
      tabindex: 0,
      role: "button",
      "aria-label": label + ": " + row.count,
    });
    const title = make("title", {}, label + ": " + row.count);
    group.append(title);
    const text = make("text", { x: 218, y: y + 18, class: "reason-label", "text-anchor": "end", direction: "ltr" }, label);
    const bar = make("rect", {
      x: 230,
      y: y + 3,
      width: Math.max(3, (row.count / max) * 330),
      height: 20,
      rx: 5,
      class: "reason-bar",
      "data-category": row.value,
    });
    const count = make("text", { x: 575, y: y + 18, class: "reason-count" }, formatNumber(row.count));
    group.append(text, bar, count);
    const open = () => onSelect(row.value);
    group.addEventListener("click", open);
    group.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });
  });
  return svg;
}
function simpleTable(columns, rows, currency) {
  const wrap = el("div", null, "table-scroll");
  wrap.tabIndex = 0;
  wrap.setAttribute("role", "region");
  wrap.setAttribute("aria-label", L("تفصيل العملات", "Currency breakdown"));
  const tableElement = el("table"),
    caption = el("caption", L("المبالغ حسب العملة؛ لا تُجمع العملات معًا.", "Amounts by currency; currencies are never combined."), "sr-only"),
    thead = el("thead"),
    headRow = el("tr"),
    body = el("tbody");
  tableElement.append(caption);
  for (const column of columns) {
    const th = el("th", column.label);
    th.scope = "col";
    headRow.append(th);
  }
  thead.append(headRow);
  tableElement.append(thead);
  for (const row of rows) {
    const tr = el("tr");
    for (const column of columns) {
      const td = el("td", column.render ? column.render(row) : row[column.key]);
      if (column.id) td.className = "table-id";
      td.title = column.render ? td.textContent : String(row[column.key] ?? "");
      tr.append(td);
    }
    body.append(tr);
  }
  tableElement.append(body);
  wrap.append(tableElement);
  return wrap;
}
function currencyPanel(data) {
  const currencies = Object.keys(data.summary.by_currency).sort();
  const section = panel(L("المبالغ حسب العملة", "Amounts by currency"), L("كل عملة منفصلة؛ لا يوجد تحويل أو جمع بين العملات.", "Each currency is separate; no conversion or cross-currency total."), "reports");
  const columns = [
    { label: L("العملة", "Currency"), key: "currency", id: true },
    { label: L("المطالب به", "Claimed"), render: row => formatMoney(row.claimed, row.currency) },
    { label: L("القيمة الموسومة", "Flagged value"), render: row => formatMoney(row.flagged, row.currency) },
    { label: L("الفرق المقترح", "Proposed difference"), render: row => formatMoney(row.proposed_difference, row.currency) },
    { label: L("فرق السعر", "Price variance"), render: row => formatMoney(row.price_variance, row.currency) },
    { label: L("تعرض التكرار", "Duplicate exposure"), render: row => formatMoney(row.duplicate_exposure, row.currency) },
  ];
  const rows = currencies.map(currency => ({ currency, ...data.summary.by_currency[currency] }));
  section.append(rows.length ? simpleTable(columns, rows) : empty(L("لا مبالغ", "No amounts"), L("لا توجد بنود مطابقة للتصفية.", "No lines match the filter."), "reports", true));
  return section;
}
function qualityPanel(data) {
  const quality = data.quality;
  const section = panel(L("جودة البيانات ومصدر المؤشرات", "Data quality and metric provenance"), L("المؤشرات مصدرها آخر مجموعة نتائج مجمّدة لكل دفعة، وليست إعادة تدقيق.", "Metrics come from the latest frozen result set per batch, not a re-audit."), "security");
  const list = el("dl", null, "quality-list");
  const row = (label, value, note) => {
    const item = el("div");
    item.append(el("dt", label), el("dd", value));
    if (note) item.append(el("p", note, "muted"));
    list.append(item);
  };
  row(L("اكتمال المعالجة", "Processing completion"), formatRate(quality.processing_completion.rate),
    formatNumber(quality.processing_completion.numerator) + " / " + formatNumber(quality.processing_completion.denominator));
  const recon = quality.reconciliation;
  row(
    L("مطابقة الاستيراد مع النتائج", "Import-to-results reconciliation"),
    recon.status === "reconciled" ? L("متطابقة", "Reconciled") : recon.status === "mismatch" ? L("فرق", "Mismatch") : L("غير متاح", "Unavailable"),
    L("المستورد: ", "Imported: ") + formatNumber(recon.imported_records) + " · " + L("بنود النتائج: ", "Result lines: ") + formatNumber(recon.result_lines),
  );
  row(L("نقص البيانات", "Missing data"), formatRate(quality.missing_data_rate.rate),
    formatNumber(quality.missing_data_rate.numerator) + " / " + formatNumber(quality.missing_data_rate.denominator));
  row(L("البنود غير المحسومة", "Unverified lines"), formatRate(quality.unverified_rate.rate),
    formatNumber(quality.unverified_rate.numerator) + " / " + formatNumber(quality.unverified_rate.denominator));
  row(L("القواعد التي شُغّلت", "Rules actually run"),
    quality.rules_run.available ? formatNumber(quality.rules_run.count) : L("غير متاح", "Unavailable"),
    L("وكلاء التدقيق المرصودون في اللقطات؛ عدد رموز الملاحظات المرصودة: ", "Audit agents observed in snapshots; distinct note codes observed: ") + formatNumber(quality.rules_run.note_codes_count));
  row(L("زمن التدقيق", "Audit duration"), L("غير متاح", "Unavailable"),
    L("لا تسجل اللقطات الحالية وقت البداية والنهاية؛ لا يُستنتج الزمن من تواريخ الحفظ.", "Current snapshots do not record start and end times; storage timestamps are not used as duration."));
  section.append(list);
  if (quality.rules_run.available) {
    const details = el("details", null, "metric-provenance");
    details.append(el("summary", L("وكلاء التدقيق", "Audit agents")), el("code", quality.rules_run.agents.join(" · ")));
    section.append(details);
  }
  return section;
}
function definitionsPanel() {
  const section = panel(L("منهجية المؤشرات", "Metric definitions"), L("تعريفات ثابتة تمنع تفسير القيم الموسومة كخسائر مؤكدة.", "Fixed definitions prevent flagged values from being read as confirmed losses."), "reports");
  const list = el("dl", null, "quality-list");
  const add = (term, text) => {
    const item = el("div");
    item.append(el("dt", term), el("dd", text));
    list.append(item);
  };
  add(L("مطابق مبدئي", "Preliminary match"), L("البنود المصنفة accepted فقط.", "Lines classified as accepted only."));
  add(L("مرفوض وغير محسوم", "Rejected and unverified"), L("مرفوض = rejected؛ غير محسوم = held + review. لا يُعد أي منهما اعتمادًا ماليًا نهائيًا.", "Rejected = rejected; unverified = held + review. Neither is a final financial approval."));
  add(L("القيمة الموسومة", "Flagged value"), L("مجموع صافي البنود غير المقبولة مرة واحدة. ليست خسارة مؤكدة ولا مبلغًا نهائيًا.", "Sum of non-accepted line net values once. Not a confirmed loss or a final amount."));
  add(L("فرق السعر", "Price variance"), L("مجموع استقطاعات التعرفة الموجبة فقط عند وجود مقارنة مرجعية مجمّدة. ليس وفورات محققة.", "Sum of positive tariff deductions only where a frozen reference comparison exists. Not realized savings."));
  add(L("تعرض التكرار", "Duplicate exposure"), L("قيمة البنود المكررة الفريدة. تعرض محتمل وقد يتداخل مع القيمة الموسومة.", "Value of unique duplicate lines. Potential exposure that may overlap flagged value."));
  add(L("الفرق المقترح", "Proposed difference"), L("فرق البنود المرفوضة المبدئي، وليس مبلغًا معتمدًا.", "Preliminary rejected-line difference, not an approved amount."));
  add(L("العملات", "Currencies"), L("لا تُجمع العملات أو تُحوّل. اختر عملة لعرض مبالغ موحدة.", "Currencies are neither combined nor converted. Select a currency for a unified amount view."));
  add(L("المصدر", "Source"), L("آخر مجموعة نتائج مجمّدة لكل دفعة ضمن نطاق التصفية، مع التحقق من بصمة السلامة.", "Latest frozen result set per batch in scope, with integrity fingerprint verification."));
  section.append(list);
  return section;
}
function providerServiceTable(data, key, title, description, asset, onOpen) {
  const rows = data[key].slice(0, 25);
  const currency = data.filters.currency || (Object.keys(data.summary.by_currency).length === 1 ? Object.keys(data.summary.by_currency)[0] : "");
  const tableRows = rows.map(row => {
    const amounts = currency ? row.amounts[currency] || {} : {};
    return {
      [key === "providers" ? "provider_code" : "service_code"]: row.value,
      total_lines: row.lines,
      matched_count: row.matched,
      unmatched_count: row.unmatched,
      unverified_count: row.unverified,
      flagged_value: currency ? formatMoney(amounts.flagged, currency) : L("متعدد", "Multiple"),
      price_variance: currency ? formatMoney(amounts.price_variance, currency) : L("متعدد", "Multiple"),
    };
  });
  const section = panel(title, description, asset);
  if (!tableRows.length) {
    section.append(empty(L("لا نتائج", "No results"), L("لا توجد بنود مطابقة للتصفية.", "No lines match the filter."), asset, true));
    return section;
  }
  const target = el("div");
  section.append(target);
  table(target, tableRows, {
    columns: [
      key === "providers" ? "provider_code" : "service_code",
      "total_lines",
      "matched_count",
      "unmatched_count",
      "unverified_count",
      "flagged_value",
      "price_variance",
    ],
    open: row => onOpen(key === "providers" ? { provider: row.provider_code } : { service: row.service_code }),
    caption: title,
  });
  section.append(el("p", L("أعلى 25 مجموعة حسب عدد البنود. المبالغ تظهر عند اختيار عملة واحدة.", "Top 25 groups by line count. Amounts display when one currency is selected."), "chart-note"));
  return section;
}

export function executiveKpi(ctx) {
  const state = { ...defaults };
  const hero = el("section", null, "hero executive-hero");
  const heroText = el("div");
  heroText.append(
    badge(L("قراءة فقط من النتائج المجمّدة", "Read-only from frozen results")),
    el("h2", L("MCIAS Core — لوحة المؤشرات التنفيذية", "MCIAS Core — Executive KPI Dashboard")),
    el("p", L("نظرة تنفيذية على المطابقة والاختلافات وجودة البيانات، مع فصل العملات وتعريف صريح لكل مؤشر. هذه ليست اعتمادًا ماليًا.", "An executive view of matching, differences, and data quality, with currencies separated and every metric defined. This is not financial approval.")),
  );
  hero.append(heroText, icon("dashboard"));
  ctx.root.append(hero);
  const filterHost = el("div");
  const content = el("div");
  ctx.root.append(filterHost, content);
  let generation = 0;

  async function load() {
    const current = ++generation;
    content.replaceChildren(loading());
    try {
      const params = queryFor(state);
      const data = await ctx.api.request("/api/v1/analytics/kpi" + (params ? "?" + params : ""));
      if (current !== generation) return;
      renderFilters(data.options);
      render(data);
    } catch (error) {
      if (current !== generation) return;
      content.replaceChildren(errorState(error, load));
    }
  }

  function renderFilters(options) {
    const form = el("form", null, "filters executive-filters");
    const all = ["", L("الكل", "All")];
    const optionList = (items, labeler = item => item.label) => [all].concat(items.map(item => [item.value, labeler(item)]));
    const batch = field("batch_id", L("الدفعة", "Batch"), {
      required: false,
      value: state.batch_id,
      options: optionList(options.batches),
    });
    const provider = field("provider", L("مقدم الخدمة", "Provider"), {
      required: false,
      value: state.provider,
      options: optionList(options.providers),
    });
    const service = field("service", L("رمز الخدمة", "Service code"), {
      required: false,
      value: state.service,
      options: optionList(options.services),
    });
    const currency = field("currency", L("العملة", "Currency"), {
      required: false,
      value: state.currency,
      options: [all].concat(options.currencies.map(item => [item, item])),
    });
    const outcome = field("outcome", L("النتيجة", "Outcome"), {
      required: false,
      value: state.outcome,
      options: [all].concat(options.outcomes.map(item => [item, outcomeLabel(item)])),
    });
    const rule = field("rule", L("نوع الملاحظة", "Note type"), {
      required: false,
      value: state.rule,
      options: [all].concat(options.rules.map(item => [item.value, categoryLabel(item.value, item.labels) + " (" + formatNumber(item.count) + ")"])),
    });
    const basis = field("date_basis", L("أساس التاريخ", "Date basis"), {
      required: false,
      value: state.date_basis,
      options: [["service", L("تاريخ الخدمة", "Service date")], ["audit", L("تاريخ التدقيق", "Audit date")]],
    });
    const from = field("date_from", L("من تاريخ", "From"), { required: false, type: "date", value: state.date_from });
    const to = field("date_to", L("إلى تاريخ", "To"), { required: false, type: "date", value: state.date_to });
    const search = field("q", L("بحث", "Search"), {
      required: false,
      value: state.q,
      placeholder: L("فاتورة، مقدم خدمة، خدمة…", "Invoice, provider, service…"),
    });
    const actions = el("div", null, "actions");
    const submit = el("button", L("تطبيق", "Apply"), "button primary");
    submit.type = "submit";
    const reset = button(L("إعادة الضبط", "Reset"), () => {
      Object.assign(state, defaults);
      void load();
    });
    const refresh = button(L("تحديث", "Refresh"), () => void load(), "button", "refresh");
    actions.append(submit, reset, refresh);
    form.append(batch.wrap, provider.wrap, service.wrap, currency.wrap, outcome.wrap, rule.wrap, basis.wrap, from.wrap, to.wrap, search.wrap, actions);
    const sync = target => {
      if (!(target.name in state)) return;
      state[target.name] = target.value;
    };
    form.addEventListener("change", event => {
      sync(event.target);
      void load();
    });
    form.addEventListener("submit", event => {
      event.preventDefault();
      for (const input of form.querySelectorAll("input,select")) sync(input);
      void load();
    });
    filterHost.replaceChildren(form);
  }

  function render(data) {
    content.replaceChildren();
    const scopeLine = el("div", null, "institution-freshness");
    scopeLine.append(
      badge(L("آخر تحديث: ", "Retrieved: ") + new Date(data.generated_at).toLocaleString(preferences.lang)),
      el("span", L("دفعات: ", "Batches: ") + formatNumber(data.scope.batch_count)),
      el("span", L("بنود النتائج: ", "Result lines: ") + formatNumber(data.scope.total_result_lines)),
    );
    if (data.scope.truncated) scopeLine.append(badge(L("نطاق مختصر", "Scope truncated")));
    content.append(scopeLine);
    if (!data.scope.result_ids.length) {
      content.append(empty(L("لا توجد نتائج محفوظة", "No frozen results"), L("أنشئ ملفات نتائج من دفعة مستوردة بالكامل أولًا.", "Create result files from a fully imported batch first."), "reports"));
      const actions = el("div", null, "actions");
      actions.append(button(L("الانتقال إلى سجلات المطالبات", "Go to claims"), () => { location.href = "/institution/claims"; }, "button primary"));
      content.append(actions);
      return;
    }
    if (!data.summary.total_lines) {
      content.append(empty(L("لا بنود مطابقة", "No matching lines"), L("غيّر التصفية أو أعد الضبط لعرض النتائج.", "Change or reset the filters to view results."), "reports"));
    }
    const grid = el("div", null, "grid executive-kpis");
    grid.append(
      metricCard(L("البنود المراجعة", "Reviewed lines"), formatNumber(data.summary.total_lines), L("بنود فريدة في النطاق المحدد.", "Unique lines in the selected scope."), "reports", () => openLines()),
      metricCard(L("معدل المطابقة", "Match rate"), formatRate(data.summary.rates.matched), L("مطابق مبدئي: ", "Preliminary matches: ") + formatNumber(data.summary.counts.accepted), "claims", () => openLines({ outcome: "accepted" })),
      metricCard(L("معدل الرفض", "Rejection rate"), formatRate(data.summary.rates.unmatched), L("مرفوض مبدئي: ", "Preliminary rejected: ") + formatNumber(data.summary.counts.rejected), "audit", () => openLines({ outcome: "rejected" })),
      metricCard(L("غير محسوم", "Unverified"), formatRate(data.summary.rates.unverified), L("معلّق + مراجعة: ", "Held + review: ") + formatNumber(data.summary.unverified), "security", () => openLines({ outcome: "unverified" })),
      metricCard(L("المطالب به", "Claimed"), amountView(data, "claimed"), L("حسب العملة؛ لا تُجمع العملات.", "By currency; currencies are not combined."), "reports", () => openLines()),
      metricCard(L("القيمة الموسومة", "Flagged value"), amountView(data, "flagged"), L("ليست خسارة مؤكدة ولا اعتمادًا نهائيًا.", "Not a confirmed loss or final approval."), "audit", () => openLines({ outcome: "flagged" })),
      metricCard(L("الفرق المقترح", "Proposed difference"), amountView(data, "proposed_difference"), L("فرق البنود المرفوضة المبدئي، ليس نهائيًا.", "Preliminary rejected-line difference, not final."), "claims", () => openLines({ outcome: "rejected" })),
      metricCard(L("فرق السعر", "Price variance"), amountView(data, "price_variance"), data.summary.price_available_lines ? L("استقطاعات تعرفة عند وجود مقارنة؛ ليست وفورات.", "Tariff deductions where comparison exists; not savings.") : L("غير متاح — لا توجد مقارنة مرجعية كافية.", "Unavailable — insufficient frozen comparison."), "policy", () => openLines({ rule: "price" })),
      metricCard(L("تعرض التكرار", "Duplicate exposure"), amountView(data, "duplicate_exposure"), L("بنود مكررة فريدة؛ تعرض محتمل قد يتداخل.", "Unique duplicate lines; potential exposure that may overlap."), "ai", () => openLines({ rule: "duplicate" })),
      metricCard(L("نقص البيانات", "Missing data"), formatRate(data.summary.rates.missing_data), L("بنود ذات ملاحظة نقص: ", "Lines with missing-data notes: ") + formatNumber(data.summary.missing_data), "ingestion", () => openLines({ rule: "missing_data" })),
    );
    content.append(grid);

    const charts = el("div", null, "grid executive-panels");
    const outcomeSection = panel(L("توزيع النتائج", "Outcome distribution"), L("كل بند في فئة نتيجة واحدة؛ انقر لعرض البنود.", "Each line has one outcome bucket; select to view lines."), "reports");
    outcomeSection.append(outcomeChart(data.summary.counts, outcome => openLines({ outcome })));
    const reasonSection = panel(L("الملاحظات حسب النوع", "Notes by category"), L("قد تحمل البند عدة ملاحظات؛ العد لا يجمع المبالغ.", "A line may carry multiple notes; counts do not add monetary values."), "audit");
    reasonSection.append(reasonChart(data.reasons.categories, rule => openLines({ rule })));
    charts.append(outcomeSection, reasonSection);
    content.append(charts);

    content.append(currencyPanel(data));

    const tables = el("div", null, "grid executive-panels");
    tables.append(
      providerServiceTable(data, "providers", L("مقارنة مقدمي الخدمة", "Provider comparison"), L("أكثر المقدمين بعدد البنود، مع القيمة الموسومة وفرق السعر.", "Top providers by line count, with flagged value and price variance."), "hospitals", extra => openLines(extra)),
      providerServiceTable(data, "services", L("تحليل رموز الخدمة", "Service-code analysis"), L("أكثر رموز الخدمة بعدد البنود، مع القيمة الموسومة وفرق السعر.", "Top service codes by line count, with flagged value and price variance."), "laboratories", extra => openLines(extra)),
    );
    content.append(tables);

    const lower = el("div", null, "grid executive-panels");
    lower.append(qualityPanel(data), definitionsPanel());
    content.append(lower);

    if (data.batches.length > 1) {
      const batchPanel = panel(L("نطاق الدفعات", "Batch scope"), L("آخر مجموعة نتائج مجمّدة لكل دفعة ضمن النطاق.", "Latest frozen result set per batch in scope."), "claims");
      const columns = [
        { label: L("الدفعة", "Batch"), key: "name" },
        { label: L("البنود", "Lines"), render: row => formatNumber(row.total_lines) },
        { label: L("المستورد", "Imported"), render: row => formatNumber(row.imported_count) },
        { label: L("السجلات", "Records"), render: row => formatNumber(row.record_count) },
        { label: L("أُنشئت", "Created"), render: row => row.created_at ? new Date(row.created_at).toLocaleString(preferences.lang) : "—" },
      ];
      batchPanel.append(simpleTable(columns, data.batches));
      content.append(batchPanel);
    }
  }

  async function openLines(extra = {}) {
    const body = el("div");
    body.append(loading());
    openDrawer(L("بنود المؤشر", "Metric lines"), body);
    try {
      const params = queryFor(state, extra);
      const page = await ctx.api.request("/api/v1/analytics/lines?limit=200" + (params ? "&" + params : ""));
      body.replaceChildren();
      const summary = el("p", L("البنود المطابقة: ", "Matching lines: ") + formatNumber(page.total) + " · " + L("المعروض: ", "Displayed: ") + formatNumber(page.items.length), "muted");
      body.append(summary);
      if (!page.items.length) {
        body.append(empty(L("لا بنود مطابقة", "No matching lines"), L("غيّر المؤشر أو التصفية.", "Change the metric or filter."), "reports", true));
        return;
      }
      for (const row of page.items) {
        const title = (row.invoice_number || row.line_id) + " · " + outcomeLabel(row.bucket);
        const item = panel(title, (row.provider_code || "—") + " · " + (row.service_code || "—") + " · " + formatMoney(row.claimed_net, row.currency), "audit");
        const facts = el("dl", null, "detail-grid");
        const fact = (term, value) => {
          const box = el("div", null, "detail");
          box.append(el("dt", term), el("dd", value));
          facts.append(box);
        };
        fact(L("تاريخ الخدمة", "Service date"), row.service_date || "—");
        fact(L("الفرق المقترح", "Proposed difference"), row.proposed_difference === null ? "—" : formatMoney(row.proposed_difference, row.currency));
        fact(L("فرق السعر", "Price variance"), row.price_available ? formatMoney(row.price_variance, row.currency) : L("غير متاح", "Unavailable"));
        fact(L("تكرار", "Duplicate"), row.duplicate ? L("نعم", "Yes") : L("لا", "No"));
        fact(L("نقص بيانات", "Missing data"), row.missing ? L("نعم", "Yes") : L("لا", "No"));
        fact(L("تاريخ التدقيق", "Audit date"), row.audited_at ? new Date(row.audited_at).toLocaleString(preferences.lang) : "—");
        item.append(facts);
        if (row.reasons.length) {
          const list = el("ul", null, "line-reasons");
          for (const reason of row.reasons) {
            list.append(el("li", (reason.code || "") + (reason.message && reason.message !== reason.code ? " — " + reason.message : "")));
          }
          item.append(el("h3", L("الأسباب", "Reasons")), list);
        }
        body.append(item);
      }
      if (page.has_more) body.append(el("p", L("تُعرض أول 200 بند. استخدم التصفية لتضييق النطاق.", "Showing the first 200 lines. Narrow the filter for more detail."), "chart-note"));
    } catch (error) {
      body.replaceChildren(errorState(error, () => openLines(extra)));
    }
  }

  void load();
}
