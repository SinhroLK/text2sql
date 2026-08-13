PRAGMA foreign_keys = ON;

CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE
);

CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    order_date TEXT NOT NULL,
    status TEXT NOT NULL,
    total_amount REAL NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

INSERT INTO customers (customer_id, first_name, last_name, email) VALUES
    (1, 'Alice', 'Smith', 'alice@example.test'),
    (2, 'Bob', 'Jones', 'bob@example.test');

INSERT INTO orders (order_id, customer_id, order_date, status, total_amount) VALUES
    (1, 1, '2026-01-15', 'delivered', 120.50),
    (2, 2, '2026-02-01', 'pending', 75.00);

