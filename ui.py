# -*- coding: utf-8 -*-
"""共通UI部品・配色トークン・Plotlyテーマ.

配色の方針(「彩度の予算」):
  - ベースは無彩色+紺(PRIMARY)のみ。見出し・リンク・通常のチャート線はこの範囲。
  - 色が付いている = 注意を向ける対象。上昇/下落・警告・割安圏だけに色を使う。
  - 騰落色は現行の 緑=上昇 / 赤=下落。日本式(赤=上昇)にしたい場合は UP/DOWN を入れ替える。
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ----------------------------------------------------------------------------
# 配色トークン
# ----------------------------------------------------------------------------
PRIMARY = "#1A5CB0"       # 紺(基調色)
PRIMARY_LIGHT = "#7EA6D9"
UP = "#2E7D32"            # 上昇(緑)
DOWN = "#C62828"          # 下落(赤)
WARN = "#EF6C00"          # 警告(橙)
CHEAP = "#0D47A1"         # 割安圏(濃紺)
GRAY = "#757575"
GRAY_LIGHT = "#BDBDBD"

# 下落率の深刻度ヒート(下限, 上限, 背景色, ラベル) ※下限 < 下落率 <= 上限
DECLINE_BUCKETS = [
    (20.0, float("inf"), "#C62828", "20%超の下落"),
    (15.0, 20.0, "#EF6C00", "16〜20%の下落"),
    (10.0, 15.0, "#F9A825", "11〜15%の下落"),
    (5.0, 10.0, "#FFEE58", "5〜10%の下落"),
]


def bucket_for(decline: float):
    """下落率(%)に対応する (背景色, 文字色, ラベル) を返す。該当なしは None。"""
    if decline is None or pd.isna(decline):
        return None
    for low, high, color, label in DECLINE_BUCKETS:
        if low < decline <= high:
            text = "#FFFFFF" if color in ("#C62828", "#EF6C00") else "#000000"
            return color, text, label
    return None


def daychg_style(val):
    """前日比(%)セルの Styler 用スタイル文字列。"""
    if val is None or pd.isna(val):
        return ""
    if val >= 0:
        return f"color: {UP};"
    b = bucket_for(-val)
    if b is None:
        return f"color: {DOWN};"
    bg, text, _ = b
    return f"background-color: {bg}; color: {text}; font-weight: 600;"


# ----------------------------------------------------------------------------
# Plotly 共通テーマ
# ----------------------------------------------------------------------------
def apply_layout(fig: go.Figure, height: int = 340, **kwargs) -> go.Figure:
    """全チャート共通のレイアウト(フォント・マージン・凡例位置)を適用する。"""
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=30, b=10),
        font=dict(size=12),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", y=1.12, font=dict(size=11)),
        hoverlabel=dict(font_size=12),
        **kwargs,
    )
    fig.update_xaxes(gridcolor="rgba(128,128,128,.15)")
    fig.update_yaxes(gridcolor="rgba(128,128,128,.15)")
    return fig


# ----------------------------------------------------------------------------
# バッジ(pill)
# ----------------------------------------------------------------------------
def badge(label: str, value: str, color: str = GRAY) -> str:
    """事実バッジ1個分のHTML。"""
    return (
        f"<span style='display:inline-block;margin:2px 6px 2px 0;padding:3px 10px;"
        f"border:1px solid {color};border-radius:12px;font-size:.82em;'>"
        f"<span style='opacity:.75;'>{label}</span>　"
        f"<b style='color:{color};'>{value}</b></span>"
    )


def badges(items: list[tuple[str, str, str]]):
    """(label, value, color) のリストをバッジ列として描画する。"""
    st.markdown("".join(badge(*it) for it in items), unsafe_allow_html=True)


def fmt(v, spec="{:,.1f}", suffix=""):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return spec.format(v) + suffix


# ----------------------------------------------------------------------------
# ボリンジャーバンド付き株価チャート(日本株・米国ETF共通)
# ----------------------------------------------------------------------------
PERIOD_DAYS = {"3か月": 66, "6か月": 126, "1年": 252, "3年": 756, "5年": 10**6}


def bollinger_chart(close: pd.Series, color: str, days: int,
                    window: int = 20, currency: str = "$",
                    mas: tuple[int, ...] = ()):
    """ボリンジャーバンド(window日移動平均±1σ/±2σ)付き株価チャート。

    mas に (75, 200) 等を渡すと長期移動平均線も重ねる。
    戻り値: (figure, 現在値のσ位置z)。データ不足時は (None, None)。
    """
    if close is None or close.empty or len(close) < window + 5:
        return None, None
    sma = close.rolling(window).mean()
    std = close.rolling(window).std()

    x = close.index[-days:]

    def s(series):
        return series.iloc[-days:]

    money = "{:,.2f}" if currency == "$" else "{:,.0f}"
    unit = "$" if currency == "$" else "円"
    hover_val = "$%{y:,.2f}" if currency == "$" else "%{y:,.0f}円"

    fig = go.Figure()
    # ±2σ帯(薄い塗り)
    fig.add_scatter(x=x, y=s(sma + 2 * std), line=dict(width=0), showlegend=False, hoverinfo="skip")
    fig.add_scatter(x=x, y=s(sma - 2 * std), line=dict(width=0), fill="tonexty",
                    fillcolor="rgba(26,92,176,.08)", name="±2σ", hoverinfo="skip")
    # ±1σ帯(やや濃い塗り)
    fig.add_scatter(x=x, y=s(sma + std), line=dict(width=0), showlegend=False, hoverinfo="skip")
    fig.add_scatter(x=x, y=s(sma - std), line=dict(width=0), fill="tonexty",
                    fillcolor="rgba(26,92,176,.15)", name="±1σ", hoverinfo="skip")
    # 長期移動平均
    ma_colors = {75: "#8D6E63", 200: "#5D4037"}
    for w in mas:
        if len(close) >= w + 5:
            fig.add_scatter(x=x, y=s(close.rolling(w).mean()), name=f"{w}日MA", mode="lines",
                            line=dict(color=ma_colors.get(w, GRAY), width=1.2),
                            hoverinfo="skip")
    # 移動平均と株価
    fig.add_scatter(x=x, y=s(sma), name=f"{window}日MA", mode="lines",
                    line=dict(color=GRAY, width=1.5, dash="dash"),
                    hovertemplate="%{x|%Y/%m/%d} 平均: " + hover_val + "<extra></extra>")
    fig.add_scatter(x=x, y=s(close), name="株価", mode="lines",
                    line=dict(color=color, width=2.2),
                    hovertemplate="%{x|%Y/%m/%d}: " + hover_val + "<extra></extra>")
    apply_layout(fig, height=380, yaxis_title=f"株価({unit})", hovermode="x unified")

    z = None
    if pd.notna(std.iloc[-1]) and std.iloc[-1]:
        z = float((close.iloc[-1] - sma.iloc[-1]) / std.iloc[-1])
    return fig, z


def sigma_badge(z: float | None) -> str:
    """現在値のσ位置を説明するバッジHTMLを返す。"""
    if z is None:
        return ""
    if z >= 2:
        col, desc = DOWN, "＋2σ超：過去平均よりかなり高い水準(過熱気味)"
    elif z >= 1:
        col, desc = WARN, "＋σ側：過去平均より高い水準"
    elif z > -1:
        col, desc = GRAY, "±σ圏内：過去平均に近い水準"
    elif z > -2:
        col, desc = PRIMARY, "−σ側：過去平均より低い水準"
    else:
        col, desc = CHEAP, "−2σ超：過去平均よりかなり低い水準"
    return (
        f"<span style='background:{col};color:#fff;padding:4px 12px;border-radius:12px;"
        f"font-weight:700;'>現在位置 {z:+.2f}σ</span>"
        f"<span style='margin-left:10px;color:{col};font-weight:600;'>{desc}</span>"
    )


def show_chart(fig, empty_msg: str):
    if fig:
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info(empty_msg)
