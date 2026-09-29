"""Expense category in the person's own words (suggested from existing categories); staff edit pending expenses."""
from .conftest import login


def test_category_in_own_words_and_staff_edit(app, manager):
    manager.post("/api/users", json={"full_name": "Asha", "username": "asha01", "password": "Temp1234"})
    asha = login(app, "asha01", "Temp1234")
    names = lambda: [c["name"] for c in asha.get("/api/expense-categories").json["categories"]]  # noqa: E731
    before = names()

    # An existing category typed with different capitals is reused, not duplicated.
    r = asha.post("/api/expenses", json={"category_name": "cleaning", "description": "Soap", "amount": 5000,
                                         "expense_date": asha.get("/api/time").json["now"][:10]})
    assert r.status_code == 201 and r.json["expense"]["category"] == "Cleaning"
    assert names() == before

    # A new word becomes a new category and is suggested from then on.
    r = asha.post("/api/expenses", json={"category_name": "generator fuel", "description": "5 litres for the generator",
                                         "amount": 18000, "expense_date": asha.get("/api/time").json["now"][:10]})
    assert r.json["expense"]["category"] == "Generator fuel"
    assert "Generator fuel" in names()
    eid = r.json["expense"]["id"]

    # Staff edit their own pending expense in their own words.
    r = asha.put(f"/api/expenses/{eid}", json={"category_name": "Toilet paper", "description": "Two packs", "amount": 7000})
    assert r.status_code == 200
    assert (r.json["expense"]["category"], r.json["expense"]["description"], r.json["expense"]["amount"]) == (
        "Toilet paper", "Two packs", 7000)

    # Once the manager approves it, staff can no longer change it.
    manager.post(f"/api/expenses/{eid}/approve")
    assert asha.put(f"/api/expenses/{eid}", json={"category_name": "X", "description": "Y", "amount": 1}).status_code == 403
    assert asha.post("/api/expenses", json={"category_name": " ", "description": "d", "amount": 1,
                                            "expense_date": "2020-01-01"}).status_code == 400
