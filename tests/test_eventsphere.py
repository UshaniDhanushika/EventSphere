import pytest
import asyncio
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from database import SessionLocal
from models import (
    User, Venue, Event, TicketCategory, Booking, Ticket, WaitlistEntry, Notification
)
from security import create_access_token
from agents import (
    message_bus, venue_agent, booking_agent, ticket_agent,
    waitlist_agent, notification_agent, AgentMessage
)
from main import app
from seed_data import seed_database

# Ensure database is seeded before running tests
seed_database()

# ==============================================================
# 1. VenueAgent: Turnaround Buffer, Clashes & Weekend Peak Pricing
# ==============================================================
def test_venue_agent_turnaround_buffer_clash():
    """Rule V1: VenueAgent detects overlap within the 60-minute turnaround buffer and returns PROPOSE."""
    db = SessionLocal()
    try:
        ev = db.query(Event).filter(Event.status == "published").first()
        assert ev is not None
        v_id = ev.venue_id
        # Request a slot that overlaps Event 1
        clash_start = ev.start_time + timedelta(minutes=30)
        clash_end = ev.end_time + timedelta(hours=1)
    finally:
        db.close()

    async def run():
        msg = AgentMessage(
            sender="TestHarness",
            receiver="VenueAgent",
            performative="REQUEST",
            content={
                "action": "check_slot",
                "venue_id": v_id,
                "start_time": clash_start.isoformat(),
                "end_time": clash_end.isoformat()
            }
        )
        return await message_bus.send(msg)

    res = asyncio.run(run())
    assert res is not None
    assert res.performative == "PROPOSE"
    assert "occupied" in res.content.get("reason", "").lower() or "clash" in res.content.get("reason", "").lower()
    assert "suggested_slot" in res.content

def test_venue_agent_weekend_pricing():
    """Rule V3: VenueAgent applies 1.25x weekend peak rate."""
    saturday = datetime(2026, 11, 21, 10, 0, 0)
    saturday_end = datetime(2026, 11, 21, 14, 0, 0)  # 4 hours
    venue = Venue(name="Arena", hourly_rate=100.0)
    price = venue_agent._calculate_hire_price(venue, saturday, saturday_end)
    assert price == 500.0  # 4 * 100 * 1.25


# ==============================================================
# 2. BookingAgent: Quantity Limits, Anti-Overselling & Refund Policy
# ==============================================================
def test_booking_agent_refuses_over_six_tickets():
    """Rule B1: Refuse booking requests for >6 tickets."""
    async def run():
        msg = AgentMessage(
            sender="TestHarness",
            receiver="BookingAgent",
            performative="REQUEST",
            content={
                "action": "book_tickets",
                "user_id": 3,
                "event_id": 1,
                "ticket_category_id": 1,
                "quantity": 8
            }
        )
        return await message_bus.send(msg)

    res = asyncio.run(run())
    assert res.performative == "REFUSE"
    assert "maximum 6 tickets" in res.content.get("reason", "").lower()

