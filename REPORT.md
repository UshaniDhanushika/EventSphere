# IT4123 Agent Based Computing Practical
## ICA 02 - Project Report: EventSphere
**Submission Date**: 15 November 2026  
**System Name**: EventSphere  
**Technology Stack**: Python 3.11+, FastAPI, SQLAlchemy, SQLite/PostgreSQL, Jinja2, Bootstrap 5, Pytest  

---

### Executive Summary
EventSphere is a mobile-friendly university venue booking and digital ticketing platform designed to manage events, room allocations, ticket sales, gate admissions, and private venue hires. Unlike conventional database applications where controllers execute procedural CRUD queries, EventSphere is engineered as an **Autonomous Multi-Agent System**. Every scheduling decision, capacity allocation, check-in validation, and waitlist hold is governed by five cooperating software agents communicating asynchronously via structured messages with formal speech-act performatives (`REQUEST`, `AGREE`, `REFUSE`, `PROPOSE`, `INFORM`, `FAILURE`).

---

### 1. Multi-Agent System Architecture

```
                          +-------------------------+
                          |   AgentMessageBus       |
                          | (Logs & Conversation ID)|
                          +------------+------------+
                                       |
       +---------------+---------------+---------------+---------------+
       |               |               |               |               |
+------v-------+ +-----v-------+ +-----v-------+ +-----v-------+ +-----v-------+
|  VenueAgent  | | BookingAgent| | TicketAgent | |WaitlistAgent| |Notification |
|              | |             | |             | |             | |    Agent    |
| - Buffer     | | - Anti-     | | - QR codes  | | - Reactive  | | - Broadcast |
| - Clashes    | |   Oversell  | | - Single    | |   offer     | |   listener  |
| - Pricing    | | - Policy    | |   admit     | | - Proactive | | - In-app    |
| - Escalate   | |   refunds   | | - Suspicious| |   sweep     | |   alerts    |
+--------------+ +-------------+ +-------------+ +-------------+ +-------------+
```

#### The Five Cooperating Agents:

| Agent Name | Primary Goal | Autonomous Decisions Made | Communication Partners |
| :--- | :--- | :--- | :--- |
| **`VenueAgent`** | Keep every venue's calendar free of clashes | Accepts/refuses event time slots with a 60-min turnaround buffer; evaluates private hires (auto-approve routine, escalate large hires); counter-proposes alternative slots/venues; calculates duration & weekend peak pricing (+25%). | `NotificationAgent`, `BookingAgent` |
| **`BookingAgent`** | Sell tickets fairly without overselling | Enforces max 6 tickets limit; verifies atomic seat capacity; calculates stated cancellation refunds (>24h = 100%, 2-24h = 50%, <2h = 0%); executes full refunds when events are cancelled; notifies WaitlistAgent when seats are freed. | `TicketAgent`, `WaitlistAgent`, `NotificationAgent` |
| **`TicketAgent`** | Admit each ticket exactly once | Generates unguessable cryptographically secure codes; validates gate check-in (admit, already used, wrong event, cancelled, outside check-in window); detects suspicious repeated scans of already admitted tickets (>2 scans). | `BookingAgent` |
| **`WaitlistAgent`** | Pass freed seats to waiting customers | Reactive: immediately assigns released seats to earliest waiting customer with a 10-minute hold countdown. Proactive: background timer sweep expires holds and automatically re-offers them to the next user in line. | `BookingAgent`, `NotificationAgent` |
| **`NotificationAgent`**| Tell the right person what affects them | Listens to all broadcast `INFORM` performatives on the message bus and inserts user-visible notifications into the persistent database. | All agents (listener) |

---

### 2. Documented Agent Decision Rules

