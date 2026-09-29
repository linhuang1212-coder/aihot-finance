"use strict";

const CHANNEL = "finance";
const VIEW_TITLES = {
  selected: "精选",
  all: "全量",
  breaking: "突发快讯",
  daily: "金融日报",
  category: "分类",
  search: "搜索",
  analysis: "深度分析",
  source: "信源",
  stock: "个股",
  mainline: "主线",
};
const VIEW_KICKERS = {
  selected: "Selected · 实时盯盘",
  all: "All · 全量信息流",
  breaking: "Breaking · 突发快讯",
  daily: "Daily · 金融日报",
  analysis: "Analysis · 深度研判",
  stock: "Equities · 个股动态",
  mainline: "Mainline · 算力景气链",
};
const CAT_LABELS = {
  macro: "宏观 · 政策", commodity: "大宗 · 能源", equity: "股指 · 汇率",
  sector: "行业 · 公司", geo: "地缘 · 风险", ashare: "A股 · 公告",
};

const state = {
  view: "selected",
  category: null,
  market: "",
  q: "",
  hideUnverified: false,
  dailyDate: null,
  source: null,
};

let renderedIds = new Set();
const PAGE_SIZE = 100;
let loadedCount = 0;          // 当前信息流已加载条数(用于「加载更多」offset)
const FEED_VIEWS = new Set(["selected", "all", "breaking", "category", "search", "source", "stock", "mainline"]);

// ---------- helpers ----------
function $(sel, root) { return (root || document).querySelector(sel); }
function $all(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

function beijingTime(iso) {
  // published_at carries +08:00; format as Beijing time
  const d = new Date(iso);
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff >= 0 && diff < 3600) return Math.floor(diff / 60) + " 分钟前";
  if (diff >= 0 && diff < 86400) return Math.floor(diff / 3600) + " 小时前";
  // absolute Beijing time
  const parts = new Intl.DateTimeFormat("zh-CN", {
    month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
    hour12: false, timeZone: "Asia/Shanghai",
  }).format(d);
  return parts;
}

function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}

function esc(s) {
  return (s || "").replace(/[&<>"]/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]
  ));
}

// 累计扩散 sparkline 的 polyline points 串。pts=[[relMin,cum],...]；归一化到 w×h。纯函数。
function sparkPath(pts, w, h) {
  if (!pts || !pts.length) return "";
  const yMax = Math.max(...pts.map(p => p[1]), 1);
  const xMax = Math.max(...pts.map(p => p[0]), 1);
  const toY = v => (h - (v / yMax) * h).toFixed(1);
  if (pts.length === 1) return "0," + toY(pts[0][1]) + " " + w + "," + toY(pts[0][1]);
  return pts.map(p => ((p[0] / xMax) * w).toFixed(1) + "," + toY(p[1])).join(" ");
}

function fmtHM(iso) {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Shanghai",
  }).format(new Date(iso));
}

// 累计阶梯曲线(step-after)。points=[{ts,source,cum}]。返回 SVG 字符串。纯函数。
function propChartSvg(points) {
  const W = 520, H = 200, PAD = 28;
  if (!points.length) return "";
  const n = points[points.length - 1].cum;
  const t0 = new Date(points[0].ts).getTime();
  const t1 = new Date(points[points.length - 1].ts).getTime();
  const span = Math.max(1, t1 - t0);
  const x = t => PAD + ((t - t0) / span) * (W - 2 * PAD);
  const y = c => H - PAD - (c / Math.max(1, n)) * (H - 2 * PAD);
  let d = `M ${x(t0).toFixed(1)} ${y(0).toFixed(1)}`;
  points.forEach(p => {
    const px = x(new Date(p.ts).getTime()).toFixed(1);
    d += ` L ${px} ${y(p.cum - 1).toFixed(1)} L ${px} ${y(p.cum).toFixed(1)}`;
  });
  d += ` L ${x(t1).toFixed(1)} ${y(n).toFixed(1)}`;
  return `<svg viewBox="0 0 ${W} ${H}" class="prop-chart">`
    + `<path d="${d}" fill="none" stroke-width="2"/>`
    + `<text x="${PAD}" y="${H - 6}" class="ax">${fmtHM(points[0].ts)}</text>`
    + `<text x="${W - PAD}" y="${H - 6}" class="ax" text-anchor="end">${fmtHM(points[points.length - 1].ts)}</text>`
    + `<text x="6" y="${(y(n) + 4).toFixed(1)}" class="ax">${n}</text>`
    + `</svg>`;
}

