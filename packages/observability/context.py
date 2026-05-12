from contextvars import ContextVar, Token

trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)


def get_trace_id() -> str | None:
    return trace_id_var.get()


def bind_trace_id(value: str) -> Token[str | None]:
    return trace_id_var.set(value)


def reset_trace_id(token: Token[str | None]) -> None:
    trace_id_var.reset(token)
