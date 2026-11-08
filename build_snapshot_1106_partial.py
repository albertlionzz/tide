#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组装 2026-11-06 的**分片**快照（raw_snapshot_1106_partial.json）。

⚠️ 这是分片文件，不是可直接喂给 derive.py 的完整快照。
   2026-11-07 更新时，通达信 MCP 在抓完指数 / 涨停池 / 跌停池 / 半导体板块后
   触发配额上限（"Your usage quota has been reached"），以下数据缺失：
     - 另外 26 个行业板块
     - 龙虎榜席位活跃度（seatActivity）
     - 股票活跃度（stockListActivity）
     - 观察池 5 只实时价与 60 日 K 线
   因此本文件仅作真实数据的落盘留档，配额恢复后补齐再合并。
   **禁止**用它覆盖 raw_snapshot.json —— 否则底板块/龙虎榜/观察池三块会大面积空白。

11-06 行情背景：三大指数全线下跌（上证 -1.10% / 深成指 -2.57% / 创业板指 -3.96%），
与 11-04 的普涨完全相反；涨停仅 33 家（11-04 为 121 家），最高 5 连板，
跌停 15 家且全部为首次跌停。主线从半导体切到光伏储能 + 传媒短剧。
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "raw_snapshot_1106_partial.json"

# ============================================================
# 1) 三大指数（2026-11-06 收盘，tdx_quotes 实抓）
#    约定：close 字段 = 昨收（HQInfo.Close），now = 收盘价
# ============================================================
indices = [
    {
        "code": "000001", "setcode": "1", "name": "上证指数",
        "close": 3689.91089, "open": 3612.14697, "high": 3660.48608, "low": 3610.19775,
        "now": 3649.17749, "prevClose": 3689.91089,
        "amount": 772634575000, "volume": 477042561, "turnoverRate": 0.98107934,
        "advancers": 381, "decliners": 1936, "unchanged": 40, "pe": 16.6084,
    },
    {
        "code": "399001", "setcode": "0", "name": "深证成指",
        "close": 11471.0938, "open": 11044.5605, "high": 11303.2217, "low": 11044.5605,
        "now": 11175.7803, "prevClose": 11471.0938,
        "amount": 885159887000, "volume": 569400962, "turnoverRate": 2.30675364,
        "advancers": 344, "decliners": 2547, "unchanged": 47, "pe": 35.0518,
    },
    {
        "code": "399006", "setcode": "0", "name": "创业板指",
        "close": 2566.80298, "open": 2438.71509, "high": 2506.9668, "low": 2438.71509,
        "now": 2465.23193, "prevClose": 2566.80298,
        "amount": 444017082000, "volume": 189023617, "turnoverRate": 3.31973267,
        "advancers": 125, "decliners": 1280, "unchanged": 6, "pe": 48.3666,
    },
]

# ============================================================
# 2) 行业板块（仅半导体验证过，其余缺失）
# ============================================================
industryBoards = [
    {"code": "881319", "name": "半导体", "pct": -3.08, "turnover": 3.63,
     "advancers": 19, "decliners": 167, "pe": 43.1138},
]

