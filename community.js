/* 潮汐 · 研习社 — 论坛与留言板前端
 *
 * 数据全部走 /api（生产环境由 nginx 反代到 127.0.0.1:8091）。
 * 本地用 python -m http.server 预览时端口不是 8091，自动指向本地后端。
 *
 * 安全约定：所有用户输入一律经 escapeHTML 或 textContent 渲染，
 * 绝不拼进 innerHTML 的标签位置。正文用 white-space: pre-wrap 保留换行。
 */
(function () {
  "use strict";

  var API = (["127.0.0.1", "localhost"].indexOf(location.hostname) >= 0 && location.port !== "8091")
    ? "http://127.0.0.1:8091/api"
    : "api";

  var STATE = {
    type: "note",
    page: 1,
    size: 20,
    q: "",
    category: "",
    pages: 1,
    total: 0,
    openId: null,     // 当前展开评论的帖子
    detail: {},       // id -> 完整详情（含评论）
    likes: {},        // id -> 本地已赞标记，防重复点击
  };

  var CATS = ["盘面复盘", "个股研究", "方法论", "交易心理", "复盘教训", "其他"];

  // 服务端开关（/api/config）。默认按「留言板关闭」渲染，只有服务端明确
  // 返回 messageBoard:true 才显示入口——这样即使开关配置错了，前端也不会
  // 先把匿名白板亮出来。真正的拦截在后端，前端只是不展示。
  var CFG = { messageBoard: false, comments: true, notice: "" };

  // ---------- 工具 ----------
  function $(id) { return document.getElementById(id); }

  function escapeHTML(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fmtTime(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    var now = new Date();
    var diff = (now - d) / 1000;
    if (diff < 60) return "刚刚";
    if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
    if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
    if (diff < 86400 * 7) return Math.floor(diff / 86400) + " 天前";
    var p = function (n) { return n < 10 ? "0" + n : "" + n; };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  }

  function initial(name) {
    var s = String(name || "匿").trim();
    return s ? s.charAt(0) : "匿";
  }

  function request(path, opts) {
    opts = opts || {};
    var url = API + path;
    var init = {
      method: opts.method || "GET",
      headers: { "Accept": "application/json" },
    };
    if (opts.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    return fetch(url, init).then(function (r) {
      return r.text().then(function (txt) {
        var data = null;
        try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = null; }
        if (!r.ok) {
          var msg = (data && data.error) || ("请求失败（HTTP " + r.status + "）");
          throw new Error(msg);
        }
        return data;
      });
    });
  }

  // ---------- 开关 ----------
  function applyConfig() {
    var open = CFG.messageBoard !== false;
    var tab = $("cmTabMessage");
    if (tab) tab.hidden = !open;
    var stat = $("cmStatMessages");
    if (stat) stat.hidden = !open;
    if (!open) {
      STATE.type = "note";
      var note = $("cmSideNote");
      if (note && CFG.notice) note.textContent = CFG.notice + "。发布无需登录但有频率限制。";
    }
  }

  // ---------- 统计 ----------
  function loadStats() {
    request("/stats").then(function (d) {
      Object.keys(d).forEach(function (k) {
        var el = document.querySelector('.cm-stat-num[data-k="' + k + '"]');
        if (el) el.textContent = d[k];
      });
      var hc = $("cmHeaderCount");
      if (hc) {
        hc.textContent = CFG.messageBoard === false
          ? d.notes + " 篇 · " + d.comments + " 条评论"
          : d.notes + " 篇 · " + d.messages + " 条留言";
      }
    }).catch(function () { /* 统计失败不影响主流程 */ });
  }

  // ---------- 列表 ----------
  function renderCard(p) {
    var isNote = p.type === "note";
    var titleHtml = isNote && p.title
      ? '<h2 class="cm-post-title is-clickable" data-open="' + p.id + '">' + escapeHTML(p.title) + "</h2>"
      : "";
    var bodyCls = "cm-post-body" + (p.body.length > 260 ? " is-clamped" : "");
    var tags = (p.tags || []).map(function (t) {
      return '<span class="cm-tag">#' + escapeHTML(t) + "</span>";
    }).join("");
    var catHtml = isNote && p.category
      ? '<span class="cm-cat">' + escapeHTML(p.category) + "</span>"
      : '<span class="cm-cat">' + (isNote ? "经验" : "留言") + "</span>";

    var isOpen = STATE.openId === p.id;
    var detail = STATE.detail[p.id];

    return (
      '<article class="cm-post" data-id="' + p.id + '">' +
        '<div class="cm-post-head">' +
          catHtml +
          '<span class="cm-meta">' + escapeHTML(p.author) + "</span>" +
          '<span class="cm-meta cm-meta-dot">' + fmtTime(p.createdAt) + "</span>" +
        "</div>" +
        titleHtml +
        '<p class="' + bodyCls + '">' + escapeHTML(p.body) + "</p>" +
        (tags ? '<div class="cm-post-tags">' + tags + "</div>" : "") +
        '<div class="cm-post-foot">' +
          '<button class="cm-act' + (STATE.likes[p.id] ? " is-liked" : "") + '" data-like="' + p.id + '" type="button" aria-label="点赞">' +
            heartSvg() + "<span>" + (p.likes || 0) + "</span>" +
          "</button>" +
          '<button class="cm-act" data-open="' + p.id + '" type="button">' +
            commentSvg() + "<span>" + (p.comments || 0) + " 条评论</span>" +
          "</button>" +
          '<button class="cm-act cm-expand" data-open="' + p.id + '" type="button">' +
            (isOpen ? "收起" : "展开") +
          "</button>" +
        "</div>" +
        (isOpen ? renderComments(p.id, detail) : "") +
      "</article>"
    );
  }

  function renderComments(pid, detail) {
    if (!detail) {
      return '<div class="cm-comments"><p class="cm-meta">正在读取评论…</p></div>';
    }
    var list = (detail.commentList || []).map(function (c) {
      return (
        '<div class="cm-comment">' +
          '<div class="cm-avatar" aria-hidden="true">' + escapeHTML(initial(c.author)) + "</div>" +
          '<div class="cm-comment-main">' +
            '<div class="cm-comment-head">' +
              '<span class="cm-comment-author">' + escapeHTML(c.author) + "</span>" +
              '<span class="cm-comment-time">' + fmtTime(c.createdAt) + "</span>" +
            "</div>" +
            '<p class="cm-comment-body">' + escapeHTML(c.body) + "</p>" +
          "</div>" +
        "</div>"
      );
    }).join("");

    return (
      '<div class="cm-comments">' +
        (list || '<p class="cm-meta">还没有评论，说说你的看法。</p>') +
        '<form class="cm-comment-form" data-comment="' + pid + '">' +
          '<input type="text" class="cm-input-author" maxlength="24" placeholder="署名" autocomplete="nickname" />' +
          '<input type="text" class="cm-input-body" maxlength="1200" placeholder="写下你的补充或反驳…" />' +
          '<button type="submit" class="cm-btn cm-btn-primary">发送</button>' +
        "</form>" +
      "</div>"
    );
  }

  function heartSvg() {
    return '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true">' +
      '<path d="M8 13.6S2.6 10.2 2.6 6.4A2.9 2.9 0 0 1 8 4.9a2.9 2.9 0 0 1 5.4 1.5c0 3.8-5.4 7.2-5.4 7.2Z" ' +
      'stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>';
  }

  function commentSvg() {
    return '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true">' +
      '<path d="M13.4 8.7c0 2.8-2.4 5-5.4 5-.8 0-1.6-.2-2.3-.5L2.6 14l.9-2.6a4.8 4.8 0 0 1-1.3-3.2c0-2.8 2.4-5 5.4-5s5.8 2.2 5.8 5.5Z" ' +
      'stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>';
  }

  function renderEmpty() {
    var isNote = STATE.type === "note";
    var searching = STATE.q || STATE.category;
    var title = searching ? "没有匹配的内容" : (isNote ? "还没有经验总结" : "还没有留言");
    var sub = searching
      ? "换个关键词，或清空筛选看看全部。"
      : (isNote ? "点上面的输入框，写下第一条可验证的判断。" : "点上面的输入框，留下第一句话。");
    return '<div class="cm-empty"><p class="cm-empty-title">' + title + "</p><p>" + sub + "</p></div>";
  }

  function loadList() {
    // 注意：不要在这里引用 #cmLoading —— 它就在 #cmFeed 内部，下一行的
    // innerHTML 替换会把它从 DOM 中移除，第二次调用时 $("cmLoading") 为 null，
    // 会抛 "Cannot set properties of null"，导致切换 tab / 发帖后列表不刷新。
    $("cmFeed").innerHTML = '<div class="cm-skeleton-wrap"><div class="cm-skeleton"></div><div class="cm-skeleton"></div><div class="cm-skeleton"></div></div>';

    var qs = "/posts?type=" + encodeURIComponent(STATE.type) +
      "&page=" + STATE.page + "&size=" + STATE.size;
    if (STATE.q) qs += "&q=" + encodeURIComponent(STATE.q);
    if (STATE.category) qs += "&category=" + encodeURIComponent(STATE.category);

    request(qs).then(function (d) {
      STATE.pages = d.pages;
      STATE.total = d.total;
      var feed = $("cmFeed");
      var items = d.items || [];

      if (!items.length) {
        feed.innerHTML = renderEmpty();
      } else {
        // 从列表数据补齐缺失的评论数，避免展开时闪一下
        feed.innerHTML = items.map(function (p) {
          STATE.detail[p.id] = STATE.detail[p.id] || null;
          return renderCard(p);
        }).join("");
      }

      var pager = $("cmPager");
      pager.hidden = STATE.pages <= 1;
      $("cmPageInfo").textContent = STATE.page + " / " + STATE.pages;
      $("cmPrev").disabled = STATE.page <= 1;
      $("cmNext").disabled = STATE.page >= STATE.pages;

      // 若当前展开的帖子在本页，补拉详情
      if (STATE.openId && STATE.detail[STATE.openId] === null) {
        var inPage = items.some(function (p) { return p.id === STATE.openId; });
        if (inPage) fetchDetail(STATE.openId);
      }
    }).catch(function (err) {
      $("cmFeed").innerHTML =
        '<div class="cm-empty"><p class="cm-empty-title">列表加载失败</p><p>' +
        escapeHTML(err.message) + "</p></div>";
    });
  }

  function fetchDetail(pid) {
    request("/posts/" + pid).then(function (d) {
      STATE.detail[pid] = d;
      var art = document.querySelector('.cm-post[data-id="' + pid + '"]');
      if (!art) return;
      var old = art.querySelector(".cm-comments");
      var html = renderComments(pid, d);
      if (old) {
        old.outerHTML = html;
      } else {
        art.insertAdjacentHTML("beforeend", html);
      }
    }).catch(function (err) {
      var art = document.querySelector('.cm-post[data-id="' + pid + '"]');
      if (art) {
        var old = art.querySelector(".cm-comments");
        if (old) old.innerHTML = '<p class="cm-meta">评论加载失败：' + escapeHTML(err.message) + "</p>";
      }
    });
  }

  function toggleOpen(pid) {
    if (STATE.openId === pid) {
      STATE.openId = null;
      var art = document.querySelector('.cm-post[data-id="' + pid + '"]');
      if (art) {
        var c = art.querySelector(".cm-comments");
        if (c) c.remove();
        var btn = art.querySelector(".cm-expand");
        if (btn) btn.textContent = "展开";
      }
      return;
    }
    STATE.openId = pid;
    var prev = document.querySelectorAll(".cm-post .cm-comments");
    for (var i = 0; i < prev.length; i++) prev[i].remove();
    var buttons = document.querySelectorAll(".cm-post .cm-expand");
    for (var j = 0; j < buttons.length; j++) buttons[j].textContent = "展开";

    var post = document.querySelector('.cm-post[data-id="' + pid + '"]');
    if (!post) return;
    post.insertAdjacentHTML("beforeend", renderComments(pid, STATE.detail[pid]));
    var b = post.querySelector(".cm-expand");
    if (b) b.textContent = "收起";
    if (STATE.detail[pid] === null || STATE.detail[pid] === undefined) fetchDetail(pid);
  }

  // ---------- 点赞 ----------
  function like(pid, btn) {
    if (STATE.likes[pid]) return;
    STATE.likes[pid] = true;
    btn.disabled = true;
    request("/posts/" + pid + "/like", { method: "POST", body: {} }).then(function (d) {
      var span = btn.querySelector("span");
      if (span) span.textContent = d.likes;
      btn.classList.add("is-liked");
    }).catch(function () {
      STATE.likes[pid] = false;
      delete STATE.likes[pid];
    }).then(function () {
      btn.disabled = false;
    });
  }

  // ---------- 发帖 ----------
  var composing = false;

  function openCompose(open) {
    composing = open;
    $("cmForm").hidden = !open;
    $("cmComposeTrigger").setAttribute("aria-expanded", open ? "true" : "false");
    if (open) $("cmTitle").focus();
  }

  function syncComposeLabel() {
    var isNote = STATE.type === "note";
    $("cmComposeLabel").textContent = isNote ? "写下今天的盘面判断" : "留一句话";
    $("cmTitleField").hidden = !isNote;
    $("cmCategory").parentNode.hidden = !isNote;
    $("cmTags").parentNode.hidden = !isNote;
  }

  function submitPost(e) {
    e.preventDefault();
    var isNote = STATE.type === "note";
    var body = $("cmBody").value.trim();
    var msg = $("cmFormMsg");
    msg.className = "cm-form-msg";

    if (body.length < 2) {
      msg.textContent = "内容太短，至少 2 个字";
      msg.className = "cm-form-msg is-error";
      return;
    }
    if (isNote && !$("cmTitle").value.trim()) {
      msg.textContent = "经验总结需要填标题";
      msg.className = "cm-form-msg is-error";
      return;
    }

    var payload = {
      type: STATE.type,
      body: body,
      author: $("cmAuthor").value.trim() || "匿名",
    };
    if (isNote) {
      payload.title = $("cmTitle").value.trim();
      payload.category = $("cmCategory").value;
      payload.tags = $("cmTags").value;
    }

    var btn = $("cmSubmit");
    btn.disabled = true;
    msg.textContent = "发布中…";

    request("/posts", { method: "POST", body: payload }).then(function () {
      msg.textContent = "已发布";
      msg.className = "cm-form-msg is-ok";
      // 记住署名，下次自动填
      try { localStorage.setItem("tide.author", payload.author); } catch (err) { /* 隐私模式 */ }
      $("cmBody").value = "";
      $("cmTitle").value = "";
      $("cmTags").value = "";
      $("cmCount").textContent = "0";
      STATE.page = 1;
      STATE.detail = {};
      loadList();
      loadStats();
      setTimeout(function () { openCompose(false); msg.textContent = ""; }, 900);
    }).catch(function (err) {
      msg.textContent = err.message;
      msg.className = "cm-form-msg is-error";
    }).then(function () {
      btn.disabled = false;
    });
  }

  // ---------- 评论 ----------
  function submitComment(form) {
    var pid = Number(form.getAttribute("data-comment"));
    var authorEl = form.querySelector(".cm-input-author");
    var bodyEl = form.querySelector(".cm-input-body");
    var body = bodyEl.value.trim();
    if (!body) { bodyEl.focus(); return; }

    var btn = form.querySelector("button");
    btn.disabled = true;

    request("/posts/" + pid + "/comments", {
      method: "POST",
      body: { body: body, author: (authorEl.value.trim() || "匿名") },
    }).then(function () {
      bodyEl.value = "";
      STATE.detail[pid] = null;
      fetchDetail(pid);
      // 同步列表上的评论计数
      var card = document.querySelector('.cm-post[data-id="' + pid + '"]');
      if (card) {
        var countBtn = card.querySelector('[data-open="' + pid + '"] span');
        if (countBtn) {
          var m = countBtn.textContent.match(/\d+/);
          var n = m ? Number(m[0]) + 1 : 1;
          countBtn.textContent = n + " 条评论";
        }
      }
      loadStats();
    }).catch(function (err) {
      alert(err.message);
    }).then(function () {
      btn.disabled = false;
    });
  }

  // ---------- 事件绑定 ----------
  function bind() {
    // Tab
    document.querySelectorAll(".cm-tab").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (btn.classList.contains("is-active")) return;
        document.querySelectorAll(".cm-tab").forEach(function (b) {
          b.classList.remove("is-active");
          b.setAttribute("aria-selected", "false");
        });
        btn.classList.add("is-active");
        btn.setAttribute("aria-selected", "true");
        STATE.type = btn.getAttribute("data-type");
        STATE.page = 1;
        STATE.openId = null;
        STATE.detail = {};
        STATE.category = "";
        document.querySelectorAll(".cm-chip").forEach(function (c) {
          c.classList.toggle("is-active", c.getAttribute("data-cat") === "");
        });
        syncComposeLabel();
        loadList();
      });
    });

    // 分类 chips
    document.querySelectorAll(".cm-chip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        document.querySelectorAll(".cm-chip").forEach(function (c) { c.classList.remove("is-active"); });
        chip.classList.add("is-active");
        STATE.category = chip.getAttribute("data-cat");
        STATE.page = 1;
        loadList();
      });
    });

    // 搜索（防抖）
    var timer = null;
    $("cmSearch").addEventListener("input", function (e) {
      clearTimeout(timer);
      var v = e.target.value.trim();
      timer = setTimeout(function () {
        STATE.q = v;
        STATE.page = 1;
        loadList();
      }, 320);
    });

    // 发帖
    $("cmComposeTrigger").addEventListener("click", function () { openCompose(true); });
    $("cmCancel").addEventListener("click", function () { openCompose(false); });
    $("cmForm").addEventListener("submit", submitPost);
    $("cmBody").addEventListener("input", function (e) {
      $("cmCount").textContent = e.target.value.length;
    });

    // 列表事件委托
    $("cmFeed").addEventListener("click", function (e) {
      var likeBtn = e.target.closest("[data-like]");
      if (likeBtn) {
        like(Number(likeBtn.getAttribute("data-like")), likeBtn);
        return;
      }
      var openBtn = e.target.closest("[data-open]");
      if (openBtn) {
        toggleOpen(Number(openBtn.getAttribute("data-open")));
      }
    });

    $("cmFeed").addEventListener("submit", function (e) {
      var form = e.target.closest("[data-comment]");
      if (form) {
        e.preventDefault();
        submitComment(form);
      }
    });

    // 分页
    $("cmPrev").addEventListener("click", function () {
      if (STATE.page > 1) { STATE.page -= 1; STATE.openId = null; loadList(); window.scrollTo({ top: 240, behavior: "smooth" }); }
    });
    $("cmNext").addEventListener("click", function () {
      if (STATE.page < STATE.pages) { STATE.page += 1; STATE.openId = null; loadList(); window.scrollTo({ top: 240, behavior: "smooth" }); }
    });
  }

  // ---------- 初始化 ----------
  function init() {
    bind();
    applyConfig();     // 先按默认（留言板关闭）收敛界面，避免先闪一下匿名白板
    syncComposeLabel();
    try {
      var saved = localStorage.getItem("tide.author");
      if (saved) $("cmAuthor").value = saved;
    } catch (e) { /* 隐私模式忽略 */ }

    request("/config")
      .then(function (c) {
        CFG = {
          messageBoard: c.messageBoard !== false,
          comments: c.comments !== false,
          notice: c.notice || "",
        };
        applyConfig();
      })
      .catch(function () { /* 取不到开关就沿用默认关闭，宁可少一个入口 */ })
      .then(function () {
        loadStats();
        loadList();
      });

    // 底部显示当前盘面交易日，失败就算了
    fetch("tide_data.json", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var el = $("cmTradeDate");
        if (el && d && d.meta && d.meta.tradeDate) el.textContent = d.meta.tradeDate;
      })
      .catch(function () {});
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
