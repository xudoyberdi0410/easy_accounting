import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


# ── Enums ────────────────────────────────────────────────────────────────────

class AccountType(str, enum.Enum):
    CASH = "cash"
    CARD = "card"
    SAVINGS = "savings"
    CRYPTO = "crypto"
    OTHER = "other"


class TransactionType(str, enum.Enum):
    INCOME = "income"
    EXPENSE = "expense"
    TRANSFER = "transfer"


class TransactionSource(str, enum.Enum):
    MANUAL = "manual"
    SCREENSHOT = "screenshot"
    VOICE = "voice"
    FORWARDED_MESSAGE = "forwarded_message"


class CategoryType(str, enum.Enum):
    INCOME = "income"
    EXPENSE = "expense"


class BudgetPeriod(str, enum.Enum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


# ── Models ───────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    language_code: Mapped[str] = mapped_column(String(8), default="ru")
    default_currency: Mapped[str] = mapped_column(String(3), default="USD")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    accounts: Mapped[list["Account"]] = relationship(back_populates="user")
    categories: Mapped[list["Category"]] = relationship(back_populates="user")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="user")
    budgets: Mapped[list["Budget"]] = relationship(back_populates="user")
    tags: Mapped[list["Tag"]] = relationship(back_populates="user")
    recurring_transactions: Mapped[list["RecurringTransaction"]] = relationship(
        back_populates="user"
    )


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        Index("idx_accounts_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    balance: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    type: Mapped[AccountType] = mapped_column(
        SAEnum(AccountType, values_callable=lambda e: [m.value for m in e]),
        default=AccountType.CASH,
    )
    is_archive: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="accounts")
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="account",
        foreign_keys="Transaction.account_id",
    )


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("user_id", "name", "type", name="uq_category_user_name_type"),
        Index("idx_categories_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[CategoryType] = mapped_column(
        SAEnum(CategoryType, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    icon: Mapped[str | None] = mapped_column(String(32))
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("categories.id", ondelete="SET NULL")
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User | None"] = relationship(back_populates="categories")
    parent: Mapped["Category | None"] = relationship(
        remote_side="Category.id", back_populates="children"
    )
    children: Mapped[list["Category"]] = relationship(back_populates="parent")


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint(
            "type != 'transfer' OR to_account_id IS NOT NULL",
            name="ck_transfer_has_to_account",
        ),
        Index(
            "idx_transactions_user", "user_id",
            postgresql_where="deleted_at IS NULL",
        ),
        Index(
            "idx_transactions_account", "account_id",
            postgresql_where="deleted_at IS NULL",
        ),
        Index(
            "idx_transactions_date", "occurred_at",
            postgresql_where="deleted_at IS NULL",
        ),
        Index(
            "idx_transactions_type", "type",
            postgresql_where="deleted_at IS NULL",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    category_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("categories.id", ondelete="SET NULL")
    )
    to_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="SET NULL")
    )
    amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    amount_base: Mapped[float | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    exchange_rate: Mapped[float | None] = mapped_column(Numeric(18, 6))
    type: Mapped[TransactionType] = mapped_column(
        SAEnum(TransactionType, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[TransactionSource] = mapped_column(
        SAEnum(TransactionSource, values_callable=lambda e: [m.value for m in e]),
        default=TransactionSource.MANUAL,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    user: Mapped["User"] = relationship(back_populates="transactions")
    account: Mapped["Account"] = relationship(
        back_populates="transactions", foreign_keys=[account_id]
    )
    to_account: Mapped["Account | None"] = relationship(foreign_keys=[to_account_id])
    category: Mapped["Category | None"] = relationship()
    tags: Mapped[list["Tag"]] = relationship(
        secondary="transaction_tags", back_populates="transactions"
    )


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_tag_user_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)

    user: Mapped["User"] = relationship(back_populates="tags")
    transactions: Mapped[list["Transaction"]] = relationship(
        secondary="transaction_tags", back_populates="tags"
    )


class TransactionTag(Base):
    __tablename__ = "transaction_tags"

    transaction_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("transactions.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (
        CheckConstraint("period_end > period_start", name="ck_budget_period_range"),
        Index("idx_budgets_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    category_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("categories.id", ondelete="SET NULL")
    )
    limit_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    period: Mapped[BudgetPeriod] = mapped_column(
        SAEnum(BudgetPeriod, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    user: Mapped["User"] = relationship(back_populates="budgets")
    category: Mapped["Category | None"] = relationship()


class RecurringTransaction(Base):
    __tablename__ = "recurring_transactions"
    __table_args__ = (
        Index(
            "idx_recurring_next_run", "next_run_at",
            postgresql_where="is_active = true",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    category_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("categories.id", ondelete="SET NULL")
    )
    amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    type: Mapped[TransactionType] = mapped_column(
        SAEnum(TransactionType, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    note: Mapped[str | None] = mapped_column(Text)
    cron_expr: Mapped[str] = mapped_column(String(32), nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped["User"] = relationship(back_populates="recurring_transactions")
    account: Mapped["Account"] = relationship()
    category: Mapped["Category | None"] = relationship()


class ExchangeRate(Base):
    __tablename__ = "exchange_rates"
    __table_args__ = (
        Index("idx_exchange_rates_pair_time", "base_currency", "quote_currency", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