function openPropModal(it) {
  const existing = document.getElementById("prop-modal");
  if (existing) existing.remove();
  const mask = el("div", "prop-mask");
  mask.id = "prop-modal";
  mask.innerHTML = `<div class="prop-panel"><button class="prop-close">×</button>`
    + `<div class="prop-title">${esc(it.title_zh)}</div>`
    + `<div class="prop-body">加载中…</div></div>`;
  document.body.appendChild(mask);
  const close = () => { mask.remove(); document.removeEventListener("keydown", onEsc); };
  const onEsc = e => { if (e.key === "Escape") close(); };
  mask.addEventListener("click", e => { if (e.target === mask) close(); });
  mask.querySelector(".prop-close").addEventListener("click", close);
  document.addEventListener("keydown", onEsc);

  fetch("api/public/propagation?group=" + encodeURIComponent(it.dedup_group || ""))
    .then(r => r.json())
    .then(d => {
      const body = mask.querySelector(".prop-body");
      if (!d.points || !d.points.length) { body.textContent = "暂无传播数据"; return; }
      const recentK = d.points.filter(p =>
        (Date.now() - new Date(p.ts).getTime()) <= 90 * 60000).length;
      const ferment = it.propagation ? "仍在发酵" : "已降温";
      const cap = `首报 ${esc(d.points[0].source)} ${fmtHM(d.first_ts)}`
        + ` · 现累计 ${d.n} 家 · 近90分 +${recentK} 家（${ferment}）`;
      const timeline = d.points.map((p, i) =>
        `<div class="prop-step${i === d.points.length - 1 ? " last" : ""}">`
        + `${fmtHM(p.ts)}　${esc(p.source)}</div>`).join("");
      body.innerHTML = propChartSvg(d.points)
        + `<div class="prop-cap">${cap}</div>`
        + `<div class="prop-timeline">${timeline}</div>`;
    })
    .catch(() => { mask.querySelector(".prop-body").textContent = "加载失败。"; });
}

// ---------- rendering ----------
// 方案 A「精密终端」卡片：左侧时间/信源导轨 + 右侧标题正文。
function cardNode(it) {
  const unv = it.verified === "unverified";
  const card = el("div", "card" + (unv ? " unverified" : ""));

  // 左侧导轨：时间 + 信源
  const rail = el("div", "card-rail");
  rail.appendChild(el("div", "t", esc(beijingTime(it.published_at))));
  rail.appendChild(el("div", "s", esc(it.source)));
  // 信号列：利多/利空(方向+强度+标的) + 热度 —— 填满左导轨、一眼扫读
  const sent = it.sentiment;
  if (sent && sent.dir) {
    const dcls = sent.dir === "利多" ? "up" : sent.dir === "利空" ? "down" : "flat";
    const chip = el("div", "rail-sent rail-sent-" + dcls);
    const n = Math.max(0, Math.min(3, sent.str || 0));
    chip.innerHTML = `<span class="rs-dir">${esc(sent.dir)}</span>`
      + (n ? `<span class="rs-str">${"●".repeat(n)}</span>` : "")
      + (sent.tgt ? `<span class="rs-tgt">${esc(sent.tgt)}</span>` : "");
    rail.appendChild(chip);
  }
  if (it.heat) rail.appendChild(el("div", "rail-heat", esc(it.heat)));
  card.appendChild(rail);

  const main = el("div", "card-main");

  // 眉栏：仅"未证实"(利多利空/热度已移到左导轨信号列)
  const eb = el("div", "card-eyebrow");
  if (unv) eb.appendChild(el("span", "badge badge-unverified", "未证实"));
  if (eb.children.length) main.appendChild(eb);

  // 标题
  const title = el("div", "card-title");
  const a = el("a");
  a.href = it.source_url || "#";
  a.target = "_blank";
  a.rel = "noopener";
  a.textContent = it.title_zh;
  title.appendChild(a);
  main.appendChild(title);

  // 正文 / 合并陈述
  if (it.segments && it.segments.length) {
    const segs = el("div", "card-segs");
    it.segments.forEach(sg => segs.appendChild(el("div", "card-seg", esc(sg))));
    main.appendChild(segs);
  } else if (it.summary_zh) {
    main.appendChild(el("div", "card-summary", esc(it.summary_zh)));
  }

  // 脚注：标签(左) + 传播曲线(右)
  const foot = el("div", "card-foot");

  const tags = el("div", "tags");
  (it.entities || []).forEach(e => {
    const label = e.code ? `${e.name} ${e.code}` : e.name;
    tags.appendChild(el("span", "tag entity", esc(label)));
  });
  (it.tags || []).slice(0, 4).forEach(t => tags.appendChild(el("span", "tag", esc(t))));
  if (tags.children.length) foot.appendChild(tags);

  const pc = it.prop_curve;
  if (pc && pc.pts && pc.pts.length) {
    const lvl = (it.propagation && it.propagation.level) || 0;
    const row = el("div", "prop-row prop-lv" + lvl);
    row.title = "点击看传播曲线";
    const badge = lvl >= 1 ? `<span class="badge badge-prop">发酵 LV${lvl}</span>` : "";
    row.innerHTML = badge
      + `<svg class="spark" viewBox="0 0 88 22" preserveAspectRatio="none">`
      + `<polyline points="${sparkPath(pc.pts, 88, 22)}" fill="none" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/></svg>`
      + `<span class="prop-n">累计 ${pc.n} 家</span>`;
    row.addEventListener("click", () => openPropModal(it));
    foot.appendChild(row);
  }

  if (foot.children.length) main.appendChild(foot);

  card.appendChild(main);
  return card;
}

