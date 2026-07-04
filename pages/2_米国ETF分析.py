# -*- coding: utf-8 -*-
"""米国 高配当・増配ETF 分析ページ.

HDV / SPYD / VYM / SCHD / VIG の5本を
  ❶ セクター構成  ❷ 構成銘柄の重複関係（上位10銘柄ベース）  ❸ 分配金推移
の3視点で比較し、個別ETFの詳細分析も行う。

データは yfinance（Yahoo Finance）から取得。
※ 構成銘柄はAPIの制約により各ETFの「上位10銘柄」のみ。重複関係は上位銘柄ベースの参考値です。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
import yfinance as yf
import plotly.graph_objects as go

st.set_page_config(page_title="米国ETF分析", page_icon="🇺🇸", layout="wide")

ETFS = {
    "HDV": "iシェアーズ 米国高配当株ETF",
    "SPYD": "SPDR S&P500 高配当株ETF",
    "VYM": "バンガード 米国高配当株ETF",
    "SCHD": "シュワブ 米国配当株ETF",
    "VIG": "バンガード 米国増配株ETF",
}

ETF_COLORS = {
    "HDV": "#1565C0",
    "SPYD": "#EF6C00",
    "VYM": "#2E7D32",
    "SCHD": "#8E24AA",
    "VIG": "#C62828",
}

SECTOR_JA = {
    "technology": "情報技術",
    "financial_services": "金融",
    "healthcare": "ヘルスケア",
    "consumer_defensive": "生活必需品",
    "consumer_cyclical": "一般消費財",
    "industrials": "資本財",
    "energy": "エネルギー",
    "utilities": "公益事業",
    "communication_services": "通信",
    "basic_materials": "素材",
    "realestate": "不動産",
}


# ----------------------------------------------------------------------------
# データ取得
# ----------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner="ETFデータを取得中…")
def fetch_etf(symbol: str) -> dict:
    t = yf.Ticker(symbol)
    try:
        info = t.info or {}
    except Exception:
        info = {}

    sectors, holdings = {}, pd.DataFrame()
    try:
        fd = t.funds_data
        sectors = dict(fd.sector_weightings or {})
        th = fd.top_holdings
        if isinstance(th, pd.DataFrame):
            holdings = th.reset_index()
    except Exception:
        pass

    try:
        div = t.dividends
    except Exception:
        div = pd.Series(dtype=float)

    try:
        hist = t.history(period="5y")["Close"]
    except Exception:
        hist = pd.Series(dtype=float)

    keep = ["dividendYield", "netExpenseRatio", "totalAssets", "regularMarketPrice", "longBusinessSummary"]
    return {
        "info": {k: info.get(k) for k in keep},
        "sectors": sectors,
        "holdings": holdings,
        "dividends": div if isinstance(div, pd.Series) else pd.Series(dtype=float),
        "price": hist if isinstance(hist, pd.Series) else pd.Series(dtype=float),
    }


@st.cache_data(ttl=3600, show_spinner="5本のETFを取得中…")
def fetch_all() -> dict[str, dict]:
    return {sym: fetch_etf(sym) for sym in ETFS}


def annual_dividends(div: pd.Series) -> pd.Series:
    """年間分配金（$/口）。当年（進行中の年）は除外する。"""
    if div is None or div.empty:
        return pd.Series(dtype=float)
    yearly = div.groupby(div.index.year).sum()
    this_year = pd.Timestamp.now(tz="UTC").year
    return yearly[yearly.index < this_year]


def fmt(v, spec="{:,.2f}", suffix=""):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return spec.format(v) + suffix


def kpi_card(col, label, value, sub, gradient, icon=""):
    col.markdown(
        f"""<div style="background:linear-gradient(135deg,{gradient});border-radius:14px;
        padding:14px 16px;color:#fff;box-shadow:0 4px 10px rgba(0,0,0,.12);min-height:104px;">
        <div style="font-size:.78em;opacity:.9;">{icon} {label}</div>
        <div style="font-size:1.6em;font-weight:800;line-height:1.4;">{value}</div>
        <div style="font-size:.75em;opacity:.85;">{sub}</div></div>""",
        unsafe_allow_html=True,
    )


# ----------------------------------------------------------------------------
# 比較グラフ
# ----------------------------------------------------------------------------
def sector_compare_chart(data: dict[str, dict]):
    """❶ セクター構成の積み上げ棒グラフ（x=ETF）。"""
    rows = {}
    for sym, d in data.items():
        rows[sym] = {SECTOR_JA.get(k, k): v * 100 for k, v in d["sectors"].items()}
    df = pd.DataFrame(rows).fillna(0)
    if df.empty:
        return None
    # 全ETF合計が大きいセクター順に積む
    df = df.loc[df.sum(axis=1).sort_values(ascending=False).index]
    fig = go.Figure()
    palette = ["#1565C0", "#EF6C00", "#2E7D32", "#8E24AA", "#C62828", "#00838F",
               "#F9A825", "#5D4037", "#7B1FA2", "#455A64", "#AD1457"]
    for i, sector in enumerate(df.index):
        fig.add_bar(
            x=list(df.columns), y=df.loc[sector], name=sector,
            marker_color=palette[i % len(palette)],
            hovertemplate="%{x}<br>" + sector + " %{y:.1f}%<extra></extra>",
        )
    fig.update_layout(
        barmode="stack", height=430, margin=dict(l=10, r=10, t=10, b=10),
        yaxis_title="構成比(%)", legend=dict(font=dict(size=11)),
    )
    return fig


def overlap_matrix(data: dict[str, dict]):
    """❷ 上位10銘柄の重複数マトリクスと、複数ETFに含まれる銘柄の一覧を返す。"""
    sets, names = {}, {}
    for sym, d in data.items():
        h = d["holdings"]
        if h.empty or "Symbol" not in h.columns:
            sets[sym] = set()
            continue
        sets[sym] = set(h["Symbol"])
        for _, r in h.iterrows():
            names[r["Symbol"]] = r.get("Name", r["Symbol"])

    syms = list(ETFS)
    mat = [[len(sets[a] & sets[b]) for b in syms] for a in syms]

    fig = go.Figure(
        go.Heatmap(
            z=mat, x=syms, y=syms,
            colorscale=[[0, "#E3F2FD"], [1, "#0D47A1"]],
            text=[[str(v) for v in row] for row in mat],
            texttemplate="%{text}", textfont=dict(size=16),
            hovertemplate="%{y} × %{x}: %{z}銘柄が重複<extra></extra>",
            showscale=False,
        )
    )
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), yaxis=dict(autorange="reversed"))

    # 2本以上のETFに登場する銘柄
    counts = {}
    for sym in syms:
        for s in sets[sym]:
            counts.setdefault(s, []).append(sym)
    dup = [
        {"ティッカー": s, "銘柄名": names.get(s, s), "保有ETF数": len(e), "保有ETF": " / ".join(e)}
        for s, e in counts.items() if len(e) >= 2
    ]
    dup_df = pd.DataFrame(dup)
    if not dup_df.empty:
        dup_df = dup_df.sort_values(["保有ETF数", "ティッカー"], ascending=[False, True]).reset_index(drop=True)
    return fig, dup_df


def dividend_compare_chart(data: dict[str, dict]):
    """❸ 年間分配金推移の折れ線（$/口）。"""
    fig = go.Figure()
    for sym, d in data.items():
        yearly = annual_dividends(d["dividends"])
        if yearly.empty:
            continue
        fig.add_scatter(
            x=[str(y) for y in yearly.index], y=yearly.values,
            name=sym, mode="lines+markers", line=dict(color=ETF_COLORS[sym], width=2.5),
            hovertemplate=sym + " %{x}年: $%{y:.3f}<extra></extra>",
        )
    if not fig.data:
        return None
    fig.update_layout(
        height=430, margin=dict(l=10, r=10, t=10, b=10),
        yaxis_title="年間分配金($/口)", legend=dict(orientation="h", y=1.08),
    )
    return fig


def dividend_growth_chart(data: dict[str, dict], base_year: int = 2016):
    """分配金の成長率（基準年=100）比較。"""
    fig = go.Figure()
    for sym, d in data.items():
        yearly = annual_dividends(d["dividends"])
        yearly = yearly[yearly.index >= base_year]
        if yearly.empty or yearly.iloc[0] == 0:
            continue
        idx = yearly / yearly.iloc[0] * 100
        fig.add_scatter(
            x=[str(y) for y in idx.index], y=idx.values,
            name=sym, mode="lines+markers", line=dict(color=ETF_COLORS[sym], width=2.5),
            hovertemplate=sym + " %{x}年: %{y:.0f}<extra></extra>",
        )
    if not fig.data:
        return None
    fig.add_hline(y=100, line_dash="dot", line_color="#999")
    fig.update_layout(
        height=430, margin=dict(l=10, r=10, t=10, b=10),
        yaxis_title=f"分配金指数（{base_year}年=100）", legend=dict(orientation="h", y=1.08),
    )
    return fig


# ----------------------------------------------------------------------------
# 個別分析グラフ
# ----------------------------------------------------------------------------
def sector_pie(d: dict):
    s = {SECTOR_JA.get(k, k): v * 100 for k, v in d["sectors"].items() if v > 0}
    if not s:
        return None
    fig = go.Figure(go.Pie(labels=list(s.keys()), values=list(s.values()), hole=0.45,
                           hovertemplate="%{label}: %{value:.1f}%<extra></extra>"))
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=10, b=10))
    return fig


def holdings_bar(d: dict):
    h = d["holdings"]
    if h.empty:
        return None
    h = h.iloc[::-1]
    fig = go.Figure(go.Bar(
        x=h["Holding Percent"] * 100, y=h["Symbol"], orientation="h",
        marker_color="#1565C0",
        text=[f"{v*100:.1f}%" for v in h["Holding Percent"]],
        textposition="outside", cliponaxis=False,
        customdata=h["Name"],
        hovertemplate="%{y}（%{customdata}）: %{x:.2f}%<extra></extra>",
    ))
    fig.update_layout(height=360, margin=dict(l=10, r=60, t=10, b=10), xaxis_title="構成比(%)")
    return fig


def dividend_bar(d: dict, color: str):
    yearly = annual_dividends(d["dividends"])
    if yearly.empty:
        return None
    fig = go.Figure(go.Bar(
        x=[str(y) for y in yearly.index], y=yearly.values, marker_color=color,
        hovertemplate="%{x}年: $%{y:.3f}<extra></extra>",
    ))
    fig.update_layout(height=330, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="年間分配金($/口)")
    return fig


def price_line(d: dict, color: str):
    p = d["price"]
    if p is None or p.empty:
        return None
    fig = go.Figure(go.Scatter(x=p.index, y=p.values, line=dict(color=color, width=2),
                               hovertemplate="%{x|%Y/%m/%d}: $%{y:.2f}<extra></extra>"))
    fig.update_layout(height=330, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="株価($)")
    return fig


def show_chart(fig, empty_msg: str):
    if fig:
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info(empty_msg)


# ----------------------------------------------------------------------------
# メイン
# ----------------------------------------------------------------------------
def main():
    if st.sidebar.button("← 銘柄一覧に戻る", use_container_width=True):
        st.switch_page("app.py")

    st.markdown(
        """<div style="background:linear-gradient(90deg,#B71C1C,#1565C0);border-radius:14px;
        padding:18px 24px;color:#fff;margin-bottom:14px;">
        <span style="font-size:1.55em;font-weight:800;">🇺🇸 米国 高配当・増配ETF 分析</span>
        <span style="margin-left:14px;font-size:.85em;opacity:.9;">HDV / SPYD / VYM / SCHD / VIG</span>
        </div>""",
        unsafe_allow_html=True,
    )

    try:
        data = fetch_all()
    except Exception as e:
        st.error(f"ETFデータの取得に失敗しました: {e}")
        return

    # --- サマリー表 ---
    rows = []
    for sym, d in data.items():
        info = d["info"]
        yearly = annual_dividends(d["dividends"])
        rows.append({
            "ETF": sym,
            "名称": ETFS[sym],
            "株価($)": info.get("regularMarketPrice"),
            "分配金利回り(%)": info.get("dividendYield"),
            "経費率(%)": info.get("netExpenseRatio"),
            "純資産(億$)": info.get("totalAssets") / 1e8 if info.get("totalAssets") else None,
            "直近年間分配金($)": yearly.iloc[-1] if not yearly.empty else None,
        })
    st.dataframe(
        pd.DataFrame(rows).style.format({
            "株価($)": "{:,.2f}", "分配金利回り(%)": "{:.2f}", "経費率(%)": "{:.2f}",
            "純資産(億$)": "{:,.0f}", "直近年間分配金($)": "{:.3f}",
        }, na_rep="—"),
        use_container_width=True, hide_index=True,
    )

    tab_cmp, tab_ind = st.tabs(["📊 5本比較", "🔎 個別分析"])

    # ------------------------------------------------------------------ 比較
    with tab_cmp:
        st.markdown("#### ❶ セクター構成の比較")
        show_chart(sector_compare_chart(data), "セクターデータを取得できませんでした。")

        st.markdown("#### ❷ 構成銘柄の重複関係（上位10銘柄ベース）")
        st.caption("※ 無料データの制約で各ETFの上位10銘柄のみで算出した参考値です。対角線は自ETFの銘柄数(10)。")
        fig, dup_df = overlap_matrix(data)
        c1, c2 = st.columns([1.1, 1.4])
        with c1:
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            if dup_df.empty:
                st.info("上位10銘柄の範囲では重複銘柄はありません。")
            else:
                st.markdown("**複数のETFに含まれる銘柄**")
                st.dataframe(dup_df, use_container_width=True, hide_index=True, height=330)

        st.markdown("#### ❸ 分配金推移の比較")
        g1, g2 = st.columns(2)
        with g1:
            st.markdown("**年間分配金（$/口）**")
            show_chart(dividend_compare_chart(data), "分配金データを取得できませんでした。")
        with g2:
            st.markdown("**分配金の成長（2016年=100）**")
            show_chart(dividend_growth_chart(data), "分配金データを取得できませんでした。")
        st.caption("※ 分配金は1口あたりの実額のため、水準の比較は成長指数（右）が便利です。当年（進行中）は年間合計から除外。")

    # ------------------------------------------------------------------ 個別
    with tab_ind:
        sym = st.selectbox("ETFを選択", list(ETFS), format_func=lambda s: f"{s}　{ETFS[s]}")
        d = data[sym]
        info = d["info"]
        color = ETF_COLORS[sym]
        yearly = annual_dividends(d["dividends"])

        # 増配年数（直近から連続で前年以上だった年数）
        streak = 0
        vals = yearly.values
        for i in range(len(vals) - 1, 0, -1):
            if vals[i] >= vals[i - 1]:
                streak += 1
            else:
                break

        k = st.columns(5)
        kpi_card(k[0], "株価", fmt(info.get("regularMarketPrice"), "${:,.2f}"), sym, "#42A5F5,#1565C0", "💵")
        kpi_card(k[1], "分配金利回り", fmt(info.get("dividendYield"), "{:.2f}", "%"), "直近12か月", "#66BB6A,#2E7D32", "💰")
        kpi_card(k[2], "経費率", fmt(info.get("netExpenseRatio"), "{:.2f}", "%"), "年率", "#FFB74D,#EF6C00", "🧾")
        aum = info.get("totalAssets")
        kpi_card(k[3], "純資産", fmt(aum / 1e8 if aum else None, "{:,.0f}", "億$"), "AUM", "#AB47BC,#6A1B9A", "🏦")
        kpi_card(k[4], "連続増配（概算）", f"{streak}年", "年間分配金ベース", "#EF5350,#B71C1C", "📈")

        st.write("")
        r1 = st.columns(2)
        with r1[0]:
            st.markdown("**セクター構成**")
            show_chart(sector_pie(d), "セクターデータなし")
        with r1[1]:
            st.markdown("**上位10銘柄**")
            show_chart(holdings_bar(d), "構成銘柄データなし")

        r2 = st.columns(2)
        with r2[0]:
            st.markdown("**年間分配金の推移（$/口）**")
            show_chart(dividend_bar(d, color), "分配金データなし")
        with r2[1]:
            st.markdown("**株価推移（5年）**")
            show_chart(price_line(d, color), "株価データなし")

    st.caption(
        "※ データは yfinance（Yahoo Finance）より取得。分配金利回り・経費率等は遅延・欠損の可能性があります。"
        "投資判断は運用会社の公式情報をご確認ください。"
    )


if __name__ == "__main__":
    main()
