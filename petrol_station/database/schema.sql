-- Kigala Energies Petrol Station Management System
-- PostgreSQL schema (generated from backend/models; MySQL/SQLite equivalents are created by SQLAlchemy).
-- Money: NUMERIC(18,2). Litres: NUMERIC(16,3). Timestamps (*_at) are UTC; business dates are station-local.

CREATE TABLE fuel_types (
	id SERIAL NOT NULL, 
	code VARCHAR(20) NOT NULL, 
	name VARCHAR(60) NOT NULL, 
	unit VARCHAR(10) NOT NULL, 
	color VARCHAR(20) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code)
);

CREATE TABLE ip_blocks (
	ip VARCHAR(64) NOT NULL, 
	blocked_until BIGINT NOT NULL, 
	reason VARCHAR(255) NOT NULL, 
	PRIMARY KEY (ip)
);

CREATE TABLE rate_counters (
	key VARCHAR(120) NOT NULL, 
	window_start BIGINT NOT NULL, 
	count INTEGER NOT NULL, 
	PRIMARY KEY (key, window_start)
);

CREATE TABLE stations (
	id SERIAL NOT NULL, 
	name VARCHAR(150) NOT NULL, 
	code VARCHAR(30) NOT NULL, 
	address VARCHAR(255), 
	phone VARCHAR(40), 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code)
);

CREATE TABLE expense_categories (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	name VARCHAR(60) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_expense_category UNIQUE (station_id, name), 
	FOREIGN KEY(station_id) REFERENCES stations (id)
);

CREATE TABLE shift_definitions (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	code VARCHAR(30) NOT NULL, 
	name VARCHAR(60) NOT NULL, 
	start_time TIME WITHOUT TIME ZONE NOT NULL, 
	sequence INTEGER NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_shift_definition_code UNIQUE (station_id, code), 
	FOREIGN KEY(station_id) REFERENCES stations (id)
);

CREATE TABLE suppliers (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	name VARCHAR(150) NOT NULL, 
	phone VARCHAR(40), 
	email VARCHAR(150), 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_supplier_name UNIQUE (station_id, name), 
	FOREIGN KEY(station_id) REFERENCES stations (id)
);

CREATE TABLE tanks (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	fuel_type_id INTEGER NOT NULL, 
	tank_number INTEGER NOT NULL, 
	name VARCHAR(60) NOT NULL, 
	capacity_litres NUMERIC(16, 3) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_tank_number UNIQUE (station_id, fuel_type_id, tank_number), 
	CONSTRAINT ck_tank_number_positive CHECK (tank_number > 0), 
	CONSTRAINT ck_tank_capacity_positive CHECK (capacity_litres > 0), 
	FOREIGN KEY(station_id) REFERENCES stations (id), 
	FOREIGN KEY(fuel_type_id) REFERENCES fuel_types (id)
);

CREATE TABLE pumps (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	fuel_type_id INTEGER NOT NULL, 
	pump_number INTEGER NOT NULL, 
	pump_name VARCHAR(60) NOT NULL, 
	tank_id INTEGER, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_pump_name UNIQUE (station_id, pump_name), 
	CONSTRAINT uq_pump_number UNIQUE (station_id, fuel_type_id, pump_number), 
	CONSTRAINT ck_pump_number_positive CHECK (pump_number > 0), 
	FOREIGN KEY(station_id) REFERENCES stations (id), 
	FOREIGN KEY(fuel_type_id) REFERENCES fuel_types (id), 
	FOREIGN KEY(tank_id) REFERENCES tanks (id)
);

CREATE TABLE users (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	username VARCHAR(60) NOT NULL, 
	full_name VARCHAR(150) NOT NULL, 
	phone VARCHAR(40), 
	email VARCHAR(150), 
	password_hash VARCHAR(255) NOT NULL, 
	role VARCHAR(10) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	must_change_password BOOLEAN NOT NULL, 
	failed_login_count INTEGER NOT NULL, 
	locked_until TIMESTAMP WITHOUT TIME ZONE, 
	last_login_at TIMESTAMP WITHOUT TIME ZONE, 
	created_by INTEGER, 
	default_shift_definition_id INTEGER, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_users_role CHECK (role IN ('STAFF','MANAGER','DIRECTOR')), 
	FOREIGN KEY(station_id) REFERENCES stations (id), 
	FOREIGN KEY(created_by) REFERENCES users (id), 
	FOREIGN KEY(default_shift_definition_id) REFERENCES shift_definitions (id)
);

