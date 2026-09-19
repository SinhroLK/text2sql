PRAGMA foreign_keys = ON;

CREATE TABLE departments (
    department_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE employees (
    employee_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    department_id INTEGER NOT NULL,
    email TEXT NOT NULL,
    ssn TEXT NOT NULL,
    salary REAL NOT NULL,
    api_token TEXT NOT NULL,
    FOREIGN KEY (department_id) REFERENCES departments(department_id)
);

INSERT INTO departments (department_id, name) VALUES
    (1, 'Engineering'),
    (2, 'Operations');

-- All values are synthetic and reserved for tests.
INSERT INTO employees (
    employee_id, name, department_id, email, ssn, salary, api_token
) VALUES
    (1, 'Alice Example', 1, 'alice@example.test', '000-00-0001', 70000, 'test-token-1'),
    (2, 'Bob Example', 2, 'bob@example.test', '000-00-0002', 65000, 'test-token-2');
