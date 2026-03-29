import streamlit as st
import pandas as pd
import datetime
from ortools.sat.python import cp_model

st.set_page_config(page_title="ABM Skittles Scheduler", layout="wide")

# --- Mappings ---
SLOT_NAMES = {
    0: "Monday 8:00 pm", 1: "Monday 9:00 pm",
    2: "Tuesday 8:00 pm", 3: "Tuesday 9:00 pm",
    4: "Wednesday 8:00 pm", 5: "Wednesday 9:00 pm",
    6: "Thursday 8:00 pm", 7: "Thursday 9:00 pm"
}

DAY_TO_SLOTS = {
    "None": [],
    "Monday": [0, 1],
    "Tuesday": [2, 3],
    "Wednesday": [4, 5],
    "Thursday": [6, 7]
}

class ABMSchedulerEngine:
    def __init__(self, team_data, play_weeks, matches_per_pair=2):
        self.team_data = team_data # DataFrame of teams and preferences
        self.num_teams = len(team_data)
        self.play_weeks = play_weeks # List of actual dates (Mondays of playing weeks)
        self.num_weeks = len(play_weeks)
        self.matches_per_pair = matches_per_pair
        self.num_slots = 8 
        self.num_alleys = 2
        
        self.model = cp_model.CpModel()
        self.play = {}
        self.create_variables()

    def create_variables(self):
        for t1 in range(self.num_teams):
            for t2 in range(self.num_teams):
                if t1 != t2:
                    for w in range(self.num_weeks):
                        for s in range(self.num_slots):
                            for a in range(self.num_alleys):
                                self.play[(t1, t2, w, s, a)] = self.model.NewBoolVar(
                                    f'match_t{t1}_t{t2}_w{w}_s{s}_a{a}'
                                )

    def add_constraints(self):
        # 1. Total Matches
        for t1 in range(self.num_teams):
            for t2 in range(t1 + 1, self.num_teams):
                self.model.Add(sum(
                    self.play[(t1, t2, w, s, a)] + self.play[(t2, t1, w, s, a)]
                    for w in range(self.num_weeks) for s in range(self.num_slots) for a in range(self.num_alleys)
                ) == self.matches_per_pair)
        
        # 2. Match Frequency (Max 1 per week per team)
        for t in range(self.num_teams):
            for w in range(self.num_weeks):
                weekly_matches = []
                for t2 in range(self.num_teams):
                    if t != t2:
                        for s in range(self.num_slots):
                            for a in range(self.num_alleys):
                                weekly_matches.append(self.play[(t, t2, w, s, a)])
                                weekly_matches.append(self.play[(t2, t, w, s, a)])
                self.model.Add(sum(weekly_matches) <= 1)

        # 3. Double Booking (Max 1 match per slot per alley)
        for w in range(self.num_weeks):
            for s in range(self.num_slots):
                for a in range(self.num_alleys):
                    slot_matches = []
                    for t1 in range(self.num_teams):
                        for t2 in range(self.num_teams):
                            if t1 != t2:
                                slot_matches.append(self.play[(t1, t2, w, s, a)])
                    self.model.Add(sum(slot_matches) <= 1)

        # 4. TEAM PREFERENCES: Block unavailable days
        for t, row in self.team_data.iterrows():
            unavailable_day = row['Cannot Play On']
            blocked_slots = DAY_TO_SLOTS.get(unavailable_day, [])
            
            if blocked_slots:
                for t2 in range(self.num_teams):
                    if t != t2:
                        for w in range(self.num_weeks):
                            for s in blocked_slots:
                                for a in range(self.num_alleys):
                                    # Force these specific match variables to be 0 (cannot happen)
                                    self.model.Add(self.play[(t, t2, w, s, a)] == 0)
                                    self.model.Add(self.play[(t2, t, w, s, a)] == 0)

    def solve(self):
        self.add_constraints()
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 45.0 
        status = solver.Solve(self.model)
        
        results = []
        if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
            for w in range(self.num_weeks):
                week_date = self.play_weeks[w].strftime("%d %b %Y")
                for s in range(self.num_slots):
                    for a in range(self.num_alleys):
                        for t1 in range(self.num_teams):
                            for t2 in range(self.num_teams):
                                if t1 != t2 and solver.Value(self.play[(t1, t2, w, s, a)]) == 1:
                                    # Get actual team names
                                    home_team = self.team_data.iloc[t1]['Team Name']
                                    away_team = self.team_data.iloc[t2]['Team Name']
                                    
                                    results.append({
                                        "Week Commencing": week_date,
                                        "Match Time": SLOT_NAMES[s],
                                        "Alley": f"Alley {a + 1}",
                                        "Home Team": home_team,
                                        "Away Team": away_team
                                    })
            return results
        else:
            return None

