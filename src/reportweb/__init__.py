"""종배 레포트 웹사이트 (read-only).

장 마감 결정/사후 레포트를 폰/데스크탑에서 읽기 좋게 HTML 로 렌더한다.
저장된 산출물(`data/reports/`, `data/decisions/`)만 읽으며 KIS 키·라이브 시세·
상태 변경 핸들러를 일절 갖지 않는다 (트러스트 경계 — 종배 동료 공유용).

- 단타 모니터링 PWA(`src/dashboard/`)와 별도 앱/포트. PWA 는 Tailscale 내부 전용,
  본 사이트는 Basic auth + Tailscale Funnel 로 공개 공유 (docs/dashboard-pwa.md §2.4).
- 자동 매매 정책 무관 — 발송/표시 전용 (CLAUDE.md).
"""
