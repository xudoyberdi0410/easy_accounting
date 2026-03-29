import re
from decimal import Decimal

from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.states import AITransaction
from app.db.models import (
    AccountType,
    CategoryType,
    TransactionSource,
    TransactionType,
    User,
)
from app.services.account import AccountService
from app.services.category import CategoryService
from app.services.transaction import TransactionService
from app.services.ai import AIResponse, GeminiService
from app.repositories.ai_pattern import AIPatternRepository
from app.bot.keyboards.inline import account_select_kb, category_select_kb

router = Router()
ai_service = GeminiService()


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _get_context(session: AsyncSession, user: User) -> dict:
    """Build context dicts for Gemini (accounts, categories, history, patterns)."""
    account_svc = AccountService(session)
    category_svc = CategoryService(session)
    txn_svc = TransactionService(session)
    pattern_repo = AIPatternRepository(session)

    accounts = await account_svc.list_by_user(user.id)
    categories = await category_svc.list_by_user(user.id)
    recent_txns = await txn_svc.list(user.id, limit=20)
    patterns = await pattern_repo.get_by_user(user.id)

    return {
        "accounts": accounts,
        "categories": categories,
        "accounts_data": [
            {"id": a.id, "name": a.name, "currency": a.currency, "type": a.type.value}
            for a in accounts
            if not a.is_archive
        ],
        "categories_data": [
            {"id": c.id, "name": c.name, "type": c.type.value, "icon": c.icon or ""}
            for c in categories
        ],
        "recent_txns_data": [
            {
                "amount": float(t.amount),
                "currency": t.currency,
                "type": t.type.value,
                "category_id": t.category_id,
                "note": t.note,
            }
            for t in recent_txns
        ],
        "patterns_data": [
            {
                "pattern_text": p.pattern_text,
                "category_id": p.category_id,
                "account_id": p.account_id,
                "transaction_type": p.transaction_type,
                "note_template": p.note_template,
            }
            for p in patterns
        ],
    }


def _confirmation_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Confirm", callback_data="ai_confirm:yes"),
                InlineKeyboardButton(text="❌ Cancel", callback_data="ai_confirm:no"),
            ]
        ]
    )


def _yes_no_kb(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Yes", callback_data=f"{prefix}:yes"),
                InlineKeyboardButton(text="No", callback_data=f"{prefix}:no"),
            ]
        ]
    )


async def _build_confirmation_text(
    session: AsyncSession, user: User, parsed_dict: dict
) -> str:
    sign = {"income": "+", "expense": "-", "transfer": "~"}.get(
        parsed_dict.get("type", ""), ""
    )
    lines = [
        "🤖 <b>AI parsed the following transaction:</b>",
        f"Type: <b>{parsed_dict.get('type', '?')}</b>",
        f"Amount: <b>{sign}{parsed_dict.get('amount', '?')}</b>",
    ]

    account_svc = AccountService(session)
    if parsed_dict.get("account_id"):
        try:
            acc = await account_svc.get_by_id(parsed_dict["account_id"], user.id)
            lines.append(f"Account: <b>{acc.name}</b> ({acc.currency})")
        except Exception:
            lines.append(f"Account ID: {parsed_dict['account_id']}")

    if parsed_dict.get("to_account_id"):
        try:
            to_acc = await account_svc.get_by_id(parsed_dict["to_account_id"], user.id)
            lines.append(f"To: <b>{to_acc.name}</b> ({to_acc.currency})")
        except Exception:
            pass

    if parsed_dict.get("category_id"):
        category_svc = CategoryService(session)
        cats = await category_svc.list_by_user(user.id)
        cat = next((c for c in cats if c.id == parsed_dict["category_id"]), None)
        if cat:
            lines.append(f"Category: <b>{cat.icon or ''} {cat.name}</b>")

    if parsed_dict.get("note"):
        lines.append(f"Note: {parsed_dict['note']}")

    lines.append("\n<i>Is this correct? You can also type a correction.</i>")
    return "\n".join(lines)


# ── Core processing ─────────────────────────────────────────────────────────


