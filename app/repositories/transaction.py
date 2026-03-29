from datetime import datetime
from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy import func, select

from app.db.models import Transaction, TransactionType
from app.repositories.base import BaseRepository


class TransactionRepository(BaseRepository[Transaction]):
    model = Transaction

    def _active(self) -> Any:
        return Transaction.deleted_at.is_(None)

    async def get_by_user(
        self,
        user_id: int,
        *,
        transaction_type: TransactionType | None = None,
        account_id: int | None = None,
        category_id: int | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Transaction]:
        filters: list[Any] = [
            Transaction.user_id == user_id,
            self._active(),
        ]
        if transaction_type is not None:
            filters.append(Transaction.type == transaction_type)
        if account_id is not None:
            filters.append(Transaction.account_id == account_id)
        if category_id is not None:
            filters.append(Transaction.category_id == category_id)
        if date_from is not None:
            filters.append(Transaction.occurred_at >= date_from)
        if date_to is not None:
            filters.append(Transaction.occurred_at <= date_to)
        return await self.get_many(
            *filters,
            order_by=Transaction.occurred_at.desc(),
            limit=limit,
            offset=offset,
        )

    async def soft_delete(self, transaction_id: int) -> Transaction | None:
        return await self.update_by_id(
            transaction_id, deleted_at=func.now()
        )

    async def get_total_by_type(
        self,
        user_id: int,
        transaction_type: TransactionType,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> Decimal:
        stmt = select(func.coalesce(func.sum(Transaction.amount_base), 0)).where(
            Transaction.user_id == user_id,
            Transaction.type == transaction_type,
            self._active(),
        )
        if date_from is not None:
            stmt = stmt.where(Transaction.occurred_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(Transaction.occurred_at <= date_to)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def get_expenses_by_category(
        self,
        user_id: int,
        date_from: datetime,
        date_to: datetime,
    ) -> Sequence[tuple[int | None, Decimal]]:
        """Returns list of (category_id, total_amount_base) for expenses."""
        stmt = (
            select(
                Transaction.category_id,
                func.coalesce(func.sum(Transaction.amount_base), 0),
            )
            .where(
                Transaction.user_id == user_id,
                Transaction.type == TransactionType.EXPENSE,
                Transaction.occurred_at >= date_from,
                Transaction.occurred_at <= date_to,
                self._active(),
            )
            .group_by(Transaction.category_id)
        )
        result = await self.session.execute(stmt)
        return result.all()  # type: ignore[return-value]
