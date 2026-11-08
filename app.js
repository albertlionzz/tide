/* 潮汐 Tide — data-driven market desk
 * 数据源: tide_data.json（由 derive.py 从通达信实抓快照加工而成）
 * 无数据时回退到 FALLBACK，并在页面标注「离线样本」。
 */

"use strict";

/* ============================================================
 *  状态
 * ============================================================ */

const STATE = {
  data: null,
  offline: false,
  sectorSort: "change",
  activeSector: 0,
  dragonFilter: "all",
  screenTierSel: "all",
  screenTrackSel: "全部",
  screen: [],
  feed: null,        // feed_data.json：财联社快讯 + 雪球股民跟踪
  feedOffline: false,
  feedSource: "all",
};

/* ============================================================
 *  兜底样本（仅在 tide_data.json 取不到时启用）
 * ============================================================ */

const FALLBACK = {
  meta: { tradeDate: "—", capturedAt: "", source: "离线样本", generatedBy: "fallback" },
  fear: {
    value: 50,
    label: "紧张",
    caption: "暂无实时数据，展示占位读数。",
    parts: [
      { name: "涨跌家数失衡", value: 50, weight: 0.3 },
      { name: "跌停扩散度", value: 50, weight: 0.22 },
      { name: "连板高度", value: 50, weight: 0.18 },
      { name: "赚钱效应", value: 50, weight: 0.15 },
      { name: "指数振幅", value: 50, weight: 0.15 },
    ],
  },
  breadth: { advancers: 0, decliners: 0, unchanged: 0, total: 0, advRatio: 0 },
  limit: { upTotal: 0, downTotal: 0, upShown: 0, downShown: 0, maxBoards: 0, highBoardCount: 0, sealedRatio: 0, avgSealYi: 0 },
  amplitude: 0,
  indices: [],
  turnoverYi: 0,
  boards: [],
  boardStats: { up: 0, down: 0, spread: 0 },
  limitUpPool: [],
  limitDownPool: [],
  seats: [],
  stockActivity: [],
  watchlist: [],
};

/* ============================================================
 *  格式化工具
 * ============================================================ */

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

function fmtPct(n, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const sign = n > 0 ? "+" : n < 0 ? "−" : "";
  return `${sign}${Math.abs(n).toFixed(digits)}%`;
}

function pctClass(n) {
  if (n > 0) return "up";
  if (n < 0) return "down";
  return "flat";
}

function fmtNum(n, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Number(n).toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtYi(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 10000) return `${(v / 10000).toFixed(2)} 万亿`;
  if (abs >= 1) return `${v.toFixed(0)} 亿`;
  return `${(v * 10000).toFixed(0)} 万`;
}

function fmtAmountYuan(v) {
  if (!v) return "—";
  const yi = v / 1e8;
  if (yi >= 1) return `${yi.toFixed(1)} 亿`;
  return `${(v / 1e4).toFixed(0)} 万`;
}

function fmtDateCN(iso) {
  if (!iso || iso === "—") return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  return `${m[1]} 年 ${Number(m[2])} 月 ${Number(m[3])} 日`;
}

function fmtTimeHM(iso) {
  if (!iso) return "";
  const m = /T(\d{2}):(\d{2})/.exec(iso);
  return m ? `${m[1]}:${m[2]}` : "";
}

function escapeHTML(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function $(id) {
  return document.getElementById(id);
}

function setText(id, text) {
  const el = $(id);
  if (el) el.textContent = text;
}

/* ============================================================
 *  数据加载
 * ============================================================ */

async function loadData() {
  try {
    const res = await fetch("tide_data.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    if (!json || !json.meta) throw new Error("payload malformed");
    return { data: json, offline: false };
  } catch (err) {
    console.warn("[潮汐] 数据加载失败，回退到离线样本：", err);
    return { data: FALLBACK, offline: true };
  }
}

async function loadFeed() {
  try {
    const res = await fetch("feed_data.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    if (!json || !json.meta) throw new Error("payload malformed");
    return { feed: json, offline: false };
  } catch (err) {
    console.warn("[潮汐] 资讯加载失败：", err);
    return { feed: null, offline: true };
  }
}

/* ============================================================
 *  顶部元信息 / 数据源状态
 * ============================================================ */

function renderMeta() {
  const d = STATE.data;
  const dateEl = $("headerDate");
  if (dateEl) dateEl.textContent = d.meta.tradeDate;

  const dot = $("liveDot");
  if (dot) {
    dot.classList.toggle("is-offline", STATE.offline);
  }
  const metaText = $("liveText");
  if (metaText) {
    metaText.textContent = STATE.offline
      ? "离线样本"
      : `已收盘 ${fmtTimeHM(d.meta.capturedAt)}`;
  }

  setText("dataDate", fmtDateCN(d.meta.tradeDate));
  setText("dataSource", d.meta.source || "—");

  const banner = $("offlineBanner");
  if (banner) banner.hidden = !STATE.offline;
}

/* ============================================================
 *  恐慌指数
 * ============================================================ */

function fearColor(v) {
  if (v >= 81) return "var(--up)";
  if (v >= 61) return "var(--warn)";
  if (v >= 41) return "var(--accent)";
  return "var(--down)";
}

function initGauge() {
  const arc = $("gaugeArc");
  const valueEl = $("fearValue");
  const badge = $("fearBadge");
  const caption = $("fearCaption");
  const f = STATE.data.fear;
  if (!arc) return;

  const C = 2 * Math.PI * 92;
  arc.style.strokeDasharray = String(C);
  arc.style.strokeDashoffset = String(C);
  arc.style.stroke = fearColor(f.value);

  const pct = clamp(f.value, 0, 100) / 100;

  if (badge) {
    badge.textContent = f.label;
    badge.className = `badge badge-${f.value >= 61 ? "warn" : f.value <= 40 ? "down" : "accent"}`;
  }
  if (caption) caption.textContent = f.caption;

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduceMotion) {
    arc.style.strokeDashoffset = String(C * (1 - pct));
    if (valueEl) valueEl.textContent = String(f.value);
    return;
  }

  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      arc.style.strokeDashoffset = String(C * (1 - pct));
    });
  });

  if (valueEl) {
    const duration = 900;
    const t0 = performance.now();
    (function tick(now) {
      const p = Math.min(1, (now - t0) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      valueEl.textContent = String(Math.round(f.value * eased));
      if (p < 1) requestAnimationFrame(tick);
      else valueEl.textContent = String(f.value);
    })(performance.now());
  }
}

