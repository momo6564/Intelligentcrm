import os
import smtplib
from email.message import EmailMessage
from typing import Tuple
from .text_utils import clean_text

def send_email_best_effort(to_email: str, subject: str, body: str) -> Tuple[bool, str]:
    host = clean_text(os.environ.get("SMTP_HOST"))
    port_raw = clean_text(os.environ.get("SMTP_PORT")) or "587"
    username = clean_text(os.environ.get("SMTP_USER"))
    password = clean_text(os.environ.get("SMTP_PASS"))
    from_email = clean_text(os.environ.get("SMTP_FROM")) or username
    if not host or not from_email:
        return False, "SMTP not configured"

    port = int(port_raw) if port_raw.isdigit() else 587
    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls()
            if username and password:
                server.login(username, password)
            server.send_message(msg)
        return True, ""
    except Exception as exc:
        return False, str(exc)
