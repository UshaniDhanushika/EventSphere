import io
import asyncio
from datetime import datetime, timedelta
from typing import List, Optional
from contextlib import asynccontextmanager

from fastapi import (
    FastAPI, Depends, HTTPException, Request, Response, status, Form
)
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
import qrcode

from database import engine, Base, SessionLocal, get_db
from models import (
    User, Venue, Event, TicketCategory, Booking, Ticket,
    PrivateHire, WaitlistEntry, AgentMessage as AgentMessageModel,
    Notification
)
from security import (
    hash_password, verify_password, create_access_token,
    get_current_user_optional, get_current_user, require_role
)
from agents import (
    message_bus, venue_agent, booking_agent, ticket_agent,
    waitlist_agent, notification_agent, AgentMessage
)
from seed_data import seed_database


# ==========================================
# Background Proactive Agent Task (WaitlistAgent)
# ==========================================
async def waitlist_proactive_sweep_loop():
    """Proactive agent behaviour: runs every 15s to expire holds and re-offer freed seats."""
    while True:
        try:
            await waitlist_agent.proactive_expiry_sweep()
        except Exception as e:
            print(f"[Waitlist Sweep] Error: {e}")
        await asyncio.sleep(15)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Initialize tables and seed demo accounts and events
    seed_database()
    # 2. Start proactive background agent sweep
    sweep_task = asyncio.create_task(waitlist_proactive_sweep_loop())
    yield
    sweep_task.cancel()


# ==========================================
# Initialize FastAPI App
# ==========================================
app = FastAPI(
    title="EventSphere - Multi-Agent Venue & Ticketing Platform",
    description="Full-stack autonomous multi-agent platform for campus events and venue booking (IT4123 ICA 02).",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")


# ==========================================
# Pydantic Request / Response Models
# ==========================================
class UserRegisterSchema(BaseModel):
    username: str
    password: str

class UserLoginSchema(BaseModel):
    username: str
    password: str

class VenueCreateSchema(BaseModel):
    name: str
    location: str
    capacity: int
    hourly_rate: float = 100.0
    description: Optional[str] = ""
    open_for_private_hire: bool = True
    image_url: Optional[str] = ""

class TicketCategoryInput(BaseModel):
    name: str
    price: float
    quantity: int

class EventCreateSchema(BaseModel):
    title: str
    category: str
    venue_id: int
    start_time: str
    end_time: str
    description: Optional[str] = ""
    image_url: Optional[str] = ""
    ticket_categories: Optional[List[TicketCategoryInput]] = None

class BookingRequestSchema(BaseModel):
    event_id: int
    ticket_category_id: int
    quantity: int = 1

class PrivateHireRequestSchema(BaseModel):
    venue_id: int
    start_time: str
    end_time: str
    guest_count: int
    purpose: str

class ScannerCheckinSchema(BaseModel):
    ticket_code: str
    event_id: Optional[int] = None

class HireDecisionSchema(BaseModel):
    decision: str  # approved, rejected

class WaitlistJoinSchema(BaseModel):
    event_id: int
    ticket_category_id: Optional[int] = None

class AgentHttpInboxSchema(BaseModel):
    sender: str
    receiver: str
    performative: str
    content: dict
    conversation_id: Optional[str] = None


# ==========================================
# QR Code Generator Endpoint
# ==========================================
@app.get("/api/tickets/{ticket_code}/qr.png", tags=["Digital Tickets"])
def get_ticket_qr(ticket_code: str):
    """Dynamically generates and streams a PNG QR code for a given ticket."""
    qr = qrcode.QRCode(
        version=1,
        box_size=8,
        border=2,
    )
    qr.add_data(ticket_code)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


# ==========================================
# Web UI Routes (HTML Responsive at 375px+)
# ==========================================
@app.get("/", response_class=HTMLResponse, tags=["Web Pages"])
def page_home(
    request: Request,
    search: Optional[str] = None,
    category: Optional[str] = None,
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    query = db.query(Event).filter(Event.status == "published")
    if category:
        query = query.filter(Event.category == category)
    if search:
        query = query.filter(
            Event.title.ilike(f"%{search}%") | Event.description.ilike(f"%{search}%")
        )
    events = query.order_by(Event.start_time.asc()).all()

    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": user,
        "events": events,
        "search_query": search or "",
        "selected_category": category or "",
        "active_page": "events"
    })


