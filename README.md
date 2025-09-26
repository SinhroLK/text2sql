# text2sql

**Natural Language to SQL Translation Engine**

`text2sql` is a framework for converting natural language queries into SQL statements. It leverages large language models (LLMs) along with structured database schema understanding to allow querying relational databases in plain English.

## Features

- Translate natural language queries into SQL.
- Supports Groq and LLaMA models for context-aware query generation.
- Reads database schemas including tables, columns, and foreign keys.
- Optional validation to ensure SQL only references valid tables and columns.
- Execute SQL on MySQL databases and return results as Pandas DataFrames.
- Interactive web interface using Gradio.
- Extensible for preprocessing, postprocessing, and evaluation metrics.

## Architecture

The system consists of four main modules:

1. **Schema Loader**: Parses the database schema from JSON and converts it into an LLM prompt.
2. **Text-to-SQL Generator**: Uses Groq/LLM models to generate SQL queries from natural language input.
3. **SQL Executor**: Executes SQL queries on a MySQL database and returns results as Pandas DataFrames.
4. **Gradio Frontend**: Provides a web-based interface for users to input questions and view results.

## Installation

Clone the repository:

```bash
git clone https://github.com/SinhroLK/text2sql.git
cd text2sql
```
## Configuration

Create a `.env` file in the root directory with your database credentials and API key:

```env
DB_USER=your_database_user
DB_PASSWORD=your_database_password
DB_HOST=your_database_host
DB_NAME=your_database_name
GROQ_API_KEY=your_groq_api_key
```
## Usage

Follow these steps to run the text2sql application and interact with your database.

### 1. Start the Application

Run the main script:

```bash
python app.py
```

### 2. Open the Web Interface

Open your browser and navigate to:

http://127.0.0.1:7860

You will see a simple interface with:

A textbox to enter your natural language query.

A panel showing the generated SQL.

A table displaying the query results.

### 3. Enter a Query

Type a question in plain English, for example:

List all customers who placed orders over $500 in September 2025

### 4. Generate and Execute SQL

Click the Generate & Run SQL button. The workflow will:

Convert your English question into an SQL query using the LLM.

Execute the SQL query on the connected MySQL database.

Display the generated SQL in the left panel.

Show the query results in the right panel as a table.

### 5. Example Queries

Find all customers who have never placed an order.

Show top-selling products by category.

List employees earning more than the average salary in their department.

Compute total order revenue per customer.

Display orders with more than 3 items and total value over $1000.

### 6. Demo
[![Watch the video](https://img.youtube.com/vi/3njaLWPuj8Y/0.jpg)](https://www.youtube.com/watch?v=3njaLWPuj8Y)
