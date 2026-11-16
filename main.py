import io
import asyncio
import traceback
from datetime import datetime, timedelta
from typing import List, Optional
from contextlib import asynccontextmanager

from fastapi import (
    FastAPI, Depends, HTTPException, Request, Response, status, Form
)
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
import qrcode

from database import engine, Base, SessionLocal, get_db
from models import (
    utcnow,
    User, Venue, Event, TicketCategory, Booking, Ticket,
    PrivateHire, WaitlistEntry, AgentMessage as AgentMessageModel,
    Notification
)
from security import (
    hash_password, verify_password, create_access_token,
    get_current_user_optional, get_current_user, require_role,
    get_or_create_csrf_token, validate_csrf_token
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
    # Stagger initial sweep to give database time to initialize
    await asyncio.sleep(5)
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
# Health Check Endpoint (Railway / Monitoring)
# ==========================================
@app.get("/health", tags=["System"])
def health_check():
    """Lightweight zero-dependency health check endpoint for Railway container verification."""
    return {"status": "ok", "service": "eventsphere", "agents": "active"}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Logs unhandled 500 errors to container console so Railway logs reveal exact tracebacks."""
    print(f"[Unhandled Error] {request.method} {request.url.path}: {exc}")
    traceback.print_exc()
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "error": str(exc)}
        )
    return HTMLResponse(
        status_code=500,
        content="<div style='font-family:sans-serif;padding:40px;text-align:center;'><h2>500 Internal Server Error</h2><p>An unexpected server error occurred. Check Railway deployment logs for full traceback.</p><a href='/'>Back to Home</a></div>"
    )


# ==========================================
# Pydantic Request / Response Models
# ==========================================
class UserRegisterSchema(BaseModel):
    username: str
    password: str
    role: Optional[str] = "attendee"

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

class VenueUpdateSchema(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None
    capacity: Optional[int] = None
    hourly_rate: Optional[float] = None
    description: Optional[str] = None
    open_for_private_hire: Optional[bool] = None
    image_url: Optional[str] = None

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
    status: Optional[str] = "published"
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

class WaitlistClaimSchema(BaseModel):
    waitlist_id: int

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
# Web UI Helpers & Routes (HTML Responsive at 375px+)
# ==========================================
def get_user_active_holds(user: Optional[User], db: Session) -> List[dict]:
    """Retrieves active waitlist holds currently offered to the attendee with remaining seconds."""
    if not user:
        return []
    now = utcnow()
    holds = db.query(WaitlistEntry).filter(
        WaitlistEntry.user_id == user.id,
        WaitlistEntry.status == "offered",
        WaitlistEntry.expires_at > now
    ).all()
    results = []
    for h in holds:
        rem_sec = max(0, int((h.expires_at - now).total_seconds()))
        results.append({
            "id": h.id,
            "event_id": h.event_id,
            "event_title": h.event.title,
            "category_name": h.ticket_category.name if h.ticket_category else "Standard",
            "price": h.ticket_category.price if h.ticket_category else 0.0,
            "expires_at": h.expires_at.isoformat(),
            "seconds_remaining": rem_sec,
            "minutes_remaining": max(1, rem_sec // 60)
        })
    return results

def render_template(template_name: str, request: Request, context: dict, status_code: int = 200) -> Response:
    """Helper ensuring CSRF token is provided in context and stored in a secure cookie."""
    csrf_token = get_or_create_csrf_token(request)
    context["csrf_token"] = csrf_token
    context["request"] = request
    resp = templates.TemplateResponse(request=request, name=template_name, context=context, status_code=status_code)
    if not request.cookies.get("csrf_token"):
        resp.set_cookie(key="csrf_token", value=csrf_token, httponly=False, samesite="lax")
    return resp


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
    active_holds = get_user_active_holds(user, db)

    return render_template("index.html", request, {
        "user": user,
        "events": events,
        "search_query": search or "",
        "selected_category": category or "",
        "active_holds": active_holds,
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

    active_holds = get_user_active_holds(user, db)
    total_cap = sum(c.total_quantity for c in event.ticket_categories)
    total_sold = sum(c.sold_quantity for c in event.ticket_categories)
    total_available = max(0, total_cap - total_sold)

    is_on_waitlist = False
    if user:
        is_on_waitlist = db.query(WaitlistEntry).filter(
            WaitlistEntry.user_id == user.id,
            WaitlistEntry.event_id == event_id,
            WaitlistEntry.status == "waiting"
        ).first() is not None

    return render_template("event_detail.html", request, {
        "user": user,
        "event": event,
        "active_holds": active_holds,
        "total_cap": total_cap,
        "total_sold": total_sold,
        "total_available": total_available,
        "is_on_waitlist": is_on_waitlist,
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
    active_holds = get_user_active_holds(user, db)

    return render_template("my_tickets.html", request, {
        "user": user,
        "bookings": bookings,
        "active_holds": active_holds,
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

    return render_template("private_hire.html", request, {
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

    return render_template("scanner.html", request, {
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
    all_hires = db.query(PrivateHire).order_by(PrivateHire.created_at.desc()).all()

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

    venue_hire_revenue = sum(h.total_price for h in all_hires if h.status in ["confirmed", "approved"])
    total_combined_revenue = total_revenue + venue_hire_revenue

    return render_template("admin_dashboard.html", request, {
        "user": user,
        "stats": {
            "total_events": len(events),
            "total_tickets_sold": total_tickets_sold,
            "ticket_revenue": total_revenue,
            "venue_hire_revenue": venue_hire_revenue,
            "total_revenue": total_combined_revenue,
            "total_checkins": total_checkins
        },
        "event_metrics": event_metrics,
        "venues": venues,
        "agent_messages": agent_messages,
        "escalated_hires": escalated_hires,
        "all_hires": all_hires,
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

    return render_template("notifications.html", request, {
        "user": user,
        "notifications": notifs,
        "active_page": "notifications"
    })


# ==========================================
# Authentication Routes (HTML Form & API)
# ==========================================
@app.get("/login", response_class=HTMLResponse, tags=["Authentication"])
def page_login(request: Request, next: Optional[str] = None):
    return render_template("login.html", request, {"error": None, "next_url": next or ""})

@app.post("/login", tags=["Authentication"])
async def handle_login(
    request: Request,
    db: Session = Depends(get_db)
):
    content_type = request.headers.get("content-type", "")
    is_html = request.headers.get("accept", "").find("text/html") != -1
    uname = None
    pwd = None
    next_param = request.query_params.get("next", "")

    if "application/json" in content_type:
        try:
            body = await request.json()
            uname = body.get("username")
            pwd = body.get("password")
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")
    else:
        form = await request.form()
        uname = form.get("username")
        pwd = form.get("password")
        next_param = form.get("next") or next_param
        csrf_token = form.get("csrf_token")
        if is_html and not validate_csrf_token(request, csrf_token):
            return render_template("login.html", request, {
                "error": "Security validation failed: Invalid or missing CSRF token. Please refresh the page.",
                "next_url": next_param
            }, status_code=403)

    if not uname or not pwd:
        if is_html:
            return render_template("login.html", request, {
                "error": "Username and password are required",
                "next_url": next_param
            }, status_code=400)
        raise HTTPException(status_code=400, detail="Username and password are required")

    user = db.query(User).filter(User.username == uname).first()
    if not user or not verify_password(pwd, user.password_hash):
        if is_html:
            return render_template("login.html", request, {
                "error": "Invalid username or password",
                "next_url": next_param
            }, status_code=401)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = create_access_token(user.username, user.role)

    # If browser form submission, set HTTP-only cookie and redirect to designated role workspace
    if is_html:
        if next_param and next_param.startswith("/") and not next_param.startswith("//"):
            redirect_url = next_param
        else:
            redirect_url = "/admin" if user.role == "admin" else ("/scanner" if user.role == "staff" else "/")
        resp = RedirectResponse(url=redirect_url, status_code=302)
        resp.set_cookie(key="access_token", value=f"Bearer {token}", httponly=True, path="/", samesite="lax")
        return resp

    resp = JSONResponse(content={
        "access_token": token,
        "token_type": "bearer",
        "username": user.username,
        "role": user.role
    })
    resp.set_cookie(key="access_token", value=f"Bearer {token}", httponly=True, path="/", samesite="lax")
    return resp


@app.get("/register", response_class=HTMLResponse, tags=["Authentication"])
def page_register(request: Request):
    return render_template("register.html", request, {"error": None})

@app.post("/register", tags=["Authentication"])
async def handle_register(
    request: Request,
    db: Session = Depends(get_db)
):
    content_type = request.headers.get("content-type", "")
    is_html = request.headers.get("accept", "").find("text/html") != -1
    uname = None
    pwd = None
    role_val = "attendee"

    if "application/json" in content_type:
        try:
            body = await request.json()
            uname = body.get("username")
            pwd = body.get("password")
            role_val = body.get("role", "attendee")
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")
    else:
        form = await request.form()
        uname = form.get("username")
        pwd = form.get("password")
        role_val = form.get("role", "attendee")
        csrf_token = form.get("csrf_token")
        if is_html and not validate_csrf_token(request, csrf_token):
            return render_template("register.html", request, {
                "error": "Security validation failed: Invalid or missing CSRF token. Please refresh the page."
            }, status_code=403)

    role_val = (role_val or "attendee").lower().strip()
    if role_val not in ["attendee", "staff", "admin"]:
        role_val = "attendee"

    if not uname or not pwd:
        if is_html:
            return render_template("register.html", request, {
                "error": "Username and password required"
            }, status_code=400)
        raise HTTPException(status_code=400, detail="Username and password required")

    existing = db.query(User).filter(User.username == uname).first()
    if existing:
        if is_html:
            return render_template("register.html", request, {
                "error": "Username already registered"
            }, status_code=400)
        raise HTTPException(status_code=400, detail="Username already exists")

    new_user = User(
        username=uname,
        email=uname if "@" in uname else f"{uname}@eventsphere.com",
        password_hash=hash_password(pwd),
        role=role_val
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    token = create_access_token(new_user.username, new_user.role)

    if is_html:
        redirect_url = "/admin" if new_user.role == "admin" else ("/scanner" if new_user.role == "staff" else "/")
        resp = RedirectResponse(url=redirect_url, status_code=302)
        resp.set_cookie(key="access_token", value=f"Bearer {token}", httponly=True, path="/", samesite="lax")
        return resp

    resp = JSONResponse(content={
        "message": "Registration successful",
        "access_token": token,
        "token_type": "bearer",
        "username": new_user.username,
        "role": new_user.role
    })
    resp.set_cookie(key="access_token", value=f"Bearer {token}", httponly=True, path="/", samesite="lax")
    return resp

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

@app.put("/api/venues/{venue_id}", tags=["Venues"])
def update_venue(
    venue_id: int,
    venue_in: VenueUpdateSchema,
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db)
):
    """Admin route to edit venue name, location, capacity, description, image, hourly rate, and private hire status."""
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if not venue:
        raise HTTPException(status_code=404, detail="Venue not found")
    if venue_in.name is not None:
        venue.name = venue_in.name
    if venue_in.location is not None:
        venue.location = venue_in.location
    if venue_in.capacity is not None:
        venue.capacity = venue_in.capacity
    if venue_in.hourly_rate is not None:
        venue.hourly_rate = venue_in.hourly_rate
    if venue_in.description is not None:
        venue.description = venue_in.description
    if venue_in.open_for_private_hire is not None:
        venue.open_for_private_hire = venue_in.open_for_private_hire
    if venue_in.image_url is not None:
        venue.image_url = venue_in.image_url
    db.commit()
    db.refresh(venue)
    return venue


# ==========================================
# REST API: Events (Admin & Attendee)
# ==========================================
@app.get("/events", tags=["Events"])
@app.get("/api/events", tags=["Events"])
def list_events(db: Session = Depends(get_db)):
    events = db.query(Event).filter(Event.status == "published").order_by(Event.start_time.asc()).all()
    results = []
    for ev in events:
        results.append({
            "id": ev.id,
            "title": ev.title,
            "category": ev.category,
            "venue_id": ev.venue_id,
            "venue_name": ev.venue.name if ev.venue else "Campus Venue",
            "venue_location": ev.venue.location if ev.venue else "",
            "description": ev.description,
            "image_url": ev.image_url,
            "start_time": ev.start_time.isoformat() if ev.start_time else "",
            "end_time": ev.end_time.isoformat() if ev.end_time else "",
            "status": ev.status,
            "ticket_categories": [
                {
                    "id": tc.id,
                    "name": tc.name,
                    "price": tc.price,
                    "total_quantity": tc.total_quantity,
                    "sold_quantity": tc.sold_quantity,
                    "available_quantity": tc.total_quantity - tc.sold_quantity
                } for tc in ev.ticket_categories
            ]
        })
    return results

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
    status_choice = event_in.status if event_in.status in ["published", "draft"] else "published"

    new_event = Event(
        title=event_in.title,
        category=event_in.category,
        venue_id=event_in.venue_id,
        description=event_in.description or "",
        image_url=event_in.image_url or "https://images.unsplash.com/photo-1540575467063-178a50c2df87?w=800",
        start_time=start_dt,
        end_time=end_dt,
        status=status_choice
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

    return {"message": f"Event {status_choice} successfully", "event_id": new_event.id, "status": status_choice}

@app.post("/api/events/{event_id}/publish", tags=["Events"])
async def publish_event(
    event_id: int,
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db)
):
    """Publishes an event previously saved as a draft after re-validating the slot with VenueAgent."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.status == "published":
        return {"message": "Event is already published", "status": "published"}

    msg = AgentMessage(
        sender="AdminController",
        receiver="VenueAgent",
        performative="REQUEST",
        content={
            "action": "check_slot",
            "venue_id": event.venue_id,
            "start_time": event.start_time.isoformat(),
            "end_time": event.end_time.isoformat(),
            "exclude_event_id": event.id
        }
    )
    res = await message_bus.send(msg)
    if not res or res.performative != "AGREE":
        reason = res.content.get("reason", "Slot rejected by VenueAgent") if res else "No response"
        raise HTTPException(status_code=400, detail=f"VenueAgent refused slot: {reason}")

    event.status = "published"
    db.commit()
    return {"message": f"Event '{event.title}' published successfully", "status": "published"}

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

@app.post("/api/waitlist/claim", tags=["Waitlist"])
async def claim_waitlist_seat(
    claim_in: WaitlistClaimSchema,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Allows an attendee to claim and instantly book their held waitlist seat during the countdown window."""
    entry = db.query(WaitlistEntry).filter(
        WaitlistEntry.id == claim_in.waitlist_id,
        WaitlistEntry.user_id == user.id
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Waitlist record not found")
    if entry.status != "offered":
        raise HTTPException(status_code=400, detail=f"Waitlist seat is not currently held for you (status: {entry.status})")

    now = utcnow()
    if entry.expires_at and entry.expires_at < now:
        entry.status = "expired"
        db.commit()
        raise HTTPException(status_code=400, detail="The 10-minute hold window for this seat has expired")

    # Book the held seat via BookingAgent
    msg = AgentMessage(
        sender="WaitlistClaimController",
        receiver="BookingAgent",
        performative="REQUEST",
        content={
            "action": "book_tickets",
            "user_id": user.id,
            "event_id": entry.event_id,
            "ticket_category_id": entry.ticket_category_id,
            "quantity": 1
        }
    )
    res = await message_bus.send(msg)
    if not res or res.performative != "AGREE":
        reason = res.content.get("reason", "Seat reservation failed") if res else "No response"
        raise HTTPException(status_code=400, detail=f"BookingAgent refused seat claim: {reason}")

    entry.status = "claimed"
    db.commit()

    # Inform attendee
    await message_bus.broadcast(AgentMessage(
        sender="WaitlistClaimController",
        receiver="NotificationAgent",
        performative="INFORM",
        content={
            "event_type": "waitlist_seat_claimed",
            "user_id": user.id,
            "message": f"Congratulations! You successfully claimed your held seat for '{entry.event.title}'."
        }
    ))

    return {
        "message": "Waitlist seat successfully claimed and booked!",
        "booking": res.content
    }

@app.get("/api/waitlist/my-holds", tags=["Waitlist"])
def get_my_waitlist_holds(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Returns active seat holds currently offered to the attendee with remaining countdown seconds."""
    now = utcnow()
    holds = db.query(WaitlistEntry).filter(
        WaitlistEntry.user_id == user.id,
        WaitlistEntry.status == "offered",
        WaitlistEntry.expires_at > now
    ).all()

    result = []
    for h in holds:
        rem_sec = max(0, int((h.expires_at - now).total_seconds()))
        result.append({
            "waitlist_id": h.id,
            "event_id": h.event_id,
            "event_title": h.event.title,
            "category_name": h.ticket_category.name if h.ticket_category else "Standard",
            "price": h.ticket_category.price if h.ticket_category else 0.0,
            "expires_at": h.expires_at.isoformat(),
            "seconds_remaining": rem_sec,
            "minutes_remaining": max(1, rem_sec // 60)
        })
    return result


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
