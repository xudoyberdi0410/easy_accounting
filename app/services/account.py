from decimal import Decimal
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, AccountType
from app.repositories.account import AccountRepository
from app.services.base import BaseService
from app.services.errors import InactiveAccountError, NotFoundError, OwnershipError


class AccountService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.repo = AccountRepository(session)

    async def create(
        self,
        user_id: int,
        name: str,
        currency: str,
        account_type: AccountType = AccountType.CASH,
        balance: Decimal = Decimal(0),
    ) -> Account:
        account = await self.repo.create(
            user_id=user_id,
            name=name,
            currency=currency,
            type=account_type,
            balance=balance,
        )
        await self.commit()
        return account

    async def get_by_id(self, account_id: int, user_id: int) -> Account:
        account = await self.repo.get_by_id(account_id)
        if account is None:
            raise NotFoundError("Account", account_id)
        if account.user_id != user_id:
            raise OwnershipError
        return account

    async def list_by_user(
        self, user_id: int, include_archived: bool = False
    ) -> Sequence[Account]:
        return await self.repo.get_by_user(user_id, include_archived=include_archived)

    async def update(
        self,
        account_id: int,
        user_id: int,
        *,
        name: str | None = None,
        account_type: AccountType | None = None,
    ) -> Account:
        await self.get_by_id(account_id, user_id)
        kwargs = {}
        if name is not None:
            kwargs["name"] = name
        if account_type is not None:
            kwargs["type"] = account_type
        if not kwargs:
            return await self.get_by_id(account_id, user_id)
        account = await self.repo.update_by_id(account_id, **kwargs)
        await self.commit()
        return account  # type: ignore[return-value]

    async def archive(self, account_id: int, user_id: int) -> Account:
        await self.get_by_id(account_id, user_id)
        account = await self.repo.archive(account_id)
        await self.commit()
        return account  # type: ignore[return-value]

    async def ensure_active(self, account_id: int, user_id: int) -> Account:
        """Get account and verify it's not archived. Used by other services."""
        account = await self.get_by_id(account_id, user_id)
        if account.is_archive:
            raise InactiveAccountError
        return account
