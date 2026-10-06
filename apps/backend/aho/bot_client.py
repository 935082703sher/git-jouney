"""Telegram HTTP session honoring the environment's HTTPS proxy and trusted CA.

The pinned aiogram session does not enable aiohttp trust_env by default. Cloud
egress requires the supplied proxy; ssl.create_default_context also honors the
SSL_CERT_FILE provided by infrastructure/compose.cloud.yml.
"""
import ssl
from aiohttp import ClientSession, TCPConnector
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession


class EnvironmentSession(AiohttpSession):
    async def create_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession(
                connector=TCPConnector(ssl=ssl.create_default_context(), limit=100),
                trust_env=True,
            )
        return self._session


def create_bot(token: str) -> Bot:
    return Bot(token, session=EnvironmentSession())
