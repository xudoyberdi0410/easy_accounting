import json
from typing import Optional
from pydantic import BaseModel, Field

from google import genai
from google.genai import types

from app.config import settings


# ── Response Schemas ─────────────────────────────────────────────────────────


class ParsedTransaction(BaseModel):
    amount: Optional[float] = Field(
        None, description="The numeric amount of the transaction"
    )
    currency: Optional[str] = Field(
        None, description="Currency code (e.g. USD, RUB, EUR, UZS)"
    )
    category_id: Optional[int] = Field(
        None,
        description="ID of the best matching category from CATEGORIES list. Null if uncertain or no match.",
    )
    account_id: Optional[int] = Field(
        None,
        description="ID of the best matching account from ACCOUNTS list. Null if uncertain or no match.",
    )
    to_account_id: Optional[int] = Field(
        None,
        description="ID of destination account (only for transfers). Null otherwise.",
    )
    type: Optional[str] = Field(
        None, description="One of: 'income', 'expense', 'transfer'. Null if uncertain."
    )
    note: Optional[str] = Field(
        None, description="Brief note, merchant name, or summary."
    )


class EntitySuggestion(BaseModel):
    entity_type: str = Field(description="One of: 'account', 'category', 'tag'")
    name: str = Field(description="Suggested name for the new entity")
    extra: Optional[str] = Field(
        None,
        description="Extra detail: for account — the account type (cash/card/savings/crypto/other); "
        "for category — 'income' or 'expense'; for tag — null.",
    )
    reason: str = Field(description="Short reason why this entity should be created")


class AIResponse(BaseModel):
    transaction: ParsedTransaction = Field(description="The parsed transaction data")
    questions: list[str] = Field(
        default_factory=list,
        description=(
            "Questions to ask the user when you are UNCERTAIN about something. "
            "Examples: 'What is ELEKSIR S03 T19 SHL?', "
            "'Is this income from freelance or salary?'. "
            "Do NOT ask about fields you are confident about."
        ),
    )
    suggestions: list[EntitySuggestion] = Field(
        default_factory=list,
        description=(
            "Suggest creating new entities when NONE of the provided ones match well. "
            "For example: suggest a new 'card' account for 'VISA *6730' if no card accounts exist, "
            "or a new category 'Freelance' if no matching category exists."
        ),
    )
    detected_merchant: Optional[str] = Field(
        None,
        description="Detected merchant/sender name (e.g. 'ELEKSIR S03 T19 SHL') for pattern learning. Null if N/A.",
    )


SYSTEM_PROMPT = """\
You are a smart financial assistant that parses user messages, voice transcripts, \
receipt photos, and forwarded bank notifications into structured transactions.

CRITICAL RULES:
1. MATCHING ACCOUNTS: Look at the account TYPE carefully.
   - If the message mentions a card (VISA, MasterCard, Humo, UzCard, etc.), \
match ONLY to 'card'-type accounts. NEVER pick a 'cash' account for a card transaction.
   - If no suitable account exists, leave account_id as null and add a suggestion \
to create a new account with the correct type.
2. ASKING QUESTIONS: If you see an unknown merchant, location, or abbreviation that \
you cannot confidently categorize, add a question asking the user what it is. \
Do NOT guess blindly.
3. SUGGESTING ENTITIES: If none of the provided categories/accounts/tags fit, \
suggest creating a new one with a sensible name.
4. If the user's message is a forwarded bank notification, parse the amount, \
card info, merchant, date, and transaction type carefully.
5. PATTERNS: Look at RECENT TRANSACTIONS to learn the user's habits. \
If the same merchant appeared before with a specific category, reuse that category.
6. Use the user's KNOWN PATTERNS — these are explicit rules the user has confirmed. \
Always apply them when the pattern matches.

Return your response as structured JSON following the schema exactly.\
"""


