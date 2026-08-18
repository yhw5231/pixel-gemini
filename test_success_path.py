"""Temporary mock test: success path of the web run pipeline."""
import threading
import time

import webapp

# Patch the automation entry point to simulate a found offer.
def fake_check_gemini_offer(email, password, device):
    time.sleep(0.2)
    return "https://one.google.com/offer/mock-gemini-12m"

webapp.check_gemini_offer = fake_check_gemini_offer

with webapp.app.app_context():
    aid = webapp.add_account("mock.user@gmail.com", "mock-pass", "mock note")
    # add_account returns None; fetch the account id
    row = webapp.get_db().execute(
        "SELECT id FROM accounts WHERE email = 'mock.user@gmail.com'"
    ).fetchone()
    aid = row["id"]
    run_id = webapp.insert_run(aid, "mock.user@gmail.com", webapp.RUN_STATUS_QUEUED)
    t = threading.Thread(
        target=webapp.run_automation,
        args=(aid, "mock.user@gmail.com", "mock-pass", run_id),
        daemon=True,
    )
    t.start()
    t.join(timeout=30)

    row = webapp.get_db().execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert row["status"] == "done", f"expected done, got {row['status']}"
    assert row["offer_link"] == "https://one.google.com/offer/mock-gemini-12m", row["offer_link"]
    assert row["device"] and "Pixel 10 Pro" in row["device"], row["device"]
    assert row["finished_at"], row["finished_at"]
    print("SUCCESS-PATH OK: run", run_id, "status=", row["status"], "link=", row["offer_link"])

    # Cleanup mock account
    webapp.delete_account(aid)
    print("mock account cleaned up")