async def _handle_ai_response(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    ai_resp: AIResponse,
) -> None:
    """Route the AI response through the conversation flow."""
    parsed_dict = ai_resp.transaction.model_dump()
    data = await state.get_data()
    parsed_dict["source"] = data.get("source", "manual")

    # Store everything in state
    await state.update_data(
        parsed_txn=parsed_dict,
        detected_merchant=ai_resp.detected_merchant,
        pending_suggestions=[s.model_dump() for s in ai_resp.suggestions],
    )

    # Step 1: If AI has questions, ask them
    if ai_resp.questions:
        qa_so_far = data.get("qa_pairs", [])
        await state.update_data(
            pending_questions=ai_resp.questions,
            qa_pairs=qa_so_far,
        )
        await state.set_state(AITransaction.answering_question)
        await message.edit_text(
            f"🤔 <b>I need to clarify:</b>\n\n{ai_resp.questions[0]}",
            parse_mode="HTML",
        )
        return

    # Step 2: If AI suggests creating entities, ask about them
    if ai_resp.suggestions:
        await _ask_next_suggestion(message, state)
        return

    # Step 3: Check for missing required fields
    if await _ask_missing_fields(message, state, session, user, parsed_dict):
        return

    # Step 4: All good — show confirmation
    await _show_confirmation(message, state, session, user, parsed_dict)


async def _ask_next_suggestion(message: Message, state: FSMContext) -> None:
    """Ask the user about the next pending entity suggestion."""
    data = await state.get_data()
    suggestions = data.get("pending_suggestions", [])

    if not suggestions:
        # No more suggestions — proceed to missing fields / confirmation
        # We can't easily call _ask_missing_fields here without session/user,
        # so go straight to confirmation state
        await state.set_state(AITransaction.confirm)
        # We'll rely on the confirm handler to finalize
        # For now, send a "processing" message; the confirm state handles it
        await message.edit_text("⏳ Processing...")
        return

    current = suggestions[0]
    entity_type = current["entity_type"]
    name = current["name"]
    reason = current["reason"]
    extra = current.get("extra", "")

    type_label = {
        "account": "💳 Account",
        "category": "📁 Category",
        "tag": "🏷 Tag",
    }.get(entity_type, entity_type)
    extra_info = f" ({extra})" if extra else ""

    await state.set_state(AITransaction.confirm_create_entity)
    await message.edit_text(
        f"💡 <b>Suggestion:</b> Create a new {type_label}?\n\n"
        f"Name: <b>{name}</b>{extra_info}\n"
        f"Reason: {reason}",
        reply_markup=_yes_no_kb("ai_create"),
        parse_mode="HTML",
    )


async def _ask_missing_fields(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    parsed_dict: dict,
) -> bool:
    """Check for missing required fields and ask user. Returns True if something is missing."""
    ctx = await _get_context(session, user)

    if not parsed_dict.get("account_id"):
        await state.set_state(AITransaction.missing_account)
        kb = account_select_kb(ctx["accounts"], prefix="ai_acc")
        # Add a "Create new" button
        kb.inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text="➕ Create new account", callback_data="ai_acc:new"
                )
            ]
        )
        await message.edit_text(
            "From which account?",
            reply_markup=kb,
        )
        return True

    if parsed_dict.get("type") == "transfer" and not parsed_dict.get("to_account_id"):
        await state.set_state(AITransaction.missing_to_account)
        remaining = [a for a in ctx["accounts"] if a.id != parsed_dict["account_id"]]
        kb = account_select_kb(remaining, prefix="ai_to_acc")
        kb.inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text="➕ Create new account", callback_data="ai_to_acc:new"
                )
            ]
        )
        await message.edit_text(
            "To which account?",
            reply_markup=kb,
        )
        return True

    if not parsed_dict.get("category_id") and parsed_dict.get("type") in (
        "income",
        "expense",
    ):
        cat_type = (
            CategoryType.INCOME
            if parsed_dict["type"] == "income"
            else CategoryType.EXPENSE
        )
        matched = [c for c in ctx["categories"] if c.type == cat_type]
        if matched:
            await state.set_state(AITransaction.missing_category)
            kb = category_select_kb(matched, prefix="ai_cat")
            kb.inline_keyboard.append(
                [
                    InlineKeyboardButton(
                        text="➕ Create new category", callback_data="ai_cat:new"
                    )
                ]
            )
            await message.edit_text(
                "Which category?",
                reply_markup=kb,
            )
            return True

    return False


