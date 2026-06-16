"""
Phase 1: EDGAR RSS polling + Form 4 parsing + cluster detection.
This is currently a HEARTBEAT STUB. It sends a Telegram message to confirm
the pipeline is connected. Replace the main() body with real EDGAR logic in Phase 2.
"""
from datetime import datetime, timezone
from .telegram_alerts import send_message


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    send_message(
        f"<b>✅ EDGAR Poll Job — Heartbeat</b>\n"
        f"Pipeline is running.\n"
        f"Time: {now}\n"
        f"Next step: replace this stub with EDGAR RSS parsing."
    )


if __name__ == "__main__":
    main()
