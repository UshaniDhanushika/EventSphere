from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import relationship
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(150), unique=True, nullable=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="attendee", nullable=False)  # admin, staff, attendee
    created_at = Column(DateTime, default=datetime.utcnow)

    bookings = relationship("Booking", back_populates="user")
    tickets = relationship("Ticket", back_populates="user")
    hires = relationship("PrivateHire", back_populates="user")
    notifications = relationship("Notification", back_populates="user")


class Venue(Base):
    __tablename__ = "venues"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    location = Column(String(200), nullable=False)
    capacity = Column(Integer, nullable=False)
    description = Column(Text, default="")
    image_url = Column(String(500), default="")
    hourly_rate = Column(Float, default=100.0)
    open_for_private_hire = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    events = relationship("Event", back_populates="venue")
    hires = relationship("PrivateHire", back_populates="venue")


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    category = Column(String(100), nullable=False)  # Academic, Workshop, Sports, Cultural, Clubs
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    description = Column(Text, default="")
    image_url = Column(String(500), default="")
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    status = Column(String(20), default="published")  # draft, published, cancelled
    created_at = Column(DateTime, default=datetime.utcnow)

    venue = relationship("Venue", back_populates="events")
    ticket_categories = relationship("TicketCategory", back_populates="event", cascade="all, delete-orphan")
    bookings = relationship("Booking", back_populates="event")
    tickets = relationship("Ticket", back_populates="event")
    waitlist_entries = relationship("WaitlistEntry", back_populates="event")


class TicketCategory(Base):
    __tablename__ = "ticket_categories"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    name = Column(String(100), nullable=False)  # e.g., Standard, VIP, Student
    price = Column(Float, nullable=False)
    total_quantity = Column(Integer, nullable=False)
    sold_quantity = Column(Integer, default=0, nullable=False)

    event = relationship("Event", back_populates="ticket_categories")
    bookings = relationship("Booking", back_populates="ticket_category")


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)
    booking_reference = Column(String(32), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    ticket_category_id = Column(Integer, ForeignKey("ticket_categories.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    total_price = Column(Float, nullable=False)
    payment_reference = Column(String(64), nullable=False)
    status = Column(String(20), default="confirmed")  # confirmed, cancelled, refunded
    refund_amount = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="bookings")
    event = relationship("Event", back_populates="bookings")
    ticket_category = relationship("TicketCategory", back_populates="bookings")
    tickets = relationship("Ticket", back_populates="booking", cascade="all, delete-orphan")


class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, index=True)
    ticket_code = Column(String(64), unique=True, nullable=False, index=True)
    booking_id = Column(Integer, ForeignKey("bookings.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    status = Column(String(20), default="valid")  # valid, admitted, cancelled
    admitted_at = Column(DateTime, nullable=True)
    scan_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    booking = relationship("Booking", back_populates="tickets")
    user = relationship("User", back_populates="tickets")
    event = relationship("Event", back_populates="tickets")


class PrivateHire(Base):
    __tablename__ = "private_hires"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    purpose = Column(String(255), nullable=False)
    guest_count = Column(Integer, nullable=False)
    total_price = Column(Float, default=0.0)
    status = Column(String(20), default="confirmed")  # confirmed, escalated, approved, rejected
    alternative_proposal = Column(Text, nullable=True)
    admin_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="hires")
    venue = relationship("Venue", back_populates="hires")


class WaitlistEntry(Base):
    __tablename__ = "waitlist_entries"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    ticket_category_id = Column(Integer, ForeignKey("ticket_categories.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String(20), default="waiting")  # waiting, offered, claimed, expired
    offered_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    event = relationship("Event", back_populates="waitlist_entries")
    user = relationship("User")
    ticket_category = relationship("TicketCategory")


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(String(64), index=True, nullable=False)
    sender = Column(String(50), nullable=False)
    receiver = Column(String(50), nullable=False)
    performative = Column(String(20), nullable=False)  # REQUEST, AGREE, REFUSE, PROPOSE, INFORM, FAILURE
    content_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(150), nullable=False)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="notifications")


class AgentBelief(Base):
    __tablename__ = "agent_beliefs"

    id = Column(Integer, primary_key=True, index=True)
    agent_name = Column(String(50), index=True, nullable=False)
    belief_key = Column(String(100), index=True, nullable=False)
    belief_value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
