from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AIPattern


class AIPatternRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: int,
        pattern_text: str,
        category_id: int | None = None,
        account_id: int | None = None,
        transaction_type: str | None = None,
        note_template: str | None = None,
    ) -> AIPattern:
        pattern = AIPattern(
            user_id=user_id,
            pattern_text=pattern_text,
            category_id=category_id,
            account_id=account_id,
            transaction_type=transaction_type,
            note_template=note_template,
        )
        self.session.add(pattern)
        await self.session.flush()
        return pattern

    async def get_by_user(self, user_id: int) -> Sequence[AIPattern]:
        result = await self.session.execute(
            select(AIPattern).where(AIPattern.user_id == user_id)
        )
        return result.scalars().all()

    async def delete(self, pattern_id: int, user_id: int) -> None:
        result = await self.session.execute(
            select(AIPattern).where(
                AIPattern.id == pattern_id,
                AIPattern.user_id == user_id,
            )
        )
        pattern = result.scalar_one_or_none()
        if pattern:
            await self.session.delete(pattern)
            await self.session.flush()
