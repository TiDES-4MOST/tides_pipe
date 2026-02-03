import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _get_webhook_url() -> Optional[str]:
    """
    Resolve Slack Incoming Webhook URL from env.
    Prefer SLACK_WEBHOOK_URL, fallback to TIDES_SLACK_WEBHOOK_URL.
    """
    return (
        os.getenv("SLACK_WEBHOOK_URL")
        or os.getenv("TIDES_SLACK_WEBHOOK_URL")
        or None
    )


def _get_bot_token() -> Optional[str]:
    return os.getenv("SLACK_BOT_TOKEN") or os.getenv("TIDES_SLACK_BOT_TOKEN")


def _get_channel() -> Optional[str]:
    return os.getenv("SLACK_CHANNEL") or os.getenv("TIDES_SLACK_CHANNEL")


def send_slack_message(
    text: str,
    webhook_url: Optional[str] = None,
    *,
    channel: Optional[str] = None,
    token: Optional[str] = None,
) -> bool:
    """
    Send a Slack message using the official Slack SDK.

    Preference order:
      1) Incoming Webhook via slack_sdk.webhook.WebhookClient
      2) Bot token + channel via slack_sdk.WebClient.chat_postMessage

    Returns True on success, False otherwise. If no configuration,
    logs at DEBUG and returns False (no-op).
    """
    # 1) Incoming Webhook path
    url = webhook_url or _get_webhook_url()
    if url:
        try:
            from slack_sdk.webhook import WebhookClient

            wh = WebhookClient(url)
            resp = wh.send(text=text)
            if getattr(resp, "status_code", 500) in (200, 201):
                return True
            logger.warning("Slack webhook error %s: %s", getattr(resp, "status_code", None), getattr(resp, "body", None))
            return False
        except ImportError:
            logger.warning("slack_sdk not installed; cannot send Slack webhook message.")
            return False
        except Exception as e:
            logger.exception("Failed to send Slack webhook message: %s", e)
            return False

    # 2) Bot token + channel path
    tok = token or _get_bot_token()
    chan = channel or _get_channel()
    if tok and chan:
        try:
            from slack_sdk import WebClient
            from slack_sdk.errors import SlackApiError

            client = WebClient(token=tok)
            res = client.chat_postMessage(channel=chan, text=text)
            ok = getattr(res, "data", {}).get("ok", False)
            return bool(ok)
        except ImportError:
            logger.warning("slack_sdk not installed; cannot send Slack API message.")
            return False
        except SlackApiError as e:
            logger.warning("Slack API error: %s", getattr(e.response, "data", {}))
            return False
        except Exception as e:
            logger.exception("Failed to send Slack API message: %s", e)
            return False

    logger.debug("Slack not configured; skipping message: %s", text)
    return False
