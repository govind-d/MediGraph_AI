"""Create a demo account with sample data, so the app can be explored immediately.

Usage:
    python seed_demo.py           # create/refresh the demo account
    python seed_demo.py --reset   # wipe the database first
"""
from __future__ import annotations

import sys

from backend import pipeline, scheduler
from database import models
from database.db import init_db, reset_db
from utils.helpers import today_iso

DEMO_USER = "demo"
DEMO_PASSWORD = "demo1234"


def main() -> int:
    if "--reset" in sys.argv:
        reset_db()
        print("Database reset.")
    else:
        init_db()

    user = models.get_user_by_username(DEMO_USER)
    if user:
        uid = user["id"]
        print("Demo user already exists (id=%s)." % uid)
    else:
        uid, err = models.create_user(DEMO_USER, DEMO_PASSWORD, "demo@example.com", "Demo User")
        if err:
            print("Could not create the demo user: %s" % err)
            return 1
        print("Created demo user '%s' with password '%s'." % (DEMO_USER, DEMO_PASSWORD))

    profiles = {p["name"]: p for p in models.list_profiles(uid)}

    if "Asha Verma (demo)" not in profiles:
        pid = models.create_profile(uid, "Asha Verma (demo)", "Self", 68, "female", 62.0,
                                    "Dummy data for demonstration only.")
        models.add_allergy(pid, "penicillin", "widespread rash as a child", "moderate")
        models.add_condition(pid, "asthma")
        models.add_condition(pid, "hypertension")
        print("Created profile 'Asha Verma (demo)' with a penicillin allergy, asthma and hypertension.")

        outcome = pipeline.analyze(
            "Warfarin 5 mg once daily at night.\n"
            "Ibuprofen 400 mg tablet, take one tablet three times a day after food for 7 days.",
            models.get_profile(pid), save=True, build_schedule=True,
        )
        print("  Prescription #%s saved - highest severity: %s"
              % (outcome["prescription_id"], outcome["findings"]["highest_severity"]))

        outcome2 = pipeline.analyze(
            "Amoxicillin 500mg, take one capsule three times daily for five days.",
            models.get_profile(pid), save=True, build_schedule=True,
        )
        print("  Prescription #%s saved - allergy warnings: %d"
              % (outcome2["prescription_id"], len(outcome2["findings"]["allergy_warnings"])))

        for entry in models.list_schedule(pid):
            scheduler.mark_dose(pid, entry["id"], today_iso(), 1, "taken")
        print("  Marked the first dose of each schedule entry as taken.")
    else:
        pid = profiles["Asha Verma (demo)"]["id"]
        print("Profile 'Asha Verma (demo)' already exists (id=%s)." % pid)

    if "Rohan (demo child)" not in profiles:
        cid = models.create_profile(uid, "Rohan (demo child)", "Child", 9, "male", 28.0,
                                    "Dummy data for demonstration only.")
        models.add_condition(cid, "asthma")
        print("Created profile 'Rohan (demo child)'.")

    print("\nDone. Start the app with:  streamlit run app.py")
    print("Sign in with %s / %s" % (DEMO_USER, DEMO_PASSWORD))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