@app.get("/event/{event_id}", response_class=HTMLResponse, tags=["Web Pages"])
def page_event_detail(
    request: Request,
    event_id: int,
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    return templates.TemplateResponse("event_detail.html", {
        "request": request,
        "user": user,
        "event": event,
        "active_page": "events"
    })


@app.get("/my-tickets", response_class=HTMLResponse, tags=["Web Pages"])
def page_my_tickets(
    request: Request,
    success: Optional[str] = None,
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    bookings = db.query(Booking).filter(
        Booking.user_id == user.id
    ).order_by(Booking.created_at.desc()).all()

    return templates.TemplateResponse("my_tickets.html", {
        "request": request,
        "user": user,
        "bookings": bookings,
        "success_ref": success,
        "active_page": "tickets"
    })


@app.get("/hire", response_class=HTMLResponse, tags=["Web Pages"])
def page_private_hire(
    request: Request,
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    venues = db.query(Venue).filter(Venue.open_for_private_hire == True).all()
    my_hires = db.query(PrivateHire).filter(
        PrivateHire.user_id == user.id
    ).order_by(PrivateHire.created_at.desc()).all()

    return templates.TemplateResponse("private_hire.html", {
        "request": request,
        "user": user,
        "venues": venues,
        "my_hires": my_hires,
        "active_page": "hire"
    })


@app.get("/scanner", response_class=HTMLResponse, tags=["Web Pages"])
def page_scanner(
    request: Request,
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.role not in ["admin", "staff"]:
        raise HTTPException(status_code=403, detail="Gate scanner is restricted to Staff or Admin.")

    return templates.TemplateResponse("scanner.html", {
        "request": request,
        "user": user,
        "active_page": "scanner"
    })


@app.get("/admin", response_class=HTMLResponse, tags=["Web Pages"])
def page_admin_dashboard(
    request: Request,
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    if not user or user.role != "admin":
        return RedirectResponse(url="/login", status_code=302)

    venues = db.query(Venue).all()
    events = db.query(Event).all()
    bookings = db.query(Booking).all()
    agent_messages = db.query(AgentMessageModel).order_by(AgentMessageModel.created_at.desc()).limit(100).all()
    escalated_hires = db.query(PrivateHire).filter(PrivateHire.status == "escalated").all()

    # Calculate metrics per event
    event_metrics = []
    chart_labels = []
    chart_sales = []
    chart_checkins = []

    total_tickets_sold = 0
    total_revenue = 0.0
    total_checkins = 0

    for ev in events:
        ev_bookings = [b for b in bookings if b.event_id == ev.id and b.status == "confirmed"]
        sold = sum(b.quantity for b in ev_bookings)
        rev = sum(b.total_price for b in ev_bookings)
        checkins = db.query(Ticket).filter(Ticket.event_id == ev.id, Ticket.status == "admitted").count()

        total_cap = sum(c.total_quantity for c in ev.ticket_categories) or ev.venue.capacity
        fill_pct = min(100, int((sold / max(1, total_cap)) * 100))

        total_tickets_sold += sold
        total_revenue += rev
        total_checkins += checkins

        event_metrics.append({
            "id": ev.id,
            "title": ev.title,
            "category": ev.category,
            "start_time": ev.start_time,
            "venue_name": ev.venue.name,
            "venue_capacity": ev.venue.capacity,
            "total_capacity": total_cap,
            "sold": sold,
            "checkins": checkins,
            "revenue": rev,
            "status": ev.status,
            "fill_percentage": fill_pct
        })

        chart_labels.append(ev.title[:15] + "...")
        chart_sales.append(sold)
        chart_checkins.append(checkins)

    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request,
        "user": user,
        "stats": {
            "total_events": len(events),
            "total_tickets_sold": total_tickets_sold,
            "total_revenue": total_revenue,
            "total_checkins": total_checkins
        },
        "event_metrics": event_metrics,
        "venues": venues,
        "agent_messages": agent_messages,
        "escalated_hires": escalated_hires,
        "chart_labels": chart_labels,
        "chart_sales": chart_sales,
        "chart_checkins": chart_checkins,
        "active_page": "admin"
    })


@app.get("/notifications", response_class=HTMLResponse, tags=["Web Pages"])
def page_notifications(
    request: Request,
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    notifs = db.query(Notification).filter(
        Notification.user_id == user.id
    ).order_by(Notification.created_at.desc()).all()

    return templates.TemplateResponse("notifications.html", {
        "request": request,
        "user": user,
        "notifications": notifs,
        "active_page": "notifications"
    })


# ==========================================
# Authentication Routes (HTML Form & API)
# ==========================================
@app.get("/login", response_class=HTMLResponse, tags=["Authentication"])
def page_login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@app.post("/login", tags=["Authentication"])
def handle_login(
    response: Response,
    request: Request,
    username: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
    body: Optional[UserLoginSchema] = None,
    db: Session = Depends(get_db)
):
    # Support both HTML form and JSON API body
    uname = username or (body.username if body else None)
    pwd = password or (body.password if body else None)

    if not uname or not pwd:
        raise HTTPException(status_code=400, detail="Username and password are required")

    user = db.query(User).filter(User.username == uname).first()
    if not user or not verify_password(pwd, user.password_hash):
        if request.headers.get("accept", "").find("text/html") != -1 and not body:
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": "Invalid username or password"
            }, status_code=401)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = create_access_token(user.username, user.role)

    # If browser form submission, set HTTP-only cookie and redirect to dashboard/home
    if request.headers.get("accept", "").find("text/html") != -1 and not body:
        redirect_url = "/admin" if user.role == "admin" else ("/scanner" if user.role == "staff" else "/")
        resp = RedirectResponse(url=redirect_url, status_code=302)
        resp.set_cookie(key="access_token", value=f"Bearer {token}", httponly=True)
        return resp

    return {
        "access_token": token,
        "token_type": "bearer",
        "username": user.username,
        "role": user.role
    }


@app.get("/register", response_class=HTMLResponse, tags=["Authentication"])
def page_register(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "error": None})

@app.post("/register", tags=["Authentication"])
def handle_register(
    request: Request,
    username: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
    body: Optional[UserRegisterSchema] = None,
    db: Session = Depends(get_db)
):
    uname = username or (body.username if body else None)
    pwd = password or (body.password if body else None)

    if not uname or not pwd:
        raise HTTPException(status_code=400, detail="Username and password required")

    existing = db.query(User).filter(User.username == uname).first()
    if existing:
        if request.headers.get("accept", "").find("text/html") != -1 and not body:
            return templates.TemplateResponse("register.html", {
                "request": request,
                "error": "Username already registered"
            }, status_code=400)
        raise HTTPException(status_code=400, detail="Username already exists")

    new_user = User(
        username=uname,
        email=uname if "@" in uname else f"{uname}@eventsphere.com",
        password_hash=hash_password(pwd),
        role="attendee"
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    if request.headers.get("accept", "").find("text/html") != -1 and not body:
        token = create_access_token(new_user.username, new_user.role)
        resp = RedirectResponse(url="/", status_code=302)
        resp.set_cookie(key="access_token", value=f"Bearer {token}", httponly=True)
        return resp

    return {
        "message": "Registration successful",
        "username": new_user.username,
        "role": new_user.role
    }

@app.get("/logout", tags=["Authentication"])
def handle_logout():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie("access_token")
    return resp

@app.get("/me", tags=["Authentication"])
def get_me(user: User = Depends(get_current_user)):
    return {"username": user.username, "role": user.role, "id": user.id}


# ==========================================
# REST API: Venues (Admin Management)
# ==========================================
@app.get("/api/venues", tags=["Venues"])
def get_venues(db: Session = Depends(get_db)):
    return db.query(Venue).all()

@app.post("/api/venues", tags=["Venues"])
def create_venue(
    venue_in: VenueCreateSchema,
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db)
):
    venue = Venue(
        name=venue_in.name,
        location=venue_in.location,
        capacity=venue_in.capacity,
        hourly_rate=venue_in.hourly_rate,
        description=venue_in.description or "",
        open_for_private_hire=venue_in.open_for_private_hire,
        image_url=venue_in.image_url or ""
    )
    db.add(venue)
    db.commit()
    db.refresh(venue)
    return venue


# ==========================================
# REST API: Events (Admin & Attendee)
# ==========================================
@app.get("/events", tags=["Events"])
@app.get("/api/events", tags=["Events"])
def list_events(db: Session = Depends(get_db)):
    return db.query(Event).filter(Event.status == "published").all()

@app.post("/events", tags=["Events"])
@app.post("/api/events", tags=["Events"])
async def create_event(
    event_in: EventCreateSchema,
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db)
):
    # Rule V1: Ask VenueAgent to check slot & turnaround buffer
    msg = AgentMessage(
        sender="AdminController",
        receiver="VenueAgent",
        performative="REQUEST",
        content={
            "action": "check_slot",
            "venue_id": event_in.venue_id,
            "start_time": event_in.start_time,
            "end_time": event_in.end_time
        }
    )
    res = await message_bus.send(msg)
    if not res or res.performative != "AGREE":
        reason = res.content.get("reason", "Slot rejected by VenueAgent") if res else "No response"
        raise HTTPException(status_code=400, detail=f"VenueAgent refused slot: {reason}")

    # Check total tickets does not exceed venue capacity
    venue = db.query(Venue).filter(Venue.id == event_in.venue_id).first()
    categories_in = event_in.ticket_categories or [TicketCategoryInput(name="Standard", price=20.0, quantity=min(100, venue.capacity))]
    total_requested_tickets = sum(c.quantity for c in categories_in)

    if total_requested_tickets > venue.capacity:
        raise HTTPException(
            status_code=400,
            detail=f"Total tickets ({total_requested_tickets}) exceeds venue capacity ({venue.capacity})."
        )

    start_dt = datetime.fromisoformat(event_in.start_time)
    end_dt = datetime.fromisoformat(event_in.end_time)

    new_event = Event(
        title=event_in.title,
        category=event_in.category,
        venue_id=event_in.venue_id,
        description=event_in.description or "",
        image_url=event_in.image_url or "https://images.unsplash.com/photo-1540575467063-178a50c2df87?w=800",
        start_time=start_dt,
        end_time=end_dt,
        status="published"
    )
    db.add(new_event)
    db.commit()
    db.refresh(new_event)

    for cat in categories_in:
        tc = TicketCategory(
            event_id=new_event.id,
            name=cat.name,
            price=cat.price,
            total_quantity=cat.quantity,
            sold_quantity=0
        )
        db.add(tc)
    db.commit()

    return {"message": "Event published successfully", "event_id": new_event.id}

