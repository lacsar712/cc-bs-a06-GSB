import m from "mithril";

const TOKEN_KEY = "bridge_strain_token";
const USER_KEY = "bridge_strain_user";

// 唯一的结论着色/措辞来源：总览表与详情弹层都调用它，保证“同色同字”。
// 警戒黄带读数仍是“合格”，但文案与颜色统一为黄底“合格 · 警戒”。
function verdictDisplay(row) {
  if (row && row.verdict === "合格") {
    if (row.warning) return { text: "合格 · 警戒", cls: "tag warn" };
    return { text: "合格", cls: "tag pass" };
  }
  if (row && row.verdict === "越界") {
    return { text: "越界", cls: "tag fail" };
  }
  if (row && row.status === "processing") {
    return { text: "处理中", cls: "tag wait" };
  }
  return { text: "待处理", cls: "tag wait" };
}

function fmtTime(iso) {
  if (!iso) return "—";
  return iso.replace("T", " ").slice(0, 16);
}

function fmtNum(x) {
  if (x === null || x === undefined) return "—";
  const n = Number(x);
  return Number.isInteger(n) ? String(n) : String(n);
}

// 详情脚注：展示的是“领单瞬间抄下”的黄带边界快照，与判定同源，绝不读当前配置。
function bandSnapshotNote(row) {
  if (!row || row.status !== "done") {
    return "本单尚未完成判定。";
  }
  if (row.band_enabled === null || row.band_enabled === undefined) {
    return "本单判定时未记录黄带边界。";
  }
  if (!row.band_enabled) {
    return `本单于 ${fmtTime(row.processed_at)} 判定，当时黄带处于停用状态，按合格带边界判定。`;
  }
  return (
    `本单于 ${fmtTime(row.processed_at)} 判定，沿用领单瞬间抄下的黄带边界 ` +
    `${fmtNum(row.band_inner)}～${fmtNum(row.band_outer)} με；` +
    `黄带改档不影响已认领的本单。`
  );
}

const state = {
  token: localStorage.getItem(TOKEN_KEY) || "",
  user: null,
  loginForm: { username: "surveyor", password: "surv123456" },
  submitForm: { span_code: "", microstrain: "" },
  rows: [],
  error: "",
  msg: "",
  loading: false,
  timer: null,
  view: "list", // "list" | "band"
  band: null,
  bandForm: { enabled: true, inner_edge: "", outer_edge: "", note: "" },
  history: [],
  detail: null,
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
  if (!state.token) return;
  try {
    state.band = await api("/api/warning-band");
    // 仅在首次进入时用当前配置初始化表单；轮询刷新不得冲掉正在编辑的值。
    if (state.bandForm.inner_edge === "") {
      state.bandForm = {
        enabled: state.band.enabled,
        inner_edge: fmtNum(state.band.inner_edge),
        outer_edge: fmtNum(state.band.outer_edge),
        note: "",
      };
    }
    state.history = await api("/api/warning-band/history");
  } catch (err) {
    state.error = err.message || "加载警戒带失败";
  }
  m.redraw();
}

function startPolling() {
  if (state.timer) clearInterval(state.timer);
  if (!state.token) return;
  state.timer = setInterval(() => {
    loadReadings();
    if (state.view === "band") loadBand();
  }, 3000);
}

function logout() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  state.token = "";
  state.user = null;
  state.rows = [];
  state.band = null;
  state.history = [];
  state.bandForm = { enabled: true, inner_edge: "", outer_edge: "", note: "" };
  state.detail = null;
  state.view = "list";
  if (state.timer) clearInterval(state.timer);
}

