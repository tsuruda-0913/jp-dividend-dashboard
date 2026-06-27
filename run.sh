#!/bin/bash
# 高配当株ダッシュボード 起動スクリプト
# 初回は仮想環境の作成と依存パッケージのインストールを自動で行います。
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "初回セットアップ: 仮想環境を作成します…"
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip
  ./.venv/bin/pip install -r requirements.txt
fi

echo "ダッシュボードを起動します。ブラウザが自動で開きます (Ctrl+C で停止)"
exec ./.venv/bin/streamlit run app.py
