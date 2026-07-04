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

import plotly.graph_objects as go
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


def kpi_card(col, label, value, sub, gradient, icon=""):
    """グラデーション背景のKPIカードを描画する。"""
    col.markdown(
        f"""<div style="background:linear-gradient(135deg,{gradient});border-radius:14px;
        padding:14px 16px;color:#fff;box-shadow:0 4px 10px rgba(0,0,0,.12);min-height:104px;">
        <div style="font-size:.78em;opacity:.9;letter-spacing:.05em;">{icon} {label}</div>
        <div style="font-size:1.7em;font-weight:800;line-height:1.4;">{value}</div>
        <div style="font-size:.75em;opacity:.85;">{sub}</div></div>""",
        unsafe_allow_html=True,
    )


def color_for_decline(chg: float) -> str:
    """前日比(%)から棒グラフ用の色を返す。"""
    if chg >= 0:
        return "#2E7D32"
    b = bucket_for(-chg)
    return b[0] if b else "#E57373"


def decline_top_chart(df: pd.DataFrame, n: int = 10):
    """本日の下落TOP n 横棒グラフ。"""
    d = df.dropna(subset=["前日比(%)"]).nsmallest(n, "前日比(%)")
    d = d[d["前日比(%)"] < 0]
    if d.empty:
        return None
    d = d.iloc[::-1]  # 大きい下落を上に
    fig = go.Figure(
        go.Bar(
            x=d["前日比(%)"], y=d["銘柄"], orientation="h",
            marker_color=[color_for_decline(v) for v in d["前日比(%)"]],
            text=[f"{v:+.2f}%" for v in d["前日比(%)"]],
            textposition="outside", cliponaxis=False,
            customdata=d["コード"],
            hovertemplate="%{y} (%{customdata})<br>前日比 %{x:+.2f}%<extra></extra>",
        )
    )
    fig.update_layout(
        height=360, margin=dict(l=10, r=50, t=10, b=10),
        xaxis_title="前日比(%)", yaxis=dict(tickfont=dict(size=11)),
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def yield_top_chart(df: pd.DataFrame, n: int = 10):
    """配当利回りTOP n 横棒グラフ。"""
    d = df.dropna(subset=["配当利回り(%)"]).nlargest(n, "配当利回り(%)")
    if d.empty:
        return None
    d = d.iloc[::-1]
    fig = go.Figure(
        go.Bar(
            x=d["配当利回り(%)"], y=d["銘柄"], orientation="h",
            marker=dict(color=d["配当利回り(%)"], colorscale=[[0, "#90CAF9"], [1, "#1565C0"]]),
            text=[f"{v:.2f}%" for v in d["配当利回り(%)"]],
            textposition="outside", cliponaxis=False,
            customdata=d["コード"],
            hovertemplate="%{y} (%{customdata})<br>利回り %{x:.2f}%<extra></extra>",
        )
    )
    fig.update_layout(
        height=360, margin=dict(l=10, r=50, t=10, b=10),
        xaxis_title="配当利回り(%)", yaxis=dict(tickfont=dict(size=11)),
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def main():
    st.set_page_config(page_title="日本 高配当株 ダッシュボード", page_icon="📈", layout="wide")

    now = dt.datetime.now(JST)
    is_open = market_is_open(now)

    # --- 全体の軽いスタイル調整 ---
    st.markdown(
        """<style>
        .block-container {padding-top: 1.2rem;}
        div[data-testid="stTabs"] button p {font-size: 1.0em; font-weight: 600;}
        </style>""",
        unsafe_allow_html=True,
    )

    # --- ヘッダーバナー ---
    status = "🟢 ザラ場中" if is_open else "⚪ 市場時間外"
    h1, h2 = st.columns([5, 1])
    h1.markdown(
        f"""<div style="background:linear-gradient(90deg,#0D2B52,#1565C0);border-radius:14px;
        padding:18px 24px;color:#fff;">
        <span style="font-size:1.55em;font-weight:800;">📈 日本 高配当株 ダッシュボード</span>
        <span style="margin-left:14px;font-size:.85em;opacity:.9;">{status}　|　最終更新 {now:%m/%d %H:%M} JST</span>
        </div>""",
        unsafe_allow_html=True,
    )
    with h2:
        if st.button("🔄 今すぐ更新", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        if st.button("🇺🇸 米国ETF分析", use_container_width=True):
            st.switch_page("pages/2_米国ETF分析.py")

    # --- データ取得 ---
    try:
        df = fetch_prices()
    except Exception as e:
        st.error(f"株価データの取得に失敗しました: {e}")
        return

    chg = df["前日比(%)"].dropna()
    up_n = int((chg > 0).sum())
    down_n = int((chg < 0).sum())
    avg_chg = chg.mean() if not chg.empty else None
    avg_yield = df["配当利回り(%)"].dropna().mean()
    if not chg.empty:
        worst = df.loc[df["前日比(%)"].idxmin()]
        worst_txt, worst_sub = f"{worst['前日比(%)']:+.2f}%", worst["銘柄"]
    else:
        worst_txt, worst_sub = "—", ""

    # --- KPIカード ---
    st.write("")
    k = st.columns(5)
    kpi_card(k[0], "監視銘柄", f"{len(df)}", "Googleシート連携", "#42A5F5,#1565C0", "📋")
    kpi_card(k[1], "上昇 / 下落", f"{up_n} / {down_n}", "前日比ベース", "#66BB6A,#2E7D32", "⚖️")
    kpi_card(k[2], "平均前日比", f"{avg_chg:+.2f}%" if avg_chg is not None else "—", "全銘柄平均", "#FFB74D,#EF6C00", "📊")
    kpi_card(k[3], "平均配当利回り", f"{avg_yield:.2f}%" if pd.notna(avg_yield) else "—", "シート値の平均", "#AB47BC,#6A1B9A", "💰")
    kpi_card(k[4], "本日の最大下落", worst_txt, worst_sub, "#EF5350,#B71C1C", "📉")

    st.write("")

    # --- グラフ 2枚 ---
    g1, g2 = st.columns(2)
    with g1:
        st.markdown("##### 📉 本日の下落 TOP10")
        fig = decline_top_chart(df)
        if fig:
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("本日下落している銘柄はありません。")
    with g2:
        st.markdown("##### 💰 配当利回り TOP10")
        fig = yield_top_chart(df)
        if fig:
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("配当利回りデータがありません。")

    # --- タブ: 銘柄一覧 / ニュース ---
    tab_list, tab_news = st.tabs(["📋 銘柄一覧", "📰 ニュース"])

    with tab_list:
        st.caption("👆 行をクリックすると、その銘柄の「個別銘柄分析」ページへ移動します。下落が大きい順に表示。")
        sorted_df = (
            df.sort_values("前日比(%)", ascending=True, na_position="last")
            .reset_index(drop=True)
        )
        event = st.dataframe(
            style_table(sorted_df),
            use_container_width=True,
            hide_index=True,
            height=600,
            on_select="rerun",
            selection_mode="single-row",
            key="stock_table",
            column_config={
                "IRBANK": st.column_config.LinkColumn("IRBANK", display_text="決算情報"),
                "企業情報": st.column_config.LinkColumn("企業情報", display_text="Google検索"),
            },
        )
        # 行が選択されたら、その銘柄を個別分析ページで開く
        selected = event.selection.rows if event and event.selection else []
        if selected:
            r = sorted_df.iloc[selected[0]]
            st.session_state["selected_label"] = f"{r['コード']}_{r['銘柄']}"
            st.switch_page("pages/1_個別銘柄分析.py")

    with tab_news:
        try:
            news = fetch_news()
            if not news:
                st.info("ニュースを取得できませんでした（ネットワークまたはRSSの状態をご確認ください）。")
            for it in news:
                ts = it["published"].strftime("%m/%d %H:%M") if it["published"] else "--/-- --:--"
                st.markdown(
                    f"<div style='border-left:3px solid #1976D2;background:rgba(25,118,210,.04);"
                    f"border-radius:0 8px 8px 0;padding:6px 12px;margin:8px 0;'>"
                    f"<span style='color:#888;font-size:0.8em;'>🕒 {ts}　|　{it['source']}</span><br>"
                    f"<a href='{it['link']}' target='_blank' style='text-decoration:none;font-weight:600;'>"
                    f"{it['title']}</a></div>",
                    unsafe_allow_html=True,
                )
        except Exception as e:
            st.warning(f"ニュースの取得に失敗しました: {e}")


if __name__ == "__main__":
    main()
