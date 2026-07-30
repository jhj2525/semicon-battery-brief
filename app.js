const rows = document.querySelector("#newsRows");
const empty = document.querySelector("#empty");
const updated = document.querySelector("#updated");
const searchInput = document.querySelector("#searchInput");
const regionFilter = document.querySelector("#regionFilter");
const categoryFilter = document.querySelector("#categoryFilter");
const template = document.querySelector("#rowTemplate");
const tabs = [...document.querySelectorAll(".sector-tab")];

let items = [];
let sector = "semiconductor";

const labels = {
  domestic: "국내",
  global: "해외",
};

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
  fragment.querySelector(".overview").textContent = item.overview;
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

function render() {
  const query = searchInput.value.trim().toLowerCase();
  const region = regionFilter.value;
  const category = categoryFilter.value;
  const visible = items.filter(item =>
    item.verified_source === true &&
    item.sector === sector &&
    (region === "all" || item.region === region) &&
    (category === "all" || item.category === category) &&
    (!query || searchable(item).includes(query))
  );

  rows.innerHTML = "";
  visible.forEach(item => rows.appendChild(buildRow(item)));
  empty.hidden = visible.length !== 0;
}

tabs.forEach(tab => tab.addEventListener("click", () => {
  tabs.forEach(item => item.classList.remove("active"));
  tab.classList.add("active");
  sector = tab.dataset.sector;
  render();
}));
[searchInput, regionFilter, categoryFilter].forEach(control =>
  control.addEventListener("input", render)
);

fetch("data/news.json", { cache: "no-store" })
  .then(response => response.json())
  .then(data => {
    items = data.items || [];
    updated.textContent = `최근 업데이트 ${data.updated_at || "-"}`;
    document.querySelector("#semiCount").textContent =
      items.filter(x => x.sector === "semiconductor" && x.verified_source).length;
    document.querySelector("#batteryCount").textContent =
      items.filter(x => x.sector === "battery" && x.verified_source).length;
    render();
  })
  .catch(() => {
    updated.textContent = "데이터 로드 실패";
    empty.hidden = false;
  });

