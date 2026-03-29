from decimal import Decimal
from typing import Sequence

from sqlalchemy import func, select

from app.db.models import Budget, Transaction, TransactionType
from app.repositories.base import BaseRepository


class BudgetRepository(BaseRepository[Budget]):
    model = Budget

    async def get_by_user(self, user_id: int) -> Sequence[Budget]:
        return await self.get_many(
            Budget.user_id == user_id, order_by=Budget.period_start.desc()
        )

    async def get_active_by_user(self, user_id: int) -> Sequence[Budget]:
        return await self.get_many(
            Budget.user_id == user_id,
            Budget.period_start <= func.current_date(),
            Budget.period_end >= func.current_date(),
            order_by=Budget.period_start.desc(),
        )

    async def get_spent(self, budget_id: int) -> tuple[Decimal, Decimal]:
        """Returns (limit_amount, spent) for a budget per DATABASE.md spec."""
        stmt = (
            select(
                Budget.limit_amount,
                func.coalesce(func.sum(Transaction.amount_base), 0),
            )
            .outerjoin(
                Transaction,
                (Transaction.user_id == Budget.user_id)
                & (Transaction.category_id == Budget.category_id)
                & (Transaction.type == TransactionType.EXPENSE)
                & (Transaction.occurred_at >= Budget.period_start)
                & (Transaction.occurred_at <= Budget.period_end)
                & (Transaction.deleted_at.is_(None)),
            )
            .where(Budget.id == budget_id)
            .group_by(Budget.limit_amount)
        )
        result = await self.session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return Decimal(0), Decimal(0)
        return row[0], row[1]
