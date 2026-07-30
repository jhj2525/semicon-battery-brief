const grid = document.querySelector("#newsGrid");
const empty = document.querySelector("#empty");
const count = document.querySelector("#resultCount");
const updated = document.querySelector("#updated");
const regionFilter = document.querySelector("#regionFilter");
const tabs = [...document.querySelectorAll(".tab")];

let items = [];
let sector = "all";

function label(value) {
  return {
    semiconductor: "반도체", battery: "배터리",
    domestic: "국내", global: "해외"
  }[value] || value;
}

function render() {
  const region = regionFilter.value;
  const visible = items.filter(item =>
    (sector === "all" || item.sector === sector) &&
    (region === "all" || item.region === region)
  );
  count.textContent = `${visible.length}개 기사`;
  empty.hidden = visible.length !== 0;
  grid.innerHTML = visible.map(item => `
    <article class="news-card">
      <div class="badges">
        <span class="badge ${item.sector}">${label(item.sector)}</span>
        <span class="badge">${label(item.region)}</span>
        <span class="badge">${item.category}</span>
      </div>
      <h2>${item.title}</h2>
      <div class="meta">${item.source} · ${item.published}</div>
      <p class="summary">${item.summary}</p>
      <p class="insight"><strong>공정기술 관점</strong><br>${item.insight}</p>
      <a class="source-link" href="${item.link}" target="_blank" rel="noopener noreferrer">원문 보기 →</a>
    </article>
  `).join("");
}

tabs.forEach(tab => tab.addEventListener("click", () => {
  tabs.forEach(item => item.classList.remove("active"));
  tab.classList.add("active");
  sector = tab.dataset.sector;
  render();
}));
regionFilter.addEventListener("change", render);

fetch("data/news.json", { cache: "no-store" })
  .then(response => response.json())
  .then(data => {
    items = data.items || [];
    updated.textContent = `최근 업데이트 ${data.updated_at || "-"}`;
    render();
  })
  .catch(() => {
    updated.textContent = "데이터를 불러오지 못했습니다";
    empty.hidden = false;
  });

