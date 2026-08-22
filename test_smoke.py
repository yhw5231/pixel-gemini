"""Smoke test for every web page using Flask's test client."""
import webapp

client = webapp.app.test_client()

# 1. Auth guard
r = client.get("/runs")
assert r.status_code == 302 and "/login" in r.headers["Location"], r.status_code
print("auth guard OK")

# 2. Login page renders in the default Chinese language
r = client.get("/login")
text = r.get_data(as_text=True)
assert r.status_code == 200 and "管理员登录" in text
assert 'lang="zh-CN"' in text
assert "中文" in text and "English" in text
print("Chinese login page OK")

# 3. Wrong password is rejected with a Chinese message
r = client.post("/login", data={"username": "admin", "password": "wrong"})
assert r.status_code == 200 and "用户名或密码错误" in r.get_data(as_text=True)
print("wrong password rejected OK")

# 4. Switch to English and verify that the choice persists
r = client.get("/language/en", headers={"Referer": "http://localhost/login"})
assert r.status_code == 302 and r.headers["Location"] == "/login"
r = client.get("/login")
assert r.status_code == 200 and "Admin Login" in r.get_data(as_text=True)
r = client.get("/login")
assert "Admin Login" in r.get_data(as_text=True)
print("English language switch and persistence OK")

# 5. Switch back to Chinese before login
r = client.get("/language/zh", headers={"Referer": "http://localhost/login"})
assert r.status_code == 302 and r.headers["Location"] == "/login"
r = client.get("/login")
assert "管理员登录" in r.get_data(as_text=True)
print("Chinese language restoration OK")

# 6. Correct login
r = client.post("/login", data={"username": "admin", "password": "admin"})
assert r.status_code == 302 and r.headers["Location"].endswith("/")
print("login OK")

# 7. Every page renders in Chinese
for path, needle in [
    ("/", "控制台"),
    ("/accounts", "Gmail 账户"),
    ("/runs", "运行记录"),
    ("/logs", "应用日志"),
    ("/run/1", "运行 #1"),
    ("/run/2", "运行 #2"),
]:
    r = client.get(path)
    assert r.status_code == 200, (path, r.status_code)
    assert needle in r.get_data(as_text=True), path
    print(f"Chinese page {path} OK")

# 8. Authenticated pages also render in English
r = client.get("/language/en", headers={"Referer": "http://localhost/"})
assert r.status_code == 302 and r.headers["Location"] == "/"
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
    print(f"English page {path} OK")

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