// 进场动效：仅位移(不降透明度)，即使合成器被节流也不会留下空白。
function revealNew() {
  $all(".card:not([data-shown])").forEach((n, i) => {
    n.setAttribute("data-shown", "1");
    n.style.transform = "translateY(14px)";
    void n.offsetWidth;
    n.style.transition = "transform .5s cubic-bezier(.2,.7,.2,1)";
    setTimeout(() => { n.style.transform = "none"; }, 40 + Math.min(i, 10) * 45);
  });
  // 兜底：sparkline 自绘动画结束后锁定为完整绘出。
  setTimeout(() => {
    $all(".spark polyline").forEach(p => { p.style.animation = "none"; p.style.strokeDashoffset = "0"; });
  }, 1500);
}

function renderList(items) {
  const list = $("#list");
  list.innerHTML = "";
  $("#empty").hidden = items.length > 0;
  items.forEach(it => list.appendChild(cardNode(it)));
  renderedIds = new Set(items.map(it => it.id));
  loadedCount = items.length;
  hideNewPill();
  setLoadMore(items.length >= PAGE_SIZE);   // 满页才可能有下一页
  revealNew();
}

function setLoadMore(show) {
  let btn = document.getElementById("load-more");
  if (!btn) {
    btn = el("button", "load-more", "加载更多");
    btn.id = "load-more";
    btn.addEventListener("click", loadMore);
    $("#list").insertAdjacentElement("afterend", btn);
  }
  btn.hidden = !show;
}

function loadMore() {
  const btn = document.getElementById("load-more");
  if (btn) { btn.disabled = true; btn.textContent = "加载中…"; }
  fetch(buildItemsUrl(loadedCount)).then(r => r.json()).then(d => {
    const items = d.items || [];
    const list = $("#list");
    items.forEach(it => {
      if (!renderedIds.has(it.id)) { list.appendChild(cardNode(it)); renderedIds.add(it.id); }
    });
    loadedCount += items.length;
    if (btn) { btn.disabled = false; btn.textContent = "加载更多"; }
    setLoadMore(items.length >= PAGE_SIZE);   // 不足一页 = 没更多了
    revealNew();
  }).catch(() => { if (btn) { btn.disabled = false; btn.textContent = "加载更多"; } });
}

function renderDaily(data) {
  const list = $("#list");
  list.innerHTML = "";
  $("#empty").hidden = (data.sections || []).length > 0;

  // date bar
  fetch(`api/public/dailies?channel=${CHANNEL}&take=10`)
    .then(r => r.json())
    .then(dl => {
      const bar = el("div", "daily-date-bar");
      (dl.dates || []).forEach(d => {
        const b = el("button", "chip" + (d === data.date ? " active" : ""), d);
        b.onclick = () => { state.dailyDate = d; load(); };
        bar.appendChild(b);
      });
      list.prepend(bar);
    });

  (data.sections || []).forEach(sec => {
    const wrap = el("div", "daily-section");
    wrap.appendChild(el("h2", null,
      `${esc(sec.label)}<span class="count">${sec.items.length}</span>`));
    sec.items.forEach(it => wrap.appendChild(cardNode(it)));
    list.appendChild(wrap);
  });
  revealNew();
}

