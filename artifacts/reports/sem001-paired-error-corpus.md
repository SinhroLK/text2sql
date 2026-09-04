# SEM-001 paired semantic-error analysis

- Analysis: `sem001-paired-semantic-errors-v1`
- Config SHA-256: `6a863f548d05b3ffdc02d18a9042f5c2ef860ca0d073fdcdeb3497f97806e6de`
- Corpus JSONL SHA-256: `b7a90912244ab3b648f97aceefcc730b27b25dbe6df3a52e2941a230d26cfe2f`
- Scope: development (31 examples)
- B5: 4 correct / 27 failures
- Provider calls: 0
- Gold SQL used: no
- Spider2 test examples used: 0

## Frozen inputs

| Arm | Predictions SHA-256 | Report SHA-256 |
|---|---|---|
| B1 | `b85afc0bf08bc513eb639cad2e2fc05e151d6b7a9f2907c2746e7dfd163ea7b9` | `296f9725977faa88bd53236221d32402d1c0c849611c36c31405d313230ac375` |
| B6R | `46664f819ce0d8faa2fe377babda3a6555df38477e0570415df146345df26577` | `aaa130ec8e2f606058c413ac9434f49b9af618ec2fb5da45214f4896bd7802fc` |
| B4 | `d64cd4f5e74cecce79c4aa465b8c762246b25241b5ec2e9f7b850b8f3c7bc580` | `0d2e995ebc7ce2e63b45b8416f301d84794cfe10ece35ee3a0739bccb3c5307a` |
| B5 | `47fd965671ad8ae6a65c9e007649c4065c68803d10ce9f9e0cb114020ae697e7` | `878be5ed4fb0c6b96c2195ef16cf4bd19350c0a308eb58dc29aa91c408499f9e` |

## Dominant B5 failure categories

| Rank | Primary category | Failures |
|---:|---|---:|
| 1 | aggregation_or_grouping | 6 |
| 2 | output_shape | 5 |
| 3 | join_path_or_cardinality | 4 |

All primary-category counts:

| Category | Failures |
|---|---:|
| aggregation_or_grouping | 6 |
| output_shape | 5 |
| join_path_or_cardinality | 4 |
| date_time_or_window | 3 |
| filter_or_literal | 3 |
| recursion_or_set_operation | 3 |
| table_or_column | 3 |

## Paired 31-example matrix

