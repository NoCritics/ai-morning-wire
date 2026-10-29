(function () {
  "use strict";

  const TYPE_LABELS = {
    "model-release": "Model release",
    "quantization-local": "Quantization / local",
    "tooling": "Tooling",
    "research": "Research",
    "product-api": "Product / API",
    "benchmark-eval": "Benchmark / eval",
    "industry": "Industry",
  };
  const PR_NOTE_LABELS = {
    "context": "From the source",
    "careful": "Careful",
    "background": "Background, not from the source",
  };
  const READ_KEY = "ainews.read";
  const LOCAL_BOOKMARKS_KEY = "ainews.bookmarks";
  const THEME_KEY = "ainews.theme";

  const newsData = JSON.parse(document.getElementById("news-data").textContent);

  const ARCHIVE_TAB = "archive";

  const state = {
    activeDay: null,
    activeType: null,
    query: "",
    unreadOnly: false,
    prCheckOnly: false,
    openPrChecks: {},
    showBookmarks: false,
    readIds: loadStored(READ_KEY, {}),
    localBookmarks: loadStored(LOCAL_BOOKMARKS_KEY, {}),
    syncedBookmarks: {},
    database: null,
    focusedId: null,
  };
  let statusTimer = null;
  let searchTimer = null;

  function loadStored(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      if (raw === null) return fallback;
      return JSON.parse(raw);
    } catch (error) {
      return fallback;
    }
  }

  function saveStored(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (error) {
      // Storage can be blocked (private windows, previews); the page keeps working without it.
    }
  }

  function indexById(items) {
    const byId = {};
    for (const item of items) {
      byId[item.id] = item;
    }
    return byId;
  }

  function element(tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function localIsoDate(dateValue) {
    const month = String(dateValue.getMonth() + 1).padStart(2, "0");
    const day = String(dateValue.getDate()).padStart(2, "0");
    return dateValue.getFullYear() + "-" + month + "-" + day;
  }

  function shortDate(isoDate) {
    return new Date(isoDate + "T12:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
  }

  function dayLabel(isoDate) {
    const now = new Date();
    if (isoDate === localIsoDate(now)) return "Today";
    if (isoDate === localIsoDate(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1))) return "Yesterday";
    return new Date(isoDate + "T12:00:00").toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  }

  function tabLabel(isoDate) {
    const label = dayLabel(isoDate);
    if (label === "Today" || label === "Yesterday") return label;
    return new Date(isoDate + "T12:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric" });
  }

  function sourceHost(url) {
    try {
      return new URL(url).hostname.replace(/^www\./, "");
    } catch (error) {
      return "";
    }
  }

  function searchableText(item) {
    const parts = [item.title, TYPE_LABELS[item.type] || ""];
    for (const bullet of item.bullets) {
      parts.push(bullet);
    }
    for (const source of item.sources) {
      parts.push(source.label);
    }
    if (item.pr_check) {
      parts.push("pr check", item.pr_check.why, item.pr_check.holds_up, item.pr_check.pitch || "");
      for (const note of item.pr_check.notes) {
        parts.push(note.text);
      }
    }
    return parts.join(" ").toLowerCase();
  }

  function isRead(itemId) {
    return state.readIds[itemId] === true;
  }

  function isBookmarked(itemId) {
    if (state.syncedBookmarks[itemId]) return true;
    return state.database === null && Boolean(state.localBookmarks[itemId]);
  }

  function itemMatches(item) {
    if (state.activeType !== null && item.type !== state.activeType) return false;
    if (state.unreadOnly && isRead(item.id)) return false;
    if (state.prCheckOnly && !item.pr_check) return false;
    if (state.query !== "" && !searchableText(item).includes(state.query)) return false;
    return true;
  }

  function showStatus(message) {
    const status = document.getElementById("status");
    status.textContent = message;
    window.clearTimeout(statusTimer);
    statusTimer = window.setTimeout(function () {
      status.textContent = "";
    }, 6000);
  }

  function bookmarkGlyph() {
    const svgNamespace = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNamespace, "svg");
    svg.setAttribute("viewBox", "0 0 12 16");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", "bookmark-glyph");
    const path = document.createElementNS(svgNamespace, "path");
    path.setAttribute("d", "M1.5 1.5h9v13l-4.5-3.5-4.5 3.5z");
    svg.appendChild(path);
    return svg;
  }

  function setRead(itemId, read) {
    if (read) {
      state.readIds[itemId] = true;
    } else {
      delete state.readIds[itemId];
    }
    saveStored(READ_KEY, state.readIds);
    for (const article of document.querySelectorAll('.item[data-id="' + itemId + '"]')) {
      article.classList.toggle("is-read", read);
      article.querySelector(".read-button").textContent = read ? "Mark unread" : "Mark read";
    }
  }

  function bookmarkCopy(item) {
    const copy = {};
    for (const key of Object.keys(item)) {
      copy[key] = item[key];
    }
    copy.bookmarked_at = new Date().toISOString();
    return copy;
  }

  async function toggleBookmark(item) {
    if (state.database !== null) {
      const documentReference = state.database.collection("bookmarks").doc(item.id);
      try {
        if (isBookmarked(item.id)) {
          await documentReference.delete();
        } else {
          await documentReference.set(bookmarkCopy(item));
        }
      } catch (error) {
        showStatus("Couldn't update the bookmark. Check your connection and try again.");
      }
      return;
    }
    if (state.localBookmarks[item.id]) {
      delete state.localBookmarks[item.id];
    } else if (state.syncedBookmarks[item.id]) {
      showStatus("This bookmark was saved on the claude.ai page. Remove it there.");
      return;
    } else {
      state.localBookmarks[item.id] = bookmarkCopy(item);
    }
    saveStored(LOCAL_BOOKMARKS_KEY, state.localBookmarks);
    render();
  }

  function bookmarkedItems() {
    const byId = {};
    for (const itemId of Object.keys(state.syncedBookmarks)) {
      byId[itemId] = state.syncedBookmarks[itemId];
    }
    if (state.database === null) {
      for (const itemId of Object.keys(state.localBookmarks)) {
        byId[itemId] = state.localBookmarks[itemId];
      }
    }
    const items = Object.values(byId);
    items.sort(function (first, second) {
      return String(second.bookmarked_at).localeCompare(String(first.bookmarked_at));
    });
    return items;
  }

  function renderItem(item) {
    const article = element("article", "item");
    article.dataset.id = item.id;
    article.tabIndex = -1;
    if (isRead(item.id)) article.classList.add("is-read");
    if (item.id === state.focusedId) article.classList.add("is-focused");

    const titleRow = element("div", "item-title-row");
    const unreadDot = element("span", "unread-dot");
    unreadDot.setAttribute("aria-hidden", "true");
    titleRow.appendChild(unreadDot);
    titleRow.appendChild(element("h3", "item-title", item.title));
    article.appendChild(titleRow);

    const meta = element("p", "meta");
    meta.appendChild(element("span", "", shortDate(item.published)));
    meta.appendChild(element("span", "tag", TYPE_LABELS[item.type] || item.type));
    if (item.pr_check) meta.appendChild(element("span", "tag tag-pr", "PR check"));
    const hosts = [];
    for (const source of item.sources) {
      const host = sourceHost(source.url);
      if (host !== "" && !hosts.includes(host)) hosts.push(host);
    }
    meta.appendChild(element("span", "", hosts.join(" · ")));
    article.appendChild(meta);

    const bulletList = element("ul", "bullets");
    for (const bullet of item.bullets) {
      bulletList.appendChild(element("li", "", bullet));
    }
    article.appendChild(bulletList);
    if (item.pr_check) article.appendChild(renderPrCheck(item));

    const actions = element("div", "item-actions");
    const sourceLinks = element("p", "source-links");
    for (const source of item.sources) {
      const link = element("a", "source-link", source.label);
      link.href = source.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.addEventListener("click", function () {
        setRead(item.id, true);
      });
      link.addEventListener("auxclick", function () {
        setRead(item.id, true);
      });
      sourceLinks.appendChild(link);
    }
    actions.appendChild(sourceLinks);

    const readButton = element("button", "item-button read-button", isRead(item.id) ? "Mark unread" : "Mark read");
    readButton.type = "button";
    readButton.addEventListener("click", function () {
      setRead(item.id, !isRead(item.id));
    });
    actions.appendChild(readButton);

    const bookmarkButton = element("button", "item-button bookmark-button");
    bookmarkButton.type = "button";
    bookmarkButton.setAttribute("aria-pressed", String(isBookmarked(item.id)));
    bookmarkButton.appendChild(bookmarkGlyph());
    bookmarkButton.appendChild(element("span", "", isBookmarked(item.id) ? "Saved" : "Save"));
    bookmarkButton.addEventListener("click", function () {
      toggleBookmark(item);
    });
    actions.appendChild(bookmarkButton);
    article.appendChild(actions);

    article.addEventListener("click", function (event) {
      if (event.target.closest("a, button")) return;
      focusItem(item.id, false);
    });
    return article;
  }

  function renderPrCheck(item) {
    const prCheck = item.pr_check;
    const block = element("details", "pr-check");
    // Re-renders (filters, bookmark sync) rebuild every item; keep the blocks the reader opened open.
    block.open = Boolean(state.openPrChecks[item.id]);
    block.addEventListener("toggle", function () {
      state.openPrChecks[item.id] = block.open;
    });
    block.appendChild(element("summary", "pr-check-summary", "Without the PR"));
    const body = element("div", "pr-check-body");
    block.appendChild(body);
    const why = element("p", "pr-why");
    why.appendChild(element("span", "pr-label", "Why checked"));
    why.appendChild(document.createTextNode(prCheck.why));
    body.appendChild(why);
    const holdsUp = element("p", "pr-holds-up");
    holdsUp.appendChild(element("span", "pr-label", "What holds up"));
    holdsUp.appendChild(document.createTextNode(prCheck.holds_up));
    body.appendChild(holdsUp);
    const notes = element("ul", "pr-notes");
    for (const kind of Object.keys(PR_NOTE_LABELS)) {
      for (const note of prCheck.notes) {
        if (note.kind !== kind) continue;
        const noteItem = element("li", "pr-note pr-note-" + note.kind);
        noteItem.appendChild(element("span", "pr-label", PR_NOTE_LABELS[note.kind]));
        noteItem.appendChild(document.createTextNode(note.text));
        notes.appendChild(noteItem);
      }
    }
    body.appendChild(notes);
    if (prCheck.pitch) {
      const pitch = element("p", "pr-pitch");
      pitch.appendChild(element("span", "pr-label", "The pitch"));
      pitch.appendChild(element("q", "", prCheck.pitch));
      body.appendChild(pitch);
    }
    return block;
  }

  function renderDay(day, container) {
    const visibleItems = [];
    for (const item of day.items) {
      if (itemMatches(item)) visibleItems.push(item);
    }
    if (visibleItems.length === 0) return 0;
    const section = element("section", "day");
    const header = element("header", "day-header");
    const label = dayLabel(day.date);
    const title = element("h2", "day-title");
    if (label === "Today") {
      const marker = element("span", "today-marker");
      marker.setAttribute("aria-hidden", "true");
      title.appendChild(marker);
    }
    title.appendChild(document.createTextNode(label));
    header.appendChild(title);
    let countText = visibleItems.length + (visibleItems.length === 1 ? " story" : " stories");
    if (label === "Today" || label === "Yesterday") countText = shortDate(day.date) + " · " + countText;
    header.appendChild(element("p", "day-count", countText));
    section.appendChild(header);
    for (const item of visibleItems) {
      section.appendChild(renderItem(item));
    }
    container.appendChild(section);
    return visibleItems.length;
  }

  function renderBookmarks(container) {
    const allBookmarks = bookmarkedItems();
    const visibleItems = [];
    for (const item of allBookmarks) {
      if (itemMatches(item)) visibleItems.push(item);
    }
    const section = element("section", "day");
    const header = element("header", "day-header");
    header.appendChild(element("h2", "day-title", "Bookmarks"));
    header.appendChild(element("p", "day-count", visibleItems.length + " saved"));
    section.appendChild(header);
    if (allBookmarks.length === 0) {
      section.appendChild(element("p", "empty", "No bookmarks yet. Press Save on any story to keep it here for good."));
    } else if (visibleItems.length === 0) {
      section.appendChild(element("p", "empty", "No bookmarks match the current search or filter."));
    }
    for (const item of visibleItems) {
      section.appendChild(renderItem(item));
    }
    container.appendChild(section);
  }

  function clearFilters() {
    state.activeType = null;
    state.query = "";
    state.unreadOnly = false;
    state.prCheckOnly = false;
    document.getElementById("search").value = "";
    render();
  }

  function staleNotice(built, digestTime, now) {
    if (digestTime === null) return null;
    if (built >= localIsoDate(now)) return null;
    const nowTime = String(now.getHours()).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0");
    if (nowTime <= digestTime) return null;
    return "Today's digest hasn't arrived; this one is from " + shortDate(built) + ".";
  }

  function newestDayDate() {
    const days = newsData.feed.concat(newsData.archive);
    if (days.length === 0) return null;
    let newest = days[0].date;
    for (const day of days) {
      if (day.date > newest) newest = day.date;
    }
    return newest;
  }

  function runStatusText(now) {
    const notice = staleNotice(newsData.built, newsData.digestTime, now);
    if (notice !== null) return notice;
    // The recorded status is about the day the page was built; it only describes today when the viewer's today is that day.
    if (newsData.built !== localIsoDate(now)) return null;
    for (const day of newsData.feed) {
      if (day.date === newsData.built) return null;
    }
    const status = newsData.status;
    if (status.result === "failed") {
      let text = "Today's digest failed: " + String(status.failedReason).replace(/\.$/, "") + ".";
      const newest = newestDayDate();
      if (newest !== null) text += " The last digest is from " + shortDate(newest) + ".";
      return text;
    }
    if (status.result === "published" && status.items === 0) return "No new stories today.";
    return null;
  }

  function renderStatus() {
    const line = document.getElementById("run-status");
    const text = runStatusText(new Date());
    line.hidden = text === null;
    line.textContent = text === null ? "" : text;
  }

  function renderEmptyFeed(container) {
    if (newsData.feed.length + newsData.archive.length === 0) {
      let text = "No stories yet.";
      if (newsData.digestTime !== null) text += " The next digest arrives at " + newsData.digestTime + ".";
      container.appendChild(element("p", "empty", text));
      return;
    }
    const message = element("p", "empty", "No stories match. ");
    const clearButton = element("button", "text-button", "Clear search and filters");
    clearButton.type = "button";
    clearButton.addEventListener("click", clearFilters);
    message.appendChild(clearButton);
    container.appendChild(message);
  }

  function updateControls() {
    for (const tab of document.querySelectorAll("#day-tabs .day-tab")) {
      const tabDay = tab.dataset.day === "" ? null : tab.dataset.day;
      tab.setAttribute("aria-pressed", String(!state.showBookmarks && tabDay === state.activeDay));
    }
    for (const chip of document.querySelectorAll("#type-chips .chip")) {
      const chipType = chip.dataset.type === "" ? null : chip.dataset.type;
      chip.setAttribute("aria-pressed", String(chipType === state.activeType));
    }
    document.getElementById("unread-toggle").setAttribute("aria-pressed", String(state.unreadOnly));
    document.getElementById("pr-toggle").setAttribute("aria-pressed", String(state.prCheckOnly));
    document.getElementById("bookmarks-toggle").setAttribute("aria-pressed", String(state.showBookmarks));
    document.getElementById("bookmark-count").textContent = String(bookmarkedItems().length);
  }

  function render() {
    updateControls();
    const feedContainer = document.getElementById("feed");
    const archiveSection = document.getElementById("archive-section");
    const archiveDays = document.getElementById("archive-days");
    const bookmarksView = document.getElementById("bookmarks-view");
    feedContainer.replaceChildren();
    archiveDays.replaceChildren();
    bookmarksView.replaceChildren();
    if (state.showBookmarks) {
      feedContainer.hidden = true;
      archiveSection.hidden = true;
      bookmarksView.hidden = false;
      renderBookmarks(bookmarksView);
      return;
    }
    feedContainer.hidden = false;
    bookmarksView.hidden = true;
    if (state.activeDay === ARCHIVE_TAB) {
      archiveSection.hidden = true;
      let shownCount = 0;
      for (const day of newsData.archive) {
        shownCount += renderDay(day, feedContainer);
      }
      if (newsData.archive.length === 0) {
        feedContainer.appendChild(element("p", "empty", "Nothing archived yet. Stories move here 7 days after they arrive and stay for 30 days."));
      } else if (shownCount === 0) {
        renderEmptyFeed(feedContainer);
      }
      return;
    }
    if (state.activeDay !== null) {
      archiveSection.hidden = true;
      let shownCount = 0;
      for (const day of newsData.feed) {
        if (day.date === state.activeDay) shownCount += renderDay(day, feedContainer);
      }
      if (shownCount === 0) renderEmptyFeed(feedContainer);
      return;
    }
    let feedCount = 0;
    for (const day of newsData.feed) {
      feedCount += renderDay(day, feedContainer);
    }
    let archiveCount = 0;
    for (const day of newsData.archive) {
      archiveCount += renderDay(day, archiveDays);
    }
    archiveSection.hidden = archiveCount === 0;
    document.getElementById("archive-count").textContent = archiveCount + (archiveCount === 1 ? " story" : " stories");
    if (state.query !== "" && archiveCount > 0) document.getElementById("archive").open = true;
    if (feedCount + archiveCount === 0) renderEmptyFeed(feedContainer);
  }

  function makeChip(label, type) {
    const chip = element("button", "chip", label);
    chip.type = "button";
    chip.dataset.type = type === null ? "" : type;
    chip.addEventListener("click", function () {
      if (type === null || state.activeType === type) {
        state.activeType = null;
      } else {
        state.activeType = type;
      }
      render();
    });
    return chip;
  }

  function makeDayTab(label, day, count) {
    const tab = element("button", "day-tab", label);
    tab.type = "button";
    tab.dataset.day = day === null ? "" : day;
    if (count !== null) tab.appendChild(element("span", "day-tab-count", String(count)));
    tab.addEventListener("click", function () {
      state.activeDay = day;
      state.showBookmarks = false;
      state.focusedId = null;
      render();
      tab.scrollIntoView({ block: "nearest", inline: "nearest" });
      window.scrollTo({ top: 0 });
    });
    return tab;
  }

  function buildDayTabs() {
    const tabRow = document.getElementById("day-tabs");
    tabRow.appendChild(makeDayTab("All days", null, null));
    for (const day of newsData.feed) {
      tabRow.appendChild(makeDayTab(tabLabel(day.date), day.date, null));
    }
    let archivedCount = 0;
    for (const day of newsData.archive) {
      archivedCount += day.items.length;
    }
    tabRow.appendChild(makeDayTab("Archive", ARCHIVE_TAB, archivedCount));
  }

  function updateFadeEdges(row) {
    const hiddenStart = row.scrollLeft > 1;
    const hiddenEnd = row.scrollLeft + row.clientWidth < row.scrollWidth - 1;
    row.classList.toggle("fade-start", hiddenStart);
    row.classList.toggle("fade-end", hiddenEnd);
  }

  function watchFadeEdges() {
    const rows = document.querySelectorAll(".scroll-row");
    for (const row of rows) {
      row.addEventListener("scroll", function () {
        updateFadeEdges(row);
      }, { passive: true });
      updateFadeEdges(row);
    }
    window.addEventListener("resize", function () {
      for (const row of rows) {
        updateFadeEdges(row);
      }
    });
  }

  function visibleItemElements() {
    const visible = [];
    for (const article of document.querySelectorAll(".item")) {
      if (article.offsetParent !== null) visible.push(article);
    }
    return visible;
  }

  function focusItem(itemId, scroll) {
    state.focusedId = itemId;
    for (const article of document.querySelectorAll(".item.is-focused")) {
      article.classList.remove("is-focused");
    }
    const target = document.querySelector('.item[data-id="' + itemId + '"]');
    if (!target) return;
    target.classList.add("is-focused");
    if (scroll) {
      target.focus({ preventScroll: true });
      const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      target.scrollIntoView({ block: "nearest", behavior: reduceMotion ? "auto" : "smooth" });
    }
  }

  function moveFocus(step) {
    const items = visibleItemElements();
    if (items.length === 0) return;
    let currentIndex = -1;
    for (let position = 0; position < items.length; position += 1) {
      if (items[position].dataset.id === state.focusedId) currentIndex = position;
    }
    let nextIndex;
    if (currentIndex === -1) {
      nextIndex = step > 0 ? 0 : items.length - 1;
    } else {
      nextIndex = Math.min(items.length - 1, Math.max(0, currentIndex + step));
    }
    focusItem(items[nextIndex].dataset.id, true);
  }

  function findItem(itemId) {
    for (const day of newsData.feed.concat(newsData.archive)) {
      for (const item of day.items) {
        if (item.id === itemId) return item;
      }
    }
    for (const item of bookmarkedItems()) {
      if (item.id === itemId) return item;
    }
    return null;
  }

  function handleKey(event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const target = event.target;
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) {
      if (event.key === "Escape") target.blur();
      return;
    }
    if (event.key === "j") {
      moveFocus(1);
    } else if (event.key === "k") {
      moveFocus(-1);
    } else if (event.key === "o") {
      const link = document.querySelector(".item.is-focused .source-link");
      if (link) link.click();
    } else if (event.key === "b") {
      if (document.querySelector(".item.is-focused")) {
        const item = findItem(state.focusedId);
        if (item) toggleBookmark(item);
      }
    } else if (event.key === "/") {
      event.preventDefault();
      document.getElementById("search").focus();
    }
  }

  function loadTheme() {
    try {
      return localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light";
    } catch (error) {
      return "light";
    }
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    const button = document.getElementById("theme-toggle");
    button.textContent = theme === "dark" ? "Light mode" : "Dark mode";
    button.setAttribute("aria-pressed", String(theme === "dark"));
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch (error) {
      // The toggle still works for this visit.
    }
  }

  async function syncLocalBookmarks() {
    let failed = false;
    for (const itemId of Object.keys(state.localBookmarks)) {
      if (state.syncedBookmarks[itemId]) continue;
      try {
        await state.database.collection("bookmarks").doc(itemId).set(state.localBookmarks[itemId]);
        delete state.localBookmarks[itemId];
      } catch (error) {
        failed = true;
      }
    }
    saveStored(LOCAL_BOOKMARKS_KEY, state.localBookmarks);
    if (failed) showStatus("Couldn't move some saved bookmarks to claude.ai. They stay in this browser.");
  }

  function connectDatabase() {
    if (!window.claude || typeof window.claude.use !== "function") return;
    window.claude.use("db").then(function (database) {
      if (!database) return;
      state.database = database;
      let movedLocalBookmarks = false;
      database.collection("bookmarks").onSnapshot(
        function (snapshot) {
          const synced = {};
          for (const documentSnapshot of snapshot.docs) {
            synced[documentSnapshot.id] = documentSnapshot.data();
          }
          state.syncedBookmarks = synced;
          // Only after the first snapshot is known which local bookmarks the database already holds.
          if (!movedLocalBookmarks) {
            movedLocalBookmarks = true;
            syncLocalBookmarks();
          }
          render();
        },
        function () {
          showStatus("Bookmarks stopped syncing. Reload the page to reconnect.");
        }
      );
      render();
    }).catch(function () {
      // No database in this view; bookmarks stay in this browser.
    });
  }

  function init() {
    state.syncedBookmarks = indexById(newsData.bookmarks);
    applyTheme(loadTheme());
    document.getElementById("theme-toggle").addEventListener("click", function () {
      applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");
    });
    document.getElementById("masthead-meta").textContent = "Updated " + shortDate(newsData.built) + " · summaries from the original sources";
    buildDayTabs();
    renderStatus();
    const chipRow = document.getElementById("type-chips");
    chipRow.appendChild(makeChip("All", null));
    for (const type of Object.keys(TYPE_LABELS)) {
      chipRow.appendChild(makeChip(TYPE_LABELS[type], type));
    }
    document.getElementById("search").addEventListener("input", function (event) {
      state.query = event.target.value.trim().toLowerCase();
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(render, 120);
    });
    document.getElementById("unread-toggle").addEventListener("click", function () {
      state.unreadOnly = !state.unreadOnly;
      render();
    });
    document.getElementById("pr-toggle").addEventListener("click", function () {
      state.prCheckOnly = !state.prCheckOnly;
      render();
    });
    document.getElementById("bookmarks-toggle").addEventListener("click", function () {
      state.showBookmarks = !state.showBookmarks;
      render();
    });
    document.addEventListener("keydown", handleKey);
    render();
    watchFadeEdges();
    connectDatabase();
  }

  function fetchJson(path) {
    return fetch(path, { cache: "no-store" }).then(function (response) {
      if (!response.ok) throw new Error(path + ": " + response.status);
      return response.json();
    }).catch(function () {
      return null;
    });
  }

  function loadSideFiles() {
    newsData.feed = [];
    newsData.archive = [];
    newsData.bookmarks = [];
    document.getElementById("status").textContent = "Loading the digest…";
    const allDates = newsData.feedDates.concat(newsData.archiveDates);
    const requests = [];
    for (const date of allDates) {
      requests.push(fetchJson("days/" + date + ".json"));
    }
    requests.push(fetchJson("bookmarks.json"));
    Promise.all(requests).then(function (results) {
      let failedDays = 0;
      for (let index = 0; index < allDates.length; index++) {
        if (results[index] === null) failedDays++;
        else if (index < newsData.feedDates.length) newsData.feed.push(results[index]);
        else newsData.archive.push(results[index]);
      }
      const bookmarks = results[allDates.length];
      const bookmarksFailed = !Array.isArray(bookmarks);
      if (!bookmarksFailed) newsData.bookmarks = bookmarks;
      const failedParts = [];
      if (failedDays > 0) failedParts.push(failedDays + " day(s)");
      if (bookmarksFailed) failedParts.push("the bookmarks");
      document.getElementById("status").textContent = "";
      init();
      if (failedParts.length > 0) {
        document.getElementById("status").textContent = "Couldn't load " + failedParts.join(" and ") + ". Reload the page to try again.";
      }
    });
  }

  if (newsData.feedDates) {
    loadSideFiles();
  } else {
    init();
  }
})();
