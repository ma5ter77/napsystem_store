"""E-mail delivery (NAP-007).

Backends: `log` (demo default: the message goes to the application log), `file` (each message is written as an .eml
file — handy for a test stand without a mail server), `smtp` (any SMTP provider: STARTTLS on 587, TLS on 465).
Notifications are rendered and localized before they reach this module; it only transports them.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from html import escape

from ..config import get_settings

log = logging.getLogger("nap.email")


class EmailError(Exception):
    """Temporary or permanent delivery failure; the outbox retries with backoff."""


def build_message(to: str, subject: str, body: str, locale: str, ref: str) -> EmailMessage:
    s = get_settings()
    m = EmailMessage()
    m["From"] = s.email_from
    m["To"] = to
    m["Subject"] = subject
    m["Date"] = formatdate(localtime=False)
    m["Message-ID"] = make_msgid(domain=(s.email_from.rsplit("@", 1)[-1].strip("> ") or "nap.local"))
    m["X-NAP-Ref"] = ref  # notification dedupe key: support can find the message
    if s.email_reply_to:
        m["Reply-To"] = s.email_reply_to
    footer = {"ru": "Это служебное письмо NAP Store. Отвечать на него не нужно.",
              "en": "This is a service message from NAP Store. No reply is needed."}.get(locale, "")
    site = s.public_base_url.rstrip("/")
    m.set_content(f"{body}\n\n—\n{footer}\n{site}/{locale}/account?tab=notifications\n")
    paras = "".join(f"<p>{escape(p)}</p>" for p in body.split("\n") if p.strip())
    m.add_alternative(
        f"<!doctype html><html lang=\"{locale}\"><body style=\"font-family:system-ui,sans-serif;color:#1c2a35;"
        f"max-width:560px;margin:0 auto;padding:24px\"><h2 style=\"font-size:18px\">{escape(subject)}</h2>{paras}"
        f"<hr style=\"border:0;border-top:1px solid #d3dbdc\"><p style=\"color:#5d6c76;font-size:13px\">"
        f"{escape(footer)}<br><a href=\"{escape(site)}/{locale}/account?tab=notifications\">{escape(site)}</a></p>"
        f"</body></html>", subtype="html")
    m["Content-Language"] = locale  # after the parts: Content-* headers set earlier move into the first part
    return m


def send(msg: EmailMessage) -> str:
    """Deliver one message; returns the backend name. Raises EmailError on failure."""
    s = get_settings()
    backend = s.email_backend
    if backend == "log":
        log.info("email (log backend) to=%s subject=%s", msg["To"], msg["Subject"])
        return "log"
    if backend == "file":
        d = s.email_file_dir
        os.makedirs(d, exist_ok=True)
        name = msg["Message-ID"].strip("<>").replace("@", "_").replace("/", "_") + ".eml"
        with open(os.path.join(d, name), "wb") as fh:
            fh.write(bytes(msg))
        return "file"
    if backend == "smtp":
        if not s.smtp_host:
            raise EmailError("smtp_not_configured")
        try:
            if s.smtp_security == "ssl":
                conn = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20, context=ssl.create_default_context())
            else:
                conn = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20)
            with conn:
                conn.ehlo()
                if s.smtp_security == "starttls":
                    conn.starttls(context=ssl.create_default_context())
                    conn.ehlo()
                if s.smtp_user:
                    conn.login(s.smtp_user, s.smtp_password or "")
                refused = conn.send_message(msg)
            if refused:
                raise EmailError(f"recipient_refused:{sorted(refused)}")
        except (smtplib.SMTPException, OSError) as e:
            raise EmailError(f"{type(e).__name__}: {e}") from e
        return "smtp"
    raise EmailError(f"unknown_backend:{backend}")
