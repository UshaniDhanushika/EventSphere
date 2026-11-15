import json
import uuid
import secrets
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from sqlalchemy import update
from sqlalchemy.orm import Session
from database import SessionLocal
from models import (
    utcnow,
    AgentMessage as AgentMessageModel,
    AgentBelief,
    Venue,
    Event,
    TicketCategory,
    Booking,
    Ticket,
    PrivateHire,
    WaitlistEntry,
    Notification,
    User
)


class AgentMessage:
    """Structured message adhering to multi-agent communication standards.
    Performatives: REQUEST, AGREE, REFUSE, PROPOSE, INFORM, FAILURE
    """
    def __init__(
        self,
        sender: str,
        receiver: str,
        performative: str,
        content: Dict[str, Any],
        conversation_id: Optional[str] = None,
        timestamp: Optional[datetime] = None
    ):
        self.conversation_id = conversation_id or str(uuid.uuid4())[:8]
        self.sender = sender
        self.receiver = receiver
        self.performative = performative.upper()
        self.content = content
        self.timestamp = timestamp or utcnow()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "sender": self.sender,
            "receiver": self.receiver,
            "performative": self.performative,
            "content": self.content,
            "timestamp": self.timestamp.isoformat()
        }


class AgentMessageBus:
    """Central message bus routing messages between agents without direct function coupling.
    Logs every message in the database for auditability and the organiser dashboard.
    """
    def __init__(self):
        self._agents: Dict[str, 'BaseAgent'] = {}

    def register_agent(self, agent: 'BaseAgent'):
        self._agents[agent.name] = agent
        agent.bus = self

    def log_message(self, message: AgentMessage):
        """Persist message for dashboard trace log."""
        db = SessionLocal()
        try:
            db_msg = AgentMessageModel(
                conversation_id=message.conversation_id,
                sender=message.sender,
                receiver=message.receiver,
                performative=message.performative,
                content_json=json.dumps(message.content),
                created_at=message.timestamp
            )
            db.add(db_msg)
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"[MessageBus Error] Failed to log message: {e}")
        finally:
            db.close()

    async def send(self, message: AgentMessage) -> Optional[AgentMessage]:
        """Send message directly to a target agent."""
        self.log_message(message)
        receiver_agent = self._agents.get(message.receiver)
        if not receiver_agent:
            return AgentMessage(
                sender="System",
                receiver=message.sender,
                performative="FAILURE",
                content={"reason": f"Agent '{message.receiver}' not found"},
                conversation_id=message.conversation_id
            )
        response = await receiver_agent.receive_message(message)
        if response:
            self.log_message(response)
        return response

    async def broadcast(self, message: AgentMessage) -> List[AgentMessage]:
        """Broadcast an INFORM message to all registered agents (e.g. NotificationAgent)."""
        self.log_message(message)
        responses = []
        for name, agent in self._agents.items():
            if name != message.sender:
                res = await agent.receive_message(message)
                if res:
                    self.log_message(res)
                    responses.append(res)
        return responses


class BaseAgent:
    """Base class for all software agents providing persistent beliefs and messaging."""
    def __init__(self, name: str, goal: str):
        self.name = name
        self.goal = goal
        self.bus: Optional[AgentMessageBus] = None

    def get_belief(self, key: str, default: Any = None) -> Any:
        """Read persistent belief from database."""
        db = SessionLocal()
        try:
            belief = db.query(AgentBelief).filter(
                AgentBelief.agent_name == self.name,
                AgentBelief.belief_key == key
            ).first()
            if belief:
                return json.loads(belief.belief_value)
            return default
        finally:
            db.close()

    def set_belief(self, key: str, value: Any):
        """Save persistent belief to database that survives restarts."""
        db = SessionLocal()
        try:
            belief = db.query(AgentBelief).filter(
                AgentBelief.agent_name == self.name,
                AgentBelief.belief_key == key
            ).first()
            if not belief:
                belief = AgentBelief(
                    agent_name=self.name,
                    belief_key=key,
                    belief_value=json.dumps(value)
                )
                db.add(belief)
            else:
                belief.belief_value = json.dumps(value)
                belief.updated_at = utcnow()
            db.commit()
        finally:
            db.close()

    async def receive_message(self, message: AgentMessage) -> Optional[AgentMessage]:
        raise NotImplementedError


