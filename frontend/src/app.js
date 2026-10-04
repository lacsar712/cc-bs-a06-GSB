import m from "mithril";

const TOKEN_KEY = "bridge_strain_token";
const USER_KEY = "bridge_strain_user";

// ---------------------------------------------------------------------------
// 结论标签：总览列表与读数详情共用这唯一一个渲染出口，保证「同色同字」。
// 着色只认单据自身领单瞬间抄下的快照（row.warning / row.verdict / row.status），
// 不再回头查当前黄带，杜绝两套边界漏一环。
// ---------------------------------------------------------------------------
function verdictBadge(row) {
  if (!row.verdict) {
    if (row.status === "processing") return m("span.tag.wait", "处理中");
    return m("span.tag.wait", "待处理");
  }
  if (row.warning) return m("span.tag.warn", "警戒");
  if (row.verdict === "合格") return m("span.tag.pass", "合格");
  return m("span.tag.fail", "越界");
}

// 样例色块也走同一标签：按当前边界把样例值映射成与单据同构的 pseudo-row。
function sampleRow(value, inner, outer) {
  const inBand = inner <= value && value <= outer;
  const inPass = 80 <= value && value <= 220;
  return {
    status: "done",
    verdict: inBand || inPass ? "合格" : "越界",
    warning: inBand,
  };
}

const state = {
  token: localStorage.getItem(TOKEN_KEY) || "",
  user: null,
  route: "overview",
  routeParam: null,
  loginForm: { username: "surveyor", password: "surv123456" },
  submitForm: { span_code: "", microstrain: "" },
  rows: [],
  band: null, // 当前生效黄带
  versions: [], // 改档流水
  detail: null, // 读数详情
  bandForm: { warn_inner: "", warn_outer: "", note: "" },
  error: "",
  msg: "",
  loading: false,
  timer: null,
};

try {
  state.user = JSON.parse(localStorage.getItem(USER_KEY) || "null");
} catch {
  state.user = null;
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const res = await fetch(path, { ...opts, headers });
  const text = await res.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { detail: text };
  }
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function stopTimer() {
  if (state.timer) {
    clearInterval(state.timer);
    state.timer = null;
  }
}

function startTimer(fn) {
  stopTimer();
  if (!state.token) return;
  state.timer = setInterval(fn, 3000);
}

async function loadReadings() {
  if (!state.token) return;
  try {
    state.rows = await api("/api/readings");
    state.error = "";
  } catch {
    state.error = "加载列表失败，请重新登录";
  }
  m.redraw();
}

async function loadBand() {
  try {
    const [b, vs] = await Promise.all([
      api("/api/warning-band"),
      api("/api/warning-band/versions"),
    ]);
    state.band = b.band;
    state.versions = vs;
    // 表单首次打开时用当前生效版本预填；用户已输入或已改档后不覆盖
    if (state.bandForm.warn_inner === "" && state.band) {
      state.bandForm.warn_inner = String(num(state.band.warn_inner));
      state.bandForm.warn_outer = String(num(state.band.warn_outer));
    }
    state.error = "";
  } catch (e) {
    state.error = e.message || "加载警戒带失败";
  }
  m.redraw();
}

async function loadDetail(id) {
  try {
    state.detail = await api(`/api/readings/${id}`);
    state.error = "";
  } catch (e) {
    state.error = e.message || "加载详情失败";
    state.detail = null;
  }
  m.redraw();
}

