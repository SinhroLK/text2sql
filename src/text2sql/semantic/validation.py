"""Conservative structural checks over a relational plan, never SQL authorization."""
from dataclasses import asdict, dataclass
from datetime import datetime
import re

from text2sql.domain import SchemaSnapshot
from text2sql.schema.canonical import canonical_schema_sha256
from .models import SemanticPlan, TaskContract, VERSION, checksum, dumps, loads


@dataclass(frozen=True)
class Issue:
    code: str
    location: str
    severity: str = 'error'


@dataclass(frozen=True)
class Result:
    valid: bool
    version: str
    plan_sha256: str
    contract_sha256: str
    schema_sha256: str
    issues: tuple[Issue, ...]
    sql_authorized: bool = False

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Fact:
    origins: frozenset[tuple[str, str]]
    representation: str = 'unknown'
    measure: str = 'value'


@dataclass
class Relation:
    columns: dict[str, Fact]
    keys: set[frozenset[str]]
    filters: set[str]


def validate(plan: SemanticPlan, schema: SchemaSnapshot, contract: TaskContract) -> Result:
    # Recheck even direct Python construction: annotations alone are not runtime checks.
    plan = loads(dumps(plan), SemanticPlan)
    contract = loads(dumps(contract), TaskContract)
    schema_hash = canonical_schema_sha256(schema)
    issues = []

    def issue(code, where, severity='error'):
        item = Issue(code, where, severity)
        if item not in issues:
            issues.append(item)

    def unique(names, where):
        folded = [n.casefold() for n in names]
        if len(folded) != len(set(folded)):
            issue('duplicate_name', where)

    def names_ok(names, where):
        unique(names, where)
        if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', n) for n in names):
            issue('invalid_plan_identifier', where)

    def refkey(ref):
        return ref.relation.casefold(), ref.column.casefold()

    tables = {}
    for table in schema.tables:
        columns = {c.name.casefold(): Fact(frozenset({(table.name.casefold(),c.name.casefold())})) for c in table.columns}
        # Nullable composite SQLite primary keys do not establish uniqueness under
        # NULL semantics. Do not turn them into a proof merely from their label.
        primary = [c for c in table.columns if c.primary_key]
        keys = {frozenset(c.name.casefold() for c in primary)} if primary and all(not c.nullable for c in primary) else set()
        tables[table.name.casefold()] = Relation(columns,keys,set())

    def canonical(ref, where):
        relation = tables.get(ref.relation.casefold())
        if relation is None or ref.column.casefold() not in relation.columns:
            issue('unknown_canonical_column', where)
            return None
        return relation

    unique([f.id for f in contract.filters], 'contract.filters')
    meanings_seen = set()
    for meaning in contract.column_meanings:
        key = refkey(meaning.column)
        if key in meanings_seen:
            issue('duplicate_column_meaning','contract.column_meanings')
        meanings_seen.add(key)
        table = canonical(meaning.column,'contract.column_meanings')
        if table:
            table.columns[key[1]] = Fact(frozenset({key}),meaning.representation,meaning.measure)
    for predicate in contract.filters:
        canonical(predicate.column,'contract.filters')
        if not predicate.id.strip(): issue('empty_filter_id','contract.filters')
    requirements = {f.id:f for f in contract.filters}
    if plan.version != VERSION or contract.version != VERSION:
        issue('unsupported_version','version')
    if schema.dialect.casefold() != 'sqlite': issue('unsupported_dialect','schema')
    if not plan.steps: issue('empty_plan','steps')
    names_ok([s.name for s in plan.steps],'steps')
    steps = {}
    dependencies = {}
    used_filters = set()
    aggregates = {'min','max','sum','count','avg'}
    arity = {'column':1,'literal':0,'min':1,'max':1,'sum':1,'count':None,'avg':1,
             'ratio':2,'date_key':1,'row_number':0,'lag':1}
    for step in plan.steps:
        where = 'steps.'+step.name
        names_ok([i.alias for i in step.inputs],where+'.inputs')
        names_ok([e.name for e in step.outputs],where+'.outputs')
        unique(step.group_by,where+'.group_by')
        if not step.inputs or not step.outputs: issue('empty_relation',where)
        inputs = {}
        dependencies[step.name.casefold()] = {i.source.casefold() for i in step.inputs if i.kind=='step'}
        for binding in step.inputs:
            source = (tables if binding.kind=='table' else steps).get(binding.source.casefold())
            if source is None:
                issue('unknown_or_forward_source',where+'.'+binding.alias)
                source = Relation({},set(),set())
            inputs[binding.alias.casefold()] = source

        def resolve(ref):
            relation = inputs.get(ref.relation.casefold())
            if relation is None or ref.column.casefold() not in relation.columns:
                issue('column_out_of_scope',where+'.'+ref.relation+'.'+ref.column)
                return Fact(frozenset())
            return relation.columns[ref.column.casefold()]

        def proves_unique(refs):
            # A single input's keys prove uniqueness only before a join.
            if len(inputs)!=1: return False
            alias, relation = next(iter(inputs.items()))
            return (all(r.relation.casefold()==alias for r in refs)
                    and any(k <= {r.column.casefold() for r in refs} for k in relation.keys))

        inherited = set().union(*(r.filters for r in inputs.values()))
        # A filtered right relation does not restrict the preserved left rows.
        if step.operation=='join' and len(step.joins)==1 and step.joins[0].kind=='left':
            left_aliases = {r.relation.casefold() for r in step.joins[0].left}
            inherited = set().union(*(inputs[a].filters for a in left_aliases if a in inputs))
        for predicate in step.filters:
            fact = resolve(predicate.column)
            if fact.representation in ('day','month','year') and predicate.operator not in ('is_null','not_null'):
                patterns = {'day':(r'\d{4}-\d{2}-\d{2}','%Y-%m-%d'),
                            'month':(r'\d{4}-\d{2}','%Y-%m'), 'year':(r'\d{4}','%Y')}
                pattern,fmt=patterns[fact.representation]
                try:
                    if not re.fullmatch(pattern,predicate.value): raise ValueError()
                    datetime.strptime(predicate.value,fmt)
                except ValueError:
                    issue('incompatible_date_literal',where+'.filters.'+predicate.id)
            expected = requirements.get(predicate.id)
            if expected is None:
                issue('undeclared_filter',where+'.filters')
            elif (fact.origins != frozenset({refkey(expected.column)})
                  or (predicate.operator,predicate.value)!=(expected.operator,expected.value)):
                issue('filter_definition_mismatch',where+'.filters.'+predicate.id)
            inherited.add(predicate.id)
            used_filters.add(predicate.id)
        # Bounds/definitions are text values, never evaluated here.
        if len(inputs)>1 and step.operation!='join': issue('join_stage_required',where)
        if step.operation=='join' and (len(inputs)!=2 or len(step.joins)!=1):
            issue('unsupported_join_shape',where)
        if step.joins and step.operation!='join': issue('unexpected_join',where)
        join_keys = set()
        for join in step.joins:
            if not join.left or len(join.left)!=len(join.right): issue('invalid_join_keys',where)
            left_aliases = {r.relation.casefold() for r in join.left}
            right_aliases = {r.relation.casefold() for r in join.right}
            if len(left_aliases)!=1 or len(right_aliases)!=1 or left_aliases==right_aliases:
                issue('invalid_join_scope',where)
            for left,right in zip(join.left,join.right):
                a,b = resolve(left),resolve(right)
                if 'unknown' in (a.representation,b.representation):
                    issue('unverified_join_representation',where,'warning')
                elif a.representation!=b.representation:
                    issue('incompatible_join_representation',where)
            def side_unique(refs):
                aliases={r.relation.casefold() for r in refs}
                if len(aliases)!=1: return False
                rel=inputs.get(next(iter(aliases)))
                return rel is not None and any(k <= {r.column.casefold() for r in refs} for k in rel.keys)
            left_unique,right_unique=side_unique(join.left),side_unique(join.right)
            if join.cardinality in ('many_to_one','one_to_one') and not right_unique:
                issue('unproved_join_cardinality',where)
            if join.cardinality in ('one_to_many','one_to_one') and not left_unique:
                issue('unproved_join_cardinality',where)
            issue('join_relationship_requires_review',where,'warning')
            if not (left_unique and right_unique): issue('join_multiplicity_requires_review',where,'warning')
            if not join.evidence.strip(): issue('missing_join_evidence',where)
            if join.cardinality=='unknown': issue('unknown_join_cardinality',where,'warning')
            # Preserve the non-multiplied side only when keys actually prove it.
            for refs,other_unique in ((join.left,right_unique),(join.right,left_unique)):
                if other_unique and len({r.relation.casefold() for r in refs})==1:
                    alias=refs[0].relation.casefold()
                    if alias in inputs and not (join.kind=='left' and refs is join.right):
                        join_keys.update(frozenset((alias,c) for c in k) for k in inputs[alias].keys)
        columns = {}
        direct = {}
        for expr in step.outputs:
            location=where+'.outputs.'+expr.name
            facts=[resolve(r) for r in expr.args]
            origins=frozenset().union(*(f.origins for f in facts))
            representation=facts[0].representation if len(facts)==1 else 'unknown'
            measure=facts[0].measure if len(facts)==1 else 'value'
            expected_arity=arity[expr.op]
            if (expected_arity is not None and len(expr.args)!=expected_arity) or (expr.op=='count' and len(expr.args)>1):
                issue('invalid_expression_arity',location)
            if expr.op in aggregates and step.operation!='aggregate': issue('aggregate_stage_required',location)
            if expr.op in ('row_number','lag'):
                if step.operation!='window' or expr.window is None: issue('window_stage_required',location)
            elif expr.window is not None: issue('unexpected_window',location)
            if expr.window:
                for ref in (*expr.window.partition_by,*expr.window.order_by): resolve(ref)
                if not expr.window.order_by: issue('missing_window_order',location)
                if expr.window.require_unique_order and not proves_unique((*expr.window.partition_by,*expr.window.order_by)):
                    issue('window_order_not_unique',location)
                if not expr.window.require_unique_order: issue('window_ties_unverified',location,'warning')
            if expr.op in ('sum','avg') and any(f.measure=='cumulative' for f in facts):
                issue('cumulative_measure_aggregation',location)
            if expr.op=='ratio':
                if expr.zero_denominator=='': issue('missing_zero_denominator_policy',location)
                if expr.unit not in ('fraction','percent'): issue('missing_ratio_unit',location)
            elif expr.zero_denominator: issue('unexpected_denominator_policy',location)
            if expr.op=='date_key':
                if not expr.date_format: issue('missing_date_format',location)
                representation=expr.date_format or 'unknown'
                issue('date_conversion_requires_sql_check',location,'warning')
            elif expr.date_format: issue('unexpected_date_format',location)
            if expr.op=='literal':
                representation='unknown'
            elif expr.literal: issue('unexpected_literal',location)
            if expr.op in ('ratio','count','row_number'): representation='number'; measure='value'
            columns[expr.name.casefold()]=Fact(origins,representation,measure)
            if expr.op=='column' and len(expr.args)==1:
                direct.setdefault(refkey(expr.args[0]),set()).add(expr.name.casefold())
        group={n.casefold() for n in step.group_by}
        if not group <= columns.keys(): issue('unknown_group_column',where)
        if step.operation=='aggregate':
            for expr in step.outputs:
                if expr.name.casefold() in group:
                    if expr.op not in ('column','date_key'): issue('invalid_group_expression',where)
                elif expr.op not in aggregates: issue('ungrouped_projection',where)
            keys={frozenset(group)}
        elif step.group_by:
            issue('unexpected_group_by',where); keys=set()
        elif step.operation=='distinct': keys={frozenset(columns)}
        else:
            source_keys=join_keys if step.operation=='join' else {
                frozenset((alias,c) for c in key) for alias,rel in inputs.items() for key in rel.keys}
            keys=set()
            for key in source_keys:
                if all(ref in direct for ref in key):
                    # One exposed alias per key component is enough for proof;
                    # additional equivalent aliases may be conservatively rejected.
                    keys.add(frozenset(sorted(direct[ref])[0] for ref in key))
        steps[step.name.casefold()]=Relation(columns,keys,inherited)
    for rule in contract.populations:
        relation=steps.get(rule.measure.relation.casefold())
        if relation is None or rule.measure.column.casefold() not in relation.columns:
            issue('unknown_population_measure','contract.populations'); continue
        if set(rule.required_filters)&set(rule.forbidden_filters): issue('contradictory_population_rule','contract.populations')
        for fid in (*rule.required_filters,*rule.forbidden_filters):
            if fid not in requirements: issue('unknown_population_filter','contract.populations')
        if not set(rule.required_filters)<=relation.filters: issue('missing_population_filter',rule.measure.relation)
        if set(rule.forbidden_filters)&relation.filters: issue('premature_population_filter',rule.measure.relation)
    reachable=set()
    pending=[plan.final_step.casefold()]
    while pending:
        name=pending.pop()
        if name not in reachable:
            reachable.add(name)
            pending.extend(dependencies.get(name,()))
    if set(steps)-reachable: issue('unused_step','steps')
    final=steps.get(plan.final_step.casefold())
    if final is None: issue('unknown_final_step','final_step')
    else:
        if not set(requirements)<=final.filters: issue('missing_final_filter','final_step')
        if set(requirements)-used_filters: issue('unused_contract_filter','filters')
        for name in (*plan.output_columns,*plan.output_grain,*(o.column for o in plan.order_by)):
            if name.casefold() not in final.columns: issue('unknown_output_column','output')
        if not {n.casefold() for n in plan.output_grain}<={n.casefold() for n in plan.output_columns}:
            issue('grain_not_exposed','output')
        if not any(k<={n.casefold() for n in plan.output_grain} for k in final.keys):
            issue('output_grain_not_proven','output_grain')
        if plan.expected_shape=='scalar' and (len(plan.output_columns)!=1 or frozenset() not in final.keys):
            issue('scalar_shape_not_proven','expected_shape')
        if plan.ties=='deterministic' and not any(k<={o.column.casefold() for o in plan.order_by} for k in final.keys):
            issue('output_order_not_unique','order_by')
    for field in ('output_columns','output_grain','order_by','ties','expected_shape'):
        if getattr(plan,field)!=getattr(contract,field): issue('output_contract_mismatch',field)
    unique(plan.output_columns,'output_columns')
    if not plan.output_columns: issue('empty_output','output_columns')
    # Population definitions, key meanings, and the future SQL translation still
    # need review even when every structural check succeeds.
    issue('natural_language_alignment_unverified','contract','warning')
    return Result(not any(i.severity=='error' for i in issues),VERSION,checksum(plan),checksum(contract),schema_hash,tuple(issues))