# ==========================================
# 1. VenueAgent
# Goal: Keep every venue's calendar free of clashes
# ==========================================
class VenueAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="VenueAgent",
            goal="Keep every venue's calendar free of clashes and manage venue reservations"
        )
        self.turnaround_buffer_minutes = 60  # 1-hour buffer

    async def receive_message(self, message: AgentMessage) -> Optional[AgentMessage]:
        action = message.content.get("action")

        if message.performative == "REQUEST":
            if action == "check_slot":
                return self._handle_check_slot(message)
            elif action == "evaluate_private_hire":
                return await self._handle_private_hire(message)

        return None

    def _has_clash(self, db: Session, venue_id: int, start_time: datetime, end_time: datetime, exclude_event_id: Optional[int] = None) -> bool:
        """Checks if a venue is occupied considering the turnaround buffer."""
        buffer = timedelta(minutes=self.turnaround_buffer_minutes)
        buffered_start = start_time - buffer
        buffered_end = end_time + buffer

        # Check existing published/draft events
        event_query = db.query(Event).filter(
            Event.venue_id == venue_id,
            Event.status != "cancelled",
            Event.start_time < buffered_end,
            Event.end_time > buffered_start
        )
        if exclude_event_id:
            event_query = event_query.filter(Event.id != exclude_event_id)
        if event_query.first():
            return True

        # Check existing confirmed/approved private hires
        hire_clash = db.query(PrivateHire).filter(
            PrivateHire.venue_id == venue_id,
            PrivateHire.status.in_(["confirmed", "approved"]),
            PrivateHire.start_time < buffered_end,
            PrivateHire.end_time > buffered_start
        ).first()
        if hire_clash:
            return True

        return False

    def _calculate_hire_price(self, venue: Venue, start_time: datetime, end_time: datetime) -> float:
        """Calculates hire price with weekend peak rate (Part C advanced feature)."""
        duration_hours = max(1.0, (end_time - start_time).total_seconds() / 3600.0)
        base_price = duration_hours * venue.hourly_rate
        # Weekend check: Saturday=5, Sunday=6
        is_weekend = start_time.weekday() in (5, 6)
        if is_weekend:
            base_price *= 1.25  # 25% peak rate for weekends
        return round(base_price, 2)

    def _handle_check_slot(self, message: AgentMessage) -> AgentMessage:
        """Checks if an event time slot is free with a turnaround buffer."""
        db = SessionLocal()
        try:
            venue_id = message.content.get("venue_id")
            start = datetime.fromisoformat(message.content.get("start_time"))
            end = datetime.fromisoformat(message.content.get("end_time"))
            exclude_id = message.content.get("exclude_event_id")

            venue = db.query(Venue).filter(Venue.id == venue_id).first()
            if not venue:
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="REFUSE",
                    content={"reason": f"Venue with ID {venue_id} does not exist"},
                    conversation_id=message.conversation_id
                )

            if self._has_clash(db, venue_id, start, end, exclude_id):
                # Suggest next available slot
                alt_start = end + timedelta(hours=2)
                alt_end = alt_start + (end - start)
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="PROPOSE",
                    content={
                        "reason": f"Venue '{venue.name}' is already occupied during this time or within the 1-hour turnaround buffer.",
                        "suggested_slot": {
                            "start_time": alt_start.isoformat(),
                            "end_time": alt_end.isoformat()
                        }
                    },
                    conversation_id=message.conversation_id
                )

            return AgentMessage(
                sender=self.name,
                receiver=message.sender,
                performative="AGREE",
                content={"message": "Slot is free and complies with turnaround buffer", "venue_name": venue.name},
                conversation_id=message.conversation_id
            )
        finally:
            db.close()

    async def _handle_private_hire(self, message: AgentMessage) -> AgentMessage:
        """Evaluates private hire requests: routine approval, counter-proposal, or escalation."""
        db = SessionLocal()
        try:
            venue_id = message.content.get("venue_id")
            start = datetime.fromisoformat(message.content.get("start_time"))
            end = datetime.fromisoformat(message.content.get("end_time"))
            guest_count = message.content.get("guest_count", 0)
            user_id = message.content.get("user_id")
            purpose = message.content.get("purpose", "")

            venue = db.query(Venue).filter(Venue.id == venue_id).first()
            if not venue:
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="REFUSE",
                    content={"reason": "Selected venue does not exist."},
                    conversation_id=message.conversation_id
                )

            if not venue.open_for_private_hire:
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="REFUSE",
                    content={"reason": f"Venue '{venue.name}' is not marked open for private hire."},
                    conversation_id=message.conversation_id
                )

            if guest_count > venue.capacity:
                # Counter-propose a larger venue that fits
                alt_venues = db.query(Venue).filter(
                    Venue.capacity >= guest_count,
                    Venue.open_for_private_hire == True
                ).all()
                alt_names = [v.name for v in alt_venues]
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="PROPOSE",
                    content={
                        "reason": f"Requested guests ({guest_count}) exceeds venue capacity ({venue.capacity}).",
                        "alternative_venues": alt_names
                    },
                    conversation_id=message.conversation_id
                )

            duration_hours = (end - start).total_seconds() / 3600.0
            price = self._calculate_hire_price(venue, start, end)

            # Check clashes
            if self._has_clash(db, venue_id, start, end):
                # Check another slot or venue
                alt_start = end + timedelta(hours=3)
                alt_end = alt_start + timedelta(hours=duration_hours)
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="PROPOSE",
                    content={
                        "reason": f"Venue '{venue.name}' is not available at this time due to scheduling or buffer clash.",
                        "suggested_slot": {
                            "start_time": alt_start.isoformat(),
                            "end_time": alt_end.isoformat()
                        }
                    },
                    conversation_id=message.conversation_id
                )

            # Routine vs Escalation rule:
            # Routine: guest_count <= 100 and duration <= 8 hours -> Auto-confirm
            # Non-routine: guest_count > 100 or duration > 8 hours -> Escalate to admin
            if guest_count > 100 or duration_hours > 8:
                hire = PrivateHire(
                    user_id=user_id,
                    venue_id=venue_id,
                    start_time=start,
                    end_time=end,
                    purpose=purpose,
                    guest_count=guest_count,
                    total_price=price,
                    status="escalated"
                )
                db.add(hire)
                db.commit()
                db.refresh(hire)

                # Broadcast inform to notification agent
                if self.bus:
                    await self.bus.broadcast(AgentMessage(
                        sender=self.name,
                        receiver="NotificationAgent",
                        performative="INFORM",
                        content={
                            "event_type": "hire_escalated",
                            "user_id": user_id,
                            "hire_id": hire.id,
                            "message": f"Private hire request for '{venue.name}' escalated for Admin approval due to size ({guest_count} guests)."
                        },
                        conversation_id=message.conversation_id
                    ))

                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="PROPOSE",
                    content={
                        "status": "escalated",
                        "hire_id": hire.id,
                        "price": price,
                        "reason": "Request exceeds routine limits (>100 guests or >8 hrs). Submitted for Admin manual review."
                    },
                    conversation_id=message.conversation_id
                )

            # Routine: confirmed automatically
            hire = PrivateHire(
                user_id=user_id,
                venue_id=venue_id,
                start_time=start,
                end_time=end,
                purpose=purpose,
                guest_count=guest_count,
                total_price=price,
                status="confirmed"
            )
            db.add(hire)
            db.commit()
            db.refresh(hire)

            if self.bus:
                await self.bus.broadcast(AgentMessage(
                    sender=self.name,
                    receiver="NotificationAgent",
                    performative="INFORM",
                    content={
                        "event_type": "hire_confirmed",
                        "user_id": user_id,
                        "hire_id": hire.id,
                        "message": f"Private hire for '{venue.name}' automatically confirmed! Total: Rs. {price:.2f}."
                    },
                    conversation_id=message.conversation_id
                ))

            return AgentMessage(
                sender=self.name,
                receiver=message.sender,
                performative="AGREE",
                content={
                    "status": "confirmed",
                    "hire_id": hire.id,
                    "price": price,
                    "message": "Routine private hire automatically confirmed."
                },
                conversation_id=message.conversation_id
            )
        finally:
            db.close()