// ---- 极简 hash 路由：#/ | #/band | #/reading/:id ----
function parseHash() {
  const h = location.hash.replace(/^#/, "");
  if (h === "/band") {
    state.route = "band";
    state.routeParam = null;
  } else if (h.startsWith("/reading/")) {
    state.route = "detail";
    state.routeParam = h.slice("/reading/".length);
  } else {
    state.route = "overview";
    state.routeParam = null;
  }
}

window.addEventListener("hashchange", () => {
  parseHash();
  routeChanged();
});

function routeChanged() {
  stopTimer();
  state.error = "";
  state.msg = "";
  if (!state.token) return;
  if (state.route === "overview") {
    loadReadings();
    startTimer(loadReadings);
  } else if (state.route === "band") {
    loadBand();
  } else if (state.route === "detail") {
    loadDetail(state.routeParam);
    startTimer(() => {
      if (state.detail && state.detail.status !== "done") {
        loadDetail(state.routeParam);
      }
    });
  }
}

function logout() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  state.token = "";
  state.user = null;
  state.rows = [];
  stopTimer();
  location.hash = "#/";
}

function fmtTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return iso;
  }
}

function num(x) {
  if (x === null || x === undefined) return "—";
  return String(parseFloat(x));
}

// ---------------------------------------------------------------------------
// 登录页
// ---------------------------------------------------------------------------
const LoginPage = {
  view() {
    return m("div.wrap", [
      m("h1", "桥梁应变班交台"),
      m(
        "p.sub",
        "测量员提交跨段编号与微应变读数，后台工人认领队列后判定合格、警戒或越界。"
      ),
      m("div.card", [
        m(
          "form",
          {
            onsubmit: async (e) => {
              e.preventDefault();
              state.error = "";
              state.loading = true;
              try {
                const data = await api("/api/auth/login", {
                  method: "POST",
                  body: JSON.stringify(state.loginForm),
                });
                state.token = data.access_token;
                state.user = { username: data.username, role: data.role };
                localStorage.setItem(TOKEN_KEY, state.token);
                localStorage.setItem(USER_KEY, JSON.stringify(state.user));
                parseHash();
                routeChanged();
              } catch {
                state.error = "用户名或密码错误";
              } finally {
                state.loading = false;
                m.redraw();
              }
            },
          },
          [
            m("div.row", [
              m("label", [
                "用户名",
                m("input", {
                  value: state.loginForm.username,
                  oninput: (e) => {
                    state.loginForm.username = e.target.value;
                  },
                }),
              ]),
              m("label", [
                "密码",
                m("input", {
                  type: "password",
                  value: state.loginForm.password,
                  oninput: (e) => {
                    state.loginForm.password = e.target.value;
                  },
                }),
              ]),
              m("button", { type: "submit", disabled: state.loading }, "登录"),
            ]),
            state.error ? m("p.err", state.error) : null,
          ]
        ),
        m(
          "p.sub",
          { style: { marginBottom: 0 } },
          "测量员 surveyor / surv123456 · 复核员 reviewer / rev123456"
        ),
      ]),
    ]);
  },
};

// ---------------------------------------------------------------------------
// 顶栏
// ---------------------------------------------------------------------------
function TopBar(isWriter) {
  return m("div.topbar", [
    m("div", [
      m("h1", "桥梁应变班交台"),
      m("p.sub", "微应变 80～220 με 为合格；落入警戒黄带须警戒；越过外沿为越界。"),
      m("div.nav", [
        m(
          "a.navlink",
          { href: "#/", class: state.route === "overview" ? "active" : "" },
          "总览"
        ),
        m(
          "a.navlink",
          {
            href: "#/band",
            class: state.route === "band" ? "active" : "",
          },
          [m("span.band-dot"), "警戒带"]
        ),
      ]),
    ]),
    m("div", [
      `${state.user?.username}（${isWriter ? "测量员" : "复核员"}） `,
      m("button.secondary", { type: "button", onclick: logout }, "退出"),
    ]),
  ]);
}

