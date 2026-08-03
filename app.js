const rows = document.querySelector("#newsRows");
const empty = document.querySelector("#empty");
const updated = document.querySelector("#updated");
const searchInput = document.querySelector("#searchInput");
const regionFilter = document.querySelector("#regionFilter");
const categoryFilter = document.querySelector("#categoryFilter");
const archiveSector = document.querySelector("#archiveSector");
const archiveDate = document.querySelector("#archiveDate");
const archiveControls = document.querySelector("#archiveControls");
const viewNote = document.querySelector("#viewNote");
const template = document.querySelector("#rowTemplate");
const tabs = [...document.querySelectorAll(".sector-tab")];
const database = document.querySelector(".database");
const controls = document.querySelector(".controls");
const manualPanel = document.querySelector("#manualPanel");
const manualForm = document.querySelector("#manualForm");
const manualStatus = document.querySelector("#manualStatus");
const manualNewsRows = document.querySelector("#manualNewsRows");
const manualEmpty = document.querySelector("#manualEmpty");

let currentItems = [];
let archiveItems = [];
let semiItems = [];
let manualItems = [];
let view = "semiconductor";

const labels = { domestic: "국내", global: "해외" };

function searchable(item) {
  return [
    item.title, item.source, item.category, item.overview,
    ...(item.keywords || []), ...(item.key_points || [])
  ].join(" ").toLowerCase();
}

function fillList(element, values) {
  element.innerHTML = "";
  (values || []).forEach(value => {
    const li = document.createElement("li");
    li.textContent = value;
    element.appendChild(li);
  });
}

function buildRow(item) {
  const fragment = template.content.cloneNode(true);
  const article = fragment.querySelector(".news-row");
  const button = fragment.querySelector(".row-summary");
  const detail = fragment.querySelector(".row-detail");

  fragment.querySelector(".published").textContent = (item.published || "").slice(0, 10);
  fragment.querySelector(".region").textContent = labels[item.region] || item.region;
  fragment.querySelector(".region").classList.add(item.region);
  fragment.querySelector(".category").textContent = item.category;
  fragment.querySelector(".headline").textContent = item.title;
  fragment.querySelector(".keywords").textContent = (item.keywords || []).slice(0, 3).join(" · ");
  fragment.querySelector(".source").textContent = item.source;
  fragment.querySelector(".source-type").textContent =
    item.source_type || (item.official_source ? "공식 자료" : "언론 보도");
  fragment.querySelector(".overview").textContent = item.overview || "요약 정보가 없습니다.";
  fragment.querySelector(".industry-context").textContent = item.industry_context || "";
  fragment.querySelector(".industry-context-wrap").hidden = !item.industry_context;
  fillList(fragment.querySelector(".key-points"), item.key_points);
  fillList(fragment.querySelector(".numbers"), item.numbers);
  fragment.querySelector(".numbers-wrap").hidden = !(item.numbers || []).length;
  fragment.querySelector(".outlook").textContent = item.stated_outlook || "";
  fragment.querySelector(".outlook-wrap").hidden = !item.stated_outlook;
  fragment.querySelector(".collected").textContent = item.collected || "-";
  fragment.querySelector(".published-full").textContent = item.published || "-";
  fragment.querySelector(".source-full").textContent = item.source || "-";
  const originalSourceRow = fragment.querySelector(".original-source-row");
  if (item.original_source) {
    fragment.querySelector(".original-source").textContent = item.original_source;
    originalSourceRow.hidden = false;
  }
  fragment.querySelector(".original-link").href = item.link;
  const semiPostLink = fragment.querySelector(".semi-post-link");
  if (item.semi_post_link) {
    semiPostLink.href = item.semi_post_link;
    semiPostLink.hidden = false;
  }

  button.addEventListener("click", () => {
    const opening = detail.hidden;
    detail.hidden = !opening;
    button.setAttribute("aria-expanded", String(opening));
    article.classList.toggle("open", opening);
  });
  return fragment;
}

function activeItems() {
  if (view === "archive") {
    return archiveItems.filter(item =>
      (archiveSector.value === "all" || item.sector === archiveSector.value) &&
      (!archiveDate.value || (item.published || "").slice(0, 10) === archiveDate.value)
    );
  }
  if (view === "semi_market") return semiItems;
  return [
    ...manualItems.filter(item => item.sector === view),
    ...currentItems.filter(item => item.sector === view),
  ];
}

function render() {
  if (view === "manual_add") return;
  const query = searchInput.value.trim().toLowerCase();
  const region = regionFilter.value;
  const category = categoryFilter.value;
  const visible = activeItems().filter(item =>
    (item.verified_source === true || item.summary_status === "link_only") &&
    (region === "all" || item.region === region) &&
    (category === "all" || item.category === category) &&
    (!query || searchable(item).includes(query))
  );

  rows.innerHTML = "";
  visible.forEach(item => rows.appendChild(buildRow(item)));
  empty.hidden = visible.length !== 0;
  empty.textContent = view === "archive"
    ? "조건에 맞는 아카이브 기사가 없습니다."
    : view === "semi_market"
      ? "SEMI 블로그 원문 링크를 확인한 기사가 아직 없습니다."
      : "최근 3일 안에 조건을 통과한 기사가 없습니다.";
}

function renderManual() {
  manualNewsRows.innerHTML = "";
  const ordered = [...manualItems].sort((a, b) =>
    `${b.collected || ""}${b.published || ""}`.localeCompare(`${a.collected || ""}${a.published || ""}`)
  );
  ordered.forEach(item => manualNewsRows.appendChild(buildRow(item)));
  manualEmpty.hidden = ordered.length !== 0;
}

