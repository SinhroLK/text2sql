import os
import mysql.connector
import pandas as pd
import re
import json
import gradio as gr
from groq import Groq
from dotenv import load_dotenv
import sqlparse

# ------------------ ENV + DB ------------------
load_dotenv()  # expects DB_USER, DB_PASSWORD, DB_HOST, DB_NAME, GROQ_API_KEY in .env

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MODEL = "llama-3.3-70b-versatile"

conn = mysql.connector.connect(
    host=DB_HOST,
    user=DB_USER,
    password=DB_PASSWORD,
    database=DB_NAME,
    auth_plugin="mysql_native_password"
)
cursor = conn.cursor()
print("Connected successfully")

client = Groq(api_key=GROQ_API_KEY)

# ------------------ LOAD SCHEMA ------------------
with open("schema.json", "r") as f:
    schema = json.load(f)

def schema_to_prompt(schema_json):
    msg = f"Database ID: {schema_json['db_id']}\n"
    msg += "Tables and Columns:\n"
    for table in schema_json["tables"]:
        msg += f"- {table['table_name']}:\n"
        for col in table["columns"]:
            pk = " (PK)" if col["is_primary"] else ""
            msg += f"    - {col['column_name']} {col['data_type']}{pk}\n"
    msg += "\nForeign Keys:\n"
    for fk in schema_json["foreign_keys"]:
        msg += f"  - {fk['column']} -> {fk['references']}\n"
    return msg

SCHEMA_PROMPT = schema_to_prompt(schema)

# Collect valid table/column names for validation
valid_tables = {t["table_name"] for t in schema["tables"]}
valid_columns = {c["column_name"] for t in schema["tables"] for c in t["columns"]}

# ------------------ CORE FUNCTIONS ------------------
def groq_generate(question: str) -> str:
    """Generate SQL from natural language using Groq and format it."""
    chat_completion = client.chat.completions.create(
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a helpful assistant that converts English questions "
                    "into SQL queries. Only output the SQL query, no explanation, "
                    "no new lines.\n\n"
                    f"Here is the database schema:\n{SCHEMA_PROMPT}"
                )
            },
            {"role": "user", "content": f"Translate into SQL: {question}"}
        ],
        model=MODEL,
    )
    sql = chat_completion.choices[0].message.content.strip()
    sql = re.sub(r"```sql|```", "", sql).strip()

    # ---------- Pretty format ----------
    sql_formatted = sqlparse.format(
        sql,
        reindent=True,          # add newlines and indentation
        keyword_case="upper",   # make SQL keywords uppercase
        identifier_case="lower" # optional: lowercase table/column names
    )
    return sql_formatted

# def validate_sql(sql: str) -> bool:
#     """Check if SQL only uses valid schema tables and columns."""
#     tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", sql.lower())
#     for token in tokens:
#         if token not in valid_tables and token not in valid_columns and token not in {"select","from","where","and","or","join","on","group","by","order","limit","asc","desc","count","sum","avg","max","min","as"}:
#             return False
#     return True

def run_sql(query: str):
    """Execute SQL on MySQL and return a Pandas DataFrame."""
    try:
        cursor.execute(query)
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description] if cursor.description else []
        df = pd.DataFrame(rows, columns=cols)
        return df
    except Exception as e:
        return pd.DataFrame({"Error": [str(e)]})

def nl_to_sql_and_run(question: str):
    """Full pipeline: NL → SQL → Execute → Return results."""
    sql = groq_generate(question)
    # if not validate_sql(sql):
    #     return sql, pd.DataFrame({"Error": ["Invalid SQL: references unknown table/column"]})
    result = run_sql(sql)
    return sql, result

# ------------------ GRADIO APP ------------------
with gr.Blocks() as demo:
    gr.Markdown("# Natural Language to SQL (Groq + MySQL + Gradio)")

    with gr.Row():
        with gr.Column(scale=1):
            question_in = gr.Textbox(
                label="Ask a question (in English)", 
                placeholder="e.g. List all customers who placed an order in September 2025",
                lines= 5
            )
            submit_btn = gr.Button("Generate & Run SQL")

        with gr.Column(scale=1):
            sql_out = gr.Textbox(label="Generated SQL", lines=5)
            result_out = gr.Dataframe(label="Query Result")

    submit_btn.click(
        fn=nl_to_sql_and_run,
        inputs=[question_in],
        outputs=[sql_out, result_out]
    )

if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=7860)
