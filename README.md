# EventSphere - Autonomous Multi-Agent Venue Booking & Digital Ticketing Platform

> **IT4123 Agent Based Computing Practical — ICA 02**  
> A mobile-friendly venue booking and digital ticketing platform where every booking decision and calendar reservation is made by cooperating software agents.

---

## 🌟 Key Features

1. **Autonomous Multi-Agent System (Part B)**:
   - **Messages, not method calls**: Agents communicate exclusively through structured JSON messages carrying performatives (`REQUEST`, `AGREE`, `REFUSE`, `PROPOSE`, `INFORM`, `FAILURE`) and conversation IDs.
   - **5 Cooperating Agents**:
     - **`VenueAgent`**: Keeps calendar clash-free with a 60-minute turnaround buffer, manages routine private hire auto-confirmations, escalations, counter-proposals with alternative slots/venues, and weekend peak pricing (+25%).
     - **`BookingAgent`**: Prevents overselling with database concurrency protection, enforces quantity limit ($\le 6$ tickets), calculates stated cancellation refunds (>24h = 100%, 2-24h = 50%, <2h = 0%), and triggers full refunds on event cancellation.
     - **`TicketAgent`**: Issues unguessable cryptographically secure codes (`secrets.token_urlsafe`), validates single-admission check-in at the gate, rejects duplicates, and triggers security alerts upon suspicious repeated scans (>2).
     - **`WaitlistAgent`**: Reactive allocation of released seats upon cancellation, holds seats with a 10-minute countdown, and performs **proactive background sweeps** on a periodic timer to expire unclaimed holds and re-offer them.
     - **`NotificationAgent`**: Broadcast listener that turns events from other agents into user and admin notifications.
2. **Mobile-First Experience (Part A)**:
   - Responsive Bootstrap 5 interface designed for phones (tested down to **375px** width).
   - Dynamic SVG / PNG digital QR codes on attendee phones.
   - Live HTML5 camera QR code scanner with manual code fallback for gate staff.
3. **Advanced Features (Part C)**:
   - Waitlist with timed seat holds and visible countdown.
   - Weekend peak rate (+25%) evaluated by VenueAgent.
   - Detection of suspicious duplicate scans (shared screenshot alerts).
   - Agent HTTP inbox endpoints (`/api/agents/{agent_name}/inbox`) documented in OpenAPI docs.
   - Organiser dashboard with interactive Chart.js visualizations (sales, check-ins).
   - Complete multi-agent message trace log viewer on the Admin dashboard.

---

## 👥 Demo Accounts (Pre-Seeded)

The database automatically initializes with three role-specific demo accounts:

| Role | Username / Email | Password | Access Rights |
| :--- | :--- | :--- | :--- |
| **Admin** | `admin@eventsphere.com` | `admin123` | Full dashboard, venue/event CRUD, cancel/refund, hire review, agent trace log |
| **Gate Staff** | `staff@eventsphere.com` | `staff123` | Camera QR scanner, manual ticket check-in |
| **Attendee** | `student1@student.edu` | `Password123!` | Browse events, simulated booking, digital tickets, private hire request, waitlists |

---

## 🚀 Quick Start (Run Locally)

### 1. Prerequisites
- Python 3.11+
- Virtual environment (`venv`)

