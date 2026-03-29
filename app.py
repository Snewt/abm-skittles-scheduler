import streamlit as st
import datetime

st.set_page_config(page_title="ABM Skittles Scheduler", layout="wide")

st.title("ABM Skittles Scheduler")
st.write("Welcome to the fixture generation tool.")

st.header("1. Season Parameters")
col1, col2 = st.columns(2)
with col1:
    start_date = st.date_input("Season Start Date", datetime.date(2026, 9, 14))
with col2:
    end_date = st.date_input("Target End Date", datetime.date(2027, 5, 14))

st.subheader("Blackout Dates (No Play)")
st.write("Select the start and end dates for Christmas, Easter, or hall bookings.")
blackout_start = st.date_input("Blackout Start")
blackout_end = st.date_input("Blackout End")

st.header("2. Division Setup")
num_teams = st.number_input("Number of Teams in Division", min_value=4, max_value=20, value=10)
matches_per_pair = st.selectbox("How many times do teams play each other?", [1, 2, 3, 4], index=1)

st.header("3. Smart Team Preferences")
st.write("Add specific constraints for teams here. The system will prioritise equal alley use first.")
team_name = st.text_input("Team Name (e.g., The Rollers)")
unavailable_day = st.selectbox("Cannot play on:", ["None", "Monday", "Tuesday", "Wednesday", "Thursday"])
preferred_time = st.selectbox("Prefers time slot:", ["No Preference", "8:00 pm", "9:00 pm"])

st.markdown("---")

if st.button("Generate Fixtures"):
    st.info("The optimisation engine is analysing millions of permutations...")
    # The mathematical logic we discussed previously will be injected here in our next iteration.
    st.success("Success! (Placeholder for final schedule output)")