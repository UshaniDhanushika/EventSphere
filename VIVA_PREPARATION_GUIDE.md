# EventSphere - Viva Preparation Guide & Defense Notes

> **Subject**: IT4123 Agent Based Computing Practical (ICA 02)  
> **Key Goal**: Equip you to confidently answer every examiner question with clear, simple, and technically rigorous explanations.

---

## 🎓 1. Core Concept: What makes this an "Agent-Based System" and not a regular CRUD app?

### Examiner Question:
> *"Why is this an agent-based system rather than an ordinary database web application? How are your agents different from plain controller functions?"*

### Your Answer (Memorize this key pitch):
1. **Autonomy & Goals**:
   - In an ordinary CRUD app, controller endpoints directly write and read from SQL tables without internal goals.
   - In EventSphere, each of our 5 agents (**`VenueAgent`**, **`BookingAgent`**, **`TicketAgent`**, **`WaitlistAgent`**, **`NotificationAgent`**) **owns a specific goal**, maintains its own **internal beliefs/state**, and makes decisions autonomously based on **documented decision rules**.
2. **Messages, Not Method Calls**:
   - An agent **never** calls another agent's functions directly.
   - All agent communication flows asynchronously through the **`AgentMessageBus`** using structured JSON messages that carry **FIPA performatives** (`REQUEST`, `AGREE`, `REFUSE`, `PROPOSE`, `INFORM`, `FAILURE`), content payloads, and unique **conversation IDs**.
3. **Traceability**:
   - Every single inter-agent exchange is logged into the `agent_messages` table and rendered live in the **Organiser Dashboard Message Trace Log**, allowing any user action to be tracked step-by-step across multiple agents.
4. **Proactive & Reactive Behaviour**:
   - Unlike passive CRUD apps that only do something when a user clicks a button, our agents demonstrate both **reactive behaviour** (listening to broadcast `INFORM` events) and **proactive behaviour** (`WaitlistAgent` background timer that sweeps expired holds with zero user interaction).

---

## 🤖 2. The 5 Agents: Roles, Goals & Documented Rules

### 1. `VenueAgent`
- **Goal**: Keep every venue's calendar free of scheduling clashes.
- **Rules applied**:
  - **Rule V1 (Turnaround Buffer)**: Enforces an automatic **60-minute buffer** before and after every event/hire. If another activity is within that window, it refuses or counter-proposes.
  - **Rule V2 (Counter-Proposal / `PROPOSE`)**: If an overlap occurs, it does not just say no; it computes the next available free time slot (+2 hours) or finds alternate venues with matching capacity and sends a `PROPOSE` performative.
  - **Rule V3 (Pricing & Weekend Peak)**: Calculates price based on duration $\times$ hourly rate. If the requested booking falls on a weekend (Saturday or Sunday), it dynamically applies a **1.25x peak multiplier**.
  - **Rule V4 (Routine vs Escalation)**: Routine private hire requests ($\le 100$ guests and $\le 8$ hours) are auto-confirmed immediately (`AGREE`). Larger or non-routine requests are escalated to the Admin with a `PROPOSE` message for manual human sign-off.

### 2. `BookingAgent`
- **Goal**: Sell tickets fairly without overselling, manage payments and cancellations.
- **Rules applied**:
  - **Rule B1 (Quantity Limit)**: Refuses any single booking request with more than **6 tickets** (`REFUSE: "Maximum 6 tickets allowed per booking"`).
  - **Rule B2 (Anti-Overselling & Concurrency)**: Performs atomic inventory verification (`sold_quantity + requested <= total_quantity`). If two users try to purchase the last ticket simultaneously, only one succeeds and the other receives a `REFUSE: "Sold out or insufficient tickets"`.
  - **Rule B3 (Stated Cancellation Refund Policy)**:
    - Cancelled $>24\text{ hours}$ before event start: **100% refund**.
    - Cancelled $2\text{ to }24\text{ hours}$ before event start: **50% refund**.
    - Cancelled $<2\text{ hours}$ before event start: **0% refund** (no refund).
  - **Rule B4 (Event Cancellation)**: When an organiser cancels an event, `BookingAgent` automatically issues **100% full refunds** to all attendees.
  - **Rule B5 (Waitlist Trigger)**: Whenever a ticket is cancelled, it broadcasts an `INFORM` message with `action: "seats_freed"` to `WaitlistAgent`.