| ID | DB | B1 | B6R | B4 | B5 | Behavior | Primary | Secondary | Rationale |
|---|---|---|---|---|---|---|---|---|---|
| local009 | Airlines | result_mismatch | result_mismatch | generated_execution_error | result_mismatch | stable_failure | table_or_column | aggregation_or_grouping | Uses aircraft range as route distance instead of deriving the distance between the departure and destination airports. |
| local010 | Airlines | generated_execution_error | result_mismatch | generated_execution_error | result_mismatch | stable_failure | aggregation_or_grouping | ordering_limit_or_ties | Returns a hard-coded zero and omits city-pair averaging, distance bucketing, and selection of the least-populated bucket. |
| local068 | city_legislation | correct | generated_execution_error | correct | generated_execution_error | prompt_sensitive | date_time_or_window | sqlite_dialect_or_syntax | Nests SUM as an argument to LAG in the same SELECT level, which SQLite rejects as window-function misuse. |
| local070 | city_legislation | result_mismatch | result_mismatch | result_mismatch | result_mismatch | stable_failure | date_time_or_window | output_shape | Builds streaks over duplicate city rows rather than distinct dates, so row numbers split the wrong islands and do not guarantee one representative city per date. |
| local071 | city_legislation | result_mismatch | result_mismatch | result_mismatch | result_mismatch | stable_failure | filter_or_literal | date_time_or_window | Never restricts the source rows to June 2022, so the longest-streak calculation is performed over the complete date history. |
| local072 | city_legislation | result_mismatch | result_mismatch | result_mismatch | result_mismatch | stable_failure | output_shape | date_time_or_window | Returns only country, run length, and a ratio while omitting the evaluated distinct-day, streak-boundary, row-count, and capital-count fields. |
| local167 | city_legislation | result_mismatch | result_mismatch | result_mismatch | result_mismatch | stable_failure | join_path_or_cardinality | date_time_or_window | Groups every represented state rather than anchoring each legislator to the state of the first term, and its broad date predicates do not test service on an actual December 31. |
| local168 | city_legislation | result_mismatch | result_mismatch | result_mismatch | result_mismatch | stable_failure | join_path_or_cardinality | aggregation_or_grouping | The direct skill join repeats a salary once for every matching top-three skill, producing a skill-weighted average instead of one salary contribution per qualifying posting. |
| local169 | city_legislation | result_mismatch | generated_execution_error | generated_execution_error | result_mismatch | stable_failure | output_shape | date_time_or_window | Omits total-cohort and retained-legislator counts and reports a fraction rather than the evaluated percentage; retention is also based only on the first term row. |
| local170 | city_legislation | result_mismatch | generated_execution_error | generated_execution_error | generated_execution_error | stable_failure | table_or_column | date_time_or_window | References nonexistent legislators_terms.gender instead of joining the legislator gender source, so the query cannot execute. |
| local171 | city_legislation | generated_execution_error | correct | result_mismatch | result_mismatch | prompt_sensitive | date_time_or_window | join_path_or_cardinality | Computes elapsed years from each active term rather than from each legislator's first term and does not explicitly evaluate service on December 31. |
| local202 | city_legislation | correct | correct | correct | correct | stable_correct |  |  |  |
| local244 | music | result_mismatch | result_mismatch | result_mismatch | result_mismatch | stable_failure | aggregation_or_grouping | output_shape | Adds genre to the grouping and output, yielding 60 genre-length rows instead of the three requested duration categories. |
| local269 | oracle_sql | result_mismatch | result_mismatch | result_mismatch | result_mismatch | stable_failure | recursion_or_set_operation | join_path_or_cardinality | Seeds every packaging relation as a root, so nested containers are treated as final top-level combinations when leaf quantities are averaged. |
| local270 | oracle_sql | correct | correct | result_mismatch | result_mismatch | prompt_sensitive | recursion_or_set_operation | aggregation_or_grouping | Carries only the edge quantity during recursion rather than multiplying quantities across hierarchy levels, leaving every accumulated item total below the intended threshold. |
| local272 | oracle_sql | generated_execution_error | result_mismatch | correct | result_mismatch | prompt_sensitive | join_path_or_cardinality | aggregation_or_grouping | Joins each picking line to every inventory row for the product instead of its allocated location and never performs the required cumulative FIFO allocation. |
| local273 | oracle_sql | result_mismatch | result_mismatch | result_mismatch | result_mismatch | stable_failure | aggregation_or_grouping | output_shape | Averages picked-line quantity divided by order-line quantity rather than computing FIFO overlap ranges before aggregation, and its product output does not match the evaluated identifier shape. |
| local274 | oracle_sql | correct | correct | result_mismatch | correct | prompt_sensitive |  |  |  |
| local275 | oracle_sql | correct | correct | correct | correct | stable_correct |  |  |  |
| local277 | oracle_sql | result_mismatch | result_mismatch | result_mismatch | result_mismatch | stable_failure | aggregation_or_grouping | date_time_or_window | Computes a simple historical average and omits the required seasonality adjustment, time-step window, weighted regression, and 2018 annual forecast. |
| local279 | oracle_sql | result_mismatch | generated_execution_error | generated_execution_error | result_mismatch | stable_failure | recursion_or_set_operation | table_or_column | Initializes recursion from only the single inventory row with the global maximum ID, so most products never enter the monthly inventory simulation. |
| local286 | electronic_sales | result_mismatch | result_mismatch | generated_execution_error | result_mismatch | stable_failure | output_shape | join_path_or_cardinality | Omits total quantity sold, reports packing duration in days instead of hours, and uses joins/aggregates that admit two extra seller rows. |
| local309 | f1 | result_mismatch | result_mismatch | result_mismatch | result_mismatch | stable_failure | output_shape | ordering_limit_or_ties | Returns driver and constructor point totals in addition to the requested year, driver name, and constructor name, so the 75-row result has the wrong projection. |
| local310 | f1 | generated_execution_error | correct | generated_execution_error | generated_execution_error | prompt_sensitive | table_or_column | aggregation_or_grouping | The outer query references r.year although only the agg subquery exists in that scope, causing an execution error before yearly maxima can be compared. |
| local311 | f1 | generated_execution_error | generated_execution_error | correct | result_mismatch | prompt_sensitive | aggregation_or_grouping | output_shape | Combines MAX driver-standing points with a sum of repeated constructor-standing rows instead of deriving each year's best driver and team totals, and emits constructor IDs rather than names. |
| local335 | f1 | result_mismatch | result_mismatch | result_mismatch | result_mismatch | stable_failure | aggregation_or_grouping | join_path_or_cardinality | Finds constructors with the lowest constructor-wide points, while the question first requires the lowest point-scoring drivers per season and then their constructors. |
| local336 | f1 | result_mismatch | result_mismatch | result_mismatch | result_mismatch | stable_failure | output_shape | aggregation_or_grouping | Returns one hard-coded four-column zero row instead of deriving one category/count row for each of the four overtake causes. |
| local344 | f1 | result_mismatch | result_mismatch | result_mismatch | result_mismatch | stable_failure | join_path_or_cardinality | date_time_or_window | Joins every lap position to every pit stop in the race, multiplying events, and does not correctly establish the previous-lap ahead/behind pair before classifying the change. |
| local354 | f1 | result_mismatch | result_mismatch | result_mismatch | correct | prompt_sensitive |  |  |  |
| local355 | f1 | result_mismatch | generated_execution_error | result_mismatch | result_mismatch | stable_failure | filter_or_literal | join_path_or_cardinality | Filters short participation spans rather than gaps of fewer than three missed races and omits the required constructor switch across each hiatus. |
| local356 | f1 | result_mismatch | result_mismatch | generated_execution_error | result_mismatch | stable_failure | filter_or_literal | join_path_or_cardinality | Excludes current-lap pit stops only; pit exits on the following lap and retirement-related changes remain, so non-track position movements dominate the count. |

Development-only diagnostic labels are engineering evidence. They do not use or reconstruct protected gold SQL and do not replace EVAL-003.
