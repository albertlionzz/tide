/**
 * 潮汐 · 渲染校验（jsdom）
 *
 * 在 jsdom 里加载真实的 index.html + styles.css + app.js + tide_data.json +
 * screen_pool.json，跑完整 boot()，然后断言关键 DOM 是否被正确填充。
 *
 * 目的：在没有 Chromium 的情况下，等价验证「页面能否真实渲染、有无 JS 报错」。
 */

const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const DIR = __dirname;

const html = fs.readFileSync(path.join(DIR, "index.html"), "utf8");
const appjs = fs.readFileSync(path.join(DIR, "app.js"), "utf8");
const tideData = JSON.parse(fs.readFileSync(path.join(DIR, "tide_data.json"), "utf8"));
const screenPool = JSON.parse(fs.readFileSync(path.join(DIR, "screen_pool.json"), "utf8"));
const feedPath = path.join(DIR, "feed_data.json");
const feedData = fs.existsSync(feedPath)
  ? JSON.parse(fs.readFileSync(feedPath, "utf8"))
  : null;

const errors = [];
const warns = [];

const vc = new VirtualConsole();
vc.on("jsdomError", (e) => errors.push(`jsdomError: ${e.message}`));
vc.on("error", (...a) => errors.push(`console.error: ${a.join(" ")}`));
vc.on("warn", (...a) => warns.push(`console.warn: ${a.join(" ")}`));

// 让 fetch 直接返回本地 JSON
function fakeFetch(url) {
  if (url.includes("tide_data.json")) {
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(tideData) });
  }
  if (url.includes("screen_pool.json")) {
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(screenPool) });
  }
  if (url.includes("feed_data.json")) {
    if (feedData) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(feedData) });
    return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve(null) });
  }
  return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve(null) });
}

const dom = new JSDOM(html, {
  runScripts: "outside-only",
  pretendToBeVisual: true,
  virtualConsole: vc,
  url: "file://" + DIR.replace(/\\/g, "/") + "/index.html",
});

const { window } = dom;
window.fetch = fakeFetch;
// jsdom 不实现 IntersectionObserver，给个空壳
window.IntersectionObserver = class {
  constructor() {}
  observe() {}
  unobserve() {}
  disconnect() {}
};
window.matchMedia = window.matchMedia || (() => ({
  matches: false,
  addEventListener() {},
  removeEventListener() {},
}));

// 跑 app.js
try {
  window.eval(appjs);
} catch (e) {
  errors.push(`app.js 执行异常: ${e.message}`);
}

