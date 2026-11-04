#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把源项目的 index.html 改造成数据驱动版。

改动清单：
1. header：live-dot → #liveDot + #liveText（数据源状态）
2. 新增 #offlineBanner（离线提示）
3. 新增「涨停 / 跌停池」区块（#poolFilter / #poolBody）
4. 个股图表：加 #chartRange 区间标注
5. 恐慌指数：加 #fearRaw 原始读数组
6. 页脚：加 #dataDate / #dataSource 数据来源
7. 静态占位数字全部清空（改由 JS 填），文案改为真实口径
8. 移除 AI 生成标记
"""

from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = Path(
    r"C:/Users/zhouy/XiaomiMiMoProjects/.mimo-sessions/2026-09-29/"
    r"帮我做一个股票分析网站，采用苹果极简风格，包含每天行业板块分析，个股分析，龙虎榜/index.html"
)
OUT = HERE / "index.html"


def main() -> None:
    html = SRC.read_text(encoding="utf-8").replace("\r\n", "\n")

    # 逐条 (old, new)，命中数必须恰好为 1，否则报错
    patches: list[tuple[str, str]] = []

    # ---------- 1. 移除 AI 生成标记 ----------
    patches.append((
        '<p data-aigc-mark="1" style="margin:0 0 8px;font-size:10px;line-height:1.3;color:#6b6560;font-weight:400;">AI生成</p>\n\n',
        "",
    ))

    # ---------- 2. header 数据源状态 ----------
    patches.append((
        """      <div class="header-meta">
        <span class="live-dot" aria-hidden="true"></span>
        <time id="headerDate">2026-03-18</time>
      </div>""",
        """      <div class="header-meta">
        <span class="live-dot" id="liveDot" aria-hidden="true"></span>
        <span class="live-text" id="liveText">加载中</span>
        <time id="headerDate">—</time>
      </div>""",
    ))

    # ---------- 3. 离线提示条 ----------
    patches.append((
        "  <main>\n",
        """  <div class="offline-banner" id="offlineBanner" hidden>
    未能读取本地行情快照，当前展示的是<b>离线样本</b>，数字不代表真实盘面。
  </div>

  <main>
