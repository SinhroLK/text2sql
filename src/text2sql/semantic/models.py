"""Strict, versioned data contracts. No SQL strings or provider dependencies."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, is_dataclass
import hashlib
import json
import types
from typing import Literal, Union, get_args, get_origin, get_type_hints

VERSION = 'semantic-plan-v1'


@dataclass(frozen=True)
class Ref:
    relation: str
    column: str


@dataclass(frozen=True)
class Input:
    alias: str
    kind: Literal['table', 'step']
    source: str


@dataclass(frozen=True)
class Predicate:
    id: str
    column: Ref
    operator: Literal['eq', 'ne', 'lt', 'le', 'gt', 'ge', 'is_null', 'not_null']
    value: str


@dataclass(frozen=True)
class Window:
    partition_by: tuple[Ref, ...]
    order_by: tuple[Ref, ...]
    require_unique_order: bool
    frame: Literal['rows', 'range']


@dataclass(frozen=True)
class Expression:
    name: str
    op: Literal['column', 'literal', 'min', 'max', 'sum', 'count', 'avg',
                'ratio', 'date_key', 'row_number', 'lag']
    args: tuple[Ref, ...]
    literal: str = ''
    date_format: Literal['', 'day', 'month', 'year'] = ''
    window: Window | None = None
    zero_denominator: Literal['', 'null', 'zero', 'error'] = ''
    unit: str = ''


@dataclass(frozen=True)
class Join:
    kind: Literal['inner', 'left']
    left: tuple[Ref, ...]
    right: tuple[Ref, ...]
    cardinality: Literal['one_to_one', 'many_to_one', 'one_to_many', 'many_to_many', 'unknown']
    evidence: str


@dataclass(frozen=True)
class Step:
    name: str
    operation: Literal['project', 'distinct', 'aggregate', 'window', 'join']
    inputs: tuple[Input, ...]
    outputs: tuple[Expression, ...]
    group_by: tuple[str, ...] = ()
    filters: tuple[Predicate, ...] = ()
    joins: tuple[Join, ...] = ()


@dataclass(frozen=True)
class Order:
    column: str
    direction: Literal['asc', 'desc']


@dataclass(frozen=True)
class SemanticPlan:
    version: str
    steps: tuple[Step, ...]
    final_step: str
    output_columns: tuple[str, ...]
    output_grain: tuple[str, ...]
    order_by: tuple[Order, ...]
    ties: Literal['all', 'deterministic', 'unspecified']
    expected_shape: Literal['scalar', 'rows']


@dataclass(frozen=True)
class ColumnMeaning:
    column: Ref  # Canonical table and column, not a step alias.
    representation: Literal['unknown', 'text', 'number', 'day', 'month', 'year']
    measure: Literal['value', 'identifier', 'incremental', 'cumulative']


@dataclass(frozen=True)
class PopulationRule:
    measure: Ref  # Step and output column to inspect.
    required_filters: tuple[str, ...]
    forbidden_filters: tuple[str, ...]


@dataclass(frozen=True)
class TaskContract:
    version: str
    output_columns: tuple[str, ...]
    output_grain: tuple[str, ...]
    order_by: tuple[Order, ...]
    ties: Literal['all', 'deterministic', 'unspecified']
    expected_shape: Literal['scalar', 'rows']
    filters: tuple[Predicate, ...]  # Canonical references; independently reviewed.
    populations: tuple[PopulationRule, ...]
    column_meanings: tuple[ColumnMeaning, ...]


def _decode(value, annotation, path):
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Literal:
        if type(value) is not str or value not in args:
            raise ValueError(f'{path}: invalid enum value')
        return value
    if origin in (Union, types.UnionType):
        for option in args:
            try:
                return _decode(value, option, path)
            except ValueError:
                pass
        raise ValueError(f'{path}: wrong union type')
    if origin is tuple:
        if type(value) not in (list, tuple):
            raise ValueError(f'{path}: expected array')
        if len(value) > 256:
            raise ValueError(f'{path}: array exceeds 256 elements')
        return tuple(_decode(v, args[0], f'{path}[{i}]') for i, v in enumerate(value))
    if is_dataclass(annotation):
        if type(value) is not dict:
            raise ValueError(f'{path}: expected object')
        names = {f.name for f in fields(annotation)}
        if set(value) - names:
            raise ValueError(f'{path}: unknown fields')
        hints = get_type_hints(annotation)
        try:
            return annotation(**{k: _decode(v, hints[k], f'{path}.{k}') for k,v in value.items()})
        except TypeError as error:
            raise ValueError(f'{path}: missing required fields') from error
    if type(value) is not annotation:
        raise ValueError(f'{path}: wrong scalar type')
    if isinstance(value, str) and (len(value) > 4096 or '\x00' in value):
        raise ValueError(f'{path}: invalid string size/content')
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def loads(text: str, model):
    if len(text.encode('utf-8')) > 262144:
        raise ValueError('Plan/contract exceeds 256 KiB')
    try:
        return _decode(json.loads(text, object_pairs_hook=_pairs), model, model.__name__)
    except (RecursionError, json.JSONDecodeError) as error:
        raise ValueError('Invalid or excessively nested JSON') from error


def dumps(model) -> str:
    return json.dumps(asdict(model), ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def checksum(model) -> str:
    return hashlib.sha256(dumps(model).encode()).hexdigest()
