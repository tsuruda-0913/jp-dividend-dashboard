# -*- coding: utf-8 -*-
"""監視対象の銘柄リスト。

銘柄は Google スプレッドシートから読み込みます。
シート側を編集すれば、アプリの監視銘柄が自動で更新されます。

シート URL:
  https://docs.google.com/spreadsheets/d/1rI-X6hIi_FhThMoYGYEDWVkvJHqVMCZQED8_lDweUgw/edit
（「リンクを知っている全員が閲覧可」になっている必要があります）
"""

import io
import urllib.parse
import urllib.request

import pandas as pd

# 読み込むスプレッドシートのID。別のシートに変えたいときはここを差し替えます。
SHEET_ID = "1rI-X6hIi_FhThMoYGYEDWVkvJHqVMCZQED8_lDweUgw"
SHEET_CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv"

# シートが読み込めなかったときに使う最低限のフォールバック銘柄。
FALLBACK_STOCKS = [
    {"code": "8058", "name": "三菱商事"},
    {"code": "2914", "name": "JT（日本たばこ産業）"},
    {"code": "9432", "name": "NTT（日本電信電話）"},
]


def ticker_of(code: str) -> str:
    """証券コードを yfinance 用のティッカー（例: 8058.T）に変換する。"""
    return f"{code}.T"


def _to_float(val):
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _text(val):
    s = str(val).strip()
    return "" if s.lower() == "nan" else s


def load_stocks() -> list[dict]:
    """Google スプレッドシートから銘柄リストと財務指標を読み込む。

    返す各要素の例:
      {"code": "1450", "name": "ＴＡＮＡＫＥＮ", "market": "東S", "finance": "(単)",
       "roe": 17.1, "equity_ratio": 76.57, "current_ratio": 394.27,
       "quick_ratio": 393.19, "yield": 4.1,
       "irbank_url": "https://irbank.net/1450",
       "info_url": "https://www.google.com/search?q=..."}
    ※ 現在値・前日比はシートの値を使わず、アプリ側で yfinance から取得する。
    失敗時は FALLBACK_STOCKS を返す。
    """
    try:
        req = urllib.request.Request(SHEET_CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
        df = pd.read_csv(io.StringIO(raw), dtype=str)
        df = df.rename(columns={c: c.strip() for c in df.columns})

        def find(*keywords):
            for c in df.columns:
                if all(k in c for k in keywords):
                    return c
            return None

        code_col = find("コード")
        name_col = find("銘柄")
        market_col = find("市場")
        finance_col = find("財務")
        roe_col = find("ROE")
        equity_col = find("自己資本比率")
        current_col = find("流動比率")
        quick_col = find("当座比率")
        yield_col = find("配当利回り")
        if code_col is None or name_col is None:
            raise ValueError("コード/銘柄名の列が見つかりません")

        stocks = []
        for _, row in df.iterrows():
            code = _text(row[code_col])
            if not code:
                continue
            name = _text(row[name_col])
            stocks.append(
                {
                    "code": code,
                    "name": name,
                    "market": _text(row[market_col]) if market_col else "",
                    "finance": _text(row[finance_col]) if finance_col else "",
                    "roe": _to_float(row[roe_col]) if roe_col else None,
                    "equity_ratio": _to_float(row[equity_col]) if equity_col else None,
                    "current_ratio": _to_float(row[current_col]) if current_col else None,
                    "quick_ratio": _to_float(row[quick_col]) if quick_col else None,
                    "yield": _to_float(row[yield_col]) if yield_col else None,
                    "irbank_url": f"https://irbank.net/{code}",
                    "info_url": "https://www.google.com/search?q="
                    + urllib.parse.quote(f"{code} {name} 株価"),
                }
            )
        if not stocks:
            raise ValueError("銘柄が0件でした")
        return stocks
    except Exception:
        return FALLBACK_STOCKS
