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
  feedCat: "all",    // 快讯主题筛选
  history: null,     // history.json：多日恐慌/涨跌停序列
  trendRange: "all",
  rules: null,       // screen_rules.json
  presetId: null,
  cond: null,        // 当前生效条件（预设 + 用户微调）
  poolKline: null,   // pool_kline.json：候选池全量日线
  focus: [],         // 当前焦点池（按 cond 实时计算）
  focusIdx: 0,
  linkSeat: null,    // 游资 ↔ 龙虎榜联动高亮
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

async function loadHistory() {
  try {
    const res = await fetch("history.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn("[潮汐] 历史序列加载失败：", err);
    return null;
  }
}

async function loadPoolKline() {
  try {
    const res = await fetch("pool_kline.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn("[潮汐] 候选池 K 线加载失败：", err);
    return null;
  }
}

/** 筛选规则的兜底副本：与 screen_rules.json 的 default 预设一致。
 *  网络失败时用它，页面仍然可用 —— 面板空白比数字不准更糟。 */
const FALLBACK_RULES = {
  default: "default",
  scoreRules: { a1: 1, a2: 1, a3: 1, a4: 2, a5: 2 },
  tiers: [{ name: "★★★", min: 4 }, { name: "★★", min: 2 }, { name: "★", min: 0 }],
  presets: [{
    id: "default", name: "默认（既定条件）", note: "内置兜底条件",
    mcapMin: 45, mcapMax: 330, turnoverMin: 2.5,
    excludePrefix: ["688", "689", "8", "4", "9"],
    minScore: 0, topN: 5,
  }],
};

async function loadRules() {
  try {
    const res = await fetch("screen_rules.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const d = await res.json();
    if (!d || !Array.isArray(d.presets) || !d.presets.length) throw new Error("presets 为空");
    return d;
  } catch (err) {
    console.warn("[潮汐] 筛选规则加载失败，用内置兜底：", err);
    return FALLBACK_RULES;
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
  setText("poolSource", d.meta.source || "—");   // 涨跌停池口径随数据源变化，写死会过时

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

  // 该板块内的涨停股。只能按行业关键词粗匹配：板块名带 Ⅰ/Ⅱ/Ⅲ 层级后缀，
  // 且涨停池的行业归属来自另一套行业分类，两者不保证同名，所以措辞取「样本内」。
  const leaders = $("sectorLeaders");
  if (leaders) {
    const bare = String(s.name || "").replace(/[ⅠⅡⅢⅣⅤ]/g, "").trim();
    const pool = STATE.data.limitUpPool.filter((r) => {
      const reason = r.reason || "";
      return (bare && reason.includes(bare)) ||
        reason.split(".").some((t) => t && (t.includes(bare) || bare.includes(t)));
    });
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
      : `<li class="leader-empty">样本内未见该板块涨停股</li>`;
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

/** 从候选池 K 线里取某只票的走势数据（前端切条件后仍能显示） */
function klineOf(code) {
  const pool = (STATE.poolKline && STATE.poolKline.items) || [];
  return pool.find((x) => x.code === code) || null;
}

function renderStock(idx) {
  const list = STATE.focus;
  if (!list.length) {
    setText("stockName", "—");
    setText("stockCode", "当前筛选条件下没有候选");
    setText("stockNote", "调整「股票筛选」的条件，或放宽市值 / 换手区间。");
    const svg = $("stockChart");
    if (svg) svg.innerHTML = "";
    const metrics = $("stockMetrics");
    if (metrics) metrics.innerHTML = "";
    const picker = $("stockPicker");
    if (picker) picker.innerHTML = `<span class="empty-hint">无入选标的</span>`;
    return;
  }
  const item = list[clamp(idx, 0, list.length - 1)];
  const code = item.code;
  const k = klineOf(code);

  // 价格优先用候选池的当日行情；缺了再退回筛选池字段，最后才用 K 线末根
  const px = (k && k.price) || item.px || null;
  const pct = (k && k.pct !== null && k.pct !== undefined) ? k.pct : (item.chg1 ?? null);
  const turn = (k && k.turnoverRate) || item.turnover || null;
  const kl = (k && k.kline) || [];
  const kline60 = kl.map((x) => ({ d: x.d, c: x.c }));

  setText("stockName", item.name);
  setText("stockCode", `${code} · ${item.track || "—"}`);

  const priceEl = $("stockPrice");
  if (priceEl) priceEl.textContent = px === null ? "—" : fmtNum(px);

  const delta = $("stockDelta");
  if (delta) {
    if (pct === null || pct === undefined) {
      delta.textContent = "—";
      delta.className = "stock-delta flat";
    } else {
      const sign = pct >= 0 ? "+" : "−";
      delta.textContent = `${sign}${Math.abs(pct).toFixed(2)}%`;
      delta.className = `stock-delta ${pctClass(pct)}`;
    }
  }

  const last = kline60[kline60.length - 1];
  const high5 = kline60.length ? Math.max(...kline60.slice(-5).map((x) => x.c)) : 0;
  const low5 = kline60.length ? Math.min(...kline60.slice(-5).map((x) => x.c)) : 0;
  const chg5 = kline60.length > 5 ? (last.c / kline60[kline60.length - 6].c - 1) * 100 : null;
  const chg20 = kline60.length > 20 ? (last.c / kline60[kline60.length - 21].c - 1) * 100 : null;

  const metrics = $("stockMetrics");
  if (metrics) {
    metrics.innerHTML = [
      { label: "最新价", value: px === null ? "—" : fmtNum(px) },
      { label: "今日涨幅", value: pct === null || pct === undefined ? "—" : fmtPct(pct), cls: pct === null ? "" : pctClass(pct) },
      { label: "换手率", value: turn ? `${Number(turn).toFixed(2)}%` : "—" },
      { label: "5日涨幅", value: chg5 === null ? "—" : fmtPct(chg5), cls: chg5 === null ? "" : pctClass(chg5) },
      { label: "20日涨幅", value: chg20 === null ? "—" : fmtPct(chg20), cls: chg20 === null ? "" : pctClass(chg20) },
      { label: "近5日区间", value: kline60.length ? `${fmtNum(low5)} – ${fmtNum(high5)}` : "—" },
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

  // 盘面解读：全部由真实数字生成，不写形容词
  const note = $("stockNote");
  if (note) {
    const parts = [];
    parts.push(`${item.name}（${code}）当前筛选排名第 ${item.rank}，综合 ${item.score} 分。`);
    if (pct !== null && pct !== undefined) {
      parts.push(`今日${pct >= 0 ? "上涨" : "下跌"} ${Math.abs(pct).toFixed(2)}%`);
    }
    if (turn) parts.push(`换手率 ${Number(turn).toFixed(2)}%`);
    if (chg20 !== null) parts.push(`近 20 个交易日累计 ${fmtPct(chg20)}`);
    if (chg5 !== null) parts.push(`5 日累计 ${fmtPct(chg5)}`);
    let text = parts.join("，") + "。";
    if (item.a4 && item.a4Why) text += `加分项 A4 命中：${item.a4Why}。`;
    if (item.a5 && item.a5Why) text += `加分项 A5 命中：${item.a5Why}。`;
    else if (!item.a5 && item.a5Why) text += `A5 未命中（${item.a5Why}）。`;
    if (item.risk) text += `风险提示：${item.risk}。`;
    note.textContent = text;
  }

  renderStockChart({ kline60, pct: pct === null ? 0 : pct });

  document.querySelectorAll(".stock-chip").forEach((chip) => {
    const on = Number(chip.dataset.idx) === idx;
    chip.classList.toggle("is-active", on);
    chip.setAttribute("aria-selected", on ? "true" : "false");
  });
  STATE.focusIdx = idx;
}

function initStockPicker() {
  const picker = $("stockPicker");
  if (!picker) return;
  const list = STATE.focus;
  if (!list.length) {
    picker.innerHTML = `<span class="empty-hint">无入选标的</span>`;
    return;
  }
  picker.innerHTML = list
    .map(
      (s, i) => `
    <button class="stock-chip ${i === STATE.focusIdx ? "is-active" : ""}" data-idx="${i}" role="tab"
      aria-selected="${i === STATE.focusIdx}">
      <span class="sc-name">${escapeHTML(s.name)}</span>
      <span class="sc-rank">#${s.rank}</span>
    </button>`
    )
    .join("");

  picker.querySelectorAll(".stock-chip").forEach((chip) => {
    chip.addEventListener("click", () => renderStock(Number(chip.dataset.idx)));
  });

  renderStock(clamp(STATE.focusIdx, 0, list.length - 1));
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
    body.innerHTML = `<tr><td colspan="7" class="table-empty">暂无符合条件的席位记录</td></tr>`;
    return;
  }

  body.innerHTML = rows
    .map((s) => {
      // 席位 → 游资档案：能对上就给个可点的标签，点回去高亮游资卡片
      const prof = profileOfSeat(s.seat);
      const profCell = prof
        ? `<td><button class="seat-link" data-profile="${escapeHTML(prof.name)}" type="button">${
            escapeHTML(prof.name)}</button></td>`
        : `<td class="cell-risk">—</td>`;
      const linked = STATE.linkSeat
        && (String(s.seat).includes(STATE.linkSeat.key) || (prof && prof.name === STATE.linkSeat.key));
      return `
    <tr class="${linked ? "is-linked" : ""}">
      <td>
        <span class="t-name">${escapeHTML(s.seat)}</span>
        <span class="t-code">${escapeHTML(s.kind)}专用 · 当日上榜 ${s.lists} 只</span>
      </td>
      <td class="t-num up">+${fmtNum(s.buy)} 亿</td>
      <td class="t-num down">−${fmtNum(s.sell)} 亿</td>
      <td class="t-num ${pctClass(s.net)}">${s.net >= 0 ? "+" : "−"}${fmtNum(Math.abs(s.net))} 亿</td>
      <td class="t-num">${fmtNum(s.turnover)} 亿</td>
      ${profCell}
      <td><span class="badge ${s.net > 0 ? "badge-up" : "badge-down"}">${escapeHTML(s.signal)}</span></td>
    </tr>`;
    })
    .join("");

  body.querySelectorAll(".seat-link").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const name = btn.dataset.profile;
      setLinkSeat(name, name);
      document.getElementById("hotmoney")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
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
  const profiles = seatProfiles();
  const win = (STATE.data.seatStocks && STATE.data.seatStocks.window) || {};

  grid.innerHTML = HOT_RESEARCH.map((h) => {
    const hit = seats.find((s) => s.seat.includes(h.seatKey));
    const today = hit
      ? `<span class="hot-today">今日该席位有记录 · 净${hit.net >= 0 ? "买入" : "卖出"} ${fmtNum(Math.abs(hit.net))} 亿</span>`
      : `<span class="hot-today is-muted">今日公开榜无该席位记录</span>`;

    // 近 10 个交易日真实买入的个股：来自龙虎榜席位—个股明细，不是人工标注。
    // 点开可看逐日买卖，解决「游资买了什么、哪天买的」这个核心问题。
    const p = profiles.find((x) => x.name === h.name);
    let recent;
    if (p && (p.stocks || []).length) {
      const chips = p.stocks.slice(0, 4).map((st) => `
        <button class="stock-chip-mini ${st.net >= 0 ? "is-buy" : "is-sell"}" type="button"
          data-profile="${escapeHTML(h.name)}" data-code="${escapeHTML(st.code)}"
          title="区间买 ${fmtNum(st.buy)} 亿 / 卖 ${fmtNum(st.sell)} 亿 · 上榜 ${st.times} 次 · 最近 ${st.lastDate}">
          ${escapeHTML(st.name || st.code)}<i>${fmtNum(st.buy, 1)}</i>
        </button>`).join("");
      recent = `
      <div class="hot-recent">
        <p class="hb-title">近 ${win.days || 10} 个交易日买入 <b>${p.stockCount}</b> 只 ·
          净 <b class="${pctClass(p.net)}">${p.net >= 0 ? "+" : "−"}${fmtNum(Math.abs(p.net))}</b> 亿</p>
        <div class="hb-chips">${chips}</div>
        <p class="hb-hint">点个股看逐日买卖 · 金额单位亿元</p>
      </div>`;
    } else {
      recent = `<div class="hot-recent"><p class="hb-title is-muted">近 ${win.days || 10} 个交易日公开榜无该席位</p></div>`;
    }

    const tierCls = h.tier.includes("停手") ? "badge-down" : h.tier.includes("下降") ? "badge-warn" : "badge-accent";
    const linked = STATE.linkSeat && STATE.linkSeat.key === h.name;
    return `
    <article class="hot-card ${linked ? "is-linked" : ""}" data-name="${escapeHTML(h.name)}">
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
      ${recent}
      <button class="hot-link-btn" type="button" data-name="${escapeHTML(h.name)}">在龙虎榜中高亮该席位</button>
      ${today}
    </article>`;
  }).join("");

  grid.querySelectorAll(".stock-chip-mini").forEach((btn) => {
    btn.addEventListener("click", () => openSeatStock(btn.dataset.profile, btn.dataset.code));
  });
  grid.querySelectorAll(".hot-link-btn").forEach((btn) => {
    btn.addEventListener("click", () => jumpToDragon(btn.dataset.name, btn.dataset.name));
  });

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

// 赛道分组用「包含」匹配。原先按全名白名单，一旦 screen_pool 里出现
// 「半导体封测设备」这种新写法就会被漏掉，换成子串匹配后不会再漏。
const TRACK_GROUPS = {
  PCB: ["PCB", "覆铜板"],
  半导体: ["半导体"],
  "散热/液冷": ["散热", "液冷"],
  光通信: ["光纤", "光模块", "光连接", "高速连接", "玻纤", "电子布"],
  创新药: ["创新药", "创新疫苗", "创新生物"],
  设备: ["装备", "自动化", "机器视觉"],
};

function matchTrack(row, group) {
  if (group === "全部") return true;
  const keys = TRACK_GROUPS[group] || [];
  return keys.some((k) => String(row.track || "").includes(k));
}

/* ============================================================
 *  筛选规则引擎
 *  与 pick_top5.py 共用 screen_rules.json 的同一套口径：
 *  硬条件（市值 / 换手 / 板块 / 加分门槛）过滤，加分项排序取前 N。
 *  改条件后表格、焦点池、个股分析三处同时重算。
 * ============================================================ */

const DEFAULT_SCORE_RULES = { a1: 1, a2: 1, a3: 1, a4: 2, a5: 2 };

function scoreOf(r) {
  const sr = (STATE.rules && STATE.rules.scoreRules) || DEFAULT_SCORE_RULES;
  return (r.a1 || 0) * sr.a1 + (r.a2 || 0) * sr.a2 + (r.a3 || 0) * sr.a3
    + (r.a4 || 0) * sr.a4 + (r.a5 || 0) * sr.a5;
}

function tierOf(score) {
  const tiers = (STATE.rules && STATE.rules.tiers) || [
    { name: "★★★", min: 4 }, { name: "★★", min: 2 }, { name: "★", min: 0 },
  ];
  for (const t of tiers) if (score >= t.min) return t.name;
  return "★";
}

const tierClass = (t) => (t === "★★★" ? "t3" : t === "★★" ? "t2" : "t1");

function presetById(id) {
  const list = (STATE.rules && STATE.rules.presets) || FALLBACK_RULES.presets;
  return list.find((p) => p.id === id) || list[0] || FALLBACK_RULES.presets[0];
}

function condOf(preset) {
  return {
    presetId: preset.id,
    name: preset.name,
    mcapMin: preset.mcapMin,
    mcapMax: preset.mcapMax,
    turnoverMin: preset.turnoverMin,
    minScore: preset.minScore || 0,
    topN: preset.topN || 5,
    requireA4: !!preset.requireA4,
    excludePrefix: preset.excludePrefix || [],
  };
}

/** 硬条件判定：返回空字符串表示通过，否则返回被排除的原因。 */
function rejectReason(r, cond) {
  const code = String(r.code || "");
  if (cond.excludePrefix.some((p) => code.startsWith(p))) return "板块排除";
  if (r.mcap === null || r.mcap === undefined) return "市值缺失";
  if (r.turnover === null || r.turnover === undefined) return "换手缺失";
  if (r.mcap < cond.mcapMin || r.mcap > cond.mcapMax) return "市值越界";
  if (r.turnover <= cond.turnoverMin) return "换手不足";
  if (cond.requireA4 && !r.a4) return "无游资足迹";
  if (scoreOf(r) < cond.minScore) return "加分不足";
  return "";
}

/** 跑一遍筛选：返回通过列表（已排序）与被排除原因的计数。 */
function evalPool(cond) {
  const passed = [];
  const rejected = {};
  (STATE.screen || []).forEach((r) => {
    const why = rejectReason(r, cond);
    if (why) {
      rejected[why] = (rejected[why] || 0) + 1;
      return;
    }
    const score = scoreOf(r);
    passed.push({ r, score, tier: tierOf(score) });
  });
  passed.sort((a, b) => b.score - a.score || (a.r.mcap || 0) - (b.r.mcap || 0));
  return { passed, rejected, total: (STATE.screen || []).length };
}

/** 依据当前条件刷新焦点池，并联动个股分析。 */
function refreshFocus() {
  const { passed } = evalPool(STATE.cond);
  STATE.focus = passed.slice(0, STATE.cond.topN).map((x, i) => ({
    ...x.r, score: x.score, tier: x.tier, rank: i + 1,
  }));
  STATE.focusIdx = clamp(STATE.focusIdx, 0, Math.max(0, STATE.focus.length - 1));
  // 先重绘表格再画 picker：表格里的「已入焦点池」标记依赖新的 STATE.focus，
  // 反过来会慢一拍（曾出现首屏一个标记都没有）
  renderScreen();
  initStockPicker();
  renderFocusNote(passed.length);
}

function renderFocusNote(passedCount) {
  const note = $("focusNote");
  if (!note) return;
  const asOf = (STATE.data && STATE.data.screenMeta && STATE.data.screenMeta.asOf)
    || (STATE.data && STATE.data.meta.tradeDate) || "—";
  note.innerHTML = STATE.focus.length
    ? `当前条件「<b>${escapeHTML(STATE.cond.name)}</b>」筛出 <b>${passedCount}</b> 只，按加分取前 <b>${STATE.focus.length}</b>；行情截面 ${escapeHTML(asOf)}。
       点上方标签切换标的。`
    : `当前条件「${escapeHTML(STATE.cond.name)}」下没有通过的标的，放宽条件后焦点池会自动重建。`;
}

/* ---------------- 条件面板 ---------------- */

function syncCondInputs() {
  const c = STATE.cond;
  if (!c) return;
  const set = (id, v) => { const el = $(id); if (el) el.value = v; };
  set("ruleMcapMin", c.mcapMin);
  set("ruleMcapMax", c.mcapMax);
  set("ruleTurnoverMin", c.turnoverMin);
  set("ruleMinScore", c.minScore);
  set("ruleTopN", c.topN);
  setText("rulesTitle", c.name);
  const desc = $("screenDesc");
  if (desc) {
    desc.innerHTML = `当前条件：市值 <b>${c.mcapMin}–${c.mcapMax}</b> 亿 · 换手 &gt; <b>${c.turnoverMin}%</b>`
      + ` · 加分 ≥ <b>${c.minScore}</b> 分 · 取前 <b>${c.topN}</b> 名。改条件后列表与「个股分析」焦点池同步重算。`;
  }
  document.querySelectorAll("#screenPresets .preset-btn").forEach((b) => {
    const on = b.dataset.preset === STATE.presetId;
    b.classList.toggle("is-active", on);
    b.setAttribute("aria-checked", on ? "true" : "false");
  });
}

function renderRuleExplain() {
  const box = $("ruleExplain");
  if (!box) return;
  const c = STATE.cond;
  const meta = (STATE.data && STATE.data.screenMeta) || {};
  const win = meta.hotWindow || {};
  const rows = [
    ["H1", `总市值 ${c.mcapMin}–${c.mcapMax} 亿元`],
    ["H2", `当日换手率 > ${c.turnoverMin}%`],
    ["H3", `板块排除 ${c.excludePrefix.join(" / ")} 开头`],
    ["H5", `加分门槛 ≥ ${c.minScore} 分`],
    ["A4", `近 ${win.days || 10} 个交易日被龙虎榜游资席位净买入 +2（命中 ${meta.a4Hit || 0} 只）`],
    ["A5", `近 5 日涨幅 > 12% +2（命中 ${meta.a5Hit || 0} 只）`],
  ];
  box.innerHTML = rows
    .map(([tag, text]) => `<li><span class="rule-tag">${escapeHTML(tag)}</span>${escapeHTML(text)}</li>`)
    .join("");
}

function applyCond(patch, fromPreset) {
  if (fromPreset) {
    STATE.cond = condOf(presetById(fromPreset));
    STATE.presetId = fromPreset;
  }
  if (patch) {
    Object.assign(STATE.cond, patch);
    // 手工改过参数就不再宣称「就是某个预设」，标题里标注已微调，
    // 避免预设名与实际生效条件对不上。
    if (!STATE.cond.name.includes("已微调")) STATE.cond.name += " · 已微调";
  }
  syncCondInputs();
  renderRuleExplain();
  refreshFocus();          // 内部含 renderScreen
}

function initScreenRules() {
  const presets = (STATE.rules && STATE.rules.presets) || [];
  const box = $("screenPresets");
  if (box) {
    box.innerHTML = presets
      .map((p) => `<button class="preset-btn" data-preset="${escapeHTML(p.id)}" role="radio"
        aria-checked="false" title="${escapeHTML(p.note || "")}">
        <span class="pb-name">${escapeHTML(p.name)}</span>
        <span class="pb-meta">${p.mcapMin}–${p.mcapMax} 亿 · 换手&gt;${p.turnoverMin}% · 前 ${p.topN}</span>
      </button>`)
      .join("");
    box.querySelectorAll(".preset-btn").forEach((btn) => {
      btn.addEventListener("click", () => applyCond(null, btn.dataset.preset));
    });
  }

  const bindNum = (id, key, min, max) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener("change", () => {
      let v = Number(el.value);
      if (!Number.isFinite(v)) return;
      v = clamp(v, min === undefined ? 0 : min, max === undefined ? 1e9 : max);
      el.value = v;
      applyCond({ [key]: v });
    });
  };
  bindNum("ruleMcapMin", "mcapMin", 0);
  bindNum("ruleMcapMax", "mcapMax", 1);
  bindNum("ruleTurnoverMin", "turnoverMin", 0, 100);
  bindNum("ruleMinScore", "minScore", 0, 20);
  bindNum("ruleTopN", "topN", 1, 10);

  const reset = $("ruleReset");
  if (reset) reset.addEventListener("click", () => {
    const def = (STATE.rules && STATE.rules.default) || "default";
    STATE.focusIdx = 0;
    applyCond(null, def);
  });
}

function renderScreen() {
  const body = $("screenBody");
  if (!body) return;
  if (!STATE.cond) return;

  const { passed, rejected, total } = evalPool(STATE.cond);
  const rows = passed.filter((x) => {
    if (STATE.screenTierSel !== "all" && x.tier !== STATE.screenTierSel) return false;
    return matchTrack(x.r, STATE.screenTrackSel);
  });

  setText("screenCount", String(rows.length));
  const asOf = (STATE.data && STATE.data.screenMeta && STATE.data.screenMeta.asOf)
    || (STATE.data && STATE.data.meta.tradeDate) || "—";
  setText("screenAsOf", asOf);

  const foot = $("screenFoot");
  if (foot) {
    const parts = Object.keys(rejected).map((k) => `${k} ${rejected[k]}`);
    foot.textContent = `全池 ${total} 只 · 通过硬条件 ${passed.length} 只`
      + (parts.length ? ` · 排除：${parts.join(" / ")}` : "")
      + " · 行情为当日收盘截面，不构成投资建议";
  }

  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="10" class="table-empty">当前条件下暂无候选，放宽市值 / 换手试试</td></tr>`;
    return;
  }

  const focusCodes = new Set(STATE.focus.map((f) => f.code));

  body.innerHTML = rows
    .map(
      ({ r, score, tier }) => `
      <tr data-code="${escapeHTML(r.code)}" class="${focusCodes.has(r.code) ? "is-focus" : ""}">
        <td class="tier ${tierClass(tier)}">${tier}${focusCodes.has(r.code) ? '<i class="focus-dot" title="已进入个股分析焦点池"></i>' : ""}</td>
        <td class="t-code">${escapeHTML(r.code)}</td>
        <td class="t-name">${escapeHTML(r.name)}</td>
        <td>${escapeHTML(r.track)}</td>
        <td class="t-num">${r.mcap === null ? "—" : r.mcap}</td>
        <td class="t-num">${r.turnover === null ? "—" : Number(r.turnover).toFixed(1)}</td>
        <td class="chg ${r.chg5 === null || r.chg5 === undefined ? "flat" : pctClass(r.chg5)}">${
          r.chg5 === null || r.chg5 === undefined ? "—"
            : `${r.chg5 >= 0 ? "+" : ""}${Number(r.chg5).toFixed(1)}%`}</td>
        <td class="score">${score}</td>
        <td class="cell-note">${escapeHTML(r.note || "")}</td>
        <td class="cell-risk">${escapeHTML(r.risk || "—")}</td>
      </tr>`)
    .join("");

  body.querySelectorAll("tr[data-code]").forEach((tr) => {
    tr.addEventListener("click", () => {
      const code = tr.dataset.code;
      const idx = STATE.focus.findIndex((f) => f.code === code);
      document.getElementById("stock")?.scrollIntoView({ behavior: "smooth", block: "start" });
      if (idx >= 0) renderStock(idx);
    });
  });
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

  // 建立初始条件：优先服务端算出的预设（top5.json），否则用 rules 默认
  const def = (STATE.rules && STATE.rules.default) || "default";
  const serverPreset = STATE.data && STATE.data.focus && STATE.data.focus.presetId;
  STATE.presetId = serverPreset || def;
  STATE.cond = condOf(presetById(STATE.presetId));

  initScreenRules();
  syncCondInputs();
  renderRuleExplain();
  refreshFocus();          // 内部含 renderScreen
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

/* ============================================================
 *  资金与趋势
 *  history.json 提供多日序列（全部点用同一版 derive.py 重算，口径一致）；
 *  板块与资金结构用当日快照。缺数据的交易日不插值，折线只连真实抓到的点。
 * ============================================================ */

const FEAR_BANDS = [
  { hi: 20, name: "冷静", fill: "rgba(52,199,89,.07)" },
  { hi: 40, name: "平稳", fill: "rgba(52,199,89,.04)" },
  { hi: 60, name: "紧张", fill: "rgba(255,159,10,.06)" },
  { hi: 80, name: "偏高", fill: "rgba(255,59,48,.05)" },
  { hi: 100, name: "恐慌", fill: "rgba(255,59,48,.09)" },
];

const MONEY_KINDS = [
  { kind: "机构", name: "机构专用", color: "var(--accent)" },
  { kind: "北向", name: "沪深股通", color: "var(--warn)" },
  { kind: "营业部", name: "营业部 / 游资", color: "var(--up)" },
];

function fearBandColor(v) {
  if (v <= 20) return "#34c759";
  if (v <= 40) return "#30b14a";
  if (v <= 60) return "#ff9f0a";
  if (v <= 80) return "#ff7a45";
  return "#ff3b30";
}

function trendPoints() {
  const all = (STATE.history && STATE.history.points) || [];
  if (!all.length) return [];
  if (STATE.trendRange === "all") return all;
  const n = Math.max(2, Number(STATE.trendRange) || all.length);
  return all.slice(Math.max(0, all.length - n));
}

const mmdd = (d) => String(d || "").slice(5).replace("-", "/");

/** 折线区间用的公共坐标系。
 *  X 方向内缩 18px：否则第一个点/柱正好落在 Y 轴线上，首尾的数值标签还会被卡片裁掉。 */
function chartFrame(pts, padTop = 22) {
  const W = 720, H = 250, L = 46, R = 20, T = padTop, B = 38, PAD = 18;
  const iw = W - L - R, ih = H - T - B;
  const x = (i) => (pts.length === 1
    ? L + iw / 2
    : L + PAD + ((iw - PAD * 2) * i) / (pts.length - 1));
  return {
    W, H, L, R, T, B, iw, ih, PAD,
    x,
    y: (v, max) => T + ih - (ih * clamp(v, 0, max)) / max,
  };
}

/* ---------------- 1. 恐慌指数折线 ---------------- */

function renderFearChart() {
  const svg = $("chartFear");
  const sub = $("trendFearSub");
  const legend = $("chartFearLegend");
  if (!svg) return;

  const pts = trendPoints();
  if (!pts.length) {
    svg.innerHTML = "";
    if (sub) sub.textContent = "暂无历史序列";
    return;
  }

  const f = chartFrame(pts, 20);
  let body = "";

  // 分档背景 + 刻度。刻度值必须画在「该档上沿」而不是下沿，
  // 否则 20/40/60/80 整体错一档（曾把 20 标在 0 的位置）。
  let lo = 0;
  FEAR_BANDS.forEach((b) => {
    const yTop = f.y(b.hi, 100), yBot = f.y(lo, 100);
    body += `<rect x="${f.L}" y="${yTop.toFixed(1)}" width="${f.iw}" height="${(yBot - yTop).toFixed(1)}"
      fill="${b.fill}"></rect>`;
    body += `<line x1="${f.L}" y1="${yBot.toFixed(1)}" x2="${f.L + f.iw}" y2="${yBot.toFixed(1)}"
      stroke="var(--tertiary)" stroke-width="0.6" stroke-dasharray="3 4"></line>`;
    body += `<text x="${f.L - 10}" y="${(yTop + 4).toFixed(1)}" text-anchor="end"
      style="fill:var(--secondary);font-size:11px">${b.hi}</text>`;
    // 分档名放右端：左侧要让位给折线的数值标签，放左边必然和首点撞
    body += `<text x="${(f.L + f.iw - 6).toFixed(1)}" y="${(yTop + 15).toFixed(1)}" text-anchor="end"
      style="fill:var(--secondary);font-size:10px;opacity:.65">${b.name}</text>`;
    lo = b.hi;
  });
  body += `<text x="${f.L - 10}" y="${f.y(0, 100) + 4}" text-anchor="end"
    style="fill:var(--secondary);font-size:11px">0</text>`;

  const coords = pts.map((p, i) => ({ p, cx: f.x(i), cy: f.y(p.fear, 100) }));
  const line = coords.map((c) => `${c.cx.toFixed(1)},${c.cy.toFixed(1)}`).join(" ");
  const baseY = (f.T + f.ih).toFixed(1);
  const firstX = coords[0].cx.toFixed(1);
  const lastX = coords[coords.length - 1].cx.toFixed(1);
  const area = `${firstX},${baseY} ${line} ${lastX},${baseY}`;

  body += `<polygon points="${area}" fill="var(--accent-soft)" class="trend-area"></polygon>`;
  body += `<polyline points="${line}" fill="none" stroke="var(--accent)" stroke-width="2.2"
    stroke-linejoin="round" stroke-linecap="round" class="trend-line"></polyline>`;

  coords.forEach((c) => {
    const col = fearBandColor(c.p.fear);
    body += `<circle cx="${c.cx.toFixed(1)}" cy="${c.cy.toFixed(1)}" r="4.2"
      fill="var(--paper)" stroke="${col}" stroke-width="2.4" class="trend-dot"></circle>`;
    body += `<text x="${c.cx.toFixed(1)}" y="${(c.cy - 11).toFixed(1)}" text-anchor="middle"
      style="fill:var(--ink);font-size:12px;font-weight:600">${c.p.fear}</text>`;
    body += `<text x="${c.cx.toFixed(1)}" y="${(f.T + f.ih + 20).toFixed(1)}" text-anchor="middle"
      style="fill:var(--secondary);font-size:11px">${mmdd(c.p.date)}</text>`;
  });

  svg.innerHTML = body;

  const first = pts[0], last = pts[pts.length - 1];
  if (sub) {
    const diff = last.fear - first.fear;
    sub.textContent = `${pts.length} 个交易日 · ${first.label} ${first.fear} → ${last.label} ${last.fear}`
      + ` · ${diff === 0 ? "持平" : (diff > 0 ? "上行 " : "回落 ") + Math.abs(diff) + " 分"}`;
  }
  if (legend) {
    legend.innerHTML = FEAR_BANDS.map(
      (b) => `<span class="lg-item"><i class="lg-swatch" style="background:${b.fill.replace(/\.\d+\)$/, ".55)")}"></i>${b.name} ≤${b.hi}</span>`
    ).join("");
  }
}

/* ---------------- 2. 涨跌停家数 ---------------- */

function renderLimitChart() {
  const svg = $("chartLimit");
  const sub = $("trendLimitSub");
  const legend = $("chartLimitLegend");
  if (!svg) return;

  const pts = trendPoints();
  if (!pts.length) {
    svg.innerHTML = "";
    if (sub) sub.textContent = "暂无历史序列";
    return;
  }

  const maxV = Math.max(10, ...pts.map((p) => Math.max(p.limitUp, p.limitDown))) * 1.2;
  const f = chartFrame(pts, 26);
  const slot = f.iw / pts.length;
  const bw = Math.max(9, Math.min(30, slot * 0.26));
  let body = "";

  [0, 0.25, 0.5, 0.75, 1].forEach((r) => {
    const v = maxV * r;
    const y = f.y(v, maxV);
    body += `<line x1="${f.L}" y1="${y.toFixed(1)}" x2="${f.L + f.iw}" y2="${y.toFixed(1)}"
      stroke="var(--tertiary)" stroke-width="0.6" stroke-dasharray="3 4"></line>`;
    body += `<text x="${f.L - 10}" y="${(y + 4).toFixed(1)}" text-anchor="end"
      style="fill:var(--secondary);font-size:11px">${Math.round(v)}</text>`;
  });

  pts.forEach((p, i) => {
    const cx = f.x(i);
    const upH = Math.max(1.5, f.y(0, maxV) - f.y(p.limitUp, maxV));
    const dnH = Math.max(1.5, f.y(0, maxV) - f.y(p.limitDown, maxV));
    const base = f.y(0, maxV);
    body += `<rect x="${(cx - bw - 2).toFixed(1)}" y="${(base - upH).toFixed(1)}"
      width="${bw}" height="${upH.toFixed(1)}" rx="3" style="fill:var(--up)"
      class="trend-bar"></rect>`;
    body += `<rect x="${(cx + 2).toFixed(1)}" y="${(base - dnH).toFixed(1)}"
      width="${bw}" height="${dnH.toFixed(1)}" rx="3" style="fill:var(--down)"
      class="trend-bar"></rect>`;
    body += `<text x="${(cx - bw / 2 - 2).toFixed(1)}" y="${(base - upH - 6).toFixed(1)}"
      text-anchor="middle" style="fill:var(--up);font-size:11px;font-weight:600">${p.limitUp}</text>`;
    if (p.limitDown > 0) {
      body += `<text x="${(cx + bw / 2 + 2).toFixed(1)}" y="${(base - dnH - 6).toFixed(1)}"
        text-anchor="middle" style="fill:var(--down);font-size:11px;font-weight:600">${p.limitDown}</text>`;
    }
    body += `<text x="${cx.toFixed(1)}" y="${(base + 20).toFixed(1)}" text-anchor="middle"
      style="fill:var(--secondary);font-size:11px">${mmdd(p.date)}</text>`;
  });

  svg.innerHTML = body;

  const last = pts[pts.length - 1];
  if (sub) {
    sub.textContent = `最新涨停 ${last.limitUp} 家 / 跌停 ${last.limitDown} 家 · 最高 ${last.maxBoards} 板`;
  }
  if (legend) {
    legend.innerHTML = `<span class="lg-item"><i class="lg-swatch" style="background:var(--up)"></i>涨停家数</span>`
      + `<span class="lg-item"><i class="lg-swatch" style="background:var(--down)"></i>跌停家数</span>`;
  }
}

/* ---------------- 3. 板块涨跌分布 ---------------- */

function renderBoardBars() {
  const box = $("chartBoards");
  const sub = $("trendBoardSub");
  if (!box) return;

  const boards = (STATE.data && STATE.data.boards) || [];
  if (!boards.length) {
    box.innerHTML = `<p class="feed-empty">当日无板块数据</p>`;
    return;
  }

  const sorted = [...boards].sort((a, b) => b.pct - a.pct);
  const head = sorted.slice(0, 6);
  const tail = sorted.slice(-5).reverse();
  const rows = [...head, { divider: true }, ...tail];
  const maxAbs = Math.max(0.5, ...boards.map((b) => Math.abs(b.pct)));

  box.innerHTML = rows.map((b) => {
    if (b.divider) return `<div class="bb-divider"><span>跌幅榜</span></div>`;
    const w = (Math.abs(b.pct) / maxAbs) * 50;
    const up = b.pct >= 0;
    const fill = up
      ? `left:50%;width:${w.toFixed(2)}%;background:var(--up)`
      : `right:50%;width:${w.toFixed(2)}%;background:var(--down)`;
    return `
      <div class="board-bar" title="${escapeHTML(b.name)} · 换手 ${b.turnover}% · 涨 ${b.advancers} / 跌 ${b.decliners}">
        <span class="bb-name">${escapeHTML(b.name)}</span>
        <span class="bb-track"><i class="bb-zero"></i><i class="bb-fill" style="${fill}"></i></span>
        <span class="bb-val ${up ? "up" : "down"}">${up ? "+" : ""}${Number(b.pct).toFixed(2)}%</span>
      </div>`;
  }).join("");

  const st = (STATE.data && STATE.data.boardStats) || {};
  if (sub) {
    sub.textContent = `${boards.length} 个行业 · 涨 ${st.up || 0} / 跌 ${st.down || 0} · 分化 ${st.spread} 个百分点`;
  }
}

/* ---------------- 4. 龙虎榜资金结构 ---------------- */

function renderMoneyChart() {
  const box = $("chartMoney");
  const sub = $("trendMoneySub");
  const legend = $("chartMoneyLegend");
  if (!box) return;

  const seats = (STATE.data && STATE.data.seats) || [];
  const rows = MONEY_KINDS.map((k) => {
    const hit = seats.filter((s) => s.kind === k.kind);
    return {
      ...k,
      buy: hit.reduce((a, s) => a + s.buy, 0),
      sell: hit.reduce((a, s) => a + s.sell, 0),
      net: hit.reduce((a, s) => a + s.net, 0),
      n: hit.length,
    };
  });

  const maxAbs = Math.max(0.2, ...rows.map((r) => Math.abs(r.net)));

  let out = rows.map((r) => {
    const w = (Math.abs(r.net) / maxAbs) * 50;
    const pos = r.net >= 0;
    const fill = pos
      ? `left:50%;width:${w.toFixed(2)}%;background:${r.color}`
      : `right:50%;width:${w.toFixed(2)}%;background:var(--down)`;
    return `
      <div class="flow-row">
        <span class="fr-name">${r.name}<i>${r.n} 席</i></span>
        <span class="bb-track"><i class="bb-zero"></i><i class="bb-fill" style="${fill}"></i></span>
        <span class="bb-val ${pos ? "up" : "down"}">${pos ? "+" : "−"}${fmtNum(Math.abs(r.net))} 亿</span>
      </div>`;
  }).join("");

  // 三类结构之后补「净买前三营业部」：金额结构只说明钱去了哪类席位，
  // 具体是谁买的还得落到席位名上，点一下就能联动到龙虎榜。
  const top3 = seats
    .filter((x) => x.kind === "营业部" && x.net > 0)
    .sort((a, b) => b.net - a.net)
    .slice(0, 3);
  if (top3.length) {
    out += `<div class="bb-divider"><span>净买前三营业部</span></div>`;
    out += top3.map((x) => `
      <button class="flow-seat" type="button" data-seat="${escapeHTML(x.seat)}">
        <span class="fs-name">${escapeHTML(x.seat)}</span>
        <span class="fs-net up">+${fmtNum(x.net)} 亿</span>
      </button>`).join("");
  }
  box.innerHTML = out;

  box.querySelectorAll(".flow-seat").forEach((btn) => {
    btn.addEventListener("click", () => jumpToDragon(btn.dataset.seat, btn.dataset.seat));
  });

  const total = rows.reduce((a, r) => a + r.net, 0);
  const buyAll = rows.reduce((a, r) => a + r.buy, 0);
  const sellAll = rows.reduce((a, r) => a + r.sell, 0);
  if (sub) {
    sub.textContent = `当日样本合计净 ${total >= 0 ? "+" : "−"}${fmtNum(Math.abs(total))} 亿`
      + ` · 买 ${fmtNum(buyAll)} / 卖 ${fmtNum(sellAll)} 亿`;
  }
  if (legend) {
    legend.innerHTML = rows.map(
      (r) => `<span class="lg-item"><i class="lg-swatch" style="background:${r.color}"></i>${r.name}</span>`
    ).join("");
  }
}

function renderTrend() {
  renderFearChart();
  renderLimitChart();
  renderBoardBars();
  renderMoneyChart();
}

function initTrendRange() {
  const box = $("trendRange");
  if (!box) return;
  box.querySelectorAll(".seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      box.querySelectorAll(".seg-btn").forEach((b) => {
        b.classList.remove("is-active");
        b.setAttribute("aria-selected", "false");
      });
      btn.classList.add("is-active");
      btn.setAttribute("aria-selected", "true");
      STATE.trendRange = btn.dataset.range;
      renderTrend();
    });
  });
}

/* ============================================================
 *  游资 ↔ 龙虎榜 联动
 *  STATE.linkSeat 存「席位关键词」或「游资名」，龙虎榜据此高亮。
 *  方向是双向的：游资卡片/活跃席位表点过来高亮龙虎榜行；
 *  龙虎榜行点「关联游资」回跳游资卡片。
 * ============================================================ */

function seatProfiles() {
  return (STATE.data && STATE.data.seatStocks && STATE.data.seatStocks.profiles) || [];
}

/** 席位名归一化：前端拿到的是 derive 后的缩写（「国泰海通证券上海长宁区江苏路」），
 *  档案里是全名（「…股份有限公司上海长宁区江苏路证券营业部」），
 *  直接 includes 一定对不上，必须先削掉这些固定后缀/中缀。 */
function normSeatName(name) {
  return String(name || "")
    .replace(/股份有限公司|有限责任公司|有限公司/g, "")
    .replace(/证券营业部|营业部/g, "")
    .replace(/证券/g, "")
    .replace(/\s+/g, "");
}

/** 龙虎榜的席位名 → 命中的游资档案（无则 null） */
function profileOfSeat(seatName) {
  const name = normSeatName(seatName);
  if (!name) return null;
  return seatProfiles().find((p) => (p.seats || []).some((s) => {
    const key = normSeatName(s);
    return key && (key.includes(name) || name.includes(key));
  })) || null;
}

function setLinkSeat(key, label) {
  STATE.linkSeat = key ? { key, label: label || key } : null;
  renderDragon(STATE.dragonFilter);
  renderHotSeat();
  const hint = $("dragonLinkHint");
  if (hint) {
    if (STATE.linkSeat) {
      hint.hidden = false;
      hint.innerHTML = `已联动筛选：<b>${escapeHTML(STATE.linkSeat.label)}</b>`
        + ` <button class="link-clear" type="button" id="linkClear">清除</button>`;
      const btn = $("linkClear");
      if (btn) btn.addEventListener("click", () => setLinkSeat(null));
    } else {
      hint.hidden = true;
      hint.innerHTML = "";
    }
  }
}

/** 从任意元素跳到龙虎榜并高亮 */
function jumpToDragon(key, label) {
  setLinkSeat(key, label);
  const sec = document.getElementById("dragon");
  if (sec) sec.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ---------------- 活跃席位表 ---------------- */

function shortSeat(name) {
  return String(name || "")
    .replace("股份有限公司", "")
    .replace("有限责任公司", "")
    .replace("证券营业部", "")
    .replace("营业部", "")
    .replace(/^(.{2,6}证券)/, "$1 ");
}

function renderHotSeat() {
  const body = $("hotSeatBody");
  if (!body) return;
  const ss = (STATE.data && STATE.data.seatStocks) || {};
  const rows = ss.topSeats || [];
  const sub = $("hotSeatSub");
  const note = $("seatNote");

  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="7" class="table-empty">未取到席位明细</td></tr>`;
    return;
  }

  body.innerHTML = rows.map((r) => {
    const up = r.net >= 0;
    const stocks = (r.stocks || []).slice(0, 3).map(
      (s) => `<span class="seat-stock" title="买入 ${fmtNum(s.buy)} 亿 · 上榜 ${s.times} 次 · 最近 ${s.lastDate}">${
        escapeHTML(s.name || s.code)}<i>${fmtNum(s.buy, 1)}</i></span>`
    ).join("");
    const on = STATE.linkSeat && (String(r.seat).includes(STATE.linkSeat.key)
      || (r.aliases || []).some((a) => String(a).includes(STATE.linkSeat.key)));
    return `
    <tr data-seat="${escapeHTML(r.seat)}" class="${on ? "is-linked" : ""}">
      <td><span class="t-name">${escapeHTML(shortSeat(r.seat))}</span>
        <span class="t-code">${(r.aliases || []).length > 1 ? "合并 " + r.aliases.length + " 种写法" : "单一席位"}</span></td>
      <td class="t-num ${up ? "up" : "down"}">${up ? "+" : "−"}${fmtNum(Math.abs(r.net))} 亿</td>
      <td class="t-num">${fmtNum(r.buy)} 亿</td>
      <td class="t-num">${fmtNum(r.sell)} 亿</td>
      <td class="t-num">${r.activeDays} 天</td>
      <td class="t-num">${r.stockCount} 只</td>
      <td class="cell-note">${stocks || "—"}</td>
    </tr>`;
  }).join("");

  body.querySelectorAll("tr[data-seat]").forEach((tr) => {
    tr.addEventListener("click", () => jumpToDragon(tr.dataset.seat, shortSeat(tr.dataset.seat)));
  });

  if (sub) {
    const w = ss.window || {};
    sub.textContent = `${w.from || "—"} ~ ${w.to || "—"} · ${w.days || 0} 个交易日 · 按净买入排序`;
  }
  if (note) {
    note.textContent = "口径：东财龙虎榜买卖席位明细（RPT_BILLBOARD_DAILYDETAILSBUY/SELL），"
      + "按席位合并同名写法后聚合；可转债不计入。机构专用与沪深股通不属于游资，已排除。点任意行可联动高亮下方龙虎榜。";
  }
}

/* ---------------- 游资卡片里的真实买入个股 ---------------- */

function openSeatStock(profileName, code) {
  const panel = $("hotStockPanel");
  if (!panel) return;
  const p = seatProfiles().find((x) => x.name === profileName);
  const st = p && (p.stocks || []).find((s) => s.code === code);
  if (!st) return;

  setText("hotStockTitle", `${profileName} · ${st.name || code} ${code}`);

  const metrics = $("hotStockMetrics");
  if (metrics) {
    metrics.innerHTML = [
      { label: "区间买入", value: `${fmtNum(st.buy)} 亿` },
      { label: "区间卖出", value: `${fmtNum(st.sell)} 亿` },
      { label: "净额", value: `${st.net >= 0 ? "+" : "−"}${fmtNum(Math.abs(st.net))} 亿`, cls: pctClass(st.net) },
      { label: "上榜次数", value: `${st.times} 次` },
      { label: "最近上榜", value: st.lastDate || "—" },
      { label: "最近收盘", value: st.close === null ? "—" : fmtNum(st.close),
        cls: st.pct === null ? "" : pctClass(st.pct) },
    ].map((m) => `<div class="metric-item"><p class="m-label">${escapeHTML(m.label)}</p>
      <p class="m-value ${m.cls || ""}">${escapeHTML(String(m.value))}</p></div>`).join("");
  }

  const days = $("hotStockDays");
  const daily = st.daily || [];
  if (days) {
    if (!daily.length) {
      days.innerHTML = `<p class="feed-empty">该席位上榜明细缺失</p>`;
    } else {
      const maxV = Math.max(0.01, ...daily.map((d) => Math.max(d.buy, d.sell)));
      days.innerHTML = daily.map((d) => {
        const bw = (d.buy / maxV) * 100;
        const sw = (d.sell / maxV) * 100;
        return `
        <div class="day-row">
          <span class="day-date">${escapeHTML(d.d)}</span>
          <span class="day-bars">
            <i class="day-bar buy" style="width:${bw.toFixed(1)}%"></i>
            <i class="day-bar sell" style="width:${sw.toFixed(1)}%"></i>
          </span>
          <span class="day-val"><b class="up">${fmtNum(d.buy, 2)}</b> / <b class="down">${fmtNum(d.sell, 2)}</b> 亿</span>
        </div>`;
      }).join("");
    }
  }

  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function initSeatStockPanel() {
  const close = $("hotStockClose");
  if (close) close.addEventListener("click", () => { $("hotStockPanel").hidden = true; });
}

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

const FEED_CATS = ["全部", "公司", "科技", "市场", "宏观", "国际", "社会", "产业"];

function renderFeedCats() {
  const box = $("feedCatFilter");
  if (!box) return;
  const all = (STATE.feed && STATE.feed.cls) || [];
  const counts = {};
  all.forEach((it) => { counts[it.cat || "市场"] = (counts[it.cat || "市场"] || 0) + 1; });
  const cats = FEED_CATS.filter((c) => c === "全部" || counts[c]);
  box.innerHTML = cats.map((c) => {
    const num = c === "全部" ? all.length : counts[c];
    const on = STATE.feedCat === c;
    return `<button class="track-chip ${on ? "is-active" : ""}" data-cat="${escapeHTML(c)}"
      role="option" aria-selected="${on}">${escapeHTML(c)}<i>${num}</i></button>`;
  }).join("");

  box.querySelectorAll(".track-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      STATE.feedCat = btn.dataset.cat;
      renderFeedCats();
      renderFeedCls();
    });
  });

  const note = $("feedFilterNote");
  if (note) {
    const drop = (STATE.feed && STATE.feed.clsDropped) || { count: 0, rawCount: all.length };
    const reasons = (drop.reasons || []).map((r) => `${r.reason} ${r.count}`).join(" · ");
    note.innerHTML = drop.count
      ? `已自动剔除 <b>${drop.count}</b> 条（${escapeHTML(reasons || "非财经")}）· 只收录财经 / 科技 / 社会重大影响`
      : "只收录财经 / 科技 / 社会重大影响，纯天气与非财经快讯不上站";
  }
}

function renderFeedCls() {
  const box = $("feedClsList");
  if (!box) return;
  const all = (STATE.feed && STATE.feed.cls) || [];
  const items = STATE.feedCat === "all" || STATE.feedCat === "全部"
    ? all
    : all.filter((it) => (it.cat || "市场") === STATE.feedCat);
  setText("feedClsCount", String(items.length));

  if (!items.length) {
    box.innerHTML = `<p class="feed-empty">该主题下暂无快讯，切换分类试试。</p>`;
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
      const cat = it.cat ? `<span class="feed-cat c-${escapeHTML(it.cat)}">${escapeHTML(it.cat)}</span>` : "";
      return `
      <article class="feed-item ${it.star >= 1 ? "is-key" : ""}">
        <div class="feed-item-head">
          <time class="feed-time">${escapeHTML(it.time || "--:--")}</time>
          ${cat}
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

  renderFeedCats();
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

  // 多日序列与候选池全量日线：失败不阻断首屏，图表降级为「暂无历史序列」
  STATE.history = await loadHistory();
  STATE.poolKline = await loadPoolKline();
  STATE.rules = await loadRules();

  renderMeta();
  initGauge();
  initSectorSort();
  renderSectors();
  initPoolTabs();
  renderPool();
  initDragonFilter();
  renderDragon("all");
  renderHot();
  renderHotSeat();
  initSeatStockPanel();
  renderFearBreakdown();
  renderIndices();
  initTrendRange();
  renderTrend();
  initScreen();            // 内部会建条件、渲染筛选表并重建焦点池与个股分析
  initFeedFilter();
  renderFeed();
  initNavHighlight();
}

document.addEventListener("DOMContentLoaded", boot);
