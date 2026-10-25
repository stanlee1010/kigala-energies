# CLAUDE.md — Kigala Energies Petrol Station Management System

Guidance for Claude Code when working in this repository. The system is ONLY for a
petrol/oil station — never add hotel, shop or restaurant modules.

## Commands (run from `petrol_station/`, interpreter `../.venv/Scripts/python.exe`)

- Dev server with reload: `..\.venv\Scripts\python.exe -m backend.app` → http://127.0.0.1:5000
- Production/LAN server: `..\.venv\Scripts\python.exe serve.py` → http://0.0.0.0:8000
- Tests: `..\.venv\Scripts\python.exe -m pytest -q tests -p no:cacheprovider`
- Run background checks once: `..\.venv\Scripts\python.exe -m flask --app backend.app check`
- Regenerate `database/schema.sql` after model changes (see how it was produced: CreateTable over `db.metadata.sorted_tables`, PostgreSQL dialect)

Local DB is SQLite at `petrol_station/database/petrol_station.db`; production uses `DATABASE_URL` (PostgreSQL/MySQL).
Tables are created by `db.create_all()` in `services/bootstrap.py`; the first run seeds station, fuels, 3 pumps,
thresholds, expense categories and the `manager` account (must change password).

Training portal: `serve_demo.py` (port 8001) sets `DEMO_MODE=true`, its own DB `database/training.db` and session
cookie `training_session`. Practice data comes from `services/demo.py` (built through the real services). Anything
that resets or simulates must check `DEMO_MODE` (see `routes/demo.py::_require_demo`) — never allow it on the real portal.

## Architecture

- `backend/calculations/` — pure functions, no Flask: `fuel.py` (sold/revenue/closing stock), `periods.py`
  (first Wednesday, period bounds), `costing.py` (perpetual weighted-average cost). Unit-test these directly.
- `backend/services/` — business logic using the DB: `shifts.py` (windows, current/previous, generation),
  `stock.py` (per-shift tank rows, mismatch alerts), `finance.py` (P&L + chart series), `periods.py`
  (first-Wednesday rollover, price review), `monitor.py` (idempotent checks), `alerts.py` (dedupe by `dedupe_key`),
  `audit.py` (every important change), `clock.py` (authoritative server time in station timezone).
- `backend/routes/` — thin JSON endpoints; raise `ApiError` for user-facing errors; always `audit.log(...)` on
  writes; commit at the end of the request.
- `frontend/` — plain HTML + external CSS + vanilla JS, no build step. Every page calls
  `App.init({role, title})` from `js/app.js`, which renders the shell and guards the role. Page logic lives in
  `js/pages/*.js`. CSP forbids inline scripts: never add `<script>` blocks or `onclick=` attributes.

## Business invariants — do not break

1. Business date/time comes from `services/clock.py` (server clock + station timezone), never the browser.
2. Reporting periods run first Wednesday → day before next first Wednesday; rollover is automatic in
   `ensure_periods()`; price changes are never automatic — the manager must KEEP or UPDATE each fuel.
3. Refill statuses: DRAFT, AWAITING_CONFIRMATION (staff-editable) → CONFIRMED (locked for staff) →
   APPROVED / FLAGGED / CORRECTED (manager only, with reason). Each confirm/return/correction writes a
   `refill_confirmations` row.
4. `calculated_closing_stock = opening + refilled − sold + adjustments`; previous closing (dip if present) is the
   next shift's expected opening.
5. COGS uses weighted-average cost; never expense a whole refill on the day it arrives.
6. Shifts are unique on (station_id, shift_date, shift_type); generation must never duplicate.
   Shift types are data (`shift_definitions`), never hard-coded: each active type ends when the next starts;
   order within a day is `Shift.sequence` (never sort by shift_type). Switching a type off CANCELS its
   not-yet-started shifts (never deletes). Staff with a regular shift (`users.default_shift_definition_id`)
   are auto-assigned with `shift_staff.source='ROSTER'`; MANUAL assignments are never moved automatically.
   Schema changes to existing tables go in `services/migrations.py` (idempotent, backs up SQLite first).