class GeminiService:
    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None

    def _build_context(
        self,
        accounts_data: list[dict],
        categories_data: list[dict],
        recent_transactions_data: list[dict],
        patterns_data: list[dict] | None = None,
    ) -> str:
        accounts_str = json.dumps(accounts_data, ensure_ascii=False)
        categories_str = json.dumps(categories_data, ensure_ascii=False)
        history_str = json.dumps(recent_transactions_data, ensure_ascii=False)
        ctx = (
            f"=== ACCOUNTS ===\n{accounts_str}\n\n"
            f"=== CATEGORIES ===\n{categories_str}\n\n"
            f"=== RECENT TRANSACTIONS (learn habits) ===\n{history_str}\n\n"
        )
        if patterns_data:
            patterns_str = json.dumps(patterns_data, ensure_ascii=False)
            ctx += f"=== KNOWN PATTERNS (always apply these) ===\n{patterns_str}\n\n"
        return ctx

    async def parse_transaction(
        self,
        text_input: Optional[str],
        accounts_data: list[dict],
        categories_data: list[dict],
        recent_transactions_data: list[dict],
        patterns_data: list[dict] | None = None,
        file_bytes: Optional[bytes] = None,
        mime_type: Optional[str] = None,
    ) -> Optional[AIResponse]:
        if not self.client:
            return None

        context = self._build_context(
            accounts_data, categories_data, recent_transactions_data, patterns_data
        )
        prompt = (
            f"{context}"
            f"USER INPUT TO PROCESS:\n"
            f"{text_input or '<See attached file/image>'}"
        )

        contents = [prompt]
        if file_bytes and mime_type:
            contents.append(types.Part.from_bytes(data=file_bytes, mime_type=mime_type))

        try:
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
        except Exception as e:
            print(f"Gemini API Error: {e}")
            return None

    async def reparse_with_answers(
        self,
        original_input: str | None,
        qa_pairs: list[dict],
        accounts_data: list[dict],
        categories_data: list[dict],
        recent_transactions_data: list[dict],
        patterns_data: list[dict] | None = None,
    ) -> Optional[AIResponse]:
        """Re-parse the transaction after user answered AI questions."""
        if not self.client:
            return None

        context = self._build_context(
            accounts_data, categories_data, recent_transactions_data, patterns_data
        )
        qa_str = "\n".join(f"Q: {qa['question']}\nA: {qa['answer']}" for qa in qa_pairs)
        prompt = (
            f"{context}"
            f"ORIGINAL USER INPUT:\n{original_input or '<attached file>'}\n\n"
            f"=== Q&A CLARIFICATIONS ===\n{qa_str}\n\n"
            "Now re-parse the transaction using the original input AND the user's answers above. "
            "You should have fewer questions now. Apply everything the user told you."
        )

        try:
            response = await self.client.aio.models.generate_content(
                model="gemini-2.0-flash",
                contents=[prompt],
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=AIResponse,
                ),
            )
            if response.text:
                return AIResponse(**json.loads(response.text))
            return None
        except Exception as e:
            print(f"Gemini API Error (reparse): {e}")
            return None

    async def correct_transaction(
        self,
        current_data: dict,
        correction_text: str,
        accounts_data: list[dict],
        categories_data: list[dict],
    ) -> Optional[AIResponse]:
        """Apply a user's correction to an already-parsed transaction."""
        if not self.client:
            return None

        current_str = json.dumps(current_data, ensure_ascii=False)
        accounts_str = json.dumps(accounts_data, ensure_ascii=False)
        categories_str = json.dumps(categories_data, ensure_ascii=False)

        prompt = (
            "The user previously provided a transaction and I parsed it into the JSON below. "
            "Now the user wants to correct something.\n\n"
            f"=== CURRENT PARSED TRANSACTION ===\n{current_str}\n\n"
            f"=== AVAILABLE ACCOUNTS ===\n{accounts_str}\n\n"
            f"=== AVAILABLE CATEGORIES ===\n{categories_str}\n\n"
            f"=== USER CORRECTION ===\n{correction_text}\n\n"
            "Apply the user's correction and return the FULL updated transaction. "
            "Only change the fields the user mentioned; keep everything else the same. "
            "Clear the questions list since this is a correction."
        )

        try:
            response = await self.client.aio.models.generate_content(
                model="gemini-2.0-flash",
                contents=[prompt],
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=AIResponse,
                ),
            )
            if response.text:
                return AIResponse(**json.loads(response.text))
            return None
        except Exception as e:
            print(f"Gemini API Error (correction): {e}")
            return None
