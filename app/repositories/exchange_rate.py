from decimal import Decimal

from sqlalchemy import select

from app.db.models import ExchangeRate
from app.repositories.base import BaseRepository


class ExchangeRateRepository(BaseRepository[ExchangeRate]):
    model = ExchangeRate

    async def get_latest_rate(
        self, base_currency: str, quote_currency: str
    ) -> Decimal | None:
        stmt = (
            select(ExchangeRate.rate)
            .where(
                ExchangeRate.base_currency == base_currency,
                ExchangeRate.quote_currency == quote_currency,
            )
            .order_by(ExchangeRate.fetched_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add_rate(
        self, base_currency: str, quote_currency: str, rate: Decimal
    ) -> ExchangeRate:
        return await self.create(
            base_currency=base_currency,
            quote_currency=quote_currency,
            rate=rate,
        )
