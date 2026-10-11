"""Only explicit self-expression supplies a short-lived signal; no extra model."""
import re
from core.narrative_parser import parse_narrative_segments

WORDS = {"happy": r"开心|高兴|笑", "sad": r"难过|伤心|叹气", "angry": r"生气|愤怒",
         "surprised": r"惊讶|怔住", "gentle": r"温柔|害羞|脸红",
         "sleepy": r"困倦|困了|想睡", "thinking": r"沉思|思考",
         "neutral": r"恢复平静|平静下来"}


def explicit_expression(reply: str) -> str:
    result = ""
    for segment in parse_narrative_segments(reply)["segments"]:
        if segment.get("type") not in ("feel", "do"):
            continue
        text = str(segment.get("text", ""))
        # Quoted, negated and second/third-person feelings provide no self signal.
        if re.search(r'[“”"「」『』]|你|用户|他说|她说|不|没|并非|别', text):
            continue
        for emotion, pattern in WORDS.items():
            if re.search(pattern, text):
                result = emotion
                break
    return result