// ---------------------------------------------------------------------------
// 总览
// ---------------------------------------------------------------------------
const OverviewPage = {
  view() {
    const isWriter = state.user?.role === "writer";
    return [
      isWriter
        ? m("div.card", [
            m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "提交读数"),
            m(
              "form",
              {
                onsubmit: async (e) => {
                  e.preventDefault();
                  state.error = "";
                  state.msg = "";
                  state.loading = true;
                  try {
                    const data = await api("/api/readings", {
                      method: "POST",
                      body: JSON.stringify({
                        span_code: state.submitForm.span_code,
                        microstrain: parseFloat(state.submitForm.microstrain),
                      }),
                    });
                    state.msg = data.message || "已提交";
                    state.submitForm = { span_code: "", microstrain: "" };
                    await loadReadings();
                  } catch (err) {
                    state.error = err.message || "提交失败";
                  } finally {
                    state.loading = false;
                    m.redraw();
                  }
                },
              },
              [
                m("div.row", [
                  m("label", [
                    "跨段编号",
                    m("input", {
                      required: true,
                      placeholder: "例如 跨中S3",
                      value: state.submitForm.span_code,
                      oninput: (e) => {
                        state.submitForm.span_code = e.target.value;
                      },
                    }),
                  ]),
                  m("label", [
                    "微应变（με）",
                    m("input", {
                      required: true,
                      type: "number",
                      step: "0.1",
                      value: state.submitForm.microstrain,
                      oninput: (e) => {
                        state.submitForm.microstrain = e.target.value;
                      },
                    }),
                  ]),
                  m("button", { type: "submit", disabled: state.loading }, "提交"),
                ]),
                state.error ? m("p.err", state.error) : null,
                state.msg ? m("p.ok", state.msg) : null,
              ]
            ),
          ])
        : null,
      m("div.card", [
        m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "读数列表"),
        m("table", [
          m("thead", [
            m("tr", [
              m("th", "编号"),
              m("th", "跨段"),
              m("th", "微应变"),
              m("th", "结论"),
              m("th", "说明"),
              m("th", "状态"),
              m("th", "提交人"),
              m("th", ""),
            ]),
          ]),
          m(
            "tbody",
            state.rows.length
              ? state.rows.map((r) =>
                  m("tr", { key: r.id, class: r.warning ? "row-warn" : "" }, [
                    m("td", r.id),
                    m("td", r.span_code),
                    m("td", r.microstrain),
                    m("td", verdictBadge(r)),
                    m("td", r.reason || "—"),
                    m("td", r.status === "done" ? "已判定" : r.status === "processing" ? "判定中" : "待处理"),
                    m("td", r.created_by),
                    m("td", m("a.linky", { href: `#/reading/${r.id}` }, "详情")),
                  ])
                )
              : [m("tr", m("td", { colspan: 8 }, "暂无数据"))]
          ),
        ]),
      ]),
    ];
  },
};

// ---------------------------------------------------------------------------
// 警戒带专页：边界设置 + 样例色块 + 改档流水
// ---------------------------------------------------------------------------
function buildSwatches(inner, outer) {
  // 色块只给取值与位置说明；颜色与字样一律由 verdictBadge(sampleRow(...)) 权威计算，
  // 保证样例与单据判定永远一致。
  const items = [];
  // 合格带内、黄带外（仅当黄带内沿高于合格带下沿 80 时存在）
  if (inner > 80) items.push({ v: 80, tag: "合格带内·黄带外" });
  items.push({ v: inner, tag: "黄带内沿" });
  const mid = Math.round(((inner + outer) / 2) * 10) / 10;
  if (mid !== inner && mid !== outer) items.push({ v: mid, tag: "黄带内" });
  items.push({ v: outer, tag: "黄带外沿" });
  // 越过外沿：取高于黄带外沿与合格带外沿之上，必判越界
  items.push({ v: Math.max(outer, 220) + 10, tag: "越过外沿·越界" });
  return items;
}

