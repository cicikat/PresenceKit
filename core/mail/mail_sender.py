"""Send plain-text character letters as multipart email."""

from __future__ import annotations

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from html import escape
import logging

logger = logging.getLogger(__name__)


async def _open_proxy_socket(proxy_url: str, host: str, port: int):
    try:
        from python_socks.async_.asyncio import Proxy
    except ImportError as exc:
        raise RuntimeError(
            "SMTP proxy requires python-socks. Run: "
            "python -m pip install 'python-socks[asyncio]'"
        ) from exc

    return await Proxy.from_url(proxy_url).connect(
        dest_host=host,
        dest_port=port,
        timeout=30,
    )


async def send_letter(subject: str, body_text: str) -> bool:
    """Send one letter. Returns True only after SMTP accepts the message."""
    from core.config_loader import get_config

    cfg = get_config().get("mail", {})
    if not cfg.get("enabled", False):
        logger.info("[mail] disabled, skipping send")
        return False

    required = ("smtp_host", "smtp_user", "smtp_password", "to_addr")
    missing = [key for key in required if not str(cfg.get(key) or "").strip()]
    if missing:
        logger.error("[mail] missing required config: %s", ", ".join(missing))
        return False

    try:
        import aiosmtplib
    except ImportError:
        logger.error(
            "[mail] aiosmtplib not installed. Run: python -m pip install aiosmtplib"
        )
        return False

    smtp_user = str(cfg["smtp_user"]).strip()
    smtp_host = str(cfg["smtp_host"]).strip()
    smtp_port = int(cfg.get("smtp_port", 587))
    from_addr = str(cfg.get("from_addr") or smtp_user).strip()
    from_name = str(cfg.get("from_name") or "角色").strip()
    to_addr = str(cfg["to_addr"]).strip()
    prefix = str(cfg.get("subject_prefix") or "")
    proxy_url = str(cfg.get("proxy_url") or "").strip()
    use_tls = bool(cfg.get("smtp_use_tls", smtp_port == 465))
    start_tls = bool(cfg.get("smtp_start_tls", not use_tls))

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{prefix}{subject}"
    msg["From"] = formataddr((from_name, from_addr))
    msg["To"] = to_addr
    msg.attach(MIMEText(body_text, "plain", "utf-8"))

    html_body = "".join(
        f"<p>{escape(line)}</p>" if line.strip() else "<br/>"
        for line in body_text.splitlines()
    )
    html = (
        '<html><body style="font-family:serif;font-size:16px;line-height:1.8;'
        'max-width:600px;margin:40px auto;color:#333;">'
        f"{html_body}</body></html>"
    )
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        proxy_sock = (
            await _open_proxy_socket(proxy_url, smtp_host, smtp_port)
            if proxy_url
            else None
        )
        await aiosmtplib.send(
            msg,
            hostname=smtp_host,
            port=None if proxy_sock else smtp_port,
            username=smtp_user,
            password=str(cfg["smtp_password"]),
            use_tls=use_tls,
            start_tls=start_tls,
            sock=proxy_sock,
        )
        logger.info("[mail] sent subject=%r to=%s", subject, to_addr)
        return True
    except Exception as exc:
        if proxy_url:
            logger.error(
                "[mail] send failed via configured proxy: %s. "
                "The proxy node may block SMTP port %s.",
                exc,
                smtp_port,
            )
        else:
            logger.error("[mail] send failed: %s", exc)
        return False