function renderFearBreakdown() {
  const list = $("fearBreakdown");
  if (!list) return;
  const f = STATE.data.fear;

  list.innerHTML = f.parts
    .map(
      (p) => `
    <div class="breakdown-item">
      <span class="bd-name">${escapeHTML(p.name)}<em class="bd-weight">${Math.round(p.weight * 100)}%</em></span>
      <span class="bd-track"><span class="bd-fill" data-w="${clamp(p.value, 0, 100)}" style="background:${fearColor(p.value)}"></span></span>
      <span class="bd-value">${p.value}</span>
    </div>`
    )
    .join("");

  requestAnimationFrame(() => {
    list.querySelectorAll(".bd-fill").forEach((el) => {
      el.style.width = `${el.dataset.w}%`;
    });
  });

  // 分项说明里的原始读数
  const b = STATE.data.breadth;
  const l = STATE.data.limit;
  const rawRow = $("fearRaw");
  if (rawRow) {
    rawRow.innerHTML = `
      <div><span>上涨</span><strong class="up">${b.advancers}</strong></div>
      <div><span>下跌</span><strong class="down">${b.decliners}</strong></div>
      <div><span>涨停</span><strong class="up">${l.upTotal}</strong></div>
      <div><span>跌停</span><strong class="down">${l.downTotal}</strong></div>
      <div><span>最高板</span><strong>${l.maxBoards} 板</strong></div>
      <div><span>均振幅</span><strong>${STATE.data.amplitude}%</strong></div>`;
  }
}

/* ============================================================
 *  指数卡片
 * ============================================================ */

function renderIndices() {
  const row = $("marketStats");
  if (!row) return;
  const d = STATE.data;

  const cards = d.indices.map(
    (i) => `
    <article class="stat-card">
      <p class="stat-label">${escapeHTML(i.name)}</p>
      <p class="stat-value">${fmtNum(i.price)}</p>
      <p class="stat-change ${pctClass(i.pct)}">${i.change >= 0 ? "+" : "−"}${fmtNum(Math.abs(i.change))} · ${fmtPct(i.pct)}</p>
    </article>`
  );

  cards.push(`
    <article class="stat-card">
      <p class="stat-label">两市成交</p>
      <p class="stat-value">${(d.turnoverYi / 10000).toFixed(2)}<span class="unit">万亿</span></p>
      <p class="stat-change flat">${fmtYi(d.turnoverYi)}</p>
    </article>`);

  row.innerHTML = cards.join("");
}

/* ============================================================
 *  行业板块
 * ============================================================ */

function sortedBoards() {
  const arr = [...STATE.data.boards];
  const key = STATE.sectorSort;
  arr.sort((a, b) => {
    if (key === "change") return b.pct - a.pct;
    if (key === "volume") return (b.turnover || 0) - (a.turnover || 0);
    return b.pct - a.pct;
  });
  return arr;
}

function renderSectors() {
  const list = $("sectorList");
  if (!list) return;
  const data = sortedBoards();
  if (!data.length) {
    list.innerHTML = `<p class="empty-hint">暂无板块数据</p>`;
    return;
  }
  const maxAbs = Math.max(...data.map((s) => Math.abs(s.pct)), 0.1);

  list.innerHTML = data
    .map((s, i) => {
      const width = Math.max(6, (Math.abs(s.pct) / maxAbs) * 100);
      const barClass = s.pct >= 0 ? "is-up" : "is-down";
      return `
      <button class="sector-item ${i === STATE.activeSector ? "is-active" : ""}" data-index="${i}" role="listitem" aria-pressed="${i === STATE.activeSector}">
        <span class="sector-rank">${String(i + 1).padStart(2, "0")}</span>
        <span class="sector-name">${escapeHTML(s.name)}</span>
        <span class="sector-bar-track" aria-hidden="true">
          <span class="sector-bar ${barClass}" style="width:${width}%"></span>
        </span>
        <span class="sector-pct ${pctClass(s.pct)}">${fmtPct(s.pct)}</span>
      </button>`;
    })
    .join("");

  list.querySelectorAll(".sector-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      STATE.activeSector = Number(btn.dataset.index);
      renderSectors();
    });
  });

  renderSectorDetail(data[STATE.activeSector] || data[0]);
}