@app.post("/api/events/{event_id}/cancel", tags=["Events"])
async def cancel_event(
    event_id: int,
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db)
):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    event.status = "cancelled"
    db.commit()

    # Inform BookingAgent to execute 100% full refunds
    msg = AgentMessage(
        sender="AdminController",
        receiver="BookingAgent",
        performative="INFORM",
        content={"action": "event_cancelled", "event_id": event_id}
    )
    await message_bus.send(msg)

    return {"message": f"Event '{event.title}' cancelled. BookingAgent refunded all ticket holders in full."}


# ==========================================
# REST API: Ticket Bookings (Multi-Agent Delegated)
# ==========================================
@app.post("/api/bookings", tags=["Bookings"])
async def book_tickets(
    req: BookingRequestSchema,
    user: User = Depends(get_current_user)
):
    """Sends REQUEST to BookingAgent to atomically allocate seats and issue tickets."""
    msg = AgentMessage(
        sender="BookingController",
        receiver="BookingAgent",
        performative="REQUEST",
        content={
            "action": "book_tickets",
            "user_id": user.id,
            "event_id": req.event_id,
            "ticket_category_id": req.ticket_category_id,
            "quantity": req.quantity
        }
    )
    res = await message_bus.send(msg)
    if not res or res.performative != "AGREE":
        reason = res.content.get("reason", "Booking rejected") if res else "No response"
        raise HTTPException(status_code=400, detail=reason)

    return res.content

