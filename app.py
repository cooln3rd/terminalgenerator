import streamlit as st
import pandas as pd
import string
import os
import pyodbc
import io
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
        f"Encrypt=no;"
        f"TrustServerCertificate=yes;"
    )
    return pyodbc.connect(conn_str)

engine = create_engine("mssql+pyodbc://", creator=get_mssql_connection, fast_executemany=True)

def int_to_base36_padded(num):
    if num == 0: return "0".zfill(SUFFIX_LENGTH).upper()
    arr = []
    temp_num = num
    while temp_num:
        temp_num, rem = divmod(temp_num, BASE)
        arr.append(CHAR_SET[rem])
    return "".join(reversed(arr)).upper().zfill(SUFFIX_LENGTH)

# --- Navigation ---
st.set_page_config(page_title="Terminal Manager", layout="wide")
page = st.sidebar.radio("Navigation", ["TID Generator", "Data Migration", "Upload Mappings", "Registry Search"])

# --- Global Sidebar Stats & Bootstrapping ---
with st.sidebar:
    st.divider()
    st.header("Database Overview")
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[terminal_registry]') AND type in (N'U'))
                CREATE TABLE terminal_registry (terminal_id VARCHAR(20) PRIMARY KEY, sequence_num INT NOT NULL)
                
                IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[terminal_mappings]') AND type in (N'U'))
                CREATE TABLE terminal_mappings (
                    terminal_id VARCHAR(20) PRIMARY KEY,
                    institution VARCHAR(100),
                    product_type VARCHAR(50),
                    mid VARCHAR(50),
                    client_tid VARCHAR(50)
                )
            """))
            conn.commit()
            total = conn.execute(text("SELECT COUNT(*) FROM terminal_registry")).scalar()
            last_seq = conn.execute(text("SELECT MAX(sequence_num) FROM terminal_registry")).scalar() or 0
            
        st.metric("Total TIDs in DB", f"{total:,}")
        st.metric("Last Sequence", last_seq)
    except Exception as e:
        st.error(f"DB Error: {e}")

# --- PAGE: TID GENERATOR ---
if page == "TID Generator":
    st.title(" Terminal ID Generator")
    st.info(f"**Current Configuration:** Prefix: `{PREFIX}` | Format: `{PREFIX}XXXX` ")
    col1, _ = st.columns([2, 3])
    with col1:
        selected_batch = st.number_input("Enter Batch Size", min_value=1, value=1000)
        if st.button("Generate and Save Batch", type="primary", use_container_width=True):
            try:
                with engine.connect() as conn:
                    res = conn.execute(text("SELECT MAX(sequence_num) FROM terminal_registry")).scalar()
                    current_id = res if res is not None else 0
                new_rows = []
                for i in range(selected_batch):
                    current_id += 1
                    tid = f"{PREFIX}{int_to_base36_padded(current_id)}"
                    new_rows.append({"terminal_id": tid, "sequence_num": current_id})
                df = pd.DataFrame(new_rows)
                df.to_sql('terminal_registry', engine, if_exists='append', index=False)
                st.success(f"Added {len(new_rows)} IDs.")
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df.to_excel(writer, index=False)
                st.download_button(" Download Excel", buffer.getvalue(), f"TID_Batch_{current_id}.xlsx", use_container_width=True)
            except Exception as e:
                st.error(f"Error: {e}")

# --- PAGE: DATA MIGRATION ---
elif page == "Data Migration":
    st.title(" Data Migration Module")
    uploaded_file = st.file_uploader("Upload Registry Data", type=['csv', 'xlsx'])
    if uploaded_file:
        try:
            df_mig = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            st.dataframe(df_mig.head(10))
            if st.button("Import to Registry", type="primary", use_container_width=True):
                df_mig[['terminal_id', 'sequence_num']].to_sql('terminal_registry', engine, if_exists='append', index=False)
                st.success("Migration complete!")
        except Exception as e:
            st.error(f"Error: {e}")

# --- PAGE: UPLOAD MAPPINGS ---
elif page == "Upload Mappings":
    st.title(" Upload Client Mappings")
    
    st.warning("""
    ###  Upload Instructions
    1. **Terminal ID**: Must match an existing registry entry.
    2. **Required Mapping Info**: Ensure your file contains columns for **Institution**, **Product Type**, **MID**, and **Client TID**.
    3. **Upsert Logic**: If the Terminal ID exists, the record will be **updated** with the new info.
    """)

    uploaded_file = st.file_uploader("Upload Client Feedback File", type=['csv', 'xlsx'])
    if uploaded_file:
        try:
            df_map = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            
            # Flexible Header Mapping
            header_map = {
                'terminal_id': ['terminal_id', 'tid', 'id', 'terminal_id'],
                'institution': ['institution', 'bank', 'client', 'institution'],
                'product_type': ['product_type', 'product', 'type', 'product_type'],
                'mid': ['mid', 'merchant_id', 'mid'],
                'client_tid': ['client_tid', "client's_tid", 'external_tid', 'client_tid']
            }
            
            # Clean current headers
            df_map.columns = [c.lower().replace(" ", "_").strip() for c in df_map.columns]
            
            final_mapping = {}
            for std_name, variations in header_map.items():
                for col in df_map.columns:
                    if col in variations:
                        final_mapping[col] = std_name
            
            df_map = df_map.rename(columns=final_mapping)
            valid_cols = ['terminal_id', 'institution', 'product_type', 'mid', 'client_tid']
            df_to_save = df_map[[c for c in valid_cols if c in df_map.columns]].copy()
            
            st.write("### Review Parsed Data")
            st.dataframe(df_to_save.head())

            if st.button("Save/Update Mappings", type="primary", use_container_width=True):
                with engine.begin() as conn:
                    # Staging
                    df_to_save.to_sql('temp_mappings', conn, if_exists='replace', index=False)
                    # MERGE (Upsert)
                    conn.execute(text("""
                        MERGE INTO terminal_mappings AS t
                        USING temp_mappings AS s ON t.terminal_id = s.terminal_id
                        WHEN MATCHED THEN UPDATE SET 
                            t.institution=s.institution, 
                            t.product_type=s.product_type, 
                            t.mid=s.mid, 
                            t.client_tid=s.client_tid
                        WHEN NOT MATCHED THEN INSERT (terminal_id, institution, product_type, mid, client_tid)
                        VALUES (s.terminal_id, s.institution, s.product_type, s.mid, s.client_tid);
                    """))
                    conn.execute(text("DROP TABLE temp_mappings"))
                st.success(f"Processed {len(df_to_save)} records!")
        except Exception as e:
            st.error(f"Mapping Upload Failed: {e}")

# --- PAGE: REGISTRY SEARCH ---
elif page == "Registry Search":
    st.title(" Registry & Mapping Lookup")
    search_val = st.text_input("Enter Terminal ID or Client TID:").strip()
    
    if search_val:
        query = text("""
            SELECT r.terminal_id AS [Terminal ID], r.sequence_num AS [Sequence], 
                   m.institution AS [Institution], m.product_type AS [Product Type], 
                   m.mid AS [MID], m.client_tid AS [Client TID]
            FROM terminal_registry r
            LEFT JOIN terminal_mappings m ON r.terminal_id = m.terminal_id
            WHERE r.terminal_id = :val OR m.client_tid = :val
        """)
        with engine.connect() as conn:
            result_df = pd.read_sql(query, conn, params={"val": search_val})
            
        if not result_df.empty:
            st.subheader(" Search Result")
            st.table(result_df)
        else:
            st.error("No record found matching that ID.")
    
    st.divider()
    st.subheader(" Recently Mapped Terminals")
    try:
        recent_query = """
            SELECT TOP 10 
                terminal_id AS [Terminal ID], 
                institution AS [Institution], 
                product_type AS [Product Type],
                mid AS [MID], 
                client_tid AS [Client TID]
            FROM terminal_mappings
            WHERE institution IS NOT NULL OR mid IS NOT NULL OR client_tid IS NOT NULL
            ORDER BY terminal_id DESC
        """
        recent_df = pd.read_sql(recent_query, engine)
        if not recent_df.empty:
            st.dataframe(recent_df, use_container_width=True, hide_index=True)
        else:
            st.info("No mappings found.")
    except Exception as e:
        st.write("Could not load preview.")