# ============================================================
# 3) 涨停池（全市场 33 家，取 20 行：封单额前 16 + 其余全部 >=3 连板 4 家）
#    连板数取「连续涨停天数」，pattern 取「几天几板」
# ============================================================
limitUpPool = [
    {"code": "002578", "setcode": "0", "name": "闽发铝业", "price": 5.01, "pct": 10.11,
     "boards": 5, "pattern": "5天5板", "boardType": "T字板", "sealAmount": 220341800,
     "firstSealTime": "09:25:00", "openTimes": 1,
     "reason": "光伏.国企改革.国资入股",
     "detail": "优必选Thinker-Sim具身智能仿真平台正式发布；公司位于福建省南安市，为海西板块铝型材龙头，表示将重点拓展机器人等工业铝型材及高端装备铝型材赛道；液冷服务器用铝型材已开始小批量供货；光伏支架和边框等配套产品是主要工业材产品之一"},
    {"code": "603366", "setcode": "1", "name": "日出东方", "price": 8.99, "pct": 10.04,
     "boards": 1, "pattern": "1天1板", "boardType": "", "sealAmount": 192785200,
     "firstSealTime": "09:45:11", "openTimes": 0,
     "reason": "储能.光伏",
     "detail": "海外选举落地或影响新能源政策；第三季度中国新增光伏装机同比增长约38%；公司光伏业务主要为分布式户用光伏和分布式工商业光伏，空气能热泵领域技术领先"},
    {"code": "002929", "setcode": "0", "name": "润建股份", "price": 63.11, "pct": 10.01,
     "boards": 2, "pattern": "5天3板", "boardType": "换手板", "sealAmount": 181214100,
     "firstSealTime": "13:05:51", "openTimes": 1,
     "reason": "储能.光伏",
     "detail": "领先的数字化智能运维（AIOps）服务商；为算力中心及IDC提供全生命周期一站式服务，近日签下2亿美元AI词元大单"},
    {"code": "601599", "setcode": "1", "name": "浙文影业", "price": 5.08, "pct": 9.96,
     "boards": 5, "pattern": "5天5板", "boardType": "T字板", "sealAmount": 169470800,
     "firstSealTime": "09:25:01", "openTimes": 1,
     "reason": "短剧游戏.国企改革.纺织制造",
     "detail": "海外市场AI漫剧活跃用户整体已超1.5亿；出品的文旅微短剧《九龙湖之有间客栈》已播出，竖屏微短剧《江南机动队》杀青待播，将继续布局微短剧业务矩阵"},
    {"code": "603381", "setcode": "1", "name": "永臻股份", "price": 20.74, "pct": 10.03,
     "boards": 4, "pattern": "4天4板", "boardType": "一字板", "sealAmount": 146364300,
     "firstSealTime": "09:25:01", "openTimes": 0,
     "reason": "人形机器人.储能",
     "detail": "国家能源局：第三季度中国新增光伏装机同比增长约38%；光伏铝边框绝对龙头，全球市占率超18%，配套隆基、天合光能等国内外头部组件厂商"},
    {"code": "001258", "setcode": "0", "name": "立新能源", "price": 9.57, "pct": 10.00,
     "boards": 1, "pattern": "1天1板", "boardType": "", "sealAmount": 143545200,
     "firstSealTime": "09:34:36", "openTimes": 1,
     "reason": "储能.风电.国企改革",
     "detail": "公司立足新疆，专注风力发电和光伏发电业务；深度卡位疆电外送核心通道，参与第三通道新疆侧配套电源项目建设，项目总装机规模8100MW，持有项目公司13.42%股权"},
    {"code": "600261", "setcode": "1", "name": "阳光照明", "price": 5.40, "pct": 9.98,
     "boards": 2, "pattern": "7天6板", "boardType": "换手板", "sealAmount": 138619600,
     "firstSealTime": "09:30:17", "openTimes": 1,
     "reason": "商业航天.智能家居.光学光电.绿色照明.节能环保",
     "detail": "中国最大的节能灯生产和出口基地之一；以自有资金3000万元参与蓝星光域（上海）航天科技融资，完成后持股1.0989%；后者专注全场景激光通信解决方案，产品用于星间、星地高速率通信"},
    {"code": "002310", "setcode": "0", "name": "东方新能", "price": 2.32, "pct": 9.95,
     "boards": 1, "pattern": "1天1板", "boardType": "", "sealAmount": 121391000,
     "firstSealTime": "09:48:45", "openTimes": 0,
     "reason": "储能.固废处理",
     "detail": "上半年业绩扭亏为盈，重大资产购买标的纳入合并报表；现金收购海城锐海100%股权及电投瑞享80%股权已完成过户，标的资产合计持有815.85MW新能源电站"},
    {"code": "002086", "setcode": "0", "name": "东方海洋", "price": 3.97, "pct": 9.97,
     "boards": 2, "pattern": "13天8板", "boardType": "换手板", "sealAmount": 111474800,
     "firstSealTime": "09:59:27", "openTimes": 2,
     "reason": "医疗器械概念.预制菜.冷链物流.基因概念.渔业",
     "detail": "《广东省扩大消费实施方案》印发；公司主要从事海水苗种繁育、养殖与水产品加工，产品包括调理食品系列、冷冻鱼片系列、海参系列"},
    {"code": "002889", "setcode": "0", "name": "东方嘉盛", "price": 14.91, "pct": 10.04,
     "boards": 1, "pattern": "1天1板", "boardType": "换手板", "sealAmount": 91180600,
     "firstSealTime": "10:22:18", "openTimes": 1,
     "reason": "跨境电商.人工智能.电商概念.智能物流.机器人概念.光刻机",
     "detail": "与国际头部芯片光刻机厂商合作，在深圳建设寄售维修保税仓库，为华南片区集成电路制造企业提供光刻机寄售维修服务；国内领先的一体化供应链服务商"},
    {"code": "600371", "setcode": "1", "name": "万向德农", "price": 12.39, "pct": 10.04,
     "boards": 1, "pattern": "3天2板", "boardType": "换手板", "sealAmount": 85850300,
     "firstSealTime": "09:36:28", "openTimes": 4,
     "reason": "专项贷款.乡村振兴.摩根中国A股基金持股.种植业.转基因",
     "detail": "主要从事玉米杂交种子的研发、生产与销售，单倍体育种技术领先"},
    {"code": "301697", "setcode": "0", "name": "贝特利", "price": 36.60, "pct": 20.00,
     "boards": 3, "pattern": "3天3板", "boardType": "换手板", "sealAmount": 83162500,
     "firstSealTime": "09:34:03", "openTimes": 1,
     "reason": "光伏.芯片.华为概念",
     "detail": "主营电子材料研发生产与销售，光伏银粉和HJT浆料是核心导电材料，银粉收入占主营七成以上，客户覆盖上海银浆、帝科股份、华晟新能源；四甲基环四硅氧烷可作半导体上游原材料，Mini-LED封装胶已量产"},
    {"code": "000525", "setcode": "0", "name": "红太阳", "price": 5.02, "pct": 10.09,
     "boards": 1, "pattern": "6天3板", "boardType": "", "sealAmount": 77237700,
     "firstSealTime": "13:39:51", "openTimes": 0,
     "reason": "乡村振兴.扣非亏损.农用化工.虫害防治",
     "detail": "绿色农药全产业链公司，拥有行业内最完整、规模最大的吡啶碱产业链；第三季度净利润同比增长38.96%"},
    {"code": "600540", "setcode": "1", "name": "新赛股份", "price": 4.58, "pct": 10.10,
     "boards": 1, "pattern": "1天1板", "boardType": "换手板", "sealAmount": 64695700,
     "firstSealTime": "10:39:22", "openTimes": 0,
     "reason": "微小盘股.风电.国企改革.种植业",
     "detail": "农业主要产品为棉籽、葵花籽、菜籽及其他油料作物，立足新疆棉花产业，聚焦棉业全产业链"},
    {"code": "600229", "setcode": "1", "name": "城市传媒", "price": 6.42, "pct": 9.93,
     "boards": 1, "pattern": "5天2板", "boardType": "", "sealAmount": 62383100,
     "firstSealTime": "09:30:08", "openTimes": 0,
     "reason": "短剧游戏.国企改革.人工智能",
     "detail": "AI短剧出海及商业化提速；推进头部IP全链条运营并开发AIGC应用产品；针对出版行业开放万象出版大模型，为出版行业首个垂直细分大模型"},
    {"code": "001316", "setcode": "0", "name": "润贝航科", "price": 27.82, "pct": 10.00,
     "boards": 1, "pattern": "1天1板", "boardType": "换手板", "sealAmount": 61760400,
     "firstSealTime": "09:57:24", "openTimes": 2,
     "reason": "低空经济.商业航天.国防军工.深圳板块.航空发动机.贸易",
     "detail": "控股子公司杭州轻云负责航空、航天先进复合材料研发生产；自2011年起服务中国商飞，曾获C919大型客机首飞先进集体奖和TC取证先进集体奖"},
    {"code": "002388", "setcode": "0", "name": "新亚制程", "price": 6.67, "pct": 10.07,
     "boards": 3, "pattern": "3天3板", "boardType": "", "sealAmount": 58130400,
     "firstSealTime": "10:27:24", "openTimes": 0,
     "reason": "储能.人形机器人.光伏",
     "detail": "优必选Thinker-Sim具身智能仿真平台正式发布；公司产品已批量用于人型机器人主板，与多家客户建立合作"},
    {"code": "600537", "setcode": "1", "name": "*ST亿晶", "price": 2.05, "pct": 10.22,
     "boards": 5, "pattern": "5天5板", "boardType": "换手板", "sealAmount": 17298700,
     "firstSealTime": "09:39:29", "openTimes": 5,
     "reason": "光伏.ST板块",
     "detail": "公司预重整投资安排生变，中润光能拟入主"},
    {"code": "603657", "setcode": "1", "name": "春光科技", "price": 33.88, "pct": 10.00,
     "boards": 3, "pattern": "3天3板", "boardType": "换手板", "sealAmount": 17024700,
     "firstSealTime": "09:31:27", "openTimes": 1,
     "reason": "机器人概念.专精特新.小米概念",
     "detail": "优必选Thinker-Sim具身智能仿真平台正式发布；公司主营吸尘器、洗地机、扫地机器人、布衣清洗机等"},
    {"code": "002088", "setcode": "0", "name": "鲁阳节能", "price": 11.09, "pct": 10.02,
     "boards": 4, "pattern": "6天5板", "boardType": "换手板", "sealAmount": 4409400,
     "firstSealTime": "14:45:51", "openTimes": 8,
     "reason": "摩根中国A股基金持股.装饰建材.锂电池概念.新材料.可燃冰",
     "detail": "中国陶瓷纤维龙头，间接控股股东筹划破产重组，公司实控人拟发生变更"},
]

