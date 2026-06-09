from __future__ import annotations

from packages.providers.registry import get_provider_matrix


def estimate_cost_usd(*, model: str | None, input_tokens: int, output_tokens: int) -> float:
    if not model:
        return 0.0
    matrix = get_provider_matrix()
    for offering in matrix.offerings:
        if offering.model == model:
            return round(
                (input_tokens / 1000.0) * offering.input_price_per_1k
                + (output_tokens / 1000.0) * offering.output_price_per_1k,
                6,
            )
    return 0.0
