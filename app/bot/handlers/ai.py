import json
import re
from decimal import Decimal

from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.filters import StateFilter
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
from app.services.ai import AIResponse, EntitySuggestion, GeminiService
from app.repositories.ai_pattern import AIPatternRepository
from app.bot.keyboards.inline import account_select_kb, category_select_kb

router = Router()
ai_service = GeminiService()


# ── Keyboards ──────────────────────────────────────────────────────────────


def _confirm_kb() -> InlineKeyboardMarkup:
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
                InlineKeyboardButton(text="✅ Create", callback_data=f"{prefix}:yes"),
                InlineKeyboardButton(text="⏭ Skip", callback_data=f"{prefix}:no"),
            ]
        ]
    )


# ── Helpers ────────────────────────────────────────────────────────────────


async def _get_context(session: AsyncSession, user: User) -> dict:
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


async def _build_confirmation_text(
    session: AsyncSession, user: User, parsed: dict
) -> str:
    sign = {"income": "+", "expense": "-", "transfer": "~"}.get(
        parsed.get("type", ""), ""
    )
    lines = [
        "🤖 <b>Transaction:</b>",
        f"Type: <b>{parsed.get('type', '?')}</b>",
        f"Amount: <b>{sign}{parsed.get('amount', '?')} {parsed.get('currency', '')}</b>",
    ]

    account_svc = AccountService(session)
    if parsed.get("account_id"):
        try:
            acc = await account_svc.get_by_id(parsed["account_id"], user.id)
            lines.append(f"Account: <b>{acc.name}</b> ({acc.currency})")
        except Exception:
            lines.append(f"Account ID: {parsed['account_id']}")

    if parsed.get("to_account_id"):
        try:
            to_acc = await account_svc.get_by_id(parsed["to_account_id"], user.id)
            lines.append(f"→ To: <b>{to_acc.name}</b> ({to_acc.currency})")
        except Exception:
            pass

    if parsed.get("category_id"):
        category_svc = CategoryService(session)
        cats = await category_svc.list_by_user(user.id)
        cat = next((c for c in cats if c.id == parsed["category_id"]), None)
        if cat:
            lines.append(f"Category: <b>{cat.icon or ''} {cat.name}</b>")

    if parsed.get("note"):
        lines.append(f"Note: {parsed['note']}")

    lines.append("\n<i>Confirm or type a correction.</i>")
    return "\n".join(lines)


def _merge_parsed(existing: dict, new_data: dict) -> dict:
    """Merge AI response into existing parsed data.

    Only override fields that the AI explicitly set (not null).
    This prevents losing data set by entity creation.
    """
    merged = existing.copy()
    for key, value in new_data.items():
        if value is not None:
            merged[key] = value
    return merged


def _find_existing_entity(
    suggestion: EntitySuggestion, ctx: dict, parsed: dict
) -> dict | None:
    """Check if a suggested entity already exists. Returns {"field": ..., "id": ...} or None."""
    name_lower = suggestion.name.lower()
    if suggestion.entity_type == "category":
        for c in ctx["categories_data"]:
            if c["name"].lower() == name_lower:
                return {"field": "category_id", "id": c["id"]}
    elif suggestion.entity_type == "account":
        for a in ctx["accounts_data"]:
            if a["name"].lower() == name_lower:
                field = "to_account_id" if parsed.get("account_id") else "account_id"
                return {"field": field, "id": a["id"]}
    return None


# ── Core Flow ──────────────────────────────────────────────────────────────


