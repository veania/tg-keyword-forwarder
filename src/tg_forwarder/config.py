from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LogCfg(BaseModel):
    level: str = "INFO"


class MatcherCfg(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    regex: str | None = None


class TargetCfg(BaseModel):
    chat: str


class SenderCfg(BaseModel):
    session: str


class AccountCfg(BaseModel):
    name: str
    session: str
    sources: list[str]


class YamlConfig(BaseModel):
    send_via: str
    log: LogCfg = LogCfg()
    matcher: MatcherCfg = MatcherCfg()
    target: TargetCfg
    accounts: list[AccountCfg] = Field(default_factory=list)
    

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    tg_api_id: int = Field(alias="TG_API_ID")
    tg_api_hash: str = Field(alias="TG_API_HASH")

    config_path: str = Field(default="config.yaml", alias="CONFIG_PATH")
    log_level: str | None = Field(default=None, alias="LOG_LEVEL")

    def load_yaml(self) -> YamlConfig:
        data: Any = yaml.safe_load(Path(self.config_path).read_text(encoding="utf-8"))
        return YamlConfig.model_validate(data)

    def effective_log_level(self, yaml_cfg: YamlConfig) -> str:
        # env override > yaml
        return (self.log_level or yaml_cfg.log.level or "INFO").upper()