### 2. Setup & Installation
```bash
# Clone the repository
git clone <your-repo-url>
cd EventSphere

# Create and activate virtual environment
python -m venv .venv

# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Run the Application
```bash
python run.py
# Or using uvicorn directly:
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open your browser and visit:
- **Web Application**: [http://127.0.0.1:8000](http://127.0.0.1:8000)
- **Interactive API Documentation (Swagger UI)**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

---

## 🧪 Running Automated Tests

Run the complete test suite verifying all 5 agents, concurrency overbooking protection, double check-in prevention, and RBAC:

```bash
pytest tests/ -v
# Or:
python -m pytest tests/test_eventsphere.py -v
```

The suite contains 14 tests:
- `test_venue_agent_turnaround_buffer_clash` (Buffer clash & PROPOSE alternatives)
- `test_venue_agent_weekend_pricing` (Weekend 1.25x peak pricing)
- `test_booking_agent_refuses_over_six_tickets` (Max 6 tickets limit rule)
- `test_booking_agent_anti_overselling` (Concurrency & capacity protection)
- `test_booking_agent_refund_policy` (Stated refund policy calculation)
- `test_ticket_agent_single_admit_and_double_checkin` (Strict single-admission check-in)
- `test_ticket_agent_suspicious_duplicate_scans` (Suspicious scan screenshot alert)
- `test_waitlist_agent_reactive_and_proactive` (Seat hold allocation & background sweep)
- `test_notification_agent_listener` (Reactive broadcast event listening)
- `test_role_based_access_control` (403/302 protection on staff & admin routes)
- `test_agent_http_inbox` (Agent HTTP JSON messaging endpoint)
- `test_venue_edit_and_draft_publishing` (Venue editing & draft-to-publish workflow)
- `test_waitlist_claim_and_csrf_validation` (Waitlist seat claim & CSRF token protection)
- `test_role_registration_and_login_redirection` (Role registration and role-based redirect after login)

---

## ☁️ Deployment (Railway + PostgreSQL)

The repository is fully configured for zero-downtime deployment on [Railway](https://railway.com) using `Dockerfile`, `railway.toml`, `railway.json`, and `Procfile`.

1. Push the repository changes to **GitHub**:
   ```bash
   git add .
   git commit -m "Fix Railway deployment configuration, add healthcheck and DB retry"
   git push origin main
   ```
2. In Railway, click **New Project → Deploy from GitHub repo** and pick the repository.
3. In the same project, click **+ New → Database → PostgreSQL**.
4. Open your web service → **Variables** tab and add:
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (Use "Add Reference" and select Postgres's `DATABASE_URL`)
   - `SECRET_KEY` = a long random string (e.g. generated via `python -c "import secrets; print(secrets.token_hex(32))"`)
5. Open **Settings → Networking → Generate Domain**. Railway assigns a public HTTPS domain (e.g., `https://eventsphere-production.up.railway.app`).
6. The app automatically verifies connection via `/health`, boots with automatic database connection retries, creates all tables in PostgreSQL, and seeds demo accounts and events.

Start command used:
```bash
python run.py
```

### Environment variables

| Variable | Purpose | Default |
| :--- | :--- | :--- |
| `DATABASE_URL` | PostgreSQL or SQLite connection string | `sqlite:///./eventsphere.db` |
| `SECRET_KEY` | Signs JWT session tokens | Auto-generated development fallback |
| `PORT` | Dynamic port injected by Railway | `8000` |
| `HOST` | Bind host address | `0.0.0.0` |

---

## 📁 Project Architecture

```
EventSphere/
│
├── main.py                     # FastAPI server, web UI routes, and REST endpoints
├── agents.py                   # Multi-agent architecture (MessageBus, 5 Agents, Rules)
├── models.py                   # SQLAlchemy ORM models (Users, Venues, Bookings, Tickets, Logs)
├── database.py                 # SQLite / PostgreSQL database connection & session
├── security.py                 # Password hashing (argon2), JWT tokens, and RBAC guards
├── seed_data.py                # Automated database seeding (demo accounts, sample venues/events)
├── run.py                      # Application launcher script
├── requirements.txt            # Python dependencies
├── railway.json                # Railway build/deploy configuration
├── Procfile                    # Fallback start command
│
├── templates/                  # Mobile-responsive HTML templates (Bootstrap 5, 375px+)
│   ├── base.html               # Shared layout, navbar, mobile viewport
│   ├── index.html              # Event browsing, keyword search & category filter
│   ├── event_detail.html       # Ticket selection & simulated payment checkout
│   ├── my_tickets.html         # Digital boarding passes with live QR codes & refund cancellation
│   ├── private_hire.html       # Venue hire form with real-time VenueAgent decisions
│   ├── scanner.html            # Staff camera scanner & manual code fallback
│   ├── admin_dashboard.html    # Organiser KPI dashboard, charts & agent message trace log
│   ├── notifications.html      # Attendee notifications feed
│   ├── login.html              # Authentication with 1-click viva demo logins
│   └── register.html           # Attendee self-registration
│
└── tests/                      # Automated Pytest suite
    └── test_eventsphere.py     # 14 tests covering all agents, overbooking, and check-in
```