function renderSectorDetail(s) {
  if (!s) return;
  setText("sectorDetailTitle", s.name);

  const l = STATE.data.limit;
  const desc = `板块今日 ${fmtPct(s.pct)}，换手 ${s.turnover ? s.turnover.toFixed(2) + "%" : "—"}。`;
  setText("sectorDetailDesc", desc);

  const chg = $("sectorChange");
  if (chg) {
    chg.textContent = fmtPct(s.pct);
    chg.className = `mini-value ${pctClass(s.pct)}`;
  }

  const turn = $("sectorFlow");
  if (turn) {
    turn.textContent = s.turnover ? `${s.turnover.toFixed(2)}%` : "—";
    turn.className = "mini-value";
  }

  const breadth = $("sectorBreadth");
  if (breadth) {
    breadth.textContent = s.advancers !== undefined
      ? `${s.advancers} / ${s.advancers + s.decliners}`
      : `${l.upTotal} / ${l.upTotal + l.downTotal} 全市场`;
  }

  // 该板块内的涨停股（按行业关键词匹配）
  const leaders = $("sectorLeaders");
  if (leaders) {
    const pool = STATE.data.limitUpPool.filter((r) =>
      (r.reason || "").includes(s.name) ||
      (r.reason || "").split(".").some((t) => t && s.name.includes(t))
    );
    const rows = pool.length ? pool.slice(0, 4) : [];
    leaders.innerHTML = rows.length
      ? rows
          .map(
            (r) => `
        <li>
          <span>
            <strong>${escapeHTML(r.name)}</strong>
            <span class="leader-code">${escapeHTML(r.code)}</span>
          </span>
          <span class="leader-pct up">${fmtPct(r.pct)}</span>
        </li>`
          )
          .join("")
      : `<li class="leader-empty">该板块今日无涨停个股</li>`;
  }
}

function initSectorSort() {
  document.querySelectorAll("#sectors .seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#sectors .seg-btn").forEach((b) => {
        b.classList.remove("is-active");
        b.setAttribute("aria-selected", "false");
      });
      btn.classList.add("is-active");
      btn.setAttribute("aria-selected", "true");
      STATE.sectorSort = btn.dataset.sort;
      STATE.activeSector = 0;
      renderSectors();
    });
  });
}

/* ============================================================
 *  焦点个股（真实 K 线）
 * ============================================================ */

function movingAverage(arr, win) {
  return arr.map((_, i) => {
    const start = Math.max(0, i - win + 1);
    const slice = arr.slice(start, i + 1);
    return slice.reduce((a, b) => a + b, 0) / slice.length;
  });
}

function renderStockChart(stock) {
  const svg = $("stockChart");
  if (!svg || !stock.kline60 || !stock.kline60.length) return;

  const W = 960, H = 280, padX = 16, padY = 24;
  const series = stock.kline60.map((k) => k.c);
  const ma = movingAverage(series, 20);
  const min = Math.min(...series) * 0.985;
  const max = Math.max(...series) * 1.015;
  const xAt = (i) => padX + (i / (series.length - 1)) * (W - padX * 2);
  const yAt = (v) => padY + (1 - (v - min) / (max - min)) * (H - padY * 2);

  const linePath = series
    .map((v, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(2)} ${yAt(v).toFixed(2)}`)
    .join(" ");
  const areaPath =
    linePath +
    ` L ${xAt(series.length - 1).toFixed(2)} ${H - padY}` +
    ` L ${xAt(0).toFixed(2)} ${H - padY} Z`;
  const maPath = ma
    .map((v, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(2)} ${yAt(v).toFixed(2)}`)
    .join(" ");

  const rising = stock.pct >= 0;
  const stroke = rising ? "#FF3B30" : "#34C759";
  const fill = rising ? "rgba(255,59,48,0.12)" : "rgba(52,199,89,0.12)";

  const grids = [0.25, 0.5, 0.75]
    .map((t) => {
      const y = padY + t * (H - padY * 2);
      return `<line x1="${padX}" y1="${y}" x2="${W - padX}" y2="${y}" stroke="rgba(0,0,0,0.06)" stroke-width="1"/>`;
    })
    .join("");

  const lastX = xAt(series.length - 1);
  const lastY = yAt(series[series.length - 1]);

  svg.innerHTML = `
    ${grids}
    <path d="${areaPath}" fill="${fill}" />
    <path d="${linePath}" fill="none" stroke="${stroke}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" />
    <path d="${maPath}" fill="none" stroke="#0071E3" stroke-width="1.5" stroke-dasharray="4 4" opacity="0.85" />
    <circle cx="${lastX}" cy="${lastY}" r="5" fill="${stroke}" />
    <circle cx="${lastX}" cy="${lastY}" r="9" fill="${stroke}" opacity="0.2" />`;

  // 更新图例里的区间信息
  setText("chartRange", `${stock.kline60[0].d} → ${stock.kline60[stock.kline60.length - 1].d}`);
}

