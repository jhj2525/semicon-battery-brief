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
const semiPanel = document.querySelector("#semiPanel");
const semiManualRows = document.querySelector("#semiManualRows");
const semiManualEmpty = document.querySelector("#semiManualEmpty");
const manualArchivePanel = document.querySelector("#manualArchivePanel");
const manualArchiveRows = document.querySelector("#manualArchiveRows");
const manualArchiveEmpty = document.querySelector("#manualArchiveEmpty");
const manualArchiveSearch = document.querySelector("#manualArchiveSearch");
const manualArchiveSector = document.querySelector("#manualArchiveSector");
const manualArchiveDate = document.querySelector("#manualArchiveDate");
const favoritePanel = document.querySelector("#favoritePanel");
const favoriteRows = document.querySelector("#favoriteRows");
const favoriteEmpty = document.querySelector("#favoriteEmpty");
const favoriteSearch = document.querySelector("#favoriteSearch");
const favoriteSector = document.querySelector("#favoriteSector");
const favoriteDate = document.querySelector("#favoriteDate");
const deleteSelectedAuto = document.querySelector("#deleteSelectedAuto");
const deleteSelectedManual = document.querySelector("#deleteSelectedManual");
const deleteSelectedCurrent = document.querySelector("#deleteSelectedCurrent");
const deleteSelectedSemi = document.querySelector("#deleteSelectedSemi");

let currentItems = [];
let archiveItems = [];
let semiItems = [];
let manualItems = [];
let favoriteItems = [];
let favoriteIds = new Set();
const selectedDeleteIds = new Set();
const pendingDeleteKey = "processBriefPendingDeletes";
const adminTokenKey = "processBriefAdminToken";
let pendingDeletes = (() => {
  try { return JSON.parse(localStorage.getItem(pendingDeleteKey) || "{}"); }
  catch { return {}; }
})();

function savePendingDeletes() {
  localStorage.setItem(pendingDeleteKey, JSON.stringify(pendingDeletes));
}

function workerEndpoint() {
  const endpoint = (window.PROCESS_BRIEF_CONFIG || {}).workerUrl || "";
  if (!endpoint || endpoint.includes("YOUR-WORKER")) {
    throw new Error("Worker 주소가 설정되지 않았습니다.");
  }
  return endpoint;
}

async function loginForAdmin(endpoint) {
  const password = prompt("관리 비밀번호를 입력해 주세요.");
  if (!password) throw new Error("관리 인증이 취소되었습니다.");
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "login", password }),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || !result.token) {
    throw new Error(result.error || "관리 인증에 실패했습니다.");
  }
  sessionStorage.setItem(adminTokenKey, result.token);
  return result.token;
}

