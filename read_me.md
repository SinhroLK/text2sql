text2sql

Natural Language to SQL Translation Engine

text2sql is a comprehensive framework for converting natural language queries into SQL statements. By combining advanced large language models (LLMs) with structured database schema understanding, it allows users to query relational databases in plain English. This project is suitable for building conversational database assistants, BI tools, or educational SQL interfaces.

Features

Natural Language to SQL Translation: Translate user questions expressed in plain English into syntactically correct SQL queries.

Large Language Model Integration: Supports state-of-the-art LLaMA and Groq models for context-aware query generation.

Schema-Aware Querying: Reads and interprets database schemas, including tables, columns, and foreign keys, to generate accurate SQL.

Query Validation: Optional validation to ensure generated SQL only references valid tables and columns.

Execution on Live Databases: Execute generated SQL on MySQL databases and return results as Pandas DataFrames.

Interactive Web Interface: Uses Gradio to provide an accessible, web-based interface for inputting queries and viewing results.

Extensible Pipeline: Easily integrates additional preprocessing, postprocessing, or evaluation metrics.

Architecture Overview

The system is composed of three main modules:

Schema Loader: Parses the database schema from JSON format and converts it into a prompt suitable for LLMs.

Text-to-SQL Generator: Sends natural language queries and schema information to a Groq/LLM model to generate SQL statements.

SQL Executor: Connects to a MySQL database, executes the generated SQL, and returns the results as a structured Pandas DataFrame.

Gradio Frontend: Provides an intuitive interface where users can type questions and immediately see both the generated SQL and query results.

Installation

Clone the repository:

git clone https://github.com/SinhroLK/text2sql.git
cd text2sql


Install dependencies:

pip install -r requirements.txt

Configuration

Create a .env file in the root directory with your credentials and API key:

DB_USER=your_database_user
DB_PASSWORD=your_database_password
DB_HOST=your_database_host
DB_NAME=your_database_name
GROQ_API_KEY=your_groq_api_key


Ensure your MySQL database is accessible and contains the necessary tables and relationships.

Usage

Start the application:

python app.py


Open your browser and go to:

http://127.0.0.1:7860

Steps:

Enter a question in plain English (e.g., "List all customers who have placed orders over $500 this month").

Click Generate & Run SQL.

The generated SQL will be displayed in the left panel.

Query results will be shown in the right panel as a table.

Example Queries

Find all customers with orders in the last month.

List employees earning more than the average salary in their department.

Show the top-selling products in each category.

Identify customers who have never placed an order.

Compute total order revenue per customer.

Extending the System

Custom LLMs: Swap out Groq/LLaMA models with other compatible language models.

Additional Preprocessing: Add text normalization, tokenization, or entity recognition before query generation.

Query Postprocessing: Format SQL, check for safety, or optimize queries before execution.

Evaluation: Integrate token-level F1, BLEU, or exact match metrics for benchmarking model performance.

Contributing

We welcome contributions! Please:

Fork the repository.

Create a new branch for your feature or bug fix.

Submit a pull request describing your changes.

License

This project is licensed under the MIT License. See the LICENSE
 file for details.