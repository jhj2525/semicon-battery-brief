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

let currentItems = [];
let archiveItems = [];
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
  fragment.querySelector(".overview").textContent = item.overview || "요약 정보가 없습니다.";
  fillList(fragment.querySelector(".key-points"), item.key_points);
  fillList(fragment.querySelector(".numbers"), item.numbers);
  fragment.querySelector(".numbers-wrap").hidden = !(item.numbers || []).length;
  fragment.querySelector(".outlook").textContent = item.stated_outlook || "";
  fragment.querySelector(".outlook-wrap").hidden = !item.stated_outlook;
  fragment.querySelector(".collected").textContent = item.collected || "-";
  fragment.querySelector(".published-full").textContent = item.published || "-";
  fragment.querySelector(".source-full").textContent = item.source || "-";
  fragment.querySelector(".original-link").href = item.link;

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
  return currentItems.filter(item => item.sector === view);
}

function render() {
  const query = searchInput.value.trim().toLowerCase();
  const region = regionFilter.value;
  const category = categoryFilter.value;
  const visible = activeItems().filter(item =>
    item.verified_source === true &&
    (region === "all" || item.region === region) &&
    (category === "all" || item.category === category) &&
    (!query || searchable(item).includes(query))
  );

  rows.innerHTML = "";
  visible.forEach(item => rows.appendChild(buildRow(item)));
  empty.hidden = visible.length !== 0;
  empty.textContent = view === "archive"
    ? "조건에 맞는 아카이브 기사가 없습니다."
    : "최근 3일 안에 조건을 통과한 기사가 없습니다.";
}

tabs.forEach(tab => tab.addEventListener("click", () => {
  tabs.forEach(item => item.classList.remove("active"));
  tab.classList.add("active");
  view = tab.dataset.view;
  archiveControls.hidden = view !== "archive";
  viewNote.textContent = view === "archive"
    ? "과거 검증 기사 · 날짜와 분야로 검색"
    : "오늘 기사 우선 · 부족하면 최대 3일 이내 기사로 구성";
  render();
}));

[searchInput, regionFilter, categoryFilter, archiveSector, archiveDate].forEach(control =>
  control.addEventListener("input", render)
);

fetch("data/news.json", { cache: "no-store" })
  .then(response => response.json())
  .then(data => {
    if (Array.isArray(data.current_items)) {
      currentItems = data.current_items;
      archiveItems = data.archive_items || [];
    } else {
      // Legacy fallback before the first v10 run: show only fixed-size current
      // lists and place every remaining verified article in the archive.
      const legacy = (data.items || []).filter(x => x.verified_source === true);
      const sorted = [...legacy].sort((a, b) => (b.published || "").localeCompare(a.published || ""));
      currentItems = [
        ...sorted.filter(x => x.sector === "semiconductor").slice(0, 6),
        ...sorted.filter(x => x.sector === "battery").slice(0, 8)
      ];
      const currentIds = new Set(currentItems.map(x => x.id));
      archiveItems = sorted.filter(x => !currentIds.has(x.id));
    }

    updated.textContent = `최근 업데이트 ${data.updated_at || "-"}`;
    document.querySelector("#semiCount").textContent =
      currentItems.filter(x => x.sector === "semiconductor").length;
    document.querySelector("#batteryCount").textContent =
      currentItems.filter(x => x.sector === "battery").length;
    document.querySelector("#archiveCount").textContent = archiveItems.length;
    render();
  })
  .catch(() => {
    updated.textContent = "데이터 로드 실패";
    empty.hidden = false;
  });