async function authorizedPost(payload) {
  const endpoint = workerEndpoint();
  let token = sessionStorage.getItem(adminTokenKey) || "";
  for (let attempt = 0; attempt < 2; attempt += 1) {
    if (!token) token = await loginForAdmin(endpoint);
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${token}`,
      },
      body: JSON.stringify(payload),
    });
    const result = await response.json().catch(() => ({}));
    if (response.status === 401 && attempt === 0) {
      sessionStorage.removeItem(adminTokenKey);
      token = "";
      continue;
    }
    if (!response.ok) throw new Error(result.error || "관리 요청에 실패했습니다.");
    return result;
  }
  throw new Error("관리 인증에 실패했습니다.");
}

function hidePendingDeletedItems(ids) {
  const hidden = new Set(ids);
  currentItems = currentItems.filter(item => !hidden.has(item.id));
  archiveItems = archiveItems.filter(item => !hidden.has(item.id));
  manualItems = manualItems.filter(item => !hidden.has(item.id));
  favoriteItems = favoriteItems.filter(item => !hidden.has(item.id));
  hidden.forEach(id => favoriteIds.delete(id));
  hidden.forEach(id => selectedDeleteIds.delete(id));
  render();
  renderManualArchive();
  renderSemiManual();
  renderFavorites();
}
let view = "semiconductor";
let dataToday = "";

const labels = { domestic: "국내", global: "해외" };
const sectorLabels = { semiconductor: "반도체", battery: "배터리", semi_market: "SEMI" };

function koreaToday() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit"
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

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

function manualIsActive(item) {
  return typeof item.manual_active === "boolean"
    ? item.manual_active
    : (item.collected || "") === dataToday;
}

async function requestDeleteItems(items, button) {
  if (!items.length) {
    alert("삭제할 기사를 먼저 선택해 주세요.");
    return;
  }
  const message = items.length === 1
    ? `이 기사를 삭제할까요?\n\n${items[0].title}`
    : `선택한 기사 ${items.length}개를 삭제할까요?`;
  if (!confirm(message)) return;
  button.disabled = true;
  button.textContent = "삭제 요청 중…";
  try {
    await authorizedPost({ action: "delete", itemIds: items.map(item => item.id) });
    const deletingIds = new Set(items.map(item => item.id));
    deletingIds.forEach(id => { pendingDeletes[id] = Date.now(); });
    savePendingDeletes();
    hidePendingDeletedItems(deletingIds);
    alert(`${items.length}개 기사를 화면에서 삭제했습니다. 영구 저장은 뒤에서 처리되며 보통 1~3분 걸립니다.`);
    let reflected = false;
    for (let attempt = 0; attempt < 18; attempt += 1) {
      await new Promise(resolve => setTimeout(resolve, 10000));
      const news = await fetch(`data/news.json?t=${Date.now()}`, { cache: "no-store" })
        .then(result => result.json()).catch(() => null);
      if (!news) continue;
      const stored = [
        ...(news.current_items || []), ...(news.archive_items || []),
        ...(news.manual_items || []), ...(news.semi_archive_items || []),
        ...(news.favorite_items || [])
      ];
      if (stored.every(item => !deletingIds.has(item.id))) {
        reflected = true;
        currentItems = news.current_items || [];
        archiveItems = [...(news.archive_items || []), ...(news.semi_archive_items || [])];
        manualItems = news.manual_items || [];
        favoriteItems = news.favorite_items || [];
        favoriteIds = new Set(favoriteItems.map(item => item.id));
        deletingIds.forEach(id => {
          selectedDeleteIds.delete(id);
          delete pendingDeletes[id];
        });
        savePendingDeletes();
        render();
        renderManualArchive();
        renderSemiManual();
        renderFavorites();
        break;
      }
    }
    if (!reflected) console.warn("삭제 영구 저장 확인이 지연되고 있습니다.");
    button.disabled = false;
    button.textContent = button.classList.contains("batch-delete") ? "선택 삭제" : "이 기사 삭제";
  } catch (error) {
    button.disabled = false;
    button.textContent = button.classList.contains("batch-delete") ? "선택 삭제" : "이 기사 삭제";
    alert(error.message || "삭제 요청에 실패했습니다.");
  }
}

function requestDelete(item, button) {
  return requestDeleteItems([item], button);
}

async function requestTitleEdit(item, button) {
  const title = prompt("수정할 기사 제목을 입력해 주세요.", item.title || "");
  if (!title || title.trim() === (item.title || "").trim()) return;
  button.disabled = true;
  button.textContent = "수정 요청 중…";
  try {
    await authorizedPost({ action: "edit_title", itemId: item.id, title: title.trim() });
    button.textContent = "수정 요청 완료";
    alert("제목 수정 요청을 접수했습니다. GitHub 실행이 끝난 뒤 새로고침하면 반영됩니다.");
  } catch (error) {
    button.disabled = false;
    button.textContent = "제목 수정";
    alert(error.message || "제목 수정 요청에 실패했습니다.");
  }
}

async function requestFavorite(item, button) {
  const adding = !favoriteIds.has(item.id);
  button.disabled = true;
  try {
    await authorizedPost({ action: "favorite", itemId: item.id, favorite: adding });
    button.textContent = adding ? "★ 추가 요청 완료" : "☆ 해제 요청 완료";
    alert(`즐겨찾기 ${adding ? "추가" : "해제"} 요청을 접수했습니다. GitHub 실행 후 반영됩니다.`);
  } catch (error) {
    button.disabled = false;
    alert(error.message || "즐겨찾기 요청에 실패했습니다.");
  }
}

function buildRow(item, { deletable = false, editable = false } = {}) {
  const fragment = template.content.cloneNode(true);
  const article = fragment.querySelector(".news-row");
  const button = fragment.querySelector(".row-summary");
  const detail = fragment.querySelector(".row-detail");
  const linkOnly = item.summary_status === "link_only";
  article.classList.toggle("link-only", linkOnly);

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
  const deleteButton = fragment.querySelector(".delete-item");
  if (deletable && item.id) {
    article.classList.add("deletable");
    const selector = fragment.querySelector(".batch-select");
    const checkbox = selector.querySelector("input");
    selector.hidden = false;
    checkbox.checked = selectedDeleteIds.has(item.id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) selectedDeleteIds.add(item.id);
      else selectedDeleteIds.delete(item.id);
    });
    deleteButton.hidden = false;
    deleteButton.addEventListener("click", () => requestDelete(item, deleteButton));
  }
  const editButton = fragment.querySelector(".edit-title");
  if (editable && item.id) {
    editButton.hidden = false;
    editButton.addEventListener("click", () => requestTitleEdit(item, editButton));
  }
  const favoriteButton = fragment.querySelector(".favorite-item");
  if (item.id) {
    const starred = favoriteIds.has(item.id);
    favoriteButton.textContent = starred ? "★ 즐겨찾기 해제" : "☆ 즐겨찾기";
    favoriteButton.classList.toggle("starred", starred);
    favoriteButton.addEventListener("click", () => requestFavorite(item, favoriteButton));
  } else {
    favoriteButton.hidden = true;
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
  if (view === "auto_archive") {
    return archiveItems.filter(item =>
      (archiveSector.value === "all" || item.sector === archiveSector.value) &&
      (!archiveDate.value || (item.published || "").slice(0, 10) === archiveDate.value)
    );
  }
  return [
    ...manualItems.filter(item => item.sector === view && manualIsActive(item)),
    ...currentItems.filter(item => item.sector === view),
  ];
}

function render() {
  if (["manual_add", "manual_archive", "semi_market", "favorites"].includes(view)) return;
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
  visible.forEach(item => rows.appendChild(buildRow(item, { deletable: true, editable: item.manual_added === true })));
  empty.hidden = visible.length !== 0;
  empty.textContent = view === "auto_archive"
    ? "조건에 맞는 아카이브 기사가 없습니다."
    : "최근 수집 범위 안에 조건을 통과한 기사가 없습니다.";
}

function renderSemiManual() {
  semiManualRows.innerHTML = "";
  const ordered = manualItems.filter(item =>
    item.sector === "semi_market" && manualIsActive(item)
  ).sort((a, b) =>
    `${b.collected || ""}${b.published || ""}`.localeCompare(`${a.collected || ""}${a.published || ""}`)
  );
  ordered.forEach(item => semiManualRows.appendChild(buildRow(item, { deletable: true, editable: true })));
  semiManualEmpty.hidden = ordered.length !== 0;
}

function renderManualArchive() {
  manualArchiveRows.innerHTML = "";
  const query = manualArchiveSearch.value.trim().toLowerCase();
  const sector = manualArchiveSector.value;
  const date = manualArchiveDate.value;
  const ordered = manualItems.filter(item =>
    !manualIsActive(item) &&
    (sector === "all" || item.sector === sector) &&
    (!date || (item.collected || "") === date) &&
    (!query || searchable(item).includes(query))
  ).sort((a, b) =>
    `${b.collected || ""}${b.published || ""}`.localeCompare(`${a.collected || ""}${a.published || ""}`)
  );
  ordered.forEach(item => manualArchiveRows.appendChild(buildRow(item, { deletable: true, editable: true })));
  manualArchiveEmpty.hidden = ordered.length !== 0;
}

function renderFavorites() {
  favoriteRows.innerHTML = "";
  const query = favoriteSearch.value.trim().toLowerCase();
  const sector = favoriteSector.value;
  const date = favoriteDate.value;
  const ordered = favoriteItems.filter(item =>
    (sector === "all" || item.sector === sector) &&
    (!date || (item.published || "").slice(0, 10) === date) &&
    (!query || searchable(item).includes(query))
  ).sort((a, b) => `${b.published || ""}`.localeCompare(`${a.published || ""}`));
  ordered.forEach(item => favoriteRows.appendChild(buildRow(item)));
  favoriteEmpty.hidden = ordered.length !== 0;
}

function selectedItemsFor(source) {
  return source.filter(item => selectedDeleteIds.has(item.id));
}

tabs.forEach(tab => tab.addEventListener("click", () => {
  tabs.forEach(item => item.classList.remove("active"));
  tab.classList.add("active");
  view = tab.dataset.view;
  const manualMode = view === "manual_add";
  const manualArchiveMode = view === "manual_archive";
  const semiMode = view === "semi_market";
  const favoriteMode = view === "favorites";
  manualPanel.hidden = !manualMode;
  manualArchivePanel.hidden = !manualArchiveMode;
  semiPanel.hidden = !semiMode;
  favoritePanel.hidden = !favoriteMode;
  database.hidden = manualMode || manualArchiveMode || semiMode || favoriteMode;
  controls.hidden = manualMode || manualArchiveMode || semiMode || favoriteMode;
  viewNote.hidden = manualMode || manualArchiveMode || semiMode || favoriteMode;
  archiveControls.hidden = view !== "auto_archive";
  deleteSelectedCurrent.hidden = view === "auto_archive";
  viewNote.textContent = view === "auto_archive"
    ? "과거 검증 기사 · 날짜와 분야로 검색"
    : "자동 뉴스 분야별 최대 5개 · 생성 후 24시간 고정";
  if (semiMode) renderSemiManual();
  if (manualArchiveMode) renderManualArchive();
  if (favoriteMode) renderFavorites();
  render();
}));

manualForm.addEventListener("submit", async event => {
  event.preventDefault();
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
    await authorizedPost({
      action: "add",
      urls,
      sector: document.querySelector("#manualSector").value,
    });
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
        renderSemiManual();
        renderManualArchive();
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
[manualArchiveSearch, manualArchiveSector, manualArchiveDate].forEach(control =>
  control.addEventListener("input", renderManualArchive)
);
[favoriteSearch, favoriteSector, favoriteDate].forEach(control =>
  control.addEventListener("input", renderFavorites)
);
deleteSelectedAuto.addEventListener("click", () =>
  requestDeleteItems(selectedItemsFor(archiveItems), deleteSelectedAuto)
);
deleteSelectedManual.addEventListener("click", () =>
  requestDeleteItems(
    selectedItemsFor(manualItems.filter(item => !manualIsActive(item))),
    deleteSelectedManual
  )
);
deleteSelectedCurrent.addEventListener("click", () =>
  requestDeleteItems(selectedItemsFor(activeItems()), deleteSelectedCurrent)
);
deleteSelectedSemi.addEventListener("click", () =>
  requestDeleteItems(
    selectedItemsFor(manualItems.filter(item =>
      item.sector === "semi_market" && manualIsActive(item)
    )),
    deleteSelectedSemi
  )
);

fetch("data/news.json", { cache: "no-store" })
  .then(response => response.json())
  .then(data => {
    dataToday = koreaToday();
    if (Array.isArray(data.current_items)) {
      currentItems = data.current_items;
      semiItems = data.semi_items || [];
      manualItems = data.manual_items || [];
      favoriteItems = data.favorite_items || [];
      favoriteIds = new Set(favoriteItems.map(item => item.id));
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

    const allStoredIds = new Set([
      ...currentItems, ...archiveItems, ...manualItems
    ].map(item => item.id));
    const serverDeleted = new Set(data.deleted_article_ids || []);
    const nowMs = Date.now();
    Object.entries(pendingDeletes).forEach(([id, requestedAt]) => {
      if (serverDeleted.has(id) || !allStoredIds.has(id) || nowMs - requestedAt > 30 * 60 * 1000) {
        delete pendingDeletes[id];
      }
    });
    savePendingDeletes();
    const pendingIds = new Set(Object.keys(pendingDeletes));
    currentItems = currentItems.filter(item => !pendingIds.has(item.id));
    archiveItems = archiveItems.filter(item => !pendingIds.has(item.id));
    manualItems = manualItems.filter(item => !pendingIds.has(item.id));
    favoriteItems = favoriteItems.filter(item => !pendingIds.has(item.id));
    favoriteIds = new Set(favoriteItems.map(item => item.id));

    updated.textContent = `자동 묶음 생성 ${data.automatic_cycle_started_at || data.last_automatic_update_at || "-"}`;
    document.querySelector("#semiCount").textContent =
      currentItems.filter(x => x.sector === "semiconductor").length
      + manualItems.filter(x => x.sector === "semiconductor" && manualIsActive(x)).length;
    document.querySelector("#batteryCount").textContent =
      currentItems.filter(x => x.sector === "battery").length
      + manualItems.filter(x => x.sector === "battery" && manualIsActive(x)).length;
    document.querySelector("#marketCount").textContent =
      manualItems.filter(x => x.sector === "semi_market" && manualIsActive(x)).length;
    document.querySelector("#archiveCount").textContent = archiveItems.length;
    document.querySelector("#manualArchiveCount").textContent =
      manualItems.filter(x => !manualIsActive(x)).length;
    document.querySelector("#favoriteCount").textContent = favoriteItems.length;
    renderSemiManual();
    renderManualArchive();
    renderFavorites();
    render();
  })
  .catch(() => {
    updated.textContent = "데이터 로드 실패";
    empty.hidden = false;
  });
