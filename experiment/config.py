\
from pathlib import Path
import json
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

def load_config():
    with (ROOT / "config.yaml").open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    from .alert_settings import effective_alerts
    cfg.setdefault("experiment", {})["alerts"] = effective_alerts(cfg, root=ROOT)
    return cfg

def load_questions():
    with (ROOT / "questions.json").open("r", encoding="utf-8") as f:
        return json.load(f)

def resolve_path(path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else ROOT / p
