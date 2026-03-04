"""CLI Configuration — ~/.ofkms/config.json management"""
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class CLIConfig:
    api_url: str = "http://localhost:8000"
    api_key: Optional[str] = None
    default_language: Optional[str] = None
    default_product: Optional[str] = None
    stream: bool = True


# kebab-case key → dataclass field name
_KEY_MAP = {
    "api-url": "api_url",
    "api-key": "api_key",
    "default-language": "default_language",
    "default-product": "default_product",
    "stream": "stream",
}

VALID_KEYS = set(_KEY_MAP.keys())


class ConfigManager:
    CONFIG_DIR = Path.home() / ".ofkms"
    CONFIG_FILE = CONFIG_DIR / "config.json"

    @classmethod
    def load(cls) -> CLIConfig:
        if cls.CONFIG_FILE.exists():
            data = json.loads(cls.CONFIG_FILE.read_text(encoding="utf-8"))
            return CLIConfig(**{k: v for k, v in data.items() if k in CLIConfig.__dataclass_fields__})
        return CLIConfig()

    @classmethod
    def save(cls, config: CLIConfig) -> None:
        cls.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in asdict(config).items() if v is not None or k in ("api_key", "default_language", "default_product")}
        cls.CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def get(cls, key: str) -> Optional[str]:
        cfg = cls.load()
        field_name = _KEY_MAP.get(key, key)
        return str(getattr(cfg, field_name, None))

    @classmethod
    def set(cls, key: str, value: str) -> None:
        cfg = cls.load()
        field_name = _KEY_MAP.get(key, key)
        if field_name == "stream":
            setattr(cfg, field_name, value.lower() in ("true", "1", "yes"))
        else:
            setattr(cfg, field_name, value)
        cls.save(cfg)

    @classmethod
    def reset(cls) -> None:
        if cls.CONFIG_FILE.exists():
            cls.CONFIG_FILE.unlink()
