from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator

class Settings(BaseSettings):
    database_url: str = 'postgresql+psycopg://aho:aho@localhost:5432/aho'
    jwt_secret: str
    telegram_bot_token: str = ''
    telegram_bot_username: str = ''
    telegram_replace_webhook: bool = False
    telegram_request_receiver_id: int = 0
    aho_telegram_chat_id: str = ''
    admin_initial_email: str = 'admin@example.local'
    admin_initial_password: str = ''
    demo_password: str = ''
    registration_mode: str = 'ADMIN_APPROVAL'
    app_base_url: str = 'http://localhost:8000'
    frontend_url: str = 'http://localhost:5173'
    log_level: str = 'INFO'
    environment: str = 'development'
    cookie_secure: bool = False
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    @field_validator('aho_telegram_chat_id')
    @classmethod
    def channel_id(cls, value):
        if value and (not value.startswith('-') or not value[1:].isdigit() or int(value) >= 0):
            raise ValueError('AHO_TELEGRAM_CHAT_ID must be a negative numeric chat ID or empty')
        return str(int(value)) if value else ''

@lru_cache
def settings():
    result = Settings()
    if len(result.jwt_secret) < 32:
        raise RuntimeError('JWT_SECRET must contain at least 32 characters')
    if result.environment == 'production' and not result.cookie_secure:
        raise RuntimeError('Production requires COOKIE_SECURE=true')
    return result
