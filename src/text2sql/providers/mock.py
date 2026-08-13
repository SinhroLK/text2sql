from __future__ import annotations

from text2sql.domain import GenerationInput
from text2sql.providers.base import ProviderResponse


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


class MockSchemaAwareProvider:
    """Deterministic provider for tests and Phase 0 smoke runs."""

    provider_name = "mock"
    model_id = "mock-schema-aware-v1"

    def generate(self, generation_input: GenerationInput) -> ProviderResponse:
        tables = generation_input.schema.tables
        if not tables:
            sql = "SELECT 1 AS result"
        else:
            question = generation_input.question.casefold()
            selected_table = next(
                (table for table in tables if table.name.casefold() in question),
                tables[0],
            )
            selected_columns = [
                column.name for column in selected_table.columns if not column.primary_key
            ] or [column.name for column in selected_table.columns]
            projection = ", ".join(_quote(name) for name in selected_columns[:2]) or "*"
            sql = f"SELECT {projection} FROM {_quote(selected_table.name)} LIMIT 10"

        return ProviderResponse(
            candidates=(sql,),
            input_tokens=len(generation_input.prompt.split()),
            output_tokens=len(sql.split()),
        )