const BandPage = {
  view() {
    const isWriter = state.user?.role === "writer";
    const band = state.band;
    const inner = parseFloat(state.bandForm.warn_inner);
    const outer = parseFloat(state.bandForm.warn_outer);
    const valid = Number.isFinite(inner) && Number.isFinite(outer) && inner < outer;

    return [
      m("div.card", [
        m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "当前生效黄带"),
        band
          ? m("p", { style: { margin: 0 } }, [
              `内边界 `,
              m("strong", `${num(band.warn_inner)} με`),
              `　外边界 `,
              m("strong", `${num(band.warn_outer)} με`),
              `（第 ${band.id} 版，${band.changed_by} 改于 ${fmtTime(band.changed_at)}）`,
            ])
          : m("p", { style: { margin: 0, color: "#555" } }, "尚未设置黄带，当前所有读数仅按合格带 80～220 με 判定。"),
      ]),

      m("div.card", [
        m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "边界设置"),
        isWriter
          ? m(
              "form",
              {
                onsubmit: async (e) => {
                  e.preventDefault();
                  state.error = "";
                  state.msg = "";
                  if (!valid) {
                    state.error = "内边界必须小于外边界，且均为数字";
                    m.redraw();
                    return;
                  }
                  state.loading = true;
                  try {
                    await api("/api/warning-band", {
                      method: "PUT",
                      body: JSON.stringify({
                        warn_inner: inner,
                        warn_outer: outer,
                        note: state.bandForm.note || null,
                      }),
                    });
                    state.msg = "黄带已改档，只影响之后新领走的单据；已领单据沿用领单时边界。";
                    state.bandForm.note = "";
                    await loadBand();
                  } catch (err) {
                    state.error = err.message || "改档失败";
                  } finally {
                    state.loading = false;
                    m.redraw();
                  }
                },
              },
              [
                m("div.row", [
                  m("label", [
                    "内边界（με）",
                    m("input", {
                      type: "number",
                      step: "0.1",
                      value: state.bandForm.warn_inner,
                      oninput: (e) => {
                        state.bandForm.warn_inner = e.target.value;
                      },
                    }),
                  ]),
                  m("label", [
                    "外边界（με）",
                    m("input", {
                      type: "number",
                      step: "0.1",
                      value: state.bandForm.warn_outer,
                      oninput: (e) => {
                        state.bandForm.warn_outer = e.target.value;
                      },
                    }),
                  ]),
                  m("label", [
                    "备注（可选）",
                    m("input", {
                      placeholder: "本次改档说明",
                      value: state.bandForm.note,
                      oninput: (e) => {
                        state.bandForm.note = e.target.value;
                      },
                    }),
                  ]),
                  m("button", { type: "submit", disabled: state.loading || !valid }, "改档"),
                ]),
                m("p.sub", { style: { margin: "0.5rem 0 0" } }, "示例：设为 200～220，则 210 判警戒（仍合格、可入队），230 才判越界。"),
                state.msg ? m("p.ok", state.msg) : null,
              ]
            )
          : m("p.lock", [m("span.lockico", "🔒"), "复核侧只读：可查看黄带与流水，不能修改边界。"]),
      ]),

      m("div.card", [
        m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "样例色块"),
        m(
          "p.sub",
          { style: { marginTop: 0 } },
          valid
            ? `按 ${num(inner)}～${num(outer)} με 着色，与读数列表/详情同一标签、同一颜色与字样。`
            : "填写有效的内/外边界后预览。"
        ),
        valid
          ? m(
              "div.swatches",
              buildSwatches(inner, outer).map((s) =>
                m("div.swatch", [
                  m("div.swatch-v", `${num(s.v)} με`),
                  verdictBadge(sampleRow(s.v, inner, outer)),
                  m("div.swatch-tag", s.tag),
                ])
              )
            )
          : null,
      ]),

      m("div.card", [
        m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "改档流水"),
        m("table", [
          m("thead", [
            m("tr", [
              m("th", "版本"),
              m("th", "内边界"),
              m("th", "外边界"),
              m("th", "改动人"),
              m("th", "改动时间"),
              m("th", "备注"),
            ]),
          ]),
          m(
            "tbody",
            state.versions.length
              ? state.versions.map((b) =>
                  m("tr", { key: b.id }, [
                    m("td", `#${b.id}`),
                    m("td", `${num(b.warn_inner)} με`),
                    m("td", `${num(b.warn_outer)} με`),
                    m("td", b.changed_by),
                    m("td", fmtTime(b.changed_at)),
                    m("td", b.note || "—"),
                  ])
                )
              : [m("tr", m("td", { colspan: 6 }, "暂无改档记录"))]
          ),
        ]),
      ]),
    ];
  },
};

