#!/usr/bin/env python3
"""Shared Japanese text utilities — common words, non-dialogue markers.

Used by: whisper_pipeline.py, build_glossary.py, noun_checker.py
"""

# Common katakana words that are NOT proper nouns — skip in noun check & glossary
# Merged from noun_checker.py (_JA_COMMON_WORDS) + build_glossary.py (_COMMON_KATAKANA)
COMMON_KATAKANA = frozenset({
    # Basic loanwords
    'ドア','テーブル','パック','バック','テスト','メモ','データ',
    'タイプ','レベル','モデル','システム','プログラム','サービス',
    'ケース','グループ','チーム','クラス','ルール','コード',
    'イメージ','デザイン','コピー','チェック','リスト','ファイル',
    'メッセージ','レポート','サポート','プロジェクト','マシン',
    'ライン','ポイント','ボタン','スイッチ','パネル','ケーブル',
    'エネルギー','スピード','バランス','コントロール','センター',
    'エリア','ゾーン','スペース','ホール','ルーム','ハウス',
    'カード','キー','ロック','ベル','サイン','マーク',
    'パパ','ママ','ボーイ','ガール','ベビー',
    # Common verbs/adjectives in katakana
    'スタート','ストップ','チェンジ','オープン','クローズ',
    'セーブ','セット','ゲット','プット','ラン',
    # Time/numbers
    'ミニッツ','セカンド','ファースト','ラスト','ネクスト',
    # Common expressions
    'オーケー','サンキュー','ソーリー','ハロー','グッバイ',
    'イエス','ノー','ウェルカム','プリーズ',
    # Grammar particles written in katakana
    'ナニ','ドコ','ダレ','イツ','ドウ','コレ','ソレ','アレ',
    # Everyday items
    'テレビ','ラジオ','カメラ','コンピュータ','インターネット',
    'スマホ','メール','ニュース','ビデオ','オーディオ',
    'バイク','バス','タクシー','ホテル','レストラン',
    'トイレ','シャワー','ベッド','ソファ',
    'コーヒー','ジュース','パン','ケーキ','アイスクリーム',
})

# Non-dialogue editorial markers — sound effects, music cues, audience reactions
# that should ALWAYS be deleted regardless of speech overlap.
# These are production notes for dubbing/editing, not dialogue.
NON_DIALOGUE_PATTERNS = [
    r'^\[音楽\]$',      # music
    r'^\[拍手\]$',       # applause
    r'^\[笑い\]$',       # laughter
    r'^\[歓声\]$',       # cheers
    r'^\[悲鳴\]$',       # scream
    r'^\[鳴き声\]$',     # animal cry
    r'^\[足音\]$',       # footsteps
    r'^\[効果音\]$',     # sound effect
    r'^\[鐘\]$',         # bell
    r'^\[笛\]$',         # whistle
    r'^\[雷\]$',         # thunder
    r'^\[風\]$',         # wind
    r'^\[波\]$',         # waves
    r'^\[雨\]$',         # rain
    r'^\[爆発\]$',       # explosion
    r'^\[銃声\]$',       # gunshot
    r'^\[車\]$',         # car
    r'^\[飛行機\]$',     # airplane
    r'^\[電話\]$',       # telephone
    r'^\[ベル\]$',       # bell (en)
    r'^\[チャイム\]$',   # chime
    r'^\[ノック\]$',     # knock
    r'^\[ドア\]$',       # door
    r'^\[SE\]$',         # sound effect (en)
    r'^\[BGM\]$',        # background music (en)
    r'^\[ざわめき\]$',   # murmur
    r'^\[どよめき\]$',   # stir
]
