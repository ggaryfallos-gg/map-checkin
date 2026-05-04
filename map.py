import streamlit as st
import os
from streamlit_js_eval import get_geolocation
from streamlit_folium import st_folium
import folium
import pandas as pd
from datetime import datetime

# --- Configuration ---
st.set_page_config(page_title="Logistics Check-in", layout="centered")
DB_FILE = "checkin_log.txt"

def save_to_txt(timestamp, lat, lon):
    """Appends data to a comma-separated TXT file."""
    # Format: Timestamp, User, Latitude, Longitude
    log_entry = f"{timestamp}, GG, {lat}, {lon}\n"
    
    with open(DB_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry)

# --- UI Layout ---
st.title("📍 Mobile Check-in")
st.markdown("---")

# 1. GPS Acquisition
location = get_geolocation()

if location:
    lat = location['coords']['latitude']
    lon = location['coords']['longitude']
    
    # 2. Check-in Trigger
    if st.button("🚀 Confirm Check-in", use_container_width=True):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_to_txt(now, lat, lon)
        st.toast("Check-in logged to TXT repository.", icon="📝")

    # 3. Visualization from TXT
    if os.path.exists(DB_FILE):
        # Read TXT into DataFrame for easy mapping/display
        try:
            df = pd.read_csv(DB_FILE, names=["Timestamp", "User", "Latitude", "Longitude"], skipinitialspace=True)
            
            if not df.empty:
                st.subheader("Historical Visibility")
                
                # Map setup
                m = folium.Map(location=[lat, lon], zoom_start=14)
                
                # Plot pins from TXT
                for _, row in df.iterrows():
                    folium.Marker(
                        [row['Latitude'], row['Longitude']],
                        popup=f"{row['Timestamp']}",
                        icon=folium.Icon(color="green", icon="check", prefix="fa")
                    ).add_to(m)
                
                st_folium(m, width="100%", height=400)
                
                # Scannable Logs
                with st.expander("View Log History"):
                    st.dataframe(df.tail(10), use_container_width=True)
        except Exception as e:
            st.error(f"Error reading log file: {e}")
else:
    st.info("Waiting for GPS signal from S25 Ultra...")