async def _show_confirmation(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    parsed_dict: dict,
) -> None:
    await state.update_data(parsed_txn=parsed_dict)
    await state.set_state(AITransaction.confirm)
    text = await _build_confirmation_text(session, user, parsed_dict)
    await message.edit_text(text, reply_markup=_confirmation_kb(), parse_mode="HTML")


async def _save_transaction(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    parsed_dict: dict,
) -> None:
    svc = TransactionService(session)
    account_svc = AccountService(session)
    account = await account_svc.get_by_id(parsed_dict["account_id"], user.id)
    txn_type = TransactionType(parsed_dict["type"])

    await svc.add(
        user_id=user.id,
        account_id=account.id,
        amount=Decimal(str(parsed_dict["amount"])),
        currency=account.currency,
        transaction_type=txn_type,
        user_default_currency=user.default_currency,
        category_id=parsed_dict.get("category_id"),
        to_account_id=parsed_dict.get("to_account_id"),
        note=parsed_dict.get("note"),
        source=TransactionSource(parsed_dict.get("source", "manual")),
    )

    sign = (
        "+"
        if txn_type == TransactionType.INCOME
        else "-"
        if txn_type == TransactionType.EXPENSE
        else "~"
    )

    data = await state.get_data()
    merchant = data.get("detected_merchant")

    if merchant:
        await state.set_state(AITransaction.confirm_pattern)
        await message.edit_text(
            f"✅ Recorded: {sign}{parsed_dict['amount']} {account.currency}\n"
            f"Account: {account.name}\n"
            f"Note: {parsed_dict.get('note') or ''}\n\n"
            f"💡 Should I <b>remember</b> that <b>{merchant}</b> = this type of transaction?\n"
            f"Next time I'll auto-fill it.",
            reply_markup=_yes_no_kb("ai_pattern"),
            parse_mode="HTML",
        )
    else:
        await state.clear()
        await message.edit_text(
            f"✅ Recorded: {sign}{parsed_dict['amount']} {account.currency}\n"
            f"Account: {account.name}\n"
            f"Note: {parsed_dict.get('note') or ''}"
        )


# ── Input Handlers ───────────────────────────────────────────────────────────


async def _process_input(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    text_input: str | None = None,
    file_bytes: bytes | None = None,
    mime_type: str | None = None,
    source: TransactionSource = TransactionSource.MANUAL,
) -> None:
    if not ai_service.client:
        await message.answer("Gemini API key is not configured.")
        return

    wait_msg = await message.answer("⏳ Processing with AI...")
    ctx = await _get_context(session, user)

    await state.update_data(
        source=source.value,
        original_input=text_input,
        qa_pairs=[],
    )

    ai_resp = await ai_service.parse_transaction(
        text_input=text_input,
        accounts_data=ctx["accounts_data"],
        categories_data=ctx["categories_data"],
        recent_transactions_data=ctx["recent_txns_data"],
        patterns_data=ctx["patterns_data"],
        file_bytes=file_bytes,
        mime_type=mime_type,
    )

    if not ai_resp:
        await wait_msg.edit_text("❌ Failed to parse. Please try again.")
        return

    await _handle_ai_response(wait_msg, state, session, user, ai_resp)


