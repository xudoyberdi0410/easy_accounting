class ServiceError(Exception):
    """Base service-layer error."""


class NotFoundError(ServiceError):
    def __init__(self, entity: str, id: int | str) -> None:
        self.entity = entity
        self.id = id
        super().__init__(f"{entity} {id} not found")


class OwnershipError(ServiceError):
    """User tried to access a resource they don't own."""


class InactiveAccountError(ServiceError):
    """Operation attempted on an archived account."""


class InsufficientFundsError(ServiceError):
    """Account balance is not enough for the operation."""


class InvalidTransferError(ServiceError):
    """Transfer to the same account or missing to_account_id."""


class BudgetExceededError(ServiceError):
    """Transaction would exceed the budget limit."""
    def __init__(self, budget_id: int, limit: str, spent: str) -> None:
        self.budget_id = budget_id
        super().__init__(
            f"Budget {budget_id} exceeded: limit={limit}, spent={spent}"
        )
