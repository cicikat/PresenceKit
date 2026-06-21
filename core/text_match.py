import re

# 中文虚词 / 高频无信息 2-gram，可据 trace 增删
STOP_GRAMS = {
    "什么", "这么", "那么", "怎么", "这样", "那样", "这个", "那个",
    "觉得", "可以", "不是", "就是", "但是", "然后", "的话", "一下",
    "知道", "没有", "我们", "你们", "他们", "自己", "现在", "时候",
    "自己的", "己的", "这种", "那种", "这件", "那件",
}

_SEG = re.compile(r"[^0-9A-Za-z一-鿿]+")  # 非 中/英/数 即为切段边界

def ngram_tokens(text: str, lengths=(2, 3, 4), *, stopwords: set | None = None) -> set[str]:
    """中文友好 n-gram：按标点切段（不跨段），过滤停用 gram。"""
    out: set[str] = set()
    stop = (stopwords or set()) | STOP_GRAMS
    for seg in _SEG.split(text or ""):
        if not seg:
            continue
        for n in lengths:
            for i in range(len(seg) - n + 1):
                g = seg[i:i+n]
                if g and g not in stop:
                    out.add(g)
    return out
