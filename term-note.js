const termSearchForm = document.querySelector("#termSearchForm");
const termSearchStatus = document.querySelector("#termSearchStatus");
const termResult = document.querySelector("#termResult");
const termRows = document.querySelector("#termRows");
const termEmpty = document.querySelector("#termEmpty");
const termNoteSearch = document.querySelector("#termNoteSearch");
const termIndustryFilter = document.querySelector("#termIndustryFilter");
const termReview = document.querySelector("#termReview");
let savedTerms = [];
let currentTermResult = null;

const termIndustryLabels = { semiconductor: "반도체", battery: "배터리", common: "공통" };

function selectTermView() {
  document.querySelector('.sector-tab[data-view="term_note"]')?.click();
}

window.openTermSearchForArticle = item => {
  selectTermView();
  document.querySelector("#termArticleTitle").value = item.title || "";
  document.querySelector("#termArticleUrl").value = item.link || "";
  document.querySelector("#termArticleContext").value = [
    item.title, item.overview, item.industry_context,
    ...(item.key_points || [])
  ].filter(Boolean).join("\n");
  document.querySelector("#termQuery").focus();
  termSearchStatus.textContent = `‘${item.title || "선택한 기사"}’ 문맥을 반영합니다.`;
};

function showTermResult(term) {
  currentTermResult = term;
  termResult.hidden = false;
  document.querySelector("#termResultIndustry").textContent = termIndustryLabels[term.industry] || "공통";
  document.querySelector("#termResultTitle").textContent = term.title;
  document.querySelector("#termResultEnglish").textContent = term.english || "";
  document.querySelector("#termDefinition").textContent = term.definition;
  document.querySelector("#termPrinciple").textContent = term.principle;
  document.querySelector("#termIndustryMeaning").textContent = term.industry_meaning;
  document.querySelector("#termArticleMeaning").textContent = term.article_meaning;
  const related = document.querySelector("#termRelated");
  related.innerHTML = "";
  (term.related || []).forEach(value => {
    const chip = document.createElement("span");
    chip.textContent = value;
    related.appendChild(chip);
  });
}

termSearchForm.addEventListener("submit", async event => {
  event.preventDefault();
  const submit = termSearchForm.querySelector('button[type="submit"]');
  const query = document.querySelector("#termQuery").value.trim();
  if (!query) return;
  submit.disabled = true;
  termSearchStatus.textContent = "AI가 용어와 기사 문맥을 분석하고 있습니다…";
  try {
    const result = await authorizedPost({
      action: "term_explain",
      term: query,
      articleTitle: document.querySelector("#termArticleTitle").value,
      articleUrl: document.querySelector("#termArticleUrl").value,
      articleContext: document.querySelector("#termArticleContext").value,
    });
    showTermResult(result.term);
    termSearchStatus.textContent = "설명을 만들었습니다. 확인 후 용어 노트에 저장할 수 있습니다.";
  } catch (error) {
    termSearchStatus.textContent = error.message || "용어 설명을 만들지 못했습니다.";
  } finally {
    submit.disabled = false;
  }
});

async function refreshTerms() {
  const data = await fetch(`data/terms.json?t=${Date.now()}`, { cache: "no-store" })
    .then(response => response.ok ? response.json() : Promise.reject(new Error("용어 노트를 불러오지 못했습니다.")));
  savedTerms = Array.isArray(data.terms) ? data.terms : [];
  document.querySelector("#termCount").textContent = savedTerms.length;
  window.renderTermNotes();
}

document.querySelector("#saveTerm").addEventListener("click", async event => {
  if (!currentTermResult) return;
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "저장 중…";
  try {
    const result = await authorizedPost({ action: "term_save", term: currentTermResult });
    savedTerms = result.terms || savedTerms;
    document.querySelector("#termCount").textContent = savedTerms.length;
    window.renderTermNotes();
    button.textContent = "저장 완료";
  } catch (error) {
    alert(error.message || "용어를 저장하지 못했습니다.");
    button.textContent = "내 용어 노트에 저장";
  } finally {
    button.disabled = false;
  }
});

async function deleteTerm(id, title, button) {
  if (!confirm(`‘${title}’ 용어를 삭제할까요?`)) return;
  button.disabled = true;
  try {
    const result = await authorizedPost({ action: "term_delete", termId: id });
    savedTerms = result.terms || savedTerms.filter(term => term.id !== id);
    document.querySelector("#termCount").textContent = savedTerms.length;
    window.renderTermNotes();
  } catch (error) {
    button.disabled = false;
    alert(error.message || "용어를 삭제하지 못했습니다.");
  }
}