function renderStock(idx) {
  const wl = STATE.data.watchlist;
  const stock = wl[idx];
  if (!stock) return;

  setText("stockName", stock.name);
  setText("stockCode", `${stock.code} · ${stock.industry || ""}`);

  const priceEl = $("stockPrice");
  if (priceEl) priceEl.textContent = fmtNum(stock.price);

  const delta = $("stockDelta");
  if (delta) {
    const sign = stock.pct >= 0 ? "+" : "−";
    delta.textContent = `${sign}${Math.abs(stock.pct).toFixed(2)}%`;
    delta.className = `stock-delta ${pctClass(stock.pct)}`;
  }

  const kl = stock.kline60 || [];
  const last = kl[kl.length - 1];
  const prev = kl[kl.length - 2] || last;
  const high5 = Math.max(...kl.slice(-5).map((k) => k.c));
  const low5 = Math.min(...kl.slice(-5).map((k) => k.c));
  const chg5 = prev && kl.length > 5 ? ((last.c / kl[kl.length - 6].c - 1) * 100) : 0;
  const chg20 = kl.length > 20 ? ((last.c / kl[kl.length - 21].c - 1) * 100) : 0;

  const metrics = $("stockMetrics");
  if (metrics) {
    metrics.innerHTML = [
      { label: "最新价", value: fmtNum(stock.price) },
      { label: "今日涨幅", value: fmtPct(stock.pct), cls: pctClass(stock.pct) },
      { label: "换手率", value: stock.turnoverRate ? `${stock.turnoverRate.toFixed(2)}%` : "—" },
      { label: "5日涨幅", value: fmtPct(chg5), cls: pctClass(chg5) },
      { label: "20日涨幅", value: fmtPct(chg20), cls: pctClass(chg20) },
      { label: "近5日区间", value: `${fmtNum(low5)} – ${fmtNum(high5)}` },
    ]
      .map(
        (m) => `
      <div class="metric-item">
        <p class="m-label">${escapeHTML(m.label)}</p>
        <p class="m-value ${m.cls || ""}">${escapeHTML(m.value)}</p>
      </div>`
      )
      .join("");
  }

  // 盘面解读：由真实数字生成，不编造
  const note = $("stockNote");
  if (note) {
    const dir = stock.pct >= 0 ? "上涨" : "下跌";
    note.textContent =
      `${stock.name}今日${dir} ${Math.abs(stock.pct).toFixed(2)}%，` +
      `换手率 ${stock.turnoverRate ? stock.turnoverRate.toFixed(2) + "%" : "—"}。` +
      `近 20 个交易日累计 ${fmtPct(chg20)}，5 日累计 ${fmtPct(chg5)}。` +
      `近 5 日价格运行区间 ${fmtNum(low5)} – ${fmtNum(high5)}。`;
  }

  renderStockChart(stock);

  document.querySelectorAll(".stock-chip").forEach((chip) => {
    const on = Number(chip.dataset.idx) === idx;
    chip.classList.toggle("is-active", on);
    chip.setAttribute("aria-selected", on ? "true" : "false");
  });
}

function initStockPicker() {
  const picker = $("stockPicker");
  if (!picker) return;
  const wl = STATE.data.watchlist;
  if (!wl.length) {
    picker.innerHTML = `<span class="empty-hint">暂无自选股数据</span>`;
    return;
  }
  picker.innerHTML = wl
    .map(
      (s, i) => `
    <button class="stock-chip ${i === 0 ? "is-active" : ""}" data-idx="${i}" role="tab" aria-selected="${i === 0}">
      ${escapeHTML(s.name)}
    </button>`
    )
    .join("");

  picker.querySelectorAll(".stock-chip").forEach((chip) => {
    chip.addEventListener("click", () => renderStock(Number(chip.dataset.idx)));
  });

  renderStock(0);
}

/* ============================================================
 *  涨停 / 跌停池
 * ============================================================ */

let poolTab = "up";

function renderPool() {
  const body = $("poolBody");
  if (!body) return;
  const d = STATE.data;
  const rows = poolTab === "up" ? d.limitUpPool : d.limitDownPool;

  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="7" class="table-empty">暂无数据</td></tr>`;
    return;
  }

  body.innerHTML = rows
    .map((r) => {
      const isUp = poolTab === "up";
      const ladder = isUp
        ? `${r.boards} 板`
        : r.days > 1 ? `${r.days} 连跌` : "首跌";
      const theme = (isUp ? r.reason : r.theme) || "—";
      return `
      <tr>
        <td>
          <span class="t-name">${escapeHTML(r.name)}</span>
          <span class="t-code">${escapeHTML(r.code)}</span>
        </td>
        <td class="t-num ${pctClass(r.pct)}">${fmtPct(r.pct)}</td>
        <td><span class="badge ${isUp ? "badge-up" : "badge-down"}">${escapeHTML(ladder)}</span></td>
        <td class="t-num">${fmtAmountYuan(r.sealAmount)}</td>
        <td class="t-num">${r.openTimes === 0 ? "未开板" : r.openTimes + " 次"}</td>
        <td class="cell-theme">${escapeHTML(theme)}</td>
        <td class="cell-note">${escapeHTML((r.detail || "").slice(0, 42))}</td>
      </tr>`;
    })
    .join("");
}

function initPoolTabs() {
  document.querySelectorAll("#poolFilter .seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#poolFilter .seg-btn").forEach((b) => {
        b.classList.remove("is-active");
        b.setAttribute("aria-selected", "false");
      });
      btn.classList.add("is-active");
      btn.setAttribute("aria-selected", "true");
      poolTab = btn.dataset.pool;
      renderPool();
    });
  });
}

/* ============================================================
 *  龙虎榜（席位真实数据）
 * ============================================================ */

function renderDragon(filter = STATE.dragonFilter) {
  const body = $("dragonBody");
  if (!body) return;

  const rows = STATE.data.seats.filter((s) => {
    if (filter === "all") return true;
    return s.kind === filter;
  });

  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="6" class="table-empty">暂无符合条件的席位记录</td></tr>`;
    return;
  }

  body.innerHTML = rows
    .map(
      (s) => `
    <tr>
      <td>
        <span class="t-name">${escapeHTML(s.seat)}</span>
        <span class="t-code">${escapeHTML(s.kind)}专用 · 当日上榜 ${s.lists} 只</span>
      </td>
      <td class="t-num up">+${fmtNum(s.buy)} 亿</td>
      <td class="t-num down">−${fmtNum(s.sell)} 亿</td>
      <td class="t-num ${pctClass(s.net)}">${s.net >= 0 ? "+" : "−"}${fmtNum(Math.abs(s.net))} 亿</td>
      <td class="t-num">${fmtNum(s.turnover)} 亿</td>
      <td><span class="badge ${s.net > 0 ? "badge-up" : "badge-down"}">${escapeHTML(s.signal)}</span></td>
    </tr>`
    )
    .join("");
}

