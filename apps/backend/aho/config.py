from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = 'postgresql+psycopg://aho:aho@localhost:5432/aho'
    jwt_secret: str
    telegram_bot_token: str = ''
    telegram_bot_username: str = ''
    telegram_replace_webhook: bool = False
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

@lru_cache
def settings():
    result = Settings()
    if len(result.jwt_secret) < 32:
        raise RuntimeError('JWT_SECRET must contain at least 32 characters')
    if result.environment == 'production' and not result.cookie_secure:
        raise RuntimeError('Production requires COOKIE_SECURE=true')
    return result