async def _handle_ai_response(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    ai_resp: AIResponse,
    merge_with_existing: bool = False,
) -> None:
    """Route AI response: suggestions → missing fields → confirmation."""
    new_data = ai_resp.transaction.model_dump()
    data = await state.get_data()

    if merge_with_existing:
        parsed = _merge_parsed(data.get("parsed_txn", {}), new_data)
    else:
        parsed = new_data

    parsed["source"] = data.get("source", "manual")

    # Filter out suggestions for entities that already exist
    ctx = await _get_context(session, user)
    filtered_suggestions = []
    for s in ai_resp.suggestions:
        existing = _find_existing_entity(s, ctx, parsed)
        if existing:
            parsed[existing["field"]] = existing["id"]
        else:
            filtered_suggestions.append(s)

    await state.update_data(
        parsed_txn=parsed,
        detected_merchant=ai_resp.detected_merchant or data.get("detected_merchant"),
        pending_suggestions=[s.model_dump() for s in filtered_suggestions],
    )

    # 1. Suggestions first
    if filtered_suggestions:
        await _show_next_suggestion(message, state)
        return

    # 2. Missing fields fallback
    if await _ask_missing_fields(message, state, session, user, parsed):
        return

    # 3. Confirmation
    await _show_confirmation(message, state, session, user, parsed)


