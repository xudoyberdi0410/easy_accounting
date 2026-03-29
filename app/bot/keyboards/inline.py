from typing import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.db.models import (
    Account,
    AccountType,
    Budget,
    Category,
    CategoryType,
    Transaction,
    TransactionType,
)


# ── Accounts ────────────────────────────────────────────────────────────────

ACCOUNT_TYPE_LABELS = {
    AccountType.CASH: "Cash",
    AccountType.CARD: "Card",
    AccountType.SAVINGS: "Savings",
    AccountType.CRYPTO: "Crypto",
    AccountType.OTHER: "Other",
}


def account_type_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=label, callback_data=f"acc_type:{t.value}")]
        for t, label in ACCOUNT_TYPE_LABELS.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def accounts_list_kb(accounts: Sequence[Account]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=f"{a.name} ({a.currency}) — {a.balance}",
                callback_data=f"acc:{a.id}",
            )
        ]
        for a in accounts
    ]
    buttons.append(
        [InlineKeyboardButton(text="+ New account", callback_data="acc:new")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def account_actions_kb(account_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Rename", callback_data=f"acc_rename:{account_id}"),
                InlineKeyboardButton(text="Archive", callback_data=f"acc_archive:{account_id}"),
            ],
            [InlineKeyboardButton(text="Back", callback_data="acc:list")],
        ]
    )


# ── Categories ──────────────────────────────────────────────────────────────

def category_type_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Income", callback_data="cat_type:income"),
                InlineKeyboardButton(text="Expense", callback_data="cat_type:expense"),
            ]
        ]
    )


def categories_list_kb(
    categories: Sequence[Category], action_prefix: str = "cat"
) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=f"{c.icon or ''} {c.name}".strip(),
                callback_data=f"{action_prefix}:{c.id}",
            )
        ]
        for c in categories
    ]
    buttons.append(
        [InlineKeyboardButton(text="+ New category", callback_data=f"{action_prefix}:new")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ── Transactions ────────────────────────────────────────────────────────────

def account_select_kb(
    accounts: Sequence[Account], prefix: str = "txn_acc"
) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=f"{a.name} ({a.currency})",
                callback_data=f"{prefix}:{a.id}",
            )
        ]
        for a in accounts
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def category_select_kb(
    categories: Sequence[Category], prefix: str = "txn_cat"
) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=f"{c.icon or ''} {c.name}".strip(),
                callback_data=f"{prefix}:{c.id}",
            )
        ]
        for c in categories
    ]
    buttons.append(
        [InlineKeyboardButton(text="Skip", callback_data=f"{prefix}:skip")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def confirm_delete_txn_kb(txn_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Yes, delete", callback_data=f"txn_del_yes:{txn_id}"),
                InlineKeyboardButton(text="Cancel", callback_data="txn_del_no"),
            ]
        ]
    )


def history_nav_kb(page: int, has_next: bool) -> InlineKeyboardMarkup:
    buttons: list[InlineKeyboardButton] = []
    if page > 0:
        buttons.append(
            InlineKeyboardButton(text="<< Prev", callback_data=f"hist_page:{page - 1}")
        )
    if has_next:
        buttons.append(
            InlineKeyboardButton(text="Next >>", callback_data=f"hist_page:{page + 1}")
        )
    return InlineKeyboardMarkup(inline_keyboard=[buttons] if buttons else [])


# ── Budgets ─────────────────────────────────────────────────────────────────

def budgets_list_kb(budgets: Sequence[Budget]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=f"{b.currency} {b.limit_amount} ({b.period.value})",
                callback_data=f"bdg:{b.id}",
            )
        ]
        for b in budgets
    ]
    buttons.append(
        [InlineKeyboardButton(text="+ New budget", callback_data="bdg:new")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def budget_actions_kb(budget_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Status", callback_data=f"bdg_status:{budget_id}"),
                InlineKeyboardButton(text="Delete", callback_data=f"bdg_del:{budget_id}"),
            ],
            [InlineKeyboardButton(text="Back", callback_data="bdg:list")],
        ]
    )


# ── Settings ────────────────────────────────────────────────────────────────

def settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Change currency", callback_data="set:currency")],
            [InlineKeyboardButton(text="Change language", callback_data="set:language")],
        ]
    )


def currency_select_kb() -> InlineKeyboardMarkup:
    currencies = ["USD", "EUR", "RUB", "GBP", "UZS", "KZT"]
    buttons = [
        [InlineKeyboardButton(text=c, callback_data=f"set_cur:{c}")]
        for c in currencies
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def language_select_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Русский", callback_data="set_lang:ru"),
                InlineKeyboardButton(text="English", callback_data="set_lang:en"),
            ]
        ]
    )