def calculate_playing_weeks(start_date, end_date, xmas_start, xmas_end, easter_start, easter_end):
    weeks = []
    current_date = start_date
    # Adjust to the nearest Monday
    current_date = current_date - datetime.timedelta(days=current_date.weekday())
    
    while current_date <= end_date:
        # Check if this week falls in a blackout period
        in_xmas = xmas_start <= current_date <= xmas_end
        in_easter = easter_start <= current_date <= easter_end
        
        if not in_xmas and not in_easter:
            weeks.append(current_date)
        current_date += datetime.timedelta(days=7)
    return weeks

# --- User Interface ---
st.title("ABM Skittles Scheduler")

st.header("1. Season Calendar")
col1, col2 = st.columns(2)
with col1:
    season_start = st.date_input("Season Start (W/C)", datetime.date(2026, 9, 14))
    xmas_start = st.date_input("Xmas Break Start", datetime.date(2026, 12, 21))
    easter_start = st.date_input("Easter Break Start", datetime.date(2027, 3, 22))
with col2:
    season_end = st.date_input("Season Target End", datetime.date(2027, 5, 14))
    xmas_end = st.date_input("Xmas Break End", datetime.date(2027, 1, 3))
    easter_end = st.date_input("Easter Break End", datetime.date(2027, 4, 4))

available_weeks = calculate_playing_weeks(season_start, season_end, xmas_start, xmas_end, easter_start, easter_end)
st.info(f"Based on your dates, there are **{len(available_weeks)} playable weeks** available.")

st.header("2. Teams & Preferences")
st.write("Edit the table below. You can change team names and set days they cannot play.")

# Default team data
default_teams = pd.DataFrame({
    "Team Name": [f"Team {i+1}" for i in range(10)],
    "Cannot Play On": ["None"] * 10
})

# Interactive spreadsheet
edited_teams = st.data_editor(
    default_teams,
    column_config={
        "Cannot Play On": st.column_config.SelectboxColumn(
            "Cannot Play On",
            help="Select a day this team cannot play.",
            options=["None", "Monday", "Tuesday", "Wednesday", "Thursday"],
            required=True
        )
    },
    num_rows="dynamic",
    use_container_width=True
)

st.markdown("---")

if st.button("Generate Fixtures", type="primary"):
    # Safety check
    if len(available_weeks) < (len(edited_teams) - 1) * 2:
        st.error("Not enough playable weeks to complete the season! Adjust your calendar dates.")
    else:
        with st.spinner("Calculating the smartest schedule... This may take up to 45 seconds."):
            scheduler = ABMSchedulerEngine(edited_teams, available_weeks, matches_per_pair=2)
            schedule_data = scheduler.solve()
            
            if schedule_data:
                st.success("Success! Here are your fixtures.")
                df = pd.DataFrame(schedule_data)
                st.dataframe(df, use_container_width=True)
            else:
                st.error("The engine couldn't find a schedule that fits all these rules. Try removing some 'Cannot Play On' restrictions.")