async def _show_next_suggestion(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    suggestions = data.get("pending_suggestions", [])

    if not suggestions:
        return

    current = suggestions[0]
    entity_type = current["entity_type"]
    name = current["name"]
    extra = current.get("extra", "")
    reason = current["reason"]

    icon = "💳" if entity_type == "account" else "📁"
    extra_info = f" ({extra})" if extra else ""

    await state.set_state(AITransaction.suggest_entity)
    await message.edit_text(
        f"{icon} <b>Create {entity_type}?</b>\n\n"
        f"Name: <b>{name}</b>{extra_info}\n"
        f"{reason}",
        reply_markup=_yes_no_kb("ai_create"),
        parse_mode="HTML",
    )


async def _ask_missing_fields(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    parsed: dict,
) -> bool:
    """Check missing required fields. Returns True if asking user."""
    ctx = await _get_context(session, user)

    if not parsed.get("account_id"):
        accounts = [a for a in ctx["accounts"] if not a.is_archive]
        kb = account_select_kb(accounts, prefix="ai_acc")
        kb.inline_keyboard.append(
            [InlineKeyboardButton(text="➕ Create new", callback_data="ai_acc:new")]
        )
        await state.set_state(AITransaction.select_account)
        await message.edit_text("📋 Select account:", reply_markup=kb)
        return True

    if parsed.get("type") == "transfer" and not parsed.get("to_account_id"):
        remaining = [
            a for a in ctx["accounts"]
            if a.id != parsed["account_id"] and not a.is_archive
        ]
        kb = account_select_kb(remaining, prefix="ai_to_acc")
        kb.inline_keyboard.append(
            [InlineKeyboardButton(text="➕ Create new", callback_data="ai_to_acc:new")]
        )
        await state.set_state(AITransaction.select_to_account)
        await message.edit_text("📋 Transfer to which account?", reply_markup=kb)
        return True

    if not parsed.get("category_id") and parsed.get("type") in ("income", "expense"):
        cat_type = (
            CategoryType.INCOME if parsed["type"] == "income" else CategoryType.EXPENSE
        )
        matched = [c for c in ctx["categories"] if c.type == cat_type]
        if matched:
            kb = category_select_kb(matched, prefix="ai_cat")
            kb.inline_keyboard.append(
                [InlineKeyboardButton(text="➕ Create new", callback_data="ai_cat:new")]
            )
            await state.set_state(AITransaction.select_category)
            await message.edit_text("📋 Select category:", reply_markup=kb)
            return True

    return False


async def _show_confirmation(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    parsed: dict,
) -> None:
    await state.update_data(parsed_txn=parsed)
    await state.set_state(AITransaction.confirm)
    text = await _build_confirmation_text(session, user, parsed)
    await message.edit_text(text, reply_markup=_confirm_kb(), parse_mode="HTML")


async def _save_transaction(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    parsed: dict,
) -> None:
    svc = TransactionService(session)
    account_svc = AccountService(session)
    account = await account_svc.get_by_id(parsed["account_id"], user.id)
    txn_type = TransactionType(parsed["type"])

    await svc.add(
        user_id=user.id,
        account_id=account.id,
        amount=Decimal(str(parsed["amount"])),
        currency=account.currency,
        transaction_type=txn_type,
        user_default_currency=user.default_currency,
        category_id=parsed.get("category_id"),
        to_account_id=parsed.get("to_account_id"),
        note=parsed.get("note"),
        source=TransactionSource(parsed.get("source", "manual")),
    )

    sign = {TransactionType.INCOME: "+", TransactionType.EXPENSE: "-"}.get(
        txn_type, "~"
    )

    data = await state.get_data()
    merchant = data.get("detected_merchant")

    result_text = (
        f"✅ Saved: {sign}{parsed['amount']} {account.currency}\n"
        f"Account: {account.name}"
    )
    if parsed.get("note"):
        result_text += f"\nNote: {parsed['note']}"

    if merchant:
        await state.set_state(AITransaction.confirm_pattern)
        await message.edit_text(
            f"{result_text}\n\n"
            f"💡 Remember <b>{merchant}</b> for next time?",
            reply_markup=_yes_no_kb("ai_pattern"),
            parse_mode="HTML",
        )
    else:
        await state.clear()
        await message.edit_text(result_text)


async def _continue_after_suggestion(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    """After handling a suggestion, move to next or missing fields or confirm."""
    data = await state.get_data()
    suggestions = data.get("pending_suggestions", [])

    if suggestions:
        await _show_next_suggestion(message, state)
        return

    parsed = data["parsed_txn"]
    if await _ask_missing_fields(message, state, session, user, parsed):
        return

    await _show_confirmation(message, state, session, user, parsed)


async def _apply_correction(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    correction_text: str,
    file_bytes: bytes | None = None,
    mime_type: str | None = None,
) -> None:
    """Send user correction to AI and handle the response with data merging."""
    data = await state.get_data()
    parsed = data["parsed_txn"]
    conv_history = data.get("conv_history", [])
    ctx = await _get_context(session, user)

    wait_msg = await message.answer("⏳ Processing...")

    correction_msg = (
        f"=== CURRENT TRANSACTION DATA ===\n"
        f"{json.dumps(parsed, ensure_ascii=False)}\n\n"
        f"=== AVAILABLE ACCOUNTS ===\n"
        f"{json.dumps(ctx['accounts_data'], ensure_ascii=False)}\n\n"
        f"=== AVAILABLE CATEGORIES ===\n"
        f"{json.dumps(ctx['categories_data'], ensure_ascii=False)}\n\n"
        f"User correction: {correction_text or '<See attached voice message>'}\n\n"
        "CORRECTION RULES:\n"
        "1. The user is correcting SPECIFIC wrong details in the transaction above. "
        "Figure out WHICH part of the current data is wrong and fix ONLY that part.\n"
        "2. If the user corrects a detail in the note (e.g. destination, item name), "
        "update the note text to fix that detail while KEEPING the rest of the note intact. "
        "Do NOT replace the entire note with just the correction.\n"
        "3. Do NOT change category, account, type, or amount unless the user EXPLICITLY "
        "asks to change them. A correction like 'to school, not work' fixes the note, "
        "NOT the category.\n"
        "4. NEVER set a field to null if it already has a value.\n"
        "5. Return ALL fields from CURRENT TRANSACTION DATA, with only the corrected field(s) changed."
    )

    ai_resp, conv_history = await ai_service.continue_conversation(
        history=conv_history,
        user_message=correction_msg,
        file_bytes=file_bytes,
        mime_type=mime_type,
    )
    await state.update_data(conv_history=conv_history)

    if not ai_resp:
        await wait_msg.edit_text("❌ Could not understand. Try again.")
        return

    # merge_with_existing=True prevents losing fields the AI didn't return
    await _handle_ai_response(
        wait_msg, state, session, user, ai_resp, merge_with_existing=True
    )


# ══════════════════════════════════════════════════════════════════════════
# IMPORTANT: State-specific handlers MUST be registered BEFORE
# the catch-all input handlers, otherwise aiogram matches the catch-all first.
# ══════════════════════════════════════════════════════════════════════════


# ── Entity Suggestions ────────────────────────────────────────────────────


@router.callback_query(AITransaction.suggest_entity, F.data == "ai_create:yes")
async def handle_suggest_yes(
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
    parsed = data["parsed_txn"]

    try:
        if entity_type == "account":
            acc_type = AccountType(extra) if extra else AccountType.OTHER
            new_acc = await AccountService(session).create(
                user_id=user.id,
                name=name,
                currency=parsed.get("currency") or user.default_currency,
                account_type=acc_type,
            )
            if (
                parsed.get("type") == "transfer"
                and parsed.get("account_id")
                and not parsed.get("to_account_id")
            ):
                parsed["to_account_id"] = new_acc.id
            else:
                parsed["account_id"] = new_acc.id
            await cb.answer(f"Created: {name}")

        elif entity_type == "category":
            cat_type = CategoryType(extra) if extra else CategoryType.EXPENSE
            new_cat = await CategoryService(session).create(
                user_id=user.id,
                name=name,
                category_type=cat_type,
            )
            parsed["category_id"] = new_cat.id
            await cb.answer(f"Created: {name}")

    except Exception as e:
        await cb.answer(f"Error: {e}")

    await state.update_data(parsed_txn=parsed)
    await _continue_after_suggestion(cb.message, state, session, user)


@router.callback_query(AITransaction.suggest_entity, F.data == "ai_create:no")
async def handle_suggest_no(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    suggestions = data.get("pending_suggestions", [])
    if suggestions:
        suggestions.pop(0)
        await state.update_data(pending_suggestions=suggestions)

    await cb.answer()
    await _continue_after_suggestion(cb.message, state, session, user)


# ── Account Selection (fallback) ───────────────────────────────────────────


@router.callback_query(AITransaction.select_account, F.data == "ai_acc:new")
async def handle_create_account(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    parsed = data["parsed_txn"]
    currency = parsed.get("currency") or user.default_currency

    original = (data.get("original_input") or "").lower()
    card_keywords = ["visa", "mastercard", "humo", "uzcard", "карт"]
    if any(kw in original for kw in card_keywords):
        acc_type = AccountType.CARD
        match = re.search(r"[*]\s*(\d{4})", data.get("original_input") or "")
        name = f"Card *{match.group(1)}" if match else f"Card ({currency})"
    else:
        acc_type = AccountType.OTHER
        name = f"Account ({currency})"

    new_acc = await AccountService(session).create(
        user_id=user.id, name=name, currency=currency, account_type=acc_type,
    )
    parsed["account_id"] = new_acc.id
    await state.update_data(parsed_txn=parsed)
    await cb.answer(f"Created: {name}")

    if await _ask_missing_fields(cb.message, state, session, user, parsed):
        return
    await _show_confirmation(cb.message, state, session, user, parsed)


@router.callback_query(AITransaction.select_account, F.data.startswith("ai_acc:"))
async def handle_select_account(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    account_id = int(cb.data.split(":")[1])
    data = await state.get_data()
    parsed = data["parsed_txn"]
    parsed["account_id"] = account_id
    await state.update_data(parsed_txn=parsed)
    await cb.answer()

    if await _ask_missing_fields(cb.message, state, session, user, parsed):
        return
    await _show_confirmation(cb.message, state, session, user, parsed)


# ── To-Account Selection (fallback) ───────────────────────────────────────


@router.callback_query(AITransaction.select_to_account, F.data == "ai_to_acc:new")
async def handle_create_to_account(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    parsed = data["parsed_txn"]
    currency = parsed.get("currency") or user.default_currency

    new_acc = await AccountService(session).create(
        user_id=user.id,
        name=f"Account ({currency})",
        currency=currency,
        account_type=AccountType.OTHER,
    )
    parsed["to_account_id"] = new_acc.id
    await state.update_data(parsed_txn=parsed)
    await cb.answer(f"Created: Account ({currency})")

    if await _ask_missing_fields(cb.message, state, session, user, parsed):
        return
    await _show_confirmation(cb.message, state, session, user, parsed)


@router.callback_query(
    AITransaction.select_to_account, F.data.startswith("ai_to_acc:")
)
async def handle_select_to_account(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    account_id = int(cb.data.split(":")[1])
    data = await state.get_data()
    parsed = data["parsed_txn"]
    parsed["to_account_id"] = account_id
    await state.update_data(parsed_txn=parsed)
    await cb.answer()

    if await _ask_missing_fields(cb.message, state, session, user, parsed):
        return
    await _show_confirmation(cb.message, state, session, user, parsed)


# ── Category Selection (fallback) ─────────────────────────────────────────


@router.callback_query(AITransaction.select_category, F.data == "ai_cat:new")
async def handle_create_category(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    parsed = data["parsed_txn"]
    cat_type = (
        CategoryType.INCOME if parsed.get("type") == "income" else CategoryType.EXPENSE
    )
    name = (parsed.get("note") or "New")[:64]

    new_cat = await CategoryService(session).create(
        user_id=user.id, name=name, category_type=cat_type,
    )
    parsed["category_id"] = new_cat.id
    await state.update_data(parsed_txn=parsed)
    await cb.answer(f"Created: {name}")
    await _show_confirmation(cb.message, state, session, user, parsed)


@router.callback_query(AITransaction.select_category, F.data.startswith("ai_cat:"))
async def handle_select_category(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    value = cb.data.split(":")[1]
    category_id = None if value == "skip" else int(value)
    data = await state.get_data()
    parsed = data["parsed_txn"]
    parsed["category_id"] = category_id
    await state.update_data(parsed_txn=parsed)
    await cb.answer()
    await _show_confirmation(cb.message, state, session, user, parsed)


# ── Text corrections during any active state ──────────────────────────────


@router.message(AITransaction.suggest_entity, F.text)
@router.message(AITransaction.select_account, F.text)
@router.message(AITransaction.select_to_account, F.text)
@router.message(AITransaction.select_category, F.text)
@router.message(AITransaction.confirm, F.text)
async def handle_text_correction(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    """User typed text in any active AI state — treat as correction."""
    await _apply_correction(message, state, session, user, message.text)


@router.message(AITransaction.suggest_entity, F.voice)
@router.message(AITransaction.select_account, F.voice)
@router.message(AITransaction.select_to_account, F.voice)
@router.message(AITransaction.select_category, F.voice)
@router.message(AITransaction.confirm, F.voice)
async def handle_voice_correction(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    """User sent voice in any active AI state — treat as correction."""
    file = await message.bot.get_file(message.voice.file_id)
    result = await message.bot.download_file(file.file_path)
    await _apply_correction(
        message, state, session, user,
        correction_text="",
        file_bytes=result.read(),
        mime_type="audio/ogg",
    )


# ── Confirmation ───────────────────────────────────────────────────────────


@router.callback_query(AITransaction.confirm, F.data == "ai_confirm:yes")
async def handle_confirm_yes(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    await _save_transaction(cb.message, state, session, user, data["parsed_txn"])
    await cb.answer("Saved!")


@router.callback_query(AITransaction.confirm, F.data == "ai_confirm:no")
async def handle_confirm_no(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text("❌ Cancelled.")
    await cb.answer()


# ── Pattern Learning ───────────────────────────────────────────────────────


@router.callback_query(AITransaction.confirm_pattern, F.data == "ai_pattern:yes")
async def handle_pattern_yes(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    parsed = data["parsed_txn"]
    merchant = data.get("detected_merchant")

    if merchant:
        repo = AIPatternRepository(session)
        await repo.create(
            user_id=user.id,
            pattern_text=merchant,
            category_id=parsed.get("category_id"),
            account_id=parsed.get("account_id"),
            transaction_type=parsed.get("type"),
            note_template=parsed.get("note"),
        )
        await session.commit()
        await cb.message.edit_text(
            cb.message.text + f"\n\n✅ Saved! I'll remember <b>{merchant}</b>.",
            parse_mode="HTML",
        )
    await state.clear()
    await cb.answer("Remembered!")


@router.callback_query(AITransaction.confirm_pattern, F.data == "ai_pattern:no")
async def handle_pattern_no(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.answer("OK")


# ══════════════════════════════════════════════════════════════════════════
# Input handlers — MUST be LAST so state-specific handlers take priority.
# StateFilter(None) ensures these only fire when no FSM state is active.
# ══════════════════════════════════════════════════════════════════════════


def _match_patterns(
    text: str, patterns: list[dict], active_account_ids: set[int]
) -> dict:
    """Match saved patterns against input text (case-insensitive substring).

    Returns a dict of pre-filled fields from the first matching pattern.
    Only applies account_id if the account is still active.
    """
    if not text:
        return {}
    text_lower = text.lower()
    for p in patterns:
        if p["pattern_text"].lower() in text_lower:
            result = {}
            if p.get("category_id"):
                result["category_id"] = p["category_id"]
            if p.get("account_id") and p["account_id"] in active_account_ids:
                result["account_id"] = p["account_id"]
            if p.get("transaction_type"):
                result["type"] = p["transaction_type"]
            if p.get("note_template"):
                result["note"] = p["note_template"]
            return result
    return {}


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

    wait_msg = await message.answer("⏳ Processing...")
    ctx = await _get_context(session, user)

    await state.update_data(source=source.value, original_input=text_input)

    # Pre-match patterns on bot side (don't rely on AI)
    active_account_ids = {a.id for a in ctx["accounts"] if not a.is_archive}
    pattern_overrides = _match_patterns(text_input, ctx["patterns_data"], active_account_ids)

    ai_resp, conv_history = await ai_service.start_parse(
        text_input=text_input,
        accounts_data=ctx["accounts_data"],
        categories_data=ctx["categories_data"],
        recent_transactions_data=ctx["recent_txns_data"],
        patterns_data=ctx["patterns_data"],
        file_bytes=file_bytes,
        mime_type=mime_type,
    )

    await state.update_data(conv_history=conv_history)

    if not ai_resp:
        await wait_msg.edit_text("❌ Failed to parse. Please try again.")
        return

    # Apply pattern overrides on top of AI response
    if pattern_overrides:
        for key, value in pattern_overrides.items():
            setattr(ai_resp.transaction, key, value)
        # Remove suggestions for fields already filled by pattern
        ai_resp.suggestions = [
            s for s in ai_resp.suggestions
            if not (s.entity_type == "account" and "account_id" in pattern_overrides)
            and not (s.entity_type == "category" and "category_id" in pattern_overrides)
        ]

    await _handle_ai_response(wait_msg, state, session, user, ai_resp)


@router.message(StateFilter(None), F.text, F.forward_origin)
async def handle_ai_forwarded(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    await _process_input(
        message, state, session, user,
        text_input=message.text,
        source=TransactionSource.FORWARDED_MESSAGE,
    )


@router.message(StateFilter(None), F.voice)
async def handle_ai_voice(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    file = await message.bot.get_file(message.voice.file_id)
    result = await message.bot.download_file(file.file_path)
    await _process_input(
        message, state, session, user,
        file_bytes=result.read(),
        mime_type="audio/ogg",
        source=TransactionSource.VOICE,
    )


@router.message(F.photo)
async def handle_ai_photo(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    """Handle photos in any state.

    If user sent text first (which started a parse), then sent a photo,
    we restart the parse combining the original text with the photo.
    This covers the common pattern: "bought 2 HDDs" + [screenshot].
    """
    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    result = await message.bot.download_file(file.file_path)
    file_bytes = result.read()

    # Combine caption with any previous text input from active state
    text = message.caption
    current_state = await state.get_state()
    if current_state:
        data = await state.get_data()
        prev_text = data.get("original_input")
        if prev_text:
            text = f"{prev_text}\n{text}" if text else prev_text
        await state.clear()

    await _process_input(
        message, state, session, user,
        text_input=text,
        file_bytes=file_bytes,
        mime_type="image/jpeg",
        source=TransactionSource.SCREENSHOT,
    )


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def handle_ai_text(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    await _process_input(
        message, state, session, user,
        text_input=message.text,
        source=TransactionSource.MANUAL,
    )