function initDragonFilter() {
  document.querySelectorAll("#dragonFilter .seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#dragonFilter .seg-btn").forEach((b) => {
        b.classList.remove("is-active");
        b.setAttribute("aria-selected", "false");
      });
      btn.classList.add("is-active");
      btn.setAttribute("aria-selected", "true");
      STATE.dragonFilter = btn.dataset.filter;
      renderDragon(STATE.dragonFilter);
    });
  });
}

/* ============================================================
 *  游资研究结论
 *  来源：research/youzi-2nd-board/REPORT.md（2026-09-29，25+ 源）
 *  其中「今日席位」对照由当日真实席位数据实时计算，不写死。
 * ============================================================ */

// 知名席位档案：活跃度字段为报告口径的公开统计（截至 2026-09-29）
const HOT_RESEARCH = [
  {
    name: "章盟主",
    initials: "章",
    seatKey: "国泰海通",
    tags: ["容量核心", "隔日切换"],
    hold: "3–5 日",
    tier: "最活跃",
    upto: "累计上榜 1,995 日 · 净买入约 +802 亿",
    style: "偏好液冷/热管理/CPO/PCB 等行业主线容量核心，单日多票、分散打板后隔日切换，不锁仓。",
  },
  {
    name: "桑田路",
    initials: "桑",
    seatKey: "拉萨",
    tags: ["高位抱团", "题材切换"],
    hold: "2–4 日",
    tier: "仍活跃",
    upto: "累计上榜 1,786 日 · 偏数字经济/CPO",
    style: "以高位抱团代替梯队接力：在少数票上反复聚集，题材切换快，公开榜少见连续多日锁仓。",
  },
  {
    name: "炒股养家",
    initials: "养",
    seatKey: "中信证券",
    tags: ["情绪周期", "择时"],
    hold: "5–8 日",
    tier: "活跃度下降",
    upto: "累计上榜 1,864 日 · 末次公开上榜 09-16",
    style: "通道优势 + 一字 + 隔日高位兑现 + 低位挖掘，情绪周期心法出处。择时权重高于选股，冰点/退潮休息。",
  },
  {
    name: "赵老哥",
    initials: "赵",
    seatKey: "华鑫",
    tags: ["龙头接力", "趋势加速"],
    hold: "1–2 日",
    tier: "近乎停手",
    upto: "近库仅 2 个上榜日 · 末次 2026-06-04",
    style: "教科书式趋势加速板与龙头接力。当前公开榜几乎沉默——「二板接力」代表人物自己不在场，本身就是风险信号。",
  },
];

// 情绪阶段 → 总仓建议（报告 §7.1）
const STAGE_TABLE = [
  { stage: "冰点", pos: "0–2 成", sec: "低吸四类 / 空仓", tone: "down" },
  { stage: "修复", pos: "2–4 成", sec: "二板轻仓试错", tone: "accent" },
  { stage: "发酵", pos: "5–7 成", sec: "二板主武器", tone: "warn" },
  { stage: "高潮", pos: "3–5 成", sec: "分批兑现", tone: "warn" },
  { stage: "分歧", pos: "0–3 成", sec: "良性分歧低吸", tone: "accent" },
  { stage: "退潮", pos: "0–1 成", sec: "空仓，不战", tone: "down" },
];

/**
 * 由当日真实盘面推导「二板阶段门」的四道关卡。
 * 阈值取自 REPORT.md §4.3 公开复盘硬开关。
 */
function computeStageGate() {
  const l = STATE.data.limit;
  const b = STATE.data.breadth;

  // 炸板率：样本中位数约 25%，>30% 且涨停收缩则推迟
  const brokenRate = l.upShown ? (l.openTimesShown / l.upShown) * 100 : 0;
  const gateBreak = {
    label: "炸板率与涨停收缩",
    pass: brokenRate <= 30 || l.upTotal >= 80,
    detail: `开板合计 ${l.openTimesShown} 次 / 样本 ${l.upShown} 只 · 涨停 ${l.upTotal} 家`,
  };

  const gateLimitDown = {
    label: "跌停潮预警",
    pass: l.downTotal < 10,
    detail: `跌停 ${l.downTotal} 家（≥10 为修复中断警报）`,
  };

  const gateLadder = {
    label: "连板梯队完整性",
    pass: l.maxBoards >= 4 && l.highBoardCount >= 2,
    detail: `最高 ${l.maxBoards} 板 · 3 板以上 ${l.highBoardCount} 家`,
  };

  const gateBreadth = {
    label: "情绪结构（筛选 or 退潮）",
    pass: l.upTotal >= 50 && b.advRatio >= 45,
    detail: `涨停 ${l.upTotal} 家 · 上涨占比 ${b.advRatio.toFixed(1)}%`,
  };

  const gates = [gateBreak, gateLimitDown, gateLadder, gateBreadth];
  const passCount = gates.filter((g) => g.pass).length;

  // 阶段判定：四门全过 → 发酵/修复；两门以上不过 → 退潮
  let stage;
  if (passCount === 4) stage = "发酵";
  else if (passCount === 3) stage = "修复";
  else if (passCount === 2) stage = "分歧";
  else stage = "退潮";

  return { gates, passCount, stage };
}

