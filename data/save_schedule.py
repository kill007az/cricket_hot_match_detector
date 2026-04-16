import json
from datetime import datetime, timezone, timedelta

TEAM_ABBR = {
    "Royal Challengers Bengaluru": "RCB",
    "Sunrisers Hyderabad": "SRH",
    "Mumbai Indians": "MI",
    "Kolkata Knight Riders": "KKR",
    "Rajasthan Royals": "RR",
    "Chennai Super Kings": "CSK",
    "Punjab Kings": "PBKS",
    "Gujarat Titans": "GT",
    "Lucknow Super Giants": "LSG",
    "Delhi Capitals": "DC",
}

raw = [
    (1,"28-MAR-26","Sat","7:30 PM","Royal Challengers Bengaluru","Sunrisers Hyderabad","Bengaluru"),
    (2,"29-MAR-26","Sun","7:30 PM","Mumbai Indians","Kolkata Knight Riders","Mumbai"),
    (3,"30-MAR-26","Mon","7:30 PM","Rajasthan Royals","Chennai Super Kings","Guwahati"),
    (4,"31-MAR-26","Tue","7:30 PM","Punjab Kings","Gujarat Titans","New Chandigarh"),
    (5,"01-APR-26","Wed","7:30 PM","Lucknow Super Giants","Delhi Capitals","Lucknow"),
    (6,"02-APR-26","Thu","7:30 PM","Kolkata Knight Riders","Sunrisers Hyderabad","Kolkata"),
    (7,"03-APR-26","Fri","7:30 PM","Chennai Super Kings","Punjab Kings","Chennai"),
    (8,"04-APR-26","Sat","3:30 PM","Delhi Capitals","Mumbai Indians","Delhi"),
    (9,"04-APR-26","Sat","7:30 PM","Gujarat Titans","Rajasthan Royals","Ahmedabad"),
    (10,"05-APR-26","Sun","3:30 PM","Sunrisers Hyderabad","Lucknow Super Giants","Hyderabad"),
    (11,"05-APR-26","Sun","7:30 PM","Royal Challengers Bengaluru","Chennai Super Kings","Bengaluru"),
    (12,"06-APR-26","Mon","7:30 PM","Kolkata Knight Riders","Punjab Kings","Kolkata"),
    (13,"07-APR-26","Tue","7:30 PM","Rajasthan Royals","Mumbai Indians","Guwahati"),
    (14,"08-APR-26","Wed","7:30 PM","Delhi Capitals","Gujarat Titans","Delhi"),
    (15,"09-APR-26","Thu","7:30 PM","Kolkata Knight Riders","Lucknow Super Giants","Kolkata"),
    (16,"10-APR-26","Fri","7:30 PM","Rajasthan Royals","Royal Challengers Bengaluru","Guwahati"),
    (17,"11-APR-26","Sat","3:30 PM","Punjab Kings","Sunrisers Hyderabad","New Chandigarh"),
    (18,"11-APR-26","Sat","7:30 PM","Chennai Super Kings","Delhi Capitals","Chennai"),
    (19,"12-APR-26","Sun","3:30 PM","Lucknow Super Giants","Gujarat Titans","Lucknow"),
    (20,"12-APR-26","Sun","7:30 PM","Mumbai Indians","Royal Challengers Bengaluru","Mumbai"),
    (21,"13-APR-26","Mon","7:30 PM","Sunrisers Hyderabad","Rajasthan Royals","Hyderabad"),
    (22,"14-APR-26","Tue","7:30 PM","Chennai Super Kings","Kolkata Knight Riders","Chennai"),
    (23,"15-APR-26","Wed","7:30 PM","Royal Challengers Bengaluru","Lucknow Super Giants","Bengaluru"),
    (24,"16-APR-26","Thu","7:30 PM","Mumbai Indians","Punjab Kings","Mumbai"),
    (25,"17-APR-26","Fri","7:30 PM","Gujarat Titans","Kolkata Knight Riders","Ahmedabad"),
    (26,"18-APR-26","Sat","3:30 PM","Royal Challengers Bengaluru","Delhi Capitals","Bengaluru"),
    (27,"18-APR-26","Sat","7:30 PM","Sunrisers Hyderabad","Chennai Super Kings","Hyderabad"),
    (28,"19-APR-26","Sun","3:30 PM","Kolkata Knight Riders","Rajasthan Royals","Kolkata"),
    (29,"19-APR-26","Sun","7:30 PM","Punjab Kings","Lucknow Super Giants","New Chandigarh"),
    (30,"20-APR-26","Mon","7:30 PM","Gujarat Titans","Mumbai Indians","Ahmedabad"),
    (31,"21-APR-26","Tue","7:30 PM","Sunrisers Hyderabad","Delhi Capitals","Hyderabad"),
    (32,"22-APR-26","Wed","7:30 PM","Lucknow Super Giants","Rajasthan Royals","Lucknow"),
    (33,"23-APR-26","Thu","7:30 PM","Mumbai Indians","Chennai Super Kings","Mumbai"),
    (34,"24-APR-26","Fri","7:30 PM","Royal Challengers Bengaluru","Gujarat Titans","Bengaluru"),
    (35,"25-APR-26","Sat","3:30 PM","Delhi Capitals","Punjab Kings","Delhi"),
    (36,"25-APR-26","Sat","7:30 PM","Rajasthan Royals","Sunrisers Hyderabad","Jaipur"),
    (37,"26-APR-26","Sun","3:30 PM","Gujarat Titans","Chennai Super Kings","Ahmedabad"),
    (38,"26-APR-26","Sun","7:30 PM","Lucknow Super Giants","Kolkata Knight Riders","Lucknow"),
    (39,"27-APR-26","Mon","7:30 PM","Delhi Capitals","Royal Challengers Bengaluru","Delhi"),
    (40,"28-APR-26","Tue","7:30 PM","Punjab Kings","Rajasthan Royals","New Chandigarh"),
    (41,"29-APR-26","Wed","7:30 PM","Mumbai Indians","Sunrisers Hyderabad","Mumbai"),
    (42,"30-APR-26","Thu","7:30 PM","Gujarat Titans","Royal Challengers Bengaluru","Ahmedabad"),
    (43,"01-MAY-26","Fri","7:30 PM","Rajasthan Royals","Delhi Capitals","Jaipur"),
    (44,"02-MAY-26","Sat","7:30 PM","Chennai Super Kings","Mumbai Indians","Chennai"),
    (45,"03-MAY-26","Sun","3:30 PM","Sunrisers Hyderabad","Kolkata Knight Riders","Hyderabad"),
    (46,"03-MAY-26","Sun","7:30 PM","Gujarat Titans","Punjab Kings","Ahmedabad"),
    (47,"04-MAY-26","Mon","7:30 PM","Mumbai Indians","Lucknow Super Giants","Mumbai"),
    (48,"05-MAY-26","Tue","7:30 PM","Delhi Capitals","Chennai Super Kings","Delhi"),
    (49,"06-MAY-26","Wed","7:30 PM","Sunrisers Hyderabad","Punjab Kings","Hyderabad"),
    (50,"07-MAY-26","Thu","7:30 PM","Lucknow Super Giants","Royal Challengers Bengaluru","Lucknow"),
    (51,"08-MAY-26","Fri","7:30 PM","Delhi Capitals","Kolkata Knight Riders","Delhi"),
    (52,"09-MAY-26","Sat","7:30 PM","Rajasthan Royals","Gujarat Titans","Jaipur"),
    (53,"10-MAY-26","Sun","3:30 PM","Chennai Super Kings","Lucknow Super Giants","Chennai"),
    (54,"10-MAY-26","Sun","7:30 PM","Royal Challengers Bengaluru","Mumbai Indians","Raipur"),
    (55,"11-MAY-26","Mon","7:30 PM","Punjab Kings","Delhi Capitals","Dharamshala"),
    (56,"12-MAY-26","Tue","7:30 PM","Gujarat Titans","Sunrisers Hyderabad","Ahmedabad"),
    (57,"13-MAY-26","Wed","7:30 PM","Royal Challengers Bengaluru","Kolkata Knight Riders","Raipur"),
    (58,"14-MAY-26","Thu","7:30 PM","Punjab Kings","Mumbai Indians","Dharamshala"),
    (59,"15-MAY-26","Fri","7:30 PM","Lucknow Super Giants","Chennai Super Kings","Lucknow"),
    (60,"16-MAY-26","Sat","7:30 PM","Kolkata Knight Riders","Gujarat Titans","Kolkata"),
    (61,"17-MAY-26","Sun","3:30 PM","Punjab Kings","Royal Challengers Bengaluru","Dharamshala"),
    (62,"17-MAY-26","Sun","7:30 PM","Delhi Capitals","Rajasthan Royals","Delhi"),
    (63,"18-MAY-26","Mon","7:30 PM","Chennai Super Kings","Sunrisers Hyderabad","Chennai"),
    (64,"19-MAY-26","Tue","7:30 PM","Rajasthan Royals","Lucknow Super Giants","Jaipur"),
    (65,"20-MAY-26","Wed","7:30 PM","Kolkata Knight Riders","Mumbai Indians","Kolkata"),
    (66,"21-MAY-26","Thu","7:30 PM","Chennai Super Kings","Gujarat Titans","Chennai"),
    (67,"22-MAY-26","Fri","7:30 PM","Sunrisers Hyderabad","Royal Challengers Bengaluru","Hyderabad"),
    (68,"23-MAY-26","Sat","7:30 PM","Lucknow Super Giants","Punjab Kings","Lucknow"),
    (69,"24-MAY-26","Sun","3:30 PM","Mumbai Indians","Rajasthan Royals","Mumbai"),
    (70,"24-MAY-26","Sun","7:30 PM","Kolkata Knight Riders","Delhi Capitals","Kolkata"),
]

