import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model

st.set_page_config(page_title="ABM Skittles Scheduler", layout="wide")

class ABMSchedulerEngine:
    def __init__(self, num_teams, num_weeks, matches_per_pair=2):
        self.num_teams = num_teams
        self.num_weeks = num_weeks
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

    def add_hard_constraints(self):
        # 1. Total Matches
        for t1 in range(self.num_teams):
            for t2 in range(t1 + 1, self.num_teams):
                self.model.AddExactlyOne([
                    self.play[(t1, t2, w, s, a)] for w in range(self.num_weeks) 
                    for s in range(self.num_slots) for a in range(self.num_alleys)
                ])
        
        # 2. Match Frequency: Max 1 match per week per team
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

        # 3. Double Booking: Only 1 match per slot per alley
        for w in range(self.num_weeks):
            for s in range(self.num_slots):
                for a in range(self.num_alleys):
                    slot_matches = []
                    for t1 in range(self.num_teams):
                        for t2 in range(self.num_teams):
                            if t1 != t2:
                                slot_matches.append(self.play[(t1, t2, w, s, a)])
                    self.model.Add(sum(slot_matches) <= 1)

    def solve(self):
        self.add_hard_constraints()
        solver = cp_model.CpSolver()
        # Set a time limit so the website doesn't hang indefinitely
        solver.parameters.max_time_in_seconds = 30.0 
        status = solver.Solve(self.model)
        
        results = []
        if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
            for w in range(self.num_weeks):
                for s in range(self.num_slots):
                    for a in range(self.num_alleys):
                        for t1 in range(self.num_teams):
                            for t2 in range(self.num_teams):
                                if t1 != t2 and solver.Value(self.play[(t1, t2, w, s, a)]) == 1:
                                    results.append({
                                        "Week": f"Week {w + 1}",
                                        "Alley": f"Alley {a + 1}",
                                        "Slot ID": s,
                                        "Home Team": f"Team {t1 + 1}",
                                        "Away Team": f"Team {t2 + 1}"
                                    })
            return results
        else:
            return None

# --- User Interface ---
st.title("ABM Skittles Scheduler")
st.write("Welcome to the fixture generation tool. Set your parameters below.")

st.header("Division Setup")
col1, col2 = st.columns(2)
with col1:
    num_teams = st.number_input("Number of Teams", min_value=4, max_value=20, value=10)
with col2:
    matches_per_pair = st.selectbox("Matches per pair (e.g. Home & Away = 2)", [1, 2, 3, 4], index=1)

# A rough calculation for total weeks needed to fit the matches (1 match a week max)
minimum_weeks_required = (num_teams - 1) * matches_per_pair
st.info(f"Note: With {num_teams} teams playing each other {matches_per_pair} times, you need a minimum of {minimum_weeks_required} playing weeks.")

st.markdown("---")

if st.button("Generate Fixtures", type="primary"):
    with st.spinner("The optimisation engine is calculating the fairest schedule..."):
        # Initialise and run the engine
        scheduler = ABMSchedulerEngine(num_teams=int(num_teams), num_weeks=minimum_weeks_required, matches_per_pair=int(matches_per_pair))
        schedule_data = scheduler.solve()
        
        if schedule_data:
            st.success("Success! A valid schedule has been generated.")
            df = pd.DataFrame(schedule_data)
            # Sort the table to make it readable
            df = df.sort_values(by=["Week", "Slot ID", "Alley"])
            st.dataframe(df, use_container_width=True)
        else:
            st.error("No valid schedule could be found. We may need to add more playing weeks to the season.")
