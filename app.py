# -*- coding: utf-8 -*-
"""日本 高配当株 ダッシュボード（トップ画面）.

「今日、どの銘柄を見に行くべきか」を最短で判断するための画面。
  - KPI 3枚（平均利回り / 利回りが過去レンジ上位の銘柄数 / 本日の最大下落）
  - 銘柄一覧テーブル
      前日比（下落の深刻度で色分け）・配当利回り・
      利回り位置（現在の利回りが過去3年レンジのどの位置か 0〜100%）・
      52週高値比・配当増減（TTM）
  - 下落TOP10 / 配当利回りTOP10
  - ニュースタイムライン

株価と配当履歴は yf.download(actions=True) で全銘柄まとめて一括取得する。
データの更新は「今すぐ更新」ボタンで手動（取得結果は1時間キャッシュ）。

実行: streamlit run app.py
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

import ui
from stocks import load_stocks, ticker_of

JST = ZoneInfo("Asia/Tokyo")


# ----------------------------------------------------------------------------
# データ取得（1時間キャッシュ・全銘柄1バッチ）
# ----------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner="銘柄リストと株価・配当履歴を取得中…")
def fetch_market_data() -> tuple[pd.DataFrame, bool]:
    """シートの銘柄リストを読み込み、株価・利回りレンジ位置等を計算して返す。

    戻り値: (一覧DataFrame, シート読込がフォールバックしたか)
    """
    stocks, fallback = load_stocks()
    tickers = [ticker_of(s["code"]) for s in stocks]
    raw = yf.download(
        tickers,
        period="3y",
        interval="1d",
        group_by="ticker",
        actions=True,          # 配当履歴も同じ1回のリクエストで取得する
        auto_adjust=False,
        progress=False,
        threads=True,
    )

    rows = []
    for s in stocks:
        tk = ticker_of(s["code"])
        current = day_chg = from_high = yld_pos = div_chg = calc_yield = None
        try:
            df = raw[tk] if len(tickers) > 1 else raw
            close = df["Close"].dropna()
            if close.empty:
                raise ValueError("no data")
            current = float(close.iloc[-1])
            prev = float(close.iloc[-2]) if len(close) >= 2 else current
            day_chg = (current - prev) / prev * 100 if prev else None

            end = close.index[-1]

            # 52週高値からの下落率
            last1y = close[close.index > end - pd.Timedelta(days=365)]
            if not last1y.empty and last1y.max():
                from_high = (current / float(last1y.max()) - 1) * 100

            # 配当（TTM）と利回りレンジ位置
            if "Dividends" in df.columns:
                div = df["Dividends"].reindex(close.index).fillna(0.0)
                ttm = div.rolling("365D").sum()
                yld = (ttm / close * 100).dropna()
                # 最初の1年はTTMが揃わないため除外
                yld = yld[yld.index >= close.index[0] + pd.Timedelta(days=365)]
                if len(yld) >= 200 and yld.iloc[-1] > 0:
                    calc_yield = float(yld.iloc[-1])
                    yld_pos = float((yld <= yld.iloc[-1]).mean() * 100)

                last12 = float(div[div.index > end - pd.Timedelta(days=365)].sum())
                prev12 = float(
                    div[(div.index <= end - pd.Timedelta(days=365))
                        & (div.index > end - pd.Timedelta(days=730))].sum()
                )
                if prev12 > 0 and last12 > 0:
                    div_chg = (last12 / prev12 - 1) * 100
        except Exception:
            pass

        # 表示する利回りはシート優先、無ければ株価と配当履歴から算出したTTM値
        disp_yield = s.get("yield") if s.get("yield") is not None else calc_yield

        rows.append(
            {
                "銘柄": s["name"],
                "コード": s["code"],
                "市場": s.get("market", ""),
                "セクター": s.get("sector", ""),
                "財務": s.get("finance", ""),
                "現在値": current,
                "前日比(%)": day_chg,
                "配当利回り(%)": disp_yield,
                "利回り位置": yld_pos,        # 過去3年レンジ内の位置(0-100, 高いほど割安寄り)
                "52週高値比(%)": from_high,
                "配当増減(%)": div_chg,       # 直近12か月 vs その前12か月（参考値）
                "ROE(%)": s.get("roe"),
                "自己資本比率(%)": s.get("equity_ratio"),
                "流動比率(%)": s.get("current_ratio"),
                "当座比率(%)": s.get("quick_ratio"),
                "IRBANK": s.get("irbank_url", ""),
                "企業情報": s.get("info_url", ""),
            }
        )

    return pd.DataFrame(rows), fallback


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_news(limit: int = 15) -> list[dict]:
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
    if now.weekday() >= 5:
        return False
    t = now.time()
    morning = dt.time(9, 0) <= t <= dt.time(11, 30)
    afternoon = dt.time(12, 30) <= t <= dt.time(15, 0)
    return morning or afternoon


# ----------------------------------------------------------------------------
# 一覧テーブル
# ----------------------------------------------------------------------------
SIMPLE_COLS = [
    "銘柄", "コード", "現在値", "前日比(%)", "配当利回り(%)",
    "利回り位置", "52週高値比(%)", "配当増減(%)", "IRBANK", "企業情報",
]
DETAIL_COLS = [
    "銘柄", "コード", "市場", "セクター", "財務", "現在値", "前日比(%)",
    "配当利回り(%)", "利回り位置", "52週高値比(%)", "配当増減(%)",
    "ROE(%)", "自己資本比率(%)", "流動比率(%)", "当座比率(%)", "IRBANK", "企業情報",
]

NUM_FMT = {
    "現在値": "{:,.1f}",
    "前日比(%)": "{:+.2f}%",
    "配当利回り(%)": "{:.2f}%",
    "52週高値比(%)": "{:+.1f}%",
    "配当増減(%)": "{:+.1f}%",
    "ROE(%)": "{:.1f}%",
    "自己資本比率(%)": "{:.2f}%",
    "流動比率(%)": "{:.2f}%",
    "当座比率(%)": "{:.2f}%",
}


def style_table(df: pd.DataFrame):
    """前日比を下落深刻度で、配当増減のマイナスを赤で色分けした Styler。"""

    def color_divchg(val):
        if val is None or pd.isna(val):
            return ""
        return f"color: {ui.DOWN}; font-weight: 600;" if val < 0 else ""

    fmt = {k: v for k, v in NUM_FMT.items() if k in df.columns}
    styler = df.style.map(ui.daychg_style, subset=["前日比(%)"]).format(fmt, na_rep="—")
    if "配当増減(%)" in df.columns:
        styler = styler.map(color_divchg, subset=["配当増減(%)"])
    return styler


def render_table(df: pd.DataFrame):
    detail = st.toggle("財務指標も表示（市場・ROE・自己資本比率など）", value=False)
    cols = DETAIL_COLS if detail else SIMPLE_COLS
    cols = [c for c in cols if c in df.columns]
    # セクター列はシートに値があるときだけ出す
    if "セクター" in cols and not df["セクター"].astype(str).str.strip().any():
        cols.remove("セクター")

    sorted_df = (
        df.sort_values("前日比(%)", ascending=True, na_position="last")
        .reset_index(drop=True)
    )
    st.caption(
        "👆 行をクリックすると個別銘柄の分析ページへ移動します（下落が大きい順に表示）。"
        "「利回り位置」= 現在の配当利回りが過去3年レンジのどこにあるか。"
        "100%に近いほど過去との比較では高利回り＝割安寄り。ただし減配リスク（配当増減がマイナス）に注意。"
    )
    event = st.dataframe(
        style_table(sorted_df[cols]),
        use_container_width=True,
        hide_index=True,
        height=600,
        on_select="rerun",
        selection_mode="single-row",
        key="stock_table_jp",
        column_config={
            "利回り位置": st.column_config.ProgressColumn(
                "利回り位置(3年)", min_value=0, max_value=100, format="%.0f%%",
                help="現在の配当利回りが過去3年の利回りレンジのどの位置にあるか。100%に近いほど過去比で高利回り（＝株価は割安寄り）",
            ),
            "52週高値比(%)": st.column_config.NumberColumn(
                help="52週高値からの下落率。買い場探しの参考",
            ),
            "配当増減(%)": st.column_config.NumberColumn(
                "配当増減(TTM)",
                help="直近12か月の配当合計をその前の12か月と比較（yfinance由来の参考値）。マイナスは減配の可能性",
            ),
            "IRBANK": st.column_config.LinkColumn("IRBANK", display_text="決算情報"),
            "企業情報": st.column_config.LinkColumn("企業情報", display_text="Google検索"),
        },
    )
    selected = event.selection.rows if event and event.selection else []
    if selected:
        r = sorted_df.iloc[selected[0]]
        st.session_state["selected_label"] = f"{r['コード']}_{r['銘柄']}"
        st.switch_page("pages/1_個別銘柄分析.py")


# ----------------------------------------------------------------------------
# TOP10 チャート
# ----------------------------------------------------------------------------
def color_for_decline(chg: float) -> str:
    if chg >= 0:
        return ui.UP
    b = ui.bucket_for(-chg)
    return b[0] if b else "#E57373"


def decline_top_chart(df: pd.DataFrame, n: int = 10):
    d = df.dropna(subset=["前日比(%)"]).nsmallest(n, "前日比(%)")
    d = d[d["前日比(%)"] < 0]
    if d.empty:
        return None
    d = d.iloc[::-1]
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
    ui.apply_layout(fig, height=360, xaxis_title="前日比(%)")
    fig.update_yaxes(tickfont=dict(size=11))
    return fig


def yield_top_chart(df: pd.DataFrame, n: int = 10):
    d = df.dropna(subset=["配当利回り(%)"]).nlargest(n, "配当利回り(%)")
    if d.empty:
        return None
    d = d.iloc[::-1]
    fig = go.Figure(
        go.Bar(
            x=d["配当利回り(%)"], y=d["銘柄"], orientation="h",
            marker=dict(color=d["配当利回り(%)"],
                        colorscale=[[0, ui.PRIMARY_LIGHT], [1, ui.PRIMARY]]),
            text=[f"{v:.2f}%" for v in d["配当利回り(%)"]],
            textposition="outside", cliponaxis=False,
            customdata=d["コード"],
            hovertemplate="%{y} (%{customdata})<br>利回り %{x:.2f}%<extra></extra>",
        )
    )
    ui.apply_layout(fig, height=360, xaxis_title="配当利回り(%)")
    fig.update_yaxes(tickfont=dict(size=11))
    return fig


# ----------------------------------------------------------------------------
# ニュース
# ----------------------------------------------------------------------------
def render_news():
    try:
        news = fetch_news()
        if not news:
            st.info("ニュースを取得できませんでした（ネットワークまたはRSSの状態をご確認ください）。")
        for it in news:
            ts = it["published"].strftime("%m/%d %H:%M") if it["published"] else "--/-- --:--"
            st.markdown(
                f"<div style='border-left:3px solid {ui.PRIMARY};background:rgba(26,92,176,.05);"
                f"border-radius:0 8px 8px 0;padding:6px 12px;margin:8px 0;'>"
                f"<span style='opacity:.6;font-size:0.8em;'>🕒 {ts}　|　{it['source']}</span><br>"
                f"<a href='{it['link']}' target='_blank' style='text-decoration:none;font-weight:600;'>"
                f"{it['title']}</a></div>",
                unsafe_allow_html=True,
            )
    except Exception as e:
        st.warning(f"ニュースの取得に失敗しました: {e}")


# ----------------------------------------------------------------------------
# メイン
# ----------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="高配当株ダッシュボード", page_icon="📈", layout="wide")

    now = dt.datetime.now(JST)
    status = "🟢 ザラ場中" if market_is_open(now) else "⚪ 市場時間外"

    # --- ヘッダー（状態バー） ---
    h1, h2, h3 = st.columns([5, 1, 1.2])
    with h1:
        st.title("📈 高配当株ダッシュボード")
        st.caption(f"{status}　|　最終更新 {now:%m/%d %H:%M} JST　|　銘柄リスト: Googleシート連携")
    with h2:
        st.write("")
        if st.button("🔄 今すぐ更新", use_container_width=True):
            # 対象を限定してクリア（個別ページ等の重いキャッシュは温存する）
            fetch_market_data.clear()
            fetch_news.clear()
            st.rerun()
    with h3:
        st.write("")
        if st.button("🇺🇸 米国ETF分析へ", use_container_width=True):
            st.switch_page("pages/2_米国ETF分析.py")

    # --- データ取得 ---
    try:
        df, fallback = fetch_market_data()
    except Exception as e:
        st.error(f"株価データの取得に失敗しました: {e}")
        return

    if fallback:
        st.warning(
            "⚠️ Googleスプレッドシートを読み込めなかったため、最小限のフォールバック銘柄"
            f"（{len(df)}件）で表示しています。シートの共有設定（リンクを知っている全員が閲覧可）と"
            "ネットワークをご確認のうえ「今すぐ更新」を押してください。"
        )

    # --- KPI 3枚 ---
    chg = df["前日比(%)"].dropna()
    avg_yield = df["配当利回り(%)"].dropna().mean()
    high_pos_n = int((df["利回り位置"].dropna() >= 80).sum())
    k1, k2, k3 = st.columns(3)
    with k1, st.container(border=True):
        st.metric("平均配当利回り", ui.fmt(avg_yield, "{:.2f}", "%"),
                  help="監視銘柄全体の平均")
    with k2, st.container(border=True):
        st.metric("利回りが過去レンジ上位の銘柄", f"{high_pos_n} 銘柄",
                  help="現在の配当利回りが過去3年レンジの上位20%（利回り位置80%以上）にある銘柄数。割安候補の目安")
    with k3, st.container(border=True):
        if not chg.empty:
            worst = df.loc[df["前日比(%)"].idxmin()]
            st.metric("本日の最大下落", f"{worst['前日比(%)']:+.2f}%",
                      delta=str(worst["銘柄"]), delta_color="off")
        else:
            st.metric("本日の最大下落", "—")

    # --- タブ ---
    tab_list, tab_news = st.tabs(["📋 銘柄一覧", "📰 ニュース"])

    with tab_list:
        render_table(df)

        st.write("")
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

    with tab_news:
        render_news()


if __name__ == "__main__":
    main()