tabs.forEach(tab => tab.addEventListener("click", () => {
  tabs.forEach(item => item.classList.remove("active"));
  tab.classList.add("active");
  view = tab.dataset.view;
  const manualMode = view === "manual_add";
  manualPanel.hidden = !manualMode;
  database.hidden = manualMode;
  controls.hidden = manualMode;
  viewNote.hidden = manualMode;
  archiveControls.hidden = view !== "archive";
  viewNote.textContent = view === "archive"
    ? "과거 검증 기사 · 날짜와 분야로 검색"
    : view === "semi_market"
      ? "SEMI Korea 블로그 · 반도체업계 뉴스의 최신 게시일 글 전체"
      : "오늘 기사 우선 · 부족하면 최대 3일 이내 기사로 구성";
  if (manualMode) renderManual();
  render();
}));

manualForm.addEventListener("submit", async event => {
  event.preventDefault();
  const endpoint = (window.PROCESS_BRIEF_CONFIG || {}).workerUrl || "";
  if (!endpoint || endpoint.includes("YOUR-WORKER")) {
    manualStatus.textContent = "관리자 설정이 아직 완료되지 않았습니다. config.js에 Worker 주소를 등록해 주세요.";
    return;
  }
  const submit = manualForm.querySelector("button[type='submit']");
  const urls = document.querySelector("#manualUrl").value
    .split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  if (urls.length < 1) {
    manualStatus.textContent = "기사 URL을 한 줄에 하나씩 입력해 주세요.";
    return;
  }
  submit.disabled = true;
  manualStatus.textContent = "추가 요청을 보내는 중입니다…";
  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        urls,
        sector: document.querySelector("#manualSector").value,
        password: document.querySelector("#manualPassword").value,
      }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "추가 요청에 실패했습니다.");
    manualStatus.textContent = "요청 완료. 실제 반영을 확인하는 중입니다…";
    const expected = new Set(urls.map(value => value.replace(/\/$/, "")));
    let reflected = false;
    let linkOnlyCount = 0;
    for (let attempt = 0; attempt < 18; attempt += 1) {
      await new Promise(resolve => setTimeout(resolve, 10000));
      const news = await fetch(`data/news.json?t=${Date.now()}`, { cache: "no-store" })
        .then(response => response.json()).catch(() => null);
      if (!news) continue;
      const current = news.manual_items || [];
      const links = new Set(current.flatMap(item => [item.link, item.requested_link])
        .filter(Boolean).map(value => value.replace(/\/$/, "")));
      if ([...expected].every(url => links.has(url))) {
        reflected = true;
        manualItems = current;
        renderManual();
        linkOnlyCount = current.filter(item =>
          (expected.has((item.link || "").replace(/\/$/, ""))
            || expected.has((item.requested_link || "").replace(/\/$/, "")))
          && item.summary_status === "link_only"
        ).length;
        break;
      }
    }
    manualStatus.textContent = reflected
      ? linkOnlyCount
        ? `추가 완료. ${linkOnlyCount}개는 원문 접근 제한으로 제목과 링크만 표시됩니다.`
        : "추가 및 요약 완료. 페이지를 새로고침하면 새 기사가 표시됩니다."
      : "요청은 접수됐지만 아직 반영되지 않았습니다. 잠시 후 새로고침해 주세요.";
    manualForm.reset();
  } catch (error) {
    manualStatus.textContent = error.message || "추가 요청에 실패했습니다.";
  } finally {
    submit.disabled = false;
  }
});

[searchInput, regionFilter, categoryFilter, archiveSector, archiveDate].forEach(control =>
  control.addEventListener("input", render)
);

fetch("data/news.json", { cache: "no-store" })
  .then(response => response.json())
  .then(data => {
    if (Array.isArray(data.current_items)) {
      currentItems = data.current_items;
      semiItems = data.semi_items || [];
      manualItems = data.manual_items || [];
      archiveItems = [
        ...(data.archive_items || []),
        ...(data.semi_archive_items || [])
      ];
    } else {
      // Legacy fallback before the first v10 run: show only fixed-size current
      // lists and place every remaining verified article in the archive.
      const legacy = (data.items || []).filter(x => x.verified_source === true);
      const sorted = [...legacy].sort((a, b) => (b.published || "").localeCompare(a.published || ""));
      currentItems = [
        ...sorted.filter(x => x.sector === "semiconductor").slice(0, 5),
        ...sorted.filter(x => x.sector === "battery").slice(0, 5)
      ];
      semiItems = (data.market_items || [])
        .filter(x => x.verified_source === true || x.summary_status === "link_only");
      const currentIds = new Set(currentItems.map(x => x.id));
      archiveItems = sorted.filter(x => !currentIds.has(x.id));
    }

    updated.textContent = `최근 업데이트 ${data.updated_at || "-"}`;
    document.querySelector("#semiCount").textContent =
      currentItems.filter(x => x.sector === "semiconductor").length
      + manualItems.filter(x => x.sector === "semiconductor").length;
    document.querySelector("#batteryCount").textContent =
      currentItems.filter(x => x.sector === "battery").length
      + manualItems.filter(x => x.sector === "battery").length;
    document.querySelector("#marketCount").textContent = semiItems.length;
    document.querySelector("#archiveCount").textContent = archiveItems.length;
    document.querySelector("#manualCount").textContent = manualItems.length;
    renderManual();
    render();
  })
  .catch(() => {
    updated.textContent = "데이터 로드 실패";
    empty.hidden = false;
  });
