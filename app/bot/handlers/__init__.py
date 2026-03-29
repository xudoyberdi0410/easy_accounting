from aiogram import Router

from app.bot.handlers.cancel import router as cancel_router
from app.bot.handlers.start import router as start_router
from app.bot.handlers.account import router as account_router
from app.bot.handlers.category import router as category_router
from app.bot.handlers.transaction import router as transaction_router
from app.bot.handlers.budget import router as budget_router
from app.bot.handlers.settings import router as settings_router
from app.bot.handlers.ai import router as ai_router


def setup_routers() -> Router:
    root = Router()
    root.include_router(cancel_router)
    root.include_router(start_router)
    root.include_router(account_router)
    root.include_router(category_router)
    root.include_router(transaction_router)
    root.include_router(budget_router)
    root.include_router(settings_router)
    root.include_router(ai_router)
    return root
