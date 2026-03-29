from aiogram.fsm.state import State, StatesGroup


class AddAccount(StatesGroup):
    name = State()
    currency = State()
    account_type = State()
    balance = State()


class AddCategory(StatesGroup):
    category_type = State()
    name = State()
    icon = State()


class AddTransaction(StatesGroup):
    account = State()
    amount = State()
    category = State()
    note = State()


class AddTransfer(StatesGroup):
    from_account = State()
    to_account = State()
    amount = State()
    note = State()


class AddBudget(StatesGroup):
    category = State()
    amount = State()
    period = State()


class RenameAccount(StatesGroup):
    name = State()


class AITransaction(StatesGroup):
    # Q&A loop: AI asks questions, user answers
    answering_question = State()
    # Inline entity creation (account / category / tag)
    confirm_create_entity = State()
    # Final confirmation: user sees parsed data, can confirm / cancel / correct
    confirm = State()
    # After save: offer to remember a pattern
    confirm_pattern = State()
    # Fallback: manual field selection
    missing_account = State()
    missing_category = State()
    missing_to_account = State()
