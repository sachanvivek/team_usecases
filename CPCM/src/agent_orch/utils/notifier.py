import os
import smtplib
import requests
import configparser
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Read config.ini
config = configparser.ConfigParser()
config.read('src/agent_orch/utils/config.ini')
#config.read('src\agent_orch\utils\config.ini')

SLACK_WEBHOOK_URL = config.get("slack", "webhook_url", fallback=None)

SMTP_SERVER = config.get("email", "smtp_server", fallback="smtp.gmail.com")
SMTP_PORT = config.getint("email", "smtp_port", fallback=587)
SMTP_USER = config.get("email", "smtp_user", fallback=None)
SMTP_PASS = config.get("email", "smtp_pass", fallback=None)
EMAIL_TO = config.get("email", "email_to", fallback=None)

def send_notification(title: str, message: str, level: str = "info"):
    """
    Sends a notification to Slack and Email using config.ini settings.
    """

    # === Slack Notification ===
    if SLACK_WEBHOOK_URL:
        try:
            payload = {
                "text": f"*{title}* ({level.upper()})\n```{message}```"
            }
            resp = requests.post(SLACK_WEBHOOK_URL, json=payload)
            resp.raise_for_status()
            print(f"Slack notification sent: {title}")
        except Exception as e:
            print(f"Slack notification failed: {e}")

    # === Email Notification ===
    if SMTP_USER and SMTP_PASS and EMAIL_TO:
        try:
            msg = MIMEMultipart()
            msg["From"] = SMTP_USER
            msg["To"] = EMAIL_TO
            msg["Subject"] = f"{title} - {level.upper()}"
            msg.attach(MIMEText(message, "plain"))

            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_USER, EMAIL_TO, msg.as_string())

            print(f"Email notification sent: {title}")
        except Exception as e:
            print(f"Email notification failed: {e}")
