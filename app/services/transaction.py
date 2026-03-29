from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Account,
    Transaction,
    TransactionSource,
    TransactionType,
)
from app.repositories.account import AccountRepository
from app.repositories.exchange_rate import ExchangeRateRepository
from app.repositories.tag import TagRepository
from app.repositories.transaction import TransactionRepository
from app.services.base import BaseService
from app.services.errors import (
    InactiveAccountError,
    InvalidTransferError,
    NotFoundError,
    OwnershipError,
)


@dataclass
class TransactionResult:
    transaction: Transaction
    balance_updated: bool


class TransactionService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.repo = TransactionRepository(session)
        self.account_repo = AccountRepository(session)
        self.tag_repo = TagRepository(session)
        self.rate_repo = ExchangeRateRepository(session)

    # ── helpers ──────────────────────────────────────────────────────────

    async def _get_account(self, account_id: int, user_id: int) -> Account:
        account = await self.account_repo.get_by_id(account_id)
        if account is None:
            raise NotFoundError("Account", account_id)
        if account.user_id != user_id:
            raise OwnershipError
        if account.is_archive:
            raise InactiveAccountError
        return account

    async def _resolve_exchange(
        self, currency: str, user_default_currency: str, amount: Decimal
    ) -> tuple[Decimal, Decimal]:
        """Returns (amount_base, exchange_rate)."""
        if currency == user_default_currency:
            return amount, Decimal(1)
        rate = await self.rate_repo.get_latest_rate(currency, user_default_currency)
        if rate is None:
            # no rate available — store amount as-is, rate=1
            return amount, Decimal(1)
        return (amount * rate).quantize(Decimal("0.01")), rate

    async def _apply_balance(
        self,
        transaction_type: TransactionType,
        account_id: int,
        amount: Decimal,
        to_account_id: int | None = None,
    ) -> None:
        if transaction_type == TransactionType.INCOME:
            await self.account_repo.update_balance(account_id, amount)
        elif transaction_type == TransactionType.EXPENSE:
            await self.account_repo.update_balance(account_id, -amount)
        elif transaction_type == TransactionType.TRANSFER:
            await self.account_repo.update_balance(account_id, -amount)
            if to_account_id is not None:
                await self.account_repo.update_balance(to_account_id, amount)

    async def _revert_balance(self, txn: Transaction) -> None:
        if txn.type == TransactionType.INCOME:
            await self.account_repo.update_balance(txn.account_id, -txn.amount)
        elif txn.type == TransactionType.EXPENSE:
            await self.account_repo.update_balance(txn.account_id, txn.amount)
        elif txn.type == TransactionType.TRANSFER:
            await self.account_repo.update_balance(txn.account_id, txn.amount)
            if txn.to_account_id is not None:
                await self.account_repo.update_balance(txn.to_account_id, -txn.amount)

    # ── public API ──────────────────────────────────────────────────────

    async def add(
        self,
        user_id: int,
        account_id: int,
        amount: Decimal,
        currency: str,
        transaction_type: TransactionType,
        user_default_currency: str,
        *,
        category_id: int | None = None,
        to_account_id: int | None = None,
        note: str | None = None,
        source: TransactionSource = TransactionSource.MANUAL,
        occurred_at: datetime | None = None,
        tag_names: list[str] | None = None,
    ) -> TransactionResult:
        # validate accounts
        await self._get_account(account_id, user_id)
        if transaction_type == TransactionType.TRANSFER:
            if to_account_id is None or to_account_id == account_id:
                raise InvalidTransferError
            await self._get_account(to_account_id, user_id)

        # exchange rate
        amount_base, exchange_rate = await self._resolve_exchange(
            currency, user_default_currency, amount
        )

        # create transaction
        txn = await self.repo.create(
            user_id=user_id,
            account_id=account_id,
            category_id=category_id,
            to_account_id=to_account_id,
            amount=amount,
            amount_base=amount_base,
            currency=currency,
            exchange_rate=exchange_rate,
            type=transaction_type,
            note=note,
            source=source,
            **({"occurred_at": occurred_at} if occurred_at else {}),
        )

        # tags
        if tag_names:
            for name in tag_names:
                tag, _ = await self.tag_repo.get_or_create(user_id, name)
                txn.tags.append(tag)

        # update account balance
        await self._apply_balance(transaction_type, account_id, amount, to_account_id)

        await self.commit()
        return TransactionResult(transaction=txn, balance_updated=True)

    async def delete(self, transaction_id: int, user_id: int) -> Transaction:
        txn = await self.repo.get_by_id(transaction_id)
        if txn is None or txn.deleted_at is not None:
            raise NotFoundError("Transaction", transaction_id)
        if txn.user_id != user_id:
            raise OwnershipError

        # soft delete
        updated = await self.repo.soft_delete(transaction_id)

        # revert balance
        await self._revert_balance(txn)

        await self.commit()
        return updated  # type: ignore[return-value]

    async def list(
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
        return await self.repo.get_by_user(
            user_id,
            transaction_type=transaction_type,
            account_id=account_id,
            category_id=category_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )

    async def get_summary(
        self,
        user_id: int,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> dict[str, Decimal]:
        income = await self.repo.get_total_by_type(
            user_id, TransactionType.INCOME, date_from, date_to
        )
        expense = await self.repo.get_total_by_type(
            user_id, TransactionType.EXPENSE, date_from, date_to
        )
        return {"income": income, "expense": expense, "net": income - expense}

    async def get_expenses_by_category(
        self,
        user_id: int,
        date_from: datetime,
        date_to: datetime,
    ) -> Sequence[tuple[int | None, Decimal]]:
        return await self.repo.get_expenses_by_category(
            user_id, date_from, date_to
        )
