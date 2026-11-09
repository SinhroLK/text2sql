"""Deterministic lowering of the supported plan subset; never executes SQL.

The independent SQL security validator is mandatory even after plan validation.
"""
from .models import dumps, loads, SemanticPlan
from .validation import validate
from text2sql.security import SQLiteSQLValidator


def _identifier(value):
    return '"'+value.replace('"','""')+'"'


def _literal(value):
    return "'"+value.replace("'","''")+"'"


def compile_plan(plan, schema, contract):
    receipt=validate(plan,schema,contract)
    if not receipt.valid:
        raise ValueError('Semantic plan rejected: '+','.join(sorted({i.code for i in receipt.issues if i.severity=='error'})))
    plan=loads(dumps(plan),SemanticPlan)
    def ref(value): return _identifier(value.relation)+'.'+_identifier(value.column)
    def expression(e):
        args=[ref(r) for r in e.args]
        if e.op=='column': return args[0]
        if e.op=='literal': return _literal(e.literal)
        if e.op=='number': return '('+e.literal+')'  # Validator accepts a bounded decimal grammar only.
        if e.op in ('add','subtract','multiply'):
            return '('+args[0]+{'add': ' + ', 'subtract':' - ', 'multiply':' * '}[e.op]+args[1]+')'
        if e.op=='abs': return 'ABS('+args[0]+')'
        if e.op=='case':
            op={'eq':'=','ne':'<>','lt':'<','le':'<=','gt':'>','ge':'>='}[e.comparison]
            return '(CASE WHEN '+args[0]+' '+op+' '+args[1]+' THEN '+args[2]+' ELSE '+args[3]+' END)'
        if e.op=='days_between': return '(julianday('+args[0]+') - julianday('+args[1]+'))'
        if e.op=='date_shift': return 'date('+args[0]+', '+_literal(f'{e.days:+d} days')+')'
        if e.op in ('min','max','sum','avg','count'):
            return e.op.upper()+'('+(', '.join(args) if args else '*')+')'
        if e.op=='date_key':
            return 'strftime('+_literal({'day':'%Y-%m-%d','month':'%Y-%m','year':'%Y'}[e.date_format])+', '+args[0]+')'
        if e.op=='ratio':
            if e.zero_denominator=='error':
                raise ValueError('SQLite lowering does not support error-on-zero ratios')
            value=('100.0' if e.unit=='percent' else '1.0')+' * '+args[0]+' / NULLIF('+args[1]+', 0)'
            return 'COALESCE('+value+', 0.0)' if e.zero_denominator=='zero' else value
        if e.op in ('row_number','lag','window_sum','window_avg'):
            clauses=[]
            if e.window.partition_by: clauses.append('PARTITION BY '+', '.join(ref(r) for r in e.window.partition_by))
            clauses.append('ORDER BY '+', '.join(ref(r)+' '+d.upper() for r,d in zip(e.window.order_by,e.window.directions or ('asc',)*len(e.window.order_by))))
            def boundary(n):
                if n is None: return 'UNBOUNDED PRECEDING'
                if n==0: return 'CURRENT ROW'
                return str(abs(n))+(' PRECEDING' if n<0 else ' FOLLOWING')
            clauses.append(e.window.frame.upper()+' BETWEEN '+boundary(e.window.start)+' AND '+boundary(e.window.end))
            return e.op.removeprefix('window_').upper()+'('+', '.join(args)+') OVER ('+' '.join(clauses)+')'
        raise ValueError('Unsupported expression')
    def source(binding):
        # Schema-qualify physical tables so CTE names cannot shadow them.
        return ('main.' if binding.kind=='table' else '')+_identifier(binding.source)+' AS '+_identifier(binding.alias)
    ctes=[]
    for step in plan.steps:
        sql='SELECT '+('DISTINCT ' if step.operation=='distinct' else '')
        sql+=', '.join(expression(e)+' AS '+_identifier(e.name) for e in step.outputs)
        if step.operation=='join':
            join=step.joins[0]
            bindings={i.alias.casefold():i for i in step.inputs}
            sql+=' FROM '+source(bindings[join.left[0].relation.casefold()])
            sql+=' '+join.kind.upper()+' JOIN '+source(bindings[join.right[0].relation.casefold()])
            sql+=' ON '+' AND '.join(ref(a)+' = '+ref(b) for a,b in zip(join.left,join.right))
        else:
            sql+=' FROM '+source(step.inputs[0])
        predicates=[]
        for p in step.filters:
            if p.operator in ('is_null','not_null'):
                predicates.append(ref(p.column)+(' IS NULL' if p.operator=='is_null' else ' IS NOT NULL'))
            else:
                op={'eq':'=','ne':'<>','lt':'<','le':'<=','gt':'>','ge':'>='}[p.operator]
                predicates.append(ref(p.column)+' '+op+' '+_literal(p.value))
        if predicates: sql+=' WHERE '+' AND '.join(predicates)
        if step.group_by:
            outputs={e.name.casefold():e for e in step.outputs}
            sql+=' GROUP BY '+', '.join(expression(outputs[n.casefold()]) for n in step.group_by)
        ctes.append(_identifier(step.name)+' AS ('+sql+')')
    sql='WITH '+',\n'.join(ctes)+'\nSELECT '+', '.join(_identifier(n) for n in plan.output_columns)
    sql+=' FROM '+_identifier(plan.final_step)
    if plan.order_by:
        sql+=' ORDER BY '+', '.join(_identifier(o.column)+' '+o.direction.upper() for o in plan.order_by)
    sql+=';'
    SQLiteSQLValidator().validate(sql,schema).require_valid()
    return sql
