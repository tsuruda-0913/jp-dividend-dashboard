# -*- coding: utf-8 -*-
"""監視対象の銘柄リスト。

銘柄は Google スプレッドシートから読み込みます。
シート側を編集すれば、アプリの監視銘柄が自動で更新されます。

シート URL:
  https://docs.google.com/spreadsheets/d/1rI-X6hIi_FhThMoYGYEDWVkvJHqVMCZQED8_lDweUgw/edit
（「リンクを知っている全員が閲覧可」になっている必要があります）
"""

import io
import re
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


# 米国株タブの名前（スプレッドシート内のワークシート名）
US_SHEET_NAME = "米国株"
US_SHEET_CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv"
    f"&sheet={urllib.parse.quote(US_SHEET_NAME)}"
)

# 米国株タブが未作成・読込失敗時に使う代表的な米国高配当株。
FALLBACK_US_STOCKS = [
    {"code": "KO", "name": "コカ・コーラ"},
    {"code": "PEP", "name": "ペプシコ"},
    {"code": "PG", "name": "P&G"},
    {"code": "JNJ", "name": "ジョンソン・エンド・ジョンソン"},
    {"code": "ABBV", "name": "アッヴィ"},
    {"code": "MRK", "name": "メルク"},
    {"code": "PFE", "name": "ファイザー"},
    {"code": "XOM", "name": "エクソンモービル"},
    {"code": "CVX", "name": "シェブロン"},
    {"code": "VZ", "name": "ベライゾン"},
    {"code": "T", "name": "AT&T"},
    {"code": "MO", "name": "アルトリア"},
    {"code": "PM", "name": "フィリップモリス"},
    {"code": "IBM", "name": "IBM"},
    {"code": "CSCO", "name": "シスコシステムズ"},
    {"code": "TXN", "name": "テキサス・インスツルメンツ"},
    {"code": "MCD", "name": "マクドナルド"},
    {"code": "HD", "name": "ホーム・デポ"},
    {"code": "MMM", "name": "スリーエム"},
    {"code": "CAT", "name": "キャタピラー"},
    {"code": "JPM", "name": "JPモルガン・チェース"},
    {"code": "USB", "name": "USバンコープ"},
    {"code": "O", "name": "リアルティ・インカム"},
    {"code": "DUK", "name": "デューク・エナジー"},
    {"code": "SO", "name": "サザン"},
    {"code": "ED", "name": "コンソリデーテッド・エジソン"},
    {"code": "KMB", "name": "キンバリークラーク"},
    {"code": "GILD", "name": "ギリアド・サイエンシズ"},
    {"code": "LMT", "name": "ロッキード・マーチン"},
    {"code": "UPS", "name": "UPS"},
]


def ticker_of(code: str) -> str:
    """コードを yfinance 用ティッカーに変換する。

    数字のみ（日本株の証券コード）→ 末尾に .T を付与（例: 8058.T）
    英字を含む（米国株ティッカー・483Aなど英数字混在の日本コード）:
      日本株の新形式コード（4桁英数字）は .T、純粋な英字ティッカーはそのまま。
    """
    code = code.strip().upper()
    if code.isdigit():
        return f"{code}.T"
    # 「483A」のような日本の英数字混在コード（数字始まり4桁）は東証
    if len(code) == 4 and code[0].isdigit():
        return f"{code}.T"
    return code


def is_us(code: str) -> bool:
    """コードが米国株ティッカーかどうか。"""
    return not ticker_of(code).endswith(".T")


def _to_float(val):
    try:
        f = float(str(val).replace(",", "").strip())
        return None if pd.isna(f) else f
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


def load_us_stocks() -> list[dict]:
    """スプレッドシートの「米国株」タブから銘柄リストを読み込む。

    必要な列: 「ティッカー」（または「コード」）と「銘柄名」。任意で「配当利回り」。
    タブが無い・読めない場合は FALLBACK_US_STOCKS を返す。
    返す各要素の例:
      {"code": "KO", "name": "コカ・コーラ", "yield": 3.1,
       "yahoo_url": "https://finance.yahoo.com/quote/KO",
       "info_url": "https://www.google.com/search?q=..."}
    """
    def enrich(s: dict) -> dict:
        code = s["code"]
        s.setdefault("yield", None)
        s["yahoo_url"] = f"https://finance.yahoo.com/quote/{code}"
        s["info_url"] = "https://www.google.com/search?q=" + urllib.parse.quote(
            f"{code} {s['name']} 株価"
        )
        return s

    try:
        req = urllib.request.Request(US_SHEET_CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
        df = pd.read_csv(io.StringIO(raw), dtype=str)
        df = df.rename(columns={c: c.strip() for c in df.columns})

        code_col = next((c for c in df.columns if "ティッカー" in c or "コード" in c), None)
        name_col = next((c for c in df.columns if "銘柄" in c or "名称" in c), None)
        yield_col = next((c for c in df.columns if "配当利回り" in c or "利回り" in c), None)
        if code_col is None:
            raise ValueError("ティッカー列が見つかりません")

        stocks = []
        for _, row in df.iterrows():
            code = _text(row[code_col]).upper()
            # 米国ティッカー形式（英字1〜5文字、.- 可）のみ受け付ける。
            # ※「米国株」タブが未作成だと gviz は既定シート（日本株）を返すため、
            #   日本の証券コードが混ざった場合はここで弾いてフォールバックさせる。
            if not code or not re.fullmatch(r"[A-Z][A-Z.\-]{0,5}", code):
                continue
            stocks.append(enrich({
                "code": code,
                "name": _text(row[name_col]) if name_col else code,
                "yield": _to_float(row[yield_col]) if yield_col else None,
            }))
        if not stocks:
            raise ValueError("米国株の銘柄が0件でした")
        return stocks
    except Exception:
        return [enrich(dict(s)) for s in FALLBACK_US_STOCKS]