7. Staff cannot change prices, approved/confirmed refills, closed shifts, settings or other staff's records.
8. Financial records are never hard-deleted by staff; use status changes (VOID, CANCELLED, is_active=false).
9. Money is `Decimal` in the backend (`calculations.D`, `money`, `litres`) — never float arithmetic.
10. Several locations: every record carries `station_id`. Roles: STAFF (one station), MANAGER (stations in
    `user_stations`; `users.station_id` = the station being worked on, switched via `POST /api/auth/station`),
    DIRECTOR (all stations; read-only sales/profit dashboard + adds/removes managers and stations in
    `routes/director.py`). Managers create STAFF only. Access checks live in `services/stations.py`.

## Requirement → code map

| Requirement | Where |
|---|---|
| Staff accounts, disable, reset password, activity | `routes/users.py`, `frontend/manager/staff.html` |
| Shifts, auto daily Morning/Night, assignment, approve/flag | `services/shifts.py`, `routes/shifts.py`, `manager-shifts.js` |
| Shift types (Morning/Afternoon/Night), presets, roster | `services/shifts.py`, `services/roster.py`, `routes/roster.py`, Shifts page |
| People per shift + one person per pump | `shift_definitions.staff_needed` (upgrade_005), `routes/shifts.py::_assigned_pump_ids/_set_pumps/_pump_choice`, `PUT /api/shifts/<id>/my-pumps`, pump picker in `staff-shift.js`; training portal copies it and makes that many trainees per shift (`services/demo.py`) |
| Pumps (not hard-coded), fuel types | `routes/pumps.py`, `manager-pumps.js` |
| Pump readings, INVALID METER READING | `routes/shifts.py::save_reading`, `staff-shift.js` |
| Refill entry + confirmation + lock | `routes/refills.py`, `staff/refill.html`, `staff-refill.js` |
| Refill not reconciled / stock not updated | `services/monitor.py::check_refill_reconciliation` |
| Stock mismatch between shifts | `services/stock.py::set_opening_stock` |
| Low stock thresholds | `monitor.check_low_stock`, `manager-stock.js` |
| Prices + history + first-Wednesday review | `routes/fuel.py`, `App.showPriceReview` in `app.js` |
| Expenses + receipts (category in own words, suggested) | `routes/expenses.py`, `App.expenseForm` in `app.js` |
| Capital: profit from selling vs buying price (manager only) | `routes/capital.py`, `manager-capital.js` |
| Director portal: sales/profit by location or all, managers, stations | `routes/director.py`, `frontend/director/` |
| Several locations, manager station switcher, worker location | `services/stations.py`, `routes/users.py`, `app.js` |
| Online money, cash, day money (staff: online only) | `services/cash.py`, `routes/shifts.py` |
| Tanks (levels, capacity, per-pump breakdown) | `services/tanks.py`, `routes/tanks.py`, `manager-stock.js` |
| Deposits / expected shift money | `routes/shifts.py::record_deposit` |
| Monthly P&L notification | `services/periods.py`, dashboard banner in `dashboard.js` |
| Graphs daily/weekly/monthly/yearly | `services/finance.py::chart_series`, `dashboard.js` (Chart.js) |
| Reports + PDF/Excel | `reports/builder.py`, `reports/exporters.py`, `routes/reports.py` |
| Audit log | `services/audit.py`, `routes/alerts.py::audit_log` |
| API rate limit, device blocking, wrong-password waits | `services/guard.py`, `services/ratelimit.py` (memory, or DB tables `rate_counters`/`ip_blocks` when `SHARED_RATE_LIMITS`, default on Vercel), `auth/routes.py::login` |
| Offline saves (outbox) + page copies | `App.api`/`App.flushOutbox` in `app.js` (only whole-record PUTs are queued; each carries an `Idempotency-Key`, applied once via `processed_requests` in `app.py::apply_each_save_once`; refused saves kept in a "could not be sent" list; `X-Saved-At` = typed time in SERVER time (phone keeps its offset from `/api/time`), checked by `common.saved_at`; `based_on`/`was` values make the server refuse overwriting newer changes via `common.check_unchanged`), `frontend/sw.js` (page files from saved copies, refreshed when the `X-App-Version` header changes; new pages must be added to `PRECACHE`), `manifest.webmanifest` (installable; `navigator.storage.persist`) |
| Salaries: manager sets salary, money taken is deducted; staff see their own | `routes/salaries.py`, `models/salary.py` (`salary_months` carries forward, a month below zero is deducted from the next via `owed_from_before`, `salary_advances` VOID not deleted), `manager-salaries.js`, `staff-salary.js` |
| Expense sheet (Matumizi) | `App.expenseSheet` in `app.js`, `routes/expenses.py::_save_sheet` |