# ==========================================
# 2. BookingAgent
# Goal: Sell tickets fairly without overselling
# ==========================================
class BookingAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="BookingAgent",
            goal="Sell tickets fairly without overselling, manage payments and cancellations"
        )
        self.max_tickets_per_booking = 6

    async def receive_message(self, message: AgentMessage) -> Optional[AgentMessage]:
        action = message.content.get("action")

        if message.performative == "REQUEST":
            if action == "book_tickets":
                return await self._handle_booking(message)
            elif action == "cancel_booking":
                return await self._handle_cancellation(message)

        elif message.performative == "INFORM":
            if action == "event_cancelled":
                return await self._handle_event_cancellation(message)

        return None

    async def _handle_booking(self, message: AgentMessage) -> AgentMessage:
        """Processes ticket booking with concurrency protection and quantity limits."""
        db = SessionLocal()
        try:
            user_id = message.content.get("user_id")
            event_id = message.content.get("event_id")
            category_id = message.content.get("ticket_category_id")
            quantity = message.content.get("quantity", 1)

            # Rule B1: Refuse booking requests of more than 6 tickets
            if quantity > self.max_tickets_per_booking:
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="REFUSE",
                    content={"reason": f"Maximum {self.max_tickets_per_booking} tickets allowed per booking."},
                    conversation_id=message.conversation_id
                )

            if quantity <= 0:
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="REFUSE",
                    content={"reason": "Quantity must be at least 1."},
                    conversation_id=message.conversation_id
                )

            # Atomic database capacity check
            cat = db.query(TicketCategory).filter(
                TicketCategory.id == category_id,
                TicketCategory.event_id == event_id
            ).first()

            if not cat:
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="REFUSE",
                    content={"reason": "Ticket category not found for this event."},
                    conversation_id=message.conversation_id
                )

            event = db.query(Event).filter(Event.id == event_id).first()
            if not event or event.status != "published":
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="REFUSE",
                    content={"reason": "Event is not open for booking."},
                    conversation_id=message.conversation_id
                )

            # Concurrency protection check
            available = cat.total_quantity - cat.sold_quantity
            if available < quantity:
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="REFUSE",
                    content={
                        "reason": f"Sold out or insufficient tickets available (Requested: {quantity}, Left: {max(0, available)}).",
                        "available": max(0, available)
                    },
                    conversation_id=message.conversation_id
                )

            # Reserve seats atomically (Rule B2): a single conditional UPDATE that only
            # succeeds if enough seats remain. The database applies it row-by-row, so when two
            # requests race for the last seat only one UPDATE matches; the other gets rowcount 0.
            result = db.execute(
                update(TicketCategory)
                .where(
                    TicketCategory.id == category_id,
                    TicketCategory.sold_quantity + quantity <= TicketCategory.total_quantity
                )
                .values(sold_quantity=TicketCategory.sold_quantity + quantity)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                db.rollback()
                db.refresh(cat)
                left = max(0, cat.total_quantity - cat.sold_quantity)
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="REFUSE",
                    content={
                        "reason": f"Sold out or insufficient tickets available (Requested: {quantity}, Left: {left}).",
                        "available": left
                    },
                    conversation_id=message.conversation_id
                )
            total_price = round(quantity * cat.price, 2)
            payment_ref = f"SIM-PAY-{secrets.token_hex(4).upper()}"
            booking_ref = f"BK-{secrets.token_hex(4).upper()}"

            booking = Booking(
                booking_reference=booking_ref,
                user_id=user_id,
                event_id=event_id,
                ticket_category_id=category_id,
                quantity=quantity,
                unit_price=cat.price,
                total_price=total_price,
                payment_reference=payment_ref,
                status="confirmed"
            )
            db.add(booking)
            db.commit()
            db.refresh(booking)

            # Request TicketAgent to issue tickets via MessageBus
            tickets_issued = []
            if self.bus:
                ticket_req = AgentMessage(
                    sender=self.name,
                    receiver="TicketAgent",
                    performative="REQUEST",
                    content={
                        "action": "issue_tickets",
                        "booking_id": booking.id,
                        "user_id": user_id,
                        "event_id": event_id,
                        "quantity": quantity
                    },
                    conversation_id=message.conversation_id
                )
                ticket_res = await self.bus.send(ticket_req)
                if ticket_res and ticket_res.performative == "AGREE":
                    tickets_issued = ticket_res.content.get("tickets", [])

                # Notify user
                await self.bus.broadcast(AgentMessage(
                    sender=self.name,
                    receiver="NotificationAgent",
                    performative="INFORM",
                    content={
                        "event_type": "booking_confirmed",
                        "user_id": user_id,
                        "booking_reference": booking_ref,
                        "message": f"Your booking for '{event.title}' is confirmed! ({quantity} tickets, Ref: {booking_ref})"
                    },
                    conversation_id=message.conversation_id
                ))

            return AgentMessage(
                sender=self.name,
                receiver=message.sender,
                performative="AGREE",
                content={
                    "booking_id": booking.id,
                    "booking_reference": booking.booking_reference,
                    "payment_reference": payment_ref,
                    "total_price": total_price,
                    "tickets": tickets_issued
                },
                conversation_id=message.conversation_id
            )
        finally:
            db.close()

    async def _handle_cancellation(self, message: AgentMessage) -> AgentMessage:
        """Calculates refund according to stated policy and informs WaitlistAgent."""
        db = SessionLocal()
        try:
            booking_id = message.content.get("booking_id")
            user_id = message.content.get("user_id")

            booking = db.query(Booking).filter(
                Booking.id == booking_id,
                Booking.user_id == user_id,
                Booking.status == "confirmed"
            ).first()

            if not booking:
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="REFUSE",
                    content={"reason": "Active booking not found."},
                    conversation_id=message.conversation_id
                )

            event = booking.event
            now = utcnow()
            hours_until_event = (event.start_time - now).total_seconds() / 3600.0

            # Stated refund policy:
            # > 24 hours: 100% refund
            # 2 to 24 hours: 50% refund
            # < 2 hours: 0% refund
            if hours_until_event > 24:
                refund_percent = 1.0
                policy_note = "100% refund (cancelled > 24 hours in advance)"
            elif hours_until_event >= 2:
                refund_percent = 0.5
                policy_note = "50% refund (cancelled 2-24 hours before event)"
            else:
                refund_percent = 0.0
                policy_note = "No refund (cancelled < 2 hours before event)"

            refund_amount = round(booking.total_price * refund_percent, 2)
            booking.status = "cancelled"
            booking.refund_amount = refund_amount

            # Cancel tickets
            for ticket in booking.tickets:
                ticket.status = "cancelled"

            # Return inventory
            cat = booking.ticket_category
            cat.sold_quantity = max(0, cat.sold_quantity - booking.quantity)

            db.commit()

            # Inform WaitlistAgent and NotificationAgent
            if self.bus:
                await self.bus.broadcast(AgentMessage(
                    sender=self.name,
                    receiver="WaitlistAgent",
                    performative="INFORM",
                    content={
                        "action": "seats_freed",
                        "event_id": event.id,
                        "ticket_category_id": cat.id,
                        "freed_seats": booking.quantity
                    },
                    conversation_id=message.conversation_id
                ))

                await self.bus.broadcast(AgentMessage(
                    sender=self.name,
                    receiver="NotificationAgent",
                    performative="INFORM",
                    content={
                        "event_type": "booking_cancelled",
                        "user_id": user_id,
                        "message": f"Booking {booking.booking_reference} cancelled. Refund: Rs. {refund_amount:.2f} ({policy_note})."
                    },
                    conversation_id=message.conversation_id
                ))

            return AgentMessage(
                sender=self.name,
                receiver=message.sender,
                performative="AGREE",
                content={
                    "booking_id": booking.id,
                    "refund_amount": refund_amount,
                    "refund_percent": int(refund_percent * 100),
                    "policy_applied": policy_note
                },
                conversation_id=message.conversation_id
            )
        finally:
            db.close()

    async def _handle_event_cancellation(self, message: AgentMessage) -> AgentMessage:
        """Processes full refund for all attendees when an event is cancelled by admin."""
        db = SessionLocal()
        try:
            event_id = message.content.get("event_id")
            bookings = db.query(Booking).filter(
                Booking.event_id == event_id,
                Booking.status == "confirmed"
            ).all()

            for b in bookings:
                b.status = "refunded"
                b.refund_amount = b.total_price  # 100% refund
                for t in b.tickets:
                    t.status = "cancelled"

                if self.bus:
                    await self.bus.broadcast(AgentMessage(
                        sender=self.name,
                        receiver="NotificationAgent",
                        performative="INFORM",
                        content={
                            "event_type": "event_cancelled_full_refund",
                            "user_id": b.user_id,
                            "message": f"Event was cancelled by organiser. Full refund of Rs. {b.total_price:.2f} credited."
                        },
                        conversation_id=message.conversation_id
                    ))

            db.commit()
            return AgentMessage(
                sender=self.name,
                receiver=message.sender,
                performative="INFORM",
                content={"message": f"All {len(bookings)} bookings refunded in full."},
                conversation_id=message.conversation_id
            )
        finally:
            db.close()


