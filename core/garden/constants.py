# 五种花的元数据，mood_keys 是该槽位收容的 mood 集合
FLOWERS = [
    {
        "slot_key": "calm",
        "id": "daisy",
        "name": "雏菊",
        "en_name": "Daisy",
        "language": "纯真，与你日常的安静相处",
        "mood_keys": ["neutral", "gentle"],
    },
    {
        "slot_key": "bright",
        "id": "sunflower",
        "name": "向日葵",
        "en_name": "Sunflower",
        "language": "她在你身边时的明亮",
        "mood_keys": ["happy", "surprised"],
    },
    {
        "slot_key": "low",
        "id": "bluebell",
        "name": "蓝铃",
        "en_name": "Bluebell",
        "language": "低落时仍想被你看见",
        "mood_keys": ["sad"],
    },
    {
        "slot_key": "yandere",
        "id": "rose",
        "name": "红玫瑰",
        "en_name": "Rose",
        "language": "占有的、不肯松手的爱意",
        "mood_keys": ["yandere", "angry"],
    },
    {
        "slot_key": "adrift",
        "id": "dandelion",
        "name": "蒲公英",
        "en_name": "Dandelion",
        "language": "心思飘远时也在生长",
        "mood_keys": ["thinking", "sleepy"],
    },
]

# 生长参数
GROWTH_PER_WATER = 10
STAGE_THRESHOLDS = [
    ("seed",    0),
    ("sprout",  100),
    ("budding", 200),
    ("bloom",   300),
]

# 自动浇水概率（每次 scheduler tick 命中冷却后 roll）
AUTO_WATER_PROBABILITY = 0.30

# harvest / vase 时长（秒）
HARVEST_EXPIRE_SECONDS = 15 * 86400   # 15 天后过期丢掉
HARVEST_HANDLE_SECONDS = 3 * 86400    # 3 天后触发处理逻辑
VASE_WILT_SECONDS = 7 * 86400         # 花瓶 7 天枯萎

# 处理逻辑分布（累积阈值）
HANDLE_ASK_THRESHOLD  = 0.30     # 0.00 ~ 0.30: 问用户
HANDLE_SELF_THRESHOLD = 0.60     # 0.30 ~ 0.60: 自己处理（干花/花瓶）
HANDLE_GIFT_THRESHOLD = 0.80     # 0.60 ~ 0.80: 送给用户
                                 # 0.80 ~ 1.00: 静默
