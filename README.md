# Kigala Energies — Petrol Station Management System

Fuel sales, pump readings, Morning/Night shifts, fuel refills with confirmation,
tank stock, expenses, deposits, alerts, first-Wednesday reporting periods,
weighted-average profit/loss, charts and PDF/Excel reports. It covers the
petrol station only, with no hotel, shop or restaurant modules.

## Quick start (Windows)

1. Double-click **`start.bat`**. You can also open this folder in VS Code and press **Ctrl+Shift+B**.
2. Open <http://localhost:8000>.
3. Log in as **`manager`** with the password **`ChangeMe123!`**. You must change it at first login.
4. Follow the **Getting started** checklist on the dashboard:
   set the fuel prices, record the opening tank stock and its cost, create staff accounts, and assign staff to shifts.

Staff phones on the same Wi-Fi can open `http://<this-computer-IP>:8000`.
`start.bat` prints the address.

## Training portal (practice)

`start.bat` also opens the **training portal** at <http://localhost:8001> (on phones use `http://<this-computer-IP>:8001`).
You can start it on its own with `start-training.bat`.

- It is **for staff only**; managers use the real system.
- It uses its own database (`database/training.db`), so nothing done there affects the real station.
- It **copies the real station's shift pattern, pumps, tanks and prices** (reading the real database without changing it). It rebuilds itself when those change.
- It creates **one practice attendant per shift** (for example `demo.morning` and `demo.night`). The password for all of them is **`Demo1234`**.
- Each trainee's clock is paused inside their own shift, so the practice shift never runs out.
- A yellow **TRAINING PORTAL** bar on every page has two buttons:
  - **Training guide** opens `/training.html`, with 7 staff lessons, each with a "Try it" button.
  - **Reset training data** starts a fresh session.

## Developing in VS Code

Open the folder `D:\kigala energies project`, or open `kigala-energies.code-workspace`.

| What | How |
|---|---|
| Python interpreter | `.venv` is selected automatically (Python 3.12) |
| Run with the debugger | Run and Debug → *Run station system (debug)* → <http://127.0.0.1:5000> |
| Start the server | Terminal → Run Build Task (**Ctrl+Shift+B**) |
| Tests | Testing panel, or the *Run tests* task (`pytest`) |
| Ask Claude | Open the Claude Code panel. `CLAUDE.md` gives it the full project context |

## Project layout

```
petrol_station/
├── backend/            Flask API (Python)
│   ├── app.py          application factory, static frontend, security headers
│   ├── config.py       environment configuration
│   ├── models/         SQLAlchemy tables (24)
│   ├── routes/         REST endpoints
│   ├── services/       shifts, stock, alerts, periods, finance, monitor, audit
│   ├── calculations/   pure formulas: fuel, first Wednesday, weighted-average costing
│   ├── reports/        report builder + PDF/Excel exporters
│   └── auth/           sessions + role-based access
├── frontend/           HTML + external CSS + JavaScript (no build step)
│   ├── staff/          phone-first staff screens
│   ├── manager/        manager screens
│   ├── css/            style.css, dashboard.css, forms.css
│   └── js/             app.js, dashboard.js, validation.js, pages/*
├── database/           schema.sql (PostgreSQL), migrations/, seed/
├── reports/  uploads/  generated files and receipts
└── tests/              pytest (calculations + end-to-end workflow)
```

## Going live on the internet

The app is ready to deploy. Publishing it needs an account you own. I can't create one for you.

**Option A: Render (managed PostgreSQL, HTTPS).**
1. Push this folder to a GitHub repository.
2. On render.com, go to **New → Blueprint** and choose the repository. `render.yaml` creates the web service and the PostgreSQL database.
3. When asked, enter the first `MANAGER_PASSWORD`.

