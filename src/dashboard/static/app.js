// 종배 모니터링 대시보드 (M7 Phase 1)
// WebSocket 으로 server push 받고 카드 그리드 갱신. 보유/세션/감시 토글은 REST.
// 정책 (CLAUDE.md `자동 매매 절대 금지`): 거래소 주문 input X. holdings.json
// 토글만 허용.
(() => {
  "use strict";

  // ── State ──────────────────────────────────────────────────────────────────
  const state = {
    ws: null,
    reconnectAttempt: 0,
    cardEls: new Map(), // code -> DOM element
    pendingBuyCode: null,
    lastSnapshot: null, // 마지막 받은 snapshot — stale 검사용
  };

  // ── DOM helpers ────────────────────────────────────────────────────────────
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  function fmtPct(v) {
    if (v === null || v === undefined) return "—";
    const sign = v >= 0 ? "+" : "";
    return `${sign}${v.toFixed(1)}%`;
  }

  function fmtNum(v) {
    if (v === null || v === undefined) return "—";
    return v.toLocaleString("ko-KR");
  }

  function fmtBillion(v) {
    if (v === null || v === undefined || v === 0) return "—";
    const a = Math.abs(v);
    if (a >= 1e8) return `${Math.round(v / 1e8).toLocaleString("ko-KR")}억`;
    if (a >= 1e4) return `${Math.round(v / 1e4).toLocaleString("ko-KR")}만`;
    return v.toLocaleString("ko-KR");
  }

  function gradeClass(grade) {
    return grade ? `grade-${grade}` : "";
  }

  // round 35: multi-flag 라벨 조합. payload.flags = {auto, rising, manual, hold}
  // 켜진 flag 만 모아서 표시 — 예: "💎 보유 / 🔵 수동 / ⭐ 자동"
  function flagsLabel(flags) {
    const parts = [];
    if (!flags) return "";
    if (flags.hold) parts.push("💎 보유");
    if (flags.manual) parts.push("🔵 수동");
    if (flags.auto) parts.push("⭐ 자동");
    if (flags.rising) parts.push("⚡ 후보");
    return parts.join(" / ");
  }

  // source 별 정렬 우선순위 — 보유 → 수동 → 자동 → 후보.
  const SOURCE_ORDER = { hold: 0, manual: 1, auto: 2, rising: 3 };

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  function fmtRatio(v, suffix = "배") {
    if (v === null || v === undefined) return "—";
    return `${v.toFixed(1)}${suffix}`;
  }

  function fmtScore(v) {
    if (v === null || v === undefined) return "0.0";
    return (v >= 0 ? "+" : "") + v.toFixed(1);
  }

  // round 35: 카드 우상단 액션 버튼 — flag 조합별 분기.
  //   보유 flag O               → [✕ 청산]
  //   수동 flag X / 보유 X      → [→ 수동] [+ 보유]
  //   수동 flag O / 보유 X      → [× 해제] [+ 보유]
  // 모든 버튼은 telegram_bot 의 apply_command(toggle_code/buy/sell) 핸들러와 동일 효과.
  function buildActionButtons(payload) {
    const code = payload.code;
    const flags = payload.flags || {};
    const buttons = [];
    if (flags.hold) {
      buttons.push(`<button data-act="sell" data-code="${code}" class="text-[10px] px-2 py-0.5 rounded bg-rose-700 hover:bg-rose-600">✕ 청산</button>`);
    } else {
      // 수동 토글 버튼
      if (flags.manual) {
        buttons.push(`<button data-act="unwatch" data-code="${code}" class="text-[10px] px-2 py-0.5 rounded bg-slate-600 hover:bg-slate-500" title="수동 핀 해제">× 해제</button>`);
      } else {
        buttons.push(`<button data-act="promote" data-code="${code}" class="text-[10px] px-2 py-0.5 rounded bg-sky-700 hover:bg-sky-600" title="자동/후보 풀 이탈해도 유지">→ 수동</button>`);
      }
      buttons.push(`<button data-act="buy" data-code="${code}" class="text-[10px] px-2 py-0.5 rounded bg-emerald-700 hover:bg-emerald-600">+ 보유</button>`);
    }
    return `<div class="flex gap-1">${buttons.join("")}</div>`;
  }

  // ── Card render ────────────────────────────────────────────────────────────
  function renderCard(payload) {
    const code = payload.code;
    let el = state.cardEls.get(code);
    if (!el) {
      el = document.createElement("article");
      el.className = "rounded-md border-l-4 border-slate-600 bg-slate-800 p-2 text-xs";
      state.cardEls.set(code, el);
    }
    el.classList.remove("card-hold", "card-auto", "card-rising", "card-manual");
    el.classList.add(`card-${payload.source}`);

    const header = payload.header || {};
    const price = payload.price || {};
    const vol = payload.volume || {};
    const a5 = payload.accel_5m || {};
    const a1 = payload.accel_1m || {};
    const vp = payload.vp || {};
    const ask = payload.asking || {};
    const holding = payload.holding;
    const triggers = payload.trigger_states || {};
    const triggerLines = payload.trigger_lines || [];
    const transition = payload.transition;

    const grade = header.grade || "";
    const gradeSpan = grade
      ? `<span class="${gradeClass(grade)} font-bold">${grade} ${fmtScore(header.score)}점</span>`
      : "";
    const lupMark = price.is_limit_up ? ' <span class="text-rose-400">🔴상한가</span>' : "";

    const actions = buildActionButtons(payload);

    // 청산 시그널 — payload.trigger_lines 그대로 출력 (텔레그램과 동일 텍스트).
    // 발화 항목(✅) 이 하나라도 있으면 카드 빨간 펄스.
    const anyFired = Object.values(triggers).some((v) => v);
    if (anyFired) el.classList.add("pulse-trigger");
    else el.classList.remove("pulse-trigger");
    let triggerBlock = "";
    if (triggerLines.length) {
      const html = triggerLines.map((line) => {
        const fired = line.includes("✅");
        const cls = fired ? "text-rose-300" : "text-slate-400";
        return `<div class="${cls}">${escapeHtml(line)}</div>`;
      }).join("");
      triggerBlock = `<div class="mt-1 pt-1 border-t border-slate-700">${html}</div>`;
    }

    // Transition (a1 카드에 a2 부상 후보 표시)
    const transitionLine = transition && transition.state
      ? `<div class="text-violet-300">🔥 ${transition.state.toUpperCase()} a2: ${transition.candidate_code} (${fmtPct(transition.candidate_turnover)})</div>`
      : "";

    // 보유 정보
    let holdingLine = "";
    if (holding) {
      const elapsedMin = Math.floor(holding.elapsed_sec / 60);
      const elapsedSec = holding.elapsed_sec % 60;
      holdingLine = `
        <div class="text-cyan-300">매수 ${fmtNum(holding.entry_price)}원 → 손익 ${fmtPct(holding.pnl_pct)}  경과 ${elapsedMin}분 ${elapsedSec}초</div>
        <div class="text-slate-400">손절 ${fmtNum(holding.stop_loss_price)} / 익절1 ${fmtNum(holding.take_profit_1_price)} / 익절2 ${fmtNum(holding.take_profit_2_price)}</div>`;
    }

    const themes = (payload.themes || []).join(" / ") || "—";
    const reasons = (header.reasons || []).slice(0, 3).join(" / ");

    el.innerHTML = `
      <div class="flex items-center gap-2">
        <span class="font-bold text-slate-100">${escapeHtml(payload.name)}</span>
        <span class="text-slate-400">${code}</span>
        <span class="text-[10px] text-slate-300">${flagsLabel(payload.flags)}</span>
        ${gradeSpan}
        <span class="ml-auto">${actions}</span>
      </div>
      <div class="text-slate-400">테마: ${escapeHtml(themes)}</div>
      ${reasons ? `<div class="text-slate-300">사유: ${escapeHtml(reasons)}</div>` : ""}
      ${transitionLine}
      <div class="mt-1">
        <span class="text-slate-100 font-bold">${fmtNum(price.current)}원</span>
        <span class="text-slate-300">(${fmtPct(price.change_pct)})</span>${lupMark}
        ${price.sell_29_pct ? `<span class="text-slate-500 ml-2">+29% 매도 ${fmtNum(price.sell_29_pct)}</span>` : ""}
      </div>
      ${holdingLine}
      <div class="text-slate-400">거래대금 ${fmtBillion(vol.amount)} (${vol.rank ?? "—"}위) · 회전율 ${fmtPct(vol.turnover_pct)}</div>
      <div class="text-slate-400">5m가속 ${fmtRatio(a5.ratio)} · 1m가속 ${fmtRatio(a1.ratio)}</div>
      <div class="text-slate-400">체결강도 ${vp.current !== null && vp.current !== undefined ? vp.current.toFixed(0) : "—"} (5MA ${vp.ma_5 !== null && vp.ma_5 !== undefined ? vp.ma_5.toFixed(0) : "—"} / 1MA ${vp.ma_1 !== null && vp.ma_1 !== undefined ? vp.ma_1.toFixed(0) : "—"})</div>
      <div class="text-slate-400">호가 매수 ${fmtNum(ask.bid_total)} / 매도 ${fmtNum(ask.ask_total)} (${fmtRatio(ask.ratio)})</div>
      ${triggerBlock}
    `;
    return el;
  }

  function refreshStaleIndicator() {
    const snap = state.lastSnapshot;
    if (!snap || !snap.updated_at) {
      $("#updated-at").textContent = "갱신 —";
      $("#updated-at").className = "text-xs text-slate-500 ml-auto";
      return;
    }
    const ts = new Date(snap.updated_at);
    const ageSec = (Date.now() - ts.getTime()) / 1000;
    const stale = ageSec > 10;
    $("#updated-at").textContent =
      (stale ? "⚠ stale " : "갱신 ") +
      ts.toLocaleTimeString("ko-KR") +
      (stale ? ` (${Math.round(ageSec)}s)` : "");
    $("#updated-at").className = stale
      ? "text-xs text-amber-400 ml-auto"
      : "text-xs text-slate-500 ml-auto";
  }

  function applySnapshot(snap) {
    if (!snap) return;
    state.lastSnapshot = snap;
    $("#session-state").textContent = snap.paused ? "/off" : "/on";
    $("#session-state").className = snap.paused
      ? "text-xs px-2 py-0.5 rounded bg-rose-800 text-rose-200"
      : "text-xs px-2 py-0.5 rounded bg-emerald-800 text-emerald-200";
    $("#monitored-count").textContent = `${(snap.stocks || []).length}종목`;
    refreshStaleIndicator();

    // 단일 그리드 — source 우선순위 정렬 (보유 → 자동 → 부상 → 수동), 점수 내림차순
    const stocks = (snap.stocks || []).slice().sort((a, b) => {
      const sa = SOURCE_ORDER[a.source] ?? 9;
      const sb = SOURCE_ORDER[b.source] ?? 9;
      if (sa !== sb) return sa - sb;
      const ba = a.header?.score ?? -Infinity;
      const bb = b.header?.score ?? -Infinity;
      return bb - ba;
    });

    // 빠진 카드 정리
    const presentCodes = new Set(stocks.map((s) => s.code));
    for (const code of Array.from(state.cardEls.keys())) {
      if (!presentCodes.has(code)) {
        state.cardEls.get(code).remove();
        state.cardEls.delete(code);
      }
    }

    const grid = $("#cards");
    if (!grid) return;
    grid.innerHTML = "";
    stocks.forEach((s) => grid.appendChild(renderCard(s)));
  }

  // ── WebSocket ──────────────────────────────────────────────────────────────
  function connectWS() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${location.host}/ws/monitor`;
    const ws = new WebSocket(url);
    state.ws = ws;
    ws.onopen = () => {
      state.reconnectAttempt = 0;
      $("#conn-dot").className = "inline-block w-2 h-2 rounded-full bg-emerald-500";
      $("#conn-dot").title = "WebSocket 연결됨";
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "snapshot" || msg.type === "tick") {
          applySnapshot(msg.payload);
        }
      } catch (e) {
        console.error("ws message parse", e);
      }
    };
    ws.onclose = () => {
      $("#conn-dot").className = "inline-block w-2 h-2 rounded-full bg-slate-500";
      $("#conn-dot").title = "WebSocket 끊김 — 재연결 시도";
      // 지수 백오프 (1s/2s/4s/8s/16s/cap 30s)
      const delay = Math.min(30000, 1000 * 2 ** state.reconnectAttempt);
      state.reconnectAttempt++;
      setTimeout(connectWS, delay);
    };
    ws.onerror = () => ws.close();
  }

  // ── REST helpers ───────────────────────────────────────────────────────────
  async function post(path, body) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || r.statusText);
    return data;
  }

  function flash(msg) {
    // 우상단 토스트 (간단)
    const t = document.createElement("div");
    t.textContent = msg;
    t.className = "fixed top-2 right-2 z-30 px-3 py-1 rounded bg-slate-700 text-slate-100 text-xs shadow";
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2500);
  }

  // ── Event handlers ─────────────────────────────────────────────────────────
  document.body.addEventListener("click", async (ev) => {
    const t = ev.target.closest("[data-act]");
    if (!t) return;
    const act = t.dataset.act;
    const code = t.dataset.code;
    if (act === "buy") {
      // 보유 등록 모달 오픈
      state.pendingBuyCode = code;
      const card = state.cardEls.get(code);
      const name = card?.querySelector(".font-bold")?.textContent || code;
      $("#buy-modal-name").textContent = `${name} (${code})`;
      $("#buy-price").value = "";
      $("#buy-time-stop").value = "";
      $("#buy-modal").classList.remove("hidden");
    } else if (act === "sell") {
      if (!confirm(`${code} 청산 처리?`)) return;
      try {
        const r = await post("/api/holdings", { action: "sell", code });
        flash(r.message || `${code} 청산`);
      } catch (e) {
        flash(`오류: ${e.message}`);
      }
    } else if (act === "promote" || act === "unwatch") {
      // 둘 다 6자리 코드 토글 핸들러 — 자동/부상 → 수동 잠금, 수동 → 제거.
      // 핸들러가 source 별 분기를 알아서 처리 (add_manual 동작).
      try {
        const r = await post("/api/watchlist", { action: "toggle", code });
        flash(r.message);
      } catch (e) {
        flash(`오류: ${e.message}`);
      }
    }
  });

  $("#buy-cancel").addEventListener("click", () => $("#buy-modal").classList.add("hidden"));
  $("#buy-confirm").addEventListener("click", async () => {
    const code = state.pendingBuyCode;
    if (!code) return;
    const priceStr = $("#buy-price").value.trim().replace(/,/g, "");
    const timeStopStr = $("#buy-time-stop").value.trim();
    const body = { action: "buy", code };
    if (priceStr) body.price = Number(priceStr);
    if (timeStopStr) body.time_stop_minutes = Number(timeStopStr);
    try {
      const r = await post("/api/holdings", body);
      flash(r.message || `${code} 보유 등록`);
      $("#buy-modal").classList.add("hidden");
    } catch (e) {
      flash(`오류: ${e.message}`);
    }
  });

  $("#btn-on").addEventListener("click", async () => {
    try {
      const r = await post("/api/session", { action: "on" });
      flash(r.message);
    } catch (e) {
      flash(`오류: ${e.message}`);
    }
  });
  $("#btn-off").addEventListener("click", async () => {
    try {
      const r = await post("/api/session", { action: "off" });
      flash(r.message);
    } catch (e) {
      flash(`오류: ${e.message}`);
    }
  });

  $("#btn-add").addEventListener("click", async () => {
    const code = $("#add-code").value.trim();
    if (!/^\d{6}$/.test(code)) {
      flash("6자리 종목코드를 입력");
      return;
    }
    try {
      const r = await post("/api/watchlist", { action: "toggle", code });
      flash(r.message);
      $("#add-code").value = "";
    } catch (e) {
      flash(`오류: ${e.message}`);
    }
  });

  // ── Boot ───────────────────────────────────────────────────────────────────
  connectWS();
  // broadcast 안 들어와도 stale 표시는 1초마다 자체 갱신
  setInterval(refreshStaleIndicator, 1000);
})();