def parse_dt(date_str, time_str):
    dt = datetime.strptime(f"{date_str} {time_str}", "%d-%b-%y %I:%M %p")
    return dt.strftime("%Y-%m-%dT%H:%M:00+05:30")

matches = []
for num, date, day, time, home, away, venue in raw:
    matches.append({
        "match": num,
        "date": date,
        "day": day,
        "time_ist": time,
        "datetime_ist": parse_dt(date, time),
        "home": home,
        "home_abbr": TEAM_ABBR[home],
        "away": away,
        "away_abbr": TEAM_ABBR[away],
        "venue": venue,
    })

out = {"season": 2026, "total_matches": len(matches), "matches": matches}
with open("e:/Personal Projects/cricket_hot_match_detector/data/ipl_2026_schedule.json", "w") as f:
    json.dump(out, f, indent=2)

print(f"Saved {len(matches)} matches")
IST = timezone(timedelta(hours=5, minutes=30))
now = datetime.now(IST)
upcoming = [m for m in matches if datetime.fromisoformat(m["datetime_ist"]) > now]
print(f"Upcoming: {len(upcoming)} matches")
for m in upcoming[:5]:
    print(f"  M{m['match']:>2}  {m['datetime_ist']}  {m['home_abbr']} vs {m['away_abbr']}  ({m['venue']})")