**Option B: any server with Docker.**
```
cd petrol_station
docker build -t kigala-energies .
docker run -p 8000:8000 --env-file .env kigala-energies
```
Set `DATABASE_URL` to PostgreSQL or MySQL. Set `SESSION_COOKIE_SECURE=true` behind HTTPS.

**Option C: this PC as the station server.** Run `start.bat`. Staff use it over the local Wi-Fi.

## Security

Each item below is covered by `petrol_station/tests/test_security.py`. On GitHub, `.github/workflows/security.yml` also scans dependencies (pip-audit) and the git history (gitleaks) on every push and every week. Dependabot opens update pull requests.

- **Secrets:**
  - They live only in `.env`, which git ignores, and are never in the code or its history.
  - In production (`APP_ENV=production`) the app refuses to start without its own `SECRET_KEY`.
- **Server-side access control on every API route:**
  - Staff, manager and director roles.
  - Each record is locked to its station and owner, so guessing another station's IDs returns "not found".
  - Fields such as role, status or approval cannot be set from the browser.
- **Logins:**
  - Passwords are hashed (scrypt).
  - Temporary passwords must be changed.
  - An account locks after 5 wrong passwords.
  - Login attempts are limited per device.
  - A hidden bot-trap field refuses automated logins.
- **Sessions:** HttpOnly, SameSite and Secure cookies. Requests forged from other websites are blocked.
- **Input:**
  - Every value is validated on the server.
  - Database queries are parameterised.
  - User text is escaped on screen, in PDFs and in Excel (no formula injection).
- **Receipts:**
  - Only real PDF and photo files are accepted, checked by content and limited to 10 MB.
  - Files are stored under random names and served with a locked-down content type.
- **Headers:** CSP, X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy and COOP. The chart library is integrity-checked.
- **On the internet:** set `FORCE_HTTPS=true` (redirect, HSTS and Secure cookies), and set `TRUST_PROXY=1` behind Render or Nginx. `render.yaml` already does both.
- **Data at rest:** use an encrypted disk (Windows BitLocker on the station PC) or a managed database with encryption at rest, such as Render PostgreSQL. Keep the `database/backups` folder private.

## Key business rules

- **Clock.** Shifts, reporting periods, alerts and profit use the server's clock in the station timezone (Settings). The browser clock is only used for display.
- **Reporting period.** A period starts on the **first Wednesday** of each month, which is calculated automatically. When the date passes the end of a period, it closes by itself and its totals are frozen. The manager then gets the monthly review and a fuel-price review that must be completed before continuing.
- **Profit.** The system uses perpetual weighted-average cost. A refill enters inventory. Only the litres actually sold are charged as cost of fuel sold (see `backend/calculations/costing.py`).
- **Refills.** Staff enter a refill, then see a **CONFIRM FUEL REFILL** screen:
  - **NO** sends them back to correct it.
  - **YES** locks the record, updates the stock and writes the confirmation log and the audit log.
  - Only the manager can approve, flag or correct a confirmed refill, and every correction needs a reason.
- **Shifts.** The manager sets the shift pattern on the Shifts page: 2 shifts × 12 h, 3 shifts × 8 h, or custom shifts such as Afternoon. Each shift ends when the next one starts. Each staff member gets a regular shift and is put on every upcoming shift of that type automatically. A single day can still be changed by opening that shift.
- **Tanks (manager only).** There is one Diesel tank and one Petrol tank by default. The manager can change the number of tanks per fuel and each tank's capacity, and choose which tank each pump draws from. Staff never type tank stock:
  - when staff record a refill, they enter the litres added and, if the fuel has more than one tank, which tank;
  - pump sales come out of the pump's tank automatically;
  - per shift, the manager sees: opening + refilled − sold by each pump = closing.
- **Stock.** Closing stock = Opening + Refilled − Sold. The previous closing becomes the next opening. A mismatch raises **FUEL STOCK MISMATCH**.
- **No hard deletes.** Financial records are disabled, voided or corrected, never deleted, and every change is audited.
