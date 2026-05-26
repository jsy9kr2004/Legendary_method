"""레전드의시작(종배 고수) 픽 vs 시스템 Eod.Pick v2 후보 비교 분석.

입력: image/jb.png 에서 전사한 날짜별 종배 픽.
대조: data/daily/ohlcv.parquet (일봉 상승률·거래대금) + data/meta/stocks.parquet (이름·시총).

질문: 레전드의 픽이 우리 시스템 Eod.Pick v2 필터
      (거래대금 top50 universe + 5% ≤ 일봉상승률 ≤ 27% + 종가 고가-10% 이내)
      를 얼마나 통과하나? 어디서 어긋나나?
"""
from __future__ import annotations

import pandas as pd

# Eod.Pick v2 상수 (src/overnight/candidates.py 와 동일)
MIN_RET = 5.0
MAX_RET = 27.0
TOP_N = 50

# image/jb.png 전사 — 날짜(2026) → 레전드 픽 이름 리스트
LEGEND: dict[str, list[str]] = {
    "2026-04-07": ["심텍", "서울반도체", "삼성E&A"],
    "2026-04-08": ["삼성전자", "SK하이닉스"],
    "2026-04-14": ["OCI", "미래에셋증권"],
    "2026-04-17": ["STX엔진", "한화엔진"],
    "2026-04-21": ["한화엔진"],
    "2026-04-23": ["이수페타시스"],
    "2026-04-24": ["대원전선", "보성파워텍", "지투파워"],
    "2026-04-28": ["씨아이에스", "현대차", "로보티즈", "한화솔루션", "고영"],
    "2026-04-29": ["제일엠앤씨", "전력기기", "CJ", "LS", "SK"],
    "2026-04-30": ["한화솔루션", "SK이터닉스", "두산로보틱스", "나우로보틱스", "대원전선"],
    "2026-05-01": ["씨아이에스", "대원전선"],
    "2026-05-04": ["SK하이닉스", "산일전기", "삼성증권", "보성파워텍", "씨아이에스"],
    "2026-05-06": ["미래에셋증권", "키움증권", "삼성증권", "삼성전자"],
    "2026-05-07": ["삼성중공업", "현대차", "나우로보틱스", "두산로보틱스"],
    "2026-05-08": ["현대차"],
    "2026-05-11": ["로보티즈", "두산로보틱스", "삼성전자", "SK하이닉스"],
    "2026-05-12": ["삼성전자", "SK하이닉스", "한화", "현대무벡스"],
    "2026-05-13": ["삼성전자", "SK하이닉스", "현대차"],
    "2026-05-14": ["삼성전자", "현대차", "LG전자", "LG CNS"],
    "2026-05-15": ["두산로보틱스", "현대차"],
    "2026-05-16": ["삼성전자", "SK하이닉스", "두산로보틱스", "LG전자", "현대차", "LG CNS"],
    "2026-05-19": ["한화에어로스페이스"],
    "2026-05-20": ["삼성전자", "삼화콘덴서"],
    "2026-05-21": ["삼성전자", "SK하이닉스", "현대모비스", "LG전자"],
}


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    ohlcv = pd.read_parquet("data/daily/ohlcv.parquet")
    ohlcv["code"] = ohlcv["code"].astype(str).str.zfill(6)
    ohlcv["date"] = ohlcv["date"].astype(str)  # datetime.date → "YYYY-MM-DD"
    # change_rate 컬럼이 대부분 비어 있어(삼성전자 5/21=None) 종가로 직접 일봉상승률 계산
    ohlcv = ohlcv.sort_values(["code", "date"])
    ohlcv["ret"] = ohlcv.groupby("code")["close"].pct_change() * 100.0
    # 다음날 갭상 = (다음 거래일 시가 - 오늘 종가) / 오늘 종가 — 종배 핵심 수익 축
    ohlcv["next_open"] = ohlcv.groupby("code")["open"].shift(-1)
    ohlcv["gap"] = (ohlcv["next_open"] - ohlcv["close"]) / ohlcv["close"] * 100.0
    stocks = pd.read_parquet("data/meta/stocks.parquet")
    stocks["code"] = stocks["code"].astype(str).str.zfill(6)
    return ohlcv, stocks


def name_to_code(name: str, stocks: pd.DataFrame) -> str | None:
    exact = stocks[stocks["name"] == name]
    if len(exact) == 1:
        return exact.iloc[0]["code"]
    if len(exact) > 1:
        return exact.iloc[0]["code"]
    # 느슨한 매칭 (공백/대소문자 무시)
    norm = name.replace(" ", "").upper()
    cand = stocks[stocks["name"].str.replace(" ", "").str.upper() == norm]
    if len(cand) >= 1:
        return cand.iloc[0]["code"]
    return None