### 3. `TicketAgent`
- **Goal**: Issue unguessable digital tickets and admit each ticket exactly once at the gate.
- **Rules applied**:
  - **Rule T1 (Unguessable Code Generation)**: Uses Python's cryptographically secure `secrets.token_urlsafe(12)` to prevent forgery.
  - **Rule T2 (Gate Validation)**: Checks:
    1. Code existence (if invalid $\rightarrow$ `REFUSE`).
    2. Event match (if ticket is for Event B scanned at Event A $\rightarrow$ `REFUSE`).
    3. Status (if already admitted $\rightarrow$ `REFUSE: "Ticket has already been used"`).
    4. Check-in time window (allowed from 3 hours before start time until event conclusion).
  - **Rule T3 (Suspicious Reuse Alert)**: Tracks scan count in persistent agent beliefs (`scan_count`). If a ticket that was already admitted is scanned **more than 2 times**, it triggers a `SECURITY ALERT: Suspected duplicate screenshot sharing`.

### 4. `WaitlistAgent`
- **Goal**: Pass freed seats to waiting customers and maintain timed seat holds.
- **Rules applied**:
  - **Reactive Behaviour**: When `BookingAgent` sends `INFORM: "seats_freed"`, `WaitlistAgent` finds the earliest applicant on the waitlist, puts them in `offered` status with a **10-minute hold countdown**, and notifies `NotificationAgent`.
  - **Proactive Behaviour**: Runs a background async scheduled task (`proactive_expiry_sweep()`) every 15 seconds. If a customer's hold expires before they claim it, the agent marks it `expired` and automatically re-offers the freed seat to the next person in line.

### 5. `NotificationAgent`
- **Goal**: Inform the right user or admin whenever an event affects them.
- **Rules applied**:
  - Subscribed to all broadcast `INFORM` performatives on the `AgentMessageBus`.
  - Generates in-app notifications in the database for booking confirmations, ticket cancellations, waitlist offers, and private hire decisions.

---

## 💬 3. Agent Communication & Performatives

### Examiner Question:
> *"What performatives do your agents use and what do they mean?"*

| Performative | Meaning | EventSphere Example |
| :--- | :--- | :--- |
| **`REQUEST`** | An agent asks another agent to perform an action. | `BookingAgent` $\rightarrow$ `TicketAgent`: "REQUEST issue 1 ticket for booking BK-123". |
| **`AGREE`** | The receiving agent accepts the request and completes it. | `TicketAgent` $\rightarrow$ `BookingAgent`: "AGREE ticket TICK-ABC generated". |
| **`REFUSE`** | The receiving agent denies the request, stating a specific reason. | `BookingAgent` $\rightarrow$ User: "REFUSE: Maximum 6 tickets allowed per booking". |
| **`PROPOSE`** | The agent cannot accept the exact request, but proposes a viable alternative. | `VenueAgent` $\rightarrow$ User: "PROPOSE: Venue is occupied; suggested slot is 2:00 PM - 5:00 PM". |
| **`INFORM`** | An agent notifies others of a state change without expecting an action. | `BookingAgent` $\rightarrow$ `WaitlistAgent`: "INFORM: 1 seat freed in event 2". |
| **`FAILURE`** | An agent reports an unexpected execution error. | `MessageBus` $\rightarrow$ Sender: "FAILURE: Agent not found". |

---

## 🔒 4. Concurrency & Anti-Overselling Approach

### Examiner Question:
> *"How did you prevent overselling when two requests arrive simultaneously?"*

### Your Answer:
1. **Atomic Capacity Evaluation**:
   - In `BookingAgent`, seat availability is evaluated atomically at the database transaction layer.
   - Before confirming a booking, `BookingAgent` computes:
     $$\text{available} = \text{total\_quantity} - \text{sold\_quantity}$$
   - The update is executed atomically: `sold_quantity = sold_quantity + quantity` within a committed transaction block.
   - If `available < quantity`, the request is immediately rejected with performative `REFUSE`.
