#!/usr/bin/env python3
"""Shared Japanese text utilities — common words, non-dialogue markers.

Used by: whisper_pipeline.py, build_glossary.py, noun_checker.py, auto_classify.py
"""

import re

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
    # ── auto_clean_glossary (27 katakana words) ──
    'バカバカ',  # sound effect / onomatopoeia
    'ハーイ',  # laughter / exclamation
    'ハッハー',  # laughter / exclamation
    'チャー',  # laughter / exclamation
    'ニャー',  # laughter / exclamation
    'イェーイ',  # laughter / exclamation
    'パーッ',  # sound effect / onomatopoeia
    'キュッ',  # sound effect / onomatopoeia
    'ートム',  # fragment (starts with ー)
    'ワンワンワン',  # sound effect / onomatopoeia
    'エネルギータ',  # common non-name word
    'アイスクリー',  # common non-name word
    'ネルギー',  # common non-name word
    'バンッ',  # sound effect / onomatopoeia
    'クリッ',  # sound effect / onomatopoeia
    'バーッ',  # sound effect / onomatopoeia
    'パパママ',  # common non-name word
    'プロダクショ',  # common non-name word
    'バイキン',  # common non-name word
    'ピーッ',  # sound effect / onomatopoeia
    'リボリュー',  # common non-name word
    'ポカーン',  # sound effect / onomatopoeia
    'ポンッ',  # sound effect / onomatopoeia
    'パンッ',  # sound effect / onomatopoeia
    'パパー',  # common non-name word
    'バイバーイ',  # common non-name word
    'ボカーン',  # sound effect / onomatopoeia
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
    # ── Common words ALSO in JMnedict as rare surnames/places ──
    # These are everyday vocabulary that happen to exist as names.
    # Without this override, Jamdict keeps them (in both JMdict + JMnedict).
    '世紀','手伝','完成','太陽','馬鹿','平和','大人',
    '案内','成功','自由','仲良','無事','戦争','大切','裏切',
    '空気','惑星','安心','本場','不思議','未来','場合',
    '美味','宝石','地上','兄弟','覚悟','小僧','大勢','加減',
    '片付','年前','客様','味方','綺麗','有名','我慢','最初',
    '時代','人形','出来','早速','大陸','見張','事故','勇気',
    '運命','王様','相当','可愛','茶飲',
    # ── Additional common words discovered from corpus ──
    '犯人','反対','苦労','紹介','正体','奴隷','簡単',
    '気持','用意','地球人','大統領','計画','発明','協力',
    '風船','世界一','映画','残念','全滅','電波','完全',
    '分解','植物','科学','仕掛','責任','何者','息子','恐竜',
    '貴様','円盤','本物','用意',
    # ── Common words WITH common JMdict priority tags (news1/ichi1/etc.)
    # These are everyday vocabulary that ALSO exist as rare surnames/places
    # in JMnedict, so Jamdict alone cannot filter them.
    '地下','強力','女王','調子','利用','土産','永久','権利','自然',
    '工事','結果','改造','途中','重大','悪魔','文字','文明','一方',
    '主人','空中','本日','天下','他人','火山','風呂','黄色','津波',
    '第一','努力','出会','記録','本部','下手','見物','隕石','教授',
    '大好','首飾','出来上','元通','何言','役立','金儲','博士僕',
    '一体誰','管理位','父様',
    # ── Verb stems / compound fragments not in JMdict ──
    '似合','相変','前等','万年前','一体何者','仕返','日目','後思',
    '万円','腰抜','博士私','大嫌','出来損','前達','間抜','近寄',
    '号線','見逃','目覚','人組','背負','心当','一人残','若返',
    '秒前','見失','博士何','苦労様','命令通','端微塵','人間様',
    # ── Batch: time/number fragments, verb stems, adverb fragments ──
    '時間後','大騒','前何','僕行','解体処分','今取','研究中',
    '日前','一番大','見守','声出','前一人','今行','一目見',
    '不不','今出','一番大切','手助','僕一人','勘違','横取',
    '大急','絶対許','逃走中','覧下','見上','日後','事言',
    '全部聞','分後','年目','今私','約束通','私一人','博士一体',
    '後分','年以上','時私','一度見','万年後','時間経','僕探',
    '君大丈夫','見下','救援頼','口出','宝探','一体君',
    '証拠不十','秒読','日一人','年経','一儲','本日午後',
    '後私','人一緒','本文上','大儲','一音','今夜中','一番近',
    '全然違','冗談言','人騒','一度言','絶対大丈','分待',
    '何百年','何十年','一番強','全部揃','全部俺','家行',
    '今助','時間中','日私','今忙','予定通','随分大','一度地球',
    '時間以内','何今','水博士大','交戦中',
    # ── Second pass: more verb stems, suffix fragments ──
    '水博士私','分君','着替','見捨','手遅','今君','君僕',
    '怒鳴','君君','見直','丸焼','出直','絶対反対','逆立',
    '缶切','宇宙中','日君','目立','目隠','夜遅','見過',
    '見回','仲直','程知','僕何','身動','君私','皆殺','何万人',
    # ── Third pass: remaining common compounds ──
    '億円','嬢様','原子力発','電所','水者','名付','博士博士',
    '素早','頭兄','人間並','科学文明','水合','月様','期待人間',
    '準備完了','見覚','家創','水博士今','出場権利','歯向',
    '者名','銀行対応','見破','打差','威張','馬鹿馬鹿','関車',
    '第六','陰力','博士君',
    # ── L3.2 AI词库审查: 脚本漏网的常见词 ──
    '競技大会','腎臓細胞','通信機','瓜二','中毒患者',
    '女王様','慶応生','耳飾','出迎',
    # ── auto_clean_glossary (1 kanji words) ──
    '計画通',  # verb stem: ends with "通"
})

# Non-word patterns: dashes, repeated chars, breathing/filler sounds.
# Shared between auto_classify.py and build_glossary.py.
NON_WORD_RE = re.compile(
    r'^[-ー―]{2,}$|'           # long dashes
    r'^(.)\1{2,}$|'             # same char repeated 3+ times
    r'^(ハァ|フー|ウー|アー|エー|オー|へへ|ふふ|わあ|ああ|ええ|おお)+$'
)

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
