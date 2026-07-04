# -*- coding: utf-8 -*-
"""個別銘柄分析ページ.

選択した1銘柄について、KPI・業績/配当/財務の推移グラフ・スコアレーダーを表示する。
データは yfinance（Yahoo Finance）から取得（財務は直近4年ほど）。
スコア・総合評価は当アプリ独自の計算式による簡易評価です。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from stocks import load_stocks, ticker_of

st.set_page_config(page_title="個別銘柄分析", page_icon="🔎", layout="wide")

RADAR_AXES = ["安全性", "配当性", "成長性", "効率性", "収益性", "割安性"]


# ----------------------------------------------------------------------------
# データ取得
# ----------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def get_stock_list() -> list[dict]:
    return load_stocks()


@st.cache_data(ttl=3600, show_spinner="財務データを取得中…")
def fetch_detail(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    try:
        info = t.info or {}
    except Exception:
        info = {}

    def safe_df(attr):
        try:
            df = getattr(t, attr)
            return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    inc = safe_df("income_stmt")
    bal = safe_df("balance_sheet")
    try:
        div = t.dividends
    except Exception:
        div = pd.Series(dtype=float)

    keep = [
        "sector", "industry", "longBusinessSummary", "trailingPE",
        "returnOnEquity", "dividendYield", "marketCap", "previousClose",
        "currentPrice", "regularMarketPrice", "trailingEps",
    ]
    return {
        "info": {k: info.get(k) for k in keep},
        "income": inc,
        "balance": bal,
        "dividends": div if isinstance(div, pd.Series) else pd.Series(dtype=float),
    }


def row_series(df: pd.DataFrame, *names) -> pd.Series | None:
    """財務諸表から指定行を取り出し、年をインデックスにした昇順 Series で返す。"""
    if df is None or df.empty:
        return None
    for n in names:
        if n in df.index:
            s = df.loc[n].dropna()
            if s.empty:
                continue
            s.index = [idx.year for idx in s.index]
            return s.sort_index()
    return None


def clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


# ----------------------------------------------------------------------------
# 指標・スコア計算
# ----------------------------------------------------------------------------
def compute_metrics(detail: dict, sheet: dict) -> dict:
    info = detail["info"]
    inc, bal = detail["income"], detail["balance"]

    rev = row_series(inc, "Total Revenue")
    op = row_series(inc, "Operating Income")
    net = row_series(inc, "Net Income", "Net Income Common Stockholders")
    assets = row_series(bal, "Total Assets")
    liab = row_series(bal, "Total Liabilities Net Minority Interest")
    equity = row_series(bal, "Stockholders Equity", "Common Stock Equity")

    # 現在値
    price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")

    # 時価総額（億円）
    mcap = info.get("marketCap")
    mcap_oku = mcap / 1e8 if mcap else None

    per = info.get("trailingPE")
    roe = info.get("returnOnEquity")
    roe_pct = roe * 100 if roe is not None else None

    # 自己資本比率(%)：貸借対照表から。無ければシートの値。
    equity_ratio = None
    if equity is not None and assets is not None and assets.iloc[-1]:
        equity_ratio = equity.iloc[-1] / assets.iloc[-1] * 100
    if equity_ratio is None:
        equity_ratio = sheet.get("equity_ratio")

    # 配当利回り(%)：シート優先、無ければ info（0〜1の場合は%に換算）
    dy = sheet.get("yield")
    if dy is None:
        raw = info.get("dividendYield")
        if raw is not None:
            dy = raw * 100 if raw < 1 else raw

    # 営業利益率(直近)
    op_margin = None
    if op is not None and rev is not None and rev.iloc[-1]:
        op_margin = op.iloc[-1] / rev.iloc[-1] * 100

    # 売上高成長率(CAGR)
    growth = None
    if rev is not None and len(rev) >= 2 and rev.iloc[0] > 0:
        n = len(rev) - 1
        growth = ((rev.iloc[-1] / rev.iloc[0]) ** (1 / n) - 1) * 100

    return {
        "price": price, "mcap_oku": mcap_oku, "per": per, "roe_pct": roe_pct,
        "equity_ratio": equity_ratio, "dividend_yield": dy,
        "op_margin": op_margin, "growth": growth,
        "rev": rev, "op": op, "net": net,
        "assets": assets, "liab": liab, "equity": equity,
    }


def compute_scores(m: dict) -> tuple[dict, str]:
    """各指標を 0〜100 のスコアに変換し、総合評価(S/A/B/C/D)を返す。"""
    scores = {}

    def add(name, val, func):
        if val is not None:
            scores[name] = round(clamp(func(val)))

    add("収益性", m["roe_pct"], lambda v: v * 5)             # ROE20%→100
    add("安全性", m["equity_ratio"], lambda v: v * 1.25)      # 自己資本比率80%→100
    add("配当性", m["dividend_yield"], lambda v: v / 6 * 100)  # 利回り6%→100
    add("効率性", m["op_margin"], lambda v: v * 4)            # 営業利益率25%→100
    add("成長性", m["growth"], lambda v: (v + 5) / 20 * 100)   # 成長率-5%→0, 15%→100
    if m["per"] is not None and m["per"] > 0:
        scores["割安性"] = round(clamp((25 - m["per"]) / (25 - 8) * 100))  # PER8→100, 25→0

    if scores:
        avg = sum(scores.values()) / len(scores)
        grade = "S" if avg >= 80 else "A" if avg >= 68 else "B" if avg >= 55 else "C" if avg >= 40 else "D"
    else:
        grade = "—"
    return scores, grade


# ----------------------------------------------------------------------------
# 描画パーツ
# ----------------------------------------------------------------------------
def kpi_box(col, label, value, bg, fg="#FFFFFF"):
    col.markdown(
        f"<div style='background:{bg};border-radius:8px;padding:10px 6px;text-align:center;color:{fg};'>"
        f"<div style='font-size:0.82em;opacity:.95;'>{label}</div>"
        f"<div style='font-size:1.5em;font-weight:700;line-height:1.3;'>{value}</div></div>",
        unsafe_allow_html=True,
    )


def fmt(v, spec="{:,.1f}", suffix=""):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return spec.format(v) + suffix


def perf_chart(m: dict):
    rev, op, net = m["rev"], m["op"], m["net"]
    if rev is None:
        return None
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    years = [str(y) for y in rev.index]
    fig.add_bar(x=years, y=(rev / 1e6), name="売上高(百万円)", marker_color="#9E9E9E", secondary_y=False)
    if op is not None:
        om = [op.get(y, None) / rev.get(y) * 100 if rev.get(y) else None for y in rev.index]
        fig.add_scatter(x=years, y=om, name="営業利益率", mode="lines+markers", line=dict(color="#EF5350"), secondary_y=True)
    if net is not None:
        nm = [net.get(y, None) / rev.get(y) * 100 if rev.get(y) else None for y in rev.index]
        fig.add_scatter(x=years, y=nm, name="純利益率", mode="lines+markers", line=dict(color="#FFB300"), secondary_y=True)
    fig.update_yaxes(title_text="売上高(百万円)", secondary_y=False)
    fig.update_yaxes(title_text="利益率(%)", secondary_y=True)
    fig.update_layout(height=340, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.15))
    return fig


def finance_chart(m: dict):
    equity, liab = m["equity"], m["liab"]
    if equity is None and liab is None:
        return None
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    base = equity if equity is not None else liab
    years = [str(y) for y in base.index]
    if equity is not None:
        fig.add_bar(x=years, y=(equity / 1e6), name="純資産(百万円)", marker_color="#42A5F5", secondary_y=False)
    if liab is not None:
        fig.add_bar(x=[str(y) for y in liab.index], y=(-liab / 1e6), name="負債(百万円)", marker_color="#EF5350", secondary_y=False)
    if m["assets"] is not None and equity is not None:
        er = [equity.get(y, None) / m["assets"].get(y) * 100 if m["assets"].get(y) else None for y in equity.index]
        fig.add_scatter(x=years, y=er, name="自己資本比率", mode="lines+markers", line=dict(color="#FFB300"), secondary_y=True)
    fig.update_yaxes(title_text="金額(百万円)", secondary_y=False)
    fig.update_yaxes(title_text="自己資本比率(%)", secondary_y=True)
    fig.update_layout(height=340, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.15), barmode="relative")
    return fig


def dividend_chart(detail: dict, m: dict):
    div = detail["dividends"]
    eps = row_series(detail["income"], "Diluted EPS", "Basic EPS")
    if (div is None or div.empty) and eps is None:
        return None
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    div_year = None
    if div is not None and not div.empty:
        div_year = div.groupby(div.index.year).sum()
        years = [str(y) for y in div_year.index]
        fig.add_bar(x=years, y=div_year.values, name="配当(円)", marker_color="#42A5F5", secondary_y=False)
    if eps is not None:
        fig.add_bar(x=[str(y) for y in eps.index], y=eps.values, name="EPS(円)", marker_color="#EF5350", secondary_y=False)
    if eps is not None and div_year is not None:
        payout = [div_year.get(y, None) / eps.get(y) * 100 if eps.get(y) else None for y in eps.index]
        fig.add_scatter(x=[str(y) for y in eps.index], y=payout, name="配当性向", mode="lines+markers", line=dict(color="#FFB300"), secondary_y=True)
    fig.update_yaxes(title_text="円", secondary_y=False)
    fig.update_yaxes(title_text="配当性向(%)", secondary_y=True)
    fig.update_layout(height=340, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.15), barmode="group")
    return fig


def radar_chart(scores: dict):
    vals = [scores.get(a, 0) for a in RADAR_AXES]
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=vals + [vals[0]], theta=RADAR_AXES + [RADAR_AXES[0]],
        fill="toself", line=dict(color="#1976D2"), name="スコア",
    ))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
        height=340, margin=dict(l=30, r=30, t=30, b=30), showlegend=False,
    )
    return fig


# ----------------------------------------------------------------------------
# メイン
# ----------------------------------------------------------------------------
def main():
    st.title("🔎 個別銘柄分析")

    stocks = get_stock_list()
    labels = [f"{s['code']}_{s['name']}" for s in stocks]

    # サイドバーに一覧へ戻るボタン
    if st.sidebar.button("← 銘柄一覧に戻る", use_container_width=True):
        st.switch_page("app.py")

    # サイドバーで銘柄選択
    default = st.session_state.get("selected_label", labels[0] if labels else "")
    choice = st.sidebar.selectbox("銘柄を選択", labels, index=labels.index(default) if default in labels else 0)
    st.session_state["selected_label"] = choice
    sheet = stocks[labels.index(choice)]

    detail = fetch_detail(ticker_of(sheet["code"]))
    m = compute_metrics(detail, sheet)
    scores, grade = compute_scores(m)
    info = detail["info"]

    st.subheader(f"{sheet['code']}　{sheet['name']}")

    # --- 市場 / 業種 / 企業概要 / リンク ---
    c = st.columns([1, 1.4, 4, 2])
    c[0].markdown(f"**市場**\n\n{sheet.get('market') or '—'}")
    industry = info.get("industry") or info.get("sector") or "—"
    c[1].markdown(f"**業種**\n\n{industry}")
    summary = info.get("longBusinessSummary") or "企業概要データなし"
    c[2].markdown(f"**企業概要**\n\n{summary[:140]}{'…' if len(summary) > 140 else ''}")
    links = f"[IRBANK決算情報]({sheet.get('irbank_url','')})　[企業情報(Google)]({sheet.get('info_url','')})"
    c[3].markdown(f"**リンク**\n\n{links}")

    st.divider()

    # --- KPIカード ---
    k = st.columns(7)
    kpi_box(k[0], "株価(円)", fmt(m["price"], "{:,.0f}"), "#1565C0")
    kpi_box(k[1], "時価総額(億円)", fmt(m["mcap_oku"], "{:,.0f}"), "#EF9A06")
    kpi_box(k[2], "PER", fmt(m["per"], "{:.2f}"), "#66BB6A")
    kpi_box(k[3], "ROE", fmt(m["roe_pct"], "{:.2f}", "%"), "#EF7360")
    kpi_box(k[4], "自己資本比率", fmt(m["equity_ratio"], "{:.1f}", "%"), "#4FC3F7", fg="#0d3b52")
    kpi_box(k[5], "配当利回り", fmt(m["dividend_yield"], "{:.2f}", "%"), "#9E9E9E")
    grade_color = {"S": "#6A1B9A", "A": "#7B1FA2", "B": "#8E24AA", "C": "#AB47BC", "D": "#BA68C8", "—": "#9E9E9E"}[grade]
    kpi_box(k[6], "総合評価", grade, grade_color)

    st.divider()

    # --- グラフ 2x2 ---
    r1 = st.columns(2)
    with r1[0]:
        st.markdown("**業績推移**")
        fig = perf_chart(m)
        if fig:
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("業績データなし")
    with r1[1]:
        st.markdown("**配当推移**")
        fig = dividend_chart(detail, m)
        if fig:
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("配当データなし")

    r2 = st.columns(2)
    with r2[0]:
        st.markdown("**財務推移**")
        fig = finance_chart(m)
        if fig:
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("財務データなし")
    with r2[1]:
        st.markdown("**スコア（独自算出）**")
        st.plotly_chart(radar_chart(scores), use_container_width=True)
        if scores:
            st.caption("　".join(f"{k}:{v}" for k, v in scores.items()))

    st.caption(
        "※ 財務データは yfinance（Yahoo Finance）より取得（直近4年ほど）。"
        "スコア・総合評価は当アプリ独自の簡易計算式で、投資助言ではありません。"
    )


if __name__ == "__main__":
    main()