// ---------- deep analysis ----------
function mdToHtml(md) {
  // 极简 markdown：## 标题 / **粗体** / - 列表 / --- 分隔 / 段落。先转义防注入。
  const lines = esc(md).split("\n");
  let html = "", inList = false;
  const closeList = () => { if (inList) { html += "</ul>"; inList = false; } };
  for (const raw of lines) {
    const line = raw.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    if (/^\s*#{1,3}\s+/.test(raw)) {
      closeList();
      html += "<h3>" + line.replace(/^\s*#{1,3}\s+/, "") + "</h3>";
    } else if (/^\s*[-*]\s+/.test(raw)) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += "<li>" + line.replace(/^\s*[-*]\s+/, "") + "</li>";
    } else if (/^\s*-{3,}\s*$/.test(raw)) {
      closeList();
      html += "<hr>";
    } else if (line.trim() === "") {
      closeList();
    } else {
      closeList();
      html += "<p>" + line + "</p>";
    }
  }
  closeList();
  return html;
}

function renderAnalysisView() {
  const list = $("#list");
  $("#empty").hidden = true;
  list.innerHTML = "";

  const form = el("div", "analysis-form");
  const input = el("input", "analysis-input");
  input.type = "text";
  input.placeholder = "输入标的（如 英伟达 / 黄金 / 原油）";
  input.value = state.q || "英伟达";
  const btn = el("button", "analysis-btn", "深度分析");
  form.appendChild(input);
  form.appendChild(btn);
  list.appendChild(form);

  const out = el("div", "analysis-out");
  list.appendChild(out);

  function run() {
    const ent = input.value.trim();
    if (!ent) return;
    out.innerHTML = '<div class="analysis-loading">正在做深度分析（约半分钟）…</div>';
    fetch(`api/analysis?channel=${CHANNEL}&entity=${encodeURIComponent(ent)}`)
      .then(r => r.json())
      .then(d => {
        if (d.status === "ok") {
          out.innerHTML = `<div class="analysis-md">${mdToHtml(d.analysis_md)}</div>`;
        } else {
          out.innerHTML = `<div class="analysis-msg">${esc(d.message || "分析暂不可用。")}</div>`;
        }
      })
      .catch(() => { out.innerHTML = '<div class="analysis-msg">分析失败，请稍后再试。</div>'; });
  }
  btn.onclick = run;
  input.addEventListener("keydown", e => { if (e.key === "Enter") run(); });
}

// ---------- data ----------
function buildItemsUrl(offset) {
  const p = new URLSearchParams();
  p.set("channel", CHANNEL);
  if (state.view === "all") p.set("mode", "all");
  else if (state.view === "breaking") p.set("mode", "breaking");
  else if (state.view === "category") { p.set("mode", "all"); p.set("category", state.category); }
  else if (state.view === "search") { p.set("mode", "all"); }
  else if (state.view === "source") { p.set("mode", state.source === "DIGITIMES" ? "all" : "selected"); p.set("source", state.source); }
  else if (state.view === "stock") { p.set("mode", "all"); p.set("source", "金十"); p.set("stock", "1"); }
  else if (state.view === "mainline") { p.set("mode", "all"); p.set("mainline", "1"); }
  else p.set("mode", "selected");
  if (state.market) p.set("market", state.market);
  if (state.q) p.set("q", state.q);
  if (state.hideUnverified) p.set("include_unverified", "false");
  p.set("take", String(PAGE_SIZE));
  if (offset) p.set("offset", String(offset));
  return "api/public/items?" + p.toString();
}