# ==========================================
# 3. TicketAgent
# Goal: Admit each ticket exactly once
# ==========================================
class TicketAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="TicketAgent",
            goal="Issue unguessable codes and admit each ticket exactly once at the door"
        )

    async def receive_message(self, message: AgentMessage) -> Optional[AgentMessage]:
        action = message.content.get("action")

        if message.performative == "REQUEST":
            if action == "issue_tickets":
                return self._handle_issue(message)
            elif action == "verify_and_checkin":
                return self._handle_checkin(message)

        return None

    def _handle_issue(self, message: AgentMessage) -> AgentMessage:
        db = SessionLocal()
        try:
            booking_id = message.content.get("booking_id")
            user_id = message.content.get("user_id")
            event_id = message.content.get("event_id")
            quantity = message.content.get("quantity", 1)

            issued = []
            for _ in range(quantity):
                # Unique unguessable ticket code
                code = f"TICK-{secrets.token_urlsafe(12).replace('-', '').replace('_', '').upper()}"
                ticket = Ticket(
                    ticket_code=code,
                    booking_id=booking_id,
                    user_id=user_id,
                    event_id=event_id,
                    status="valid",
                    scan_count=0
                )
                db.add(ticket)
                issued.append(code)

            db.commit()
            return AgentMessage(
                sender=self.name,
                receiver=message.sender,
                performative="AGREE",
                content={"tickets": issued},
                conversation_id=message.conversation_id
            )
        finally:
            db.close()

    def _handle_checkin(self, message: AgentMessage) -> AgentMessage:
        """Admit ticket once only; reject duplicate, wrong event, cancelled, or outside window."""
        db = SessionLocal()
        try:
            code = message.content.get("ticket_code", "").strip()
            target_event_id = message.content.get("event_id")

            ticket = db.query(Ticket).filter(Ticket.ticket_code == code).first()
            if not ticket:
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="REFUSE",
                    content={"reason": "Invalid ticket code. Code does not exist in system."},
                    conversation_id=message.conversation_id
                )

            # Increment scan attempt belief
            ticket.scan_count = (ticket.scan_count or 0) + 1
            db.commit()

            # Rule T3: Suspicious repeated scans detection (Part C feature)
            if ticket.status == "admitted" and ticket.scan_count > 2:
                self.set_belief(f"suspicious_{ticket.ticket_code}", ticket.scan_count)
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="REFUSE",
                    content={
                        "reason": f"SECURITY ALERT: Ticket already used and scanned {ticket.scan_count} times! (Suspected duplicate screenshot sharing).",
                        "status": "suspicious_reuse"
                    },
                    conversation_id=message.conversation_id
                )

            if ticket.status == "cancelled":
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="REFUSE",
                    content={"reason": "Ticket has been cancelled or refunded."},
                    conversation_id=message.conversation_id
                )

            if ticket.status == "admitted":
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="REFUSE",
                    content={
                        "reason": f"Ticket has already been admitted at {ticket.admitted_at.strftime('%H:%M:%S') if ticket.admitted_at else 'earlier'}.",
                        "status": "already_used"
                    },
                    conversation_id=message.conversation_id
                )

            # Check event match if provided
            if target_event_id and ticket.event_id != int(target_event_id):
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="REFUSE",
                    content={"reason": f"Wrong event! Ticket is for '{ticket.event.title}' not this event."},
                    conversation_id=message.conversation_id
                )

            # Check-in time window (Rule T4): doors open 3 hours before start and close at event end
            event = ticket.event
            now = utcnow()
            if now < (event.start_time - timedelta(hours=3)):
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="REFUSE",
                    content={"reason": f"Too early for check-in. Doors open 3 hours before start time."},
                    conversation_id=message.conversation_id
                )

            if now > event.end_time:
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="REFUSE",
                    content={"reason": "Check-in closed. This event has already ended.", "status": "outside_checkin_window"},
                    conversation_id=message.conversation_id
                )

            # Mark admitted
            ticket.status = "admitted"
            ticket.admitted_at = utcnow()
            db.commit()

            return AgentMessage(
                sender=self.name,
                receiver=message.sender,
                performative="AGREE",
                content={
                    "message": "Admit attendee! Ticket verified successfully.",
                    "attendee_username": ticket.user.username,
                    "event_title": ticket.event.title,
                    "category": ticket.booking.ticket_category.name if ticket.booking and ticket.booking.ticket_category else "Standard"
                },
                conversation_id=message.conversation_id
            )
        finally:
            db.close()


