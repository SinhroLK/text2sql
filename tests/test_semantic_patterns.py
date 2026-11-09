"""Small counterexamples for SQL semantics, independent of benchmark answers."""
import sqlite3
import unittest


class SemanticPatternsTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)

    def rows(self, sql):
        return self.db.execute(sql).fetchall()

    def test_snapshot_is_not_increment_and_latest_is_not_maximum(self):
        self.db.executescript('CREATE TABLE points(round INT, cumulative INT); INSERT INTO points VALUES(1,10),(2,17),(3,15);')
        self.assertEqual(self.rows('SELECT SUM(cumulative), MAX(cumulative) FROM points'), [(42,17)])
        self.assertEqual(self.rows('SELECT cumulative FROM points ORDER BY round DESC LIMIT 1'), [(15,)])
        self.assertEqual(self.rows('WITH increments AS (SELECT cumulative - LAG(cumulative,1,0) OVER(ORDER BY round) delta FROM points) SELECT SUM(delta) FROM increments'), [(15,)])

    def test_month_join_normalizes_both_sides_and_aggregates_grain(self):
        self.db.executescript("CREATE TABLE sales(day TEXT, qty INT); INSERT INTO sales VALUES('2024-01-01',3),('2024-01-20',7); CREATE TABLE months(month TEXT); INSERT INTO months VALUES('2024-01'),('2024-02');")
        self.assertEqual(self.rows('SELECT month, COALESCE(SUM(qty),0) FROM months LEFT JOIN sales ON day=month GROUP BY month'), [('2024-01',0),('2024-02',0)])
        self.assertEqual(self.rows("SELECT month, COALESCE(SUM(qty),0) FROM months LEFT JOIN sales ON strftime('%Y-%m',day)=month GROUP BY month"), [('2024-01',10),('2024-02',0)])
        self.assertEqual(self.rows("SELECT date('2024-01'), date('2024-01-01','12-31')"), [(None,None)])

    def test_final_roots_exclude_intermediate_nodes_and_keep_isolated_roots(self):
        self.db.executescript('CREATE TABLE nodes(id INT); INSERT INTO nodes VALUES(1),(2),(3),(4); CREATE TABLE edges(parent INT, child INT, qty INT); INSERT INTO edges VALUES(1,2,2),(2,3,3);')
        query = '''WITH RECURSIVE roots AS (SELECT id FROM nodes n WHERE NOT EXISTS(SELECT 1 FROM edges WHERE child=n.id)), walk(root,node,qty) AS (SELECT id,id,1 FROM roots UNION ALL SELECT root,child,w.qty*e.qty FROM walk w JOIN edges e ON e.parent=w.node) SELECT root,SUM(qty) FROM walk WHERE NOT EXISTS(SELECT 1 FROM edges WHERE parent=node) GROUP BY root'''
        self.assertEqual(self.rows(query), [(1,6),(4,1)])
        self.assertEqual(self.rows('SELECT DISTINCT parent FROM edges'), [(1,),(2,)])

    def test_fifo_preserves_interleaved_individual_order_lines(self):
        self.db.executescript("CREATE TABLE lines(seq INT, product TEXT, qty INT); INSERT INTO lines VALUES(1,'A',4),(2,'B',2),(3,'A',4); CREATE TABLE stock(product TEXT, qty INT); INSERT INTO stock VALUES('A',6),('B',2);")
        query = '''WITH ranges AS (SELECT *, SUM(qty) OVER(PARTITION BY product ORDER BY seq ROWS UNBOUNDED PRECEDING)-qty lo FROM lines) SELECT seq,MAX(0,MIN(r.qty,s.qty-lo)) allocated FROM ranges r JOIN stock s USING(product) ORDER BY seq'''
        self.assertEqual(self.rows(query), [(1,4),(2,2),(3,2)])
        self.assertEqual(self.rows('SELECT product,SUM(qty) FROM lines GROUP BY product'), [('A',8),('B',2)])
        # Aggregating A first loses which of its two lines is short.
        self.assertEqual(self.rows('SELECT AVG(pct),SUM(filled)*1.0/SUM(qty) FROM (SELECT 1.0 pct,2 filled,2 qty UNION ALL SELECT 0.5,4,8)'), [(0.75,0.6)])

    def test_pairwise_crossings_differ_from_laps_with_improvement(self):
        self.db.executescript("CREATE TABLE laps(driver TEXT, lap INT, pos INT); INSERT INTO laps VALUES('A',1,3),('B',1,1),('C',1,2),('A',2,1),('B',2,2),('C',2,3);")
        prefix = 'WITH movement AS (SELECT *,LAG(pos) OVER(PARTITION BY driver ORDER BY lap) prev FROM laps) '
        self.assertEqual(self.rows(prefix+"SELECT COUNT(*) FROM movement WHERE driver='A' AND pos<prev"), [(1,)])
        self.assertEqual(self.rows(prefix+"SELECT COUNT(*) FROM movement a JOIN movement b ON a.lap=b.lap AND a.driver<>b.driver WHERE a.driver='A' AND a.prev>b.prev AND a.pos<b.pos"), [(2,)])
        self.assertEqual(self.rows("SELECT LAG(pos) OVER(PARTITION BY driver ORDER BY lap) FROM laps WHERE lap=2 AND driver='A'"), [(None,)])
        # Crossings alone do not classify pit/retirement causation; that needs
        # event evidence and explicit interval definitions, absent in this toy.

    def test_population_multiplicity_changes_salary_average(self):
        self.db.executescript("CREATE TABLE jobs(id INT,salary INT); INSERT INTO jobs VALUES(1,100),(2,300); CREATE TABLE matches(job INT,skill TEXT); INSERT INTO matches VALUES(1,'a'),(1,'b'),(2,'a');")
        self.assertEqual(self.rows('SELECT AVG(salary) FROM jobs WHERE EXISTS(SELECT 1 FROM matches WHERE job=id)'), [(200.0,)])
        self.assertAlmostEqual(self.rows('SELECT AVG(salary) FROM jobs JOIN matches ON job=id')[0][0], 500/3)

    def test_scope_and_sqlite_distinct_window_restrictions(self):
        for query in ('SELECT city FROM (SELECT 1 AS city_en)', 'SELECT COUNT(DISTINCT x) OVER() FROM (SELECT 1 x)'):
            with self.assertRaises(sqlite3.OperationalError):
                self.rows(query)
        self.assertEqual(self.rows('WITH unique_values AS (SELECT DISTINCT x FROM (SELECT 1 x UNION ALL SELECT 1)) SELECT COUNT(*) OVER() FROM unique_values'), [(1,)])