@app.post("/api/bookings/{booking_id}/cancel", tags=["Bookings"])
async def cancel_booking(
    booking_id: int,
    user: User = Depends(get_current_user)
):
    """Sends REQUEST to BookingAgent to cancel booking, calculate refund policy, and notify WaitlistAgent."""
    msg = AgentMessage(
        sender="BookingController",
        receiver="BookingAgent",
        performative="REQUEST",
        content={
            "action": "cancel_booking",
            "booking_id": booking_id,
            "user_id": user.id
        }
    )
    res = await message_bus.send(msg)
    if not res or res.performative != "AGREE":
        reason = res.content.get("reason", "Cancellation rejected") if res else "No response"
        raise HTTPException(status_code=400, detail=reason)

    return res.content


# ==========================================
# REST API: Digital Ticket Gate Scanner (TicketAgent)
# ==========================================
@app.post("/api/scanner/checkin", tags=["Gate Scanner"])
async def scanner_checkin(
    req: ScannerCheckinSchema,
    user: User = Depends(require_role("admin", "staff"))
):
    """Staff scanner sends REQUEST to TicketAgent to admit ticket exactly once."""
    msg = AgentMessage(
        sender="GateScannerDevice",
        receiver="TicketAgent",
        performative="REQUEST",
        content={
            "action": "verify_and_checkin",
            "ticket_code": req.ticket_code.strip(),
            "event_id": req.event_id
        }
    )
    res = await message_bus.send(msg)
    if not res or res.performative != "AGREE":
        reason = res.content.get("reason", "Ticket rejected") if res else "No response"
        raise HTTPException(status_code=400, detail=reason)

    return res.content


