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

class ABMSchedulerEngine:
    def __init__(self, team_data, play_weeks, matches_per_pair=2):
        self.team_data = team_data 
        self.num_teams = len(team_data)
        self.play_weeks = play_weeks 
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

    def block_slots_for_team(self, team_idx, slots_to_block):
        for t2 in range(self.num_teams):
            if team_idx != t2:
                for w in range(self.num_weeks):
                    for s in slots_to_block:
                        for a in range(self.num_alleys):
                            self.model.Add(self.play[(team_idx, t2, w, s, a)] == 0)
                            self.model.Add(self.play[(t2, team_idx, w, s, a)] == 0)

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

        # 4. Equal Alley Usage
        for t in range(self.num_teams):
            alley_0_matches = []
            alley_1_matches = []
            for t2 in range(self.num_teams):
                if t != t2:
                    for w in range(self.num_weeks):
                        for s in range(self.num_slots):
                            alley_0_matches.extend([self.play[(t, t2, w, s, 0)], self.play[(t2, t, w, s, 0)]])
                            alley_1_matches.extend([self.play[(t, t2, w, s, 1)], self.play[(t2, t, w, s, 1)]])
            
            self.model.Add(sum(alley_0_matches) - sum(alley_1_matches) <= 1)
            self.model.Add(sum(alley_1_matches) - sum(alley_0_matches) <= 1)

        # 5. Hard Availability Constraints
        for t, row in self.team_data.iterrows():
            if not row['Play Mon']: self.block_slots_for_team(t, [0, 1])
            if not row['Play Tue']: self.block_slots_for_team(t, [2, 3])
            if not row['Play Wed']: self.block_slots_for_team(t, [4, 5])
            if not row['Play Thu']: self.block_slots_for_team(t, [6, 7])
            if not row['Play 8pm']: self.block_slots_for_team(t, [0, 2, 4, 6])
            if not row['Play 9pm']: self.block_slots_for_team(t, [1, 3, 5, 7])

        # 6. Soft Preferences (Minimise non-preferred slots)
        penalties = []
        for t, row in self.team_data.iterrows():
            pref = row['Prefers Time']
            if pref == "8:00 pm":
                for t2 in range(self.num_teams):
                    if t != t2:
                        for w in range(self.num_weeks):
                            for s in [1, 3, 5, 7]:
                                for a in range(self.num_alleys):
                                    penalties.extend([self.play[(t, t2, w, s, a)], self.play[(t2, t, w, s, a)]])
            elif pref == "9:00 pm":
                for t2 in range(self.num_teams):
                    if t != t2:
                        for w in range(self.num_weeks):
                            for s in [0, 2, 4, 6]:
                                for a in range(self.num_alleys):
                                    penalties.extend([self.play[(t, t2, w, s, a)], self.play[(t2, t, w, s, a)]])
        
        self.model.Minimize(sum(penalties))

    def solve(self):
        self.add_constraints()
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 60.0 
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
                                    results.append({
                                        "Week Commencing": week_date,
                                        "Match Time": SLOT_NAMES[s],
                                        "Alley": f"Alley {a + 1}",
                                        "Home Team": self.team_data.iloc[t1]['Team Name'],
                                        "Away Team": self.team_data.iloc[t2]['Team Name']
                                    })
            return results
        else:
            return None

def calculate_playing_weeks(start_date, end_date, xmas_start, xmas_end, easter_start, easter_end):
    weeks = []
    current_date = start_date
    current_date = current_date - datetime.timedelta(days=current_date.weekday())
    
    while current_date <= end_date:
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
st.info(f"Playable weeks available: **{len(available_weeks)}**")

st.header("2. Teams & Preferences")
st.write("Untick the boxes for days/times a team **cannot** play. Set a soft preference in the final column.")

default_teams = pd.DataFrame({
    "Team Name": [f"Team {i+1}" for i in range(10)],
    "Play Mon": [True] * 10,
    "Play Tue": [True] * 10,
    "Play Wed": [True] * 10,
    "Play Thu": [True] * 10,
    "Play 8pm": [True] * 10,
    "Play 9pm": [True] * 10,
    "Prefers Time": ["No Preference"] * 10
})

edited_teams = st.data_editor(
    default_teams,
    column_config={
        "Prefers Time": st.column_config.SelectboxColumn(
            "Prefers Time",
            options=["No Preference", "8:00 pm", "9:00 pm"]
        )
    },
    num_rows="dynamic",
    use_container_width=True
)

st.markdown("---")

if st.button("Generate Fixtures", type="primary"):
    if len(available_weeks) < (len(edited_teams) - 1) * 2:
        st.error("Not enough playable weeks to complete the season!")
    else:
        with st.spinner("Optimising schedule (this may take up to 60 seconds)..."):
            scheduler = ABMSchedulerEngine(edited_teams, available_weeks, matches_per_pair=2)
            schedule_data = scheduler.solve()
            
            if schedule_data:
                st.success("Success! Here are your fixtures.")
                df = pd.DataFrame(schedule_data)
                st.dataframe(df, use_container_width=True)
            else:
                st.error("The engine couldn't find a schedule. Try relaxing some team restrictions.")
