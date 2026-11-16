import time
from datetime import datetime, timedelta
from database import SessionLocal, Base, engine
from models import (
    User, Venue, Event, TicketCategory, Booking, Ticket, Notification, AgentMessage
)
from security import hash_password
import secrets

def seed_database():
    """Initializes database tables and creates default demo accounts, venues, and sample events with connection retry."""
    max_retries = 10
    retry_interval = 2

    # Retry loop to allow cloud databases (e.g. Railway PostgreSQL) time to finish booting
    for attempt in range(1, max_retries + 1):
        try:
            Base.metadata.create_all(bind=engine)
            break
        except Exception as e:
            if attempt == max_retries:
                print(f"[Seed] Database connection failed after {max_retries} attempts: {e}")
                raise
            print(f"[Seed] Database not ready yet (attempt {attempt}/{max_retries}): {e}. Retrying in {retry_interval}s...")
            time.sleep(retry_interval)

    db = SessionLocal()
    try:
        # Check if already seeded
        admin_user = db.query(User).filter(User.username == "admin@eventsphere.com").first()
        if not admin_user:
            # 1. Create Demo Users
            admin = User(
                username="admin@eventsphere.com",
                email="admin@eventsphere.com",
                password_hash=hash_password("admin123"),
                role="admin"
            )
            staff = User(
                username="staff@eventsphere.com",
                email="staff@eventsphere.com",
                password_hash=hash_password("staff123"),
                role="staff"
            )
            attendee = User(
                username="attendee@eventsphere.com",
                email="attendee@eventsphere.com",
                password_hash=hash_password("attendee123"),
                role="attendee"
            )
            db.add_all([admin, staff, attendee])
            db.commit()

            # 2. Create Venues
            v1 = Venue(
                name="University Main Auditorium",
                location="North Campus, Building A",
                capacity=500,
                hourly_rate=120.0,
                open_for_private_hire=True,
                description="State-of-the-art auditorium equipped with surround sound, stage lighting, and tiered seating.",
                image_url="https://images.unsplash.com/photo-1540575467063-178a50c2df87?w=800"
            )
            v2 = Venue(
                name="Science & Innovation Centre",
                location="Technology Park, Level 2",
                capacity=150,
                hourly_rate=80.0,
                open_for_private_hire=True,
                description="High-tech space with interactive displays, projector arrays, and lab demonstration benches.",
                image_url="https://images.unsplash.com/photo-1485827404703-89b55fcc595e?w=800"
            )
            v3 = Venue(
                name="University Sports Arena",
                location="South Campus Sports Complex",
                capacity=800,
                hourly_rate=200.0,
                open_for_private_hire=True,
                description="Multi-purpose indoor sports pavilion with bleachers, electronic scoreboard, and changing facilities.",
                image_url="https://images.unsplash.com/photo-1461896836934-ffe607ba8211?w=800"
            )
            v4 = Venue(
                name="Executive Conference Hall",
                location="Senate Building, 3rd Floor",
                capacity=40,
                hourly_rate=50.0,
                open_for_private_hire=True,
                description="Modern executive boardroom with video conferencing, high-speed WiFi, and executive catering area.",
                image_url="https://images.unsplash.com/photo-1455390582262-044cdead277a?w=800"
            )
            db.add_all([v1, v2, v3, v4])
            db.commit()

            # 3. Create Sample Events
            now = datetime.utcnow()
            e1 = Event(
                title="National AI & Robotics Summit 2026",
                category="Academic",
                venue_id=v1.id,
                description="Join leading researchers, tech innovators, and industry pioneers exploring generative models, robotics, and future computing.",
                image_url="https://images.unsplash.com/photo-1540575467063-178a50c2df87?w=800",
                start_time=now + timedelta(days=5, hours=3),
                end_time=now + timedelta(days=5, hours=8),
                status="published"
            )
            e2 = Event(
                title="Annual Campus Cultural Gala",
                category="Cultural",
                venue_id=v1.id,
                description="A magnificent evening celebrating diversity through music, choreography, drama performances, and culinary treats.",
                image_url="https://images.unsplash.com/photo-1501386761578-eac5c94b800a?w=800",
                start_time=now + timedelta(days=12, hours=10),
                end_time=now + timedelta(days=12, hours=15),
                status="published"
            )
            e3 = Event(
                title="Career Accelerator & Tech Networking",
                category="Workshop",
                venue_id=v2.id,
                description="Connect with top tier tech recruiters, optimize your resume, and practice live mock technical interviews.",
                image_url="https://images.unsplash.com/photo-1552664730-d307ca884978?w=800",
                start_time=now + timedelta(days=7, hours=2),
                end_time=now + timedelta(days=7, hours=6),
                status="published"
            )
            e4 = Event(
                title="Inter-University Basketball Championship Final",
                category="Sports",
                venue_id=v3.id,
                description="Cheer for our university champions in the thrilling season finale showdown against rival university athletes.",
                image_url="https://images.unsplash.com/photo-1461896836934-ffe607ba8211?w=800",
                start_time=now + timedelta(days=3, hours=4),
                end_time=now + timedelta(days=3, hours=7),
                status="published"
            )
            db.add_all([e1, e2, e3, e4])
            db.commit()

            # 4. Create Ticket Categories
            c1_std = TicketCategory(event_id=e1.id, name="Standard Ticket", price=15.0, total_quantity=200, sold_quantity=1)
            c1_vip = TicketCategory(event_id=e1.id, name="VIP Pass", price=35.0, total_quantity=50, sold_quantity=0)
            c1_stu = TicketCategory(event_id=e1.id, name="Student Discount", price=10.0, total_quantity=100, sold_quantity=0)

            c2_std = TicketCategory(event_id=e2.id, name="General Admission", price=20.0, total_quantity=300, sold_quantity=0)
            c2_vip = TicketCategory(event_id=e2.id, name="VIP Front Row", price=45.0, total_quantity=60, sold_quantity=0)

            c3_std = TicketCategory(event_id=e3.id, name="Workshop Access", price=25.0, total_quantity=80, sold_quantity=0)

            c4_std = TicketCategory(event_id=e4.id, name="Bleacher Seat", price=10.0, total_quantity=400, sold_quantity=0)
            c4_court = TicketCategory(event_id=e4.id, name="Courtside Pass", price=30.0, total_quantity=50, sold_quantity=0)

            db.add_all([c1_std, c1_vip, c1_stu, c2_std, c2_vip, c3_std, c4_std, c4_court])
            db.commit()

            # 5. Create a pre-existing demo booking and ticket for immediate gate check-in testing
            b1 = Booking(
                booking_reference="BK-DEMO-2026",
                user_id=attendee.id,
                event_id=e1.id,
                ticket_category_id=c1_std.id,
                quantity=1,
                unit_price=15.0,
                total_price=15.0,
                payment_reference="SIM-PAY-DEMO123",
                status="confirmed"
            )
            db.add(b1)
            db.commit()

            demo_ticket = Ticket(
                ticket_code="DEMO-QR-TICKET-2026",
                booking_id=b1.id,
                user_id=attendee.id,
                event_id=e1.id,
                status="valid",
                scan_count=0
            )
            db.add(demo_ticket)

            # Notification
            n1 = Notification(
                user_id=attendee.id,
                title="Welcome to EventSphere",
                message="Welcome to EventSphere! Your demo booking for the National AI Summit is confirmed.",
                is_read=False
            )
            db.add(n1)

            # Initial Agent Message Log trace
            msg1 = AgentMessage(
                conversation_id="conv-demo-001",
                sender="BookingAgent",
                receiver="TicketAgent",
                performative="REQUEST",
                content_json='{"action": "issue_tickets", "quantity": 1, "booking_reference": "BK-DEMO-2026"}',
                created_at=now
            )
            msg2 = AgentMessage(
                conversation_id="conv-demo-001",
                sender="TicketAgent",
                receiver="BookingAgent",
                performative="AGREE",
                content_json='{"tickets": ["DEMO-QR-TICKET-2026"]}',
                created_at=now + timedelta(seconds=1)
            )
            db.add_all([msg1, msg2])
            db.commit()

            print("[Seed] EventSphere database successfully initialized with demo accounts, venues, and events.")
    finally:
        db.close()

if __name__ == "__main__":
    seed_database()