# ==========================================
# REST API: Private Venue Hire (VenueAgent)
# ==========================================
@app.post("/api/private-hire", tags=["Private Venue Hire"])
async def request_private_hire(
    req: PrivateHireRequestSchema,
    user: User = Depends(get_current_user)
):
    """Sends REQUEST to VenueAgent to evaluate private hire: auto-approval, proposal or escalation."""
    msg = AgentMessage(
        sender="HireController",
        receiver="VenueAgent",
        performative="REQUEST",
        content={
            "action": "evaluate_private_hire",
            "user_id": user.id,
            "venue_id": req.venue_id,
            "start_time": req.start_time,
            "end_time": req.end_time,
            "guest_count": req.guest_count,
            "purpose": req.purpose
        }
    )
    res = await message_bus.send(msg)
    if not res or res.performative not in ["AGREE", "PROPOSE"]:
        reason = res.content.get("reason", "Hire request refused") if res else "No response"
        raise HTTPException(status_code=400, detail=reason)

    return res.content

@app.post("/api/admin/hires/{hire_id}/decision", tags=["Private Venue Hire"])
async def admin_hire_decision(
    hire_id: int,
    decision_in: HireDecisionSchema,
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db)
):
    hire = db.query(PrivateHire).filter(PrivateHire.id == hire_id).first()
    if not hire:
        raise HTTPException(status_code=404, detail="Hire request not found")

    hire.status = decision_in.decision
    db.commit()

    # Inform user via NotificationAgent
    await message_bus.broadcast(AgentMessage(
        sender="AdminController",
        receiver="NotificationAgent",
        performative="INFORM",
        content={
            "event_type": "hire_decision",
            "user_id": hire.user_id,
            "message": f"Your escalated venue hire request for '{hire.venue.name}' has been {decision_in.decision.upper()} by the organiser."
        }
    ))

    return {"message": f"Hire request {decision_in.decision}."}


