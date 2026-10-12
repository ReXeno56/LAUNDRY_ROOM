"""Run with: python -m laundry.worker (separate from the WSGI server)."""
import argparse
import logging
import time

from .settings import settings
from .storage import Store
from .wechat import DeliveryError, WeChat

log = logging.getLogger(__name__)


def dispatch_once(store, sender, now=None):
    store.touch_worker(now)
    task = store.claim_due(now)
    if task is None:
        return False
    try:
        sender.send(task)
    except DeliveryError as exc:
        status = store.finish_delivery(task, exc.category, exc.reason, now)
        log.warning("Reminder %s: %s (%s)", task["id"], status, exc.reason)
    except Exception:
        # The process is still alive, but delivery may already have happened.
        store.finish_delivery(task, "uncertain", "unexpected_sender_error", now)
        log.exception("Reminder %s has an uncertain delivery result", task["id"])
    else:
        store.finish_delivery(task, "sent", now=now)
        log.info("Reminder %s sent", task["id"])
    return True


def main():
    parser = argparse.ArgumentParser(description="微信洗衣提醒派发进程")
    parser.add_argument("--once", action="store_true", help="处理当前到期任务后退出")
    parser.add_argument("--import-legacy", metavar="JSON", help="导入旧版提醒 JSON 后退出（可重复执行）")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    config = settings()
    store = Store(config["DATABASE"])
    if args.import_legacy:
        print(f"Imported {store.import_legacy(args.import_legacy, config['WX_TEMPLATE_ID'])} reminders")
        return
    sender = WeChat(config)
    try:
        while True:
            if dispatch_once(store, sender):
                continue
            if args.once:
                return
            time.sleep(5)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