def main() -> None:
    ohlcv, stocks = load()
    name2code = {n: c for n, c in zip(stocks["name"], stocks["code"])}

    rows = []
    unmatched = []
    for date, names in LEGEND.items():
        day = ohlcv[ohlcv["date"] == date]
        if day.empty:
            print(f"[경고] {date} 일봉 데이터 없음 (skip)")
            continue
        # 그날 거래대금 순위
        day = day.sort_values("trading_value", ascending=False).reset_index(drop=True)
        day["tv_rank"] = day.index + 1
        rank_map = dict(zip(day["code"], day["tv_rank"]))
        ret_map = dict(zip(day["code"], day["ret"]))
        gap_map = dict(zip(day["code"], day["gap"]))

        for name in names:
            code = name_to_code(name, stocks)
            if code is None:
                unmatched.append((date, name))
                continue
            ret = ret_map.get(code)
            tv_rank = rank_map.get(code)
            mcap = stocks[stocks["code"] == code]["market_cap"]
            mcap = int(mcap.iloc[0]) if len(mcap) else 0
            if ret is None or pd.isna(ret):
                unmatched.append((date, name + "(일봉없음)"))
                continue
            ret = float(ret)
            gap = gap_map.get(code)
            gap = float(gap) if gap is not None and not pd.isna(gap) else None
            in_top = tv_rank is not None and tv_rank <= TOP_N
            ret_ok = MIN_RET <= ret <= MAX_RET
            passes = bool(in_top and ret_ok)
            rows.append({
                "date": date, "name": name, "code": code,
                "ret%": round(ret, 1),
                "tv_rank": tv_rank if tv_rank else 9999,
                "gap%": round(gap, 2) if gap is not None else None,
                "in_top50": in_top,
                "ret_5to27": ret_ok,
                "SYSTEM_통과": passes,
            })

    df = pd.DataFrame(rows)
    pd.set_option("display.unicode.east_asian_width", True)
    pd.set_option("display.max_rows", None)
    pd.set_option("display.width", 200)

    print("\n" + "=" * 90)
    print("레전드 픽 → 시스템 Eod.Pick v2 필터 통과 여부")
    print("=" * 90)
    print(df.to_string(index=False))

    n = len(df)
    n_pass = int(df["SYSTEM_통과"].sum())
    n_top = int(df["in_top50"].sum())
    n_ret = int(df["ret_5to27"].sum())
    print("\n" + "=" * 90)
    print("요약")
    print("=" * 90)
    print(f"매칭된 레전드 픽: {n}개 (코드/일봉 매칭 실패 {len(unmatched)}개 제외)")
    print(f"  - 거래대금 top50 안:        {n_top}/{n}  ({n_top/n*100:.0f}%)")
    print(f"  - 일봉 5~27% 구간:          {n_ret}/{n}  ({n_ret/n*100:.0f}%)")
    print(f"  - 둘 다 통과(SYSTEM 후보):  {n_pass}/{n}  ({n_pass/n*100:.0f}%)  ★")

    print(f"\n일봉 상승률 분포 (레전드 픽):")
    print(f"  ret < 0%  (하락 마감):   {int((df['ret%'] < 0).sum())}개")
    print(f"  0 ~ 5%:                  {int(((df['ret%'] >= 0) & (df['ret%'] < 5)).sum())}개")
    print(f"  5 ~ 27% (시스템 구간):   {int(((df['ret%'] >= 5) & (df['ret%'] <= 27)).sum())}개")
    print(f"  > 27%:                   {int((df['ret%'] > 27).sum())}개")
    print(f"  중앙값 ret: {df['ret%'].median():.1f}%   평균 ret: {df['ret%'].mean():.1f}%")
    print(f"  거래대금순위 중앙값: {int(df['tv_rank'].median())}위")

    # ── 갭상 검증: 레전드 스타일이 실제로 다음날 갭상을 만드는가? ──
    g = df.dropna(subset=["gap%"])
    def stat(sub: pd.DataFrame, label: str) -> None:
        if sub.empty:
            print(f"  {label}: 표본 0")
            return
        wr = (sub["gap%"] > 0).mean() * 100
        print(f"  {label:32s} n={len(sub):2d}  평균갭 {sub['gap%'].mean():+5.2f}%  중앙갭 {sub['gap%'].median():+5.2f}%  갭상확률 {wr:.0f}%")

    print("\n" + "=" * 90)
    print("★ 갭상 검증 — 레전드 픽이 실제로 다음날 갭상했는가 (시초매도 가정)")
    print("=" * 90)
    stat(g, "레전드 픽 전체")
    stat(g[g["SYSTEM_통과"]], "  └ 시스템도 통과한 픽")
    stat(g[~g["SYSTEM_통과"]], "  └ 시스템이 탈락시킨 픽")
    print("  ── ret 구간별 (5% 하한이 거르는 픽이 실제로 졌나?) ──")
    stat(g[g["ret%"] < 0], "  ret < 0% (하락마감, 컷)")
    stat(g[(g["ret%"] >= 0) & (g["ret%"] < 5)], "  ret 0~5% (5%하한이 컷)")
    stat(g[(g["ret%"] >= 5) & (g["ret%"] <= 27)], "  ret 5~27% (시스템 채택)")
    stat(g[g["ret%"] > 27], "  ret > 27% (27%상한이 컷)")

    if unmatched:
        print(f"\n매칭 실패 ({len(unmatched)}): " + ", ".join(f"{d} {n}" for d, n in unmatched))

    # 시스템이 같은 날 실제로 뭘 골랐을지 (top50 + 5~27%, 상위 5개)
    print("\n" + "=" * 90)
    print("대조: 같은 날 시스템 Eod.Pick v2 가 골랐을 후보 (top50 ∩ 5~27%, 거래대금 상위 5)")
    print("=" * 90)
    for date in LEGEND:
        day = ohlcv[ohlcv["date"] == date]
        if day.empty:
            continue
        day = day.sort_values("trading_value", ascending=False).head(TOP_N).copy()
        sysc = day[(day["ret"] >= MIN_RET) & (day["ret"] <= MAX_RET)]
        sysc = sysc.merge(stocks[["code", "name"]], on="code", how="left")
        picks = [f"{r['name']}(+{r['ret']:.0f}%)" for _, r in sysc.head(5).iterrows()]
        legend_names = ", ".join(LEGEND[date])
        print(f"{date} | 시스템: {', '.join(picks) if picks else '후보 0'}")
        print(f"          레전드: {legend_names}")


if __name__ == "__main__":
    main()
