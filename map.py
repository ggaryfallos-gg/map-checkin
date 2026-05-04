import streamlit as st
import pandas as pd
import os
from streamlit_js_eval import get_geolocation
from streamlit_folium import st_folium
import folium
from datetime import datetime

# --- Configuration ---
st.set_page_config(page_title="Logistics Check-in Terminal", layout="centered")
DB_FILE = "logistics_checkin_database.xlsx"

def save_to_excel(new_row):
    """Saves check-in data to the local Excel repository using openpyxl."""
    if os.path.exists(DB_FILE):
        # Load existing data to append to it
        df_existing = pd.read_excel(DB_FILE, engine='openpyxl')
        df_final = pd.concat([df_existing, new_row], ignore_index=True)
    else:
        df_final = new_row
    
    # Write to file with specific engine
    with pd.ExcelWriter(DB_FILE, engine='openpyxl') as writer:
        df_final.to_excel(writer, index=False, sheet_name='Check-in Logs')

# --- UI Layout ---
st.title("📍 Mobile Logistics Terminal")
st.info("Direct-entry mode: Records GPS coordinates to repository.")

# 1. Hardware Integration (GPS)
location = get_geolocation()

if location:
    lat = location['coords']['latitude']
    lon = location['coords']['longitude']
    
    # 2. Check-in Trigger
    if st.button("🚀 Confirm Check-in", use_container_width=True):
        # Create professional data record
        entry = pd.DataFrame([{
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "User": "GG",
            "Latitude": lat,
            "Longitude": lon,
            "Status": "Verified"
        }])
        
        save_to_excel(entry)
        st.toast("Check-in successfully logged to Excel.", icon="✅")

    # 3. Visualization & Historical Data
    if os.path.exists(DB_FILE):
        df_history = pd.read_excel(DB_FILE, engine='openpyxl')
        
        if not df_history.empty:
            st.subheader("Check-in Map")
            
            # Map centered on the most recent location
            m = folium.Map(location=[lat, lon], zoom_start=14)
            
            # Populate Map with Pins from the Excel DB
            for _, row in df_history.iterrows():
                folium.Marker(
                    [row['Latitude'], row['Longitude']],
                    popup=f"Time: {row['Timestamp']}",
                    tooltip=f"User: {row['User']}",
                    icon=folium.Icon(color="blue", icon="info-sign")
                ).add_to(m)
            
            st_folium(m, width="100%", height=400)
            
            # 4. Data Scannability
            with st.expander("📊 Recent Logs"):
                st.dataframe(df_history.tail(10), use_container_width=True)
else:
    st.warning("Waiting for GPS signal. Please allow location access on your Samsung S25 Ultra.")