// ---------------------------------------------------------------- 详情弹层
const DetailModal = {
  view() {
    // 按 id 从最新列表解析，保证轮询判定后详情实时更新。
    const r = state.detail
      ? state.rows.find((x) => x.id === state.detail.id) || state.detail
      : null;
    if (!r) return null;
    const v = verdictDisplay(r);
    return m("div.modal-mask", {
      onclick: () => {
        state.detail = null;
      },
    }, [
      m("div.modal", { onclick: (e) => e.stopPropagation() }, [
        m("div.modal-head", [
          m("h2", { style: { margin: 0, fontSize: "1.1rem" } }, `读数 #${r.id} 详情`),
          m("button.secondary", {
            type: "button",
            onclick: () => {
              state.detail = null;
            },
          }, "关闭"),
        ]),
        m("table.detail-table", [
          m("tbody", [
            m("tr", [m("th", "跨段编号"), m("td", r.span_code)]),
            m("tr", [m("th", "微应变"), m("td", `${r.microstrain} με`)]),
            m("tr", [
              m("th", "结论"),
              // 与总览同一个 helper：同色同字。
              m("td", m("span", { class: v.cls }, v.text)),
            ]),
            m("tr", [m("th", "说明"), m("td", r.reason || "—")]),
            m("tr", [m("th", "处理状态"), m("td", r.status)]),
            m("tr", [m("th", "提交人"), m("td", r.created_by)]),
            m("tr", [m("th", "提交时间"), m("td", fmtTime(r.created_at))]),
            m("tr", [m("th", "判定时间"), m("td", fmtTime(r.processed_at))]),
          ]),
        ]),
        // 脚注与判定共用同一套（快照）边界。
        m("p.footnote", bandSnapshotNote(r)),
      ]),
    ]);
  },
};

// ---------------------------------------------------------------- 警戒带专页
function BandPage() {
  const isWriter = state.user?.role === "writer";
  const b = state.band;

  const swatches = [
    { cls: "tag pass", label: "合格", range: `合格带内、黄带以外（< ${b ? fmtNum(b.inner_edge) : "—"} με）` },
    { cls: "tag warn", label: "合格 · 警戒", range: b ? `落入黄带 ${fmtNum(b.inner_edge)}～${fmtNum(b.outer_edge)} με` : "落入黄带" },
    { cls: "tag fail", label: "越界", range: `越过黄带外沿（> ${b ? fmtNum(b.outer_edge) : "—"} με）或低于合格下限` },
    { cls: "tag wait", label: "待处理 / 处理中", range: "已入队，等待或正在被工人认领" },
  ];

  return [
    m("div.card", [
      m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "黄带边界设置"),
      b
        ? m("p.sub", { style: { marginBottom: "0.75rem" } },
            `合格带固定 ${fmtNum(b.pass_low)}～${fmtNum(b.pass_high)} με；` +
            `黄带须设在合格带外侧（贴近上限）。当前生效：` +
            (b.enabled
              ? `${fmtNum(b.inner_edge)}～${fmtNum(b.outer_edge)} με`
              : "已停用") +
            (b.updated_by ? `，最后由 ${b.updated_by} 于 ${fmtTime(b.updated_at)} 改档。` : "。"))
        : m("p.sub", "加载中…"),
      m(
        "form",
        {
          onsubmit: async (e) => {
            e.preventDefault();
            state.error = "";
            state.msg = "";
            state.loading = true;
            try {
              const cfg = await api("/api/warning-band", {
                method: "PUT",
                body: JSON.stringify({
                  enabled: state.bandForm.enabled,
                  inner_edge: parseFloat(state.bandForm.inner_edge),
                  outer_edge: parseFloat(state.bandForm.outer_edge),
                  note: state.bandForm.note || null,
                }),
              });
              state.msg = `黄带已改档为 ${fmtNum(cfg.inner_edge)}～${fmtNum(cfg.outer_edge)} με，仅影响此后新认领的单据`;
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
            m("label.check", [
              m("input", {
                type: "checkbox",
                style: { minWidth: 0 },
                checked: state.bandForm.enabled,
                disabled: !isWriter,
                onchange: (e) => {
                  state.bandForm.enabled = e.target.checked;
                },
              }),
              "启用黄带",
            ]),
            m("label", [
              "黄带内沿（με）",
              m("input", {
                type: "number",
                step: "0.1",
                value: state.bandForm.inner_edge,
                disabled: !isWriter,
                oninput: (e) => {
                  state.bandForm.inner_edge = e.target.value;
                },
              }),
            ]),
            m("label", [
              "黄带外沿（με）",
              m("input", {
                type: "number",
                step: "0.1",
                value: state.bandForm.outer_edge,
                disabled: !isWriter,
                oninput: (e) => {
                  state.bandForm.outer_edge = e.target.value;
                },
              }),
            ]),
            isWriter
              ? m("button", { type: "submit", disabled: state.loading }, "保存改档")
              : null,
          ]),
          isWriter
            ? m("label", { style: { marginTop: "0.75rem", maxWidth: 360 } }, [
                "改档备注（可选，记入流水）",
                m("input", {
                  value: state.bandForm.note,
                  placeholder: "例如 雨季提高巡检等级",
                  oninput: (e) => {
                    state.bandForm.note = e.target.value;
                  },
                }),
              ])
            : m("p.err", { style: { color: "#7a5c00" } },
                "复核侧只能查看，不能修改黄带。"),
          state.error ? m("p.err", state.error) : null,
          state.msg ? m("p.ok", state.msg) : null,
        ]
      ),
    ]),

    m("div.card", [
      m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "样例色块"),
      m("p.sub", { style: { marginBottom: "0.75rem" } },
        "总览与详情中的结论标签与下列色块同色同字。"),
      m("div.swatches", swatches.map((s) =>
        m("div.swatch", [
          m("span", { class: s.cls, style: { minWidth: 110, textAlign: "center" } }, s.label),
          m("span.swatch-range", s.range),
        ])
      )),
      m("p.sub", { style: { marginTop: "0.75rem", marginBottom: 0 } },
        "示例：黄带 200～220 με 时，读数 210 → 合格 · 警戒（不是越界）；读数 230 → 越界。"),
    ]),

    m("div.card", [
      m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "改档流水"),
      m("table", [
        m("thead", [
          m("tr", [
            m("th", "#"),
            m("th", "时间"),
            m("th", "启用"),
            m("th", "内沿"),
            m("th", "外沿"),
            m("th", "操作人"),
            m("th", "备注"),
          ]),
        ]),
        m(
          "tbody",
          state.history.length
            ? state.history.map((h) =>
                m("tr", { key: h.id }, [
                  m("td", h.id),
                  m("td", fmtTime(h.changed_at)),
                  m("td", h.enabled ? "启用" : "停用"),
                  m("td", fmtNum(h.inner_edge)),
                  m("td", fmtNum(h.outer_edge)),
                  m("td", h.changed_by),
                  m("td", h.note || "—"),
                ])
              )
            : [m("tr", m("td", { colspan: 7 }, "暂无改档记录"))]
        ),
      ]),
    ]),
  ];
}

