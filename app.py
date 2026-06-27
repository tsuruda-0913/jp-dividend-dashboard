# -*- coding: utf-8 -*-
"""日本の高配当株 モニタリング ダッシュボード.

機能:
  1. 株価一覧（現在値・前日比）
  2. 「前日終値からの下落率」を色分けした下落表
       5〜10% / 11〜15% / 16〜20% / 20%超 でセルを色分け
  3. 日本の株価・経済ニュースが流れるタイムライン

データの更新は「今すぐ更新」ボタンで手動で行います（取得結果は1時間キャッシュ）。

実行: streamlit run app.py
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import yfinance as yf

from stocks import load_stocks, ticker_of

JST = ZoneInfo("Asia/Tokyo")

# ----------------------------------------------------------------------------
# 下落率の色分け設定
# ----------------------------------------------------------------------------
# (下限, 上限, 背景色, ラベル) ※下限 < 下落率 <= 上限
DECLINE_BUCKETS = [
    (20.0, float("inf"), "#C62828", "20%超の下落"),   # 濃い赤
    (15.0, 20.0, "#EF6C00", "16〜20%の下落"),         # 濃いオレンジ
    (10.0, 15.0, "#F9A825", "11〜15%の下落"),         # オレンジ
    (5.0, 10.0, "#FFEE58", "5〜10%の下落"),           # 黄色
]


def bucket_for(decline: float):
    """下落率(%)に対応する (背景色, 文字色, ラベル) を返す。該当なしは None。"""
    if decline is None or pd.isna(decline):
        return None
    for low, high, color, label in DECLINE_BUCKETS:
        if low < decline <= high:
            # 濃い背景のときは文字を白に
            text = "#FFFFFF" if color in ("#C62828", "#EF6C00") else "#000000"
            return color, text, label
    return None


# ----------------------------------------------------------------------------
# データ取得（1時間キャッシュ）
# ----------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner="銘柄リストと株価を取得中…")
def fetch_prices() -> pd.DataFrame:
    """シートの銘柄リストを読み込み、株価・下落率を計算した DataFrame を返す。"""
    stocks = load_stocks()
    tickers = [ticker_of(s["code"]) for s in stocks]
    raw = yf.download(
        tickers,
        period="3mo",
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        progress=False,
        threads=True,
    )

    rows = []
    for s in stocks:
        tk = ticker_of(s["code"])
        try:
            df = raw[tk] if len(tickers) > 1 else raw
            close = df["Close"].dropna()
            if close.empty:
                raise ValueError("no data")
            current = float(close.iloc[-1])
            prev = float(close.iloc[-2]) if len(close) >= 2 else current
            day_chg = (current - prev) / prev * 100 if prev else None
        except Exception:
            current = day_chg = None

        rows.append(
            {
                "銘柄": s["name"],
                "コード": s["code"],
                "市場": s.get("market", ""),
                "財務": s.get("finance", ""),
                "現在値": current,            # yfinance のライブ値（更新ボタンで最新化）
                "前日比(%)": day_chg,         # yfinance のライブ値
                "ROE(自己資本利益率)(%)": s.get("roe"),
                "自己資本比率(%)": s.get("equity_ratio"),
                "流動比率(%)": s.get("current_ratio"),
                "当座比率(%)": s.get("quick_ratio"),
                "配当利回り(%)": s.get("yield"),
                "IRBANK": s.get("irbank_url", ""),
                "企業情報": s.get("info_url", ""),
            }
        )

    out = pd.DataFrame(rows)
    return out


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_news(limit: int = 30) -> list[dict]:
    """日本の経済・株価ニュースを RSS から取得して新しい順に返す。"""
    import feedparser

    feeds = [
        "https://news.yahoo.co.jp/rss/topics/business.xml",
        "https://news.yahoo.co.jp/rss/categories/business.xml",
        "https://assets.wor.jp/rss/rdf/nikkei/markets.rdf",
        "https://assets.wor.jp/rss/rdf/reuters/business.rdf",
    ]
    items = []
    for url in feeds:
        try:
            parsed = feedparser.parse(url)
            source = parsed.feed.get("title", "ニュース")
            for e in parsed.entries:
                published = None
                if getattr(e, "published_parsed", None):
                    published = dt.datetime(*e.published_parsed[:6], tzinfo=dt.timezone.utc).astimezone(JST)
                items.append(
                    {
                        "title": e.get("title", "(無題)"),
                        "link": e.get("link", ""),
                        "source": source,
                        "published": published,
                    }
                )
        except Exception:
            continue

    # 重複タイトル除去
    seen = set()
    uniq = []
    for it in items:
        if it["title"] in seen:
            continue
        seen.add(it["title"])
        uniq.append(it)

    uniq.sort(key=lambda x: x["published"] or dt.datetime.min.replace(tzinfo=JST), reverse=True)
    return uniq[:limit]


# ----------------------------------------------------------------------------
# 市場時間の判定
# ----------------------------------------------------------------------------
def market_is_open(now: dt.datetime | None = None) -> bool:
    """東証がザラ場中か（平日 9:00〜11:30 / 12:30〜15:00 JST）を判定。"""
    now = now or dt.datetime.now(JST)
    if now.weekday() >= 5:  # 土日
        return False
    t = now.time()
    morning = dt.time(9, 0) <= t <= dt.time(11, 30)
    afternoon = dt.time(12, 30) <= t <= dt.time(15, 0)
    return morning or afternoon


# ----------------------------------------------------------------------------
# 画面描画
# ----------------------------------------------------------------------------
def style_table(df: pd.DataFrame):
    """前日比セルを下落幅に応じて色分けした Styler を返す。"""

    def color_daychg(val):
        if val is None or pd.isna(val):
            return ""
        if val >= 0:
            return "color: #2E7D32;"  # 上昇は緑
        b = bucket_for(-val)  # 下落幅(%)で色分け
        if b is None:
            return "color: #C62828;"  # 5%未満の下落は赤文字のみ
        bg, text, _ = b
        return f"background-color: {bg}; color: {text}; font-weight: 600;"

    styler = (
        df.style
        .map(color_daychg, subset=["前日比(%)"])
        .format(
            {
                "現在値": "{:,.1f}",
                "前日比(%)": "{:+.2f}%",
                "ROE(自己資本利益率)(%)": "{:.1f}%",
                "自己資本比率(%)": "{:.2f}%",
                "流動比率(%)": "{:.2f}%",
                "当座比率(%)": "{:.2f}%",
                "配当利回り(%)": "{:.2f}%",
            },
            na_rep="—",
        )
    )
    return styler


def main():
    st.set_page_config(page_title="日本 高配当株 ダッシュボード", page_icon="📈", layout="wide")

    now = dt.datetime.now(JST)
    is_open = market_is_open(now)

    st.title("📈 日本 高配当株 モニタリング ダッシュボード")

    status = "🟢 ザラ場中" if is_open else "⚪ 市場時間外"
    c1, c2 = st.columns([3, 1])
    c1.caption(f"現在値・前日比は yfinance のライブ値（更新ボタンで最新化）　|　{status}")
    c2.caption(f"最終更新: {now:%Y-%m-%d %H:%M:%S} JST")
    if c2.button("🔄 今すぐ更新"):
        st.cache_data.clear()
        st.rerun()

    st.divider()

    # --- 株価・財務指標の表 ---
    try:
        df = fetch_prices()
        st.caption(
            f"📋 Google スプレッドシートの銘柄リスト・財務指標を反映（{len(df)} 銘柄）。"
            "現在値と前日比のみライブ取得。前日比が小さい（下落が大きい）順に表示。"
        )
        # 前日比が小さい（＝下落が大きい）順に並べる
        styler = style_table(df.sort_values("前日比(%)", ascending=True, na_position="last"))
        st.dataframe(
            styler,
            use_container_width=True,
            hide_index=True,
            height=740,
            column_config={
                "IRBANK": st.column_config.LinkColumn("IRBANK", display_text="決算情報"),
                "企業情報": st.column_config.LinkColumn("企業情報", display_text="Google検索"),
            },
        )
    except Exception as e:
        st.error(f"株価データの取得に失敗しました: {e}")

    st.divider()

    # --- ニュース・タイムライン ---
    st.subheader("📰 株価・経済ニュース タイムライン")
    try:
        news = fetch_news()
        if not news:
            st.info("ニュースを取得できませんでした（ネットワークまたはRSSの状態をご確認ください）。")
        for it in news:
            ts = it["published"].strftime("%m/%d %H:%M") if it["published"] else "--/-- --:--"
            st.markdown(
                f"<div style='border-left:3px solid #1976D2;padding:4px 12px;margin:6px 0;'>"
                f"<span style='color:#888;font-size:0.8em;'>🕒 {ts}　|　{it['source']}</span><br>"
                f"<a href='{it['link']}' target='_blank' style='text-decoration:none;font-weight:600;'>"
                f"{it['title']}</a></div>",
                unsafe_allow_html=True,
            )
    except Exception as e:
        st.warning(f"ニュースの取得に失敗しました: {e}")


if __name__ == "__main__":
    main()