# ==========================================
# 4. WaitlistAgent
# Goal: Pass freed seats to waiting customers
# Has both Reactive and Proactive scheduled behaviour!
# ==========================================
class WaitlistAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="WaitlistAgent",
            goal="Pass freed seats to waiting customers, hold them with a countdown and re-offer on expiry"
        )
        self.hold_duration_minutes = 10

    async def receive_message(self, message: AgentMessage) -> Optional[AgentMessage]:
        if message.performative == "INFORM":
            action = message.content.get("action")
            if action == "seats_freed":
                return await self._handle_seats_freed(message)

        elif message.performative == "REQUEST":
            action = message.content.get("action")
            if action == "join_waitlist":
                return self._handle_join_waitlist(message)

        return None

    def _handle_join_waitlist(self, message: AgentMessage) -> AgentMessage:
        db = SessionLocal()
        try:
            user_id = message.content.get("user_id")
            event_id = message.content.get("event_id")
            category_id = message.content.get("ticket_category_id")

            existing = db.query(WaitlistEntry).filter(
                WaitlistEntry.user_id == user_id,
                WaitlistEntry.event_id == event_id,
                WaitlistEntry.status.in_(["waiting", "offered"])
            ).first()

            if existing:
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="REFUSE",
                    content={"reason": "You are already on the waitlist for this event."},
                    conversation_id=message.conversation_id
                )

            entry = WaitlistEntry(
                user_id=user_id,
                event_id=event_id,
                ticket_category_id=category_id,
                status="waiting"
            )
            db.add(entry)
            db.commit()

            return AgentMessage(
                sender=self.name,
                receiver=message.sender,
                performative="AGREE",
                content={"message": "Successfully joined waitlist.", "waitlist_id": entry.id},
                conversation_id=message.conversation_id
            )
        finally:
            db.close()

    async def _handle_seats_freed(self, message: AgentMessage) -> AgentMessage:
        """Reactive behaviour: When seats are freed, immediately offer to the next customer in line."""
        db = SessionLocal()
        try:
            event_id = message.content.get("event_id")
            cat_id = message.content.get("ticket_category_id")

            # Find next waiting customer
            next_waiting = db.query(WaitlistEntry).filter(
                WaitlistEntry.event_id == event_id,
                WaitlistEntry.ticket_category_id == cat_id,
                WaitlistEntry.status == "waiting"
            ).order_by(WaitlistEntry.created_at.asc()).first()

            if not next_waiting:
                return AgentMessage(
                    sender=self.name,
                    receiver=message.sender,
                    performative="INFORM",
                    content={"message": "No customers currently waiting on this waitlist."},
                    conversation_id=message.conversation_id
                )

            # Offer seat with timed hold (10 min)
            now = utcnow()
            expires = now + timedelta(minutes=self.hold_duration_minutes)
            next_waiting.status = "offered"
            next_waiting.offered_at = now
            next_waiting.expires_at = expires
            db.commit()

            # Broadcast to NotificationAgent
            if self.bus:
                await self.bus.broadcast(AgentMessage(
                    sender=self.name,
                    receiver="NotificationAgent",
                    performative="INFORM",
                    content={
                        "event_type": "waitlist_seat_offered",
                        "user_id": next_waiting.user_id,
                        "expires_at": expires.isoformat(),
                        "message": f"A freed seat is held for you in '{next_waiting.event.title}'! You have {self.hold_duration_minutes} minutes to claim it."
                    },
                    conversation_id=message.conversation_id
                ))

            return AgentMessage(
                sender=self.name,
                receiver=message.sender,
                performative="AGREE",
                content={
                    "offered_to_user_id": next_waiting.user_id,
                    "expires_at": expires.isoformat()
                },
                conversation_id=message.conversation_id
            )
        finally:
            db.close()

    async def proactive_expiry_sweep(self):
        """Proactive behaviour: Scheduled periodic sweep that expires unclaimed holds and re-offers them."""
        db = SessionLocal()
        try:
            now = utcnow()
            expired_holds = db.query(WaitlistEntry).filter(
                WaitlistEntry.status == "offered",
                WaitlistEntry.expires_at < now
            ).all()

            for hold in expired_holds:
                hold.status = "expired"
                db.commit()

                # Re-offer to the next person in line
                next_in_line = db.query(WaitlistEntry).filter(
                    WaitlistEntry.event_id == hold.event_id,
                    WaitlistEntry.ticket_category_id == hold.ticket_category_id,
                    WaitlistEntry.status == "waiting"
                ).order_by(WaitlistEntry.created_at.asc()).first()

                if next_in_line:
                    expires = now + timedelta(minutes=self.hold_duration_minutes)
                    next_in_line.status = "offered"
                    next_in_line.offered_at = now
                    next_in_line.expires_at = expires
                    db.commit()

                    if self.bus:
                        await self.bus.broadcast(AgentMessage(
                            sender=self.name,
                            receiver="NotificationAgent",
                            performative="INFORM",
                            content={
                                "event_type": "waitlist_seat_offered",
                                "user_id": next_in_line.user_id,
                                "expires_at": expires.isoformat(),
                                "message": f"Seat hold re-offered to you for '{next_in_line.event.title}'! You have 10 minutes to claim."
                            }
                        ))
        except Exception as e:
            print(f"[WaitlistAgent Proactive Sweep Error]: {e}")
        finally:
            db.close()