// ---------------------------------------------------------------- 总览列表
function ListPage(isWriter) {
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
      m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "读数总览"),
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
            ? state.rows.map((r) => {
                const v = verdictDisplay(r);
                return m("tr", {
                  key: r.id,
                  className: r.warning ? "row-warn" : "",
                }, [
                  m("td", r.id),
                  m("td", r.span_code),
                  m("td", r.microstrain),
                  // 与详情弹层同一个 helper：同色同字。
                  m("td", m("span", { class: v.cls }, v.text)),
                  m("td", r.reason || "—"),
                  m("td", r.status),
                  m("td", r.created_by),
                  m("td", m("button.secondary", {
                    type: "button",
                    onclick: () => {
                      state.detail = r;
                    },
                  }, "详情")),
                ]);
              })
            : [m("tr", m("td", { colspan: 8 }, "暂无数据"))]
        ),
      ]),
    ]),
  ];
}

const App = {
  oninit() {
    loadReadings();
    startPolling();
  },
  onremove() {
    if (state.timer) clearInterval(state.timer);
  },
  view() {
    if (!state.token) {
      return m(
        "div.wrap",
        [
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
                    await loadReadings();
                    startPolling();
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
        ]
      );
    }

    const isWriter = state.user?.role === "writer";

    return m("div.wrap", [
      m("div.topbar", [
        m("div", [
          m("h1", "桥梁应变班交台"),
          m("p.sub", "合格带外侧设警戒黄带；落入黄带仍合格但标警戒，越过黄带外沿才判越界。"),
        ]),
        m("div.topactions", [
          m(
            "button.secondary",
            {
              type: "button",
              className: state.view === "band" ? "nav-active" : "",
              onclick: () => {
                state.error = "";
                state.msg = "";
                if (state.view === "band") {
                  state.view = "list";
                } else {
                  state.view = "band";
                  loadBand();
                }
              },
            },
            state.view === "band" ? "← 返回总览" : "⚠ 警戒带"
          ),
          `${state.user?.username}（${isWriter ? "测量员" : "复核员"}） `,
          m("button.secondary", { type: "button", onclick: logout }, "退出"),
        ]),
      ]),
      state.view === "band" ? BandPage() : ListPage(isWriter),
      m(DetailModal),
    ]);
  },
};

export default App;
