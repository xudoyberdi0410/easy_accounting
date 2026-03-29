from datetime import datetime
from decimal import Decimal
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RecurringTransaction, TransactionSource, TransactionType
from app.repositories.recurring_transaction import RecurringTransactionRepository
from app.services.base import BaseService
from app.services.errors import NotFoundError, OwnershipError
from app.services.transaction import TransactionService


class RecurringTransactionService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.repo = RecurringTransactionRepository(session)
        self.transaction_svc = TransactionService(session)

    async def create(
        self,
        user_id: int,
        account_id: int,
        amount: Decimal,
        currency: str,
        transaction_type: TransactionType,
        cron_expr: str,
        next_run_at: datetime,
        category_id: int | None = None,
        note: str | None = None,
    ) -> RecurringTransaction:
        recurring = await self.repo.create(
            user_id=user_id,
            account_id=account_id,
            category_id=category_id,
            amount=amount,
            currency=currency,
            type=transaction_type,
            note=note,
            cron_expr=cron_expr,
            next_run_at=next_run_at,
        )
        await self.commit()
        return recurring

    async def get_by_id(
        self, recurring_id: int, user_id: int
    ) -> RecurringTransaction:
        recurring = await self.repo.get_by_id(recurring_id)
        if recurring is None:
            raise NotFoundError("RecurringTransaction", recurring_id)
        if recurring.user_id != user_id:
            raise OwnershipError
        return recurring

    async def list_by_user(self, user_id: int) -> Sequence[RecurringTransaction]:
        return await self.repo.get_by_user(user_id)

    async def deactivate(
        self, recurring_id: int, user_id: int
    ) -> RecurringTransaction:
        await self.get_by_id(recurring_id, user_id)
        recurring = await self.repo.deactivate(recurring_id)
        await self.commit()
        return recurring  # type: ignore[return-value]

    async def execute_due(
        self,
        compute_next_run: "callable",  # type: ignore[valid-type]
        user_default_currencies: dict[int, str],
    ) -> int:
        """Execute all due recurring transactions.

        Args:
            compute_next_run: callable(cron_expr: str, after: datetime) -> datetime
            user_default_currencies: mapping of user_id -> default_currency

        Returns:
            Number of transactions created.
        """
        due = await self.repo.get_due()
        count = 0
        for rec in due:
            default_currency = user_default_currencies.get(
                rec.user_id, rec.currency
            )
            try:
                await self.transaction_svc.add(
                    user_id=rec.user_id,
                    account_id=rec.account_id,
                    amount=rec.amount,
                    currency=rec.currency,
                    transaction_type=rec.type,
                    user_default_currency=default_currency,
                    category_id=rec.category_id,
                    note=rec.note,
                    source=TransactionSource.MANUAL,
                )
                new_next = compute_next_run(rec.cron_expr, rec.next_run_at)
                await self.repo.update_by_id(rec.id, next_run_at=new_next)
                await self.commit()
                count += 1
            except Exception:
                await self.rollback()
        return count

    async def delete(self, recurring_id: int, user_id: int) -> None:
        await self.get_by_id(recurring_id, user_id)
        await self.repo.delete_by_id(recurring_id)
        await self.commit()
