"""연구/튜닝 인프라 (P0 검증 루프).

매일 누적 데이터로 *자동 평가* 하고, 채택은 walk-forward OOS 게이트가 결정한다.
daily 재fit (과적합 treadmill) X — 후보 config 들을 walk-forward 로 *선택* 만.

구성:
- strategy_config: 버전 가능한 파라미터 set (매매법별 진입/청산).
- backtest: clean day 로딩 + config 기반 채점 + 진입/청산 시뮬 + 메트릭.
- walkforward: train/test 폴드 + OOS 게이트 + 파라미터 레지스트리.

근거 문서: docs/trading-method-separation-discussion.md §11 (P0).
"""
