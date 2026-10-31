from pathlib import Path
from typing import Literal
from pydantic import Field
from .smarthome import IoBrokerConfig
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings, IoBrokerConfig):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    speech_enabled: bool = True
    public_origin: str = ''
    api_token: str = ''
    data_dir: Path = Path('data')
    poll_seconds: int = Field(default=300, ge=10)
    worker_enabled: bool = True
    allow_external_llm: bool = False
    chat_llm_enabled: bool | None = None
    llm_provider: Literal['openai','gemini'] = 'openai'
    gemini_paid_confirmed: bool = False
    llm_api_key: str = ''
    llm_model: str = ''
    imap_host: str = ''
    imap_port: int = 993
    imap_user: str = ''
    imap_password: str = ''
    imap_folder: str = 'INBOX'
    imap_archive_enabled: bool = True
    imap_archive_all_folders: bool = True
    imap_lookback_days: int = Field(default=30, ge=1)
    paperless_url: str = ''
    paperless_token: str = ''
    ntfy_url: str = ''
    ntfy_token: str = ''
    ntfy_history_urls: str = ''
    ntfy_history_enabled: bool = True
    ntfy_mode: Literal['off','important','digest'] = 'important'
    ntfy_interval_seconds: int = Field(default=3600,ge=60,le=86400)
    messenger_secret: str = ''