# ==========================================
# 5. NotificationAgent
# Goal: Tell the right person what affects them
# Listens to all broadcast INFORM messages
# ==========================================
class NotificationAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="NotificationAgent",
            goal="Turn events from other agents into user notifications"
        )

    async def receive_message(self, message: AgentMessage) -> Optional[AgentMessage]:
        if message.performative == "INFORM":
            return self._handle_inform(message)
        return None

    def _handle_inform(self, message: AgentMessage) -> Optional[AgentMessage]:
        user_id = message.content.get("user_id")
        text = message.content.get("message")
        event_type = message.content.get("event_type", "System Notification")

        if user_id and text:
            db = SessionLocal()
            try:
                notif = Notification(
                    user_id=user_id,
                    title=event_type.replace("_", " ").title(),
                    message=text,
                    is_read=False
                )
                db.add(notif)
                db.commit()
            finally:
                db.close()

        return None


# ==========================================
# Instantiate Global Multi-Agent System
# ==========================================
message_bus = AgentMessageBus()

venue_agent = VenueAgent()
booking_agent = BookingAgent()
ticket_agent = TicketAgent()
waitlist_agent = WaitlistAgent()
notification_agent = NotificationAgent()

message_bus.register_agent(venue_agent)
message_bus.register_agent(booking_agent)
message_bus.register_agent(ticket_agent)
message_bus.register_agent(waitlist_agent)
message_bus.register_agent(notification_agent)