// 等 DOMContentLoaded + 异步 boot 落定
setTimeout(() => {
  const d = window.document;
  const txt = (id) => (d.getElementById(id)?.textContent || "").trim();
  const htmlOf = (id) => (d.getElementById(id)?.innerHTML || "");
  const count = (sel) => d.querySelectorAll(sel).length;
  const txt0 = (el) => ((el && el.textContent) || "").trim();

  const checks = [];
  const assert = (name, cond, got) => checks.push({ name, ok: !!cond, got: String(got).slice(0, 90) });

  // ---- 元信息 ----
  assert("headerDate 填入交易日", txt("headerDate") === "2026-11-06", txt("headerDate"));
  assert("liveText 显示已收盘时间", txt("liveText").includes("已收盘"), txt("liveText"));
  assert("dataSource 填入数据源", txt("dataSource").includes("通达信"), txt("dataSource"));
  assert("dataDate 中文化", txt("dataDate").includes("2026 年 11 月 6 日"), txt("dataDate"));
  assert("offlineBanner 保持隐藏", d.getElementById("offlineBanner").hidden === true, d.getElementById("offlineBanner").hidden);

  // ---- 恐慌指数 ----
  assert("fearValue = 79", txt("fearValue") === "79", txt("fearValue"));
  assert("fearBadge = 偏高", txt("fearBadge") === "偏高", txt("fearBadge"));
  assert("fearBreakdown 渲染 5 项", count("#fearBreakdown .breakdown-item") === 5, count("#fearBreakdown .breakdown-item"));
  assert("fearRaw 渲染 6 项读数", count("#fearRaw > div") === 6, count("#fearRaw > div"));
  assert("gaugeArc 有描边色", (d.getElementById("gaugeArc").style.stroke || "").length > 0, d.getElementById("gaugeArc").style.stroke);

  // ---- 指数 ----
  assert("marketStats 4 张卡片", count("#marketStats .stat-card") === 4, count("#marketStats .stat-card"));
  assert("指数含上证 3649.18", htmlOf("marketStats").includes("3,649.18"), "见 innerHTML");
  assert("成交额显示万亿", htmlOf("marketStats").includes("万亿"), "见 innerHTML");

  // ---- 板块 ----
  assert("sectorList 27 个板块", count("#sectorList .sector-item") === 27, count("#sectorList .sector-item"));
  assert("板块详情标题非空", txt("sectorDetailTitle") !== "—" && txt("sectorDetailTitle").length > 0, txt("sectorDetailTitle"));
  assert("板块详情涨幅已填", txt("sectorChange") !== "—", txt("sectorChange"));

  // ---- 个股 ----
  assert("stockPicker 5 只", count("#stockPicker .stock-chip") === 5, count("#stockPicker .stock-chip"));
  assert("stockName 非空", txt("stockName").length > 0 && txt("stockName") !== "—", txt("stockName"));
  assert("stockMetrics 6 项", count("#stockMetrics .metric-item") === 6, count("#stockMetrics .metric-item"));
  assert("stockChart 有路径", count("#stockChart path") >= 3, count("#stockChart path"));
  assert("chartRange 有区间", txt("chartRange").includes("→"), txt("chartRange"));
  assert("stockNote 为真实解读", txt("stockNote").includes("%"), txt("stockNote").slice(0, 40));

  // ---- 涨停池 ----
  assert("poolBody 渲染 20 行", count("#poolBody tr") === 20, count("#poolBody tr"));
  assert("池内出现闽发铝业 5 板", htmlOf("poolBody").includes("闽发铝业") && htmlOf("poolBody").includes("5 板"), "见 innerHTML");

  // ---- 龙虎榜 ----
  assert("dragonBody 渲染 10 行", count("#dragonBody tr") === 10, count("#dragonBody tr"));
  assert("龙虎榜含北向席位", htmlOf("dragonBody").includes("北向"), "见 innerHTML");

  // ---- 游资 / 阶段门 ----
  assert("hotGrid 4 张卡片", count("#hotGrid .hot-card") === 4, count("#hotGrid .hot-card"));
  assert("游资卡含活跃度标签", htmlOf("hotGrid").includes("近乎停手"), "见 innerHTML");
  assert("stageGate 渲染 4 道关卡", count("#stageGate .gate-item") === 4, count("#stageGate .gate-item"));
  assert("stageGate 给出阶段判定", htmlOf("stageGate").includes("当前判定"), "见 innerHTML");

  // ---- 筛选 ----
  assert("screenBody 渲染候选", count("#screenBody tr") >= 10, count("#screenBody tr"));
  assert("screenCount 与行数一致", Number(txt("screenCount")) === count("#screenBody tr"), `${txt("screenCount")} vs ${count("#screenBody tr")}`);
  assert("track-chip 已生成", count("#screenTrackFilter .track-chip") === 7, count("#screenTrackFilter .track-chip"));

  // ---- 5日涨幅不再串列 ----
  const firstRow = d.querySelector("#screenBody tr");
  const cells = firstRow ? [...firstRow.querySelectorAll("td")].map((td) => td.textContent.trim()) : [];
  assert("筛选行含换手与5日涨幅两列", cells.length === 10, cells.length);
  assert(
    "换手 != 5日涨幅（列定位已修）",
    cells[5] !== cells[6],
    `换手=${cells[5]} 5日=${cells[6]}`
  );

  // ---- 资讯 · 财联社 + 雪球 ----
  assert("资讯板块存在", count("#feed") === 1, count("#feed"));
  assert("财联社快讯列表已渲染", count("#feedClsList .feed-item") >= 5, count("#feedClsList .feed-item"));
  assert("财联社条数与计数一致",
    Number(txt("feedClsCount")) === count("#feedClsList .feed-item"),
    `${txt("feedClsCount")} vs ${count("#feedClsList .feed-item")}`);
  assert("雪球帖子列表已渲染", count("#feedXqList .feed-item") >= 5, count("#feedXqList .feed-item"));
  assert("雪球条数与计数一致",
    Number(txt("feedXqCount")) === count("#feedXqList .feed-item"),
    `${txt("feedXqCount")} vs ${count("#feedXqList .feed-item")}`);
  assert("财联社快讯含时间戳", /^\d{1,2}:\d{2}/.test((d.querySelector("#feedClsList .feed-time") || {}).textContent || ""),
    (d.querySelector("#feedClsList .feed-time") || {}).textContent);
  assert("雪球帖子含作者名",
    (txt0(d.querySelector("#feedXqList .post-author")) || "").length > 0,
    txt0(d.querySelector("#feedXqList .post-author")));
  assert("雪球正文高亮了个股标签", count("#feedXqList .sym-tag") >= 1, count("#feedXqList .sym-tag"));
  assert("被讨论个股卡片已渲染", count("#buzzGrid .buzz-card") >= 1, count("#buzzGrid .buzz-card"));
  assert("话题标签云已渲染", count("#topicCloud .topic-chip") >= 1, count("#topicCloud .topic-chip"));
  assert("重要快讯带 is-key 标记", count("#feedClsList .feed-item.is-key") >= 1, count("#feedClsList .feed-item.is-key"));

  // ---- 输出 ----
  const pass = checks.filter((c) => c.ok).length;
  console.log(`\n潮汐 · 渲染校验  ${pass}/${checks.length} 通过\n${"─".repeat(64)}`);
  checks.forEach((c) => {
    console.log(`  ${c.ok ? "✓" : "✗"} ${c.name}${c.ok ? "" : `   → ${c.got}`}`);
  });

  if (errors.length) {
    console.log(`\n${"─".repeat(64)}\nJS 错误 (${errors.length}):`);
    errors.forEach((e) => console.log(`  ! ${e}`));
  } else {
    console.log(`\n${"─".repeat(64)}\nJS 运行时错误：无`);
  }

  const bootWarns = warns.filter((w) => !/Not implemented|Could not parse CSS/.test(w));
  if (bootWarns.length) {
    console.log(`\n警告 (${bootWarns.length}):`);
    bootWarns.forEach((w) => console.log(`  · ${w}`));
  }

  console.log(`\n结论：${pass === checks.length && errors.length === 0 ? "全部通过 ✓" : "存在失败项 ✗"}`);
  process.exit(pass === checks.length && errors.length === 0 ? 0 : 1);
}, 900);
