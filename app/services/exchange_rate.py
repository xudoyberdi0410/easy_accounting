from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ExchangeRate
from app.repositories.exchange_rate import ExchangeRateRepository
from app.services.base import BaseService


class ExchangeRateService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.repo = ExchangeRateRepository(session)

    async def get_rate(
        self, base_currency: str, quote_currency: str
    ) -> Decimal | None:
        if base_currency == quote_currency:
            return Decimal(1)
        return await self.repo.get_latest_rate(base_currency, quote_currency)

    async def save_rate(
        self, base_currency: str, quote_currency: str, rate: Decimal
    ) -> ExchangeRate:
        exchange_rate = await self.repo.add_rate(
            base_currency, quote_currency, rate
        )
        await self.commit()
        return exchange_rate

    async def save_rates_batch(
        self, rates: list[tuple[str, str, Decimal]]
    ) -> int:
        """Save multiple rates at once. Each tuple: (base, quote, rate)."""
        for base, quote, rate in rates:
            await self.repo.add_rate(base, quote, rate)
        await self.commit()
        return len(rates)

    async def convert(
        self, amount: Decimal, from_currency: str, to_currency: str
    ) -> Decimal | None:
        rate = await self.get_rate(from_currency, to_currency)
        if rate is None:
            return None
        return (amount * rate).quantize(Decimal("0.01"))