# ============================================================
# 4) 跌停池（全市场 15 家，全量，按封单额降序）
# ============================================================
limitDownPool = [
    {"code": "000678", "setcode": "0", "name": "襄阳轴承", "price": 17.77, "pct": -9.98,
     "days": 1, "boardType": "换手板", "sealAmount": 182112300, "openTimes": 2,
     "theme": "机器人概念.一带一路.农机"},
    {"code": "603083", "setcode": "1", "name": "剑桥科技", "price": 104.81, "pct": -10.00,
     "days": 1, "boardType": "⊥字板", "sealAmount": 139481200, "openTimes": 28,
     "theme": "非周期股.CPO概念.华为概念.通信设备"},
    {"code": "605058", "setcode": "1", "name": "澳弘电子", "price": 50.22, "pct": -10.00,
     "days": 1, "boardType": "⊥字板", "sealAmount": 98988600, "openTimes": 16,
     "theme": "储能.元器件.智能家居"},
    {"code": "002827", "setcode": "0", "name": "高争民爆", "price": 60.99, "pct": -10.00,
     "days": 1, "boardType": "换手板", "sealAmount": 87362100, "openTimes": 3,
     "theme": "回购计划.昨日断板.化学制品"},
    {"code": "603278", "setcode": "1", "name": "大业股份", "price": 13.57, "pct": -10.01,
     "days": 1, "boardType": "换手板", "sealAmount": 48809900, "openTimes": 4,
     "theme": "人形机器人.风电.通用设备"},
    {"code": "600892", "setcode": "1", "name": "大晟文化", "price": 4.63, "pct": -9.92,
     "days": 1, "boardType": "换手板", "sealAmount": 27566600, "openTimes": 1,
     "theme": "短剧游戏.微小盘股.昨日断板"},
    {"code": "002353", "setcode": "0", "name": "杰瑞股份", "price": 79.44, "pct": -10.00,
     "days": 1, "boardType": "换手板", "sealAmount": 26755400, "openTimes": 81,
     "theme": "天然气.储能"},
    {"code": "600838", "setcode": "1", "name": "上海九百", "price": 10.45, "pct": -9.99,
     "days": 1, "boardType": "换手板", "sealAmount": 23289900, "openTimes": 14,
     "theme": "国企改革.新零售.一般零售.网络直播"},
    {"code": "002384", "setcode": "0", "name": "东山精密", "price": 80.19, "pct": -10.00,
     "days": 1, "boardType": "⊥字板", "sealAmount": 13744600, "openTimes": 10,
     "theme": "人形机器人.非周期股"},
    {"code": "605100", "setcode": "1", "name": "华丰股份", "price": 22.80, "pct": -9.99,
     "days": 1, "boardType": "换手板", "sealAmount": 12601600, "openTimes": 0,
     "theme": "回购计划.通用设备"},
    {"code": "003033", "setcode": "0", "name": "征和工业", "price": 76.58, "pct": -10.00,
     "days": 1, "boardType": "换手板", "sealAmount": 10177500, "openTimes": 12,
     "theme": "人形机器人.机器人概念"},
    {"code": "000679", "setcode": "0", "name": "大连友谊", "price": 5.91, "pct": -10.05,
     "days": 1, "boardType": "换手板", "sealAmount": 5941300, "openTimes": 0,
     "theme": "新零售.微小盘股.昨日断板"},
    {"code": "600281", "setcode": "1", "name": "华阳新材", "price": 4.99, "pct": -9.93,
     "days": 1, "boardType": "换手板", "sealAmount": 4784900, "openTimes": 0,
     "theme": "微小盘股.国企改革.稀有金属"},
    {"code": "603578", "setcode": "1", "name": "三星新材", "price": 9.54, "pct": -10.00,
     "days": 1, "boardType": "换手板", "sealAmount": 3588000, "openTimes": 0,
     "theme": "光伏.微小盘股"},
    {"code": "001225", "setcode": "0", "name": "和泰机电", "price": 34.04, "pct": -9.99,
     "days": 1, "boardType": "换手板", "sealAmount": 3550400, "openTimes": 7,
     "theme": "专用设备.一带一路"},
]