// ---------------------------------------------------------------------------
// 读数详情：脚注与列表共用同一份快照边界
// ---------------------------------------------------------------------------
const DetailPage = {
  view() {
    const r = state.detail;
    if (!r) {
      return m("div.card", [
        m("p", { style: { margin: 0 } }, "单据不存在或加载中……"),
        m("a.linky", { href: "#/" }, "← 返回总览"),
      ]);
    }
    return m("div.card", [
      m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, [
        `读数 #${r.id}　`,
        verdictBadge(r),
      ]),
      m("table.kv", [
        m("tbody", [
          m("tr", [m("th", "跨段"), m("td", r.span_code)]),
          m("tr", [m("th", "微应变"), m("td", `${r.microstrain} με`)]),
          m("tr", [m("th", "结论"), m("td", verdictBadge(r))]),
          m("tr", [m("th", "说明"), m("td", r.reason || "—")]),
          m("tr", [
            m("th", "状态"),
            m("td", r.status === "done" ? "已判定" : r.status === "processing" ? "判定中" : "待处理"),
          ]),
          m("tr", [m("th", "提交人"), m("td", r.created_by)]),
          m("tr", [m("th", "提交时间"), m("td", fmtTime(r.created_at))]),
          m("tr", [m("th", "判定时间"), m("td", fmtTime(r.processed_at))]),
        ]),
      ]),

      // 详情脚注：边界直接取单据快照 r.band / r.warn_*，与标签同源
      m("div.footnote", [
        m("div.footnote-title", "判定边界脚注"),
        r.band
          ? [
              m("p", { style: { margin: "0.35rem 0" } }, [
                `本单在领单瞬间抄下第 `,
                m("strong", `#${r.band.version_id}`),
                ` 版警戒黄带：内边界 `,
                m("strong", `${num(r.band.warn_inner)} με`),
                `、外边界 `,
                m("strong", `${num(r.band.warn_outer)} με`),
                `（${r.band.changed_by} 改于 ${fmtTime(r.band.changed_at)}）。`,
              ]),
              r.warning
                ? m("p", { style: { margin: "0.35rem 0" } }, [
                    "读数 ",
                    m("strong", `${r.microstrain} με`),
                    ` 落入该黄带，结论仍为合格但须标「警戒」；越过外沿 ${num(
                      r.band.warn_outer
                    )} με 才判越界。`,
                  ])
                : m("p", { style: { margin: "0.35rem 0" } }, "读数未落入该黄带。"),
              m("p", { style: { margin: "0.35rem 0", color: "#555" } }, "黄带之后若改档，不影响本单，仍按上述领单时边界判定。"),
            ]
          : m("p", { style: { margin: "0.35rem 0" } }, "本单领取时未设置黄带，按合格带 80～220 με 判定。"),
      ]),

      m("a.linky", { href: "#/" }, "← 返回总览"),
    ]);
  },
};

// ---------------------------------------------------------------------------
const App = {
  oninit() {
    parseHash();
    if (state.token) routeChanged();
  },
  onremove() {
    stopTimer();
  },
  view() {
    if (!state.token) return m(LoginPage);
    const isWriter = state.user?.role === "writer";
    let page;
    if (state.route === "band") page = m(BandPage);
    else if (state.route === "detail") page = m(DetailPage);
    else page = m(OverviewPage);

    return m("div.wrap", [
      TopBar(isWriter),
      state.error ? m("p.err", state.error) : null,
      page,
    ]);
  },
};

export default App;
