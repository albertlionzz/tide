# 潮汐 · Tide

> A 股每日盘面分析仪表盘。行业轮动、涨停梯队、龙虎席位与游资动向，浓缩为一屏可读的信号。

数据全部取自**当日收盘快照**（通达信 tdx-connector），恐慌指数由五项盘面读数合成，不含任何人工估读数字。

![交易日](https://img.shields.io/badge/%E4%BA%A4%E6%98%93%E6%97%A5-2026--11--04-c9302b)
![数据源](https://img.shields.io/badge/%E6%95%B0%E6%8D%AE%E6%BA%90-%E9%80%9A%E8%BE%BE%E4%BF%A1-0071e3)

---

## 它解决什么

盘后复盘的真实成本不在"看不到数据"，而在**数据是散的**：指数在一个页面、涨停池在另一个、龙虎榜在交易所公告里、游资逻辑在公众号文章里。看完一圈，脑子里还是没有一个「今天到底是什么天气」的判断。

潮汐把四类信号压到一屏，并给出一个 0–100 的**恐慌指数**作为盘面温度的单一读数。

## 恐慌指数怎么算

五个分量，各自映射到「越高越恐慌」的方向后加权。权重固定，不接受调参。

| 分量 | 权重 | 方向 | 含义 |
|---|---|---|---|
| 涨跌家数失衡 | 30% | 正向 | 下跌家数占比越高越恐慌 |
| 跌停扩散度 | 22% | 正向 | 跌停占全市场比重 |
| 连板高度 | 18% | **反向** | 最高板越低，说明接力越弱，恐慌越高 |
| 赚钱效应 | 15% | **反向** | 封板率越低越恐慌 |
| 指数振幅 | 15% | 正向 | 三大指数平均振幅，0.8%→0 分，3.5%→100 分 |

五档刻度：`0-20 冷静` / `21-40 平稳` / `41-60 紧张` / `61-80 偏高` / `81-100 恐慌`。

> 恐慌指数不是买卖信号，而是仓位与预期管理的仪表盘。

**一个容易写错的坑**：反向映射的 `scale(v, lo, hi, out_lo, out_hi)` 在 `out_lo > out_hi` 时，截断区间必须按 `[min, max]` 取。早期版本按原序传给 `clamp`，导致 `clamp(62.5, 100, 0) = max(100, min(0, 62.5)) = 100`，两个反向分量被永久钉死在极值，指数整体虚高 9~13 分。凡新增反向分量，先用 3 个不同输入单测输出是否随输入变化。

## 数据管道

四段式，改数据只需重跑脚本，前端不用动：

```
tdx-connector  ──►  build_snapshot_<日期>.py  ──►  raw_snapshot.json
                                                        │
                                                   derive.py
                                                        │
                                                        ▼
                                                  tide_data.json  ──►  app.js 渲染
                                                        ▲
fetch_feed.py  ──►  feed_cls.json / feed_xueqiu.json ──► build_feed.py
                                                              │
                                                              ▼
                                                        feed_data.json
```

- `raw_snapshot.json` — 当日原始快照（指数 / 板块 / 涨跌停池 / 席位 / 活跃股 / 观察池 60 日 K 线）
- `tide_data.json` — 派生后的指标（恐慌指数五分量、宽度、连板梯队、席位汇总）
- `feed_data.json` — 资讯聚合（财联社电报 + 雪球热帖 + 被讨论个股）

### 双数据源与口径

主数据源是通达信 MCP。当它有额度时，`build_snapshot_<日期>.py` 一次抓完。

2026-11-06 那天通达信额度耗尽（`leftValue=2`），改用 `fetch_em_1106.py` 从东方财富
公开接口补齐板块 / 龙虎榜 / K 线，指数与涨跌停仍用已抓取的通达信数据。两家的**上证收盘
做了交叉验证**（3649.18 / -1.10% 完全一致），前复权 K 线也一致（11-04 收盘同为 9.03）。

2026-11-09 额度仍未恢复（`quota is below 10`），`fetch_em_1109.py` 把快照改成**全量公开接口**
并做双源核对：

| 数据 | 主源 | 核对源 | 核对结果 |
|---|---|---|---|
| 三大指数 / 涨跌家数 | 东财 `push2` ulist | 新浪 `hq.sinajs.cn` | 上证 3660.79 / 高 3679.09 / 低 3653.05 完全一致 |
| 涨停家数 | 东财 `getTopicZTPool` | 同花顺 `limit_up_pool` | 54 = 54 |
| 跌停家数 | 东财 `getTopicDTPool` | 同花顺 `lower_limit_pool` | 1 = 1 |
| 涨停原因 / 板型 | 同花顺 `reason_type` / `limit_up_type` | — | 东财只有数值，没有原因字段 |

东财涨停池的价格字段是 **1/1000 元**（`5510` = 5.51），脚本里换算后再与同花顺
`latest` 逐只比对，差 >0.011 直接抛异常。涨停的 `zttj` 是「N 天 M 个涨停」，与连板数
`lbc` 不是一回事（11 天 7 板 ≠ 7 连板），`pattern` 优先取同花顺 `high_days`。

必须知道的三个口径差异：

| 项目 | 通达信 | 东方财富 |
|---|---|---|
| 行业分类 | ~100 个二级行业 | **496 个**二级行业，粒度更细 |
| 席位 `lists` | 累计上榜天数（如机构 3726） | **当日上榜股票只数**（如机构 37） |
| 席位覆盖 | 含机构专用 / 沪深股通 | 需用买卖席位明细聚合，`RPT_OPERATEDEPT_ACTIVE` 那张表**漏掉机构与股通** |

所以 `tide_data.json` 的 `meta.source` 会写明当日实际来源，前端页脚直接展示。
**板块数值不可跨数据源直接比较**，接口口径变了就要当新指标看。

## 运行

```bash
bash serve.sh          # 默认 8768 端口
# 打开 http://127.0.0.1:8768/index.html
```

**必须走 HTTP**。`file://` 协议下 `fetch` 会被 CORS 拦截，页面会静默回退到离线样本 —— 看到的一切数字都是占位值。这是排查时第一嫌疑。

### 更新一个交易日

```bash
python build_snapshot_1104.py   # ① 组装快照（需通达信 MCP）
python derive.py                # ② 派生指标
python fetch_feed.py && python build_feed.py   # ③ 刷新资讯
node verify.js                  # ④ 渲染校验（46 条断言）
```

通达信额度不足时，① 换成 `fetch_em_1106.py`（东方财富补齐版，脚本内自带日期断言，
会拿东财上证收盘与通达信已抓值比对，不一致直接中止）。

`verify.js` 内含**写死的当日基线常量**（headerDate / dataDate / 恐慌值 / 上证点位 / 涨停池代表个股 / 北向信号），换交易日必须同步更新，否则会报失败项但页面其实是对的。注意信号类断言要跟着当日方向改 —— 11-06 北向是净流出，沿用「北向加仓」会误报。

## 部署到云服务器

纯静态站点，任意 1 核 1G 的机器都够。实测在一台 2 核 2G / 3 Mbps 的轻量云主机上：
内存占用约 12 MB，首屏 0.07 秒，`tide_data.json` 经 gzip 从 47 KB 压到 8.6 KB。

线上地址：**https://artsi.site**（TLS 1.3 + HTTP/2 + HSTS）

### 一键部署

```bash
# 本地执行：上传 7 个运行文件 + 两份 nginx 模板 + 初始化脚本
bash deploy/deploy.sh root@<服务器IP> "example.com www.example.com" 8080 -i ~/.ssh/你的私钥
```

服务端会依次完成：装 nginx → 落配置 → 禁用默认站点 → 放行系统防火墙 → 重启 → 自检。

`setup-server.sh` 会自动判断阶段：证书不存在时落纯 HTTP 配置（`nginx-tide-http.conf`），
先让你能跑通 certbot；证书已存在时落 HTTPS 配置（`nginx-tide.conf`），80 只做跳转和续期验证。
所以完整流程是 **跑一次脚本 → 签证书 → 再跑一次**。

### 申请证书

```bash
certbot certonly --webroot -w /var/www/tide \
  -d example.com -d www.example.com \
  --agree-tos -m you@example.com --preferred-challenges http
```

续期由 certbot 的 systemd timer 自动完成，验证命令 `certbot renew --dry-run`。

### 五个必踩的坑

**① 云端防火墙和系统防火墙是两层。** 脚本只能管系统层（ufw / firewalld）。云厂商控制台的
安全组通常默认只开 22/80/443，用 8080 这类高位端口必须手动加规则，否则服务器上
`curl 127.0.0.1:8080` 返回 200、公网就是连不上。**443 同样可能没开**，上 HTTPS 前先确认。

**② Ubuntu 的默认站点会抢占 80 端口。** `/etc/nginx/sites-enabled/default` 也监听 80，
请求会被它接走并返回 404（它的 root 指向 `/var/www/html`）。必须移走该软链，脚本已处理。

**③ ufw 规则不要只在 active 时添加。** apt 安装过程可能重置 ufw，导致脚本报告"已放行"
但 `ufw status` 里只剩 22/tcp。正确做法是不判断 active 直接 `ufw allow`，规则会持久化。

**④ `location ~ /\. { deny all; }` 会连 ACME 验证路径一起拒掉。** 这条安全规则拦所有
以点开头的路径，Let's Encrypt 的 `/.well-known/acme-challenge/` 正好在范围内，表现为
证书签发失败、错误信息却是"验证文件 404"。必须用 `location ^~ /.well-known/acme-challenge/`
**前缀匹配短路**正则，`^~` 的优先级高于 `~`。

**⑤ nginx reload 后旧 worker 仍在处理请求。** 改完配置立刻用 curl 验证，可能拿到旧的
404/403，看起来像配置没生效。等 1–2 秒或 `systemctl restart nginx` 再测。

### 一个容易忽略的 add_header 陷阱

nginx 的继承规则是：**location 里只要出现任意 `add_header`，server 层的 `add_header` 全部失效**。
本项目每个 location 都带了 `Cache-Control`，如果安全头只写在 server 层，那所有页面都拿不到
HSTS / X-Frame-Options。所以安全头抽成 `/etc/nginx/snippets/tide-headers.conf` 逐 location include。

### 关于备案

国内节点用 80 端口**直接以 IP 访问**通常不受影响；一旦绑定域名对外提供 web 服务，
域名必须已完成 ICP 备案，否则会被拦截。备案、域名归属、DNS 解析是三件独立的事 ——
备案下来了但 A 记录没配，浏览器照样打不开。

## 研习社（经验总结，留言板已关闭）

`community.html` —— 用来沉淀可验证的判断，而不是给自己打气。

- **经验总结**（唯一开放板块）：结构化发帖（标题 / 分类 / 正文 / 标签 / 署名），
  分类固定为盘面复盘、个股研究、方法论、交易心理、复盘教训、其他
- **留言板**：短留言、无分类、无标题 —— **2026-11-09 起暂时关闭**，避免公开白板
  被陌生人塞进不适宜内容。历史数据保留在库里，一条没删

两者共用一套帖子表（`type` 区分），都支持评论、点赞、搜索、分页。

### 开关（默认关闭，改一个环境变量即可恢复）

```bash
# 关（当前线上状态）
Environment=TIDE_MESSAGE_BOARD=0   # 默认值，不写也是关
# 开
sudo sed -i 's/^Environment=TIDE_MESSAGE_BOARD=.*/Environment=TIDE_MESSAGE_BOARD=1/' \
  /etc/systemd/system/tide-community.service
sudo systemctl daemon-reload && sudo systemctl restart tide-community
```

关闭时**前后端同时生效**：前端 `/api/config` 拿到 `messageBoard:false` 就把标签页、
「条留言」统计、列表入口全隐藏（默认值就是关，只有服务端明确返回 `true` 才显示，
所以配置写错也不会先把白板亮出来）；后端列表返回空、写入返回 403、单帖详情按不存在处理。

另有一个 `TIDE_COMMENT_OPEN`（默认 `1`）控制**文章评论**是否可写。评论目前仍然开放；
如果连评论也要一起关，把它设成 `0` 重启即可。

### 为什么这里要一个后端

静态站点做留言板只有两条路：localStorage（换设备就丢）或第三方服务（要账号、要限额）。
这台机器上跑一个 **Python 标准库 + SQLite** 的服务最省事：零第三方依赖、内存约 20 MB、
单文件、不需要维护 ORM 迁移。

```
community/server.py     # 全部后端逻辑（约 400 行）
community.db            # SQLite，运行时生成，不进版本库
```

只监听 `127.0.0.1:8091`，由 nginx 反代 `/api/` 出去，**不直接暴露公网**。

### API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| GET | `/api/stats` | 经验 / 留言 / 评论 / 参与者计数 |
| GET | `/api/posts?type=note\|message&page=&size=&q=&category=` | 列表，支持关键词与分类 |
| POST | `/api/posts` | 发帖 `{type,title,body,category,tags,author}` |
| GET | `/api/posts/<id>` | 详情，含评论 |
| POST | `/api/posts/<id>/like` | 点赞 |
| POST | `/api/posts/<id>/comments` | 评论 `{body,author}` |
| DELETE | `/api/posts/<id>` | 软删除，需 `X-Admin-Token` |

### 部署

```bash
# 1. 上传后端并初始化（生成 systemd 服务 + 随机删除口令）
scp -i ~/.ssh/tide_tencent community/server.py deploy/setup-community.sh root@<IP>:/tmp/
ssh -i ~/.ssh/tide_tencent root@<IP> \
  'sed -i "s/\r$//" /tmp/setup-community.sh && bash /tmp/setup-community.sh'
# 输出里的 ADMIN_TOKEN= 就是删帖口令，记下来

# 2. 给 nginx 加 /api/ 反代（幂等，失败自动回滚）
scp -i ~/.ssh/tide_tencent deploy/patch-nginx-api.py root@<IP>:/tmp/
ssh -i ~/.ssh/tide_tencent root@<IP> 'python3 /tmp/patch-nginx-api.py'

# 3. 上传前端
scp -i ~/.ssh/tide_tencent community.html community.css community.js root@<IP>:/var/www/tide/
```

删帖：

```bash
curl -X DELETE https://artsi.site/api/posts/12 -H "X-Admin-Token: <口令>"
```

### 安全取舍

- **无登录**。发帖是公开的，低摩擦优先。写入按 IP 每小时限 20 帖 / 60 条评论，
  内存滑动窗口，不落盘。留言板 2026-11-09 起关闭，只剩结构化经验总结；但**经验总结
  仍是无登录可写**，如果要拦，下一步得加「先审后发」而不是靠前端隐藏。
- **所有用户内容一律 textContent / escapeHTML 渲染**，不拼进 innerHTML 的标签位置。
  正文用 `white-space: pre-wrap` 保留换行，不需要富文本。
- **删除走软删除 + 口令**。口令存在 systemd 的 `Environment` 里，只 root 可读，
  没配置时删除接口直接返回 403 而不是放行。
- 请求体上限 64 KB，字段按类型截断（标题 120 / 正文 6000 / 评论 1200）。
  截断而不是报错，避免用户写长文白丢。

### 两个已修的坑

**① `display:flex` 会盖掉 `hidden` 属性。** `.cm-pager{display:flex}` 让
`element.hidden = true` 完全失效，只有 1 页时下限按钮照样显示。CSS 里必须补一条
`[hidden]{display:none !important}`。

**② innerHTML 替换掉的节点不能再引用。** `loadList()` 开头原本写了
`$("cmLoading").hidden = false`，但下一行就用 `innerHTML` 把 `#cmLoading`
（它就在 `#cmFeed` 内部）整个换掉了。第二次调用时该元素已不存在，
`null.hidden` 抛 `Cannot set properties of null`，表现为**切换标签列表不刷新、发帖后报错**。
骨架屏应该纯粹由 innerHTML 控制，不要另外持有引用。

## 资讯抓取：两家都重度反爬

唯一可靠路径是**真浏览器渲染 + DOM 解析**，curl 换 UA 无效 —— WAF 拦的是 TLS/浏览器指纹。

| 站点 | 失败路径 | 可行路径 |
|---|---|---|
| 财联社 | `nodeapi/updateTelegraphList` 返 HTML；`v1/roll/get_roll_list` 返 errno 10012 签名错误 | Edge 无头 + 桌面 UA → `--dump-dom cls.cn/telegraph` |
| 雪球 | 桌面 UA / curl 全被阿里云 WAF 拦（`_waf_*` 挑战） | Edge 无头 + **移动端 UA** → `--dump-dom xueqiu.com/today` |

两个细节：财联社按天插入的日期分隔条（`2026.11.04 星期三 …`）会被误解析成一条电报，需按「正文同时含"星期"且匹配日期格式」剔除；雪球正文自带 `$股票名(代码)$` 标签，正是「股民长期跟踪个股」语义的落点，用它聚合「被反复提及的个股」。

## 目录

```
├── index.html              盘面页骨架
├── styles.css              苹果风样式（浅色、留白、单色强调）
├── app.js                  渲染 + 交互（无框架、无构建步骤）
├── community.html          研习社（论坛 + 留言板）
├── community.css           研习社样式（复用 styles.css 的变量）
├── community.js            研习社前端（fetch + 事件委托）
├── community/
│   └── server.py           研习社后端（标准库 http.server + SQLite）
├── derive.py               派生管道：快照 → 指标 → 恐慌指数
├── build_snapshot_1104.py  当日快照组装（通达信版，每个交易日一份）
├── fetch_em_1106.py        当日快照组装（东财补齐版，通达信额度不足时用）
├── extract_screen.py       研究池抽取（市值 50-300 亿 + 换手 >2.5%）
├── fetch_feed.py           资讯抓取（Edge 无头）
├── build_feed.py           资讯聚合
├── deploy/
│   ├── nginx-tide.conf         HTTPS 站点配置模板
│   ├── nginx-tide-http.conf    证书签发前的 HTTP 过渡模板
│   ├── setup-server.sh         服务端初始化（含 acme 例外）
│   ├── setup-community.sh      研习社后端初始化（systemd + 随机口令）
│   ├── patch-nginx-api.py      给 nginx 加 /api/ 反代（幂等）
│   └── deploy.sh               本地一键上传
└── verify.js               jsdom 等价渲染校验
```

## 已知局限

- **涨停池只展示封单前 15 + 全部 3 板以上**，去重后条数随行情变（11-06 是 20 只，11-09 只有 15 只，因为 3 板以上仅 1 家）。连板梯队统计基于这份样本而非全市场，最高板数取自全量池。
- **炸板率**定义为 `开板次数总和 / 展示只数`，可超过 100%，语义上更接近"开板烈度"而非"炸板率"。
- **研究池截面固定**在 2026-09-29，不随每日行情刷新，属研究底稿而非当日选股结果。
- **板块口径随数据源变化**：通达信约 100 个二级行业，东方财富 496 个，两者数值不可直接比较。`meta.source` 会写明当日实际来源。
- **席位 `lists` 口径在 2026-11-06 起变化**：从「累计上榜天数」改为「当日上榜股票只数」，前端文案已同步为「当日上榜 N 只」。
- 龙虎榜席位维度可用，个股维度接口（`tdxf10_gg_jyds`）当前返 ErrorCode -1005 不可用。
- **研习社无登录体系**，删除依赖单一管理口令；没有用户体系也就无法做「只看我的帖子」这类功能。
- **板块名带 Ⅰ/Ⅱ/Ⅲ 层级后缀，且与涨停池的行业归属不是同一套分类**，「板块内涨停股」只能按关键词粗匹配，所以措辞写「样本内未见」，不写「今日无涨停」。
- **备注列没有公司基本面简介**（那曾是通达信独有字段）。改用「行业 · 板型 · 首封时间」，全部取自接口原始字段，不做文字生成。

## 免责

本项目仅供学习与研究使用，不构成任何投资建议。市场有风险，决策需独立。页面数据来自公开行情接口，与任何券商、交易所无隶属关系。
