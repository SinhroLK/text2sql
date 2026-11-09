WITH "days" AS (SELECT DISTINCT "e"."day" AS "day" FROM main."events" AS "e"),
"ordered" AS (SELECT "d"."day" AS "day", ROW_NUMBER() OVER (ORDER BY "d"."day" ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS "sequence" FROM "days" AS "d")
SELECT "day", "sequence" FROM "ordered" ORDER BY "day" ASC;