function load() {
  hideNewPill();
  setLoadMore(false);          // 切视图先收起「加载更多」;feed 渲染后按需重显
  // title
  let title = VIEW_TITLES[state.view] || "精选";
  let kicker = VIEW_KICKERS[state.view] || "";
  if (state.view === "category") { title = CAT_LABELS[state.category] || "分类"; kicker = "Category · 分类视图"; }
  if (state.view === "search") { title = `搜索：“${state.q}”`; kicker = "Search · 关键词检索"; }
  if (state.view === "source") {
    title = state.source === "DIGITIMES" ? "DIGITIMES · 涨价大追踪" : "X · KOL";
    kicker = "Source · 信源视图";
  }
  $("#view-title").textContent = title;
  const kEl = $("#view-kicker");
  if (kEl) kEl.textContent = kicker;

  if (state.view === "analysis") {
    $("#api-link").href = `api/analysis?channel=${CHANNEL}&entity=英伟达`;
    renderAnalysisView();
    return;
  }

  $("#api-link").href = state.view === "daily"
    ? `api/public/daily?channel=${CHANNEL}`
    : buildItemsUrl();

  if (state.view === "daily") {
    const url = state.dailyDate
      ? `api/public/daily/${state.dailyDate}?channel=${CHANNEL}`
      : `api/public/daily?channel=${CHANNEL}`;
    fetch(url).then(r => r.json()).then(renderDaily);
    return;
  }
  fetch(buildItemsUrl()).then(r => r.json()).then(d => renderList(d.items || []));
}

// ---------- wiring ----------
function setActiveNav() {
  $all(".nav-item[data-view]").forEach(n => {
    let match = n.dataset.view === state.view;
    if (match && state.view === "category") match = (n.dataset.category || null) === state.category;
    if (match && state.view === "source") match = (n.dataset.source || null) === state.source;
    n.classList.toggle("active", !!match);
  });
}

$all(".nav-item[data-view]").forEach(n => {
  n.addEventListener("click", e => {
    e.preventDefault();
    state.view = n.dataset.view;
    state.category = n.dataset.category || null;
    state.source = n.dataset.source || null;
    state.q = "";
    state.dailyDate = null;
    $("#search").value = "";
    setActiveNav();
    load();
  });
});

$all("#market-chips .chip").forEach(c => {
  c.addEventListener("click", () => {
    $all("#market-chips .chip").forEach(x => x.classList.remove("active"));
    c.classList.add("active");
    state.market = c.dataset.market || "";
    load();
  });
});

$("#hide-unverified").addEventListener("change", e => {
  state.hideUnverified = e.target.checked;
  load();
});

let searchTimer = null;
$("#search").addEventListener("input", e => {
  clearTimeout(searchTimer);
  const v = e.target.value.trim();
  searchTimer = setTimeout(() => {
    if (v) { state.view = "search"; state.category = null; state.source = null; state.q = v; }
    else { state.view = "selected"; state.source = null; state.q = ""; }
    setActiveNav();
    load();
  }, 250);
});

// theme
const THEME_KEY = "aihot-theme";
function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  localStorage.setItem(THEME_KEY, t);
}
$("#theme-toggle").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme");
  applyTheme(cur === "dark" ? "light" : "dark");
});
applyTheme(localStorage.getItem(THEME_KEY) || "dark");

// ---------- 双时区时钟（纽约 / 北京） ----------
function fmtTZ(tz) {
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: tz,
  }).format(new Date());
}
function tickClocks() {
  const ny = document.getElementById("clock-ny");
  const bj = document.getElementById("clock-bj");
  if (ny) ny.textContent = fmtTZ("America/New_York");
  if (bj) bj.textContent = fmtTZ("Asia/Shanghai");
}
setInterval(tickClocks, 1000);
tickClocks();

// ---------- 实时行情条 ----------
// 优先读取后端 api/public/quotes（字段：name/symbol、price、chg|change_pct）。
// 若接口不存在，则使用下面的占位行情，仅供展示；接入真实接口后自动切换。
const TICKER_DEMO = [
  { name: "英伟达", price: 178.42, chg: 2.10 },
  { name: "澜起科技", price: 78.50, chg: 6.48 },
  { name: "美光", price: 102.50, chg: 4.10 },
  { name: "费城半导体", price: 5821.3, chg: 1.84 },
  { name: "特斯拉", price: 251.30, chg: -1.20 },
  { name: "英特尔", price: 24.85, chg: 3.42 },
  { name: "纳斯达克", price: 19234.6, chg: 0.92 },
  { name: "布伦特原油", price: 79.30, chg: -0.62 },
  { name: "COMEX黄金", price: 2412.4, chg: 0.41 },
  { name: "深圳华强", price: 18.76, chg: 5.30 },
  { name: "博通", price: 1685.0, chg: 1.15 },
  { name: "比亚迪", price: 246.8, chg: -0.85 },
];
let tickerData = null, tickerTimer = null;