function editTerm(term, card) {
  card.open = true;
  const body = card.querySelector(".term-card-body");
  body.innerHTML = "";
  const form = document.createElement("form");
  form.className = "term-edit-form";
  const fields = [
    ["title", "용어명", "input"], ["english", "영문명", "input"],
    ["definition", "한 줄 정의", "textarea"], ["principle", "작동 원리", "textarea"],
    ["industry_meaning", "산업에서의 의미", "textarea"],
    ["article_meaning", "기사에서의 의미", "textarea"],
    ["related", "관련 용어(쉼표로 구분)", "input"]
  ];
  fields.forEach(([name, labelText, type]) => {
    const label = document.createElement("label");
    label.textContent = labelText;
    const control = document.createElement(type);
    control.name = name;
    control.value = name === "related" ? (term.related || []).join(", ") : (term[name] || "");
    if (type === "textarea") control.rows = name === "principle" ? 6 : 4;
    label.appendChild(control);
    form.appendChild(label);
  });
  const industryLabel = document.createElement("label");
  industryLabel.textContent = "산업 분류";
  const industry = document.createElement("select");
  industry.name = "industry";
  [["semiconductor", "반도체"], ["battery", "배터리"], ["common", "공통"]].forEach(([value, text]) => {
    const option = document.createElement("option");
    option.value = value; option.textContent = text; option.selected = term.industry === value;
    industry.appendChild(option);
  });
  industryLabel.appendChild(industry);
  form.insertBefore(industryLabel, form.children[2]);
  const buttons = document.createElement("div");
  buttons.className = "term-edit-actions";
  const save = document.createElement("button");
  save.type = "submit"; save.textContent = "수정 저장";
  const cancel = document.createElement("button");
  cancel.type = "button"; cancel.textContent = "취소";
  cancel.addEventListener("click", window.renderTermNotes);
  buttons.append(save, cancel); form.appendChild(buttons);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    save.disabled = true; save.textContent = "저장 중…";
    const data = new FormData(form);
    try {
      const result = await authorizedPost({ action: "term_save", term: {
        ...term,
        title: String(data.get("title") || "").trim(),
        english: String(data.get("english") || "").trim(),
        industry: String(data.get("industry") || "common"),
        definition: String(data.get("definition") || "").trim(),
        principle: String(data.get("principle") || "").trim(),
        industry_meaning: String(data.get("industry_meaning") || "").trim(),
        article_meaning: String(data.get("article_meaning") || "").trim(),
        related: String(data.get("related") || "").split(",").map(value => value.trim()).filter(Boolean),
      }});
      savedTerms = result.terms || savedTerms;
      window.renderTermNotes();
    } catch (error) {
      alert(error.message || "용어 수정에 실패했습니다.");
      save.disabled = false; save.textContent = "수정 저장";
    }
  });
  body.appendChild(form);
}

window.renderTermNotes = () => {
  if (!termRows) return;
  const query = termNoteSearch.value.trim().toLowerCase();
  const industry = termIndustryFilter.value;
  const visible = savedTerms.filter(term =>
    (industry === "all" || term.industry === industry) &&
    (!query || [term.title, term.english, term.definition, term.principle,
      term.industry_meaning, term.article_meaning, ...(term.related || [])]
      .join(" ").toLowerCase().includes(query))
  );
  termRows.innerHTML = "";
  visible.forEach(term => {
    const card = document.createElement("details");
    card.className = "term-card";
    const head = document.createElement("summary");
    head.className = "term-card-head";
    const titleWrap = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = term.title;
    const meta = document.createElement("small");
    meta.textContent = `${term.english || ""} · ${termIndustryLabels[term.industry] || "공통"}`;
    titleWrap.append(title, meta);
    const actions = document.createElement("div");
    actions.className = "term-card-actions";
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "edit-term";
    edit.textContent = "수정";
    edit.addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      editTerm(term, card);
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "delete-term";
    remove.textContent = "삭제";
    remove.addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      deleteTerm(term.id, term.title, remove);
    });
    actions.append(edit, remove);
    head.append(titleWrap, actions);
    const body = document.createElement("div");
    body.className = "term-card-body";
    const definitionHeading = document.createElement("h4");
    definitionHeading.textContent = "한 줄 정의";
    const definition = document.createElement("p");
    definition.textContent = term.definition;
    body.append(definitionHeading, definition);
    [
      ["작동 원리", term.principle], ["산업에서의 의미", term.industry_meaning],
      ["기사에서의 의미", term.article_meaning], ["관련 용어", (term.related || []).join(" · ")]
    ].forEach(([label, value]) => {
      const heading = document.createElement("h4"); heading.textContent = label;
      const paragraph = document.createElement("p"); paragraph.textContent = value || "-";
      body.append(heading, paragraph);
    });
    card.append(head, body);
    termRows.appendChild(card);
  });
  termEmpty.hidden = visible.length !== 0;
};

[termNoteSearch, termIndustryFilter].forEach(control => control.addEventListener("input", window.renderTermNotes));

document.querySelector("#reviewTerms").addEventListener("click", () => {
  if (!savedTerms.length) { alert("복습할 저장 용어가 없습니다."); return; }
  const term = savedTerms[Math.floor(Math.random() * savedTerms.length)];
  termReview.hidden = false;
  termReview.innerHTML = "";
  const title = document.createElement("h3"); title.textContent = `${term.title}를 설명해 보세요`;
  const answer = document.createElement("details");
  const summary = document.createElement("summary"); summary.textContent = "정답 확인";
  const text = document.createElement("p"); text.textContent = `${term.definition}\n\n${term.principle}`;
  answer.append(summary, text); termReview.append(title, answer);
});

document.querySelector("#exportTerms").addEventListener("click", () => {
  const exportData = savedTerms.map(term => ({
    title: term.title,
    english: term.english || "",
    industry: term.industry === "common" ? "공통" : term.industry === "battery" ? "배터리" : "반도체",
    category: term.category || "산업 용어",
    summary: term.definition || "",
    principle: term.principle || "",
    structure: term.structure || "",
    process: term.process || term.industry_meaning || "",
    defect: term.defect || "",
    job: term.job || "",
    related: term.related || [],
    source: term.source || "Process Brief AI 용어 노트"
  }));
  const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `process-brief-terms-${new Date().toISOString().slice(0, 10)}.json`;
  link.click();
  URL.revokeObjectURL(link.href);
});

refreshTerms().catch(error => { termEmpty.textContent = error.message; });
