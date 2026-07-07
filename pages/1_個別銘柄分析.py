# -*- coding: utf-8 -*-
"""個別銘柄分析ページ（銘柄カルテ）.

高配当株投資の2つの問いに1ページで答える構成:
  ① 買い時か？ … 株価チャート(移動平均・ボリンジャーバンド) と
                   配当利回りレンジ(過去5年のレンジ内の現在位置) を横並びで突合。
                   利回り→株価の逆算テーブルで指値の目安も出す。
  ② 配当は安全か？ … 年間配当の推移(減配年マーカー)・配当性向・FCF配当性向・増配率。
  ③ 財務詳細 … 業績・財務推移(確認頻度が低いので折りたたみ)。

データは yfinance（Yahoo Finance）から取得。配当履歴・財務は欠損があり得るため、
判定表示（減配なし年数など）は履歴が十分な場合のみ・参考値として出す。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import ui
from stocks import load_stocks, ticker_of

st.set_page_config(page_title="個別銘柄分析", page_icon="🔎", layout="wide")


# ----------------------------------------------------------------------------
# データ取得
# ----------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def get_stock_list() -> list[dict]:
    stocks, _ = load_stocks()
    return stocks


@st.cache_data(ttl=3600, show_spinner="株価・財務データを取得中…")
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
    cf = safe_df("cashflow")

    try:
        div = t.dividends  # 取得できる全期間の配当履歴
        if not isinstance(div, pd.Series):
            div = pd.Series(dtype=float)
    except Exception:
        div = pd.Series(dtype=float)

    try:
        hist = t.history(period="5y")
        close = hist["Close"].dropna() if "Close" in hist else pd.Series(dtype=float)
        hist_div = hist["Dividends"] if "Dividends" in hist else pd.Series(dtype=float)
    except Exception:
        close = pd.Series(dtype=float)
        hist_div = pd.Series(dtype=float)

    keep = [
        "sector", "industry", "longBusinessSummary", "trailingPE",
        "returnOnEquity", "dividendYield", "marketCap", "previousClose",
        "currentPrice", "regularMarketPrice", "trailingEps",
    ]
    return {
        "info": {k: info.get(k) for k in keep},
        "income": inc,
        "balance": bal,
        "cashflow": cf,
        "dividends": div,
        "close": close,
        "hist_div": hist_div,
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


def _ratio(numer, denom, y, scale=100):
    """numer[y] / denom[y] * scale を安全に計算する（欠損年・分母0はNone）。"""
    n = numer.get(y) if numer is not None else None
    d = denom.get(y) if denom is not None else None
    if n is None or not d:
        return None
    return n / d * scale


# ----------------------------------------------------------------------------
# 配当分析
# ----------------------------------------------------------------------------
def annual_dividends(div: pd.Series) -> pd.Series:
    """年間配当（円/株）。進行中の年は合計が不完全なため除外する。"""
    if div is None or div.empty:
        return pd.Series(dtype=float)
    yearly = div.groupby(div.index.year).sum()
    this_year = pd.Timestamp.now(tz="Asia/Tokyo").year
    return yearly[yearly.index < this_year]


def dividend_analysis(div: pd.Series) -> dict:
    """年間配当から 減配年・連続非減配年数・増配率(DGR) を算出する。

    yfinance の日本株配当履歴は中間配当の欠落があり得るため、
    半期の欠けに影響されにくい「年間合計」ベースで比較し、参考値として扱う。
    """
    yearly = annual_dividends(div)
    out = {"yearly": yearly, "cut_years": [], "no_cut_streak": None,
           "dgr3": None, "dgr5": None, "years": len(yearly)}
    if len(yearly) < 2:
        return out

    vals = yearly.values
    idx = list(yearly.index)
    # 減配年（前年の年間合計を下回った年）
    out["cut_years"] = [idx[i] for i in range(1, len(vals)) if vals[i] < vals[i - 1] * 0.999]

    # 直近から連続で「前年以上」だった年数（減配なし年数）
    streak = 0
    for i in range(len(vals) - 1, 0, -1):
        if vals[i] >= vals[i - 1] * 0.999:
            streak += 1
        else:
            break
    out["no_cut_streak"] = streak

    # 増配率（年平均, CAGR）
    def dgr(n):
        if len(yearly) >= n + 1 and vals[-n - 1] > 0:
            return ((vals[-1] / vals[-n - 1]) ** (1 / n) - 1) * 100
        return None

    out["dgr3"] = dgr(3)
    out["dgr5"] = dgr(5)
    return out


def yield_series_5y(close: pd.Series, hist_div: pd.Series) -> pd.Series:
    """過去5年の株価と配当履歴から、日次の配当利回り(TTM)系列を復元する。"""
    if close is None or close.empty or hist_div is None or hist_div.empty:
        return pd.Series(dtype=float)
    div = hist_div.reindex(close.index).fillna(0.0)
    ttm = div.rolling("365D").sum()
    yld = (ttm / close * 100).dropna()
    # 最初の1年はTTMが揃わないため除外
    yld = yld[yld.index >= close.index[0] + pd.Timedelta(days=365)]
    return yld if len(yld) >= 200 and yld.iloc[-1] > 0 else pd.Series(dtype=float)


def yield_band_chart(yld: pd.Series):
    """配当利回りレンジバンド（過去平均±1σ帯＋現在位置マーカー）。"""
    if yld.empty:
        return None, None
    mean, std = float(yld.mean()), float(yld.std())
    pctl = float((yld <= yld.iloc[-1]).mean() * 100)

    fig = go.Figure()
    fig.add_hrect(y0=mean - std, y1=mean + std, fillcolor="rgba(26,92,176,.10)",
                  line_width=0, annotation_text="過去平均±1σ", annotation_font_size=11)
    fig.add_hline(y=mean, line_dash="dot", line_color=ui.GRAY,
                  annotation_text=f"平均 {mean:.2f}%", annotation_font_size=11)
    fig.add_scatter(x=yld.index, y=yld.values, mode="lines", name="配当利回り(TTM)",
                    line=dict(color=ui.PRIMARY, width=2),
                    hovertemplate="%{x|%Y/%m/%d}: %{y:.2f}%<extra></extra>")
    fig.add_scatter(x=[yld.index[-1]], y=[yld.iloc[-1]], mode="markers", name="現在",
                    marker=dict(color=ui.DOWN, size=10),
                    hovertemplate="現在: %{y:.2f}%<extra></extra>")
    ui.apply_layout(fig, height=380, yaxis_title="配当利回り(%)", hovermode="x unified")
    return fig, pctl


def position_comment(pctl: float) -> tuple[str, str]:
    """利回り位置(百分位)に応じた一言と色を返す。"""
    if pctl >= 80:
        return "過去5年の中では高利回り圏（株価は割安寄り）", ui.CHEAP
    if pctl >= 60:
        return "過去5年の中ではやや高利回り", ui.PRIMARY
    if pctl >= 40:
        return "過去5年の平均的な水準", ui.GRAY
    return "過去5年の中では低利回り圏（株価は割高寄り）", ui.WARN


def reverse_price_table(annual_div: float, current_price: float) -> pd.DataFrame:
    """「利回り◯%になる株価」の逆算テーブル。指値注文の目安に使う。"""
    rows = []
    for y in (3.0, 3.5, 4.0, 4.5, 5.0):
        price = annual_div / (y / 100)
        rows.append({
            "目標利回り": f"{y:.1f}%",
            "株価の目安": f"{price:,.0f}円",
            "現在値との差": f"{(price / current_price - 1) * 100:+.1f}%",
        })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# チャート
# ----------------------------------------------------------------------------
def dividend_history_chart(analysis: dict):
    """年間配当の推移。減配年は赤で明示する。"""
    yearly = analysis["yearly"]
    if yearly.empty:
        return None
    cut = set(analysis["cut_years"])
    colors = [ui.DOWN if y in cut else ui.PRIMARY for y in yearly.index]
    fig = go.Figure(go.Bar(
        x=[str(y) for y in yearly.index], y=yearly.values,
        marker_color=colors,
        hovertemplate="%{x}年: %{y:.2f}円<extra></extra>",
    ))
    for y in cut:
        fig.add_annotation(x=str(y), y=float(yearly[y]), text="減配", showarrow=True,
                           arrowhead=2, arrowcolor=ui.DOWN, font=dict(color=ui.DOWN, size=11))
    ui.apply_layout(fig, height=340, yaxis_title="年間配当(円/株)")
    return fig


def payout_chart(detail: dict, analysis: dict):
    """EPSと配当、EPSベース配当性向の推移。"""
    eps = row_series(detail["income"], "Diluted EPS", "Basic EPS")
    yearly = analysis["yearly"]
    if eps is None and yearly.empty:
        return None
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    if not yearly.empty:
        recent = yearly[yearly.index >= yearly.index.max() - 9]
        fig.add_bar(x=[str(y) for y in recent.index], y=recent.values,
                    name="配当(円)", marker_color=ui.PRIMARY, secondary_y=False)
    if eps is not None:
        fig.add_bar(x=[str(y) for y in eps.index], y=eps.values,
                    name="EPS(円)", marker_color=ui.GRAY_LIGHT, secondary_y=False)
        if not yearly.empty:
            payout = [_ratio(yearly, eps, y) for y in eps.index]
            fig.add_scatter(x=[str(y) for y in eps.index], y=payout, name="配当性向",
                            mode="lines+markers", line=dict(color=ui.WARN), secondary_y=True)
    fig.update_yaxes(title_text="円/株", secondary_y=False)
    fig.update_yaxes(title_text="配当性向(%)", secondary_y=True)
    ui.apply_layout(fig, height=340, barmode="group")
    return fig


def fcf_payout_chart(detail: dict):
    """FCF配当性向（配当支払額 ÷ フリーキャッシュフロー）。約4期分の事実表示。

    FCFがマイナスの年は比率が意味を持たないため除外し、注記で示す。
    戻り値: (figure, 注意書き or None)。データ不足時は (None, None)。
    """
    cf = detail["cashflow"]
    fcf = row_series(cf, "Free Cash Flow")
    paid = row_series(cf, "Cash Dividends Paid", "Common Stock Dividend Paid")
    if fcf is None or paid is None:
        return None, None
    all_years = [y for y in fcf.index if y in paid.index and fcf.get(y) is not None]
    neg_years = [y for y in all_years if fcf[y] <= 0]
    years = [y for y in all_years if fcf[y] > 0]
    if not years:
        return None, None
    ratios = {y: -paid[y] / fcf[y] * 100 for y in years}
    colors = [ui.DOWN if v > 100 else (ui.WARN if v > 80 else ui.PRIMARY) for v in ratios.values()]
    fig = go.Figure(go.Bar(
        x=[str(y) for y in ratios], y=list(ratios.values()), marker_color=colors,
        hovertemplate="%{x}年: %{y:.0f}%<extra></extra>",
    ))
    fig.add_hline(y=100, line_dash="dot", line_color=ui.DOWN,
                  annotation_text="100%（FCF超過）", annotation_font_size=11)
    ui.apply_layout(fig, height=340, yaxis_title="FCF配当性向(%)")

    warn = None
    latest = ratios.get(max(years))
    if neg_years:
        warn = (f"⚠ {', '.join(str(y) for y in sorted(neg_years))}年はFCFがマイナス"
                "（設備投資等でCF赤字）のため表示していません")
    elif latest is not None and latest > 100:
        warn = f"⚠ 直近期 {latest:.0f}%：配当がフリーCFを上回っています（要確認）"
    return fig, warn


def perf_chart(detail: dict):
    """売上高と利益率の推移。"""
    inc = detail["income"]
    rev = row_series(inc, "Total Revenue")
    op = row_series(inc, "Operating Income")
    net = row_series(inc, "Net Income", "Net Income Common Stockholders")
    if rev is None:
        return None
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    years = [str(y) for y in rev.index]
    fig.add_bar(x=years, y=(rev / 1e6), name="売上高(百万円)", marker_color=ui.GRAY_LIGHT, secondary_y=False)
    if op is not None:
        om = [_ratio(op, rev, y) for y in rev.index]
        fig.add_scatter(x=years, y=om, name="営業利益率", mode="lines+markers",
                        line=dict(color=ui.PRIMARY), secondary_y=True)
    if net is not None:
        nm = [_ratio(net, rev, y) for y in rev.index]
        fig.add_scatter(x=years, y=nm, name="純利益率", mode="lines+markers",
                        line=dict(color=ui.WARN), secondary_y=True)
    fig.update_yaxes(title_text="売上高(百万円)", secondary_y=False)
    fig.update_yaxes(title_text="利益率(%)", secondary_y=True)
    ui.apply_layout(fig, height=340)
    return fig


def finance_chart(detail: dict):
    """純資産・負債と自己資本比率の推移。"""
    bal = detail["balance"]
    assets = row_series(bal, "Total Assets")
    liab = row_series(bal, "Total Liabilities Net Minority Interest")
    equity = row_series(bal, "Stockholders Equity", "Common Stock Equity")
    if equity is None and liab is None:
        return None
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    base = equity if equity is not None else liab
    years = [str(y) for y in base.index]
    if equity is not None:
        fig.add_bar(x=years, y=(equity / 1e6), name="純資産(百万円)", marker_color=ui.PRIMARY, secondary_y=False)
    if liab is not None:
        fig.add_bar(x=[str(y) for y in liab.index], y=(-liab / 1e6), name="負債(百万円)",
                    marker_color=ui.GRAY_LIGHT, secondary_y=False)
    if assets is not None and equity is not None:
        er = [_ratio(equity, assets, y) for y in equity.index]
        fig.add_scatter(x=years, y=er, name="自己資本比率", mode="lines+markers",
                        line=dict(color=ui.WARN), secondary_y=True)
    fig.update_yaxes(title_text="金額(百万円)", secondary_y=False)
    fig.update_yaxes(title_text="自己資本比率(%)", secondary_y=True)
    ui.apply_layout(fig, height=340, barmode="relative")
    return fig


# ----------------------------------------------------------------------------
# メイン
# ----------------------------------------------------------------------------
def main():
    stocks = get_stock_list()
    labels = [f"{s['code']}_{s['name']}" for s in stocks]

    if st.sidebar.button("← 銘柄一覧に戻る", use_container_width=True):
        st.switch_page("app.py")
    default = st.session_state.get("selected_label", labels[0] if labels else "")
    choice = st.sidebar.selectbox("銘柄を選択", labels,
                                  index=labels.index(default) if default in labels else 0)
    st.session_state["selected_label"] = choice
    sheet = stocks[labels.index(choice)]

    detail = fetch_detail(ticker_of(sheet["code"]))
    info = detail["info"]
    close = detail["close"]
    analysis = dividend_analysis(detail["dividends"])
    yearly = analysis["yearly"]

    # --- ヘッダー: 銘柄名と事実バッジ ---
    st.title(f"🔎 {sheet['code']}　{sheet['name']}")

    price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
    if price is None and not close.empty:
        price = float(close.iloc[-1])

    dy = sheet.get("yield")
    if dy is None:
        dy = info.get("dividendYield")

    eps = info.get("trailingEps")
    payout = None
    if eps and not yearly.empty:
        payout = yearly.iloc[-1] / eps * 100

    items = [
        ("株価", ui.fmt(price, "{:,.0f}", "円"), ui.PRIMARY),
        ("配当利回り", ui.fmt(dy, "{:.2f}", "%"), ui.PRIMARY),
        ("PER", ui.fmt(info.get("trailingPE"), "{:.1f}", "倍"), ui.GRAY),
        ("自己資本比率", ui.fmt(sheet.get("equity_ratio"), "{:.1f}", "%"), ui.GRAY),
    ]
    if payout is not None:
        items.append(("配当性向(直近)", ui.fmt(payout, "{:.0f}", "%"),
                      ui.DOWN if payout > 100 else (ui.WARN if payout > 80 else ui.GRAY)))
    # 減配なし年数は履歴7年以上のときだけ表示（yfinance由来の参考値）
    if analysis["years"] >= 7 and analysis["no_cut_streak"] is not None:
        streak = analysis["no_cut_streak"]
        items.append(("減配なし", f"{streak}年連続", ui.UP if streak >= 5 else ui.GRAY))
    elif analysis["years"]:
        items.append(("配当履歴", f"{analysis['years']}年分", ui.GRAY))
    mcap = info.get("marketCap")
    if mcap:
        items.append(("時価総額", f"{mcap / 1e8:,.0f}億円", ui.GRAY))
    ui.badges(items)

    # --- 市場 / 業種 / 企業概要 / リンク ---
    c = st.columns([1, 1.4, 4, 2])
    c[0].markdown(f"**市場**\n\n{sheet.get('market') or '—'}")
    industry = info.get("industry") or info.get("sector") or "—"
    c[1].markdown(f"**業種**\n\n{industry}")
    summary = info.get("longBusinessSummary") or "企業概要データなし"
    c[2].markdown(f"**企業概要**\n\n{summary[:140]}{'…' if len(summary) > 140 else ''}")
    c[3].markdown(
        f"**リンク**\n\n[IRBANK決算情報]({sheet.get('irbank_url', '')})　"
        f"[企業情報(Google)]({sheet.get('info_url', '')})"
    )

    st.divider()

    # ======================================================== ① 買い時サマリー
    st.markdown("### 💹 買い時サマリー")
    g1, g2 = st.columns(2)

    with g1:
        st.markdown("**株価とボリンジャーバンド（20日MA±1σ/±2σ・75/200日MA）**")
        period = st.radio("表示期間", ["6か月", "1年", "3年", "5年"], index=1,
                          horizontal=True, key="price_period", label_visibility="collapsed")
        days = ui.PERIOD_DAYS[period]
        fig_bb, z = ui.bollinger_chart(close, ui.PRIMARY, days, currency="円", mas=(75, 200))
        if fig_bb:
            st.markdown(ui.sigma_badge(z), unsafe_allow_html=True)
            st.plotly_chart(fig_bb, use_container_width=True)
        else:
            st.info("株価データなし")

    with g2:
        st.markdown("**配当利回りレンジ（過去5年・TTMベース）**")
        yld = yield_series_5y(close, detail["hist_div"])
        fig_y, pctl = yield_band_chart(yld)
        if fig_y:
            comment, col = position_comment(pctl)
            st.markdown(
                f"<span style='background:{col};color:#fff;padding:4px 12px;border-radius:12px;"
                f"font-weight:700;'>現在 {yld.iloc[-1]:.2f}%｜過去レンジの上位 {100 - pctl:.0f}%</span>"
                f"<span style='margin-left:10px;color:{col};font-weight:600;'>{comment}</span>",
                unsafe_allow_html=True,
            )
            st.plotly_chart(fig_y, use_container_width=True)
        else:
            st.info("配当履歴が不足しているため利回りレンジを計算できません。")

    # 逆算テーブル（指値の目安）
    annual_div = None
    if not yearly.empty:
        annual_div = float(yearly.iloc[-1])
    if annual_div and price:
        st.markdown("**利回り→株価の逆算（指値の目安）**")
        st.caption(f"直近の年間配当 {annual_div:.2f}円/株 が維持される前提での株価水準。")
        t1, _sp = st.columns([2, 1.5])
        t1.dataframe(reverse_price_table(annual_div, float(price)),
                     use_container_width=True, hide_index=True)

    st.caption(
        "💡 買い時判断の考え方: 利回りが過去レンジ上位（割安寄り）でも、"
        "下の「配当の質」で減配リスクが見える場合は利回りの罠（バリュートラップ）の可能性があります。"
        "必ずセットで確認してください。"
    )

    st.divider()

    # ======================================================== ② 配当の質
    st.markdown("### 💰 配当の質")
    if analysis["cut_years"]:
        recent_cuts = [y for y in analysis["cut_years"] if y >= (yearly.index.max() - 9)]
        if recent_cuts:
            st.markdown(
                f"<span style='background:{ui.DOWN};color:#fff;padding:4px 12px;border-radius:12px;"
                f"font-weight:700;'>⚠ 過去10年で減配 {len(recent_cuts)}回"
                f"（{', '.join(str(y) for y in recent_cuts)}年）</span>"
                f"<span style='margin-left:10px;font-size:.85em;opacity:.7;'>"
                f"yfinance由来の参考値。中間配当の欠落等で誤差があり得ます</span>",
                unsafe_allow_html=True,
            )

    d1, d2 = st.columns(2)
    with d1:
        st.markdown("**年間配当の推移（減配年は赤）**")
        ui.show_chart(dividend_history_chart(analysis), "配当データなし")
    with d2:
        st.markdown("**EPS・配当・配当性向**")
        ui.show_chart(payout_chart(detail, analysis), "EPS・配当データなし")

    d3, d4 = st.columns(2)
    with d3:
        st.markdown("**FCF配当性向（配当支払 ÷ フリーCF）**")
        fig_fcf, fcf_warn = fcf_payout_chart(detail)
        if fig_fcf:
            if fcf_warn:
                st.caption(fcf_warn)
            st.plotly_chart(fig_fcf, use_container_width=True)
        else:
            st.info("キャッシュフローデータなし")
    with d4:
        st.markdown("**増配率（年平均）**")
        dgr_items = []
        if analysis["dgr3"] is not None:
            dgr_items.append(("3年", f"{analysis['dgr3']:+.1f}%/年",
                              ui.UP if analysis["dgr3"] > 0 else ui.DOWN))
        if analysis["dgr5"] is not None:
            dgr_items.append(("5年", f"{analysis['dgr5']:+.1f}%/年",
                              ui.UP if analysis["dgr5"] > 0 else ui.DOWN))
        if dgr_items:
            ui.badges(dgr_items)
            st.caption(
                "増配率が高い銘柄は、現在の利回りが低めでも数年後の取得利回り(YOC)が伸びます。"
            )
            if not yearly.empty and dy and analysis["dgr5"] and analysis["dgr5"] > 0:
                yoc5 = dy * (1 + analysis["dgr5"] / 100) ** 5
                st.metric("5年後の想定YOC（現在の増配ペースが続いた場合）", f"{yoc5:.2f}%",
                          delta=f"{yoc5 - dy:+.2f}pt", delta_color="normal")
        else:
            st.info("増配率を計算できるだけの配当履歴がありません。")

    st.divider()

    # ======================================================== ③ 財務詳細
    with st.expander("🏥 財務詳細（業績・財務の推移）", expanded=False):
        f1, f2 = st.columns(2)
        with f1:
            st.markdown("**業績推移**")
            ui.show_chart(perf_chart(detail), "業績データなし")
        with f2:
            st.markdown("**財務推移**")
            ui.show_chart(finance_chart(detail), "財務データなし")

    st.caption(
        "※ データは yfinance（Yahoo Finance）より取得。配当履歴には中間配当の欠落等の誤差があり得ます。"
        "財務データは直近4年ほど。表示は参考情報であり、投資助言ではありません。"
    )


if __name__ == "__main__":
    main()
