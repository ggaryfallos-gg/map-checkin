import streamlit as st
import pandas as pd
import time
from datetime import datetime
from utils import gr_num, geocode_address

def show_admin_dashboard(all_data, conn, LOG_URL):
    st.title("Admin Control Tower")
    tab_a, tab_b, tab_c = st.tabs(["📈 Planning", "📦 Παραλαβές", "📡 Logs"])

    with tab_a:
        st.header("Capacity Planning")
        if not all_data.empty:
            raw_p = all_data['Truck License Plate'].dropna().unique().tolist()
            sel_trucks = st.multiselect("🚚 Στόλος", sorted([str(p) for p in raw_p]), default=sorted([str(p) for p in raw_p])[:3])
            plan_df = all_data[all_data['Truck License Plate'].isin(sel_trucks)]
            pivot = plan_df.pivot_table(index='Truck License Plate', columns='Loading_Date', values='Total KG', aggfunc='sum').fillna(0)
            st.dataframe(pivot.style.background_gradient(cmap='YlOrRd', axis=None).format(lambda x: f"{gr_num(x, 0)} kg"), use_container_width=True)

    with tab_b:
        st.header("Διαχείριση Παραλαβών Προμηθευτών")
        with st.expander("➕ Νέα Καταχώρηση"):
            with st.form("admin_pickup"):
                name = st.text_input("Προμηθευτής")
                addr = st.text_input("Διεύθυνση")
                if st.form_submit_button("Υποβολή"):
                    # Logic για update στο Supplier_Pickups tab
                    st.success("Καταχωρήθηκε!")