function renderHot() {
  const grid = $("hotGrid");
  if (!grid) return;
  const seats = STATE.data.seats;

  grid.innerHTML = HOT_RESEARCH.map((h) => {
    const hit = seats.find((s) => s.seat.includes(h.seatKey));
    const today = hit
      ? `<span class="hot-today">今日该席位有记录 · 净${hit.net >= 0 ? "买入" : "卖出"} ${fmtNum(Math.abs(hit.net))} 亿</span>`
      : `<span class="hot-today is-muted">今日公开榜无该席位记录</span>`;
    const tierCls = h.tier.includes("停手") ? "badge-down" : h.tier.includes("下降") ? "badge-warn" : "badge-accent";
    return `
    <article class="hot-card">
      <div class="hot-top">
        <div class="hot-avatar" aria-hidden="true">${escapeHTML(h.initials)}</div>
        <div>
          <h3 class="hot-name">${escapeHTML(h.name)}</h3>
          <p class="hot-seat">${escapeHTML(h.upto)}</p>
        </div>
      </div>
      <div class="hot-tags">
        <span class="badge ${tierCls}">${escapeHTML(h.tier)}</span>
        ${h.tags.map((t) => `<span class="badge badge-accent">${escapeHTML(t)}</span>`).join("")}
      </div>
      <div class="hot-stats">
        <div class="hot-stat">
          <span class="hs-label">典型持仓</span>
          <span class="hs-value">${escapeHTML(h.hold)}</span>
        </div>
      </div>
      <p class="hot-strategy">${escapeHTML(h.style)}</p>
      ${today}
    </article>`;
  }).join("");

  renderStageGate();
}

/** 阶段门：把报告里的硬阈值套到当日真实数据上 */
function renderStageGate() {
  const host = $("stageGate");
  if (!host) return;
  const { gates, passCount, stage } = computeStageGate();
  const row = STAGE_TABLE.find((s) => s.stage === stage) || STAGE_TABLE[0];

  host.innerHTML = `
    <div class="gate-head">
      <div>
        <p class="panel-kicker">情绪阶段门</p>
        <h3 class="gate-title">当前判定：<span class="gate-stage">${escapeHTML(stage)}</span></h3>
      </div>
      <div class="gate-pos">
        <span class="gate-pos-label">建议总仓</span>
        <strong class="gate-pos-value ${row.tone}">${escapeHTML(row.pos)}</strong>
      </div>
    </div>
    <p class="gate-hint">${passCount} / 4 道关卡通过 · 当前阶段优先动作：<b>${escapeHTML(row.sec)}</b>。任一项为否，今日不打二板。</p>
    <ul class="gate-list">
      ${gates.map((g) => `
        <li class="gate-item ${g.pass ? "is-pass" : "is-fail"}">
          <span class="gate-mark" aria-hidden="true">${g.pass ? "✓" : "✕"}</span>
          <span class="gate-body">
            <strong>${escapeHTML(g.label)}</strong>
            <em>${escapeHTML(g.detail)}</em>
          </span>
        </li>`).join("")}
    </ul>
    <p class="gate-note">
      阈值口径（公开复盘共识）：炸板率 &gt;30% 且涨停收缩 → 推迟；跌停 ≥10 只 → 修复中断警报；
      涨停 ≥80 只且梯队完整 → 可恢复二板。本判定由当日盘面实时计算，非人工标注。
    </p>`;
}


/* ============================================================
 *  股票筛选（研究底稿，静态资产）
 * ============================================================ */

const SCREEN_TRACKS = ["全部", "PCB", "半导体", "散热/液冷", "光通信", "创新药", "设备"];

const TRACK_MAP = {
  PCB: ["PCB", "PCB/载板", "覆铜板"],
  半导体: ["半导体封测", "半导体设备", "半导体材料", "半导体零部件", "半导体电源", "半导体精密件"],
  "散热/液冷": ["液冷散热", "散热材料", "光模块散热", "液冷泵", "散热"],
  光通信: ["光纤/CPO", "光模块LPO", "光连接器", "高速连接器", "电子布上游", "玻纤制品"],
  创新药: ["创新药", "多肽创新药", "创新疫苗", "创新生物药"],
  设备: ["制药装备", "工业自动化", "机器视觉"],
};

function screenScore(r) {
  return (r.a1 || 0) + (r.a2 || 0) + (r.a3 || 0) + (r.a4 || 0) * 2 + (r.a5 || 0) * 2;
}

function screenTier(score) {
  if (score >= 4) return "★★★";
  if (score >= 2) return "★★";
  return "★";
}

const tierClass = (t) => (t === "★★★" ? "t3" : t === "★★" ? "t2" : "t1");

