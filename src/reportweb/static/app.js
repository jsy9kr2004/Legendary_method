// 장중 라이브 갱신 — 현재 페이지 레포트의 파일 mtime 을 폴링해서,
// 새 버전(예: 14:50 결정 재생성)이 저장되면 "새로고침" pill 을 띄운다.
// 자동 reload 대신 사용자 탭으로 — 스크롤/읽던 위치 보호.
(function () {
  var el = document.currentScript;
  var date = el.getAttribute("data-date");
  var label = el.getAttribute("data-label");
  if (!date || !label) return;

  var baselineMtime = null;
  var pill = document.getElementById("reloadPill");
  var dot = document.getElementById("liveDot");

  if (pill) {
    pill.addEventListener("click", function () { location.reload(); });
  }

  function poll() {
    fetch("/api/d/" + date + "/status", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j) return;
        var m = j[label];
        if (m == null) return;
        if (baselineMtime === null) {
          baselineMtime = m;
        } else if (m > baselineMtime && pill) {
          pill.hidden = false;
          if (dot) dot.textContent = "● NEW";
        }
        // 장 시간대 벗어나면 폴링 종료 (서버 판단 신뢰).
        if (j.market_window === false) {
          clearInterval(timer);
          if (dot) dot.style.display = "none";
        }
      })
      .catch(function () { /* 네트워크 흔들림은 다음 tick 에서 재시도 */ });
  }

  poll();
  var timer = setInterval(poll, 30000); // 30초
})();
