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
            # 1. Create Registry Table
            conn.execute(text("""
                IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[terminal_registry]') AND type in (N'U'))
                BEGIN
                    CREATE TABLE terminal_registry (
                        terminal_id VARCHAR(20) PRIMARY KEY,
                        sequence_num INT NOT NULL
                    )
                END
            """))
            # 2. Create Mapping Table
            conn.execute(text("""
                IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[terminal_mappings]') AND type in (N'U'))
                BEGIN
                    CREATE TABLE terminal_mappings (
                        terminal_id VARCHAR(20) PRIMARY KEY,
                        institution VARCHAR(100),
                        product_type VARCHAR(50),
                        mid VARCHAR(50),
                        client_tid VARCHAR(50)
                    )
                END
            """))
            conn.commit()

            total = conn.execute(text("SELECT COUNT(*) FROM terminal_registry")).scalar()
            last_seq = conn.execute(text("SELECT MAX(sequence_num) FROM terminal_registry")).scalar() or 0
            
        st.metric("Total TIDs in DB", f"{total:,}")
        st.metric("Last Sequence", last_seq)
        
        max_cap = (BASE ** SUFFIX_LENGTH)
        st.write(f"Capacity: { (last_seq/max_cap)*100 :.2f}%")
        st.progress(min(last_seq / max_cap, 1.0))
    except Exception as e:
        st.error(f"Database Connection/Schema Error: {e}")

# --- PAGE 1: GENERATOR ---
if page == "TID Generator":
    st.title(" Terminal ID Generator")
    st.info(f"**Current Configuration:** Prefix: `{PREFIX}` | Format: `{PREFIX}XXXX` ")

    col1, _ = st.columns([2, 3])
    with col1:
        default_batch = int(os.getenv("BATCH_SIZE", 1000))
        selected_batch = st.number_input("Enter Batch Size", min_value=1, value=default_batch)
        generate_btn = st.button("Generate and Save Batch", type="primary", use_container_width=True)

    if generate_btn:
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
                
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df.to_excel(writer, index=False, sheet_name='Generated_TIDs')
                
                st.download_button(
                    label=" Download Excel for Client",
                    data=buffer.getvalue(),
                    file_name=f"TID_Batch_{current_id}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
                st.dataframe(df.head(100))
        except Exception as e:
            st.error(f"Action failed: {e}")

# --- PAGE 2: MIGRATION ---
elif page == "Data Migration":
    st.title(" Data Migration Module")
    st.write("Upload an Excel or CSV file to import existing Terminal IDs into the database.")
    
    with st.expander("Required File Format"):
        st.write("The file must contain at least these two columns:")
        st.code("terminal_id, sequence_num")

    uploaded_file = st.file_uploader("Choose a file", type=['csv', 'xlsx'])

    if uploaded_file:
        try:
            df_mig = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            st.write("### Preview of Uploaded Data")
            st.dataframe(df_mig.head(10))

            if st.button("Confirm and Import to MS SQL", type="primary", use_container_width=True):
                required = {'terminal_id', 'sequence_num'}
                if not required.issubset(df_mig.columns):
                    st.error(f"Missing columns! Required: {required}")
                else:
                    with st.spinner("Writing to Database..."):
                        df_mig.to_sql('terminal_registry', engine, if_exists='append', index=False)
                    st.success(f"Successfully migrated {len(df_mig)} records!")
                    st.balloons()
        except Exception as e:
            st.error(f"Migration Error: {e}")

# --- PAGE 3: UPLOAD MAPPINGS ---
elif page == "Upload Mappings":
    st.title(" Upload Client Mappings")
    st.write("Upload the feedback file from the institution to link generated TIDs to their specific MIDs and business details.")
    
    with st.expander("Expected Excel/CSV Columns"):
        st.write("The file should include: `terminal_id`, `institution`, `product_type`, `mid`, `client_tid`")

    uploaded_file = st.file_uploader("Upload Client Feedback File", type=['csv', 'xlsx'])
    if uploaded_file:
        try:
            df_map = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            st.write("### Mapping Preview")
            st.dataframe(df_map.head())

            if st.button("Save Client Mappings", type="primary", use_container_width=True):
                # Standardize headers (strip spaces, lowercase) to match SQL table
                df_map.columns = [c.lower().replace(" ", "_").strip() for c in df_map.columns]
                
                with st.spinner("Syncing mappings to SQL Server..."):
                    # if_exists='append' allows adding multiple batches from different clients
                    df_map.to_sql('terminal_mappings', engine, if_exists='append', index=False)
                st.success(" Client mappings linked successfully!")
        except Exception as e:
            st.error(f"Mapping Upload Failed: {e}")

# --- PAGE 4: REGISTRY SEARCH ---
elif page == "Registry Search":
    st.title(" Registry & Mapping Lookup")
    search_tid = st.text_input("Enter Terminal ID (TID) to find details:").strip()
    
    if search_tid:
        # Join registry and mappings to show the full picture
        query = text("""
            SELECT r.terminal_id, r.sequence_num, m.institution, m.product_type, m.mid, m.client_tid
            FROM terminal_registry r
            LEFT JOIN terminal_mappings m ON r.terminal_id = m.terminal_id
            WHERE r.terminal_id = :tid
        """)
        with engine.connect() as conn:
            result = conn.execute(query, {"tid": search_tid}).fetchone()
            
        if result:
            res_col1, res_col2 = st.columns(2)
            with res_col1:
                st.subheader(" System Registry")
                st.write(f"**ID:** {result[0]}")
                st.write(f"**Sequence:** {result[1]}")
            with res_col2:
                st.subheader(" Client Mapping")
                if result[2]: # If institution is not null
                    st.write(f"**Institution:** {result[2]}")
                    st.write(f"**Product:** {result[3]}")
                    st.write(f"**MID:** {result[4]}")
                    st.write(f"**Client TID:** {result[5]}")
                else:
                    st.info("No client mapping data has been uploaded for this ID yet.")
        else:
            st.error("This Terminal ID does not exist in the registry.")