function tkNum(v) { return Number(v || 0); }
function renderTickerTrack() {
  const track = document.getElementById("ticker-track");
  if (!track || !tickerData) return;
  const cell = t => {
    const up = tkNum(t.chg) >= 0;
    const col = up ? "var(--accent)" : "var(--green)";
    const arrow = up ? "▲" : "▼";
    const px = tkNum(t.price).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const cg = (up ? "+" : "") + tkNum(t.chg).toFixed(2) + "%";
    return `<span class="tk"><span class="nm">${esc(t.name)}</span>`
      + `<span class="px">${px}</span>`
      + `<span class="cg" style="color:${col}">${arrow}${cg}</span></span>`;
  };
  const html = tickerData.map(cell).join("");
  track.innerHTML = html + html;   // 双份拼接 → 无缝滚动
}
function normalizeQuotes(d) {
  const list = (d && (d.quotes || d.items || d.list)) || (Array.isArray(d) ? d : null);
  if (!list || !list.length) return null;
  return list.map(q => ({
    name: q.name || q.symbol || q.code || "",
    price: tkNum(q.price != null ? q.price : q.last),
    chg: tkNum(q.chg != null ? q.chg : (q.change_pct != null ? q.change_pct : q.pct)),
  }));
}
function jitterTicker() {
  if (!tickerData) return;
  tickerData.forEach(t => {
    const d = (Math.random() - 0.48) * 0.5;
    t.chg = Math.max(-9.5, Math.min(9.5, +(tkNum(t.chg) + d).toFixed(2)));
    t.price = +(tkNum(t.price) * (1 + d / 100)).toFixed(2);
  });
  renderTickerTrack();
}
function refetchTicker() {
  fetch(`api/public/quotes?channel=${CHANNEL}`)
    .then(r => r.ok ? r.json() : Promise.reject()).then(d => {
      const list = normalizeQuotes(d);
      if (list) { tickerData = list; renderTickerTrack(); }
    }).catch(() => {});
}
function startTicker(demo) {
  const box = document.getElementById("ticker");
  if (box) box.hidden = false;
  renderTickerTrack();
  clearInterval(tickerTimer);
  tickerTimer = setInterval(demo ? jitterTicker : refetchTicker, demo ? 2000 : 15000);
}
function initTicker() {
  fetch(`api/public/quotes?channel=${CHANNEL}`)
    .then(r => r.ok ? r.json() : Promise.reject()).then(d => {
      const list = normalizeQuotes(d);
      if (list) { tickerData = list; startTicker(false); }
      else throw 0;
    })
    .catch(() => {
      tickerData = TICKER_DEMO.map(x => ({ ...x }));
      startTicker(true);
      console.info("[ticker] 未取到 api/public/quotes，当前为占位行情；接入真实行情接口后自动切换。");
    });
}
initTicker();

// ---------- live refresh ----------
function showNewPill(n, freshItems) {
  let pill = document.getElementById("new-pill");
  if (!pill) {
    pill = el("button", "new-pill");
    pill.id = "new-pill";
    pill.addEventListener("click", () => {
      renderList(pill._items || []);
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
    document.body.appendChild(pill);
  }
  pill._items = freshItems;
  pill.textContent = `${n} 条新消息`;
  pill.hidden = false;
}
function hideNewPill() {
  const pill = document.getElementById("new-pill");
  if (pill) pill.hidden = true;
}
function poll() {
  if (!FEED_VIEWS.has(state.view)) return;     // 深度分析 / 日报 不自动刷新
  fetch(buildItemsUrl()).then(r => r.json()).then(d => {
    const items = d.items || [];
    const fresh = items.filter(it => !renderedIds.has(it.id));
    if (!fresh.length) return;
    if (window.scrollY < 150) renderList(items);       // 在顶部 -> 无缝刷新（新条目自然置顶）
    else showNewPill(fresh.length, items);             // 滚到下方 -> 提示条，不打断阅读
  }).catch(() => {});
}
setInterval(poll, 45000);

// initial
load();