@router.message(F.text, F.forward_origin)
async def handle_ai_forwarded(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    await _process_input(
        message,
        state,
        session,
        user,
        text_input=message.text,
        source=TransactionSource.FORWARDED_MESSAGE,
    )


@router.message(F.voice)
async def handle_ai_voice(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    file = await message.bot.get_file(message.voice.file_id)
    result = await message.bot.download_file(file.file_path)
    file_bytes = result.read()
    await _process_input(
        message,
        state,
        session,
        user,
        file_bytes=file_bytes,
        mime_type="audio/ogg",
        source=TransactionSource.VOICE,
    )


@router.message(F.photo)
async def handle_ai_photo(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    result = await message.bot.download_file(file.file_path)
    file_bytes = result.read()
    await _process_input(
        message,
        state,
        session,
        user,
        text_input=message.caption,
        file_bytes=file_bytes,
        mime_type="image/jpeg",
        source=TransactionSource.SCREENSHOT,
    )


@router.message(F.text, ~F.text.startswith("/"))
async def handle_ai_text(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    await _process_input(
        message,
        state,
        session,
        user,
        text_input=message.text,
        source=TransactionSource.MANUAL,
    )


# ── Q&A Loop ────────────────────────────────────────────────────────────────


@router.message(AITransaction.answering_question, F.text)
async def handle_qa_answer(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    pending_qs = data.get("pending_questions", [])
    qa_pairs = data.get("qa_pairs", [])

    if pending_qs:
        current_q = pending_qs.pop(0)
        qa_pairs.append({"question": current_q, "answer": message.text})
        await state.update_data(pending_questions=pending_qs, qa_pairs=qa_pairs)

    if pending_qs:
        # More questions
        await message.answer(
            f"🤔 <b>Next question:</b>\n\n{pending_qs[0]}",
            parse_mode="HTML",
        )
        return

    # All questions answered — re-parse
    wait_msg = await message.answer("⏳ Applying your answers...")
    ctx = await _get_context(session, user)

    ai_resp = await ai_service.reparse_with_answers(
        original_input=data.get("original_input"),
        current_data=data.get("parsed_txn"),
        qa_pairs=qa_pairs,
        accounts_data=ctx["accounts_data"],
        categories_data=ctx["categories_data"],
        recent_transactions_data=ctx["recent_txns_data"],
        patterns_data=ctx["patterns_data"],
    )

    if not ai_resp:
        await wait_msg.edit_text(
            "❌ Could not re-parse. Try again or type the transaction manually."
        )
        await state.clear()
        return

    await _handle_ai_response(wait_msg, state, session, user, ai_resp)


# ── Entity Creation ─────────────────────────────────────────────────────────


@router.callback_query(AITransaction.confirm_create_entity, F.data == "ai_create:yes")
async def handle_create_entity_yes(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    suggestions = data.get("pending_suggestions", [])
    if not suggestions:
        await cb.answer("Nothing to create.")
        return

    current = suggestions.pop(0)
    await state.update_data(pending_suggestions=suggestions)

    entity_type = current["entity_type"]
    name = current["name"]
    extra = current.get("extra")
    parsed_dict = data["parsed_txn"]

    try:
        if entity_type == "account":
            acc_type = AccountType(extra) if extra else AccountType.OTHER
            svc = AccountService(session)
            # Determine currency from parsed transaction or user default
            currency = parsed_dict.get("currency") or user.default_currency
            new_acc = await svc.create(
                user_id=user.id,
                name=name,
                currency=currency,
                account_type=acc_type,
            )
            # Auto-assign to transaction
            if (
                parsed_dict.get("type") == "transfer"
                and parsed_dict.get("account_id")
                and not parsed_dict.get("to_account_id")
            ):
                parsed_dict["to_account_id"] = new_acc.id
            else:
                parsed_dict["account_id"] = new_acc.id
            await state.update_data(parsed_txn=parsed_dict)
            await cb.answer(f"Created account: {name}")

        elif entity_type == "category":
            cat_type = CategoryType(extra) if extra else CategoryType.EXPENSE
            svc = CategoryService(session)
            new_cat = await svc.create(
                user_id=user.id,
                name=name,
                category_type=cat_type,
            )
            parsed_dict["category_id"] = new_cat.id
            await state.update_data(parsed_txn=parsed_dict)
            await cb.answer(f"Created category: {name}")

        elif entity_type == "tag":
            # Tags are handled via TransactionService at save time
            tags = data.get("pending_tags", [])
            tags.append(name)
            await state.update_data(pending_tags=tags)
            await cb.answer(f"Will add tag: {name}")

    except Exception as e:
        await cb.answer(f"Error: {e}")

    # Continue with next suggestion or move forward
    if suggestions:
        await _ask_next_suggestion(cb.message, state)
    else:
        # Check for missing fields
        parsed_dict = (await state.get_data())["parsed_txn"]
        if await _ask_missing_fields(cb.message, state, session, user, parsed_dict):
            return
        await _show_confirmation(cb.message, state, session, user, parsed_dict)


@router.callback_query(AITransaction.confirm_create_entity, F.data == "ai_create:no")
async def handle_create_entity_no(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    suggestions = data.get("pending_suggestions", [])
    if suggestions:
        suggestions.pop(0)
        await state.update_data(pending_suggestions=suggestions)

    if suggestions:
        await _ask_next_suggestion(cb.message, state)
    else:
        parsed_dict = data["parsed_txn"]
        if await _ask_missing_fields(cb.message, state, session, user, parsed_dict):
            return
        await _show_confirmation(cb.message, state, session, user, parsed_dict)
    await cb.answer()


# ── Missing Field Handlers ──────────────────────────────────────────────────


@router.callback_query(AITransaction.missing_account, F.data == "ai_acc:new")
async def handle_create_new_account(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    """Auto-create a new account based on transaction context."""
    data = await state.get_data()
    parsed_dict = data["parsed_txn"]
    currency = parsed_dict.get("currency") or user.default_currency
    original = data.get("original_input", "")

    # Try to detect card type from original input
    original_lower = (original or "").lower()
    if any(
        kw in original_lower for kw in ["visa", "mastercard", "humo", "uzcard", "карт"]
    ):
        acc_type = AccountType.CARD
        # Try to extract card number
        import re

        match = re.search(r"[*]\s*(\d{4})", original or "")
        name = f"Card *{match.group(1)}" if match else f"Card ({currency})"
    else:
        acc_type = AccountType.OTHER
        name = f"Account ({currency})"

    svc = AccountService(session)
    new_acc = await svc.create(
        user_id=user.id,
        name=name,
        currency=currency,
        account_type=acc_type,
    )
    parsed_dict["account_id"] = new_acc.id
    await state.update_data(parsed_txn=parsed_dict)
    await cb.answer(f"Created: {name}")

    if await _ask_missing_fields(cb.message, state, session, user, parsed_dict):
        return
    await _show_confirmation(cb.message, state, session, user, parsed_dict)


@router.callback_query(AITransaction.missing_account, F.data.startswith("ai_acc:"))
async def handle_missing_account(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    account_id = int(cb.data.split(":")[1])
    data = await state.get_data()
    parsed_dict = data["parsed_txn"]
    parsed_dict["account_id"] = account_id
    await state.update_data(parsed_txn=parsed_dict)

    if await _ask_missing_fields(cb.message, state, session, user, parsed_dict):
        return
    await _show_confirmation(cb.message, state, session, user, parsed_dict)
    await cb.answer()


@router.callback_query(AITransaction.missing_to_account, F.data == "ai_to_acc:new")
async def handle_create_new_to_account(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    parsed_dict = data["parsed_txn"]
    currency = parsed_dict.get("currency") or user.default_currency
    name = f"Account ({currency})"

    svc = AccountService(session)
    new_acc = await svc.create(
        user_id=user.id,
        name=name,
        currency=currency,
        account_type=AccountType.OTHER,
    )
    parsed_dict["to_account_id"] = new_acc.id
    await state.update_data(parsed_txn=parsed_dict)
    await cb.answer(f"Created: {name}")

    if await _ask_missing_fields(cb.message, state, session, user, parsed_dict):
        return
    await _show_confirmation(cb.message, state, session, user, parsed_dict)


@router.callback_query(
    AITransaction.missing_to_account, F.data.startswith("ai_to_acc:")
)
async def handle_missing_to_account(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    account_id = int(cb.data.split(":")[1])
    data = await state.get_data()
    parsed_dict = data["parsed_txn"]
    parsed_dict["to_account_id"] = account_id
    await state.update_data(parsed_txn=parsed_dict)

    if await _ask_missing_fields(cb.message, state, session, user, parsed_dict):
        return
    await _show_confirmation(cb.message, state, session, user, parsed_dict)
    await cb.answer()


@router.callback_query(AITransaction.missing_category, F.data == "ai_cat:new")
async def handle_create_new_category(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    parsed_dict = data["parsed_txn"]
    cat_type = (
        CategoryType.INCOME
        if parsed_dict.get("type") == "income"
        else CategoryType.EXPENSE
    )
    note = parsed_dict.get("note") or "New"
    name = note[:64]

    svc = CategoryService(session)
    new_cat = await svc.create(user_id=user.id, name=name, category_type=cat_type)
    parsed_dict["category_id"] = new_cat.id
    await state.update_data(parsed_txn=parsed_dict)
    await cb.answer(f"Created: {name}")
    await _show_confirmation(cb.message, state, session, user, parsed_dict)


@router.callback_query(AITransaction.missing_category, F.data.startswith("ai_cat:"))
async def handle_missing_category(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    value = cb.data.split(":")[1]
    category_id = None if value == "skip" else int(value)
    data = await state.get_data()
    parsed_dict = data["parsed_txn"]
    parsed_dict["category_id"] = category_id
    await state.update_data(parsed_txn=parsed_dict)
    await _show_confirmation(cb.message, state, session, user, parsed_dict)
    await cb.answer()


# Text handlers for missing fields — let user type corrections naturally


@router.message(AITransaction.confirm_create_entity, F.text)
@router.message(AITransaction.missing_account, F.text)
@router.message(AITransaction.missing_to_account, F.text)
@router.message(AITransaction.missing_category, F.text)
async def handle_missing_field_text(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    """User typed text in a missing field state — treat as a correction."""
    data = await state.get_data()
    parsed_dict = data["parsed_txn"]
    wait_msg = await message.answer("⏳ Processing...")

    ctx = await _get_context(session, user)

    current_state = await state.get_state()
    state_context = ""
    if current_state == AITransaction.missing_account.state:
        state_context = "The bot just asked 'From which account?' to fill the missing account_id. If they want to create a new one, suggest an ACCOUNT."
    elif current_state == AITransaction.missing_to_account.state:
        state_context = "The bot just asked 'To which account?' to fill the missing to_account_id. If they want to create a new one, suggest an ACCOUNT."
    elif current_state == AITransaction.missing_category.state:
        state_context = "The bot just asked 'Which category?' to fill the missing category_id. If they want to create a new one, suggest a CATEGORY."
    elif current_state == AITransaction.confirm_create_entity.state:
        state_context = "The bot just suggested creating an entity. The user is replying with a correction."

    ai_resp = await ai_service.correct_transaction(
        current_data=parsed_dict,
        correction_text=message.text,
        accounts_data=ctx["accounts_data"],
        categories_data=ctx["categories_data"],
        state_context=state_context,
    )

    if not ai_resp:
        await wait_msg.edit_text(
            "❌ Could not understand. Please select from the buttons or try again."
        )
        return

    ai_resp.questions = []
    await _handle_ai_response(wait_msg, state, session, user, ai_resp)


# ── Confirmation ─────────────────────────────────────────────────────────────


@router.callback_query(AITransaction.confirm, F.data == "ai_confirm:yes")
async def handle_confirm_yes(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    parsed_dict = data["parsed_txn"]
    await _save_transaction(cb.message, state, session, user, parsed_dict)
    await cb.answer("Saved!")


@router.callback_query(AITransaction.confirm, F.data == "ai_confirm:no")
async def handle_confirm_no(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text("❌ Cancelled.")
    await cb.answer()


@router.message(AITransaction.confirm, F.text)
async def handle_confirm_correction(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    """User typed a correction during confirmation."""
    data = await state.get_data()
    parsed_dict = data["parsed_txn"]

    wait_msg = await message.answer("⏳ Applying correction...")
    ctx = await _get_context(session, user)

    ai_resp = await ai_service.correct_transaction(
        current_data=parsed_dict,
        correction_text=message.text,
        accounts_data=ctx["accounts_data"],
        categories_data=ctx["categories_data"],
    )

    if not ai_resp:
        await wait_msg.edit_text("❌ Could not apply correction. Try again.")
        return

    updated_dict = ai_resp.transaction.model_dump()
    updated_dict["source"] = parsed_dict.get("source", "manual")

    if ai_resp.detected_merchant:
        await state.update_data(detected_merchant=ai_resp.detected_merchant)

    await _show_confirmation(wait_msg, state, session, user, updated_dict)


# ── Pattern Learning ─────────────────────────────────────────────────────────


@router.callback_query(AITransaction.confirm_pattern, F.data == "ai_pattern:yes")
async def handle_pattern_yes(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    parsed_dict = data["parsed_txn"]
    merchant = data.get("detected_merchant")

    if merchant:
        repo = AIPatternRepository(session)
        await repo.create(
            user_id=user.id,
            pattern_text=merchant,
            category_id=parsed_dict.get("category_id"),
            account_id=parsed_dict.get("account_id"),
            transaction_type=parsed_dict.get("type"),
            note_template=parsed_dict.get("note"),
        )
        await session.commit()
        await cb.message.edit_text(
            cb.message.text + f"\n\n✅ Pattern saved! I'll remember <b>{merchant}</b>.",
            parse_mode="HTML",
        )
    await state.clear()
    await cb.answer("Remembered!")


@router.callback_query(AITransaction.confirm_pattern, F.data == "ai_pattern:no")
async def handle_pattern_no(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.answer("OK, won't remember.")
