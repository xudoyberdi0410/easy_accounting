from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Budget, BudgetPeriod
from app.repositories.budget import BudgetRepository
from app.services.base import BaseService
from app.services.errors import NotFoundError, OwnershipError


@dataclass
class BudgetStatus:
    budget: Budget
    limit_amount: Decimal
    spent: Decimal
    remaining: Decimal
    usage_pct: float


class BudgetService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.repo = BudgetRepository(session)

    async def create(
        self,
        user_id: int,
        limit_amount: Decimal,
        currency: str,
        period: BudgetPeriod,
        period_start: date,
        period_end: date,
        category_id: int | None = None,
    ) -> Budget:
        budget = await self.repo.create(
            user_id=user_id,
            category_id=category_id,
            limit_amount=limit_amount,
            currency=currency,
            period=period,
            period_start=period_start,
            period_end=period_end,
        )
        await self.commit()
        return budget

    async def get_by_id(self, budget_id: int, user_id: int) -> Budget:
        budget = await self.repo.get_by_id(budget_id)
        if budget is None:
            raise NotFoundError("Budget", budget_id)
        if budget.user_id != user_id:
            raise OwnershipError
        return budget

    async def list_active(self, user_id: int) -> Sequence[Budget]:
        return await self.repo.get_active_by_user(user_id)

    async def list_all(self, user_id: int) -> Sequence[Budget]:
        return await self.repo.get_by_user(user_id)

    async def get_status(self, budget_id: int, user_id: int) -> BudgetStatus:
        budget = await self.get_by_id(budget_id, user_id)
        limit_amount, spent = await self.repo.get_spent(budget_id)
        remaining = limit_amount - spent
        usage_pct = (
            float(spent / limit_amount * 100) if limit_amount > 0 else 0.0
        )
        return BudgetStatus(
            budget=budget,
            limit_amount=limit_amount,
            spent=spent,
            remaining=remaining,
            usage_pct=usage_pct,
        )

    async def get_all_statuses(self, user_id: int) -> list[BudgetStatus]:
        budgets = await self.repo.get_active_by_user(user_id)
        statuses = []
        for b in budgets:
            limit_amount, spent = await self.repo.get_spent(b.id)
            remaining = limit_amount - spent
            usage_pct = (
                float(spent / limit_amount * 100) if limit_amount > 0 else 0.0
            )
            statuses.append(
                BudgetStatus(
                    budget=b,
                    limit_amount=limit_amount,
                    spent=spent,
                    remaining=remaining,
                    usage_pct=usage_pct,
                )
            )
        return statuses

    async def update(
        self,
        budget_id: int,
        user_id: int,
        *,
        limit_amount: Decimal | None = None,
        period_end: date | None = None,
    ) -> Budget:
        await self.get_by_id(budget_id, user_id)
        kwargs = {}
        if limit_amount is not None:
            kwargs["limit_amount"] = limit_amount
        if period_end is not None:
            kwargs["period_end"] = period_end
        if not kwargs:
            return await self.get_by_id(budget_id, user_id)
        budget = await self.repo.update_by_id(budget_id, **kwargs)
        await self.commit()
        return budget  # type: ignore[return-value]

    async def delete(self, budget_id: int, user_id: int) -> None:
        await self.get_by_id(budget_id, user_id)
        await self.repo.delete_by_id(budget_id)
        await self.commit()
