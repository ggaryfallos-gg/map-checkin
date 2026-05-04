import streamlit as st
import pandas as pd
import os
from streamlit_js_eval import get_geolocation
from streamlit_folium import st_folium
import folium
from datetime import datetime

# --- Configuration & Styling ---
st.set_page_config(page_title="Logistics Check-in Terminal", layout="centered")
DB_FILE = "logistics_checkin_database.xlsx"

def save_to_excel(new_row):
    """Handles Excel I/O with high data integrity."""
    if os.path.exists(DB_FILE):
        df = pd.read_excel(DB_FILE)
        df = pd.concat([df, new_row], ignore_index=True)
    else:
        df = new_row
    
    # Save with professional formatting (Engine: openpyxl)
    with pd.ExcelWriter(DB_FILE, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Check-in Logs')

# --- UI Header ---
st.title("📍 Executive Check-in")
st.markdown("---")

# 1. Location Acquisition
location = get_geolocation()

if location:
    lat = location['coords']['latitude']
    lon = location['coords']['longitude']
    
    # 2. Check-in Interface
    st.success("GPS Signal: High Accuracy")
    
    if st.button("🚀 Confirm Check-in", use_container_width=True):
        # Create Data Entry
        entry = pd.DataFrame([{
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "User": "GG",
            "Latitude": lat,
            "Longitude": lon,
            "Status": "Verified"
        }])
        
        save_to_excel(entry)
        st.toast("Check-in saved to local repository!", icon="💾")

    # 3. Dynamic Visualization
    if os.path.exists(DB_FILE):
        st.subheader("Historical Visibility")
        
        # Load data for mapping
        df_history = pd.read_excel(DB_FILE)
        
        if not df_history.empty:
            # Map centered on current location
            m = folium.Map(location=[lat, lon], zoom_start=14)
            
            # Add all historical pins
            for _, row in df_history.iterrows():
                folium.Marker(
                    [row['Latitude'], row['Longitude']],
                    popup=f"Time: {row['Timestamp']}",
                    icon=folium.Icon(color="blue", icon="location-dot", prefix="fa")
                ).add_to(m)
            
            st_folium(m, width="100%", height=450)
            
            # 4. Efficiency Metrics (Summary)
            with st.expander("Database Statistics"):
                st.write(f"Total entries logged: **{len(df_history)}**")
                st.dataframe(df_history.tail(5), use_container_width=True)
else:
    st.info("Awaiting GPS coordinates. Ensure Location Services are active on your S25 Ultra.")
