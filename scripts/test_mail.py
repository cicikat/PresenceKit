"""手动测试邮件发送。用法：python scripts/test_mail.py"""
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from core.mail.mail_sender import send_letter


async def main():
    ok = await send_letter(
        subject="测试来信",
        body_text="这是一封测试信。\n\n如果你收到了，说明邮件链路正常。\n\n——叶瑄",
    )
    print("发送结果:", "成功" if ok else "失败（看上方日志）")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