""",
    ))

    # ---------- 4. 概览文案 ----------
    patches.append((
        """          <p class="hero-desc">
            行业轮动、个股异动、龙虎席位与游资动向，浓缩为一屏可读的信号。
            今日市场情绪由恐慌指数给出第一眼判断。
          </p>""",
        """          <p class="hero-desc">
            行业轮动、涨停梯队、龙虎席位与游资动向，浓缩为一屏可读的信号。
            全部数据取自当日收盘快照，恐慌指数由五项盘面读数合成。
          </p>""",
    ))

    # ---------- 5. 恐慌指数卡：清空占位 ----------
    patches.append((
        '<span class="badge badge-warn" id="fearBadge">偏高</span>',
        '<span class="badge badge-warn" id="fearBadge">—</span>',
    ))
    patches.append((
        '<svg class="gauge" viewBox="0 0 240 240" role="img" aria-label="恐慌指数 68，偏高">',
        '<svg class="gauge" viewBox="0 0 240 240" role="img" aria-label="恐慌指数仪表">',
    ))
    patches.append((
        '<div class="gauge-value" id="fearValue">68</div>',
        '<div class="gauge-value" id="fearValue">—</div>',
    ))
    patches.append((
        '<p class="fear-caption" id="fearCaption">避险情绪升温，短线波动加大，控制仓位为先。</p>',
        '<p class="fear-caption" id="fearCaption">正在读取盘面数据…</p>',
    ))

    # ---------- 6. 指数卡片：清空占位（由 JS 渲染） ----------
    patches.append((
        """        <div class="stat-row" id="marketStats">
          <article class="stat-card">
            <p class="stat-label">上证指数</p>
            <p class="stat-value">3,248.16</p>
            <p class="stat-change up">+18.42 · +0.57%</p>
          </article>
          <article class="stat-card">
            <p class="stat-label">深证成指</p>
            <p class="stat-value">10,287.43</p>
            <p class="stat-change up">+62.10 · +0.61%</p>
          </article>
          <article class="stat-card">
            <p class="stat-label">创业板指</p>
            <p class="stat-value">2,056.88</p>
            <p class="stat-change down">−14.25 · −0.69%</p>
          </article>
          <article class="stat-card">
            <p class="stat-label">两市成交</p>
            <p class="stat-value">1.08<span class="unit">万亿</span></p>
            <p class="stat-change up">较昨日放量 8.2%</p>
          </article>
        </div>""",
        """        <div class="stat-row" id="marketStats"></div>""",
    ))

    # ---------- 7. 板块详情：清空占位 ----------
    patches.append((
        """            <p class="panel-kicker">今日领涨板块</p>
            <h3 class="sector-detail-title" id="sectorDetailTitle">电子 · 半导体</h3>
            <p class="sector-detail-desc" id="sectorDetailDesc">
              存储涨价预期升温，设备与材料端同步走强；北向资金连续三日净买入。
            </p>
            <ul class="leader-list" id="sectorLeaders"></ul>
            <div class="mini-metrics">
              <div>
                <p class="mini-label">板块涨幅</p>
                <p class="mini-value up" id="sectorChange">+2.84%</p>
              </div>
              <div>
                <p class="mini-label">主力净流入</p>
                <p class="mini-value" id="sectorFlow">+42.6 亿</p>
              </div>
              <div>
                <p class="mini-label">上涨家数</p>
                <p class="mini-value" id="sectorBreadth">126 / 148</p>
              </div>
            </div>""",
        """            <p class="panel-kicker">板块详情</p>
            <h3 class="sector-detail-title" id="sectorDetailTitle">—</h3>
            <p class="sector-detail-desc" id="sectorDetailDesc">正在读取板块数据…</p>
            <ul class="leader-list" id="sectorLeaders"></ul>
            <div class="mini-metrics">
              <div>
                <p class="mini-label">板块涨幅</p>
                <p class="mini-value" id="sectorChange">—</p>
              </div>
              <div>
                <p class="mini-label">板块换手</p>
                <p class="mini-value" id="sectorFlow">—</p>
              </div>
              <div>
                <p class="mini-label">板块内涨停</p>
                <p class="mini-value" id="sectorBreadth">—</p>
              </div>
            </div>""",
    ))

    # ---------- 8. 个股：清空占位 ----------
    patches.append((
        '<p class="section-desc">焦点个股的走势、量价与关键财务指标一览。</p>',
        '<p class="section-desc">焦点个股近 60 个交易日的真实收盘序列、量价与阶段涨跌。</p>',
    ))
    patches.append((
        """              <h3 class="stock-name" id="stockName">中芯国际</h3>
              <p class="stock-code" id="stockCode">688981 · SH · 半导体</p>
            </div>
            <div class="stock-quote">
              <p class="stock-price" id="stockPrice">88.60</p>
              <p class="stock-delta up" id="stockDelta">+3.12 · +3.65%</p>
            </div>""",
        """              <h3 class="stock-name" id="stockName">—</h3>
              <p class="stock-code" id="stockCode">—</p>
            </div>
            <div class="stock-quote">
              <p class="stock-price" id="stockPrice">—</p>
              <p class="stock-delta flat" id="stockDelta">—</p>
            </div>""",
    ))

    # ---------- 9. 图表：加区间标注 ----------
    patches.append((
        """              <div class="chart-legend">
                <span><i class="swatch up"></i>收盘价</span>
                <span><i class="swatch accent"></i>MA20</span>
              </div>""",
        """              <div class="chart-legend">
                <span><i class="swatch up"></i>收盘价</span>
                <span><i class="swatch accent"></i>MA20</span>
                <span class="chart-range" id="chartRange">—</span>
              </div>""",
    ))

    # ---------- 10. 盘面解读：清空占位 ----------
    patches.append((
        """            <p class="stock-note-text" id="stockNote">
              放量突破 20 日均线，MACD 金叉后红柱放大。上方关注前高 92 压力，下方 84 一线为短期多空分界。机构持仓连续两季提升。
            </p>""",
        """            <p class="stock-note-text" id="stockNote">正在读取行情数据…</p>""",
    ))

    # ---------- 11. 筛选：描述与截面日期 ----------
    patches.append((
        '<p class="section-desc">科技赛道中期池：市值 50–300 亿 · 换手 &gt;2.5% · 剔除科创板与 9 开头，叠加景气与加分项。</p>',
        '<p class="section-desc">科技赛道中期观察池：市值 50–300 亿 · 日均换手 &gt;2.5%，叠加景气与加分项。研究底稿，非当日选股结果。</p>',
    ))
    patches.append((
        '<p class="screen-count"><span id="screenCount">39</span> 只候选 · 截面 2026-09-29</p>',
        '<p class="screen-count"><span id="screenCount">0</span> 只候选 · 研究底稿截面 2026-09-29</p>',
    ))

    # ---------- 12. 龙虎榜表头：改为席位维度 ----------
    patches.append((
        """            <p class="section-desc">今日上榜个股、营业部席位与净买入强度。</p>""",
        """            <p class="section-desc">当日上榜席位活跃度：按买卖金额排序，标注北向 / 机构 / 营业部属性。</p>""",
    ))
    patches.append((
        """                <tr>
                  <th>股票</th>
                  <th>涨跌幅</th>
                  <th>净买入</th>
                  <th>上榜原因</th>
                  <th>买一席位</th>
                  <th>风格信号</th>
                </tr>""",
        """                <tr>
                  <th>席位</th>
                  <th>买入</th>
                  <th>卖出</th>
                  <th>净买入</th>
                  <th>成交额</th>
                  <th>风格信号</th>
                </tr>""",
    ))

    # ---------- 13. 新增涨停 / 跌停池区块（插在龙虎榜之前） ----------
    pool_section = """    <!-- ============ 涨停 / 跌停池 ============ -->
    <section class="section" id="pool">
      <div class="container">
        <header class="section-head">
          <div>
            <p class="eyebrow">Limit Pool</p>
            <h2 class="section-title">涨停 · 跌停池</h2>
            <p class="section-desc">当日封板与跌停个股，含连板高度、封单金额、开板次数与题材归因。</p>
          </div>
          <div class="segmented" id="poolFilter" role="tablist" aria-label="池切换">
            <button class="seg-btn is-active" data-pool="up" role="tab" aria-selected="true">涨停池</button>
            <button class="seg-btn" data-pool="down" role="tab" aria-selected="false">跌停池</button>
          </div>
        </header>

        <div class="panel table-panel">
          <div class="table-scroll">
            <table class="data-table pool-table" id="poolTable">
              <thead>
                <tr>
                  <th>股票</th>
                  <th>涨跌幅</th>
                  <th>连板</th>
                  <th>封单</th>
                  <th>开板</th>
                  <th>题材</th>
                  <th>催化 / 说明</th>
                </tr>
              </thead>
              <tbody id="poolBody"></tbody>
            </table>
          </div>
          <p class="table-foot">数据来源：通达信（tdx-connector）· 涨停池为当日封板样本 · 仅作研究参考，不构成投资建议</p>
        </div>
      </div>
    </section>

