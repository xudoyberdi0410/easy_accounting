import json
from typing import Optional
from pydantic import BaseModel, Field

from google import genai
from google.genai import types

from app.config import settings


# ── Response Schemas ─────────────────────────────────────────────────────────


class ParsedTransaction(BaseModel):
    amount: Optional[float] = Field(None, description="Numeric amount")
    currency: Optional[str] = Field(None, description="Currency code (USD, UZS, etc.)")
    category_id: Optional[int] = Field(
        None, description="ID of matching category from CATEGORIES. Null if none match."
    )
    account_id: Optional[int] = Field(
        None, description="ID of matching account from ACCOUNTS. Null if none match."
    )
    to_account_id: Optional[int] = Field(
        None, description="Destination account ID (transfers only)."
    )
    type: Optional[str] = Field(
        None, description="One of: 'income', 'expense', 'transfer'."
    )
    note: Optional[str] = Field(None, description="Brief note or merchant name.")


class EntitySuggestion(BaseModel):
    entity_type: str = Field(description="'account' or 'category'")
    name: str = Field(description="Suggested name for the new entity")
    extra: Optional[str] = Field(
        None,
        description="For account: type (cash/card/savings/crypto/other). "
        "For category: 'income' or 'expense'.",
    )
    reason: str = Field(description="Short reason why this entity should be created")


class AIResponse(BaseModel):
    transaction: ParsedTransaction = Field(description="Parsed transaction data")
    suggestions: list[EntitySuggestion] = Field(
        default_factory=list,
        description=(
            "Entities to create for missing fields. "
            "ALWAYS suggest when no existing account/category matches. "
            "For cards: use the exact identifier from the message, e.g. name='Card *0583', extra='card'. "
            "For unknown merchants: suggest a category with a reasonable name."
        ),
    )
    detected_merchant: Optional[str] = Field(
        None, description="Merchant/sender name for pattern learning."
    )


SYSTEM_PROMPT = """\
You are a smart financial assistant inside a Telegram bot. You parse user messages, \
voice transcripts, receipt photos, and forwarded bank notifications into structured transactions.

CRITICAL RULES:

1. BE DECISIVE. NEVER ask clarifying questions. Make your best guess for every field. \
The user can correct later if needed.

2. ACCOUNT MATCHING — THIS IS CRITICAL:
   - When the message contains an account/card identifier (e.g. ***0583, *6730, ****1234), \
you MUST match by those LAST DIGITS. Find an account in ACCOUNTS whose name contains \
the same digits (e.g. "*0583", "*6730").
   - If NO account in ACCOUNTS matches the identifier → set account_id to null AND \
ALWAYS add a suggestion to create a new account. Use the identifier in the name \
(e.g. "Card *0583"). Set extra to "card" if it's a card, otherwise pick the right type.
   - NEVER assign an account whose identifier doesn't match. "VISA *6730" does NOT \
match ***0583 — they are different accounts!
   - If the message has no identifier, match by type and context as best you can.
   - NEVER leave account_id null without a suggestion.

3. CATEGORY MATCHING — THIS IS CRITICAL:
   - Pick the closest matching category from CATEGORIES list.
   - If NOTHING fits well → ALWAYS suggest creating a new category with a SPECIFIC, \
descriptive name based on context (e.g. "Freelance", "Groceries", "Taxi", "Salary").
   - NEVER pick a generic "Other Income" or "Other Expense" category as a fallback. \
If no good match exists, SUGGEST a new specific category instead.
   - For income: extra="income". For expense: extra="expense".

4. TRANSACTION TYPE — THIS IS CRITICAL:
   - "transfer" means moving money between the USER'S OWN accounts. \
ONLY use "transfer" when both source and destination are the user's accounts.
   - Payments to OTHER PEOPLE (P2P transfers, purchases from sellers, payments to \
"Aksarov Davir", "Иванов", etc.) are ALWAYS "expense", NOT "transfer".
   - Screenshots showing a payment confirmation with a recipient name = expense.
   - When in doubt between transfer and expense, choose EXPENSE.

5. ALWAYS FILL FIELDS:
   - amount, currency, type — extract from the message, NEVER leave null if present.
   - note — use the user's description if provided (e.g. "bought 2 HDDs"), \
otherwise use merchant/recipient name. ALWAYS prefer user's own description over raw data.
   - account_id — match or suggest. NEVER leave null without a suggestion.
   - category_id — match or suggest. NEVER leave null without a suggestion (except transfers).

6. FORWARDED BANK NOTIFICATIONS: Parse amount, card info, merchant, date, type carefully. \
"Пополнение" = income, "Оплата"/"Списание" = expense, "Перевод" = transfer between own accounts only.

7. PATTERNS: Use RECENT TRANSACTIONS and KNOWN PATTERNS to match categories and accounts. \
If the same merchant appeared before with a specific category, reuse it.

8. CORRECTIONS: The user is fixing a SPECIFIC wrong detail. \
Figure out exactly WHICH part is wrong and fix ONLY that. \
- If the correction is about a detail in the note (destination, item, person), \
edit the note text to fix that detail — do NOT replace the entire note. \
- Do NOT change category, account, type, or amount unless the user EXPLICITLY asks. \
A destination correction does NOT mean a category change. \
- Keep ALL other fields exactly as provided in CURRENT TRANSACTION DATA. \
- NEVER reset fields to null that already have values.

9. CONVERSATION CONTINUITY: You have full conversation history. Use it for context. \
When the user sends both text AND a photo/screenshot, the text describes the PURPOSE \
of the transaction — use it for the note and to determine the category.

Return structured JSON following the schema exactly.\
"""

