from typing import Sequence

from sqlalchemy import func, select

from app.db.models import RecurringTransaction
from app.repositories.base import BaseRepository


class RecurringTransactionRepository(BaseRepository[RecurringTransaction]):
    model = RecurringTransaction

    async def get_by_user(self, user_id: int) -> Sequence[RecurringTransaction]:
        return await self.get_many(
            RecurringTransaction.user_id == user_id,
            order_by=RecurringTransaction.next_run_at,
        )

    async def get_due(self) -> Sequence[RecurringTransaction]:
        """Get all active recurring transactions that are due for execution."""
        stmt = (
            select(RecurringTransaction)
            .where(
                RecurringTransaction.is_active == True,  # noqa: E712
                RecurringTransaction.next_run_at <= func.now(),
            )
            .order_by(RecurringTransaction.next_run_at)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def deactivate(self, recurring_id: int) -> RecurringTransaction | None:
        return await self.update_by_id(recurring_id, is_active=False)