"""
    patches.append((
        '    <!-- ============ 龙虎榜 ============ -->',
        pool_section + '    <!-- ============ 龙虎榜 ============ -->',
    ))

    # ---------- 14. 恐慌指数：加原始读数组 ----------
    patches.append((
        """            <h3 class="panel-title">今日分项</h3>
            <div class="breakdown-list" id="fearBreakdown"></div>""",
        """            <h3 class="panel-title">今日分项</h3>
            <div class="breakdown-list" id="fearBreakdown"></div>
            <p class="panel-kicker raw-kicker">原始读数</p>
            <div class="fear-raw" id="fearRaw"></div>""",
    ))

    # ---------- 15. 恐慌指数说明：改为真实算法口径 ----------
    patches.append((
        '<p class="section-desc">综合波动率、涨跌家数、跌停扩散与资金避险行为的合成情绪刻度。</p>',
        '<p class="section-desc">由涨跌家数失衡、跌停扩散度、连板高度、赚钱效应、指数振幅五项加权合成，全部取自当日收盘快照。</p>',
    ))
    patches.append((
        """            <p class="fear-note">
              恐慌指数不是买卖信号，而是仓位与预期管理的仪表盘。极端读数往往对应中期拐点，而不是次日涨跌。
            </p>""",
        """            <p class="fear-note">
              权重设置：涨跌家数失衡 30% · 跌停扩散度 22% · 连板高度 18% · 赚钱效应 15% · 指数振幅 15%。
              五项均统一映射到「越高越恐慌」方向后加权。恐慌指数不是买卖信号，而是仓位与预期管理的仪表盘。
            </p>""",
    ))

    # ---------- 16. 页脚：数据来源 ----------
    patches.append((
        """      <p class="footer-disclaimer">
        本站数据为演示用模拟样本，不构成任何投资建议。市场有风险，决策需独立。
        页面仅供学习与研究使用，与任何券商、交易所无隶属关系。
      </p>
      <p class="footer-copy">© 2026 潮汐 Tide · Designed with restraint</p>""",
        """      <p class="footer-disclaimer">
        数据来源 <span id="dataSource">—</span> · 交易日 <span id="dataDate">—</span>。
        本站不构成任何投资建议，市场有风险，决策需独立。页面仅供学习与研究使用，与任何券商、交易所无隶属关系。
      </p>
      <p class="footer-copy">© 2026 潮汐 Tide · Designed with restraint</p>""",
    ))

    # ---------- 17. 导航加涨停池入口 ----------
    patches.append((
        '        <a href="#dragon">龙虎榜</a>\n',
        '        <a href="#pool">涨停池</a>\n        <a href="#dragon">龙虎榜</a>\n',
    ))

    # ---------- 18. 游资区块：加「情绪阶段门」面板 ----------
    patches.append((
        '<div class="hot-grid" id="hotGrid"></div>\n',
        '<div class="hot-grid" id="hotGrid"></div>\n\n'
        '        <div class="panel stage-gate-panel">\n'
        '          <div id="stageGate"></div>\n'
        '        </div>\n',
    ))

    # ---------- 19. 游资文案：对齐研究结论 ----------
    patches.append((
        '<p class="section-desc">拆解知名席位的打法偏好、持仓节奏与可复用原则。</p>',
        '<p class="section-desc">基于 25+ 公开源的研究梳理：知名席位近 12 个月活跃度、打法变化，以及二板战法的阶段适配门。</p>',
    ))
    patches.append((
        """            <p class="panel-kicker">可复用原则</p>
            <h3>从席位行为里提炼的四条纪律</h3>""",
        """            <p class="panel-kicker">研究结论</p>
            <h3>二板战法的四条纪律</h3>""",
    ))
    patches.append((
        """            <li>
              <strong>只做主线。</strong>
              大多数高胜率席位的买入集中在当日净流入前二的行业，拒绝「补涨幻想」。
            </li>
            <li>
              <strong>先手优势。</strong>
              首板或突破日介入，持仓平均不超过 5 个交易日；趋势破位立刻离场。
            </li>
            <li>
              <strong>分批进出。</strong>
              大资金拆单是常态：同一席位连续 2–3 日上榜，往往对应建仓而非出货。
            </li>
            <li>
              <strong>情绪择时。</strong>
              恐慌指数高于 70 时降杠杆，低于 35 时敢于进攻——情绪是仓位的开关，不是选股的依据。
            </li>""",
        """            <li>
              <strong>阶段优先于选股。</strong>
              「二板定龙」只在修复期→发酵期有效；退潮/冰点期最优动作是空仓（0–1 成）。
              战法的胜负不在战法本身，而在阶段匹配。
            </li>
            <li>
              <strong>「二板长三板」才是扩散。</strong>
              2 板里长出 3 板 = 扩散，出现完整 2-3-4 梯队 = 确认；在此之前打二板只是猜测。
              口诀：看涨停家数，不如看涨停结构。
            </li>
            <li>
              <strong>换手板优于一字板。</strong>
              一字板买不进、抛压后置，连板期换手可能低至 2%。时间窗共识落在 9:35–10:00，
              9:35 前换手不够，即使上板也易炸。
            </li>
            <li>
              <strong>仓位写进情绪表，不写进感觉。</strong>
              单笔最大亏损 ≤ 账户 2%，用止损距离反推股数；单票 ≤ 20%，持仓 ≤ 2 只。
              连续 2 笔亏损或单日回撤 &gt;2% → 至少空仓 1 个交易日。
            </li>""",
    ))

    # 执行
    for i, (old, new) in enumerate(patches, 1):
        n = html.count(old)
        if n != 1:
            raise SystemExit(f"[补丁 {i}] 命中 {n} 次（应为 1）：\n{old[:120]}")
        html = html.replace(old, new, 1)

    # 残留检查：不能再有硬编码的假数字
    for bad in ["3,248.16", "10,287.43", "2,056.88", "1.08<span", "88.60", "中芯国际", "68</div>"]:
        if bad in html:
            raise SystemExit(f"残留占位未清理：{bad}")

    OUT.write_text(html, encoding="utf-8")
    print(f"index.html 已生成：{OUT}")
    print(f"  {len(html)} 字符 / {html.count(chr(10)) + 1} 行")
    print(f"  应用补丁 {len(patches)} 处")


if __name__ == "__main__":
    main()