snapshot = {
    "_note": "分片快照：仅含已抓取并核对过的真实数据。缺 industryBoards(另26个) / seatActivity / stockListActivity / watchlist。禁止用于生成 tide_data.json。",
    "_partial": True,
    "_missing": ["industryBoards(26)", "seatActivity", "stockListActivity", "watchlist"],
    "tradeDate": "2026-11-06",
    "capturedAt": "2026-11-07T10:30:00+08:00",
    "source": "tdx-connector (通达信)",
    "indices": indices,
    "industryBoards": industryBoards,
    "limitUpPool": limitUpPool,
    "limitUpTotal": 33,
    "limitDownPool": limitDownPool,
    "limitDownTotal": 15,
}

assert len(limitUpPool) == 20, len(limitUpPool)
assert len(limitDownPool) == 15, len(limitDownPool)

OUT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"OK -> {OUT.name}（分片，不可直接喂 derive.py）")
print(f"tradeDate={snapshot['tradeDate']} 涨停={snapshot['limitUpTotal']}(采样 {len(limitUpPool)}) 跌停={snapshot['limitDownTotal']}")
print(f"板块 {len(industryBoards)}/27 | 席位 0 | 活跃股 0 | 观察池 0")
print(f"缺失: {', '.join(snapshot['_missing'])}")