function matchTrack(row, track) {
  if (track === "全部") return true;
  return (TRACK_MAP[track] || []).includes(row.track);
}

function renderScreen() {
  const body = $("screenBody");
  if (!body) return;

  const rows = STATE.screen
    .map((r) => ({ r, score: screenScore(r), tier: screenTier(screenScore(r)) }))
    .filter((x) => {
      if (STATE.screenTierSel !== "all" && x.tier !== STATE.screenTierSel) return false;
      return matchTrack(x.r, STATE.screenTrackSel);
    })
    .sort((a, b) => b.score - a.score || a.r.mcap - b.r.mcap);

  setText("screenCount", String(rows.length));

  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="10" class="table-empty">当前筛选条件下暂无候选</td></tr>`;
    return;
  }

  body.innerHTML = rows
    .map(({ r, score, tier }) => `
      <tr>
        <td class="tier ${tierClass(tier)}">${tier}</td>
        <td class="t-code">${escapeHTML(r.code)}</td>
        <td class="t-name">${escapeHTML(r.name)}</td>
        <td>${escapeHTML(r.track)}</td>
        <td class="t-num">${r.mcap}</td>
        <td class="t-num">${Number(r.turnover).toFixed(1)}</td>
        <td class="chg ${pctClass(r.chg5)}">${r.chg5 >= 0 ? "+" : ""}${Number(r.chg5).toFixed(1)}%</td>
        <td class="score">${score}</td>
        <td class="cell-note">${escapeHTML(r.note)}</td>
        <td class="cell-risk">${escapeHTML(r.risk || "—")}</td>
      </tr>`)
    .join("");
}

function initScreen() {
  const trackBox = $("screenTrackFilter");
  if (trackBox) {
    trackBox.innerHTML = SCREEN_TRACKS.map(
      (t, i) =>
        `<button class="track-chip ${i === 0 ? "is-active" : ""}" data-track="${escapeHTML(t)}" role="option" aria-selected="${i === 0}">${escapeHTML(t)}</button>`
    ).join("");
    trackBox.querySelectorAll(".track-chip").forEach((btn) => {
      btn.addEventListener("click", () => {
        trackBox.querySelectorAll(".track-chip").forEach((b) => {
          b.classList.remove("is-active");
          b.setAttribute("aria-selected", "false");
        });
        btn.classList.add("is-active");
        btn.setAttribute("aria-selected", "true");
        STATE.screenTrackSel = btn.dataset.track;
        renderScreen();
      });
    });
  }

  document.querySelectorAll("#screenTierFilter .seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#screenTierFilter .seg-btn").forEach((b) => {
        b.classList.remove("is-active");
        b.setAttribute("aria-selected", "false");
      });
      btn.classList.add("is-active");
      btn.setAttribute("aria-selected", "true");
      STATE.screenTierSel = btn.dataset.tier;
      renderScreen();
    });
  });

  renderScreen();
}

async function loadScreenPool() {
  try {
    const res = await fetch("screen_pool.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    STATE.screen = await res.json();
  } catch (err) {
    console.warn("[潮汐] 筛选池加载失败：", err);
    STATE.screen = [];
  }
}

/* ============================================================
 *  导航高亮
 * ============================================================ */

function initNavHighlight() {
  const links = [...document.querySelectorAll(".main-nav a")];
  const map = links
    .map((a) => {
      const id = a.getAttribute("href")?.slice(1);
      const section = id ? document.getElementById(id) : null;
      return section ? { a, section } : null;
    })
    .filter(Boolean);
  if (!map.length || !("IntersectionObserver" in window)) return;

  const io = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        const hit = map.find((m) => m.section === entry.target);
        if (!hit) return;
        links.forEach((l) => l.classList.remove("is-current"));
        hit.a.classList.add("is-current");
      });
    },
    { rootMargin: "-40% 0px -50% 0px", threshold: 0.01 }
  );
  map.forEach((m) => io.observe(m.section));
}

/* ============================================================
 *  资讯 · 财联社快讯 + 雪球股民跟踪
 *  数据源: feed_data.json（由 fetch_feed.py 抓取 + build_feed.py 合并）
 *    - cls      财联社电报：时效型快讯，看「发生了什么」
 *    - xueqiu   雪球帖子：股民长期跟踪，看「有人在怎么想」
 *    - stockBuzz 按 $名称(代码)$ 聚合的个股讨论度
 *    - hotTopics 财联社标签热度
 * ============================================================ */

// 快讯重要度：财联社 telegraph 的 star 只有 0/1 两档（1 = 带【】标题的正式快讯）
const starLabel = (n) => (n >= 1 ? "关注" : "");

// 高亮正文里出现的 $名称(代码)$ —— 雪球正文自带该标签
function highlightSymbols(text) {
  return escapeHTML(text).replace(
    /\$([^$()]{2,20})\((S[HZ]\d{6}|B[JS]\d{6})\)\$/g,
    (m, name, code) =>
      `<span class="sym-tag" title="${code}">${name}<i>${code}</i></span>`
  );
}

function renderFeedCls() {
  const box = $("feedClsList");
  if (!box) return;
  const items = (STATE.feed && STATE.feed.cls) || [];
  setText("feedClsCount", String(items.length));

  if (!items.length) {
    box.innerHTML = `<p class="feed-empty">未取到财联社快讯（可能是网络或站点结构变动）。</p>`;
    return;
  }

  box.innerHTML = items
    .map((it) => {
      const badge = starLabel(it.star);
      const tags = (it.tags || [])
        .map((t) => `<span class="feed-tag">${escapeHTML(t)}</span>`)
        .join("");
      const meta = [];
      if (it.read) meta.push(`阅 ${fmtNum(it.read, 0)}`);
      if (it.comments) meta.push(`评 ${it.comments}`);
      return `
      <article class="feed-item ${it.star >= 1 ? "is-key" : ""}">
        <div class="feed-item-head">
          <time class="feed-time">${escapeHTML(it.time || "--:--")}</time>
          ${badge ? `<span class="feed-badge b-watch">${badge}</span>` : ""}
          ${tags}
        </div>
        <a class="feed-title" href="${escapeHTML(it.url || "#")}" target="_blank" rel="noopener">${escapeHTML(it.title || "")}</a>
        <p class="feed-body">${escapeHTML(it.content || "")}</p>
        <div class="feed-item-foot">${meta.join(" · ")}</div>
      </article>`;
    })
    .join("");
}

function renderFeedXq() {
  const box = $("feedXqList");
  if (!box) return;
  const items = (STATE.feed && STATE.feed.xueqiu) || [];
  setText("feedXqCount", String(items.length));

  if (!items.length) {
    box.innerHTML = `<p class="feed-empty">未取到雪球帖子（站点有安全策略，可能需稍后重试）。</p>`;
    return;
  }

  box.innerHTML = items
    .map((p) => `
      <article class="feed-item is-post">
        <div class="feed-item-head">
          <span class="post-author">${escapeHTML(p.author || "雪球用户")}</span>
          <time class="feed-time">${escapeHTML(p.time || "")}</time>
        </div>
        <p class="feed-body post-body">${highlightSymbols(p.content || "")}</p>
        <div class="feed-item-foot">
          <a class="post-link" href="${escapeHTML(p.url || "#")}" target="_blank" rel="noopener">查看原帖 →</a>
        </div>
      </article>`)
    .join("");
}

function renderBuzz() {
  const box = $("buzzGrid");
  if (!box) return;
  const rows = (STATE.feed && STATE.feed.stockBuzz) || [];
  if (!rows.length) {
    box.innerHTML = `<p class="feed-empty">当日帖子里没有出现个股标签。</p>`;
    return;
  }
  box.innerHTML = rows
    .map((b) => `
      <div class="buzz-card">
        <div class="buzz-head">
          <span class="buzz-name">${escapeHTML(b.name)}</span>
          <span class="buzz-code">${escapeHTML(b.code)}</span>
        </div>
        <div class="buzz-meter">
          <span class="buzz-count">${b.postCount} 帖</span>
          <span class="buzz-authors">${b.authorCount} 位作者</span>
        </div>
        <p class="buzz-snippet">${escapeHTML((b.posts[0] || {}).snippet || "")}</p>
        <div class="buzz-foot">
          ${b.authors.slice(0, 3).map((a) => `<span class="buzz-author">${escapeHTML(a)}</span>`).join("")}
        </div>
      </div>`)
    .join("");
}

function renderTopics() {
  const box = $("topicCloud");
  if (!box) return;
  const rows = (STATE.feed && STATE.feed.hotTopics) || [];
  if (!rows.length) {
    box.innerHTML = `<p class="feed-empty">暂无可统计的标签。</p>`;
    return;
  }
  const max = rows[0].count || 1;
  box.innerHTML = rows
    .map((t) => {
      const w = Math.max(1, Math.round((t.count / max) * 5));
      return `<span class="topic-chip lv-${w}">${escapeHTML(t.tag)}<i>${t.count}</i></span>`;
    })
    .join("");
}

function renderFeed() {
  const section = $("feed");
  if (!section) return;
  // 信源筛选：只显示被选中的列
  const src = STATE.feedSource;
  const clsCol = section.querySelector(".feed-layout .feed-col:nth-child(1)");
  const xqCol = section.querySelector(".feed-layout .feed-col:nth-child(2)");
  if (clsCol) clsCol.style.display = src === "xueqiu" ? "none" : "";
  if (xqCol) xqCol.style.display = src === "cls" ? "none" : "";

  renderFeedCls();
  renderFeedXq();
  renderBuzz();
  renderTopics();
}

function initFeedFilter() {
  const box = $("feedSourceFilter");
  if (!box) return;
  box.addEventListener("click", (e) => {
    const btn = e.target.closest(".seg-btn");
    if (!btn) return;
    box.querySelectorAll(".seg-btn").forEach((b) => {
      b.classList.toggle("is-active", b === btn);
      b.setAttribute("aria-selected", b === btn ? "true" : "false");
    });
    STATE.feedSource = btn.dataset.src || "all";
    renderFeed();
  });
}

/* ============================================================
 *  启动
 * ============================================================ */

async function boot() {
  const { data, offline } = await loadData();
  STATE.data = data;
  STATE.offline = offline;

  const feedRes = await loadFeed();
  STATE.feed = feedRes.feed;
  STATE.feedOffline = feedRes.offline;

  await loadScreenPool();

  renderMeta();
  initGauge();
  initSectorSort();
  renderSectors();
  initStockPicker();
  initPoolTabs();
  renderPool();
  initDragonFilter();
  renderDragon("all");
  renderHot();
  renderFearBreakdown();
  renderIndices();
  initScreen();
  initFeedFilter();
  renderFeed();
  initNavHighlight();
}

document.addEventListener("DOMContentLoaded", boot);
