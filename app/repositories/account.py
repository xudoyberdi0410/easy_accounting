from decimal import Decimal
from typing import Sequence

from sqlalchemy import select, update

from app.db.models import Account, AccountType
from app.repositories.base import BaseRepository


class AccountRepository(BaseRepository[Account]):
    model = Account

    async def get_by_user(
        self, user_id: int, include_archived: bool = False
    ) -> Sequence[Account]:
        filters = [Account.user_id == user_id]
        if not include_archived:
            filters.append(Account.is_archive == False)  # noqa: E712
        return await self.get_many(*filters, order_by=Account.id)

    async def get_by_user_and_type(
        self, user_id: int, account_type: AccountType
    ) -> Sequence[Account]:
        return await self.get_many(
            Account.user_id == user_id,
            Account.type == account_type,
            Account.is_archive == False,  # noqa: E712
            order_by=Account.id,
        )

    async def archive(self, account_id: int) -> Account | None:
        return await self.update_by_id(account_id, is_archive=True)

    async def update_balance(self, account_id: int, delta: Decimal) -> None:
        stmt = (
            update(Account)
            .where(Account.id == account_id)
            .values(balance=Account.balance + delta)
        )
        await self.session.execute(stmt)