CREATE TABLE attachments (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	file_name VARCHAR(255) NOT NULL, 
	stored_name VARCHAR(255) NOT NULL, 
	content_type VARCHAR(100), 
	size_bytes INTEGER NOT NULL, 
	related_table VARCHAR(60), 
	related_id INTEGER, 
	uploaded_by INTEGER NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(station_id) REFERENCES stations (id), 
	UNIQUE (stored_name), 
	FOREIGN KEY(uploaded_by) REFERENCES users (id)
);

CREATE TABLE audit_logs (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	user_id INTEGER, 
	action VARCHAR(80) NOT NULL, 
	table_name VARCHAR(60), 
	record_id INTEGER, 
	old_values TEXT, 
	new_values TEXT, 
	reason VARCHAR(500), 
	ip_address VARCHAR(64), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(station_id) REFERENCES stations (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE TABLE reporting_periods (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	label VARCHAR(60) NOT NULL, 
	start_date DATE NOT NULL, 
	end_date DATE NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	total_revenue NUMERIC(18, 2), 
	fuel_sold_litres NUMERIC(16, 3), 
	fuel_purchased_litres NUMERIC(16, 3), 
	fuel_purchase_cost NUMERIC(18, 2), 
	cost_of_fuel_sold NUMERIC(18, 2), 
	operating_expenses NUMERIC(18, 2), 
	gross_profit NUMERIC(18, 2), 
	net_profit NUMERIC(18, 2), 
	closed_at TIMESTAMP WITHOUT TIME ZONE, 
	reviewed_by INTEGER, 
	reviewed_at TIMESTAMP WITHOUT TIME ZONE, 
	price_reviewed_by INTEGER, 
	price_reviewed_at TIMESTAMP WITHOUT TIME ZONE, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_period_start UNIQUE (station_id, start_date), 
	CONSTRAINT ck_period_dates CHECK (end_date >= start_date), 
	CONSTRAINT ck_period_status CHECK (status IN ('OPEN','CLOSED')), 
	FOREIGN KEY(station_id) REFERENCES stations (id), 
	FOREIGN KEY(reviewed_by) REFERENCES users (id), 
	FOREIGN KEY(price_reviewed_by) REFERENCES users (id)
);

CREATE TABLE reports (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	report_type VARCHAR(40) NOT NULL, 
	period_start DATE, 
	period_end DATE, 
	format VARCHAR(10) NOT NULL, 
	file_name VARCHAR(255), 
	generated_by INTEGER NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(station_id) REFERENCES stations (id), 
	FOREIGN KEY(generated_by) REFERENCES users (id)
);

CREATE TABLE shifts (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	shift_date DATE NOT NULL, 
	shift_type VARCHAR(30) NOT NULL, 
	sequence INTEGER NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	scheduled_start TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	scheduled_end TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	started_at TIMESTAMP WITHOUT TIME ZONE, 
	ended_at TIMESTAMP WITHOUT TIME ZONE, 
	submitted_at TIMESTAMP WITHOUT TIME ZONE, 
	approved_at TIMESTAMP WITHOUT TIME ZONE, 
	opened_by INTEGER, 
	submitted_by INTEGER, 
	closed_by INTEGER, 
	flag_reason VARCHAR(500), 
	notes VARCHAR(500), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_shift_station_date_type UNIQUE (station_id, shift_date, shift_type), 
	CONSTRAINT ck_shift_status CHECK (status IN ('SCHEDULED','OPEN','SUBMITTED','FLAGGED','CLOSED','CANCELLED')), 
	FOREIGN KEY(station_id) REFERENCES stations (id), 
	FOREIGN KEY(opened_by) REFERENCES users (id), 
	FOREIGN KEY(submitted_by) REFERENCES users (id), 
	FOREIGN KEY(closed_by) REFERENCES users (id)
);

CREATE TABLE stock_thresholds (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	fuel_type_id INTEGER NOT NULL, 
	minimum_litres NUMERIC(16, 3) NOT NULL, 
	updated_by INTEGER, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_threshold UNIQUE (station_id, fuel_type_id), 
	CONSTRAINT ck_threshold_positive CHECK (minimum_litres >= 0), 
	FOREIGN KEY(station_id) REFERENCES stations (id), 
	FOREIGN KEY(fuel_type_id) REFERENCES fuel_types (id), 
	FOREIGN KEY(updated_by) REFERENCES users (id)
);

CREATE TABLE system_settings (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	key VARCHAR(80) NOT NULL, 
	value VARCHAR(255) NOT NULL, 
	updated_by INTEGER, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_setting_station_key UNIQUE (station_id, key), 
	FOREIGN KEY(station_id) REFERENCES stations (id), 
	FOREIGN KEY(updated_by) REFERENCES users (id)
);

CREATE TABLE user_stations (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	station_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_user_station UNIQUE (user_id, station_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(station_id) REFERENCES stations (id)
);

CREATE TABLE weekly_shift_choices (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	week_start DATE NOT NULL, 
	shift_definition_id INTEGER NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_weekly_choice_user_week UNIQUE (user_id, week_start), 
	FOREIGN KEY(station_id) REFERENCES stations (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(shift_definition_id) REFERENCES shift_definitions (id)
);

CREATE TABLE alerts (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	shift_id INTEGER, 
	fuel_type_id INTEGER, 
	alert_type VARCHAR(40) NOT NULL, 
	severity VARCHAR(10) NOT NULL, 
	title VARCHAR(150) NOT NULL, 
	message TEXT NOT NULL, 
	details TEXT, 
	related_record_id INTEGER, 
	dedupe_key VARCHAR(120), 
	is_resolved BOOLEAN NOT NULL, 
	resolved_by INTEGER, 
	resolved_at TIMESTAMP WITHOUT TIME ZONE, 
	resolution_note VARCHAR(500), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_alert_severity CHECK (severity IN ('INFO','WARNING','CRITICAL')), 
	FOREIGN KEY(station_id) REFERENCES stations (id), 
	FOREIGN KEY(shift_id) REFERENCES shifts (id), 
	FOREIGN KEY(fuel_type_id) REFERENCES fuel_types (id), 
	FOREIGN KEY(resolved_by) REFERENCES users (id)
);

CREATE TABLE expenses (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	shift_id INTEGER, 
	expense_date DATE NOT NULL, 
	category_id INTEGER NOT NULL, 
	description VARCHAR(255) NOT NULL, 
	amount NUMERIC(18, 2) NOT NULL, 
	reference VARCHAR(80), 
	receipt_attachment_id INTEGER, 
	entered_by INTEGER NOT NULL, 
	approved_by INTEGER, 
	approved_at TIMESTAMP WITHOUT TIME ZONE, 
	status VARCHAR(12) NOT NULL, 
	status_reason VARCHAR(255), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_expense_amount_positive CHECK (amount > 0), 
	CONSTRAINT ck_expense_status CHECK (status IN ('PENDING','APPROVED','REJECTED','CANCELLED','VOID')), 
	FOREIGN KEY(station_id) REFERENCES stations (id), 
	FOREIGN KEY(shift_id) REFERENCES shifts (id), 
	FOREIGN KEY(category_id) REFERENCES expense_categories (id), 
	FOREIGN KEY(receipt_attachment_id) REFERENCES attachments (id), 
	FOREIGN KEY(entered_by) REFERENCES users (id), 
	FOREIGN KEY(approved_by) REFERENCES users (id)
);

CREATE TABLE fuel_prices (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	fuel_type_id INTEGER NOT NULL, 
	price_per_litre NUMERIC(14, 2) NOT NULL, 
	previous_price NUMERIC(14, 2), 
	confirmation_status VARCHAR(12) NOT NULL, 
	reporting_period_id INTEGER, 
	set_by INTEGER NOT NULL, 
	note VARCHAR(255), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_price_positive CHECK (price_per_litre > 0), 
	CONSTRAINT ck_price_confirmation CHECK (confirmation_status IN ('INITIAL','UPDATED','KEPT')), 
	FOREIGN KEY(station_id) REFERENCES stations (id), 
	FOREIGN KEY(fuel_type_id) REFERENCES fuel_types (id), 
	FOREIGN KEY(reporting_period_id) REFERENCES reporting_periods (id), 
	FOREIGN KEY(set_by) REFERENCES users (id)
);

CREATE TABLE fuel_refills (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	shift_id INTEGER, 
	fuel_type_id INTEGER NOT NULL, 
	tank_id INTEGER, 
	supplier_id INTEGER, 
	refill_date DATE NOT NULL, 
	refill_time TIME WITHOUT TIME ZONE NOT NULL, 
	quantity_litres NUMERIC(16, 3) NOT NULL, 
	cost_per_litre NUMERIC(14, 2) NOT NULL, 
	total_cost NUMERIC(18, 2) NOT NULL, 
	invoice_number VARCHAR(80), 
	delivery_reference VARCHAR(80), 
	recorded_by INTEGER NOT NULL, 
	confirmed_by INTEGER, 
	approved_by INTEGER, 
	status VARCHAR(25) NOT NULL, 
	confirmed_at TIMESTAMP WITHOUT TIME ZONE, 
	approved_at TIMESTAMP WITHOUT TIME ZONE, 
	notes VARCHAR(500), 
	manager_note VARCHAR(500), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_refill_qty_positive CHECK (quantity_litres > 0), 
	CONSTRAINT ck_refill_cost_positive CHECK (cost_per_litre >= 0), 
	CONSTRAINT ck_refill_status CHECK (status IN ('DRAFT','AWAITING_CONFIRMATION','CONFIRMED','APPROVED','FLAGGED','CORRECTED')), 
	FOREIGN KEY(station_id) REFERENCES stations (id), 
	FOREIGN KEY(shift_id) REFERENCES shifts (id), 
	FOREIGN KEY(fuel_type_id) REFERENCES fuel_types (id), 
	FOREIGN KEY(tank_id) REFERENCES tanks (id), 
	FOREIGN KEY(supplier_id) REFERENCES suppliers (id), 
	FOREIGN KEY(recorded_by) REFERENCES users (id), 
	FOREIGN KEY(confirmed_by) REFERENCES users (id), 
	FOREIGN KEY(approved_by) REFERENCES users (id)
);

CREATE TABLE fuel_stock (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	fuel_type_id INTEGER NOT NULL, 
	shift_id INTEGER NOT NULL, 
	expected_opening_stock NUMERIC(16, 3), 
	opening_stock NUMERIC(16, 3) NOT NULL, 
	opening_difference NUMERIC(16, 3) NOT NULL, 
	opening_confirmed BOOLEAN NOT NULL, 
	refilled_quantity NUMERIC(16, 3) NOT NULL, 
	fuel_sold NUMERIC(16, 3) NOT NULL, 
	adjustments NUMERIC(16, 3) NOT NULL, 
	calculated_closing_stock NUMERIC(16, 3) NOT NULL, 
	actual_closing_stock NUMERIC(16, 3), 
	stock_difference NUMERIC(16, 3), 
	status VARCHAR(15) NOT NULL, 
	recorded_by INTEGER, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_stock_shift_fuel UNIQUE (shift_id, fuel_type_id), 
	CONSTRAINT ck_opening_stock_positive CHECK (opening_stock >= 0), 
	CONSTRAINT ck_refilled_positive CHECK (refilled_quantity >= 0), 
	CONSTRAINT ck_sold_positive CHECK (fuel_sold >= 0), 
	FOREIGN KEY(station_id) REFERENCES stations (id), 
	FOREIGN KEY(fuel_type_id) REFERENCES fuel_types (id), 
	FOREIGN KEY(shift_id) REFERENCES shifts (id), 
	FOREIGN KEY(recorded_by) REFERENCES users (id)
);

CREATE TABLE pump_readings (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	shift_id INTEGER NOT NULL, 
	pump_id INTEGER NOT NULL, 
	fuel_type_id INTEGER NOT NULL, 
	tank_id INTEGER, 
	opening_meter NUMERIC(18, 3) NOT NULL, 
	closing_meter NUMERIC(18, 3), 
	fuel_sold NUMERIC(16, 3) NOT NULL, 
	price_per_litre NUMERIC(14, 2), 
	revenue NUMERIC(18, 2) NOT NULL, 
	expected_opening_meter NUMERIC(18, 3), 
	recorded_by INTEGER, 
	updated_by INTEGER, 
	opening_recorded_at TIMESTAMP WITHOUT TIME ZONE, 
	closing_recorded_at TIMESTAMP WITHOUT TIME ZONE, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_reading_shift_pump UNIQUE (shift_id, pump_id), 
	CONSTRAINT ck_opening_meter_positive CHECK (opening_meter >= 0), 
	CONSTRAINT ck_closing_ge_opening CHECK (closing_meter IS NULL OR closing_meter >= opening_meter), 
	FOREIGN KEY(station_id) REFERENCES stations (id), 
	FOREIGN KEY(shift_id) REFERENCES shifts (id), 
	FOREIGN KEY(pump_id) REFERENCES pumps (id), 
	FOREIGN KEY(fuel_type_id) REFERENCES fuel_types (id), 
	FOREIGN KEY(tank_id) REFERENCES tanks (id), 
	FOREIGN KEY(recorded_by) REFERENCES users (id), 
	FOREIGN KEY(updated_by) REFERENCES users (id)
);

CREATE TABLE shift_cash (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	shift_id INTEGER NOT NULL, 
	online_expression VARCHAR(1000) NOT NULL, 
	online_total NUMERIC(18, 2) NOT NULL, 
	updated_by INTEGER, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_online_total_positive CHECK (online_total >= 0), 
	FOREIGN KEY(station_id) REFERENCES stations (id), 
	UNIQUE (shift_id), 
	FOREIGN KEY(shift_id) REFERENCES shifts (id), 
	FOREIGN KEY(updated_by) REFERENCES users (id)
);

CREATE TABLE shift_deposits (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	shift_id INTEGER NOT NULL, 
	expected_amount NUMERIC(18, 2) NOT NULL, 
	actual_amount NUMERIC(18, 2) NOT NULL, 
	difference NUMERIC(18, 2) NOT NULL, 
	deposit_reference VARCHAR(120), 
	deposit_method VARCHAR(60), 
	deposit_account VARCHAR(120), 
	deposited_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	recorded_by INTEGER NOT NULL, 
	notes VARCHAR(500), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_deposit_positive CHECK (actual_amount >= 0), 
	FOREIGN KEY(station_id) REFERENCES stations (id), 
	FOREIGN KEY(shift_id) REFERENCES shifts (id), 
	FOREIGN KEY(recorded_by) REFERENCES users (id)
);

CREATE TABLE shift_staff (
	id SERIAL NOT NULL, 
	shift_id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	assigned_role VARCHAR(40) NOT NULL, 
	assigned_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	assigned_by INTEGER, 
	source VARCHAR(10) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_shift_staff UNIQUE (shift_id, user_id), 
	FOREIGN KEY(shift_id) REFERENCES shifts (id) ON DELETE CASCADE, 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(assigned_by) REFERENCES users (id)
);

CREATE TABLE stock_adjustments (
	id SERIAL NOT NULL, 
	station_id INTEGER NOT NULL, 
	fuel_type_id INTEGER NOT NULL, 
	tank_id INTEGER, 
	shift_id INTEGER, 
	adjustment_type VARCHAR(20) NOT NULL, 
	quantity_litres NUMERIC(16, 3) NOT NULL, 
	cost_per_litre NUMERIC(14, 2), 
	reason VARCHAR(500) NOT NULL, 
	adjusted_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	created_by INTEGER NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_adjustment_type CHECK (adjustment_type IN ('OPENING_BALANCE','DIP_CORRECTION','LOSS','RECONCILIATION','OTHER')), 
	FOREIGN KEY(station_id) REFERENCES stations (id), 
	FOREIGN KEY(fuel_type_id) REFERENCES fuel_types (id), 
	FOREIGN KEY(tank_id) REFERENCES tanks (id), 
	FOREIGN KEY(shift_id) REFERENCES shifts (id), 
	FOREIGN KEY(created_by) REFERENCES users (id)
);

CREATE TABLE refill_confirmations (
	id SERIAL NOT NULL, 
	refill_id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	entered_quantity NUMERIC(16, 3) NOT NULL, 
	confirmed_quantity NUMERIC(16, 3), 
	confirmation_result VARCHAR(30) NOT NULL, 
	confirmed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	ip_address VARCHAR(64), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_confirmation_result CHECK (confirmation_result IN ('CONFIRMED_CORRECT','RETURNED_FOR_CORRECTION','MANAGER_CORRECTED')), 
	FOREIGN KEY(refill_id) REFERENCES fuel_refills (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE TABLE shift_staff_pumps (
	id SERIAL NOT NULL, 
	shift_staff_id INTEGER NOT NULL, 
	pump_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_shift_staff_pump UNIQUE (shift_staff_id, pump_id), 
	FOREIGN KEY(shift_staff_id) REFERENCES shift_staff (id) ON DELETE CASCADE, 
	FOREIGN KEY(pump_id) REFERENCES pumps (id)
);