def test_booking_agent_anti_overselling():
    """Rule B2: Concurrency & capacity check prevents selling more tickets than available."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        v = db.query(Venue).first()
        test_ev = Event(
            title="Exclusive Masterclass",
            category="Workshop",
            venue_id=v.id,
            start_time=now + timedelta(days=10),
            end_time=now + timedelta(days=10, hours=2),
            status="published"
        )
        db.add(test_ev)
        db.commit()
        db.refresh(test_ev)

        cat = TicketCategory(
            event_id=test_ev.id,
            name="Limited Seat",
            price=50.0,
            total_quantity=2,
            sold_quantity=0
        )
        db.add(cat)
        db.commit()
        db.refresh(cat)
        ev_id = test_ev.id
        cat_id = cat.id
    finally:
        db.close()

    async def run():
        # Step 1: Book the remaining 2 tickets (allowed)
        r1 = await message_bus.send(AgentMessage(
            sender="TestHarness", receiver="BookingAgent", performative="REQUEST",
            content={"action": "book_tickets", "user_id": 3, "event_id": ev_id, "ticket_category_id": cat_id, "quantity": 2}
        ))
        # Step 2: Attempt to book 1 more (oversell attempt -> MUST BE REFUSED)
        r2 = await message_bus.send(AgentMessage(
            sender="TestHarness", receiver="BookingAgent", performative="REQUEST",
            content={"action": "book_tickets", "user_id": 3, "event_id": ev_id, "ticket_category_id": cat_id, "quantity": 1}
        ))
        return r1, r2

    res1, res2 = asyncio.run(run())
    assert res1.performative == "AGREE"
    assert res2.performative == "REFUSE"
    assert "sold out" in res2.content.get("reason", "").lower() or "insufficient" in res2.content.get("reason", "").lower()

def test_booking_agent_refund_policy():
    """Rule B3: BookingAgent applies stated refund policy (>24h = 100% refund)."""
    db = SessionLocal()
    try:
        # Use seeded demo booking
        booking = db.query(Booking).filter(Booking.status == "confirmed").first()
        assert booking is not None
        b_id = booking.id
        u_id = booking.user_id
        expected_refund = booking.total_price
    finally:
        db.close()

    async def run():
        msg = AgentMessage(
            sender="TestHarness",
            receiver="BookingAgent",
            performative="REQUEST",
            content={"action": "cancel_booking", "booking_id": b_id, "user_id": u_id}
        )
        return await message_bus.send(msg)

    res = asyncio.run(run())
    assert res.performative == "AGREE"
    assert res.content.get("refund_percent") == 100
    assert res.content.get("refund_amount") == expected_refund


# ==============================================================
# 3. TicketAgent: Unique Codes, Single Admission & Suspicious Scan Alert
# ==============================================================
def test_ticket_agent_single_admit_and_double_checkin():
    """Rule T1 & T2: Ticket admitted once; duplicate scan rejected."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        v = db.query(Venue).first()
        # Create an active event starting right now
        live_ev = Event(
            title="Live Concert Tonight",
            category="Cultural",
            venue_id=v.id,
            start_time=now - timedelta(minutes=15),
            end_time=now + timedelta(hours=3),
            status="published"
        )
        db.add(live_ev)
        db.commit()
        db.refresh(live_ev)

        cat = TicketCategory(event_id=live_ev.id, name="General", price=20.0, total_quantity=10, sold_quantity=1)
        db.add(cat)
        db.commit()

        u = db.query(User).filter(User.role == "attendee").first()
        b = Booking(
            booking_reference="BK-LIVE-001",
            user_id=u.id,
            event_id=live_ev.id,
            ticket_category_id=cat.id,
            quantity=1,
            unit_price=20.0,
            total_price=20.0,
            payment_reference="SIM-PAY-LIVE",
            status="confirmed"
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        ev_id = live_ev.id
        b_id = b.id
        u_id = u.id
    finally:
        db.close()

    async def run():
        # Issue ticket
        issue = await message_bus.send(AgentMessage(
            sender="BookingAgent", receiver="TicketAgent", performative="REQUEST",
            content={"action": "issue_tickets", "booking_id": b_id, "user_id": u_id, "event_id": ev_id, "quantity": 1}
        ))
        code = issue.content["tickets"][0]

        # Scan 1: First scan -> AGREE (admitted)
        scan1 = await message_bus.send(AgentMessage(
            sender="Scanner", receiver="TicketAgent", performative="REQUEST",
            content={"action": "verify_and_checkin", "ticket_code": code, "event_id": ev_id}
        ))

        # Scan 2: Second scan -> REFUSE (already used)
        scan2 = await message_bus.send(AgentMessage(
            sender="Scanner", receiver="TicketAgent", performative="REQUEST",
            content={"action": "verify_and_checkin", "ticket_code": code, "event_id": ev_id}
        ))
        return scan1, scan2

    s1, s2 = asyncio.run(run())
    assert s1.performative == "AGREE"
    assert "admit" in s1.content.get("message", "").lower()
    assert s2.performative == "REFUSE"
    assert "already" in s2.content.get("reason", "").lower()

def test_ticket_agent_suspicious_duplicate_scans():
    """Rule T3: Repeated scans (>2) on admitted ticket trigger security alert."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        v = db.query(Venue).first()
        live_ev = Event(
            title="Active Gala",
            category="Cultural",
            venue_id=v.id,
            start_time=now - timedelta(minutes=10),
            end_time=now + timedelta(hours=3),
            status="published"
        )
        db.add(live_ev)
        db.commit()

        cat = TicketCategory(event_id=live_ev.id, name="Standard", price=15.0, total_quantity=10, sold_quantity=1)
        db.add(cat)
        db.commit()

        u = db.query(User).filter(User.role == "attendee").first()
        b = Booking(
            booking_reference="BK-GALA-001",
            user_id=u.id,
            event_id=live_ev.id,
            ticket_category_id=cat.id,
            quantity=1,
            unit_price=15.0,
            total_price=15.0,
            payment_reference="SIM-PAY-GALA",
            status="confirmed"
        )
        db.add(b)
        db.commit()
        ev_id = live_ev.id
        b_id = b.id
        u_id = u.id
    finally:
        db.close()

    async def run():
        issue = await message_bus.send(AgentMessage(
            sender="BookingAgent", receiver="TicketAgent", performative="REQUEST",
            content={"action": "issue_tickets", "booking_id": b_id, "user_id": u_id, "event_id": ev_id, "quantity": 1}
        ))
        code = issue.content["tickets"][0]
        # Scan 1: Admit
        await message_bus.send(AgentMessage(sender="Scanner", receiver="TicketAgent", performative="REQUEST", content={"action": "verify_and_checkin", "ticket_code": code, "event_id": ev_id}))
        # Scan 2: Already used
        await message_bus.send(AgentMessage(sender="Scanner", receiver="TicketAgent", performative="REQUEST", content={"action": "verify_and_checkin", "ticket_code": code, "event_id": ev_id}))
        # Scan 3: Suspicious repeated scan detection!
        return await message_bus.send(AgentMessage(sender="Scanner", receiver="TicketAgent", performative="REQUEST", content={"action": "verify_and_checkin", "ticket_code": code, "event_id": ev_id}))

    s3 = asyncio.run(run())
    assert s3.performative == "REFUSE"
    assert "suspicious" in s3.content.get("reason", "").lower() or "security alert" in s3.content.get("reason", "").lower()


# ==============================================================
# 4. WaitlistAgent: Reactive Allocation & Proactive Sweep
# ==============================================================
def test_waitlist_agent_reactive_and_proactive():
    """Rule W1 & W2: Waitlist reacts to seats freed and assigns timed hold; proactive sweep runs."""
    async def run():
        # Join waitlist
        await message_bus.send(AgentMessage(
            sender="TestHarness", receiver="WaitlistAgent", performative="REQUEST",
            content={"action": "join_waitlist", "user_id": 3, "event_id": 1, "ticket_category_id": 1}
        ))
        # Reactive trigger
        freed = await message_bus.send(AgentMessage(
            sender="BookingAgent", receiver="WaitlistAgent", performative="INFORM",
            content={"action": "seats_freed", "event_id": 1, "ticket_category_id": 1, "freed_seats": 1}
        ))
        # Proactive sweep
        await waitlist_agent.proactive_expiry_sweep()
        return freed

    res = asyncio.run(run())
    assert res.performative in ["AGREE", "INFORM"]
    if res.performative == "AGREE":
        assert "expires_at" in res.content


# ==============================================================
# 5. NotificationAgent: Listens to broadcast INFORM
# ==============================================================
def test_notification_agent_listener():
    """Rule N1: NotificationAgent receives broadcast INFORM and creates persistent alert."""
    async def run():
        msg = AgentMessage(
            sender="System", receiver="NotificationAgent", performative="INFORM",
            content={"user_id": 3, "message": "Your booking is verified!", "event_type": "booking_alert"}
        )
        await message_bus.broadcast(msg)

    asyncio.run(run())
    db = SessionLocal()
    try:
        notif = db.query(Notification).filter(Notification.user_id == 3).first()
        assert notif is not None
    finally:
        db.close()


# ==============================================================
# 6. Role-Based Access Control (RBAC) & Agent HTTP Inbox
# ==============================================================
def test_role_based_access_control():
    """Refuse unauthorized roles from staff scanner."""
    client = TestClient(app, raise_server_exceptions=False)
    # Attendee user token trying to access /scanner -> 403 Forbidden
    attendee_token = create_access_token("student1@student.edu", "attendee")
    res = client.get("/scanner", headers={"Authorization": f"Bearer {attendee_token}"})
    assert res.status_code == 403

def test_agent_http_inbox():
    """Part C: Agent HTTP inbox endpoint accepting JSON messages."""
    client = TestClient(app, raise_server_exceptions=False)
    res = client.post("/api/agents/BookingAgent/inbox", json={
        "sender": "ExternalPartnerService",
        "receiver": "BookingAgent",
        "performative": "REQUEST",
        "content": {"action": "book_tickets", "quantity": 12}  # Over limit
    })
    assert res.status_code == 200
    data = res.json()
    assert data["performative"] == "REFUSE"
