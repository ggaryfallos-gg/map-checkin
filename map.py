import streamlit as st
from streamlit_js_eval import get_geolocation
from streamlit_folium import st_folium
import folium
import pandas as pd
from datetime import datetime

# Page Config for Mobile
st.set_page_config(page_title="Logistics Check-in", layout="centered")

st.title("📍 Mobile Check-in System")

# Initialize session state for check-in history
if 'check_in_history' not in st.session_state:
    st.session_state.check_in_history = []

# 1. Capture Location (Triggers Mobile GPS)
location = get_geolocation()

if location:
    curr_lat = location['coords']['latitude']
    curr_lon = location['coords']['longitude']
    
    st.success(f"GPS Signal Locked")
    
    # 2. Check-in Action
    if st.button("Confirm Check-in", use_container_width=True):
        new_entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "lat": curr_lat,
            "lon": curr_lon
        }
        st.session_state.check_in_history.append(new_entry)
        st.toast("Check-in recorded successfully!", icon="✅")

    # 3. Visualization Logic
    if st.session_state.check_in_history:
        st.subheader("Check-in Map")
        
        # Create Map centered on the latest check-in
        m = folium.Map(location=[curr_lat, curr_lon], zoom_start=15)
        
        # Add markers for all history
        for entry in st.session_state.check_in_history:
            folium.Marker(
                [entry['lat'], entry['lon']],
                popup=f"Time: {entry['time']}",
                icon=folium.Icon(color="green", icon="check", prefix="fa")
            ).add_to(m)
        
        # Render Map
        st_folium(m, width="100%", height=400)
        
        # 4. Data Table for Review
        with st.expander("View Raw Logs"):
            st.table(pd.DataFrame(st.session_state.check_in_history))
            
else:
    st.info("Waiting for GPS permission... Please enable 'Location' on your S25 Ultra browser.")