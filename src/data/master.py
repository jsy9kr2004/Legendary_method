"""KIS 종목 마스터 다운로드 / 파싱.

KIS 가 제공하는 KOSPI/KOSDAQ 마스터 zip 을 받아서 종배 매매 가능한 종목만
필터링한 DataFrame 을 반환한다.

mst 파일 구조 (KIS Open API GitHub `open-trading-api` 참조,
**문자 단위(char)** 슬라이스):
    | offset | length | content                                                  |
    | ------ | ------ | -------------------------------------------------------- |
    | 0      | 9      | 단축코드 (좌측 0 패딩, 마지막 6자가 KRX 종목코드)           |
    | 9      | 12     | 표준코드 (KR + ...)                                       |
    | 21     | 가변   | 한글종목명 (line 끝 - part2_len 까지)                     |
    | (len - part2_len) | 2 | 그룹코드 (ST/EF/EW/EN/...)                       |

    part2_len(char): KOSPI=228, KOSDAQ=222

그룹코드(파트2 첫 2 chars):
    KOSPI 는 보통 'ST' 2자, KOSDAQ 는 'S '/'F '/'D ' 등 1자 + 공백.
    그래서 첫 1 char 만 비교한다.

    S  주권 (보통주/우선주, 종배 룰 대상)  ← default 필터
    E  ETF / ETN / ELW
    F  펀드 / 수익증권
    D  DR (신주인수권증권)

part2 추가 필드 (KIS open-trading-api kis_kospi_code_mst.py 참조):
    char[105:113]  상장일자 (8자, YYYYMMDD)
    char[172:181]  시가총액 (9자, 단위는 raw — 일반적으로 억원)

인코딩: CP949. 한글 1자 = 1 char = 2 bytes 라 byte 슬라이스 X, 반드시
**decode 후 char 단위 슬라이스**.

ETF/펀드/리츠/스팩 필터 (M5.5):
    `is_tradable_for_jongbae(code, name, group_code)` 가 단일 진입점.
    그룹코드 'S' 외에 종목명 prefix(KODEX/TIGER/...) + 코드 패턴(`5XXXXX` 스팩)
    까지 차단한다.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date, datetime

import httpx
import pandas as pd
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

KOSPI_URL = "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"
KOSDAQ_URL = "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip"

_KOSPI_PART2_LEN = 228   # char 단위
_KOSDAQ_PART2_LEN = 222  # char 단위

_SHORTCODE_LEN = 9
_STDCODE_LEN = 12
_KORNAME_OFFSET = _SHORTCODE_LEN + _STDCODE_LEN  # 21

# KIS mst part2 내부 char 오프셋
_PART2_LISTED_AT_OFFSET = 105
_PART2_LISTED_AT_LEN = 8
_PART2_MARKET_CAP_OFFSET = 172
_PART2_MARKET_CAP_LEN = 9

_OUTPUT_COLS = ["code", "name", "market", "group_code", "market_cap", "listed_at"]

# ETF / ETN 브랜드 prefix (종목명 시작 문자열).
# 단타 대상이 아닌 패시브 상품 — 회전율 1위 자리 차지하면 노이즈.
# 영어 브랜드명만 등재 (한국어 운용사명은 보통주 이름과 충돌 — 예: "삼성전자").
# 추가 ETF 차단은 그룹코드 'E' 또는 종목명 'ETF'/'ETN' 키워드로 처리.
_ETF_NAME_PREFIXES: tuple[str, ...] = (
    "KODEX", "TIGER", "KBSTAR", "ARIRANG", "KINDEX", "HANARO", "RISE",
    "ACE", "SOL", "WOORI", "PLUS", "KOSEF", "ITF", "SMART", "FOCUS",
    "TIMEFOLIO", "TREX", "TRUSTON", "MASTER", "BNK", "MAESTRO", "KOACT",
    "FREEDOM", "KCGI", "DOORI",
)

# 차단할 그룹코드 첫글자.
#   S 주권 → 통과
#   E ETF/ETN/ELW
#   F 펀드/수익증권
#   D DR
_BLOCKED_GROUP_FIRST_CHARS: frozenset[str] = frozenset({"E", "F", "D"})


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
def _download_mst(url: str) -> bytes:
    resp = httpx.get(url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        name = z.namelist()[0]
        return z.read(name)


def _part2_len(market: str) -> int:
    return _KOSPI_PART2_LEN if market == "KOSPI" else _KOSDAQ_PART2_LEN


def is_preferred_stock(code: str) -> bool:
    """KRX 종목코드 휴리스틱: 끝자리가 0 이 아니면 우선주(또는 변형 주권).

    - 0: 보통주 (default 종배 대상)
    - 5: 1우선주 / 7: 2우선주 / 9: 3우선주 등
    - 그 외(1,2,3,4,6,8): 분할 / 리츠 / 변형 — 매우 드뭄
    """
    if len(code) != 6:
        return False
    return code[-1] != "0"


def is_etf_like_name(name: str) -> bool:
    """종목명이 ETF/ETN/펀드 운용사 prefix 로 시작하는지.

    KODEX/TIGER 같이 명확한 패시브 상품 식별. 단타 대상이 아니므로 제외.
    종목명에 공백/특수문자 섞일 수 있어 startswith 만 체크 (대소문자 무시 X — KIS 마스터는 대문자 표준).
    """
    if not name:
        return False
    upper = name.strip()
    return any(upper.startswith(p) for p in _ETF_NAME_PREFIXES)


def is_spac_code(code: str) -> bool:
    """6자리 종목코드가 스팩(SPAC) 패턴인지.

    한국 SPAC 은 종목명에 '스팩' 포함하고 코드는 보통 1XXXXX 또는 4XXXXX/5XXXXX
    범위에 분포. 코드만으로는 정밀 구분 어려워 `is_tradable_for_jongbae` 에서
    종목명 '스팩' 키워드와 함께 차단.
    """
    return False  # placeholder — 종목명 차단으로 충분


def is_tradable_for_jongbae(
    code: str,
    name: str,
    group_code: str,
) -> bool:
    """종배/단타 후보로 거래 가능한 종목인지 단일 진입점.

    제외:
        - 그룹코드 첫글자가 E (ETF/ETN/ELW), F (펀드), D (DR) 인 경우
        - 종목명이 ETF 운용사 prefix 로 시작 (KODEX/TIGER/KBSTAR/...)
        - 종목명에 '스팩' 포함 (SPAC)
        - 종목명에 'ETF'/'ETN' 포함
        - 종목명에 '리츠' 포함 (Real Estate Investment Trust)
        - 6자리 코드 아님

    Args:
        code: 6자리 KRX 종목코드.
        name: 한글 종목명.
        group_code: KIS mst 그룹코드 (앞 2자).

    Returns:
        True 면 종배 후보 자격, False 면 제외.
    """
    if len(code) != 6 or not code.isdigit():
        return False

    g = (group_code or "").strip().upper()
    if g and g[0] in _BLOCKED_GROUP_FIRST_CHARS:
        return False

    if is_etf_like_name(name):
        return False

    n = (name or "").strip()
    if "스팩" in n:
        return False
    if "ETF" in n.upper() or "ETN" in n.upper():
        return False
    if "리츠" in n:
        return False

    return True


def _parse_int_safe(s: str) -> int:
    """공백/None/빈문자열을 0 으로 안전 파싱."""
    s = (s or "").strip()
    if not s:
        return 0
    try:
        return int(s)
    except ValueError:
        return 0


def _parse_listed_at(s: str) -> date | None:
    """YYYYMMDD 형식 → date. 비정상 값은 None."""
    s = (s or "").strip()
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None


def _parse_mst(
    content: bytes,
    market: str,
    group_prefix: str | None = "S",
    include_preferred: bool = True,
    jongbae_only: bool = False,
) -> pd.DataFrame:
    """KIS mst → DataFrame.

    `content` 는 raw bytes. cp949 로 decode 후 char 단위로 슬라이스.
    `group_prefix`: 그룹코드 첫 글자 매칭 (default 'S'=주권). None 이면 전체.
    `include_preferred`: False 면 우선주(끝자리 != 0) 제외.
    `jongbae_only`: True 면 ETF/ETN/리츠/스팩/펀드까지 강력 차단
        (`is_tradable_for_jongbae` 적용). M5.5 신설.

    추출 컬럼: code, name, market, group_code, market_cap, listed_at.
        market_cap: KIS mst 의 raw 정수값 (단위는 KIS 명세 — 보통 억원).
                    파싱 실패 시 0.
        listed_at:  YYYYMMDD → date. 파싱 실패 시 None.
    """
    text = content.decode("cp949", errors="replace")
    part2_len = _part2_len(market)
    min_len = _KORNAME_OFFSET + part2_len + 1

    rows: list[dict] = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if len(line) < min_len:
            continue

        short_code = line[0:_SHORTCODE_LEN].strip()
        kor_name_end = len(line) - part2_len
        kor_name = line[_KORNAME_OFFSET:kor_name_end].strip()
        part2 = line[-part2_len:]
        group_code = part2[0:2].strip()

        krx_code = short_code[-6:] if len(short_code) >= 6 else short_code
        if len(krx_code) != 6 or not krx_code.isalnum():
            continue
        if group_prefix is not None:
            if not group_code or not group_code.startswith(group_prefix):
                continue
        if not include_preferred and is_preferred_stock(krx_code):
            continue
        if jongbae_only and not is_tradable_for_jongbae(krx_code, kor_name, group_code):
            continue

        # part2 추가 필드 — 길이가 짧아 슬라이스 실패 가능성 있음 (mst 포맷 변경 시)
        listed_raw = part2[
            _PART2_LISTED_AT_OFFSET : _PART2_LISTED_AT_OFFSET + _PART2_LISTED_AT_LEN
        ] if len(part2) >= _PART2_LISTED_AT_OFFSET + _PART2_LISTED_AT_LEN else ""
        market_cap_raw = part2[
            _PART2_MARKET_CAP_OFFSET : _PART2_MARKET_CAP_OFFSET + _PART2_MARKET_CAP_LEN
        ] if len(part2) >= _PART2_MARKET_CAP_OFFSET + _PART2_MARKET_CAP_LEN else ""

        rows.append(
            {
                "code": krx_code,
                "name": kor_name,
                "market": market,
                "group_code": group_code,
                "market_cap": _parse_int_safe(market_cap_raw),
                "listed_at": _parse_listed_at(listed_raw),
            }
        )
    return pd.DataFrame(rows, columns=_OUTPUT_COLS)


def fetch_stock_master(
    group_prefix: str | None = "S",
    include_preferred: bool = True,
    jongbae_only: bool = False,
) -> pd.DataFrame:
    """KOSPI + KOSDAQ 합본.

    Args:
        group_prefix: 그룹코드 첫글자 ('S'=주권 default, None=전체).
        include_preferred: 우선주 포함 여부 (default True).
        jongbae_only: True 면 종배 후보 자격(`is_tradable_for_jongbae`) 통과만.
                      ETF 운용사 prefix / 스팩 / 리츠 모두 제외.

    매일 16:30 갱신 권장.
    """
    logger.info("KIS 종목 마스터 다운로드 (KOSPI)")
    kospi = _parse_mst(
        _download_mst(KOSPI_URL), "KOSPI", group_prefix, include_preferred, jongbae_only
    )
    logger.info(
        f"KOSPI {len(kospi)} 종목 (group_prefix={group_prefix or 'ALL'}, "
        f"preferred={'IN' if include_preferred else 'OUT'}, "
        f"jongbae_only={jongbae_only})"
    )

    logger.info("KIS 종목 마스터 다운로드 (KOSDAQ)")
    kosdaq = _parse_mst(
        _download_mst(KOSDAQ_URL), "KOSDAQ", group_prefix, include_preferred, jongbae_only
    )
    logger.info(
        f"KOSDAQ {len(kosdaq)} 종목 (group_prefix={group_prefix or 'ALL'}, "
        f"preferred={'IN' if include_preferred else 'OUT'}, "
        f"jongbae_only={jongbae_only})"
    )

    return pd.concat([kospi, kosdaq], ignore_index=True)


def backfill_market_cap_from_snapshots(
    master_df: pd.DataFrame,
    data_dir,
    min_turnover: float = 0.0,
) -> pd.DataFrame:
    """누적 스냅샷에서 종목별 시총(억)/상장주식수 역산 → master 의 market_cap 보강.

    KIS mst 의 시총 컬럼(char[172:181]) 이 모두 0 으로 파싱되는 알려진 결함
    (update_master TODO) 의 정식 backfill. 거래대금 순위 스냅샷이 함께 주는
    거래대금 + 거래대금회전율로 역산한다:
        시총(원)   = 거래대금 / (회전율/100)
        상장주식수 = 시총 / 현재가
    `infer_market_cap_eok` 과 동일 정의라 레포트에 표시되는 회전율과 자기일관.

    소스 선택 근거: pykrx/KRX public 이 이 데이터셋 날짜(2026)에 빈 응답을 주고,
    프로젝트 시세가 전부 KIS 기반이라 외부 시총을 끌어오면 가격과 불일치 →
    KIS 스냅샷 자체 역산이 가장 일관된 소스. 상장주식수는 거의 정적이라 저장해두면
    과거 일별 시총 = 과거 종가 × 상장주식수 로 historical 회전율 layer 도 가능.

    종목별로 가장 최근 스냅샷(파일명 YYYY-MM-DD/HH_MM, 사전순=시간순) 의 turnover>0
    행을 채택. 거래대금 top50 에 한 번도 안 뜬 종목(종배 무관 저유동)은 0 유지.

    Args:
        master_df: code 컬럼 보유 DataFrame. market_cap/shares 없으면 생성.
        data_dir: 데이터 루트 (스냅샷은 {data_dir}/intraday/snapshots/...).
        min_turnover: 이 값 초과 회전율만 사용 (기본 0 = 양수 전부).

    Returns:
        market_cap(억, int) + shares(int) 가 보강된 master_df 복사본.
    """
    from pathlib import Path

    from src.data.intraday import infer_market_cap_eok

    snap_dir = Path(data_dir) / "intraday" / "snapshots"
    snap_files = sorted(snap_dir.glob("*/*.parquet"))  # 사전순 = 시간순 (오래된→최신)

    latest: dict[str, tuple[int, int]] = {}  # code → (market_cap_eok, shares)
    for f in snap_files:
        try:
            df = pd.read_parquet(f, columns=["code", "price", "trading_value", "turnover"])
        except Exception as e:  # noqa: BLE001 — 손상/스키마 불일치 스냅샷은 skip
            logger.warning(f"[master backfill] 스냅샷 {f} skip: {e}")
            continue
        if df.empty:
            continue
        df["code"] = df["code"].astype(str).str.zfill(6)
        for code, price, tv, to in zip(
            df["code"], df["price"], df["trading_value"], df["turnover"]
        ):
            to_f = float(to) if to == to else 0.0
            tv_i = int(tv) if tv == tv else 0
            px_i = int(price) if price == price else 0
            if to_f <= min_turnover or tv_i <= 0 or px_i <= 0:
                continue
            mc_eok = infer_market_cap_eok(tv_i, to_f)
            if mc_eok <= 0:
                continue
            # 나중 파일(더 최신)이 앞 값을 덮어씀 → 종목별 최신 추정 채택
            latest[code] = (mc_eok, int(round(mc_eok * 1e8 / px_i)))

    out = master_df.copy()
    codes = out["code"].astype(str).str.zfill(6)
    existing_mc = (
        out["market_cap"].fillna(0).astype(int)
        if "market_cap" in out.columns
        else pd.Series(0, index=out.index)
    )
    existing_sh = (
        out["shares"].fillna(0).astype(int)
        if "shares" in out.columns
        else pd.Series(0, index=out.index)
    )
    # 기존 비-0 값은 보존(향후 mst 파싱 복구 대비), 스냅샷 추정 있으면 그 값으로 채움
    out["market_cap"] = [
        latest[c][0] if c in latest else em for c, em in zip(codes, existing_mc)
    ]
    out["shares"] = [
        latest[c][1] if c in latest else es for c, es in zip(codes, existing_sh)
    ]
    filled = sum(1 for c in codes if c in latest)
    logger.info(
        f"[master backfill] 스냅샷 {len(snap_files)}개에서 시총/상장주식수 역산 — "
        f"{filled}/{len(out)} 종목 보강 (나머지는 거래대금 top50 미등장, 0 유지)"
    )
    return out
