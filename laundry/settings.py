import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def settings(overrides=None):
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    public = json.loads((ROOT / "config.json").read_text())
    result = {
        "DATABASE": os.environ.get("LAUNDRY_DB", str(ROOT / "instance" / "laundry.sqlite3")),
        "BACKUP_DIR": os.environ.get("LAUNDRY_BACKUP_DIR", str(ROOT.parent / "laundry-backups" / "daily")),
        "HEALTH_URL": os.environ.get("LAUNDRY_HEALTH_URL", "https://sjtulaundry.xyz/api/health"),
        "BUILDINGS": public["buildings"],
        "RESERVATIONS": public.get("reservations", {}),
        "WX_APPID": os.environ.get("WX_APPID", public["wxAppId"]),
        "WX_SECRET": os.environ.get("WX_SECRET", ""),
        "WX_TEMPLATE_ID": os.environ.get("WX_TEMPLATE_ID", public["wxTemplateId"]),
        "WX_MINIPROGRAM_STATE": os.environ.get("WX_MINIPROGRAM_STATE", "formal"),
    }
    result.update(overrides or {})
    if result["WX_MINIPROGRAM_STATE"] not in ("developer", "trial", "formal"):
        raise ValueError("WX_MINIPROGRAM_STATE must be developer, trial or formal")
    return result


def public_config(config):
    return {"buildings": config["BUILDINGS"], "templateId": config["WX_TEMPLATE_ID"],
            "appId": config["WX_APPID"], "minMinutes": 1, "maxMinutes": 30}