1. **Rule V1 (Turnaround Buffer)**: Every event or hire requires a 60-minute turnaround buffer before and after. If another activity is within that window, the slot is refused.
2. **Rule V2 (Usable Alternatives / `PROPOSE`)**: When a requested slot or venue is unavailable, `VenueAgent` does not simply refuse; it calculates the next available free window (+2 hours) or finds alternate venues with matching capacity and sends performative `PROPOSE`.
3. **Rule V3 (Weekend Peak Pricing)**: Venue hire cost = $\text{duration (hours)} \times \text{hourly rate}$. If the start date falls on Saturday or Sunday, a 1.25x peak multiplier is applied.
4. **Rule V4 (Routine vs Escalation)**: Private hire requests $\le 100$ guests and $\le 8$ hours on open venues are confirmed automatically. Requests exceeding either threshold are marked `escalated` and routed to the Organiser dashboard for manual admin approval.
5. **Rule B1 (Quantity Limit)**: A single booking request cannot exceed 6 tickets. Larger requests are rejected with `REFUSE`.
6. **Rule B2 (Anti-Overselling & Concurrency)**: Evaluates remaining capacity atomically within database transaction boundary. Prevents race conditions so two simultaneous users cannot purchase the last seat.
7. **Rule B3 (Stated Refund Policy)**:
   - Cancelled $>24$ hours before event start: 100% refund.
   - Cancelled 2 to 24 hours before event start: 50% refund.
   - Cancelled $<2$ hours before event start: 0% refund.
8. **Rule B4 (Event Cancellation)**: Organiser event cancellation triggers 100% full refunds for all confirmed attendees.
9. **Rule T1 (Unguessable Code Generation)**: Digital ticket codes are issued using `secrets.token_urlsafe(12)`, ensuring codes cannot be guessed or brute-forced.
10. **Rule T2 (Gate Validation)**: Admits ticket once; marks status `admitted` with timestamp. Second scan is rejected as already used.
11. **Rule T3 (Suspicious Reuse Detection)**: Scans on already admitted tickets increment internal belief counter. If `scan_count > 2`, triggers `SECURITY ALERT: Suspected duplicate screenshot sharing`.
12. **Rule W1 (Reactive Waitlist Offer)**: When informed of cancellation, the agent puts first waiting user into `offered` status with a 10-minute hold window.
13. **Rule W2 (Proactive Background Expiry Sweep)**: Proactive async task running every 15 seconds expires holds exceeding `expires_at` and automatically re-offers the seat to the next person in line.

---

### 3. Agent Communication Protocol

Agent messages strictly follow structured JSON carrying:
- `conversation_id`: Correlates related messages across multi-step transactions.
- `sender`: The sending agent.
- `receiver`: The destination agent.
- `performative`: One of `REQUEST`, `AGREE`, `REFUSE`, `PROPOSE`, `INFORM`, `FAILURE`.
- `content`: Dictionary containing action parameters or reasons for refusal.
- `timestamp`: UTC timestamp.

All messages are logged to `agent_messages` and surfaced on the **Admin Organiser Dashboard Trace Log**.

---

### 4. Technical Implementation & Security

1. **Framework & Language**: Python 3.11+ with FastAPI, Starlette, and Pydantic.
2. **Database**: SQLAlchemy ORM with SQLite for local execution, automatically compatible with PostgreSQL in production via `DATABASE_URL`.
3. **Security Basics**:
   - Passwords hashed using Argon2 (`pwdlib`).
   - Role-Based Access Control (RBAC): Admin routes require role `admin`; Scanner routes require role `staff` or `admin`.
   - Unguessable QR codes and token authentication via JWT (`PyJWT`).
4. **Mobile Responsiveness**: Designed using Bootstrap 5, verified down to **375px phone screen width**.

---

### 5. Automated Testing Results

The test suite was executed using Pytest:

```
tests/test_eventsphere.py::test_venue_agent_turnaround_buffer_clash PASSED
tests/test_eventsphere.py::test_venue_agent_weekend_pricing PASSED
tests/test_eventsphere.py::test_booking_agent_refuses_over_six_tickets PASSED
tests/test_eventsphere.py::test_booking_agent_anti_overselling PASSED
tests/test_eventsphere.py::test_booking_agent_refund_policy PASSED
tests/test_eventsphere.py::test_ticket_agent_single_admit_and_double_checkin PASSED
tests/test_eventsphere.py::test_ticket_agent_suspicious_duplicate_scans PASSED
tests/test_eventsphere.py::test_waitlist_agent_reactive_and_proactive PASSED
tests/test_eventsphere.py::test_notification_agent_listener PASSED
tests/test_eventsphere.py::test_role_based_access_control PASSED
tests/test_eventsphere.py::test_agent_http_inbox PASSED

11 passed in 3.52s (100% Pass Rate)
```

---

### 6. Demo Accounts

| Role | Username / Email | Password |
| :--- | :--- | :--- |
| **Admin** | `admin@eventsphere.com` | `admin123` |
| **Gate Staff** | `staff@eventsphere.com` | `staff123` |
| **Attendee** | `student1@student.edu` | `Password123!` |