# Serializable conversation history for FSM state
ConversationHistory = list[dict[str, str]]


class GeminiService:
    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None

    @staticmethod
    def _build_context(
        accounts_data: list[dict],
        categories_data: list[dict],
        recent_transactions_data: list[dict],
        patterns_data: list[dict] | None = None,
    ) -> str:
        ctx = (
            f"=== ACCOUNTS ===\n{json.dumps(accounts_data, ensure_ascii=False)}\n\n"
            f"=== CATEGORIES ===\n{json.dumps(categories_data, ensure_ascii=False)}\n\n"
            f"=== RECENT TRANSACTIONS ===\n{json.dumps(recent_transactions_data, ensure_ascii=False)}\n\n"
        )
        if patterns_data:
            ctx += f"=== KNOWN PATTERNS ===\n{json.dumps(patterns_data, ensure_ascii=False)}\n\n"
        return ctx

    @staticmethod
    def _history_to_contents(history: ConversationHistory) -> list[types.Content]:
        contents = []
        for entry in history:
            parts = []
            if entry.get("text"):
                parts.append(types.Part.from_text(text=entry["text"]))
            if entry.get("file_bytes_hex") and entry.get("mime_type"):
                parts.append(
                    types.Part.from_bytes(
                        data=bytes.fromhex(entry["file_bytes_hex"]),
                        mime_type=entry["mime_type"],
                    )
                )
            if parts:
                contents.append(types.Content(role=entry["role"], parts=parts))
        return contents

    async def _call_gemini(self, contents: list[types.Content]) -> Optional[AIResponse]:
        response = await self.client.aio.models.generate_content(
            model="gemini-2.0-flash",
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=AIResponse,
            ),
        )
        if response.text:
            return AIResponse(**json.loads(response.text))
        return None

    async def start_parse(
        self,
        text_input: Optional[str],
        accounts_data: list[dict],
        categories_data: list[dict],
        recent_transactions_data: list[dict],
        patterns_data: list[dict] | None = None,
        file_bytes: Optional[bytes] = None,
        mime_type: Optional[str] = None,
    ) -> tuple[Optional[AIResponse], ConversationHistory]:
        if not self.client:
            return None, []

        context = self._build_context(
            accounts_data, categories_data, recent_transactions_data, patterns_data
        )
        user_text = f"{context}USER INPUT:\n{text_input or '<See attached file>'}"

        history_entry: dict[str, str] = {"role": "user", "text": user_text}
        if file_bytes and mime_type and len(file_bytes) <= 512_000:
            history_entry["file_bytes_hex"] = file_bytes.hex()
            history_entry["mime_type"] = mime_type

        history: ConversationHistory = [history_entry]
        contents = self._history_to_contents(history)

        try:
            ai_resp = await self._call_gemini(contents)
            if ai_resp:
                history.append({"role": "model", "text": ai_resp.model_dump_json()})
            return ai_resp, history
        except Exception as e:
            print(f"Gemini API Error: {e}")
            return None, history

    async def continue_conversation(
        self,
        history: ConversationHistory,
        user_message: str,
        file_bytes: Optional[bytes] = None,
        mime_type: Optional[str] = None,
    ) -> tuple[Optional[AIResponse], ConversationHistory]:
        if not self.client:
            return None, history

        entry: dict[str, str] = {"role": "user", "text": user_message}
        if file_bytes and mime_type and len(file_bytes) <= 512_000:
            entry["file_bytes_hex"] = file_bytes.hex()
            entry["mime_type"] = mime_type
        history.append(entry)
        contents = self._history_to_contents(history)

        try:
            ai_resp = await self._call_gemini(contents)
            if ai_resp:
                history.append({"role": "model", "text": ai_resp.model_dump_json()})
            return ai_resp, history
        except Exception as e:
            print(f"Gemini API Error (continue): {e}")
            return None, history
