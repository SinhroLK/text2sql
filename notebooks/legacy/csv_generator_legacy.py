import pandas as pd

data = [
    # Customers
    ("Show all customer names and emails", 
     "SELECT first_name, last_name, email FROM customers"),
    
    ("List phone numbers of all customers", 
     "SELECT phone FROM customers"),
    
    ("Find the email of customer named Alice Smith", 
     "SELECT email FROM customers WHERE first_name = 'Alice' AND last_name = 'Smith'"),
    
    # Products
    ("Get all product names and prices", 
     "SELECT name, price FROM products"),
    
    ("Which products belong to the Electronics category?", 
     "SELECT name FROM products WHERE category = 'Electronics'"),
    
    ("List products that cost more than 500", 
     "SELECT name, price FROM products WHERE price > 500"),
    
    # Orders
    ("Show all orders with their status", 
     "SELECT order_id, status FROM orders"),
    
    ("Find all orders placed by customer with ID 1", 
     "SELECT * FROM orders WHERE customer_id = 1"),
    
    ("Get order IDs and dates for delivered orders", 
     "SELECT order_id, order_date FROM orders WHERE status = 'Delivered'"),
    
    # Joins: Orders + Customers
    ("List all customer names with their order status", 
     "SELECT c.first_name, c.last_name, o.status FROM customers c JOIN orders o ON c.customer_id = o.customer_id"),
    
    ("Which customers placed orders in September 2025?", 
     "SELECT DISTINCT c.first_name, c.last_name FROM customers c JOIN orders o ON c.customer_id = o.customer_id WHERE o.order_date LIKE '2025-09%'"),
    
    # Order Items
    ("Get all products and quantities for order 1", 
     "SELECT p.name, oi.quantity FROM order_items oi JOIN products p ON oi.product_id = p.product_id WHERE oi.order_id = 1"),
    
    ("List total quantity of items per order", 
     "SELECT order_id, SUM(quantity) AS total_qty FROM order_items GROUP BY order_id"),
    
    # Employees
    ("Show all employees and their departments", 
     "SELECT first_name, last_name, department FROM employees"),
    
    ("List employees with salary above 5000", 
     "SELECT first_name, last_name FROM employees WHERE salary > 5000"),
    
    # Departments
    ("Get all departments and their locations", 
     "SELECT name, location FROM departments"),
    
    ("Which department is located in Chicago?", 
     "SELECT name FROM departments WHERE location = 'Chicago'"),
    
    # Join Employees + Departments
    ("List employees with their department location", 
     "SELECT e.first_name, e.last_name, d.location FROM employees e JOIN departments d ON e.department = d.name")
]

df = pd.DataFrame(data, columns=["text_query", "sql_command"])
df.to_csv("synthetic_text2sql.csv", index=False)

import pandas as pd