# ==========================================
# REST API: Waitlist (WaitlistAgent)
# ==========================================
@app.post("/api/waitlist/join", tags=["Waitlist"])
async def join_waitlist(
    req: WaitlistJoinSchema,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    cat_id = req.ticket_category_id
    if not cat_id:
        first_cat = db.query(TicketCategory).filter(TicketCategory.event_id == req.event_id).first()
        cat_id = first_cat.id if first_cat else None

    msg = AgentMessage(
        sender="WaitlistController",
        receiver="WaitlistAgent",
        performative="REQUEST",
        content={
            "action": "join_waitlist",
            "user_id": user.id,
            "event_id": req.event_id,
            "ticket_category_id": cat_id
        }
    )
    res = await message_bus.send(msg)
    if not res or res.performative != "AGREE":
        reason = res.content.get("reason", "Waitlist join refused") if res else "No response"
        raise HTTPException(status_code=400, detail=reason)

    return res.content


# ==========================================
# REST API: Agent HTTP Inboxes (Part C Advanced Feature)
# ==========================================
@app.post("/api/agents/{agent_name}/inbox", tags=["Agent Communication Bus"])
async def agent_http_inbox(
    agent_name: str,
    msg_in: AgentHttpInboxSchema
):
    """Direct HTTP inbox endpoint per agent accepting JSON messages (Part C Advanced Feature)."""
    msg = AgentMessage(
        sender=msg_in.sender,
        receiver=agent_name,
        performative=msg_in.performative,
        content=msg_in.content,
        conversation_id=msg_in.conversation_id
    )
    res = await message_bus.send(msg)
    if not res:
        return {"status": "Message delivered, no synchronous reply required."}
    return res.to_dict()


# ==========================================
# Legacy compatibility endpoints
# ==========================================
class AgentBookingRequestLegacy(BaseModel):
    username: str
    event_name: str
    capacity: int

@app.post("/agent-booking", tags=["Legacy Compatibility"])
def legacy_agent_booking(
    req: AgentBookingRequestLegacy,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.username == req.username).first()
    if not user:
        user = User(
            username=req.username,
            password_hash=hash_password("password123"),
            role="attendee"
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    event = db.query(Event).filter(Event.title.ilike(f"%{req.event_name}%")).first()
    if not event:
        raise HTTPException(status_code=404, detail=f"Event '{req.event_name}' not found")

    cat = event.ticket_categories[0] if event.ticket_categories else None
    if not cat:
        raise HTTPException(status_code=400, detail="No ticket categories available")

    # Delegate to BookingAgent
    import secrets
    t_code = f"TICK-{secrets.token_hex(4).upper()}"
    b = Booking(
        booking_reference=f"BK-{secrets.token_hex(4).upper()}",
        user_id=user.id,
        event_id=event.id,
        ticket_category_id=cat.id,
        quantity=1,
        unit_price=cat.price,
        total_price=cat.price,
        payment_reference="SIM-PAY-LEGACY",
        status="confirmed"
    )
    db.add(b)
    db.commit()
    t = Ticket(
        ticket_code=t_code,
        booking_id=b.id,
        user_id=user.id,
        event_id=event.id,
        status="valid"
    )
    db.add(t)
    db.commit()
    return {
        "message": "Booking and ticket saved successfully",
        "booking_id": b.id,
        "ticket_code": t.ticket_code,
        "venue": event.venue.name
    }

@app.get("/tickets/{ticket_code}/validate", tags=["Legacy Compatibility"])
def legacy_validate_ticket(ticket_code: str, db: Session = Depends(get_db)):
    t = db.query(Ticket).filter(Ticket.ticket_code == ticket_code).first()
    if not t:
        raise HTTPException(status_code=404, detail="Invalid ticket")
    if t.status != "valid":
        raise HTTPException(status_code=400, detail="Ticket has already been used")
    return {"message": "Ticket is valid", "ticket_code": t.ticket_code, "event_name": t.event.title}

@app.post("/tickets/{ticket_code}/check-in", tags=["Legacy Compatibility"])
def legacy_check_in(ticket_code: str, db: Session = Depends(get_db)):
    t = db.query(Ticket).filter(Ticket.ticket_code == ticket_code).first()
    if not t:
        raise HTTPException(status_code=404, detail="Invalid ticket")
    if t.status != "valid":
        raise HTTPException(status_code=400, detail="Ticket has already been used")
    t.status = "admitted"
    t.admitted_at = datetime.utcnow()
    db.commit()
    return {"message": "Ticket checked in successfully", "ticket_code": t.ticket_code, "status": t.status}