2. **Automated Verification**:
   - We verified this with test `test_booking_agent_anti_overselling`: we created a category of 2 seats, booked 2 seats successfully, and verified that a simultaneous request for 1 more seat was safely refused!

---

## ⏱️ 5. Proactive Agent Behaviour

### Examiner Question:
> *"Where is your proactive agent behaviour? Show me where an agent acts without user interaction."*

### Your Answer:
- Point the examiner to `main.py` lines 41–56 and `agents.py` lines 380–420.
- `waitlist_proactive_sweep_loop()` runs in the background as an `asyncio.create_task` during FastAPI application startup.
- Every 15 seconds, it calls `waitlist_agent.proactive_expiry_sweep()`:
  - It inspects all holds where `status == "offered"` and `expires_at < datetime.utcnow()`.
  - Without any user clicking a button or making an HTTP request, the agent proactively marks the hold as `expired`, finds the next waiting person on the waitlist, and re-offers the seat!

---

## 📱 6. Live Viva Demonstration Script (Step-by-Step)

Follow this 5-minute walkthrough during your viva to impress the examiner:

### Step 1: Log in as Attendee
1. Open [http://127.0.0.1:8000](http://127.0.0.1:8000).
2. Click **Sign In**, then click the green **"1-Click Attendee"** button (`attendee@eventsphere.com`).
3. Point out that the page is responsive and works at mobile screen width (375px).

### Step 2: Book Tickets & View Digital QR Code
1. Click **Book Tickets** on the **National AI & Robotics Summit 2026**.
2. Select 2 tickets $\rightarrow$ click **Proceed to Simulated Payment**.
3. Point out the simulated payment modal (no real bank card collected).
4. Click **Confirm & Authorize Booking**.
5. You are redirected to **My Digital Tickets & Bookings**!
6. Click **View QR** to show the live dynamic QR code generated by `qrcode` library!

### Step 3: Test Gate Check-in & Security Alerts
1. Open a new tab or incognito window, navigate to `/login`, and click **"1-Click Gate Staff"** (`staff@eventsphere.com`).
2. Go to **Gate Scanner** (`/scanner`).
3. Click **"Fill Seed Demo Ticket"** (`DEMO-QR-TICKET-2026`) and click **Verify & Admit**.
   - **Result**: Green alert: `"ADMITTED: Ticket Valid"`.
4. Click **Verify & Admit** again with the same code.
   - **Result**: Red alert: `"REJECTED: Ticket has already been used"`.
5. Click **Verify & Admit** a third time.
   - **Result**: Red alert: `"SECURITY ALERT: Ticket already used and scanned 3 times! (Suspected duplicate screenshot sharing)"`.
   - *Explain*: "This demonstrates TicketAgent's persistent belief tracking and shared screenshot detection (Part C)!"

### Step 4: Private Venue Hire & Counter-Proposal
1. Switch back to the Attendee tab and go to **Private Venue Hire** (`/hire`).
2. Select **University Main Auditorium**, choose a weekend date (e.g. Saturday) to show the +25% weekend peak pricing.
3. Enter 50 guests (routine limit) $\rightarrow$ Submit:
   - **Result**: Immediate `AGREE (AUTO-CONFIRMED)`!
4. Enter 150 guests (>100 limit) $\rightarrow$ Submit:
   - **Result**: `PROPOSE (ESCALATED)`: Escalated to Admin for manual review!

### Step 5: Organiser Dashboard & Multi-Agent Message Trace Log
1. Log in as **Admin** (`admin@eventsphere.com`).
2. Go to **Organiser Dashboard** (`/admin`).
3. Show the **KPI Summary Cards** (Total Events, Tickets Sold, Revenue, Check-ins).
4. Click the **Visual Analytics** tab to show the Chart.js graphs.
5. Click the **Multi-Agent Message Trace Log** tab:
   - Point out the **Conversation IDs**, **Sender**, **Receiver**, **Performatives** (`REQUEST`, `AGREE`, `REFUSE`, `PROPOSE`, `INFORM`), and **JSON payloads**.
   - *Explain*: "Here you can see the complete traceable conversation: BookingAgent requested TicketAgent to issue the ticket, TicketAgent replied with AGREE, and NotificationAgent informed the user!"
