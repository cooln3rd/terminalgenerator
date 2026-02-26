import streamlit as st
import pandas as pd
import string
import os
import pyodbc  # Added for the raw connection creator
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

# --- Configuration & Base36 Logic ---
PREFIX = os.getenv("PREFIX", "2ZN1")
SUFFIX_LENGTH = 4
CHAR_SET = string.digits + string.ascii_lowercase 
BASE = len(CHAR_SET)

# --- MS SQL Connection Logic ---
def get_mssql_connection():
    """
    Directly creates a pyodbc connection to bypass SQLAlchemy/URL encoding issues
    with special characters in passwords (+, =, #).
    """
    user = os.getenv("MSSQL_USER")
    password = os.getenv("MSSQL_PASS")
    host = os.getenv("MSSQL_HOST")
    port = os.getenv("MSSQL_PORT", "1433")
    database = os.getenv("MSSQL_DB")

    conn_str = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={host},{port};"
        f"DATABASE={database};"
        f"UID={user};"
        f"PWD={password};"
        f"Encrypt=no;"  # Matches your working infrastructure
        f"TrustServerCertificate=yes;"
    )
    return pyodbc.connect(conn_str)

# Create the engine using the 'creator' parameter
engine = create_engine("mssql+pyodbc://", creator=get_mssql_connection, fast_executemany=True)

def int_to_base36_padded(num):
    if num == 0: return "0".zfill(SUFFIX_LENGTH).upper()
    arr = []
    temp_num = num
    while temp_num:
        temp_num, rem = divmod(temp_num, BASE)
        arr.append(CHAR_SET[rem])
    return "".join(reversed(arr)).upper().zfill(SUFFIX_LENGTH)

# --- UI Setup ---
st.set_page_config(page_title="Terminal Generator", layout="wide")
st.title(" Terminal ID Generator")

# Sidebar for DB Stats
with st.sidebar:
    st.header("Database Overview")
    try:
        with engine.connect() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM terminal_registry")).scalar()
            last_seq = conn.execute(text("SELECT MAX(sequence_num) FROM terminal_registry")).scalar() or 0
        st.metric("Total TIDs Generated", f"{total:,}")
        st.metric("Current Sequence", last_seq)
        
        max_cap = (BASE ** SUFFIX_LENGTH)
        st.write(f"Capacity: { (last_seq/max_cap)*100 :.2f}%")
        st.progress(min(last_seq / max_cap, 1.0))
    except Exception as e:
        st.error(f"Could not connect to MS SQL: {e}")

# --- Frontend Batch Control ---
st.subheader("Generation Settings")
col1, col2 = st.columns(2)

with col1:
    default_batch = int(os.getenv("BATCH_SIZE", 1000))
    selected_batch = st.number_input(
        "Enter Batch Size", 
        min_value=1, 
        max_value=100000, 
        value=default_batch,
        help="How many unique IDs do you want to generate in this run?"
    )

with col2:
    st.info(f"**Prefix:** {PREFIX} | **Format:** {PREFIX}XXXX")

if st.button(" Generate and Save Batch", type="primary"):
    try:
        with engine.connect() as conn:
            res = conn.execute(text("SELECT MAX(sequence_num) FROM terminal_registry")).scalar()
            current_id = res if res is not None else 0

        new_rows = []
        progress_bar = st.progress(0)
        
        for i in range(selected_batch):
            current_id += 1
            if current_id > (BASE ** SUFFIX_LENGTH) - 1:
                st.error("STOP: Maximum capacity reached!")
                break
                
            tid = f"{PREFIX}{int_to_base36_padded(current_id)}"
            new_rows.append({"terminal_id": tid, "sequence_num": current_id})
            
            if i % 1000 == 0:
                progress_bar.progress(i / selected_batch)

        if new_rows:
            df = pd.DataFrame(new_rows)
            df.to_sql('terminal_registry', engine, if_exists='append', index=False)
            st.success(f" Successfully added {len(new_rows)} IDs to MS SQL.")
            st.dataframe(df.head(100))
            
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button(" Download Batch", csv, f"TID_Batch_{current_id}.csv", "text/csv")
    except Exception as e:
        st.error(f"Action failed: {e}")
