#!/usr/bin/env python3
"""Shared Japanese text utilities — common words, non-dialogue markers.

Used by: whisper_pipeline.py, build_glossary.py, noun_checker.py, auto_classify.py
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

# Common kanji compounds — expanded from build_glossary.py's _COMMON_KANJI.
# These are frequent vocabulary words that should NEVER appear in a proper-noun
# glossary.  Used as fallback when Jamdict is unavailable.
COMMON_KANJI = frozenset({
    # Original 35 entries from build_glossary.py
    '今日','明日','昨日','今年','来年','毎日','一度','一番',
    '自分','相手','人間','世界','地球','宇宙','時間','場所',
    '電話','手紙','約束','説明','質問','返事','関係','意味',
    '本当','大体','全部','半分','一緒','大丈夫','可能性',
    '人数','方向','速度','温度','距離','重量','電力',
    '攻撃','防御','破壊','発見','開発','製造','修理',
    '到着','出発','通過','移動','停止','開始','終了',
    # ── Expanded: common adverbs/adjectives/nouns polluting glossary ──
    '大変','一体','心配','絶対','素晴','仕事','邪魔',
    '間違','秘密','友達','頑張','元気','危険','仕方',
    '失礼','無駄','勝手','無理','立派','大事','以上',
    '連絡','必要','面白','様子','理由','方法','最後',
    '名前','言葉','問題','実験','研究','命令','事件',
    '部屋','仲間','機械','動物','怪物','子供','爆発',
    '爆弾','宇宙人','科学者','警察',
    # ── Temporal / quantitative ──
    '今度','一体何','一人','二人','機会','世界中',
    # ── Titles & suffixes (not proper nouns by themselves) ──
    '博士','先生','警部','殿下','総統','団長','伯爵',
    '署長','所長','船長','部長','社長',
    # ── Fragments often from Whisper splitting ──
    '飲茶','御茶',
})

# Honorific / title suffix patterns shared between noun_checker and auto_classify.
# Compiled form for matching; also available as a raw tuple for iteration.
_HONORIFIC_LIST = (
    'さん','くん','ちゃん','様','殿',
    '博士','警部','殿下','先生','総統','団長','伯爵',
    '署長','所長','船長','部長','社長',
)

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