# New complex questions and queries
extra_data = [
    (
        "Which customers ordered more than 2 different products?",
        "SELECT c.first_name, c.last_name, COUNT(DISTINCT oi.product_id) AS product_count "
        "FROM customers c "
        "JOIN orders o ON c.customer_id = o.customer_id "
        "JOIN order_items oi ON o.order_id = oi.order_id "
        "GROUP BY c.customer_id "
        "HAVING COUNT(DISTINCT oi.product_id) > 2"
    ),
    (
        "List the total amount spent by each customer, ordered by amount descending.",
        "SELECT c.first_name, c.last_name, SUM(oi.quantity * p.price) AS total_spent "
        "FROM customers c "
        "JOIN orders o ON c.customer_id = o.customer_id "
        "JOIN order_items oi ON o.order_id = oi.order_id "
        "JOIN products p ON oi.product_id = p.product_id "
        "GROUP BY c.customer_id "
        "ORDER BY total_spent DESC"
    ),
    (
        "Show the most expensive product in each category.",
        "SELECT category, name, price "
        "FROM products p1 "
        "WHERE price = ( "
        "    SELECT MAX(p2.price) FROM products p2 WHERE p2.category = p1.category)"
    ),
    (
        "Which employees earn more than the average salary of their department?",
        "SELECT e.first_name, e.last_name, e.department, e.salary "
        "FROM employees e "
        "JOIN departments d ON e.department = d.name "
        "WHERE e.salary > ( "
        "    SELECT AVG(e2.salary) "
        "    FROM employees e2 "
        "    WHERE e2.department = e.department)"
    ),
    (
        "Find customers who have never placed an order.",
        "SELECT c.first_name, c.last_name "
        "FROM customers c "
        "LEFT JOIN orders o ON c.customer_id = o.customer_id "
        "WHERE o.order_id IS NULL"
    ),
    (
        "Which products were ordered the most times (by total quantity)?",
        "SELECT p.name, SUM(oi.quantity) AS total_quantity "
        "FROM products p "
        "JOIN order_items oi ON p.product_id = oi.product_id "
        "GROUP BY p.product_id "
        "ORDER BY total_quantity DESC"
    ),
    (
        "Get the number of orders for each order status.",
        "SELECT status, COUNT(*) AS order_count "
        "FROM orders "
        "GROUP BY status"
    ),
    (
        "Find employees working in departments located in New York.",
        "SELECT e.first_name, e.last_name, e.department "
        "FROM employees e "
        "JOIN departments d ON e.department = d.name "
        "WHERE d.location = 'New York'"
    ),
    (
        "Which customers ordered Electronics products in September 2025?",
        "SELECT DISTINCT c.first_name, c.last_name "
        "FROM customers c "
        "JOIN orders o ON c.customer_id = o.customer_id "
        "JOIN order_items oi ON o.order_id = oi.order_id "
        "JOIN products p ON oi.product_id = p.product_id "
        "WHERE p.category = 'Electronics' "
        "AND o.order_date LIKE '2025-09%'"
    ),
    (
        "Show average product price per category, sorted highest to lowest.",
        "SELECT category, AVG(price) AS avg_price "
        "FROM products "
        "GROUP BY category "
        "ORDER BY avg_price DESC"
    ),
    (
        "List customers and their total number of orders, but only if they have more than 1 order.",
        "SELECT c.first_name, c.last_name, COUNT(o.order_id) AS total_orders "
        "FROM customers c "
        "JOIN orders o ON c.customer_id = o.customer_id "
        "GROUP BY c.customer_id "
        "HAVING COUNT(o.order_id) > 1"
    ),
    (
        "Which employees earn above 5500 and are not in IT?",
        "SELECT first_name, last_name, department, salary "
        "FROM employees "
        "WHERE salary > 5500 AND department <> 'IT'"
    ),
    (
        "For each customer, list their most recent order date.",
        "SELECT c.first_name, c.last_name, MAX(o.order_date) AS latest_order "
        "FROM customers c "
        "JOIN orders o ON c.customer_id = o.customer_id "
        "GROUP BY c.customer_id"
    ),
    (
        "What is the average quantity of products per order?",
        "SELECT AVG(total_items) AS avg_items_per_order "
        "FROM ( "
        "    SELECT order_id, SUM(quantity) AS total_items "
        "    FROM order_items "
        "    GROUP BY order_id "
        ") t"
    ),
    (
        "List the departments along with the number of employees in each.",
        "SELECT d.name AS department, COUNT(e.employee_id) AS employee_count "
        "FROM departments d "
        "LEFT JOIN employees e ON e.department = d.name "
        "GROUP BY d.department_id"
    ),
]

# Append to CSV
df_extra = pd.DataFrame(extra_data, columns=["text_query", "sql_command"])
df_extra.to_csv("synthetic_text2sql.csv", mode="a", header=False, index=False)

print("✅ Added", len(extra_data), "new examples to synthetic_text2sql.csv")
