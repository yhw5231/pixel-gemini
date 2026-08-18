"""Smoke test for every web page using Flask's test client."""
import webapp

client = webapp.app.test_client()

# 1. Auth guard
r = client.get("/runs")
assert r.status_code == 302 and "/login" in r.headers["Location"], r.status_code
print("auth guard OK")

# 2. Login page renders
r = client.get("/login")
assert r.status_code == 200 and "Admin Login" in r.get_data(as_text=True)
print("login page OK")

# 3. Wrong password rejected
r = client.post("/login", data={"username": "admin", "password": "wrong"})
assert r.status_code == 200 and "Invalid username or password" in r.get_data(as_text=True)
print("wrong password rejected OK")

# 4. Correct login
r = client.post("/login", data={"username": "admin", "password": "admin"})
assert r.status_code == 302 and r.headers["Location"].endswith("/")
print("login OK")

# 5. Every page renders
for path, needle in [
    ("/", "Dashboard"),
    ("/accounts", "Gmail Accounts"),
    ("/runs", "Run history"),
    ("/logs", "Application logs"),
    ("/run/1", "Run #1"),
    ("/run/2", "Run #2"),
]:
    r = client.get(path)
    assert r.status_code == 200, (path, r.status_code)
    assert needle in r.get_data(as_text=True), path
    print(f"page {path} OK")

# 6. API endpoint
r = client.get("/api/run/2")
assert r.status_code == 200 and r.get_json()["status"] == "error"
print("api/run OK:", r.get_json()["status"])

# 7. healthz
r = client.get("/healthz")
assert r.status_code == 200 and r.get_data(as_text=True) == "ok"
print("healthz OK")

# 8. Logout
r = client.get("/logout")
assert r.status_code == 302
r = client.get("/")
assert r.status_code == 302
print("logout OK")

print("\nALL SMOKE TESTS PASSED")